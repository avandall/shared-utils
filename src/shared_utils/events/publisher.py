from __future__ import annotations

import json
import os
import socket
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Protocol
from urllib.parse import urlparse


class EventPublisher(Protocol):
    def publish(self, *, event_type: str, payload: dict[str, Any]) -> None: ...


@dataclass(slots=True)
class EventEnvelope:
    event_id: str
    schema_version: int
    occurred_at: str
    source: str
    type: str
    payload: dict[str, Any]
    traceparent: str | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_json(cls, raw: str) -> "EventEnvelope":
        data = json.loads(raw)
        return cls(
            event_id=str(data["event_id"]),
            schema_version=int(data["schema_version"]),
            occurred_at=str(data["occurred_at"]),
            source=str(data["source"]),
            type=str(data["type"]),
            payload=dict(data.get("payload") or {}),
            traceparent=data.get("traceparent"),
        )


def build_event(*, source: str, event_type: str, payload: dict[str, Any]) -> EventEnvelope:
    try:
        from shared_utils.observability.trace import current_trace_context
        ctx = current_trace_context()
        traceparent = ctx.traceparent if ctx else None
    except ImportError:
        traceparent = None

    return EventEnvelope(
        event_id=str(payload.get("event_id") or uuid.uuid4()),
        schema_version=1,
        occurred_at=datetime.now(tz=timezone.utc).isoformat(),
        source=source,
        type=event_type,
        payload=payload,
        traceparent=traceparent,
    )


class NoopEventPublisher:
    def publish(self, *, event_type: str, payload: dict[str, Any]) -> None:
        return None


@dataclass(slots=True)
class StdoutEventPublisher:
    service: str

    def publish(self, *, event_type: str, payload: dict[str, Any]) -> None:
        print(build_event(source=self.service, event_type=event_type, payload=payload).to_json())


class RedisProtocolError(RuntimeError):
    pass


class RedisStreamClient:
    def __init__(self, url: str, *, timeout: float = 2.0):
        parsed = urlparse(url)
        self.host = parsed.hostname or "localhost"
        self.port = parsed.port or 6379
        self.db = int((parsed.path or "/0").lstrip("/") or "0")
        self.timeout = timeout

    @staticmethod
    def _encode_command(*parts: object) -> bytes:
        encoded = [str(part).encode("utf-8") for part in parts]
        chunks = [f"*{len(encoded)}\r\n".encode("ascii")]
        for item in encoded:
            chunks.append(f"${len(item)}\r\n".encode("ascii"))
            chunks.append(item)
            chunks.append(b"\r\n")
        return b"".join(chunks)

    def execute(self, *parts: object) -> Any:
        with socket.create_connection((self.host, self.port), timeout=self.timeout) as sock:
            sock.settimeout(self.timeout)
            if self.db:
                sock.sendall(self._encode_command("SELECT", self.db))
                self._read_response(sock)
            sock.sendall(self._encode_command(*parts))
            return self._read_response(sock)

    def xadd(self, stream: str, envelope: EventEnvelope) -> str:
        result = self.execute("XADD", stream, "*", "event", envelope.to_json())
        return str(result)

    def xlen(self, stream: str) -> int:
        result = self.execute("XLEN", stream)
        return int(result or 0)

    def xread(self, stream: str, last_id: str, *, block_ms: int, count: int) -> list[tuple[str, EventEnvelope]]:
        result = self.execute("XREAD", "COUNT", count, "BLOCK", block_ms, "STREAMS", stream, last_id)
        if result is None:
            return []

        return self._events_from_xread_result(result)

    def xgroup_create(self, stream: str, group: str, *, start_id: str = "0", mkstream: bool = True) -> None:
        parts: list[object] = ["XGROUP", "CREATE", stream, group, start_id]
        if mkstream:
            parts.append("MKSTREAM")
        try:
            self.execute(*parts)
        except RedisProtocolError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def xreadgroup(
        self,
        stream: str,
        group: str,
        consumer: str,
        *,
        block_ms: int,
        count: int,
        message_id: str = ">",
    ) -> list[tuple[str, EventEnvelope]]:
        result = self.execute(
            "XREADGROUP",
            "GROUP",
            group,
            consumer,
            "COUNT",
            count,
            "BLOCK",
            block_ms,
            "STREAMS",
            stream,
            message_id,
        )
        if result is None:
            return []
        return self._events_from_xread_result(result)

    def xack(self, stream: str, group: str, message_id: str) -> int:
        return int(self.execute("XACK", stream, group, message_id) or 0)

    def xpending_range(
        self,
        stream: str,
        group: str,
        *,
        start_id: str = "-",
        end_id: str = "+",
        count: int = 10,
        consumer: str | None = None,
    ) -> list[dict[str, Any]]:
        parts: list[object] = ["XPENDING", stream, group, start_id, end_id, count]
        if consumer:
            parts.append(consumer)
        result = self.execute(*parts)
        if not result:
            return []
        pending = []
        for row in result:
            if len(row) >= 4:
                pending.append(
                    {
                        "message_id": str(row[0]),
                        "consumer": str(row[1]),
                        "idle_ms": int(row[2]),
                        "deliveries": int(row[3]),
                    }
                )
        return pending

    def xautoclaim(
        self,
        stream: str,
        group: str,
        consumer: str,
        *,
        min_idle_ms: int,
        start_id: str = "0-0",
        count: int = 10,
    ) -> list[tuple[str, EventEnvelope]]:
        result = self.execute(
            "XAUTOCLAIM",
            stream,
            group,
            consumer,
            min_idle_ms,
            start_id,
            "COUNT",
            count,
        )
        if not result or len(result) < 2:
            return []
        return self._events_from_rows(result[1])

    @staticmethod
    def _events_from_rows(rows: list[Any]) -> list[tuple[str, EventEnvelope]]:
        events: list[tuple[str, EventEnvelope]] = []
        for row in rows:
            message_id = str(row[0])
            fields = row[1]
            field_map = {
                str(fields[i]): str(fields[i + 1])
                for i in range(0, len(fields), 2)
                if i + 1 < len(fields)
            }
            raw_event = field_map.get("event")
            if raw_event:
                events.append((message_id, EventEnvelope.from_json(raw_event)))
        return events

    @classmethod
    def _events_from_xread_result(cls, result: list[Any]) -> list[tuple[str, EventEnvelope]]:
        events: list[tuple[str, EventEnvelope]] = []
        for stream_rows in result:
            if not stream_rows or len(stream_rows) < 2:
                continue
            events.extend(cls._events_from_rows(stream_rows[1]))
        return events

    def _read_line(self, sock: socket.socket) -> bytes:
        data = bytearray()
        while not data.endswith(b"\r\n"):
            chunk = sock.recv(1)
            if not chunk:
                raise RedisProtocolError("Unexpected Redis connection close")
            data.extend(chunk)
        return bytes(data[:-2])

    def _read_response(self, sock: socket.socket) -> Any:
        prefix = sock.recv(1)
        if not prefix:
            raise RedisProtocolError("Empty Redis response")
        if prefix == b"+":
            return self._read_line(sock).decode("utf-8")
        if prefix == b"-":
            raise RedisProtocolError(self._read_line(sock).decode("utf-8"))
        if prefix == b":":
            return int(self._read_line(sock))
        if prefix == b"$":
            length = int(self._read_line(sock))
            if length == -1:
                return None
            data = bytearray()
            while len(data) < length:
                data.extend(sock.recv(length - len(data)))
            trailer = sock.recv(2)
            if trailer != b"\r\n":
                raise RedisProtocolError("Invalid bulk string terminator")
            return bytes(data).decode("utf-8")
        if prefix == b"*":
            length = int(self._read_line(sock))
            if length == -1:
                return None
            return [self._read_response(sock) for _ in range(length)]
        raise RedisProtocolError(f"Unknown Redis response prefix: {prefix!r}")


@dataclass(slots=True)
class RedisStreamEventPublisher:
    service: str
    stream: str
    client: RedisStreamClient
    fallback: EventPublisher

    def publish(self, *, event_type: str, payload: dict[str, Any]) -> None:
        envelope = build_event(source=self.service, event_type=event_type, payload=payload)
        try:
            self.client.xadd(self.stream, envelope)
        except Exception:
            self.fallback.publish(event_type=event_type, payload=payload)


@dataclass(slots=True)
class DurableRedisStreamConsumer:
    client: RedisStreamClient
    stream: str
    group: str
    consumer: str
    handler: Callable[[str, EventEnvelope], None]
    dlq_stream: str
    block_ms: int = 5000
    batch_size: int = 20
    max_attempts: int = 3
    reclaim_idle_ms: int = 60000
    group_start_id: str = "0"

    def ensure_group(self) -> None:
        self.client.xgroup_create(self.stream, self.group, start_id=self.group_start_id, mkstream=True)

    def poll_once(self) -> int:
        self.ensure_group()
        processed = 0
        for message_id, envelope in self._read_batch():
            self._handle(message_id, envelope)
            processed += 1
        return processed

    def _read_batch(self) -> list[tuple[str, EventEnvelope]]:
        reclaimed = self.client.xautoclaim(
            self.stream,
            self.group,
            self.consumer,
            min_idle_ms=self.reclaim_idle_ms,
            count=self.batch_size,
        )
        if reclaimed:
            return reclaimed
        return self.client.xreadgroup(
            self.stream,
            self.group,
            self.consumer,
            block_ms=self.block_ms,
            count=self.batch_size,
        )

    def _handle(self, message_id: str, envelope: EventEnvelope) -> None:
        try:
            from shared_utils.observability.trace import (
                child_trace_context,
                parse_traceparent,
                set_trace_context,
            )
            parent = parse_traceparent(envelope.traceparent) if envelope.traceparent else None
            trace_ctx = child_trace_context(parent)
            set_trace_context(trace_ctx)
        except ImportError:
            trace_ctx = None

        try:
            self.handler(message_id, envelope)
        except Exception as exc:
            if self._delivery_count(message_id) >= self.max_attempts:
                self._dead_letter(message_id, envelope, exc)
            raise
        else:
            self.client.xack(self.stream, self.group, message_id)
        finally:
            if trace_ctx is not None:
                try:
                    from shared_utils.observability.trace import set_trace_context
                    set_trace_context(None)
                except ImportError:
                    pass

    def _delivery_count(self, message_id: str) -> int:
        rows = self.client.xpending_range(
            self.stream,
            self.group,
            start_id=message_id,
            end_id=message_id,
            count=1,
            consumer=self.consumer,
        )
        if not rows:
            return 1
        return int(rows[0].get("deliveries") or 1)

    def _dead_letter(self, message_id: str, envelope: EventEnvelope, exc: Exception) -> None:
        payload = dict(envelope.payload)
        payload.update(
            {
                "dead_letter_reason": str(exc),
                "original_event_id": envelope.event_id,
                "original_stream": self.stream,
                "original_stream_id": message_id,
            }
        )
        self.client.xadd(
            self.dlq_stream,
            build_event(source=f"{self.group}-dlq", event_type=envelope.type, payload=payload),
        )
        self.client.xack(self.stream, self.group, message_id)


def get_publisher(service: str) -> EventPublisher:
    if os.getenv("EVENTS_ENABLED", "1") != "1":
        return NoopEventPublisher()

    event_bus_url = os.getenv("EVENT_BUS_URL", "")
    stream = os.getenv("EVENT_STREAM", "wms.events")
    stdout = StdoutEventPublisher(service=service)
    if not event_bus_url:
        return stdout
    return RedisStreamEventPublisher(
        service=service,
        stream=stream,
        client=RedisStreamClient(event_bus_url),
        fallback=stdout,
    )

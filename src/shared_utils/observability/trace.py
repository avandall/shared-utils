from __future__ import annotations

import contextvars
import re
import secrets
from dataclasses import dataclass


TRACEPARENT_RE = re.compile(
    r"^(?P<version>[0-9a-f]{2})-(?P<trace_id>[0-9a-f]{32})-(?P<span_id>[0-9a-f]{16})-(?P<flags>[0-9a-f]{2})$"
)

_trace_context: contextvars.ContextVar["TraceContext | None"] = contextvars.ContextVar(
    "trace_context",
    default=None,
)


@dataclass(frozen=True, slots=True)
class TraceContext:
    trace_id: str
    span_id: str
    sampled: bool = True

    @property
    def traceparent(self) -> str:
        flags = "01" if self.sampled else "00"
        return f"00-{self.trace_id}-{self.span_id}-{flags}"


def _token_hex(bytes_count: int) -> str:
    value = "0" * (bytes_count * 2)
    while set(value) == {"0"}:
        value = secrets.token_hex(bytes_count)
    return value


def new_trace_id() -> str:
    return _token_hex(16)


def new_span_id() -> str:
    return _token_hex(8)


def parse_traceparent(traceparent: str | None) -> TraceContext | None:
    if not traceparent:
        return None
    match = TRACEPARENT_RE.match(traceparent.strip().lower())
    if not match:
        return None
    trace_id = match.group("trace_id")
    span_id = match.group("span_id")
    if set(trace_id) == {"0"} or set(span_id) == {"0"}:
        return None
    return TraceContext(
        trace_id=trace_id,
        span_id=span_id,
        sampled=bool(int(match.group("flags"), 16) & 1),
    )


def child_trace_context(parent: TraceContext | None = None) -> TraceContext:
    if parent is None:
        parent = current_trace_context()
    return TraceContext(
        trace_id=parent.trace_id if parent else new_trace_id(),
        span_id=new_span_id(),
        sampled=parent.sampled if parent else True,
    )


def set_trace_context(context: TraceContext | None) -> None:
    _trace_context.set(context)


def current_trace_context() -> TraceContext | None:
    return _trace_context.get()


def trace_metadata(context: TraceContext | None = None) -> list[tuple[str, str]]:
    context = context or current_trace_context()
    return [("traceparent", context.traceparent)] if context else []

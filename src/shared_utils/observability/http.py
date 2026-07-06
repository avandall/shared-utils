from __future__ import annotations

import json
import os
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from fastapi import Request

from .trace import child_trace_context, parse_traceparent, set_trace_context


@dataclass(slots=True)
class Metrics:
    requests_total: dict[str, int]
    requests_by_path: dict[str, int]
    request_duration_ms_sum: dict[str, float]

    def __init__(self) -> None:
        self.requests_total = defaultdict(int)
        self.requests_by_path = defaultdict(int)
        self.request_duration_ms_sum = defaultdict(float)

    def observe(self, *, method: str, path: str, status: int, duration_ms: float) -> None:
        key = f"{method} {status}"
        self.requests_total[key] += 1
        self.requests_by_path[path] += 1
        self.request_duration_ms_sum[path] += float(duration_ms)

    def render_prometheus(self, *, prefix: str) -> str:
        lines: list[str] = []
        lines.append(f"# TYPE {prefix}_requests_total counter")
        for k, v in sorted(self.requests_total.items()):
            method, status = k.split(" ", 1)
            lines.append(f'{prefix}_requests_total{{method="{method}",status="{status}"}} {v}')

        lines.append(f"# TYPE {prefix}_requests_by_path_total counter")
        for path, v in sorted(self.requests_by_path.items()):
            lines.append(f'{prefix}_requests_by_path_total{{path="{path}"}} {v}')

        lines.append(f"# TYPE {prefix}_request_duration_ms_sum counter")
        for path, v in sorted(self.request_duration_ms_sum.items()):
            lines.append(f'{prefix}_request_duration_ms_sum{{path="{path}"}} {v:.3f}')

        return "\n".join(lines) + "\n"


METRICS = Metrics()


def json_log(*, service: str, level: str, message: str, request_id: str | None = None, **fields: Any) -> None:
    if os.getenv("LOG_FORMAT", "json") != "json":
        return
    record = {"ts": time.time(), "service": service, "level": level, "msg": message}
    if request_id:
        record["request_id"] = request_id
    record.update(fields)
    print(json.dumps(record, ensure_ascii=False, default=str))


def http_metrics_middleware(*, service: str):
    async def _mw(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or getattr(request.state, "request_id", None)
        parent = parse_traceparent(request.headers.get("traceparent"))
        trace_context = child_trace_context(parent)
        set_trace_context(trace_context)
        request.state.trace_id = trace_context.trace_id
        request.state.span_id = trace_context.span_id
        request.state.traceparent = trace_context.traceparent
        start = time.monotonic()
        response = await call_next(request)
        response.headers["traceparent"] = trace_context.traceparent
        duration_ms = (time.monotonic() - start) * 1000.0
        METRICS.observe(
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=duration_ms,
        )
        json_log(
            service=service,
            level="info",
            message="http_request",
            request_id=str(request_id) if request_id else None,
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=duration_ms,
            trace_id=trace_context.trace_id,
            span_id=trace_context.span_id,
        )
        return response

    return _mw

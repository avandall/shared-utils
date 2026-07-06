from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator


_configured = False


def _enabled() -> bool:
    return os.getenv("OTEL_TRACES_EXPORTER", "none").strip().lower() == "otlp"


def _configure(service_name: str) -> None:
    global _configured
    if _configured or not _enabled():
        return

    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except Exception:
        _configured = True
        return

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, insecure=True)))
    trace.set_tracer_provider(provider)
    _configured = True


def tracer(service_name: str):
    _configure(service_name)
    try:
        from opentelemetry import trace

        return trace.get_tracer(service_name)
    except Exception:
        return None


def _parent_context(trace_context: Any):
    if trace_context is None:
        return None

    try:
        from opentelemetry import context, trace
        from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags, TraceState
    except Exception:
        return None

    try:
        trace_id = int(getattr(trace_context, "trace_id"), 16)
        span_id = int(getattr(trace_context, "span_id"), 16)
        sampled = bool(getattr(trace_context, "sampled", True))
    except Exception:
        return None

    span_context = SpanContext(
        trace_id=trace_id,
        span_id=span_id,
        is_remote=True,
        trace_flags=TraceFlags(TraceFlags.SAMPLED) if sampled else TraceFlags(0),
        trace_state=TraceState(),
    )
    return trace.set_span_in_context(NonRecordingSpan(span_context), context.Context())


@contextmanager
def start_span(
    *,
    service_name: str,
    span_name: str,
    trace_context: Any = None,
    attributes: dict[str, Any] | None = None,
) -> Iterator[None]:
    span_tracer = tracer(service_name)
    if span_tracer is None:
        yield
        return

    kwargs: dict[str, Any] = {}
    parent_context = _parent_context(trace_context)
    if parent_context is not None:
        kwargs["context"] = parent_context

    with span_tracer.start_as_current_span(span_name, **kwargs) as span:
        for key, value in (attributes or {}).items():
            if value is not None:
                span.set_attribute(key, value)
        yield

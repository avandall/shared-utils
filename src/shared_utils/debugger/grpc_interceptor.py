from __future__ import annotations

import logging
from collections.abc import Callable
import grpc

from .logging_wrapper import debug_log
from shared_utils.observability.trace import current_trace_context
from .config import settings
from .masking import mask_payload

logger = logging.getLogger(__name__)

try:
    from google.protobuf.json_format import MessageToDict
except ImportError:
    def MessageToDict(message, **kwargs):
        # Fallback if protobuf json_format is not available
        return {"error": "google.protobuf.json_format not available", "type": type(message).__name__}


class GrpcPayloadDebuggerInterceptor(grpc.ServerInterceptor):
    """gRPC Server Interceptor that intercepts and logs request/response payloads for debugging."""

    def __init__(self, *, service: str) -> None:
        self.service = service

    def intercept_service(self, continuation: Callable, handler_call_details):
        handler = continuation(handler_call_details)
        if handler is None or handler.unary_unary is None:
            return handler

        method = handler_call_details.method

        def unary_unary(request, context):
            if not settings.enabled:
                return handler.unary_unary(request, context)

            # Get current trace context (if set by GrpcObservabilityInterceptor or similar)
            trace_ctx = current_trace_context()
            trace_id = trace_ctx.trace_id if trace_ctx else None
            span_id = trace_ctx.span_id if trace_ctx else None

            # 1. Parse and mask Request Payload
            try:
                req_dict = MessageToDict(request, preserving_proto_field_name=True)
                req_masked = mask_payload(req_dict, settings.mask_fields)
            except Exception as e:
                req_masked = {"error": f"Failed to serialize request: {str(e)}"}

            # Log request payload
            debug_log(
                service=self.service,
                level="debug",
                message="grpc_debug_request_payload",
                method=method,
                payload=req_masked,
                trace_id=trace_id,
                span_id=span_id,
            )

            # Set OTel span attribute if exporting to OTLP
            if settings.exporter == "otlp":
                try:
                    import json
                    from opentelemetry import trace as otel_trace
                    current_span = otel_trace.get_current_span()
                    if current_span and current_span.is_recording():
                        current_span.set_attribute("wms.grpc.request", json.dumps(req_masked, ensure_ascii=False))
                except Exception:
                    pass

            # 2. Invoke handler to get Response
            response = handler.unary_unary(request, context)

            # 3. Parse and mask Response Payload
            try:
                resp_dict = MessageToDict(response, preserving_proto_field_name=True)
                resp_masked = mask_payload(resp_dict, settings.mask_fields)
            except Exception as e:
                resp_masked = {"error": f"Failed to serialize response: {str(e)}"}

            # Log response payload
            debug_log(
                service=self.service,
                level="debug",
                message="grpc_debug_response_payload",
                method=method,
                payload=resp_masked,
                trace_id=trace_id,
                span_id=span_id,
            )

            # Set OTel span attribute if exporting to OTLP
            if settings.exporter == "otlp":
                try:
                    import json
                    from opentelemetry import trace as otel_trace
                    current_span = otel_trace.get_current_span()
                    if current_span and current_span.is_recording():
                        current_span.set_attribute("wms.grpc.response", json.dumps(resp_masked, ensure_ascii=False))
                except Exception:
                    pass

            return response

        return grpc.unary_unary_rpc_method_handler(
            unary_unary,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )


def grpc_payload_debugger_interceptor(*, service: str) -> GrpcPayloadDebuggerInterceptor:
    return GrpcPayloadDebuggerInterceptor(service=service)

from __future__ import annotations

import json
import logging
from typing import Any

from .logging_wrapper import debug_log
from shared_utils.observability.trace import current_trace_context
from .config import settings
from .masking import mask_payload

logger = logging.getLogger(__name__)


def calculate_state_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Calculates difference between two states, focusing on modifications and appends."""
    diff = {}
    for key in set(before.keys()) | set(after.keys()):
        if key not in before:
            diff[key] = {"status": "added", "value": after[key]}
        elif key not in after:
            diff[key] = {"status": "removed"}
        else:
            val_before = before[key]
            val_after = after[key]
            if val_before != val_after:
                # Specialized logic for lists (e.g. LangGraph messages list)
                if isinstance(val_before, list) and isinstance(val_after, list):
                    if len(val_after) > len(val_before) and val_after[:len(val_before)] == val_before:
                        new_items = val_after[len(val_before):]
                        diff[key] = {
                            "status": "appended",
                            "new_count": len(new_items),
                            "items": [str(item) for item in new_items]
                        }
                    else:
                        diff[key] = {
                            "status": "modified",
                            "before": [str(x) for x in val_before],
                            "after": [str(x) for x in val_after]
                        }
                elif isinstance(val_before, dict) and isinstance(val_after, dict):
                    nested_diff = calculate_state_diff(val_before, val_after)
                    if nested_diff:
                        diff[key] = {"status": "modified", "diff": nested_diff}
                else:
                    diff[key] = {"status": "modified", "before": str(val_before), "after": str(val_after)}
    return diff


try:
    from langchain_core.callbacks import BaseCallbackHandler
except ImportError:
    class BaseCallbackHandler:  # type: ignore
        """Fallback base class when langchain-core is not installed."""
        pass


class WMSLangGraphDebuggerCallback(BaseCallbackHandler):
    """
    Callback handler for LangGraph / LangChain agents to log trace entries
    and state changes when debugging is enabled.
    """

    def __init__(self, *, service: str = "ai-service") -> None:
        self.service = service
        # Keep track of active chain inputs to calculate state diffs later
        self._chain_inputs: dict[str, dict[str, Any]] = {}

    def on_chain_start(self, serialized: dict[str, Any], inputs: dict[str, Any], *, run_id: Any, **kwargs: Any) -> None:
        if not settings.enabled:
            return

        run_id_str = str(run_id)
        try:
            self._chain_inputs[run_id_str] = json.loads(json.dumps(inputs, default=str))
        except Exception:
            self._chain_inputs[run_id_str] = {k: str(v) for k, v in inputs.items()}

        trace_ctx = current_trace_context()
        trace_id = trace_ctx.trace_id if trace_ctx else None
        span_id = trace_ctx.span_id if trace_ctx else None

        name = serialized.get("name") or "Chain"
        debug_log(
            service=self.service,
            level="debug",
            message="langgraph_debug_chain_start",
            name=name,
            run_id=run_id_str,
            inputs=mask_payload(self._chain_inputs[run_id_str], settings.mask_fields),
            trace_id=trace_id,
            span_id=span_id,
        )

    def on_chain_end(self, outputs: dict[str, Any], *, run_id: Any, **kwargs: Any) -> None:
        if not settings.enabled:
            return

        run_id_str = str(run_id)
        before = self._chain_inputs.pop(run_id_str, {})
        
        try:
            after = json.loads(json.dumps(outputs, default=str))
        except Exception:
            after = {k: str(v) for k, v in outputs.items()}

        state_diff = calculate_state_diff(before, after)
        
        trace_ctx = current_trace_context()
        trace_id = trace_ctx.trace_id if trace_ctx else None
        span_id = trace_ctx.span_id if trace_ctx else None

        debug_log(
            service=self.service,
            level="debug",
            message="langgraph_debug_chain_end",
            run_id=run_id_str,
            outputs=mask_payload(after, settings.mask_fields),
            state_diff=mask_payload(state_diff, settings.mask_fields),
            trace_id=trace_id,
            span_id=span_id,
        )

    def on_llm_start(self, serialized: dict[str, Any], prompts: list[str], *, run_id: Any, **kwargs: Any) -> None:
        if not settings.enabled:
            return

        trace_ctx = current_trace_context()
        trace_id = trace_ctx.trace_id if trace_ctx else None
        span_id = trace_ctx.span_id if trace_ctx else None

        debug_log(
            service=self.service,
            level="debug",
            message="langgraph_debug_llm_start",
            run_id=str(run_id),
            prompts=[mask_payload(p, settings.mask_fields) for p in prompts],
            trace_id=trace_id,
            span_id=span_id,
        )

    def on_llm_end(self, response: Any, *, run_id: Any, **kwargs: Any) -> None:
        if not settings.enabled:
            return

        trace_ctx = current_trace_context()
        trace_id = trace_ctx.trace_id if trace_ctx else None
        span_id = trace_ctx.span_id if trace_ctx else None

        generations = []
        token_usage = {}
        try:
            for g_list in response.generations:
                for g in g_list:
                    generations.append(g.text)
            if response.llm_output and "token_usage" in response.llm_output:
                token_usage = response.llm_output["token_usage"]
        except Exception:
            pass

        debug_log(
            service=self.service,
            level="debug",
            message="langgraph_debug_llm_end",
            run_id=str(run_id),
            generations=generations,
            token_usage=token_usage,
            trace_id=trace_id,
            span_id=span_id,
        )

    def on_tool_start(self, serialized: dict[str, Any], input_str: str, *, run_id: Any, **kwargs: Any) -> None:
        if not settings.enabled:
            return

        trace_ctx = current_trace_context()
        trace_id = trace_ctx.trace_id if trace_ctx else None
        span_id = trace_ctx.span_id if trace_ctx else None

        name = serialized.get("name") or "Tool"
        debug_log(
            service=self.service,
            level="debug",
            message="langgraph_debug_tool_start",
            name=name,
            run_id=str(run_id),
            input=mask_payload(input_str, settings.mask_fields),
            trace_id=trace_id,
            span_id=span_id,
        )

    def on_tool_end(self, output: Any, *, run_id: Any, **kwargs: Any) -> None:
        if not settings.enabled:
            return

        trace_ctx = current_trace_context()
        trace_id = trace_ctx.trace_id if trace_ctx else None
        span_id = trace_ctx.span_id if trace_ctx else None

        debug_log(
            service=self.service,
            level="debug",
            message="langgraph_debug_tool_end",
            run_id=str(run_id),
            output=mask_payload(str(output), settings.mask_fields),
            trace_id=trace_id,
            span_id=span_id,
        )

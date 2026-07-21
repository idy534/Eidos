from __future__ import annotations

import json
import math

from pydantic import BaseModel, ConfigDict


class ContextBudget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    payload_estimate_tokens: int
    protocol_overhead_tokens: int
    estimated_input_tokens: int
    safety_margin_tokens: int
    usable_input_budget: int
    fits: bool


def estimate_context_budget(
    canonical_model_visible_payload: object,
    *,
    context_window_tokens: int,
    request_max_output_tokens: int,
    message_count: int,
    tool_call_count: int,
    tool_result_count: int,
) -> ContextBudget:
    if context_window_tokens <= 0 or not 0 <= request_max_output_tokens < context_window_tokens:
        raise ValueError("invalid context budget")
    if min(message_count, tool_call_count, tool_result_count) < 0:
        raise ValueError("invalid context counts")
    payload_tokens = len(json.dumps(
        canonical_model_visible_payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8"))
    overhead = 64 + message_count * 8 + tool_call_count * 16 + tool_result_count * 16
    margin = min(8_192, max(1_024, math.ceil(context_window_tokens * 0.02)))
    usable = context_window_tokens - request_max_output_tokens - margin
    estimated = payload_tokens + overhead
    return ContextBudget(
        payload_estimate_tokens=payload_tokens,
        protocol_overhead_tokens=overhead,
        estimated_input_tokens=estimated,
        safety_margin_tokens=margin,
        usable_input_budget=usable,
        fits=estimated <= usable,
    )

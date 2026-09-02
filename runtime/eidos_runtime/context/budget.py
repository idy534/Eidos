from __future__ import annotations

import json
import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from eidos_runtime.model.client import (
    ModelContextItem,
    ModelToolDefinitionLike,
    ModelUsage,
)
from eidos_runtime.models import EidosFrozenStrictModel, JsonSafeInt


class ContextUsageSnapshot(EidosFrozenStrictModel):
    """The latest effective context size, separate from cumulative usage."""

    active_tokens: int = Field(ge=0)
    context_window_tokens: int = Field(gt=0)
    percent_used: float = Field(ge=0, le=100)
    source: Literal["provider", "estimated"]
    updated_at: JsonSafeInt = 0


class ContextBudget(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    payload_estimate_tokens: int
    protocol_overhead_tokens: int
    estimated_input_tokens: int
    projected_input_tokens: int
    safety_margin_tokens: int
    usable_input_budget: int
    context_usage: ContextUsageSnapshot
    fits: bool


def estimate_context_budget(
    canonical_model_visible_payload: object,
    *,
    context_window_tokens: int,
    request_max_output_tokens: int,
    message_count: int,
    tool_call_count: int,
    tool_result_count: int,
    provider_usage: ModelUsage | None = None,
    provider_calibration_estimate: int | None = None,
    usage_updated_at: int = 0,
) -> ContextBudget:
    if context_window_tokens <= 0 or not 0 <= request_max_output_tokens < context_window_tokens:
        raise ValueError("invalid context budget")
    if min(message_count, tool_call_count, tool_result_count) < 0:
        raise ValueError("invalid context counts")
    if provider_calibration_estimate is not None and provider_calibration_estimate <= 0:
        raise ValueError("invalid provider calibration estimate")
    payload_text = json.dumps(
        canonical_model_visible_payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    payload_tokens = _estimate_serialized_tokens(payload_text)
    overhead = 64 + message_count * 8 + tool_call_count * 16 + tool_result_count * 16
    margin = min(8_192, max(1_024, math.ceil(context_window_tokens * 0.02)))
    usable = context_window_tokens - request_max_output_tokens - margin
    estimated = payload_tokens + overhead
    provider_active_tokens = (
        provider_usage.input_tokens
        if provider_usage is not None and provider_usage.input_tokens is not None
        else None
    )
    active_tokens = (
        provider_active_tokens if provider_active_tokens is not None else estimated
    )
    projected = estimated
    if (
        provider_active_tokens is not None
        and provider_calibration_estimate is not None
    ):
        projected = max(
            0,
            round(
                provider_active_tokens
                * estimated
                / provider_calibration_estimate
            ),
        )
    source: Literal["provider", "estimated"] = (
        "provider" if provider_active_tokens is not None else "estimated"
    )
    context_usage = ContextUsageSnapshot(
        active_tokens=active_tokens,
        context_window_tokens=context_window_tokens,
        percent_used=min(
            100.0,
            round(active_tokens / context_window_tokens * 100, 1),
        ),
        source=source,
        updated_at=usage_updated_at,
    )
    return ContextBudget(
        payload_estimate_tokens=payload_tokens,
        protocol_overhead_tokens=overhead,
        estimated_input_tokens=estimated,
        projected_input_tokens=projected,
        safety_margin_tokens=margin,
        usable_input_budget=usable,
        context_usage=context_usage,
        fits=projected <= usable,
    )


def estimate_model_request_budget(
    model_context: tuple[ModelContextItem, ...],
    *,
    instructions: str,
    tool_definitions: tuple[ModelToolDefinitionLike, ...],
    context_window_tokens: int,
    request_max_output_tokens: int,
    provider_usage: ModelUsage | None = None,
    provider_calibration_estimate: int | None = None,
    usage_updated_at: int = 0,
) -> ContextBudget:
    """Estimate the exact structured request used by ContextBuilder/Sampling."""

    return estimate_context_budget(
        {
            "instructions": instructions,
            "messages": model_context,
            "tools": [
                tool.model_dump(mode="json") for tool in tool_definitions
            ],
        },
        context_window_tokens=context_window_tokens,
        request_max_output_tokens=request_max_output_tokens,
        message_count=len(model_context),
        tool_call_count=sum(
            item.get("type") == "tool_call" for item in model_context
        ),
        tool_result_count=sum(
            item.get("type") == "tool_result" for item in model_context
        ),
        provider_usage=provider_usage,
        provider_calibration_estimate=provider_calibration_estimate,
        usage_updated_at=usage_updated_at,
    )


def _estimate_serialized_tokens(value: str) -> int:
    """Use a bounded character heuristic only when the provider is unavailable.

    This deliberately does not treat UTF-8 bytes as tokens: ASCII text is
    estimated conservatively at one character per token, while non-ASCII code points
    conservatively count as one token each.
    """
    ascii_characters = sum(ord(character) < 128 for character in value)
    non_ascii_characters = len(value) - ascii_characters
    return max(1, ascii_characters + non_ascii_characters)


__all__ = [
    "ContextBudget",
    "ContextUsageSnapshot",
    "estimate_context_budget",
    "estimate_model_request_budget",
]

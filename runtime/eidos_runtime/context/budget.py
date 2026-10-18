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
from eidos_runtime.context.tokens import estimate_text_tokens
from eidos_runtime.model.replay import group_response_context
from eidos_runtime.domain.context_management import ContextTokenSection


class ContextUsageSnapshot(EidosFrozenStrictModel):
    """The latest effective context size, separate from cumulative usage."""

    active_tokens: int = Field(ge=0)
    context_window_tokens: int = Field(gt=0)
    percent_used: float = Field(ge=0)
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
    # Omitted defaults preserve the hashes of already persisted ContextPlans.
    measurement: Literal["provider", "provider_calibrated", "tokenizer_estimate"] | None = Field(default=None, exclude_if=lambda value: value is None)
    sections: tuple[ContextTokenSection, ...] | None = Field(default=None, exclude_if=lambda value: value is None)

    @property
    def projection_is_estimated(self) -> bool:
        return self.measurement != "provider" if self.measurement is not None else self.context_usage.source == "estimated"


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
    media_tokens: int = 0,
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
    payload_tokens = _estimate_serialized_tokens(payload_text) + max(0, media_tokens)
    overhead = 64 + message_count * 8 + tool_call_count * 16 + tool_result_count * 16
    margin = min(8_192, max(1_024, math.ceil(context_window_tokens * 0.02)))
    usable = context_window_tokens - request_max_output_tokens - margin
    estimated = payload_tokens + overhead
    provider_active_tokens = (
        provider_usage.input_tokens
        if (
            provider_usage is not None
            and provider_usage.input_tokens is not None
            and provider_usage.input_tokens > 0
        )
        else None
    )
    active_tokens = (
        provider_active_tokens if provider_active_tokens is not None else estimated
    )
    projected = provider_active_tokens if provider_active_tokens is not None else estimated
    if (
        provider_active_tokens is not None
        and provider_calibration_estimate is not None
    ):
        # Provider usage anchors the known request. The tokenizer estimates only
        # changes to that request. Growth uses a conservative scale; reductions
        # retain the observed provider/reference ratio.
        delta = estimated - provider_calibration_estimate
        scale = provider_active_tokens / provider_calibration_estimate
        projected = max(0, provider_active_tokens + math.ceil(delta * (max(1.0, scale) if delta > 0 else scale)))
    source: Literal["provider", "estimated"] = (
        "provider" if provider_active_tokens is not None else "estimated"
    )
    context_usage = ContextUsageSnapshot(
        active_tokens=active_tokens,
        context_window_tokens=context_window_tokens,
        percent_used=round(active_tokens / context_window_tokens * 100, 1),
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
        measurement=("provider_calibrated" if provider_active_tokens is not None and provider_calibration_estimate is not None
                     else "provider" if provider_active_tokens is not None else "tokenizer_estimate"),
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

    budget = estimate_context_budget(
        {
            "instructions": instructions,
            "messages": tuple(
                _text_budget_item(item)
                for item in group_response_context(model_context)
            ),
            "tools": [
                tool.model_dump(mode="json") for tool in tool_definitions
            ],
        },
        # Image bytes are not text tokens. This remains an explicitly estimated budget.
        media_tokens=sum(
            int(item.get("imageTokenEstimate", 4096)) if item.get("inputImage") else
            _tool_media_tokens(item)
            for item in model_context
        ),
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
    # These are reference-token shares, never provider item-level measurements.
    weights: dict[str, int] = {
        "instructions": estimate_text_tokens(instructions),
        "tools": estimate_text_tokens(json.dumps([tool.model_dump(mode="json") for tool in tool_definitions], ensure_ascii=False)),
    }
    for item in group_response_context(model_context):
        kind = item.get("type")
        name = ("tool_results" if kind == "tool_result" else
                "tools" if item.get("sectionId") in {"agent-catalog", "selected-agents"} else
                "summary" if str(item.get("content", "")).startswith("Compact summary:") else
                "conversation" if kind in {"user", "assistant", "model_response", "tool_call"} else "other")
        media = int(item.get("imageTokenEstimate", 4096)) if item.get("inputImage") else _tool_media_tokens(item)
        weights[name] = weights.get(name, 0) + estimate_text_tokens(json.dumps(_text_budget_item(item), ensure_ascii=False)) + media
    # Allocate the canonical payload count across independently estimated shares.
    total = max(1, sum(weights.values()))
    sections = [ContextTokenSection(name=name, tokens=value * budget.payload_estimate_tokens // total) for name, value in weights.items()]
    sections.append(ContextTokenSection(name="overhead", tokens=budget.estimated_input_tokens - sum(value.tokens for value in sections)))
    return budget.model_copy(update={"sections": tuple(sections)})


def _text_budget_item(item: ModelContextItem) -> ModelContextItem:
    projected = {key: value for key, value in item.items() if key not in {"inputImage", "imageTokenEstimate"}}
    content = projected.get("toolContent")
    if isinstance(content, dict):
        projected["toolContent"] = {
            "items": [
                {key: value for key, value in part.items() if key != "data"}
                for part in content.get("items", [])
            ],
        }
    return projected


def _tool_media_tokens(item: ModelContextItem) -> int:
    content = item.get("toolContent")
    if not isinstance(content, dict):
        return 0
    return 4096 * sum(
        isinstance(part, dict) and part.get("kind") == "binary"
        and str(part.get("mime", "")).startswith("image/")
        for part in content.get("items", [])
    )


def _estimate_serialized_tokens(value: str) -> int:
    return estimate_text_tokens(value)


__all__ = [
    "ContextBudget",
    "ContextUsageSnapshot",
    "estimate_context_budget",
    "estimate_model_request_budget",
]

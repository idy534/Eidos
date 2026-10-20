from __future__ import annotations

from typing import Literal

from pydantic import Field, StrictInt

from eidos_runtime.models import EidosFrozenStrictModel, JsonSafeInt, JSON_SAFE_INTEGER_MAX


class ContextCompactionRecord(EidosFrozenStrictModel):
    request_id: str = Field(min_length=1, max_length=80)
    stage: Literal["requested", "started", "finished"]
    trigger: Literal["manual", "soft_target", "window_pressure", "provider_limit", "projection_limit"]
    status: Literal["queued", "running", "succeeded", "failed", "canceled"]
    strategy: Literal["model", "deterministic"] = "deterministic"
    reason: str | None = Field(default=None, max_length=80)
    duration_ms: JsonSafeInt = Field(default=0, ge=0)
    before_tokens: int | None = Field(default=None, ge=0)
    after_tokens: int | None = Field(default=None, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    source_item_count: int = Field(default=0, ge=0)


class ContextCompactionActivity(EidosFrozenStrictModel):
    """A display projection of committed compaction events, never model history."""

    request_id: str = Field(min_length=1, max_length=80)
    run_id: str = Field(min_length=1, max_length=80)
    event_id: StrictInt = Field(ge=0, le=JSON_SAFE_INTEGER_MAX)
    started_at: StrictInt = Field(ge=0, le=JSON_SAFE_INTEGER_MAX)
    updated_at: StrictInt = Field(ge=0, le=JSON_SAFE_INTEGER_MAX)
    status: Literal["running", "succeeded", "failed", "canceled"]
    trigger: Literal["manual", "soft_target", "window_pressure", "provider_limit", "projection_limit"] | None = None
    reason: str | None = Field(default=None, max_length=80)


class ContextTokenSection(EidosFrozenStrictModel):
    name: Literal["instructions", "tools", "conversation", "tool_results", "summary", "other", "overhead"]
    tokens: int = Field(ge=0)


class ContextDetails(EidosFrozenStrictModel):
    run_id: str
    measurement: Literal["provider", "provider_calibrated", "tokenizer_estimate", "unavailable"]
    active_tokens: int | None = Field(default=None, ge=0)
    projected_tokens: int | None = Field(default=None, ge=0)
    context_window_tokens: int = Field(gt=0)
    sections: tuple[ContextTokenSection, ...] = ()
    cleared_tool_results: int = Field(default=0, ge=0)
    calibration_samples: int = Field(default=0, ge=0)
    last_estimation_error_percent: float | None = None
    compaction: ContextCompactionRecord | None = None
    manual_pending: bool = False
    plan_id: str | None = None
    updated_at: JsonSafeInt = 0


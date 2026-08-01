from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from eidos_runtime.models import EidosFrozenStrictModel, JsonSafeInt


class SegmentStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class StepStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class ItemKind(StrEnum):
    USER_MESSAGE = "user_message"
    ASSISTANT_MESSAGE = "assistant_message"
    FILE_CHANGE = "file_change"
    COMMAND_EXECUTION = "command_execution"
    TOOL_CALL = "tool_call"


class ItemStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    DECLINED = "declined"
    CANCELED = "canceled"


class ModelAttemptStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class ModelUsageRecord(EidosFrozenStrictModel):
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cache_read_tokens: int | None = Field(default=None, ge=0)
    cache_write_tokens: int | None = Field(default=None, ge=0)


class ExecutionSegment(EidosFrozenStrictModel):
    id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    ordinal: int = Field(ge=0)
    status: SegmentStatus
    step_count: int = Field(ge=0)
    effective_ms: int = Field(ge=0)
    created_at: JsonSafeInt
    started_at: JsonSafeInt | None = None
    completed_at: JsonSafeInt | None = None


class Step(EidosFrozenStrictModel):
    id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    segment_id: str = Field(min_length=1)
    ordinal: int = Field(ge=0)
    status: StepStatus
    observed_reconciliation_epoch: int = Field(ge=0)
    resolution_snapshot_id: str = Field(min_length=1)
    tool_snapshot_json: str | None = None
    tool_set_hash: str | None = None
    progress_signature_json: str | None = None
    created_at: JsonSafeInt
    completed_at: JsonSafeInt | None = None


class Item(EidosFrozenStrictModel):
    id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    ordinal: int = Field(ge=0)
    model_step_index: int | None = Field(default=None, ge=0)
    kind: ItemKind
    status: ItemStatus
    content: str | None = None
    incomplete: bool
    created_at: JsonSafeInt
    completed_at: JsonSafeInt | None = None


class ModelAttempt(EidosFrozenStrictModel):
    id: str = Field(min_length=1)
    step_id: str = Field(min_length=1)
    ordinal: int = Field(ge=0)
    status: ModelAttemptStatus
    provider_name: str | None = None
    resolved_model_name: str | None = None
    finish_reason: str | None = None
    provider_response_id: str | None = None
    lease_id: str | None = None
    wire_api: str | None = None
    model_id: str | None = None
    request_timeout: float | None = Field(default=None, ge=0)
    context_snapshot_id: str | None = None
    retry_decision_json: str | None = None
    usage_json: str | None = None
    error_code: str | None = None
    http_status: int | None = None
    ttft_ms: int | None = Field(default=None, ge=0)
    duration_ms: int | None = Field(default=None, ge=0)
    had_progress: bool
    started_at: JsonSafeInt
    completed_at: JsonSafeInt | None = None


__all__ = [
    "ExecutionSegment",
    "Item",
    "ItemKind",
    "ItemStatus",
    "ModelAttempt",
    "ModelAttemptStatus",
    "ModelUsageRecord",
    "SegmentStatus",
    "Step",
    "StepStatus",
]

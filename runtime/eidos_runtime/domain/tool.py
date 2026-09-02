from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import Field

from eidos_runtime.models import EidosFrozenStrictModel, JsonSafeInt


class ToolCallStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    INVALIDATED = "invalidated"
    CANCELED = "canceled"


class ApprovalKind(StrEnum):
    TOOL = "tool"
    DEFAULT = "default"
    ADDITIONAL_PERMISSIONS = "additional_permissions"
    ESCALATED = "escalated"


class ToolAttemptStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    UNCERTAIN = "uncertain"


class DurableIntentStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    UNCERTAIN = "uncertain"
    INTERRUPTED = "interrupted"


class ToolCall(EidosFrozenStrictModel):
    id: str = Field(min_length=1)
    item_id: str = Field(min_length=1)
    model_step_index: int = Field(ge=0)
    batch_order: int = Field(ge=0)
    provider_call_id: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    status: ToolCallStatus
    payload_kind: Literal["function", "custom"]
    arguments_json: str
    result_json: str | None = None
    model_result_json: str | None = None
    approval_status: ApprovalStatus | None = None
    approval_decision: str | None = None
    approval_feedback: str | None = None
    approval_diff: str | None = None
    base_sha256: str | None = None
    provenance_json: str | None = None
    tool_set_hash: str | None = None
    started_at: JsonSafeInt
    duration_ms: int | None = Field(default=None, ge=0)
    completed_at: JsonSafeInt | None = None


class Approval(EidosFrozenStrictModel):
    id: str = Field(min_length=1)
    tool_call_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    item_id: str = Field(min_length=1)
    status: ApprovalStatus
    request_hash: str = Field(min_length=64, max_length=64)
    request_json: str
    attempt_ordinal: int = Field(ge=0, le=1)
    approval_kind: ApprovalKind
    decision: str | None = None
    feedback: str | None = None
    created_at: JsonSafeInt
    decided_at: JsonSafeInt | None = None


class ToolAttempt(EidosFrozenStrictModel):
    id: str = Field(min_length=1)
    tool_call_id: str = Field(min_length=1)
    ordinal: int = Field(ge=0, le=1)
    sandbox_type: str = Field(min_length=1)
    sandbox_requested: bool
    effective_permissions_json: str
    profile_hash: str | None = None
    escalation_reason: str | None = None
    status: ToolAttemptStatus
    started_at: JsonSafeInt
    completed_at: JsonSafeInt | None = None
    result_code: str | None = None


class DurableIntent(EidosFrozenStrictModel):
    id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    tool_call_id: str = Field(min_length=1)
    execution_nonce: str = Field(min_length=1)
    arguments_hash: str = Field(min_length=64, max_length=64)
    preconditions_json: str
    status: DurableIntentStatus
    created_at: JsonSafeInt
    reconciled_at: JsonSafeInt | None = None


class AsyncOperation(EidosFrozenStrictModel):
    id: str = Field(min_length=1)
    request_id: str | None = None
    operation_id: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    request_hash: str = Field(min_length=64, max_length=64)
    status: str = Field(min_length=1)
    result_json: str | None = None
    error_code: str | None = None
    created_at: JsonSafeInt
    started_at: JsonSafeInt | None = None
    completed_at: JsonSafeInt | None = None

    @property
    def terminal(self) -> bool:
        return self.status in {"completed", "failed", "canceled", "interrupted"}


class PermissionSnapshot(EidosFrozenStrictModel):
    snapshot_hash: str = Field(min_length=64, max_length=64)
    permissions_json: str


class SandboxAttempt(EidosFrozenStrictModel):
    attempt_id: str = Field(min_length=1)
    sandbox_type: str = Field(min_length=1)
    effective_permissions_hash: str = Field(min_length=64, max_length=64)
    status: ToolAttemptStatus


__all__ = [
    "Approval",
    "ApprovalKind",
    "ApprovalStatus",
    "AsyncOperation",
    "DurableIntent",
    "DurableIntentStatus",
    "PermissionSnapshot",
    "SandboxAttempt",
    "ToolAttempt",
    "ToolAttemptStatus",
    "ToolCall",
    "ToolCallStatus",
]

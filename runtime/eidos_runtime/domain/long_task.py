from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from eidos_runtime.models import EidosFrozenStrictModel, JsonSafeInt


class LongTaskStatus(StrEnum):
    RUNNING = "running"
    PAUSE_REQUESTED = "pause_requested"
    PAUSED = "paused"
    RESUME_REQUESTED = "resume_requested"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELED = "canceled"
    COMPLETED = "completed"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class SafePoint(StrEnum):
    BEFORE_MODEL = "before_model"
    AFTER_MODEL = "after_model"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_SLOT = "waiting_slot"
    BEFORE_TOOL = "before_tool"
    AFTER_TOOL = "after_tool"
    TOOL_EXECUTING = "tool_executing"
    AFTER_CHECKPOINT = "after_checkpoint"
    AFTER_REPOSITORY_GENERATION = "after_repository_generation"


class ResumeOutcome(StrEnum):
    SAFE_RESUME = "safe_resume"
    REBUILD_CONTEXT = "rebuild_context"
    REINDEX_REQUIRED = "reindex_required"
    APPROVAL_REQUIRED = "approval_required"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    WORKSPACE_CHANGED = "workspace_changed"
    MODEL_UNAVAILABLE = "model_unavailable"
    PERMISSION_CHANGED = "permission_changed"
    CANNOT_RESUME = "cannot_resume"


class ResumeVerification(EidosFrozenStrictModel):
    run_id: str = Field(min_length=1)
    outcome: ResumeOutcome
    reasons: tuple[str, ...]
    checked_at: JsonSafeInt


class LongTaskProgress(EidosFrozenStrictModel):
    run_id: str = Field(min_length=1)
    status: LongTaskStatus
    safe_point: SafePoint
    progress_sequence: int = Field(ge=0)
    context_plan_id: str | None = None
    context_snapshot_id: str | None = None
    rule_snapshot_id: str | None = None
    inventory_snapshot_id: str | None = None
    index_snapshot_id: str | None = None
    permission_snapshot_hash: str | None = None
    workspace_path: str
    workspace_device: int
    workspace_inode: int
    workspace_owner: int
    git_head: str | None = None
    side_effects_may_exist: bool
    reconciliation_required: bool
    pause_requested_at: JsonSafeInt | None = None
    cancel_requested_at: JsonSafeInt | None = None
    paused_at: JsonSafeInt | None = None
    resumed_at: JsonSafeInt | None = None
    updated_at: JsonSafeInt
    last_verification: ResumeVerification | None = None


__all__ = [
    "LongTaskProgress",
    "LongTaskStatus",
    "ResumeVerification",
    "ResumeOutcome",
    "SafePoint",
]

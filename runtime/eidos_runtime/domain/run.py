from __future__ import annotations

from enum import StrEnum

from pydantic import Field
from eidos_runtime.models import EidosFrozenStrictModel, JsonSafeInt


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    FINALIZING = "finalizing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    STOPPED = "stopped"
    CANCELED = "canceled"
    INTERRUPTED = "interrupted"


class RunControlState(StrEnum):
    ACTIVE = "active"
    PAUSE_REQUESTED = "pause_requested"
    PAUSED = "paused"
    RESUME_REQUESTED = "resume_requested"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELING = "canceling"
    SETTLED = "settled"


class Run(EidosFrozenStrictModel):
    id: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    user_input: str
    model_id: str = Field(min_length=1)
    status: RunStatus
    model_step_count: int = Field(ge=0)
    reconciliation_required: bool
    side_effects_may_exist: bool
    workspace_version: int = Field(ge=0)
    created_at: JsonSafeInt
    started_at: JsonSafeInt | None = None
    updated_at: JsonSafeInt
    completed_at: JsonSafeInt | None = None
    error_code: str | None = None
    stop_reason: str | None = None
    cancel_requested_at: JsonSafeInt | None = None
    cancel_completed_at: JsonSafeInt | None = None


class RunQueuePosition(EidosFrozenStrictModel):
    run_id: str = Field(min_length=1)
    position: int = Field(ge=1)
    total: int = Field(ge=1)


class RunSnapshot(EidosFrozenStrictModel):
    run: Run
    model_profile_json: str
    extension_snapshot_json: str
    resolution_snapshot_id: str = Field(min_length=1)
    snapshot_hash: str = Field(min_length=64, max_length=64)


__all__ = [
    "Run",
    "RunControlState",
    "RunQueuePosition",
    "RunSnapshot",
    "RunStatus",
]

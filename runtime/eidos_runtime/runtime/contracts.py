from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict

from eidos_runtime.model.client import ModelContextItem, ModelToolCall, ModelToolDefinition
from eidos_runtime.context.budget import ContextBudget
from eidos_runtime.tools.registry import StepToolSnapshot


class _FrozenModel(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        arbitrary_types_allowed=True,
    )


class WorkspaceIdentitySnapshot(_FrozenModel):
    path: str
    device: int
    inode: int
    owner: int


class RunContext(_FrozenModel):
    run_id: str
    session_id: str
    model_id: str
    model_context: tuple[ModelContextItem, ...]
    extension_snapshot: dict[str, object]
    extension_snapshot_hash: str
    skill_context: tuple[ModelContextItem, ...] = ()


class StepContext(_FrozenModel):
    run_id: str
    session_id: str
    step_id: str
    step_index: int
    model_id: str
    model_context: tuple[ModelContextItem, ...]
    tool_snapshot: StepToolSnapshot
    tool_definitions: tuple[ModelToolDefinition, ...]
    workspace_identity: WorkspaceIdentitySnapshot
    reconciliation_epoch: int
    workspace_version: int = 0
    context_budget: ContextBudget | None = None
    extension_snapshot_hash: str
    new_user_input_ids: tuple[str, ...] = ()


class SamplingOutcome(_FrozenModel):
    text: str
    tool_calls: tuple[ModelToolCall, ...]
    assistant_item: dict[str, object] | None = None
    retry_count: int = 0


class ToolBatchOutcome(_FrozenModel):
    status: Literal[
        "no_tools",
        "ready",
        "completed",
        "validation_failed",
        "sensitive_rejected",
        "paused",
    ]
    tool_calls: tuple[ModelToolCall, ...] = ()
    error_code: str | None = None
    pause_reason: str | None = None
    feedback: tuple[ModelContextItem, ...] = ()
    error_fingerprints: tuple[str, ...] = ()
    workspace_version: int = 0
    diff_hash: str | None = None
    successful_tool_result_hashes: tuple[str, ...] = ()
    context_fact_ids: tuple[str, ...] = ()
    reconciliation_epoch: int = 0


class LoopAction(StrEnum):
    CONTINUE = "continue"
    COMPACT = "compact"
    PAUSE = "pause"
    FINALIZE = "finalize"
    COMPLETE = "complete"
    FAIL = "fail"
    CANCEL = "cancel"


class RuntimeFailure(_FrozenModel):
    code: str
    retryable: bool = False
    reason: str | None = None


class LoopDecision(_FrozenModel):
    action: LoopAction
    reason: str | None = None
    failure: RuntimeFailure | None = None


class RunBudget(_FrozenModel):
    segment_steps_remaining: int
    run_steps_remaining: int
    segment_effective_ms_remaining: int
    run_effective_ms_remaining: int


class RuntimeCancelled(RuntimeError):
    pass

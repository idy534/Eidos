from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict

from eidos_runtime.model.client import (
    ModelContextItem,
    ModelToolCall,
    ModelToolDefinition,
    ModelUsage,
    ModelProfileSnapshot,
)
from eidos_runtime.context.budget import ContextBudget
from eidos_runtime.model.prompts import ResolvedInstructions
from eidos_runtime.runtime.resolution import (
    RunResolutionSnapshot,
    StepResolutionSnapshot,
    WorkspaceIdentitySnapshot,
)
from eidos_runtime.tools.registry import StepToolSnapshot


class _FrozenModel(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        strict=True,
        arbitrary_types_allowed=True,
    )


class RunContext(_FrozenModel):
    run_id: str
    session_id: str
    model_id: str
    model_profile: ModelProfileSnapshot
    model_context: tuple[ModelContextItem, ...]
    extension_snapshot: dict[str, object]
    extension_snapshot_hash: str
    resolution_snapshot: RunResolutionSnapshot


class StepContext(_FrozenModel):
    run_id: str
    session_id: str
    step_id: str
    model_attempt_id: str
    step_index: int
    model_id: str
    model_profile: ModelProfileSnapshot
    model_context: tuple[ModelContextItem, ...]
    instructions: ResolvedInstructions
    tool_snapshot: StepToolSnapshot
    tool_definitions: tuple[ModelToolDefinition, ...]
    workspace_identity: WorkspaceIdentitySnapshot
    reconciliation_epoch: int
    workspace_version: int = 0
    context_budget: ContextBudget | None = None
    extension_snapshot_hash: str
    resolution_snapshot: StepResolutionSnapshot
    new_user_input_ids: tuple[str, ...] = ()


class SamplingOutcome(_FrozenModel):
    text: str
    tool_calls: tuple[ModelToolCall, ...]
    assistant_item: dict[str, object] | None = None
    final_response_declared: bool = True
    retry_count: int = 0
    usage: ModelUsage | None = None
    provider_name: str | None = None
    resolved_model_name: str | None = None
    finish_reason: str | None = None
    provider_response_id: str | None = None
    response_state: str | None = None
    ttft_ms: int | None = None
    duration_ms: int | None = None


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


class ProgressSignature(_FrozenModel):
    workspace_version: int
    diff_hash: str | None
    successful_tool_result_hashes: tuple[str, ...]
    new_context_fact_ids: tuple[str, ...]
    error_fingerprints: tuple[str, ...]
    resolved_error_fingerprints: tuple[str, ...]
    reconciliation_epoch: int
    new_user_input_ids: tuple[str, ...] = ()
    tool_call_fingerprint: str | None = None
    loop_state_fingerprint: str | None = None
    recovery_state_fingerprint: str | None = None


class LoopStateFingerprint(_FrozenModel):
    tool_call_fingerprint: str
    workspace_version: int
    reconciliation_epoch: int
    active_error_fingerprints: tuple[str, ...]
    context_fact_frontier_hash: str


class LoopAction(StrEnum):
    CONTINUE = "continue"
    COMPACT = "compact"
    PAUSE = "pause"
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


class RuntimeCancelled(RuntimeError):
    pass

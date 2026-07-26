from __future__ import annotations

from enum import StrEnum
from dataclasses import dataclass, field


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_USER_INPUT = "waiting_user_input"
    FINALIZING = "finalizing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    STOPPED = "stopped"
    CANCELED = "canceled"
    INTERRUPTED = "interrupted"


class RuntimeState(StrEnum):
    THINKING = "thinking"
    TOOL_EXECUTING = "tool_executing"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_USER_INPUT = "waiting_user_input"
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class RuntimeLifecycle(StrEnum):
    RUNNING = "running"
    DRAINING = "draining"
    QUIESCENT = "quiescent"
    CLOSED = "closed"


class SegmentStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_USER_INPUT = "waiting_user_input"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class StepStatus(StrEnum):
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


class ToolCallStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class OperationStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"


class EventType(StrEnum):
    SESSION_CREATED = "session.created"
    SESSION_TITLE_UPDATED = "session.title_updated"
    SESSION_TITLE_GENERATION_STARTED = "session.title_generation_started"
    SESSION_TITLE_GENERATION_FAILED = "session.title_generation_failed"
    RUN_CREATED = "run.created"
    RUN_UPDATED = "run.updated"
    RUN_STATUS_CHANGED = "run.status_changed"
    SEGMENT_CREATED = "segment.created"
    SEGMENT_STATUS_CHANGED = "segment.status_changed"
    STEP_STATUS_CHANGED = "step.status_changed"
    ITEM_STARTED = "item.started"
    ITEM_DELTA = "item.delta"
    ITEM_COMPLETED = "item.completed"
    APPROVAL_STATUS_CHANGED = "approval.status_changed"
    TOOL_CALL_STARTED = "tool_call.started"
    TOOL_CALL_COMPLETED = "tool_call.completed"
    FINALIZATION_STATUS_CHANGED = "finalization.status_changed"
    RECONCILIATION_REQUIRED = "reconciliation.required"
    RECONCILIATION_CLEARED = "reconciliation.cleared"
    PLUGIN_IMPORTED = "plugin.imported"
    PLUGIN_STATE_CHANGED = "plugin.state_changed"
    MCP_SERVER_STATE_CHANGED = "mcp_server.state_changed"
    MCP_TOOL_LIST_CHANGED = "mcp_tool_list.changed"
    CONTEXT_COMPACTED = "context.compacted"
    INPUT_QUEUED = "input.queued"
    INPUT_INJECTED = "input.injected"


TRANSITIONS: dict[type[StrEnum], dict[StrEnum, frozenset[StrEnum]]] = {
    RunStatus: {
        RunStatus.QUEUED: frozenset({RunStatus.RUNNING, RunStatus.CANCELED}),
        RunStatus.RUNNING: frozenset({
            RunStatus.WAITING_APPROVAL, RunStatus.WAITING_USER_INPUT,
            RunStatus.FINALIZING, RunStatus.SUCCEEDED, RunStatus.FAILED,
            RunStatus.CANCELED, RunStatus.INTERRUPTED,
        }),
        RunStatus.WAITING_APPROVAL: frozenset({
            RunStatus.RUNNING, RunStatus.QUEUED, RunStatus.WAITING_USER_INPUT, RunStatus.FAILED,
            RunStatus.CANCELED, RunStatus.INTERRUPTED,
        }),
        RunStatus.WAITING_USER_INPUT: frozenset({RunStatus.QUEUED, RunStatus.CANCELED}),
        RunStatus.FINALIZING: frozenset({
            RunStatus.STOPPED,
            RunStatus.FAILED,
            RunStatus.CANCELED,
            RunStatus.WAITING_USER_INPUT,
            RunStatus.INTERRUPTED,
        }),
    },
    RuntimeState: {
        RuntimeState.THINKING: frozenset({
            RuntimeState.TOOL_EXECUTING, RuntimeState.WAITING_APPROVAL,
            RuntimeState.WAITING_USER_INPUT, RuntimeState.FINALIZING,
            RuntimeState.COMPLETED, RuntimeState.FAILED, RuntimeState.CANCELED,
        }),
        RuntimeState.TOOL_EXECUTING: frozenset({
            RuntimeState.THINKING, RuntimeState.WAITING_APPROVAL, RuntimeState.WAITING_USER_INPUT,
            RuntimeState.FAILED, RuntimeState.CANCELED,
        }),
        RuntimeState.WAITING_APPROVAL: frozenset({
            RuntimeState.THINKING, RuntimeState.TOOL_EXECUTING, RuntimeState.WAITING_USER_INPUT,
            RuntimeState.FAILED, RuntimeState.CANCELED,
        }),
        RuntimeState.WAITING_USER_INPUT: frozenset({RuntimeState.THINKING, RuntimeState.CANCELED}),
        RuntimeState.FINALIZING: frozenset({
            RuntimeState.COMPLETED, RuntimeState.FAILED, RuntimeState.CANCELED,
        }),
    },
    SegmentStatus: {
        SegmentStatus.QUEUED: frozenset({SegmentStatus.RUNNING, SegmentStatus.CANCELED}),
        SegmentStatus.RUNNING: frozenset({
            SegmentStatus.QUEUED, SegmentStatus.WAITING_USER_INPUT, SegmentStatus.COMPLETED,
            SegmentStatus.FAILED, SegmentStatus.CANCELED,
        }),
        SegmentStatus.WAITING_USER_INPUT: frozenset({SegmentStatus.COMPLETED, SegmentStatus.CANCELED}),
    },
    StepStatus: {
        StepStatus.RUNNING: frozenset({StepStatus.COMPLETED, StepStatus.FAILED, StepStatus.CANCELED}),
    },
    ApprovalStatus: {
        ApprovalStatus.PENDING: frozenset({
            ApprovalStatus.APPROVED, ApprovalStatus.REJECTED,
            ApprovalStatus.INVALIDATED, ApprovalStatus.CANCELED,
        }),
    },
    ToolCallStatus: {
        ToolCallStatus.RUNNING: frozenset({
            ToolCallStatus.COMPLETED,
            ToolCallStatus.FAILED,
            ToolCallStatus.CANCELED,
        }),
    },
    OperationStatus: {
        OperationStatus.IN_PROGRESS: frozenset({OperationStatus.COMPLETED, OperationStatus.INTERRUPTED}),
    },
}


def ensure_transition(current: StrEnum, target: StrEnum) -> None:
    if type(current) is not type(target) or target not in TRANSITIONS.get(type(current), {}).get(current, ()):
        raise ValueError(f"illegal {type(current).__name__} transition")


@dataclass
class RuntimePhaseTracker:
    """Best-effort in-memory diagnostics; persisted status remains authoritative."""

    state: RuntimeState = RuntimeState.THINKING
    history: list[tuple[RuntimeState, RuntimeState, str]] = field(default_factory=list)

    def track(self, target: RuntimeState, reason: str) -> None:
        if target == self.state:
            return
        self.history.append((self.state, target, reason))
        self.state = target

    def transition(self, target: RuntimeState, reason: str) -> None:
        """Compatibility validator for callers that explicitly test phase graphs."""
        if target == self.state:
            return
        ensure_transition(self.state, target)
        self.track(target, reason)


StateMachine = RuntimePhaseTracker

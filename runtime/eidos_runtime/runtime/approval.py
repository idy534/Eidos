from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Callable

from pydantic import BaseModel, ConfigDict, ValidationError

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.protocol.schemas import ApprovalDecisionDto
from eidos_runtime.runtime.events import RuntimeEvents
from eidos_runtime.runtime.state_machine import RuntimePhaseTracker, RuntimeState
from eidos_runtime.runtime.fault_injection import hit_fault


APPROVAL_REJECTION_GUIDANCE = (
    "User rejected an approval. Do not request another approval in this run; "
    "try a non-approval alternative or provide a safe manual strategy and finish."
)


@dataclass(frozen=True)
class ApprovalDecision:
    decision: str
    feedback: str | None = None


@dataclass(frozen=True)
class ApprovalRequest:
    payload: dict[str, object]


@dataclass(frozen=True)
class ApprovalResult:
    decision: str
    feedback: str | None = None


class ApprovalTransportError(RuntimeError):
    pass


class ApprovalOutcome(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    decision: str
    feedback: str | None = None
    item: dict[str, object]


class ApprovalAdapter:
    """Compatibility transport adapter retained for existing imports."""

    def __init__(
        self,
        request: Callable[[dict[str, object], threading.Event], object] | None,
    ) -> None:
        self._request = request

    def request(self, request: ApprovalRequest, cancel: threading.Event) -> ApprovalResult:
        if self._request is None:
            return ApprovalResult("reject")
        try:
            value = self._request(request.payload, cancel)
        except Exception as error:
            raise ApprovalTransportError("approval transport failed") from error
        return ApprovalResult(
            str(getattr(value, "decision", "reject")),
            getattr(value, "feedback", None),
        )


class ApprovalCoordinator:
    """Owns the complete persisted approval pause/resume state machine."""

    def __init__(
        self,
        store: SessionStore,
        request: Callable[[dict[str, object], threading.Event], object] | None,
        events: RuntimeEvents,
        state_machine: RuntimePhaseTracker,
        pause_effective_time: Callable[[str], None],
        resume_effective_time: Callable[[], None],
        resume_execution_slot: Callable[[str, threading.Event], None],
        check_cancel: Callable[[str, threading.Event], None],
        *,
        requeue: bool,
    ) -> None:
        self.store = store
        self.transport = ApprovalAdapter(request)
        self.events = events
        self.state_machine = state_machine
        self.pause_effective_time = pause_effective_time
        self.resume_effective_time = resume_effective_time
        self.resume_execution_slot = resume_execution_slot
        self.check_cancel = check_cancel
        self.requeue = requeue

    def request(
        self,
        run_id: str,
        item: dict[str, object],
        description: dict[str, object],
        cancel: threading.Event,
        *,
        diff: str = "",
        base_sha256: str | None = None,
        transition_reason: str,
        request: dict[str, object] | None = None,
        attempt_ordinal: int = 0,
        approval_kind: str = "tool",
    ) -> ApprovalOutcome:
        if self.store.approval_prompt_blocked(run_id):
            return ApprovalOutcome(
                decision="reject",
                feedback=APPROVAL_REJECTION_GUIDANCE,
                item=item,
            )
        mutation = self.store.begin_approval_committed(
            str(item["id"]),
            diff,
            base_sha256,
            request=request,
            attempt_ordinal=attempt_ordinal,
            approval_kind=approval_kind,
        )
        pending_item = mutation.value
        approval_run = self.store.read_run(run_id)
        self.events.publish(mutation, run=approval_run, item=pending_item)
        self.state_machine.track(RuntimeState.WAITING_APPROVAL, transition_reason)
        hit_fault("cancel_approval_race")
        self.pause_effective_time(run_id)
        tool_call = pending_item["toolCall"]
        assert isinstance(tool_call, dict)
        suspend_deadline = getattr(cancel, "suspend_deadline", None)
        resume_deadline = getattr(cancel, "resume_deadline", None)
        if callable(suspend_deadline):
            suspend_deadline()
        try:
            result = self.transport.request(
                ApprovalRequest({
                    "sessionId": pending_item["sessionId"],
                    "runId": pending_item["runId"],
                    "itemId": pending_item["id"],
                    "toolCallId": tool_call["id"],
                    **description,
                }),
                cancel,
            )
        finally:
            if callable(resume_deadline):
                resume_deadline()
        self.check_cancel(run_id, cancel)
        decision = self._validated(result)
        mutation = self.store.resolve_approval_committed(
            str(item["id"]),
            decision.decision,
            decision.feedback,
            requeue=self.requeue,
        )
        approval_run = self.store.read_run(run_id)
        self.events.publish(mutation, run=approval_run, item=mutation.value)
        self.resume_execution_slot(run_id, cancel)
        self.resume_effective_time()
        self.state_machine.track(
            RuntimeState.TOOL_EXECUTING,
            f"{transition_reason}_resolved",
        )
        return ApprovalOutcome(
            decision=decision.decision,
            feedback=(
                decision.feedback
                if decision.decision == "approve" or decision.feedback
                else APPROVAL_REJECTION_GUIDANCE
            ),
            item=pending_item,
        )

    @staticmethod
    def _validated(result: ApprovalResult) -> ApprovalResult:
        try:
            value = ApprovalDecisionDto.model_validate({
                "decision": result.decision,
                "feedback": result.feedback,
            })
        except ValidationError:
            return ApprovalResult("reject")
        return ApprovalResult(value.decision, value.feedback)

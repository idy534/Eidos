from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.application.results import ApplicationResult
from eidos_runtime.domain.tool import Approval
from eidos_runtime.persistence.repositories import TypedRuntimeRepository


class ApprovalDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


@runtime_checkable
class ApprovalRuntimePort(Protocol):
    """Delivers a decision to the live Runtime approval wait.

    SQLite remains authoritative for the persisted Approval state.  The
    Runtime port owns releasing the matching in-memory worker wait; writing
    ``SessionStore.resolve_approval`` here would bypass that live hand-off.
    """

    def submit_approval_response(
        self,
        *,
        request_id: str,
        decision: ApprovalDecision,
        feedback: str | None,
    ) -> bool: ...


@dataclass(frozen=True)
class ApprovalActionResult(ApplicationResult):
    """Result of delivering a response to an active Runtime approval wait."""

    request_id: str
    decision: ApprovalDecision
    accepted: bool
    feedback: str | None = None
    reason: str | None = None


class ApprovalApplication:
    """Coordinates typed Approval reads and live decision delivery.

    Durable state transitions stay with the existing Runtime approval
    coordinator.  This application service only reads typed persisted facts
    and routes user decisions through its explicit Runtime port.
    """

    def __init__(
        self,
        repository: TypedRuntimeRepository,
        runtime: ApprovalRuntimePort,
    ) -> None:
        self._repository = repository
        self._runtime = runtime

    def read(self, approval_id: str) -> Approval:
        self._require_identifier(approval_id, "approval_id")
        approval = self._repository.read_approval(approval_id)
        if approval is None:
            raise ApplicationError("RESOURCE_NOT_FOUND", "approval not found")
        return approval

    def pending(self, run_id: str) -> Approval | None:
        self._require_identifier(run_id, "run_id")
        return self._repository.read_pending_approval(run_id)

    def approve(self, request_id: str) -> ApprovalActionResult:
        return self._submit(request_id, ApprovalDecision.APPROVE, None)

    def reject(
        self, request_id: str, *, feedback: str | None = None
    ) -> ApprovalActionResult:
        self._validate_feedback(feedback)
        return self._submit(request_id, ApprovalDecision.REJECT, feedback)

    def submit_feedback(
        self, request_id: str, feedback: str
    ) -> ApprovalActionResult:
        self._validate_feedback(feedback)
        return self._submit(request_id, ApprovalDecision.REJECT, feedback)

    def _submit(
        self,
        request_id: str,
        decision: ApprovalDecision,
        feedback: str | None,
    ) -> ApprovalActionResult:
        self._require_identifier(request_id, "approval request id")
        accepted = self._runtime.submit_approval_response(
            request_id=request_id,
            decision=decision,
            feedback=feedback,
        )
        return ApprovalActionResult(
            request_id=request_id,
            decision=decision,
            feedback=feedback,
            accepted=accepted,
            reason=None if accepted else "approval_not_pending",
        )

    @staticmethod
    def _require_identifier(value: str, name: str) -> None:
        if not isinstance(value, str) or not value:
            raise ApplicationError("INVALID_STATE", f"{name} is required")

    @staticmethod
    def _validate_feedback(feedback: str | None) -> None:
        if feedback is None:
            return
        if not isinstance(feedback, str):
            raise ApplicationError("INVALID_STATE", "feedback must be text")
        if len(feedback.encode("utf-8")) > 2_000:
            raise ApplicationError("INVALID_STATE", "feedback exceeds 2000 bytes")


__all__ = [
    "ApprovalActionResult",
    "ApprovalApplication",
    "ApprovalDecision",
    "ApprovalRuntimePort",
]

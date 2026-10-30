from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import threading
from typing import Callable

from pydantic import BaseModel, ConfigDict, ValidationError

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.domain.approval_policy import ApprovalReview
from eidos_runtime.domain.hooks import HookOutcome
from eidos_runtime.model.client import ModelClient
from eidos_runtime.runtime.approval_review import review_approval
from eidos_runtime.protocol.schemas import ApprovalDecisionDto
from eidos_runtime.protocol.tool_text import project_approval_diff
from eidos_runtime.runtime.events import RuntimeEvents
from eidos_runtime.runtime.state_machine import RuntimePhaseTracker, RuntimeState
from eidos_runtime.runtime.fault_injection import hit_fault


APPROVAL_REJECTION_GUIDANCE = (
    "User rejected this approval. Do not repeat this rejected request; "
    "choose a different action or explain what remains blocked."
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
        reviewer: ModelClient | None = None,
        hook_policy: Callable[[dict[str, object], threading.Event, str], HookOutcome] | None = None,
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
        self.reviewer = reviewer
        self.last_review: ApprovalReview | None = None
        self.hook_policy = hook_policy

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
        self.last_review = None
        mode = self.store.read_run(run_id).get("approvalMode", "manual")
        if mode == "full_access":
            self.check_cancel(run_id, cancel)
            return ApprovalOutcome(decision="approve", item=item)
        pending = self.store.typed_runtime_repository().read_pending_approval(run_id)
        pending_request = json.loads(pending.request_json) if pending is not None else None
        if pending is not None and pending.item_id == str(item["id"]) and pending_request.get("requiresHumanConfirmation") is True:
            # A relaxed policy must not silently replace an existing human
            # confirmation. The remaining fingerprint still binds the action.
            description = {**description, "requiresHumanConfirmation": True,
                           "riskWarning": pending_request["riskWarning"]}
            if request is not None:
                request = {**request, "requiresHumanConfirmation": True,
                           "riskWarning": pending_request["riskWarning"]}
        human_confirmation = description.get("requiresHumanConfirmation") is True
        user_evidence = self.store.approval_user_evidence(run_id)
        fingerprint = hashlib.sha256(json.dumps({
            "tool": item.get("toolCall", {}).get("toolName"),
            "arguments": (None if description.get("kind") == "permission_request"
                          else item.get("toolCall", {}).get("argumentsJson")),
            # Derived display metadata must not invalidate old pending approvals
            # or rejection fingerprints. Arguments and permission fields still bind the action.
            "description": {key: value for key, value in description.items()
                            if key not in {"reason", "summary", "additionalFileSystemAccess"}},
            "diff": diff,
            "baseSha256": base_sha256,
            "userEvidence": user_evidence,
            **({"reviewScriptEvidence": request["reviewScriptEvidence"]}
               if request is not None and request.get("reviewScriptEvidence")
               and (pending_request is None or "reviewScriptEvidence" in pending_request) else {}),
        }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        request = {
            **(request or description),
            **project_approval_diff(request or description),
            "permissionBlockFingerprint": fingerprint,
        }
        rejected = self.store.rejected_approval(run_id, fingerprint)
        if rejected is not None:
            self.last_review = rejected.review
            return ApprovalOutcome(
                decision="reject",
                feedback=rejected.feedback or ("Automatic approval review rejected this request previously. Do not repeat it or bypass the refusal; choose a materially safer action." if mode == "auto_review" else APPROVAL_REJECTION_GUIDANCE),
                item=item,
            )
        if pending is None and self.hook_policy is not None:
            hook = self.hook_policy(item, cancel, fingerprint)
            if hook.permission_decision == "deny" or hook.deny:
                return ApprovalOutcome(decision="reject", feedback="\n".join(hook.feedback) or "A configured Hook denied this approval.", item=item)
            if hook.permission_decision == "allow" and not human_confirmation:
                self.check_cancel(run_id, cancel)
                self.last_review = ApprovalReview(source="hook", decision="approve",
                    reason_code="hook_permission_allowed", rationale="用户信任的 Hook 允许本次普通请求。")
        if pending is not None:
            if pending.item_id != str(item["id"]) or json.loads(pending.request_json).get(
                "permissionBlockFingerprint"
            ) != fingerprint:
                raise ApprovalTransportError("pending approval no longer matches prepared action")
            pending_item = self.store.read_item(pending.item_id)
        else:
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
            if human_confirmation:
                result = self._validated(self.transport.request(
                    ApprovalRequest({
                        "sessionId": pending_item["sessionId"], "runId": pending_item["runId"],
                        "itemId": pending_item["id"], "toolCallId": tool_call["id"],
                        **project_approval_diff(description),
                    }), cancel,
                ))
                if mode == "auto_review":
                    self.last_review = ApprovalReview(source="manual", decision=result.decision,
                        reason_code="dangerous_permission_confirmed", risk="high",
                        rationale="用户已决定本次高危操作。")
            elif mode == "auto_review" and self.last_review is None:
                if pending is not None:
                    self.last_review = ApprovalReview(source="model", decision="reject",
                        reason_code="auto_review_interrupted", rationale="自动审批在 Runtime 重启前未完成；原操作未获批准。")
                elif self.reviewer is None:
                    self.last_review = ApprovalReview(source="model", decision="reject",
                        reason_code="auto_review_unavailable", rationale="自动审批模型不可用；操作未获批准。")
                else:
                    evidence = json.dumps({
                        "userMessages": user_evidence,
                        "tool": item.get("toolCall", {}).get("toolName"),
                        "arguments": item.get("toolCall", {}).get("argumentsJson"),
                        "request": request, "diff": diff, "baseSha256": base_sha256,
                    }, ensure_ascii=False)
                    self.last_review = review_approval(self.reviewer, evidence, cancel)
            fallback_review = self.last_review if (
                mode == "auto_review" and self.last_review is not None
                and self.last_review.decision == "reject"
                and self.last_review.allows_manual_fallback
            ) else None
            if fallback_review is not None:
                self.check_cancel(run_id, cancel)
                result = self.transport.request(
                    ApprovalRequest({
                        "sessionId": pending_item["sessionId"], "runId": pending_item["runId"],
                        "itemId": pending_item["id"], "toolCallId": tool_call["id"],
                        **project_approval_diff(description),
                        "reviewFallback": fallback_review.rationale,
                    }), cancel,
                )
                result = self._validated(result)
                self.last_review = fallback_review.model_copy(update={
                    "source": "manual", "decision": result.decision,
                    "rationale": fallback_review.rationale[:1470] + (" 用户手动批准。" if result.decision == "approve" else " 用户未批准。"),
                })
            elif self.last_review is not None:
                feedback = None
                if self.last_review.decision == "reject":
                    feedback = self.last_review.rationale + " Do not retry the same action or circumvent this refusal. Choose a materially safer alternative or explain the blocker."
                result = ApprovalResult(self.last_review.decision, feedback)
            elif not human_confirmation:
                result = self.transport.request(
                    ApprovalRequest({
                        "sessionId": pending_item["sessionId"],
                        "runId": pending_item["runId"],
                        "itemId": pending_item["id"],
                        "toolCallId": tool_call["id"],
                        **project_approval_diff(description),
                    }), cancel,
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
            review=self.last_review,
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

    def invalidate_unstarted(
        self, run_id: str, item: dict[str, object], reason: str, cancel: threading.Event
    ) -> None:
        pending = self.store.typed_runtime_repository().read_pending_approval(run_id)
        if pending is None or pending.item_id != str(item["id"]):
            return
        mutation = self.store.typed_runtime_repository().invalidate_unstarted_approval_committed(
            str(item["id"]), reason
        )
        if not mutation.events:
            return
        self.events.publish(
            mutation, run=self.store.read_run(run_id),
            item=self.store.read_item(str(item["id"])),
        )
        self.resume_execution_slot(run_id, cancel)
        self.resume_effective_time()
        self.state_machine.track(RuntimeState.TOOL_EXECUTING, reason)

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

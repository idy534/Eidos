from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import logging
import threading
from typing import Callable

from pydantic import BaseModel, ConfigDict, ValidationError

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.domain.approval_policy import ApprovalReview
from eidos_runtime.domain.hooks import HookOutcome
from eidos_runtime.model.client import ModelClient
from eidos_runtime.runtime.approval_review import build_review_evidence, review_approval, review_feedback
from eidos_runtime.protocol.schemas import ApprovalDecisionDto
from eidos_runtime.protocol.tool_text import project_approval_diff
from eidos_runtime.runtime.events import RuntimeEvents
from eidos_runtime.runtime.state_machine import RuntimePhaseTracker, RuntimeState
from eidos_runtime.runtime.fault_injection import hit_fault
from eidos_runtime.sandbox.sensitive import default_scanner

logger = logging.getLogger("eidos.runtime")


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
            # grantScope is derived from the action and mode; requests created
            # before it existed must still match after an upgrade.
            "description": {key: value for key, value in description.items()
                            if key not in {"reason", "summary", "additionalFileSystemAccess", "grantScope"}},
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
        previous_unknown = False
        if (rejected is not None and rejected.review is not None
                and rejected.review.source == "model" and rejected.review.retryable):
            # A failed or inconclusive review is not a decision. Review again;
            # a second inconclusive review of the same action goes to the user.
            previous_unknown = rejected.review.reason_code == "auto_review_rejected"
            rejected = None
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
        # Send presentation text only to the human prompt, after binding the request to the action.
        display_intent = tool_call.get("displayIntent")
        human_description = project_approval_diff(description)
        if description.get("kind") == "command_execution" and isinstance(display_intent, str):
            human_description = {**human_description, "displayIntent": display_intent}
        suspend_deadline = getattr(cancel, "suspend_deadline", None)
        resume_deadline = getattr(cancel, "resume_deadline", None)
        if callable(suspend_deadline):
            suspend_deadline()
        try:
            triage = None
            if human_confirmation and mode == "auto_review" and pending is None and self.reviewer is not None:
                # The model may only decline a reserved request the user did not
                # ask for. Anything it allows, or cannot judge, goes to the user.
                action_title, preview = self._approval_progress_meta(human_description, tool_call)
                try:
                    self._publish_review_progress(
                        str(pending_item["sessionId"]), run_id, str(pending_item["id"]),
                        stage="preparing", step_index=1,
                        action_title=action_title, preview=preview,
                    )
                    evidence = self._review_evidence(user_evidence, item, request, diff, base_sha256)
                    triage = review_approval(
                        self.reviewer,
                        evidence,
                        cancel,
                        on_stage=lambda stage, attempt: self._publish_review_progress(
                            str(pending_item["sessionId"]), run_id, str(pending_item["id"]),
                            stage=stage,
                            step_index=3 if stage == "deciding" else 2,
                            action_title=action_title,
                            preview=preview,
                            attempt=attempt,
                        ),
                    )
                except Exception:  # Triage is advisory: any failure asks the user.
                    triage = None
                finally:
                    self._publish_review_progress(
                        str(pending_item["sessionId"]), run_id, str(pending_item["id"]),
                        stage="finished", step_index=3,
                        action_title=action_title,
                    )
            if (triage is not None and triage.decision == "reject"
                    and triage.reason_code == "auto_review_rejected" and triage.risk != "unknown"):
                self.last_review = triage
            elif human_confirmation:
                result = self._validated(self.transport.request(
                    ApprovalRequest({
                        "sessionId": pending_item["sessionId"], "runId": pending_item["runId"],
                        "itemId": pending_item["id"], "toolCallId": tool_call["id"],
                        **human_description,
                    }), cancel,
                ))
                if mode == "auto_review":
                    self.last_review = ApprovalReview(source="manual", decision=result.decision,
                        reason_code="dangerous_permission_confirmed", risk="high",
                        rationale="用户批准了本次高危操作。" if result.decision == "approve"
                        else "用户拒绝了本次高危操作。")
            elif mode == "auto_review" and self.last_review is None:
                # A review interrupted by a restart has no side effect; run it
                # again. One that had already handed the request to the user
                # stays with the user.
                escalation = pending_request.get("reviewEscalation") if pending_request else None
                if isinstance(escalation, dict):
                    self.last_review = ApprovalReview.model_validate(escalation)
                elif self.reviewer is None:
                    self.last_review = ApprovalReview(source="model", decision="reject",
                        reason_code="auto_review_unavailable", rationale="自动审批模型不可用，操作没有执行。")
                else:
                    action_title, preview = self._approval_progress_meta(human_description, tool_call)
                    try:
                        self._publish_review_progress(
                            str(pending_item["sessionId"]), run_id, str(pending_item["id"]),
                            stage="preparing", step_index=1,
                            action_title=action_title, preview=preview,
                        )
                        evidence = self._review_evidence(user_evidence, item, request, diff, base_sha256)
                        self.last_review = review_approval(
                            self.reviewer,
                            evidence,
                            cancel,
                            on_stage=lambda stage, attempt: self._publish_review_progress(
                                str(pending_item["sessionId"]), run_id, str(pending_item["id"]),
                                stage=stage,
                                step_index=3 if stage == "deciding" else 2,
                                action_title=action_title,
                                preview=preview,
                                attempt=attempt,
                            ),
                        )
                    finally:
                        self._publish_review_progress(
                            str(pending_item["sessionId"]), run_id, str(pending_item["id"]),
                            stage="finished", step_index=3,
                            action_title=action_title,
                        )
                    if (previous_unknown and self.last_review.reason_code == "auto_review_rejected"
                            and self.last_review.risk == "unknown"):
                        self.last_review = self.last_review.model_copy(update={
                            "reason_code": "auto_review_unresolved",
                            "rationale": self.last_review.rationale[:1400] + " 补充说明后仍无法判断，交给你决定。",
                        })
            fallback_review = self.last_review if (
                mode == "auto_review" and self.last_review is not None
                and self.last_review.decision == "reject"
                and self.last_review.allows_manual_fallback
            ) else None
            if fallback_review is not None:
                self.check_cancel(run_id, cancel)
                self.store.mark_review_escalated(str(item["id"]), fallback_review.model_dump(mode="json"))
                result = self.transport.request(
                    ApprovalRequest({
                        "sessionId": pending_item["sessionId"], "runId": pending_item["runId"],
                        "itemId": pending_item["id"], "toolCallId": tool_call["id"],
                        **human_description,
                        "reviewFallback": fallback_review.rationale,
                    }), cancel,
                )
                result = self._validated(result)
                self.last_review = fallback_review.model_copy(update={
                    "source": "manual", "decision": result.decision,
                    "rationale": fallback_review.rationale[:1470] + (" 用户手动批准。" if result.decision == "approve" else " 用户未批准。"),
                })
            elif self.last_review is not None and self.last_review.source != "manual":
                # Model and Hook decisions explain themselves. A human decision
                # above keeps the user's own result and feedback.
                feedback = review_feedback(self.last_review) if self.last_review.decision == "reject" else None
                result = ApprovalResult(self.last_review.decision, feedback)
            elif not human_confirmation:
                result = self.transport.request(
                    ApprovalRequest({
                        "sessionId": pending_item["sessionId"],
                        "runId": pending_item["runId"],
                        "itemId": pending_item["id"],
                        "toolCallId": tool_call["id"],
                        **human_description,
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

    @staticmethod
    def _approval_progress_meta(
        description: dict[str, object],
        tool_call: dict[str, object],
    ) -> tuple[str, str | None]:
        scanner = default_scanner()
        kind = description.get("kind")
        raw_preview: str | None = None
        if kind == "command_execution":
            intent = description.get("displayIntent") or tool_call.get("displayIntent")
            title = intent.strip() if isinstance(intent, str) and intent.strip() else "执行命令"
            if description.get("executionMode") == "unsandboxed":
                if "沙盒外" not in title:
                    title += "（沙盒外）"
            elif description.get("networkEnabled") and "联网" not in title:
                title += "（联网）"
            cmd = description.get("command")
            if isinstance(cmd, str) and cmd.strip():
                raw_preview = " ".join(cmd.strip().splitlines())
        elif kind == "file_change":
            title = "文件变更"
            access = description.get("additionalFileSystemAccess")
            if isinstance(access, list) and access and isinstance(access[0], dict):
                path = access[0].get("path")
                if isinstance(path, str) and path.strip():
                    raw_preview = path.strip()
        elif kind == "permission_request":
            title = "权限申请"
            cmd = description.get("command")
            if isinstance(cmd, str) and cmd.strip():
                raw_preview = " ".join(cmd.strip().splitlines())
        elif kind == "browser_access":
            title = "浏览器访问"
        elif kind == "network_access":
            title = "网络访问"
            target = description.get("target")
            if isinstance(target, str) and target.strip():
                raw_preview = target.strip()
        else:
            tool_name = description.get("toolName") or tool_call.get("toolName")
            title = f"外部工具 · {tool_name}" if isinstance(tool_name, str) and tool_name.strip() else "外部工具调用"

        redacted_title = " ".join(scanner.redact_for_presentation(title).text.split())[:80] or "操作"
        redacted_preview: str | None = None
        if raw_preview:
            cleaned = scanner.redact_for_presentation(raw_preview).text.strip()
            if cleaned:
                redacted_preview = cleaned[:240]
        return redacted_title, redacted_preview

    def _publish_review_progress(
        self,
        session_id: str,
        run_id: str,
        item_id: str,
        *,
        stage: str,
        step_index: int,
        action_title: str,
        preview: str | None = None,
        attempt: int | None = None,
    ) -> None:
        try:
            params: dict[str, object] = {
                "sessionId": session_id,
                "runId": run_id,
                "itemId": item_id,
                "stage": stage,
                "stepIndex": step_index,
                "totalSteps": 3,
                "actionTitle": action_title,
            }
            if preview:
                params["preview"] = preview
            if attempt is not None and attempt >= 1:
                params["attempt"] = attempt
            self.events.publish_transient("run/approvalReviewProgress", params)
        except Exception:
            logger.warning("Approval review progress notification was not published", exc_info=True)

    @staticmethod
    def _review_evidence(
        user_evidence: tuple[str, ...], item: dict[str, object], request: dict[str, object],
        diff: str, base_sha256: str | None,
    ) -> str:
        tool_call = item.get("toolCall", {})
        assert isinstance(tool_call, dict)
        return build_review_evidence(
            user_messages=user_evidence, tool=tool_call.get("toolName"),
            arguments=tool_call.get("argumentsJson"), request=request,
            diff=diff, base_sha256=base_sha256,
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

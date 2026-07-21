from __future__ import annotations

from eidos_runtime.runtime.contracts import (
    LoopAction,
    LoopDecision,
    RuntimeFailure,
    SamplingOutcome,
    ToolBatchOutcome,
)


class LoopDecisionEngine:
    """Maps closed sampling/tool outcomes to one loop action."""

    def after_validation(
        self,
        sampling: SamplingOutcome,
        tools: ToolBatchOutcome,
        *,
        protocol_errors: int,
    ) -> LoopDecision:
        if tools.status == "ready":
            return LoopDecision(action=LoopAction.EXECUTE_TOOLS)
        if tools.status == "no_tools" and sampling.assistant_item is not None:
            return LoopDecision(action=LoopAction.COMPLETE)
        if tools.status in {"validation_failed", "no_tools"}:
            reason = tools.error_code or "empty_response"
            if protocol_errors < 2:
                return LoopDecision(action=LoopAction.CONTINUE, reason=reason)
            return LoopDecision(
                action=LoopAction.FAIL,
                reason=reason,
                failure=RuntimeFailure(
                    code="MODEL_PROTOCOL_ERROR", reason=reason
                ),
            )
        return LoopDecision(
            action=LoopAction.FAIL,
            failure=RuntimeFailure(code="INTERNAL_ERROR"),
        )

    def after_tools(self, outcome: ToolBatchOutcome) -> LoopDecision:
        if outcome.status in {"completed", "sensitive_rejected"}:
            return LoopDecision(action=LoopAction.CONTINUE)
        if outcome.status == "paused":
            return LoopDecision(
                action=LoopAction.PAUSE, reason=outcome.pause_reason
            )
        return LoopDecision(
            action=LoopAction.FAIL,
            failure=RuntimeFailure(code="INTERNAL_ERROR"),
        )

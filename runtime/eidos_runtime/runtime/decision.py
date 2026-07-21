from __future__ import annotations

from eidos_runtime.context.budget import ContextBudget
from eidos_runtime.runtime.contracts import (
    LoopAction,
    LoopDecision,
    RuntimeFailure,
    RunBudget,
    SamplingOutcome,
    ToolBatchOutcome,
)


class LoopDecisionEngine:
    """Applies one priority order to every loop boundary."""

    def decide(
        self,
        *,
        sampling: SamplingOutcome | None = None,
        tool_batch: ToolBatchOutcome | None = None,
        pending_user_input: bool = False,
        context_budget: ContextBudget | None = None,
        run_budget: RunBudget | None = None,
        compaction_count: int = 0,
        loop_guard_result: str | None = None,
        cancelled: bool = False,
        protocol_errors: int = 0,
    ) -> LoopDecision:
        if cancelled:
            return LoopDecision(action=LoopAction.CANCEL)
        if loop_guard_result is not None:
            return LoopDecision(action=LoopAction.PAUSE, reason=loop_guard_result)
        if context_budget is not None and not context_budget.fits:
            if compaction_count < 2:
                return LoopDecision(action=LoopAction.COMPACT, reason="context_over_budget")
            return LoopDecision(
                action=LoopAction.PAUSE, reason="context_still_over_budget"
            )
        if run_budget is not None:
            if run_budget.run_steps_remaining <= 0 or run_budget.run_effective_ms_remaining <= 0:
                return LoopDecision(action=LoopAction.FINALIZE, reason="run_budget_exhausted")
            if (
                run_budget.segment_steps_remaining <= 0
                or run_budget.segment_effective_ms_remaining <= 0
            ):
                return LoopDecision(action=LoopAction.PAUSE, reason="segment_budget_exhausted")
        if tool_batch is not None:
            if tool_batch.status == "paused":
                return LoopDecision(action=LoopAction.PAUSE, reason=tool_batch.pause_reason)
            if tool_batch.status in {"completed", "sensitive_rejected", "ready"}:
                return LoopDecision(action=LoopAction.CONTINUE)
            if pending_user_input and tool_batch.status == "no_tools":
                return LoopDecision(action=LoopAction.CONTINUE, reason="pending_input")
            if tool_batch.status == "no_tools" and sampling is not None and sampling.assistant_item is not None:
                return LoopDecision(action=LoopAction.COMPLETE)
            if tool_batch.status in {"validation_failed", "no_tools"}:
                reason = tool_batch.error_code or "empty_response"
                if protocol_errors < 2:
                    return LoopDecision(action=LoopAction.CONTINUE, reason=reason)
                return LoopDecision(
                    action=LoopAction.FAIL,
                    reason=reason,
                    failure=RuntimeFailure(code="MODEL_PROTOCOL_ERROR", reason=reason),
                )
        if pending_user_input:
            return LoopDecision(action=LoopAction.CONTINUE, reason="pending_input")
        return LoopDecision(action=LoopAction.CONTINUE)

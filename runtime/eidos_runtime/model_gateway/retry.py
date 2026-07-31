from __future__ import annotations

from pydantic import Field

from eidos_runtime.models import EidosFrozenStrictModel
from eidos_runtime.model_gateway.errors import EidosModelError
from eidos_runtime.model_gateway.models import RetryPolicy


class RetryState(EidosFrozenStrictModel):
    attempt_number: int = Field(ge=1)
    complete_tool_call_emitted: bool = False
    tool_result_committed: bool = False


class RetryDecision(EidosFrozenStrictModel):
    retry: bool
    reason: str
    backoff_seconds: float = Field(ge=0)


def retry_decision(
    error: EidosModelError,
    state: RetryState,
    policy: RetryPolicy | None = None,
) -> RetryDecision:
    policy = policy or RetryPolicy()
    if state.complete_tool_call_emitted or state.tool_result_committed:
        return RetryDecision(
            retry=False,
            reason="unsafe_tool_progress",
            backoff_seconds=0,
        )
    if not error.retryable:
        return RetryDecision(
            retry=False,
            reason="non_retryable_error",
            backoff_seconds=0,
        )
    if state.attempt_number >= policy.max_attempts:
        return RetryDecision(
            retry=False,
            reason="retry_budget_exhausted",
            backoff_seconds=0,
        )
    backoff = min(
        policy.initial_backoff_seconds * 2 ** (state.attempt_number - 1),
        policy.max_backoff_seconds,
    )
    if error.retry_after is not None:
        backoff = max(backoff, error.retry_after)
    return RetryDecision(retry=True, reason="transient_error", backoff_seconds=backoff)

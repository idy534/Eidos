from __future__ import annotations

import httpx
from pydantic import Field

from eidos_runtime.models import EidosFrozenStrictModel
from eidos_runtime.model_gateway.errors import EidosModelError
from eidos_runtime.model_gateway.models import RetryPolicy


RETRYABLE_HTTP_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
RETRYABLE_TRANSPORT_EXCEPTIONS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.ReadError,
    httpx.WriteError,
    httpx.RemoteProtocolError,
)


class RetryState(EidosFrozenStrictModel):
    """Eidos-owned safety state for one logical model request."""

    attempt_number: int = Field(ge=1)
    visible_output_emitted: bool = False
    complete_tool_call_emitted: bool = False
    tool_result_committed: bool = False
    canceled: bool = False


class RetryDecision(EidosFrozenStrictModel):
    retry: bool
    reason: str
    # Backoff selection belongs to Tenacity.  This field remains in the durable
    # decision DTO for backwards-compatible diagnostics only.
    backoff_seconds: float = Field(default=0, ge=0)


def is_retryable_http_status(status_code: int) -> bool:
    return status_code in RETRYABLE_HTTP_STATUSES


def is_retryable_transport_exception(error: BaseException) -> bool:
    if isinstance(error, httpx.HTTPStatusError):
        return is_retryable_http_status(error.response.status_code)
    return isinstance(error, RETRYABLE_TRANSPORT_EXCEPTIONS)


def retry_decision(
    error: EidosModelError | BaseException | object,
    state: RetryState,
    policy: RetryPolicy | None = None,
) -> RetryDecision:
    """Classify whether a pre-stream transport failure is safe to retry.

    Tenacity owns timing and execution.  Eidos owns the policy and stream safety
    boundary so the same classification is used by the transport and terminal
    ModelAttempt diagnostics.
    """
    policy = policy or RetryPolicy()
    if state.canceled:
        return RetryDecision(retry=False, reason="canceled")
    if (
        state.visible_output_emitted
        or state.complete_tool_call_emitted
        or state.tool_result_committed
    ):
        return RetryDecision(retry=False, reason="unsafe_stream_progress")
    if not _is_transport_retryable(error):
        return RetryDecision(retry=False, reason="non_retryable_error")
    if state.attempt_number >= policy.max_attempts:
        return RetryDecision(retry=False, reason="retry_budget_exhausted")
    return RetryDecision(retry=True, reason="transport_retry")


def _is_transport_retryable(error: EidosModelError | BaseException | object) -> bool:
    if isinstance(error, BaseException):
        return is_retryable_transport_exception(error)
    if isinstance(error, EidosModelError):
        return error.retryable and (
            error.http_status is None or is_retryable_http_status(error.http_status)
        )
    retryable = getattr(error, "retryable", False)
    status_code = getattr(error, "status_code", None)
    return bool(retryable) and (
        status_code is None or is_retryable_http_status(status_code)
    )

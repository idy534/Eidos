from __future__ import annotations

import httpx2 as httpx
from pydantic import Field
from pydantic_ai.retries import wait_retry_after
from tenacity import RetryCallState, wait_exponential, wait_random

from eidos_runtime.models import EidosFrozenStrictModel, JsonSafeInt
from eidos_runtime.model.errors import EidosModelError
from eidos_runtime.model.gateway_types import RetryPolicy


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


class ModelRetryRecord(EidosFrozenStrictModel):
    attempt_id: str
    request_group_id: str
    retry_index: JsonSafeInt = Field(ge=0, le=9)
    deadline_at: JsonSafeInt | None = None
    next_retry_at: JsonSafeInt | None = None


class RetryDecision(EidosFrozenStrictModel):
    retry: bool
    reason: str
    # Backoff selection belongs to Tenacity.  This field remains in the durable
    # decision DTO for backwards-compatible diagnostics only.
    backoff_seconds: float = Field(default=0, ge=0)
    deadline_at: JsonSafeInt | None = None
    next_retry_at: JsonSafeInt | None = None


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
    """Allow a fresh response while this request has executed no tools.

    Tenacity owns timing and execution.  Eidos owns the policy and stream safety
    boundary so the same classification is used by the transport and terminal
    ModelAttempt diagnostics.
    """
    policy = policy or RetryPolicy()
    if state.canceled:
        return RetryDecision(retry=False, reason="canceled")
    if (
        state.complete_tool_call_emitted
        or state.tool_result_committed
    ):
        return RetryDecision(retry=False, reason="unsafe_stream_progress")
    if not _is_transport_retryable(error):
        return RetryDecision(retry=False, reason="non_retryable_error")
    if state.attempt_number >= policy.max_attempts:
        return RetryDecision(retry=False, reason="retry_budget_exhausted")
    return RetryDecision(retry=True, reason="transport_retry")


def retry_after_seconds(headers: object) -> float | None:
    """Delegate both Retry-After formats to the locked Pydantic AI parser."""
    if not hasattr(headers, "get"):
        return None
    value = headers.get("retry-after")
    if not isinstance(value, str) or len(value) > 256:
        return None
    response = httpx.Response(429, headers={"retry-after": value}, request=httpx.Request("POST", "https://retry.invalid"))
    state = RetryCallState(None, None, (), {})
    error = httpx.HTTPStatusError("retry", request=response.request, response=response)
    state.set_exception((type(error), error, None))
    delay = wait_retry_after(lambda _: -1, max_wait=float("inf"))(state)
    return delay if delay >= 0 else None


def select_retry_delay(policy: RetryPolicy, attempt_number: int, server_delay: float | None) -> float:
    state = RetryCallState(None, None, (), {})
    state.attempt_number = attempt_number
    delay = wait_exponential(multiplier=policy.initial_backoff_seconds, max=policy.max_backoff_seconds)(state)
    jitter = wait_random(0, min(0.5, policy.initial_backoff_seconds / 4))(state)
    return max(delay, server_delay or 0) + jitter


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

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import inspect
import logging
import threading

import httpx
from pydantic_ai.retries import AsyncTenacityTransport, RetryConfig, wait_retry_after
from tenacity import RetryCallState, retry_if_exception, stop_after_attempt, wait_exponential

from eidos_runtime.model.config import MODEL_CATALOG, ModelConfig
from eidos_runtime.model_gateway.models import RetryPolicy
from eidos_runtime.model_gateway.retry import (
    RetryState,
    is_retryable_transport_exception,
    retry_decision,
)


logger = logging.getLogger("eidos.runtime.model_gateway.retry_transport")


class RetryBackoffCanceled(RuntimeError):
    """Raised inside a retry sleep so cancellation never becomes RetryError."""


@dataclass
class RetryTracker:
    """Request-scoped retry diagnostics; it is never shared between Runs."""

    transport_attempt_count: int = 0
    transport_retry_count: int = 0
    last_retry_reason: str | None = None
    last_backoff_seconds: float | None = None
    retry_after_applied: bool = False
    last_http_status: int | None = None


@dataclass(frozen=True)
class _RetryRequestScope:
    cancel: threading.Event
    tracker: RetryTracker


_retry_request_scope: ContextVar[_RetryRequestScope | None] = ContextVar(
    "eidos_model_retry_request_scope", default=None
)


class RetryTransportClient:
    """Owns one profile-frozen HTTP client and its official retry transport."""

    def __init__(
        self,
        profile: ModelConfig,
        *,
        wrapped: httpx.AsyncBaseTransport | None = None,
        timeout: httpx.Timeout | None = None,
        wire_api: str = "chat_completions",
    ) -> None:
        self._profile = profile
        self._wire_api = wire_api
        self._policy = RetryPolicy()
        self._sleep_for_testing: Callable[[float], object] | None = None
        fallback = wait_exponential(
            multiplier=self._policy.initial_backoff_seconds,
            max=self._policy.max_backoff_seconds,
        )
        retry_config: RetryConfig = {
            "retry": retry_if_exception(is_retryable_transport_exception),
            "stop": stop_after_attempt(self._policy.max_attempts),
            "wait": wait_retry_after(
                fallback_strategy=fallback,
                max_wait=self._policy.max_backoff_seconds,
            ),
            "sleep": self._cancellation_aware_sleep,
            "before": self._before_attempt,
            "before_sleep": self._before_sleep,
            "reraise": True,
        }
        self.transport = AsyncTenacityTransport(
            retry_config,
            wrapped=wrapped,
            validate_response=self._validate_response,
        )
        self.http_client = httpx.AsyncClient(
            transport=self.transport,
            timeout=timeout,
            follow_redirects=False,
        )

    @contextmanager
    def request_scope(
        self,
        cancel: threading.Event,
        tracker: RetryTracker | None = None,
    ) -> Iterator[RetryTracker]:
        tracker = tracker or RetryTracker()
        token = _retry_request_scope.set(_RetryRequestScope(cancel, tracker))
        try:
            yield tracker
        finally:
            _retry_request_scope.reset(token)

    def set_sleep_for_testing(self, sleep: Callable[[float], object]) -> None:
        self._sleep_for_testing = sleep

    async def aclose(self) -> None:
        await self.http_client.aclose()

    def _validate_response(self, response: httpx.Response) -> None:
        if response.status_code in _retryable_statuses():
            response.raise_for_status()

    def _before_attempt(self, state: RetryCallState) -> None:
        scope = _retry_request_scope.get()
        if scope is not None:
            scope.tracker.transport_attempt_count = state.attempt_number

    def _before_sleep(self, state: RetryCallState) -> None:
        scope = _retry_request_scope.get()
        error = state.outcome.exception() if state.outcome is not None else None
        if scope is None or not isinstance(error, BaseException):
            return
        decision = retry_decision(
            error,
            RetryState(attempt_number=state.attempt_number, canceled=scope.cancel.is_set()),
            self._policy,
        )
        tracker = scope.tracker
        tracker.transport_retry_count = state.attempt_number
        tracker.last_retry_reason = decision.reason
        tracker.last_backoff_seconds = (
            state.next_action.sleep if state.next_action is not None else None
        )
        if isinstance(error, httpx.HTTPStatusError):
            tracker.last_http_status = error.response.status_code
            tracker.retry_after_applied = "retry-after" in error.response.headers
        logger.info(
            "model transport retry provider=%s model_id=%s wire_api=%s "
            "transport_attempt_number=%s max_attempts=%s failure_classification=%s "
            "http_status=%s selected_backoff_seconds=%s retry_after_applied=%s",
            MODEL_CATALOG.provider_id_for(self._profile.id),
            self._profile.id,
            self._wire_api,
            state.attempt_number,
            self._policy.max_attempts,
            decision.reason,
            tracker.last_http_status,
            tracker.last_backoff_seconds,
            tracker.retry_after_applied,
        )

    async def _cancellation_aware_sleep(self, seconds: float) -> None:
        scope = _retry_request_scope.get()
        if scope is None:
            await self._sleep(seconds)
            return
        remaining = max(seconds, 0.0)
        while remaining > 0:
            if scope.cancel.is_set():
                raise RetryBackoffCanceled("sampling canceled")
            interval = min(remaining, 0.025)
            await self._sleep(interval)
            remaining -= interval
        if scope.cancel.is_set():
            raise RetryBackoffCanceled("sampling canceled")

    async def _sleep(self, seconds: float) -> None:
        selected = self._sleep_for_testing
        if selected is None:
            await asyncio.sleep(seconds)
            return
        value = selected(seconds)
        if inspect.isawaitable(value):
            await value


def build_retrying_http_client(
    profile: ModelConfig,
    *,
    wrapped: httpx.AsyncBaseTransport | None = None,
    timeout: httpx.Timeout | None = None,
    wire_api: str = "chat_completions",
) -> RetryTransportClient:
    return RetryTransportClient(
        profile, wrapped=wrapped, timeout=timeout, wire_api=wire_api
    )


def _retryable_statuses() -> frozenset[int]:
    # Keep the only status authority in retry.py while avoiding a mutable module
    # alias in the transport's public construction path.
    from eidos_runtime.model_gateway.retry import RETRYABLE_HTTP_STATUSES

    return RETRYABLE_HTTP_STATUSES

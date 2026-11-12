"""Backoff timing, Retry-After and cancellation of the model retry transport.

The transport's sleep hook records the waits, so no test sleeps for real.
"""
from __future__ import annotations

import threading

import anyio
import httpx2 as httpx
import pytest

from eidos_runtime.model.config import ModelConfig
from eidos_runtime.model.gateway_types import RetryPolicy
from eidos_runtime.model.retry_transport import (
    RetryBackoffCanceled,
    RetryTracker,
    RetryTransportClient,
    build_retrying_http_client,
)

URL = "https://fixture.invalid/chat/completions"
# One slice of a cancellable wait; the transport re-checks cancel between slices.
SLICE_SECONDS = 0.025


def _client(responses, *, on_request=None, policy: RetryPolicy | None = None) -> tuple[RetryTransportClient, list[httpx.Request]]:
    """Serve `responses` in order and keep repeating the last one."""
    requests: list[httpx.Request] = []
    remaining = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if on_request is not None:
            on_request()
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    config = ModelConfig(
        id="deepseek-flash",
        name="Fixture",
        vendor="deepseek",
        url="https://api.deepseek.com",
        api_key="fixture",
        supports_tool_call=True,
        supports_reasoning=False,
    )
    client = build_retrying_http_client(
        config,
        wrapped=httpx.MockTransport(handler),
        retry_policy=policy or RetryPolicy(max_attempts=3, initial_backoff_seconds=0.2, max_backoff_seconds=2.0),
    )
    return client, requests


def _overloaded(headers: dict[str, str] | None = None, status: int = 503) -> httpx.Response:
    return httpx.Response(status, headers=headers, json={"error": {"code": "server_is_overloaded"}})


def _ok() -> httpx.Response:
    return httpx.Response(200, json={"ok": True})


def test_backoff_doubles_per_retry_and_is_reported_on_the_tracker():
    client, requests = _client([_overloaded(), _overloaded(), _ok()])
    slept: list[float] = []
    client.set_sleep_for_testing(slept.append)
    tracker = RetryTracker()

    async def check() -> None:
        try:
            with client.request_scope(threading.Event(), tracker):
                response = await client.http_client.post(URL)
        finally:
            await client.aclose()
        assert response.status_code == 200

    anyio.run(check)

    assert len(requests) == 3
    # 0.2s after the first failure, 0.4s after the second.
    assert sum(slept) == pytest.approx(0.6)
    assert max(slept) <= SLICE_SECONDS + 1e-9
    assert tracker.transport_attempt_count == 3
    assert tracker.transport_retry_count == 2
    assert tracker.last_backoff_seconds == pytest.approx(0.4)
    assert tracker.last_http_status == 503
    assert tracker.retry_after_applied is False
    assert tracker.last_retry_reason


@pytest.mark.parametrize(("retry_after", "expected_wait"), [("1", 1.0), ("30", 2.0)])
def test_retry_after_replaces_the_fallback_and_is_capped_by_the_policy(retry_after, expected_wait):
    client, requests = _client([_overloaded({"retry-after": retry_after}, status=429), _ok()])
    slept: list[float] = []
    client.set_sleep_for_testing(slept.append)
    tracker = RetryTracker()

    async def check() -> None:
        try:
            with client.request_scope(threading.Event(), tracker):
                assert (await client.http_client.post(URL)).status_code == 200
        finally:
            await client.aclose()

    anyio.run(check)

    assert len(requests) == 2
    assert sum(slept) == pytest.approx(expected_wait)
    assert tracker.last_backoff_seconds == pytest.approx(expected_wait)
    assert tracker.retry_after_applied is True
    assert tracker.last_http_status == 429


def test_cancel_during_backoff_stops_before_the_next_attempt():
    client, requests = _client([_overloaded()])
    cancel = threading.Event()
    slept: list[float] = []

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        cancel.set()

    client.set_sleep_for_testing(sleep)

    async def check() -> None:
        try:
            with client.request_scope(cancel), pytest.raises(RetryBackoffCanceled):
                await client.http_client.post(URL)
        finally:
            await client.aclose()

    anyio.run(check)

    # The wait ended after one slice, and the request was not sent again.
    assert len(slept) == 1
    assert len(requests) == 1


def test_a_request_canceled_before_the_backoff_does_not_wait_at_all():
    cancel = threading.Event()
    client, requests = _client([_overloaded()], on_request=cancel.set)
    slept: list[float] = []
    client.set_sleep_for_testing(slept.append)

    async def check() -> None:
        try:
            with client.request_scope(cancel), pytest.raises(RetryBackoffCanceled):
                await client.http_client.post(URL)
        finally:
            await client.aclose()

    anyio.run(check)

    assert slept == []
    assert len(requests) == 1


def test_without_a_request_scope_the_wait_is_one_uninterrupted_sleep():
    client, requests = _client([_overloaded(), _ok()])
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    client.set_sleep_for_testing(sleep)

    async def check() -> None:
        try:
            assert (await client.http_client.post(URL)).status_code == 200
        finally:
            await client.aclose()

    anyio.run(check)

    assert slept == [pytest.approx(0.2)]
    assert len(requests) == 2


def test_a_status_that_is_not_retryable_is_returned_without_waiting():
    client, requests = _client([httpx.Response(400, json={"error": {"code": "invalid_request"}})])
    slept: list[float] = []
    client.set_sleep_for_testing(slept.append)
    tracker = RetryTracker()

    async def check() -> None:
        try:
            with client.request_scope(threading.Event(), tracker):
                assert (await client.http_client.post(URL)).status_code == 400
        finally:
            await client.aclose()

    anyio.run(check)

    assert slept == []
    assert len(requests) == 1
    assert tracker.transport_attempt_count == 1
    assert tracker.transport_retry_count == 0

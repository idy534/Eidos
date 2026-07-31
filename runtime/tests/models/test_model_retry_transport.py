from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch

import httpx
import pytest


RUNTIME_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.model_gateway.models import (  # noqa: E402
    ModelProfile,
    ReasoningMode,
    RetryPolicy,
    WireAPI,
)
from eidos_runtime.model_gateway.retry_transport import (  # noqa: E402
    RetryBackoffCanceled,
    build_retrying_http_client,
)
from eidos_runtime.model_gateway.capabilities import resolve_model_capabilities  # noqa: E402
from eidos_runtime.model_gateway.gateway import (  # noqa: E402
    legacy_profile_snapshot,
    model_profile_spec,
)
from eidos_runtime.model_gateway.pydantic_factory import build_pydantic_model  # noqa: E402
from eidos_runtime.model_gateway.presets import PRESETS  # noqa: E402
from eidos_runtime.model_gateway.models import RunModelSnapshot  # noqa: E402
from eidos_runtime.model.pydantic_ai_client import PydanticAIModelClient  # noqa: E402
from eidos_runtime.db.storage import SessionStore  # noqa: E402
from eidos_runtime.runtime.engine import RuntimeEngine  # noqa: E402
from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel  # noqa: E402


NOW = datetime(2026, 7, 31, tzinfo=UTC)


def profile(**updates: object) -> ModelProfile:
    values: dict[str, object] = {
        "id": "profile-retry",
        "name": "Retry fixture",
        "provider": "deepseek",
        "base_url": "https://api.example.test/v1",
        "auth_reference": "env:RETRY_TEST_KEY",
        "wire_api": WireAPI.OPENAI_CHAT_COMPLETIONS,
        "model_id": "deepseek-v4-flash",
        "context_window": 128_000,
        "max_output_tokens": 4_096,
        "reasoning_mode": ReasoningMode.NONE,
        "request_timeout": 30.0,
        "retry_policy": RetryPolicy(
            max_attempts=3,
            initial_backoff_seconds=0,
            max_backoff_seconds=2,
        ),
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(updates)
    return ModelProfile.model_validate(values)


class ScriptedAsyncTransport(httpx.AsyncBaseTransport):
    def __init__(self, outcomes: Sequence[httpx.Response | Exception]) -> None:
        self.outcomes = list(outcomes)
        self.request_count = 0
        self.closed = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.request_count += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def aclose(self) -> None:
        self.closed += 1


def response(status_code: int, *, retry_after: str | None = None) -> httpx.Response:
    headers = {"retry-after": retry_after} if retry_after is not None else {}
    return httpx.Response(status_code, headers=headers, json={})


async def request_with_tracker(
    transport: ScriptedAsyncTransport,
    *,
    value: ModelProfile | None = None,
    cancel: threading.Event | None = None,
):
    client = build_retrying_http_client(value or profile(), wrapped=transport)
    with client.request_scope(cancel or threading.Event()) as tracker:
        result = await client.http_client.get("https://api.example.test/v1/models")
    return client, tracker, result


@pytest.mark.parametrize("status", [408, 425, 500, 502, 503, 504])
def test_retryable_statuses_succeed_with_one_request_scoped_tracker(status: int) -> None:
    async def scenario() -> None:
        transport = ScriptedAsyncTransport([response(status), response(status), response(200)])
        client, tracker, result = await request_with_tracker(transport)
        try:
            assert result.status_code == 200
            assert transport.request_count == 3
            assert tracker.transport_attempt_count == 3
            assert tracker.transport_retry_count == 2
            assert tracker.last_retry_reason == "transport_retry"
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_frozen_profile_factory_retries_once_and_persists_one_model_attempt() -> None:
    stream = httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content=(
            b'data: {"id":"response-1","object":"chat.completion.chunk",'
            b'"created":0,"model":"fixture-model",'
            b'"choices":[{"index":0,"delta":{"content":"OK"},'
            b'"finish_reason":null}]}\n\n'
            b'data: {"id":"response-1","object":"chat.completion.chunk",'
            b'"created":0,"model":"fixture-model",'
            b'"choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}\n\n'
            b'data: [DONE]\n\n'
        ),
    )
    transport = ScriptedAsyncTransport([response(503), stream])
    frozen_profile = profile()
    frozen = RunModelSnapshot(
        profile=frozen_profile,
        capability=resolve_model_capabilities(frozen_profile, PRESETS["deepseek"]),
        frozen_at=NOW,
    )

    def build_with_scripted_transport(value: ModelProfile, *, timeout: httpx.Timeout):
        return build_retrying_http_client(value, wrapped=transport, timeout=timeout)

    with patch(
        "eidos_runtime.model_gateway.pydantic_factory.build_retrying_http_client",
        side_effect=build_with_scripted_transport,
    ):
        built = build_pydantic_model(frozen, "provider-key-value-123456")
    kernel = RuntimeAsyncKernel()
    kernel.start()
    client = PydanticAIModelClient(
        built.model,
        model_profile_spec(frozen),
        openai_client=built.provider_client,
        provider_client=built.provider_client,
        retry_transport=built.retry_client,
        profile_snapshot=legacy_profile_snapshot(frozen),
        settings_extra_body={"thinking": {"type": "disabled"}},
        parallel_tool_calls=False,
        async_kernel=kernel,
    )
    try:
        with tempfile.TemporaryDirectory(prefix="eidos-retry-integration-") as temporary:
            root = Path(temporary)
            data = root / "data"
            workspace = root / "workspace"
            data.mkdir(mode=0o700)
            workspace.mkdir()
            store = SessionStore(data)
            store.initialize()
            try:
                session = store.create_session(str(workspace))
                run, _ = store.create_run(
                    session["id"],
                    "retry once",
                    model_profile=legacy_profile_snapshot(frozen),
                )
                RuntimeEngine(store, client, lambda _event: None).run(
                    run["id"], threading.Event()
                )
                attempts = store.read_model_attempts(run["id"])
                assert len(attempts) == 1
                assert attempts[0]["status"] == "completed"
                assert attempts[0]["retryDecision"]["transportRetryCount"] == 1
                assert attempts[0]["retryDecision"]["reason"] == "completed"
                assert transport.request_count == 2
            finally:
                store.close()
    finally:
        client.close()
        kernel.close()
    assert built.retry_client.http_client.is_closed


def test_retry_client_close_is_idempotent_and_closes_wrapped_transport_once() -> None:
    async def scenario() -> None:
        transport = ScriptedAsyncTransport([response(200)])
        client = build_retrying_http_client(profile(), wrapped=transport)
        await client.aclose()
        await client.aclose()
        assert transport.closed == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "retry_after",
    ["60", format_datetime(datetime.now(UTC) + timedelta(seconds=60))],
)
def test_retry_after_is_capped_by_profile_maximum_backoff(retry_after: str) -> None:
    async def scenario() -> None:
        transport = ScriptedAsyncTransport([response(429, retry_after=retry_after), response(200)])
        client = build_retrying_http_client(profile(), wrapped=transport)
        sleeps: list[float] = []
        client.set_sleep_for_testing(lambda seconds: sleeps.append(seconds))
        with client.request_scope(threading.Event()) as tracker:
            result = await client.http_client.get("https://api.example.test/v1/models")
        try:
            assert result.status_code == 200
            assert tracker.transport_retry_count == 1
            assert sum(sleeps) == pytest.approx(2)
            assert all(seconds <= 0.025 for seconds in sleeps)
            assert tracker.retry_after_applied is True
            assert tracker.last_backoff_seconds == 2
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_retries_connect_and_read_failures() -> None:
    async def scenario() -> None:
        request = httpx.Request("GET", "https://api.example.test/v1/models")
        transport = ScriptedAsyncTransport([
            httpx.ConnectError("offline", request=request),
            httpx.ReadTimeout("slow", request=request),
            response(200),
        ])
        client, tracker, result = await request_with_tracker(transport)
        try:
            assert result.status_code == 200
            assert transport.request_count == 3
            assert tracker.transport_retry_count == 2
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_budget_is_total_requests_and_reraises_last_http_error() -> None:
    async def scenario() -> None:
        transport = ScriptedAsyncTransport([response(503), response(503), response(503)])
        client = build_retrying_http_client(profile(), wrapped=transport)
        try:
            with client.request_scope(threading.Event()) as tracker, pytest.raises(httpx.HTTPStatusError):
                await client.http_client.get("https://api.example.test/v1/models")
            assert transport.request_count == 3
            assert tracker.transport_attempt_count == 3
            assert tracker.transport_retry_count == 2
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_one_attempt_never_retries() -> None:
    async def scenario() -> None:
        transport = ScriptedAsyncTransport([response(503)])
        client = build_retrying_http_client(
            profile(retry_policy=RetryPolicy(max_attempts=1)), wrapped=transport
        )
        try:
            with client.request_scope(threading.Event()), pytest.raises(httpx.HTTPStatusError):
                await client.http_client.get("https://api.example.test/v1/models")
            assert transport.request_count == 1
        finally:
            await client.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("status", [400, 401, 403, 404, 413, 422])
def test_non_retryable_statuses_are_returned_to_the_model_error_mapper(status: int) -> None:
    async def scenario() -> None:
        transport = ScriptedAsyncTransport([response(status)])
        client = build_retrying_http_client(profile(), wrapped=transport)
        try:
            with client.request_scope(threading.Event()):
                result = await client.http_client.get("https://api.example.test/v1/models")
            assert result.status_code == status
            assert transport.request_count == 1
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_cancel_during_backoff_stops_before_a_second_request() -> None:
    async def scenario() -> None:
        transport = ScriptedAsyncTransport([response(503), response(200)])
        cancel = threading.Event()
        client = build_retrying_http_client(
            profile(retry_policy=RetryPolicy(
                max_attempts=3,
                initial_backoff_seconds=2,
                max_backoff_seconds=2,
            )),
            wrapped=transport,
        )
        try:
            with client.request_scope(cancel):
                task = asyncio.create_task(
                    client.http_client.get("https://api.example.test/v1/models")
                )
                while transport.request_count != 1:
                    await asyncio.sleep(0)
                cancel.set()
                with pytest.raises(RetryBackoffCanceled):
                    await task
            assert transport.request_count == 1
        finally:
            await client.aclose()

    asyncio.run(scenario())

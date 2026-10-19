from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import anyio
import httpx2 as httpx
from pydantic_ai.exceptions import ModelHTTPError
import pytest

from eidos_runtime.db.database import Database
from eidos_runtime.db.errors import InvalidRunStateError
from eidos_runtime.db.schema import V30_SCHEMA_SQL, SCHEMA_VERSION
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.model.client import (
    ModelRequestError,
    ModelRequestFailure,
    ModelResponse,
    ModelToolCall,
    ScriptedModel,
)
from eidos_runtime.model.config import ModelConfig
from eidos_runtime.model.gateway_types import RetryPolicy
from eidos_runtime.model.pydantic_ai_client import map_model_error
from eidos_runtime.model.request_scope import foreground_request, run_model_request
from eidos_runtime.model.retry import retry_after_seconds, select_retry_delay
from eidos_runtime.model.retry_transport import build_retrying_http_client
from eidos_runtime.runtime.engine import RuntimeEngine
from eidos_runtime.runtime.assistant_stream import AssistantStreamWriter
from eidos_runtime.runtime.events import RuntimeEvents


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "eidos_runtime.runtime.sampling.select_retry_delay", lambda *_: 0
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    session = store.create_session(str(workspace))
    try:
        yield store, session["id"], workspace
    finally:
        store.close()


class Disconnected:
    def __init__(
        self,
        fail_count=100,
        *,
        code="provider_unavailable",
        status=503,
        text="",
        reason=None,
        retry_after=None,
    ):
        self.calls = 0
        self.contexts = []
        self.fail_count, self.code, self.status, self.text = (
            fail_count,
            code,
            status,
            text,
        )
        self.reason, self.retry_after = reason, retry_after

    def complete(self, context, cancel, on_text_delta, **options):
        self.calls += 1
        self.contexts.append(context)
        if self.calls <= self.fail_count:
            if self.text:
                on_text_delta(self.text)
            raise ModelRequestError(
                ModelRequestFailure(
                    code=self.code,
                    status_code=self.status,
                    retryable=True,
                    reason=self.reason,
                    retry_after_seconds=self.retry_after,
                )
            )
        on_text_delta("Recovered\n")
        return ModelResponse(text="Recovered\n", response_state="complete")


@pytest.mark.parametrize("text", ["", "Partial reply\n"])
@pytest.mark.parametrize(
    "code,status",
    [("provider_unavailable", 503), ("rate_limited", 429), ("provider_timeout", None)],
)
def test_retry_budget_does_not_reenter_capacity_recovery(runtime, text, code, status):
    store, session, _ = runtime
    run, _ = store.create_run(session, "retry")
    model = Disconnected(text=text, code=code, status=status)
    RuntimeEngine(store, model, lambda _: None).run(run["id"], threading.Event())
    attempts = store.read_model_attempts(run["id"])
    assert model.calls == 6
    assert len(attempts) == 6
    assert store.read_run(run["id"])["status"] == "failed"
    assert store.read_run(run["id"])["modelStepCount"] == 1
    assert attempts[-1]["retryDecision"]["reason"] == "retry_budget_exhausted"
    assert len({a["contextSnapshotId"] for a in attempts}) == 1


def test_visible_stream_recovery_owns_separate_items_and_rejects_old_writer(runtime):
    store, session, _ = runtime
    run, _ = store.create_run(session, "recover")
    model = Disconnected(1, text="Old fragment\n")
    RuntimeEngine(store, model, lambda _: None).run(run["id"], threading.Event())
    rows = store.connection.execute(
        "SELECT * FROM model_attempts ORDER BY creation_seq"
    ).fetchall()
    assert [r["retry_index"] for r in rows] == [0, 1]
    assert rows[0]["request_group_id"] == rows[1]["request_group_id"]
    assert rows[0]["assistant_item_id"] != rows[1]["assistant_item_id"]
    assert model.contexts[0] == model.contexts[1]
    assert store.read_run(run["id"])["status"] == "succeeded"
    items = [store.read_item(r["assistant_item_id"]) for r in rows]
    assert [i["content"] for i in items] == ["Old fragment\n", "Recovered\n"]
    assert [i["status"] for i in items] == ["failed", "completed"]
    old = AssistantStreamWriter(
        store,
        RuntimeEvents(lambda _: None, store=store),
        run["id"],
        1,
        model_attempt_id=rows[0]["id"],
    )
    with pytest.raises(InvalidRunStateError):
        old.stream("late data\n")
    assert not store.complete_current_model_attempt(
        run["id"], "completed", expected_attempt_id=rows[0]["id"]
    )


def test_failed_attempt_and_draft_roll_back_together_on_event_failure(
    runtime, monkeypatch
):
    from eidos_runtime.db.repositories import execution

    store, session, _ = runtime
    run, _ = store.create_run(session, "atomic failure")

    class Crash(BaseException):
        pass

    class InterruptedProcess:
        def complete(self, context, cancel, on_text_delta, **options):
            on_text_delta("Draft before process exit\n")
            raise Crash()

    with pytest.raises(Crash):
        RuntimeEngine(store, InterruptedProcess(), lambda _: None).run(
            run["id"], threading.Event()
        )
    attempt = store.connection.execute(
        "SELECT id,assistant_item_id FROM model_attempts"
    ).fetchone()
    events_before = store.connection.execute("SELECT count(*) FROM events").fetchone()[
        0
    ]

    def fail_event(*args, **kwargs):
        raise sqlite3.OperationalError("fixture event transaction failed")

    monkeypatch.setattr(execution, "append_event", fail_event)
    with pytest.raises(sqlite3.OperationalError):
        store.complete_current_model_attempt(
            run["id"],
            "failed",
            expected_attempt_id=attempt["id"],
            retry_decision={"retry": True, "reason": "transport_retry"},
        )
    assert store.read_model_attempts(run["id"])[0]["status"] == "running"
    assert store.read_item(attempt["assistant_item_id"])["status"] == "in_progress"
    assert (
        store.connection.execute("SELECT count(*) FROM events").fetchone()[0]
        == events_before
    )


@pytest.mark.parametrize("tool_name", ["apply_patch", "run_shell"])
def test_prior_file_write_is_not_replayed_after_disconnect(runtime, tool_name):
    store, session, workspace = runtime
    run, _ = store.create_run(
        session, "write then explain", approval_mode="full_access"
    )

    class WriteThenDisconnect(ScriptedModel):
        calls = 0

        def complete(self, context, cancel, on_text_delta, **options):
            self.calls += 1
            if self.calls == 2:
                on_text_delta("Explanation fragment\n")
                raise ModelRequestError(
                    ModelRequestFailure(code="provider_unavailable", retryable=True)
                )
            return super().complete(context, cancel, on_text_delta, **options)

    arguments = (
        {"patch": "*** Begin Patch\n*** Add File: result.txt\n+once\n*** End Patch"}
        if tool_name == "apply_patch"
        else {"command": "printf 'once\\n' >> result.txt"}
    )
    model = WriteThenDisconnect(
        [
            ModelResponse(
                tool_calls=(
                    ModelToolCall(
                        "write-once",
                        tool_name,
                        arguments,
                    ),
                )
            ),
            ModelResponse(text="Done"),
        ]
    )
    RuntimeEngine(store, model, lambda _: None).run(run["id"], threading.Event())
    assert store.read_run(run["id"])["status"] == "succeeded"
    assert (workspace / "result.txt").read_text() == "once\n"
    assert (
        store.connection.execute(
            "SELECT count(*) FROM tool_calls WHERE tool_name=?", (tool_name,)
        ).fetchone()[0]
        == 1
    )


@pytest.mark.parametrize(
    "code",
    [
        "insufficient_quota",
        "credit_balance_exhausted",
        "project_spend_limit_exceeded",
        "insufficient_balance",
    ],
)
def test_billing_errors_override_http_retry_status(runtime, code):
    store, session, _ = runtime
    run, _ = store.create_run(session, "billing")

    class Billing(Disconnected):
        def complete(self, *args, **kwargs):
            self.calls += 1
            failure = map_model_error(
                ModelHTTPError(
                    429,
                    "fixture",
                    {"error": {"code": code, "message": "private-marker"}},
                )
            )
            assert not failure.retryable
            assert "private-marker" not in failure.model_dump_json()
            raise ModelRequestError(failure)

    model = Billing()
    RuntimeEngine(store, model, lambda _: None).run(run["id"], threading.Event())
    assert model.calls == 1
    assert store.read_run(run["id"])["termination"]["reason"] == "insufficient_quota"


def test_retry_after_longer_than_window_is_not_shortened(runtime, monkeypatch):
    from eidos_runtime.runtime import sampling

    monkeypatch.setattr(sampling, "select_retry_delay", select_retry_delay)
    store, session, _ = runtime
    run, _ = store.create_run(session, "wait")
    model = Disconnected(retry_after=600)
    RuntimeEngine(store, model, lambda _: None).run(run["id"], threading.Event())
    assert model.calls == 1
    assert (
        store.read_model_attempts(run["id"])[0]["retryDecision"]["reason"]
        == "retry_window_exhausted"
    )
    assert retry_after_seconds({"retry-after": "600"}) == 600
    assert retry_after_seconds({"retry-after": "invalid"}) is None
    future = format_datetime(datetime.now(UTC) + timedelta(seconds=60))
    assert 58 <= retry_after_seconds({"retry-after": future}) <= 60


def test_cancel_during_backoff_prevents_second_request(runtime, monkeypatch):
    scheduled = threading.Event()

    def delay(*_):
        scheduled.set()
        return 30

    monkeypatch.setattr("eidos_runtime.runtime.sampling.select_retry_delay", delay)
    store, session, _ = runtime
    run, _ = store.create_run(session, "cancel")
    cancel = threading.Event()
    model = Disconnected()
    worker = threading.Thread(
        target=RuntimeEngine(store, model, lambda _: None).run, args=(run["id"], cancel)
    )
    worker.start()
    try:
        assert scheduled.wait(5)
        other = store.create_session(str(runtime[2]))
        other_run, _ = store.create_run(other["id"], "independent")
        RuntimeEngine(
            store, ScriptedModel([ModelResponse(text="Independent")]), lambda _: None
        ).run(other_run["id"], threading.Event())
        assert store.read_run(other_run["id"])["status"] == "succeeded"
        cancel.set()
        worker.join(5)
        assert not worker.is_alive()
        assert model.calls == 1
        assert store.read_run(run["id"])["status"] == "canceled"
    finally:
        cancel.set()
        worker.join(5)


def test_foreground_disables_transport_retries_but_auxiliary_keeps_policy():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(503, json={"error": {"code": "server_is_overloaded"}})

    async def check():
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
            retry_policy=RetryPolicy(
                max_attempts=3, initial_backoff_seconds=0, max_backoff_seconds=0
            ),
        )
        try:
            with foreground_request(None):
                assert (
                    await client.http_client.post(
                        "https://fixture.invalid/chat/completions"
                    )
                ).status_code == 503
            assert calls == 1
            with pytest.raises(httpx.HTTPStatusError):
                await client.http_client.post(
                    "https://fixture.invalid/chat/completions"
                )
            assert calls == 4
        finally:
            await client.aclose()

    anyio.run(check)


def test_retry_deadline_closes_inflight_request():
    closed = False

    async def hung():
        nonlocal closed
        try:
            await anyio.sleep_forever()
        finally:
            closed = True

    async def check():
        with foreground_request(time.time_ns() // 1_000_000 + 20):
            with pytest.raises(ModelRequestError) as error:
                await run_model_request(hung)
            assert error.value.failure.reason == "retry_window_exhausted"

    anyio.run(check)
    assert closed


def test_request_timeout_is_distinct_from_recovery_deadline():
    async def timed_out():
        raise TimeoutError()

    async def check():
        with foreground_request(time.time_ns() // 1_000_000 + 60_000):
            with pytest.raises(ModelRequestError) as error:
                await run_model_request(timed_out)
            assert error.value.failure.code == "provider_timeout"
            assert error.value.failure.reason is None

    anyio.run(check)


def test_v30_upgrade_and_failed_upgrade_roll_back(tmp_path, monkeypatch):
    from eidos_runtime.db import model_retry_migration

    root = tmp_path / "data"
    root.mkdir(mode=0o700)
    path = root / "state.sqlite"
    connection = sqlite3.connect(path)
    connection.executescript(V30_SCHEMA_SQL + "\nPRAGMA user_version=30;")
    connection.close()
    path.chmod(0o600)
    original = model_retry_migration.MODEL_RETRY_SCHEMA_SQL
    monkeypatch.setattr(
        model_retry_migration, "MODEL_RETRY_SCHEMA_SQL", original + "\nINVALID SQL;"
    )
    failed = Database(root)
    failed.initialize()
    assert failed.health_state == "health_only"
    assert failed.health_code == "schema_migration_failed"
    failed.close()
    connection = sqlite3.connect(path)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 30
    assert "request_group_id" not in {
        r[1] for r in connection.execute("PRAGMA table_info(model_attempts)")
    }
    connection.close()
    monkeypatch.setattr(model_retry_migration, "MODEL_RETRY_SCHEMA_SQL", original)
    db = Database(root)
    db.initialize()
    assert (
        db.connection().execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    )
    assert db.connection().execute("PRAGMA foreign_key_check").fetchall() == []
    db.close()


@pytest.mark.parametrize(
    "exhausted,steering", [(False, False), (False, True), (True, False)]
)
def test_restart_preserves_retry_group_and_consumed_requests(
    runtime, exhausted, steering
):
    store, session, _ = runtime
    run, _ = store.create_run(session, "restart")

    class Crash(BaseException):
        pass

    class CrashDuringRequest(Disconnected):
        def complete(self, *args, **kwargs):
            self.calls += 1
            if self.calls == (6 if exhausted else 2):
                raise Crash()
            raise ModelRequestError(
                ModelRequestFailure(code="provider_unavailable", retryable=True)
            )

    model = CrashDuringRequest()
    with pytest.raises(Crash):
        RuntimeEngine(store, model, lambda _: None).run(run["id"], threading.Event())
    old = store.connection.execute(
        "SELECT * FROM model_attempts ORDER BY creation_seq DESC LIMIT 1"
    ).fetchone()
    root = store.database.data_directory
    store.close()
    store = SessionStore(root)
    store.initialize()
    try:
        _assert_recovered_retry(store, run, old, exhausted, steering)
    finally:
        store.close()


def _assert_recovered_retry(store, run, old, exhausted, steering):
    if exhausted:
        assert store.read_run(run["id"])["status"] == "interrupted"
        assert len(store.read_model_attempts(run["id"])) == 6
        return
    assert store.read_run(run["id"])["status"] == "queued"
    assert store.claim_next_run()["id"] == run["id"]
    if steering:
        store.enqueue_input(run["id"], "steering-new")
    model = Disconnected(0 if steering else 100)
    RuntimeEngine(store, model, lambda _: None).run(run["id"], threading.Event())
    if steering:
        assert model.calls == 2
        assert "steering-new" not in str(model.contexts[0])
        assert "steering-new" in str(model.contexts[1])
        assert store.read_run(run["id"])["status"] == "succeeded"
        return
    assert model.calls == 4
    rows = store.connection.execute(
        "SELECT * FROM model_attempts ORDER BY creation_seq"
    ).fetchall()
    assert [r["retry_index"] for r in rows] == list(range(6))
    assert {r["request_group_id"] for r in rows} == {old["request_group_id"]}
    assert {r["retry_deadline_at"] for r in rows} == {old["retry_deadline_at"]}
    assert len({r["context_snapshot_id"] for r in rows}) == 1
    assert store.read_run(run["id"])["status"] == "failed"


@pytest.mark.parametrize(
    "code,retryable",
    [
        ("server_error", True),
        ("rate_limit_exceeded", True),
        ("insufficient_quota", False),
        ("invalid_prompt", False),
    ],
)
def test_native_failed_response_classifies_provider_errors(code, retryable):
    from eidos_runtime.model.native_custom import map_responses_response

    with pytest.raises(ModelRequestError) as raised:
        map_responses_response(
            {"status": "failed", "error": {"code": code, "message": "private"}}
        )
    assert raised.value.failure.retryable is retryable
    assert "private" not in raised.value.failure.model_dump_json()


@pytest.mark.parametrize("protocol", ["chat_completions", "openai_responses"])
@pytest.mark.parametrize(
    "failure", ["disconnect", "http503", "quota", "stalled", "server_wait"]
)
def test_real_adapters_recover_without_sdk_retry_or_partial_tool_execution(
    runtime, tmp_path, monkeypatch, protocol, failure
):
    from eidos_runtime.model import pydantic_factory
    from eidos_runtime.model.config import CustomModelSettings, ModelConfigStore
    from eidos_runtime.model.gateway import ModelGateway
    from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel

    store, session, _ = runtime
    requests = []
    closed = False

    def sse(events):
        return "".join("data: " + json.dumps(e) + "\n\n" for e in events).encode()

    class BrokenStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            if protocol == "chat_completions":
                yield sse(
                    [
                        {
                            "id": "partial",
                            "object": "chat.completion.chunk",
                            "created": 0,
                            "model": "fixture",
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {
                                        "role": "assistant",
                                        "content": "Fragment\n",
                                        "tool_calls": [
                                            {
                                                "index": 0,
                                                "id": "partial-call",
                                                "type": "function",
                                                "function": {
                                                    "name": "apply_patch",
                                                    "arguments": "{",
                                                },
                                            }
                                        ],
                                    },
                                    "finish_reason": None,
                                }
                            ],
                        }
                    ]
                )
            else:
                yield sse(
                    [
                        {
                            "type": "response.output_text.delta",
                            "sequence_number": 0,
                            "delta": "Fragment\n",
                        },
                        {
                            "type": "response.function_call_arguments.delta",
                            "sequence_number": 1,
                            "item_id": "partial-call",
                            "delta": "{",
                        },
                    ]
                )
            raise httpx.RemoteProtocolError("fixture connection dropped")

        async def aclose(self):
            nonlocal closed
            closed = True

    def handler(request):
        nonlocal closed
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            if failure == "quota":
                return httpx.Response(
                    429,
                    json={
                        "error": {"code": "insufficient_quota", "message": "private"}
                    },
                )
            if failure == "http503":
                return httpx.Response(
                    503, json={"error": {"code": "server_is_overloaded"}}
                )
            if failure == "server_wait":
                return httpx.Response(
                    503,
                    headers={"Retry-After": "600"},
                    json={"error": {"code": "server_is_overloaded"}},
                )
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=BrokenStream(),
            )
        if failure in {"disconnect", "stalled"}:
            assert closed
        if failure == "stalled":
            closed = False

            class StalledStream(BrokenStream):
                async def __aiter__(self):
                    await anyio.sleep_forever()
                    yield b""

            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                stream=StalledStream(),
            )
        if protocol == "chat_completions":
            body = (
                sse(
                    [
                        {
                            "id": "complete",
                            "object": "chat.completion.chunk",
                            "created": 0,
                            "model": "fixture",
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {
                                        "role": "assistant",
                                        "content": "Recovered\n",
                                    },
                                    "finish_reason": None,
                                }
                            ],
                        },
                        {
                            "id": "complete",
                            "object": "chat.completion.chunk",
                            "created": 0,
                            "model": "fixture",
                            "choices": [
                                {"index": 0, "delta": {}, "finish_reason": "stop"}
                            ],
                        },
                    ]
                )
                + b"data: [DONE]\n\n"
            )
        else:
            body = sse(
                [
                    {
                        "type": "response.output_text.delta",
                        "sequence_number": 0,
                        "delta": "Recovered\n",
                    },
                    {
                        "type": "response.completed",
                        "sequence_number": 1,
                        "response": {
                            "id": "complete",
                            "object": "response",
                            "created_at": 0,
                            "model": "fixture",
                            "status": "completed",
                            "output": [
                                {
                                    "id": "message",
                                    "type": "message",
                                    "role": "assistant",
                                    "status": "completed",
                                    "content": [
                                        {
                                            "type": "output_text",
                                            "text": "Recovered\n",
                                            "annotations": [],
                                        }
                                    ],
                                }
                            ],
                        },
                    },
                ]
            )
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=body
        )

    monkeypatch.setattr(
        pydantic_factory,
        "build_retrying_http_client",
        lambda config, **kwargs: build_retrying_http_client(
            config, wrapped=httpx.MockTransport(handler), **kwargs
        ),
    )
    configs = ModelConfigStore(tmp_path / "models")
    if failure == "server_wait":
        monkeypatch.setattr(
            "eidos_runtime.runtime.sampling.select_retry_delay", select_retry_delay
        )
    configs.initialize()
    config = configs.create(
        provider_id="custom",
        model_id="fixture",
        api_key="",
        custom=CustomModelSettings(
            provider_name="Fixture",
            base_url="https://fixture.invalid/v1",
            name="Fixture",
            wire_api=protocol,
        ),
    )
    kernel = RuntimeAsyncKernel()
    kernel.start()
    lease = ModelGateway(configs, async_kernel=kernel).acquire_lease(config)
    try:
        client = lease.client
        profile = client.profile_snapshot
        if failure == "stalled":
            profile = profile.model_copy(update={"retry_recovery_window_seconds": 0.1})
        run, _ = store.create_run(
            session, "wire retry", model_id=config.id, model_profile=profile
        )
        RuntimeEngine(store, client, lambda _: None, async_kernel=kernel).run(
            run["id"], threading.Event()
        )
        assert len(requests) == (1 if failure in {"quota", "server_wait"} else 2)
        assert store.read_run(run["id"])["status"] == (
            "failed" if failure in {"quota", "stalled", "server_wait"} else "succeeded"
        )
        if failure == "stalled":
            assert closed
        assert (
            store.connection.execute("SELECT count(*) FROM tool_calls").fetchone()[0]
            == 0
        )
        if len(requests) == 2:
            assert requests[0] == requests[1]
    finally:
        lease.close()
        kernel.close()

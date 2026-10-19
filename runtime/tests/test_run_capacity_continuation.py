from __future__ import annotations

import hashlib
import json
from pathlib import Path
import threading
import time

import pytest

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.db.errors import ResourceNotFoundError
from eidos_runtime.domain.long_task import LongTaskStatus, ResumeOutcome, ResumeVerification
from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel
from eidos_runtime.runtime.engine import RuntimeEngine
from eidos_runtime.runtime.long_task import ResumeVerifier


@pytest.fixture
def run_store(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    session = store.create_session(str(workspace))
    run, _ = store.create_run(session["id"], "Continue the task", approval_mode="full_access")
    try:
        yield store, session, run, workspace
    finally:
        store.close()


def wait_until(predicate, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not reached")


def background_run(store, run, model):
    cancel = threading.Event()
    errors = []

    def drive():
        try:
            RuntimeEngine(store, model, lambda _: None).run(run["id"], cancel)
        except BaseException as error:
            errors.append(error)

    worker = threading.Thread(target=drive)
    worker.start()
    return cancel, worker, errors


def unknown_tool_result(store, run):
    item = store.create_tool_item(run["id"], 0, 0, "uncertain-call", "run_shell", "{}")
    store.begin_durable_intent(item["id"], preconditions={}, approval_required=False)
    store.complete_tool_item(item["id"], json.dumps({
        "toolName": "run_shell", "outcome": "error", "code": "shell_timeout",
        "summary": "The command outcome is unknown", "data": {},
        "sideEffectsMayExist": True, "reconciliationRequired": True,
    }), item_status="failed", tool_status="failed")
    return item


def test_long_answer_is_saved_and_paged_without_overflowing_control_messages(run_store):
    store, session, run, _ = run_store
    answer = "A sufficiently long safe explanation.\n" * 18_000
    assert len(answer.encode()) > 512 * 1024
    notifications = []
    RuntimeEngine(store, ScriptedModel([ModelResponse(text=answer)]), notifications.append).run(
        run["id"], threading.Event(),
    )
    assert store.read_run(run["id"])["status"] == "succeeded"
    row = store.connection.execute(
        "SELECT id,content FROM items WHERE run_id=? AND kind='assistant_message' ORDER BY creation_seq DESC LIMIT 1",
        (run["id"],),
    ).fetchone()
    assert row["content"] == answer
    assert all(len(json.dumps(event).encode()) <= 1024 * 1024 for event in notifications)
    projected = store.read_session_snapshot(session["id"])["items"][-1]
    assert projected["contentBytes"] == len(answer.encode())
    digest = hashlib.sha256(answer.encode()).hexdigest()
    pieces = []
    offset = 0
    while offset < len(answer):
        page = store.read_tool_text(session["id"], row["id"], "content", digest, offset)
        pieces.append(page.content)
        offset = page.next_offset
    assert "".join(pieces) == answer
    foreign = store.create_session(str(run_store[3]))
    with pytest.raises(ResourceNotFoundError, match="not found"):
        store.read_tool_text(foreign["id"], row["id"], "content", digest, 0)


def test_repeated_output_capacity_pauses_then_resumes_same_run(run_store, monkeypatch):
    store, _, run, _ = run_store
    allowance = [1024]
    monkeypatch.setattr("eidos_runtime.runtime.model_runner.model_output_byte_budget", lambda _: allowance[0])
    model = ScriptedModel([ModelResponse(text="x" * 2048), ModelResponse(text="x" * 2048), ModelResponse(text="Done")])
    cancel, worker, errors = background_run(store, run, model)
    try:
        repository = store.long_task_repository()
        wait_until(lambda: repository.read(run["id"]) is not None and repository.read(run["id"]).status is LongTaskStatus.PAUSED)
        current = store.read_run(run["id"])
        assert current["status"] == "running"
        assert not current.get("errorCode")
        assert model._index == 2
        allowance[0] = 4096
        repository.request_resume(run["id"])
        repository.record_verification(run["id"], ResumeVerification(
            run_id=run["id"], outcome=ResumeOutcome.SAFE_RESUME, reasons=(), checked_at=0,
        ))
        repository.mark_resumed(run["id"])
        worker.join(5)
        assert not worker.is_alive()
        assert not errors
        assert model._index == 3
        assert store.read_run(run["id"])["status"] == "succeeded"
    finally:
        cancel.set()
        worker.join(5)


def test_projection_capacity_pauses_and_cancel_still_works(run_store, monkeypatch):
    store, _, run, _ = run_store
    monkeypatch.setattr("eidos_runtime.db.repositories.context.CONTEXT_PROJECTION_HARD_MAX_BYTES", 6000)
    item = store.create_assistant_item(run["id"], 1)
    store.append_item_content(item["id"], "x" * 7000)
    store.complete_assistant_item(item["id"])
    model = ScriptedModel([ModelResponse(text="Must not be called")])
    cancel, worker, errors = background_run(store, run, model)
    try:
        repository = store.long_task_repository()
        wait_until(lambda: repository.read(run["id"]) is not None and repository.read(run["id"]).status is LongTaskStatus.PAUSED)
        assert store.read_run(run["id"])["status"] == "running"
        assert not model.contexts
        cancel.set()
        worker.join(5)
        assert not worker.is_alive()
        assert not errors
        assert store.read_run(run["id"])["status"] == "canceled"
    finally:
        cancel.set()
        worker.join(5)


def test_unknown_effect_does_not_block_following_write_or_completion(run_store):
    store, _, run, workspace = run_store
    uncertain = unknown_tool_result(store, run)
    model = ScriptedModel([
        ModelResponse(tool_calls=(ModelToolCall("next-call", "apply_patch", {
            "patch": "*** Begin Patch\n*** Add File: next.txt\n+next\n*** End Patch\n",
        }),)),
        ModelResponse(text="The earlier outcome remains unknown. The next file was created."),
    ])
    RuntimeEngine(store, model, lambda _: None).run(run["id"], threading.Event())
    assert store.read_run(run["id"])["status"] == "succeeded"
    assert (workspace / "next.txt").read_text() == "next\n"
    intent = store.connection.execute(
        "SELECT status FROM durable_intents WHERE tool_call_id=?", (uncertain["toolCall"]["id"],),
    ).fetchone()
    assert intent[0] == "uncertain"
    assert not store.side_effects_blocked(run["id"])
    assert store.read_run(run["id"])["reconciliationRequired"]


def test_unknown_effect_does_not_change_cancel_to_interrupted(run_store):
    store, _, run, _ = run_store
    unknown_tool_result(store, run)
    store.request_cancel_committed(run["id"])
    store.complete_requested_cancel_committed(run["id"])
    assert store.read_run(run["id"])["status"] == "canceled"


def test_resume_preserves_workspace_checks_without_uncertainty_gate():
    same = ("/workspace", 1, 2, 3)
    arguments = dict(
        run_id="run", expected_workspace=same, current_workspace=same,
        expected_git_head=None, current_git_head=None,
        expected_rule_snapshot_id=None, current_rule_snapshot_id=None,
        expected_index_snapshot_id=None, current_index_snapshot_id=None,
        side_effects_may_exist=True, unfinished_side_effecting_tool=True,
        durable_intent_unfinished=True,
    )
    assert ResumeVerifier.verify(**arguments).outcome is ResumeOutcome.SAFE_RESUME
    arguments["current_workspace"] = ("/workspace", 1, 99, 3)
    assert ResumeVerifier.verify(**arguments).outcome is ResumeOutcome.WORKSPACE_CHANGED


def test_tool_batch_larger_than_sixteen_executes_in_declared_order(run_store):
    store, _, run, _ = run_store
    calls = tuple(ModelToolCall(f"read-{i}", "list_files", {}) for i in range(20))
    model = ScriptedModel([ModelResponse(tool_calls=calls), ModelResponse(text="Done")])
    RuntimeEngine(store, model, lambda _: None).run(run["id"], threading.Event())
    assert store.read_run(run["id"])["status"] == "succeeded"
    rows = store.connection.execute(
        "SELECT provider_call_id FROM tool_calls JOIN items ON items.id=tool_calls.item_id WHERE items.run_id=? ORDER BY items.ordinal",
        (run["id"],),
    ).fetchall()
    assert [row[0] for row in rows] == [f"read-{i}" for i in range(20)]


def test_repeated_argument_capacity_pauses_before_any_tool_execution(run_store):
    store, _, run, workspace = run_store
    oversized = ModelResponse(tool_calls=(ModelToolCall("oversized", "read_file", {
        "path": "x" * (70 * 1024),
    }),))
    model = ScriptedModel([oversized, oversized])
    cancel, worker, errors = background_run(store, run, model)
    try:
        repository = store.long_task_repository()
        wait_until(lambda: repository.read(run["id"]) is not None and repository.read(run["id"]).status is LongTaskStatus.PAUSED)
        assert store.read_run(run["id"])["status"] == "running"
        assert not (workspace / "must-not-exist.txt").exists()
        assert store.connection.execute("SELECT count(*) FROM tool_calls JOIN items ON items.id=tool_calls.item_id WHERE items.run_id=?", (run["id"],)).fetchone()[0] == 0
    finally:
        cancel.set()
        worker.join(5)
        assert not worker.is_alive()
        assert not errors


def test_reasoning_replay_does_not_reject_small_content_by_part_count():
    from eidos_runtime.model.replay import ModelReasoningPart, ModelResponseReplay
    replay = ModelResponseReplay(reasoning_parts=tuple(ModelReasoningPart(content="evidence") for _ in range(300)))
    assert len(replay.reasoning_parts) == 300


def test_oversized_rpc_reply_returns_a_bounded_error_without_broken_stdout():
    import io
    from eidos_runtime.protocol.server import write_message
    output = io.StringIO()
    write_message(output, {"jsonrpc": "2.0", "id": "client-1", "result": {"body": "x" * (1024 * 1024)}})
    response = json.loads(output.getvalue())
    assert response["id"] == "client-1"
    assert response["error"]["data"]["code"] == "RESPONSE_TOO_LARGE"
    assert len(output.getvalue().encode()) < 1024


def test_capacity_continuation_uses_the_shared_closed_protocol_fixture():
    from eidos_runtime.protocol.methods import ToolTextReadRequestDto, RunResumeRequestDto, RunResumeResponseDto
    from eidos_runtime.protocol.schemas import ItemDto
    fixture = json.loads((Path(__file__).parents[2] / "protocol/fixtures/capacity-continuation-v1.json").read_text())
    item = ItemDto.model_validate(fixture["item"])
    page = ToolTextReadRequestDto.model_validate(fixture["pageRequest"])
    assert page.field == "content" and page.tool_call_id == item.id
    assert item.content_hash == page.sha256
    RunResumeRequestDto.model_validate(fixture["resumeRequest"])
    assert RunResumeResponseDto.model_validate(fixture["resumeResponse"]).run.status == "running"


@pytest.mark.parametrize("code,retryable,attempts,notice", [
    ("rate_limited", False, 1, "模型服务暂时限制请求频率"),
    ("provider_timeout", False, 1, "模型请求超过等待时间"),
    ("provider_unavailable", True, 3, "模型请求的自动重试次数已用完"),
])
def test_provider_limits_pause_without_terminating_the_run(run_store, code, retryable, attempts, notice):
    from eidos_runtime.model.client import ModelRequestError, ModelRequestFailure

    class LimitedModel(ScriptedModel):
        def complete(self, *args, **kwargs):
            raise ModelRequestError(ModelRequestFailure(
                code=code, retryable=retryable, transport_attempt_count=attempts,
            ))

    store, session, run, _ = run_store
    cancel, worker, errors = background_run(store, run, LimitedModel([]))
    try:
        repository = store.long_task_repository()
        wait_until(lambda: repository.read(run["id"]) is not None and repository.read(run["id"]).status is LongTaskStatus.PAUSED)
        assert store.read_run(run["id"])["status"] == "running"
        wait_until(lambda: any(notice in (item.get("content") or "") for item in store.read_session_snapshot(session["id"])["items"]))
    finally:
        cancel.set()
        worker.join(5)
        assert not worker.is_alive()
        assert not errors
    assert store.read_run(run["id"])["status"] == "canceled"


@pytest.mark.parametrize("kind", ["custom", "function", "identifier", "output"])
def test_native_responses_capacity_is_distinct_from_protocol_errors(monkeypatch, kind):
    from types import SimpleNamespace
    from eidos_runtime.model.client import ModelRequestError
    from eidos_runtime.model import native_custom
    monkeypatch.setattr(native_custom, "MAX_CUSTOM_TOOL_INPUT_BYTES", 32)
    item = {"type": "custom_tool_call", "name": "apply_patch", "call_id": "call", "input": "x" * 33}
    response = {"status": "completed", "output": [item]}
    if kind == "function":
        item.update(type="function_call", name="read_file", arguments=json.dumps({"path": "x" * (70 * 1024)}))
    elif kind == "identifier":
        item.update(call_id="x" * 257, input="small")
    elif kind == "output":
        response.update(status="incomplete", incomplete_details=SimpleNamespace(reason="max_output_tokens"))
    with pytest.raises(ModelRequestError) as raised:
        native_custom.map_responses_response(response)
    assert raised.value.failure.code == "capacity_exceeded"


def test_native_stream_capacity_closes_the_response_without_exposing_tools(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from eidos_runtime.model.client import ModelRequestError
    from eidos_runtime.model import native_custom
    monkeypatch.setattr(native_custom, "MAX_CUSTOM_TOOL_INPUT_BYTES", 32)

    class Stream:
        closed = False
        def __aiter__(self):
            return self
        async def __anext__(self):
            return SimpleNamespace(type="response.custom_tool_call_input.done", item_id="item", input="x" * 33)
        async def close(self):
            self.closed = True

    stream = Stream()
    client = native_custom.OpenAIResponsesModelClient.__new__(native_custom.OpenAIResponsesModelClient)
    with pytest.raises(ModelRequestError) as raised:
        asyncio.run(client._consume_stream(stream, threading.Event(), lambda _: None))
    assert raised.value.failure.code == "capacity_exceeded"
    assert stream.closed

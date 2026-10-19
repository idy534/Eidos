from __future__ import annotations

import json
import tempfile
import threading
import sqlite3
from pathlib import Path

import pytest
from pydantic_ai.messages import (
    ModelResponse as PAIResponse,
    TextPart,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
)

from eidos_runtime.context.builder import ContextBuilder
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel
from eidos_runtime.model.pydantic_ai_client import encode_context, map_model_response
from eidos_runtime.model.replay import ModelReasoningPart, ModelResponseReplay
from eidos_runtime.runtime.engine import RuntimeEngine


def test_adapter_preserves_reasoning_without_emitting_it_as_text():
    original = PAIResponse(
        parts=[
            ThinkingPart(
                "private thought", id="reasoning_content", provider_name="deepseek"
            ),
            TextPart("progress"),
            ToolCallPart("read_file", {"path": "a"}, "call-1"),
        ],
        provider_name="deepseek",
        model_name="deepseek-flash",
        finish_reason="tool_call",
    )
    response = map_model_response(original)
    assert response.text == "progress"
    assert response.reasoning_parts[0].content == "private thought"
    assert response.reasoning_parts[0].id == "reasoning_content"
    assert response.reasoning_parts[0].provider_name == "deepseek"


def test_replay_groups_text_reasoning_and_all_calls_before_returns():
    replay = ModelResponseReplay(
        provider_name="deepseek",
        tool_call_ids=("call-1", "call-2"),
        reasoning_parts=(
            ModelReasoningPart(
                content="thought", id="reasoning_content", provider_name="deepseek"
            ),
        ),
    )
    context = (
        {
            "type": "assistant",
            "content": "progress",
            "responseId": "attempt-1",
            "responseReplay": replay.model_dump(mode="json"),
        },
        {
            "type": "tool_call",
            "name": "read_file",
            "callId": "call-1",
            "arguments": "{}",
            "responseId": "attempt-1",
        },
        {
            "type": "tool_result",
            "name": "read_file",
            "callId": "call-1",
            "result": "one",
        },
        {
            "type": "tool_call",
            "name": "read_file",
            "callId": "call-2",
            "arguments": "{}",
            "responseId": "attempt-1",
        },
        {
            "type": "tool_result",
            "name": "read_file",
            "callId": "call-2",
            "result": "two",
        },
    )
    messages = encode_context(context)
    assert len(messages) == 2
    assert isinstance(messages[0], PAIResponse)
    assert [p.part_kind for p in messages[0].parts] == [
        "thinking",
        "text",
        "tool-call",
        "tool-call",
    ]
    assert [c.tool_call_id for c in messages[0].tool_calls] == ["call-1", "call-2"]
    assert len(messages[1].parts) == 2
    assert all(isinstance(part, ToolReturnPart) for part in messages[1].parts)


def _native_tool_batch(
    calls: tuple[tuple[str, str], ...],
    results: tuple[tuple[str, str, str], ...],
) -> tuple[dict[str, object], ...]:
    context: list[dict[str, object]] = [{
        "type": "assistant",
        "content": "",
        "responseId": "response-1",
    }]
    context.extend({
        "type": "tool_call",
        "callId": call_id,
        "name": name,
        "arguments": "{}",
        "responseId": "response-1",
    } for call_id, name in calls)
    context.extend({
        "type": "tool_result",
        "callId": call_id,
        "name": name,
        "result": result,
    } for call_id, name, result in results)
    return tuple(context)


@pytest.mark.parametrize(
    ("calls", "results", "reason"),
    [
        (
            (("one", "read_file"), ("two", "run_shell")),
            (("one", "read_file", "one result"),),
            "incomplete_tool_results",
        ),
        (
            (("one", "read_file"),),
            (("one", "read_file", "first"), ("one", "read_file", "repeat")),
            "unexpected_tool_result",
        ),
        (
            (("one", "read_file"),),
            (("one", "run_shell", "wrong tool"),),
            "tool_result_name_mismatch",
        ),
        (
            (("one", "read_file"), ("one", "run_shell")),
            (("one", "read_file", "result"),),
            "duplicate_tool_call_id",
        ),
    ],
)
def test_chat_completion_rejects_incomplete_or_mismatched_tool_batches(
    calls: tuple[tuple[str, str], ...],
    results: tuple[tuple[str, str, str], ...],
    reason: str,
) -> None:
    with pytest.raises(ValueError, match=reason):
        encode_context(_native_tool_batch(calls, results))


def test_legacy_missing_reasoning_becomes_labelled_history_when_required():
    context = (
        {"type": "assistant", "content": "old progress", "responseId": "old-step"},
        {
            "type": "tool_call",
            "name": "read_file",
            "callId": "old-call",
            "arguments": "{}",
            "responseId": "old-step",
        },
        {
            "type": "tool_result",
            "name": "read_file",
            "callId": "old-call",
            "result": "old result",
        },
    )
    messages = encode_context(context, reasoning_required=True)
    assert not any(isinstance(m, PAIResponse) for m in messages)
    text = str(messages)
    assert "reasoning unavailable" in text
    assert "old progress" in text and "old result" in text


def test_reasoning_survives_restart_and_stays_out_of_session_items():
    with tempfile.TemporaryDirectory(prefix="eidos-reasoning-") as directory:
        root = Path(directory)
        workspace = root / "workspace"
        workspace.mkdir()
        store = SessionStore(root / "data")
        store.initialize()
        try:
            session = store.create_session(str(workspace))
            run, _ = store.create_run(session["id"], "hello")
            response = ModelResponse(
                text="answer",
                reasoning_parts=(
                    ModelReasoningPart(
                        content="private thought",
                        id="reasoning_content",
                        provider_name="deepseek",
                    ),
                ),
            )
            RuntimeEngine(store, ScriptedModel([response]), lambda _: None).run(
                run["id"], threading.Event()
            )
            assert "private thought" not in json.dumps(
                store.read_session_snapshot(session["id"]), default=str
            )
            store.close()
            store = SessionStore(root / "data")
            store.initialize()
            next_run, _ = store.create_run(session["id"], "continue")
            messages = encode_context(
                ContextBuilder(store).build(next_run["id"]).model_context
            )
            thinking = [
                p
                for m in messages
                if isinstance(m, PAIResponse)
                for p in m.parts
                if isinstance(p, ThinkingPart)
            ]
            assert [p.content for p in thinking] == ["private thought"]
        finally:
            store.close()


def test_reasoning_record_omits_oversized_provider_parts_whole():
    replay = ModelResponseReplay.model_validate({
        "reasoning_parts": (
            ModelReasoningPart(content="small retained part", id="reasoning-1"),
            ModelReasoningPart(content="x" * (4 * 1024 * 1024), id="oversized"),
        ),
    })

    assert replay.reasoning_parts == (
        ModelReasoningPart(content="small retained part", id="reasoning-1"),
    )
    assert len(replay.model_dump_json().encode("utf-8")) <= 4 * 1024 * 1024


def test_signatures_and_provider_metadata_survive_chat_replay():
    part = ModelReasoningPart(
        content="thought",
        signature="opaque signature",
        id="reasoning_content",
        provider_name="deepseek",
        provider_details={"signature": "opaque", "nested": {"index": 1}},
    )
    replay = ModelResponseReplay(reasoning_parts=(part,))
    messages = encode_context(
        (
            {
                "type": "assistant",
                "content": "answer",
                "responseId": "attempt",
                "responseReplay": replay.model_dump(mode="json"),
            },
        )
    )
    thinking = messages[0].parts[0]
    assert thinking.signature == part.signature
    assert thinking.provider_details == part.provider_details


def test_partial_response_uses_history_without_orphaned_calls_or_reasoning():
    replay = ModelResponseReplay(
        tool_call_ids=("one", "two"),
        reasoning_parts=(ModelReasoningPart(content="private"),),
    )
    messages = encode_context(
        (
            {
                "type": "tool_call",
                "name": "read_file",
                "callId": "one",
                "arguments": "{}",
                "responseId": "attempt",
                "responseReplay": replay.model_dump(mode="json"),
            },
            {
                "type": "tool_result",
                "name": "read_file",
                "callId": "one",
                "result": "retained result",
            },
        )
    )
    assert not any(isinstance(m, PAIResponse) for m in messages)
    assert "retained result" in str(messages)
    assert "private" not in str(messages)


def test_tool_round_reasoning_is_persisted_and_never_enters_feed(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    try:
        session = store.create_session(str(workspace))
        run, _ = store.create_run(session["id"], "list")
        private = "hidden model thought"
        model = ScriptedModel(
            [
                ModelResponse(
                    text="progress",
                    reasoning_parts=(
                        ModelReasoningPart(
                            content=private,
                            id="reasoning_content",
                            provider_name="deepseek",
                        ),
                    ),
                    tool_calls=(
                        ModelToolCall("one", "list_files", {}),
                        ModelToolCall("two", "list_files", {}),
                    ),
                ),
                ModelResponse(text="answer"),
            ]
        )
        RuntimeEngine(store, model, lambda _: None).run(run["id"], threading.Event())
        assert store.read_run(run["id"])["status"] == "succeeded"
        assert private not in json.dumps(
            store.read_session_snapshot(session["id"]), default=str
        )
        messages = encode_context(model.contexts[1])
        response = next(m for m in messages if isinstance(m, PAIResponse))
        assert [c.tool_call_id for c in response.tool_calls] == ["one", "two"]
        assert response.parts[0].content == private
        assert (
            store.connection.execute(
                "SELECT count(*) FROM model_attempts WHERE response_replay_json IS NOT NULL"
            ).fetchone()[0]
            == 2
        )
    finally:
        store.close()


def test_cancelled_attempt_rejects_late_reasoning_commit(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    try:
        session = store.create_session(str(workspace))
        run, _ = store.create_run(session["id"], "cancel")
        store.increment_model_step(run["id"])
        store.cancel_run(run["id"])
        assert (
            store.complete_current_model_attempt(
                run["id"],
                "completed",
                response_replay=ModelResponseReplay(
                    reasoning_parts=(ModelReasoningPart(content="late"),)
                ),
            )
            is False
        )
        row = store.connection.execute(
            "SELECT status,response_replay_json FROM model_attempts"
        ).fetchone()
        assert row[0] == "canceled" and row[1] is None
    finally:
        store.close()


def test_incomplete_response_keeps_reasoning_for_diagnostics_without_replaying_it(
    tmp_path,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    try:
        session = store.create_session(str(workspace))
        run, _ = store.create_run(session["id"], "incomplete")
        RuntimeEngine(
            store,
            ScriptedModel(
                [
                    ModelResponse(
                        text="partial",
                        finish_reason="length",
                        reasoning_parts=(
                            ModelReasoningPart(content="incomplete thought"),
                        ),
                    )
                ]
            ),
            lambda _: None,
        ).run(run["id"], threading.Event())
        row = store.connection.execute(
            "SELECT status,response_replay_json FROM model_attempts"
        ).fetchone()
        assert row[0] == "failed" and row[1] is not None
        raw = store.database.json_blobs.read_json(
            row[1], expected_kind="model-response-replay"
        )
        assert "incomplete thought" in raw
        assert not any(
            item.response_replay
            for item in store.context_projection_facts(run["id"]).items
        )
    finally:
        store.close()


def test_multi_tool_reasoning_is_not_counted_once_per_tool_in_projection(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    try:
        session = store.create_session(str(workspace))
        run, _ = store.create_run(session["id"], "many tools")
        store.increment_model_step(run["id"])
        calls = tuple(f"call-{i}" for i in range(8))
        for index, call in enumerate(calls):
            item = store.create_tool_item(run["id"], 1, index, call, "list_files", "{}")
            store.complete_tool_item(item["id"], '{"outcome":"success","code":"ok"}')
        store.complete_current_model_attempt(
            run["id"],
            "completed",
            response_replay=ModelResponseReplay(
                tool_call_ids=calls,
                reasoning_parts=(ModelReasoningPart(content="x" * (1024 * 1024)),),
            ),
        )
        facts = store.context_projection_facts(run["id"])
        assert (
            len([item for item in facts.items if item.response_replay is not None]) == 1
        )
        assert (
            len([item for item in facts.items if item.provider_call_id is not None])
            == 8
        )
    finally:
        store.close()


@pytest.mark.parametrize(
    "provider,model_name",
    [
        ("deepseek", "deepseek-flash"),
        ("kimi", "kimi-k3"),
        ("minimax", "MiniMax-M3"),
        ("volcengine", "glm-5.3-flash"),
    ],
)
def test_provider_wire_replays_reasoning_and_grouped_tool_calls(provider, model_name):
    import httpx2 as httpx
    from openai import AsyncOpenAI
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.deepseek import DeepSeekProvider
    from pydantic_ai.providers.moonshotai import MoonshotAIProvider
    from pydantic_ai.providers.openai import OpenAIProvider
    from eidos_runtime.model.client import FunctionToolDefinition
    from eidos_runtime.model.config import ModelProfileSpec
    from eidos_runtime.model.pydantic_ai_client import PydanticAIModelClient
    from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel

    captured = []

    def handler(request):
        captured.append(json.loads(request.content))
        if len(captured) == 1:
            delta = {
                "role": "assistant",
                "content": "progress",
                "reasoning_content": "private thought",
                "tool_calls": [
                    {
                        "index": index,
                        "id": f"call-{index + 1}",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": "{}"},
                    }
                    for index in range(2)
                ],
            }
            finish_reason = "tool_calls"
        else:
            delta = {
                "role": "assistant",
                "content": "answer",
                "reasoning_content": "next thought",
            }
            finish_reason = "stop"
        chunks = [
            {
                "id": "response",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": model_name,
                "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
            },
            {
                "id": "response",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": model_name,
                "choices": [{"index": 0, "delta": {}, "finish_reason": finish_reason}],
            },
        ]
        body = (
            "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks)
            + "data: [DONE]\n\n"
        )
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=body.encode()
        )

    kernel = RuntimeAsyncKernel()
    kernel.start()
    sdk = AsyncOpenAI(
        api_key="offline-fixture",
        base_url="https://fixture.invalid",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    provider_class = {"deepseek": DeepSeekProvider, "kimi": MoonshotAIProvider}.get(
        provider, OpenAIProvider
    )
    model = OpenAIChatModel(model_name, provider=provider_class(openai_client=sdk))
    client = PydanticAIModelClient(
        model,
        ModelProfileSpec(
            provider_id=provider,
            model_id=model_name,
            context_window_tokens=4096,
            max_output_tokens=512,
            request_timeout_seconds=5,
        ),
        openai_client=sdk,
        reasoning_selection="max",
        async_kernel=kernel,
    )
    try:
        tools = (
            FunctionToolDefinition(
                name="read_file",
                description="fixture",
                parameters_json_schema={"type": "object"},
            ),
        )
        deltas = []
        first = client.complete(
            ({"type": "user", "content": "hello"},),
            threading.Event(),
            deltas.append,
            instructions="fixture",
            tool_definitions=tools,
        )
        assert "private thought" not in "".join(deltas)
        replay = ModelResponseReplay(
            provider_name=first.provider_name,
            reasoning_parts=first.reasoning_parts,
            tool_call_ids=tuple(c.provider_call_id for c in first.tool_calls),
            response_text_present=True,
        )
        context = [
            {"type": "user", "content": "hello"},
            {
                "type": "assistant",
                "content": first.text,
                "responseId": "attempt",
                "responseReplay": replay.model_dump(mode="json"),
            },
        ]
        for call in first.tool_calls:
            context.extend(
                [
                    {
                        "type": "tool_call",
                        "callId": call.provider_call_id,
                        "name": call.name,
                        "arguments": "{}",
                        "responseId": "attempt",
                    },
                    {
                        "type": "tool_result",
                        "callId": call.provider_call_id,
                        "name": call.name,
                        "result": "ok",
                    },
                ]
            )
        client.complete(
            tuple(context),
            threading.Event(),
            lambda _: None,
            instructions="fixture",
            tool_definitions=tools,
        )
        assistants = [m for m in captured[1]["messages"] if m["role"] == "assistant"]
        assert len(assistants) == 1
        assert assistants[0]["reasoning_content"] == "private thought"
        assert assistants[0]["content"] == "progress"
        assert [call["id"] for call in assistants[0]["tool_calls"]] == [
            "call-1",
            "call-2",
        ]
    finally:
        client.close()
        kernel.close()


def test_responses_preserves_encrypted_reasoning_item():
    from eidos_runtime.model.native_custom import (
        map_responses_response,
        encode_responses_context,
    )

    item = {
        "type": "reasoning",
        "id": "reason-1",
        "summary": [{"type": "summary_text", "text": "summary"}],
        "encrypted_content": "opaque",
    }
    response = map_responses_response(
        {"status": "completed", "output": [item], "model": "fixture"},
        provider_name="openai",
    )
    assert response.reasoning_parts[0].native_item == item
    replay = ModelResponseReplay(
        wire_api="openai_responses",
        provider_name="openai",
        reasoning_parts=response.reasoning_parts,
    )
    encoded = encode_responses_context(
        (
            {
                "type": "assistant",
                "content": "answer",
                "responseId": "attempt",
                "responseReplay": replay.model_dump(mode="json"),
            },
        ),
        provider_name="openai",
    )
    assert encoded[0] == item


@pytest.mark.parametrize("supports_reasoning", [True, False])
@pytest.mark.parametrize("required_tool", [None, "submit_summary"])
def test_stateless_responses_requests_encrypted_reasoning_when_supported(
    supports_reasoning,
    required_tool,
):
    from types import SimpleNamespace
    from eidos_runtime.model.client import ModelProfileSnapshot, FunctionToolDefinition
    from eidos_runtime.model.config import ModelProfileSpec
    from eidos_runtime.model.native_custom import OpenAIResponsesModelClient
    from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel

    captured = []

    def create(**kwargs):
        captured.append(kwargs)
        return {
            "status": "completed",
            "model": "fixture",
            "output": [
                {
                    "type": "reasoning",
                    "id": "reason-1",
                    "summary": [],
                    "encrypted_content": "opaque",
                }
            ],
        }

    kernel = RuntimeAsyncKernel()
    kernel.start()
    spec = ModelProfileSpec(
        provider_id="openai",
        model_id="fixture",
        wire_api="openai_responses",
        context_window_tokens=4096,
        max_output_tokens=512,
        request_timeout_seconds=5,
    )
    snapshot = ModelProfileSnapshot(
        provider_id="openai",
        model_id="fixture",
        wire_api="openai_responses",
        context_window_tokens=4096,
        max_output_tokens=512,
        request_timeout_seconds=5,
        supports_tools=True,
        supports_json_schema_output=False,
        supports_reasoning=supports_reasoning,
    )
    client = OpenAIResponsesModelClient(
        spec,
        openai_client=SimpleNamespace(responses=SimpleNamespace(create=create)),
        retry_transport=None,
        profile_snapshot=snapshot,
        async_kernel=kernel,
    )
    try:
        response = client.complete(
            ({"type": "user", "content": "hello"},),
            threading.Event(),
            lambda _: None,
            instructions="fixture",
            allow_tools=required_tool is not None,
            tool_definitions=(FunctionToolDefinition(name=required_tool, description="Output", parameters_json_schema={"type": "object"}),) if required_tool else (),
            required_tool=required_tool,
            max_output_tokens=128,
        )
        assert captured[0]["store"] is False
        assert captured[0]["max_output_tokens"] == 128
        assert captured[0].get("tool_choice") == ({"type": "function", "name": required_tool} if required_tool else None)
        assert (
            captured[0].get("include") == ["reasoning.encrypted_content"]
        ) == supports_reasoning
        assert response.reasoning_parts[0].native_item["encrypted_content"] == "opaque"
    finally:
        client.close()
        kernel.close()


def test_v21_upgrade_adds_replay_without_fabricating_history(tmp_path):
    from eidos_runtime.db.database import Database
    from eidos_runtime.db.schema import V21_SCHEMA_SQL, SCHEMA_VERSION

    directory = tmp_path / "data"
    directory.mkdir(mode=0o700)
    path = directory / "state.sqlite"
    with sqlite3.connect(path) as connection:
        connection.executescript(V21_SCHEMA_SQL + "PRAGMA user_version=21;")
    path.chmod(0o600)
    database = Database(directory)
    try:
        database.initialize()
        assert (
            database.connection().execute("PRAGMA user_version").fetchone()[0]
            == SCHEMA_VERSION
        )
        assert "response_replay_json" in {
            r[1]
            for r in database.connection().execute("PRAGMA table_info(model_attempts)")
        }
    finally:
        database.close()


def test_v21_upgrade_failure_rolls_back_new_column(tmp_path, monkeypatch):
    import eidos_runtime.db.database as database_module
    import eidos_runtime.db.model_replay_migration as migration_module
    from eidos_runtime.db.schema import V21_SCHEMA_SQL

    directory = tmp_path / "data"
    directory.mkdir(mode=0o700)
    path = directory / "state.sqlite"
    with sqlite3.connect(path) as connection:
        connection.executescript(V21_SCHEMA_SQL + "PRAGMA user_version=21;")
    path.chmod(0o600)
    monkeypatch.setattr(
        migration_module,
        "V21_TO_V22_MIGRATION_SQL",
        migration_module.V21_TO_V22_MIGRATION_SQL + "INVALID SQL;\n",
    )
    database = database_module.Database(directory)
    try:
        database.initialize()
        assert database.health_state == "health_only"
        assert database.health_code == "schema_migration_failed"
    finally:
        database.close()
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 21
        assert "response_replay_json" not in {
            r[1] for r in connection.execute("PRAGMA table_info(model_attempts)")
        }

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import threading
from uuid import uuid4

from pydantic_ai.messages import ModelRequest, ToolReturnPart
import pytest

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.context.facts import ContextItemFact
from eidos_runtime.context.summary_evidence import source_text
from eidos_runtime.memory.contracts import MemoryEditRequest, MemoryReadRequest
from eidos_runtime.memory.tool_projection import project_memory_tool_result
from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel
from eidos_runtime.model.context_projection import model_result_json, project_context
from eidos_runtime.model.pydantic_ai_client import encode_context
from eidos_runtime.persistence.session_history import SessionHistoryRepository
from eidos_runtime.protocol.session_fork import SessionForkRequest
from eidos_runtime.runtime.engine import RuntimeEngine
from eidos_runtime.tools.memory import memory_entries


FIXTURE = json.loads(
    (
        Path(__file__).parents[2] / "protocol/fixtures/memory-tool-model-v1.json"
    ).read_text()
)


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda value: value["name"])
def test_memory_model_contract_keeps_canonical_and_ui_metadata(case):
    entry = next(value for value in memory_entries() if value.spec.name == case["name"])
    canonical = entry.validate_result(case["canonical"])
    original = deepcopy(canonical)
    projection = entry.projector.project(entry, canonical)
    assert entry.projector.policy_id == "memory"
    assert projection.model_result == case["model"]
    assert projection.canonical_result == original == canonical
    assert projection.ui_result["data"] == canonical["data"]
    assert len(json.dumps(projection.model_result)) < len(json.dumps(canonical))
    assert (
        project_memory_tool_result(case["name"], projection.model_result)
        == projection.model_result
    )


@pytest.mark.parametrize("case", FIXTURE["cases"], ids=lambda value: value["name"])
def test_legacy_context_and_summary_projection_use_the_same_compact_contract(case):
    raw = json.dumps(case["canonical"])
    assert json.loads(model_result_json(raw)) == case["model"]
    context = project_context(
        ({"type": "tool_result", "name": case["name"], "result": raw},)
    )
    assert json.loads(context[0]["result"]) == case["model"]
    assert (
        json.loads(model_result_json(context[0]["result"], tool_name=case["name"]))
        == case["model"]
    )
    source = source_text(
        ContextItemFact(
            item_id="tool-item",
            run_id="run",
            kind="tool_call",
            status="completed",
            provider_call_id="call",
            tool_name=case["name"],
            result_json=raw,
        )
    )
    assert all(
        field not in source
        for field in ("currentRevision", "scopeId", "epochs", "operationId")
    )


def test_hook_feedback_remains_visible_for_memory_tools():
    feedback = {
        "hookFeedback": "用户规则要求修改摘要后再保存。",
        "notice": "The tool already ran.",
    }
    context = project_context(
        (
            {
                "type": "tool_result",
                "name": "memory_edit",
                "result": json.dumps(feedback),
            },
        )
    )
    assert json.loads(context[0]["result"]) == feedback


def test_error_guidance_and_real_uncertainty_are_retained_without_empty_data():
    canonical = {
        **FIXTURE["cases"][0]["canonical"],
        "outcome": "error",
        "code": "memory_revision_conflict",
        "summary": "The note changed. Read its current version before preparing another edit.",
        "sideEffectsMayExist": True,
        "reconciliationRequired": True,
    }
    projected = project_memory_tool_result("memory_edit", canonical)
    assert projected == {
        key: canonical[key]
        for key in (
            "outcome",
            "code",
            "summary",
            "sideEffectsMayExist",
            "reconciliationRequired",
        )
    }


def test_deleted_note_does_not_advertise_a_revision_for_a_future_new_note():
    canonical = deepcopy(FIXTURE["cases"][0]["canonical"])
    canonical["code"] = canonical["data"]["action"]["code"] = "memory_deleted"
    assert project_memory_tool_result("memory_edit", canonical) == {
        "outcome": "success",
        "code": "memory_deleted",
        "data": {"path": "feedback_testing.md"},
    }


def test_empty_search_has_no_null_cursor_or_false_limit_marker():
    canonical = deepcopy(FIXTURE["cases"][2]["canonical"])
    canonical["data"].update(documents=[], next_cursor=None, search_limited=False)
    assert project_memory_tool_result("memory_search", canonical) == {
        "outcome": "success",
        "data": {"documents": []},
    }


def test_index_read_has_no_note_metadata_and_note_read_keeps_editable_metadata():
    canonical = deepcopy(FIXTURE["cases"][1]["canonical"])
    page = canonical["data"]["read"]
    page["document"].update(kind="project", userOwned=False)
    note = project_memory_tool_result("memory_read", canonical)["data"]
    assert note["title"] == "测试规则" and note["summary"] == "全量测试需要明确要求"
    assert note["kind"] == "project" and "userOwned" not in note
    page.update(path="MEMORY.md", document=None)
    index = project_memory_tool_result("memory_read", canonical)["data"]
    assert set(index) == {"scope", "path", "revision", "content"}


def test_json_escaping_and_unicode_pagination_never_skip_note_characters():
    text = '中文🙂"\\\n' * 80
    actual, offset = "", 137
    while len(actual) < len(text):
        canonical = deepcopy(FIXTURE["cases"][1]["canonical"])
        page = canonical["data"]["read"]
        content = text[len(actual) : len(actual) + 200]
        page.update(
            offset=offset,
            content=content,
            nextOffset=offset + len(content)
            if len(actual) + len(content) < len(text)
            else None,
        )
        projected = project_memory_tool_result("memory_read", canonical, max_bytes=512)
        assert (
            len(
                json.dumps(
                    projected, ensure_ascii=False, separators=(",", ":")
                ).encode()
            )
            <= 512
        )
        visible = projected["data"]
        actual += visible["content"]
        if "nextOffset" not in visible:
            break
        assert visible["nextOffset"] == offset + len(visible["content"])
        offset = visible["nextOffset"]
    assert actual == text


def test_metadata_like_words_inside_the_note_remain_verbatim():
    canonical = deepcopy(FIXTURE["cases"][1]["canonical"])
    text = (
        '用户正文中的 {"scopeId":"literal","epochs":3,"currentRevision":2} 必须保留。'
    )
    canonical["data"]["read"]["content"] = text
    assert (
        project_memory_tool_result("memory_read", canonical)["data"]["content"] == text
    )


@pytest.fixture
def memory_store(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    session = store.create_session(str(workspace))
    yield store, session
    store.close()


def tool_payload(context, name):
    return json.loads(
        next(
            item["result"]
            for item in reversed(context)
            if item.get("type") == "tool_result" and item.get("name") == name
        )
    )


@pytest.mark.integration
def test_model_and_provider_receive_compact_results_with_private_durable_guards(
    memory_store,
):
    store, session = memory_store
    service = store.database.memory
    for scope in ("global", "current"):
        service.edit(
            MemoryEditRequest(
                session_id=session["id"],
                operation_id=scope,
                scope=scope,
                path="testing.md",
                summary=f"{scope} 测试约定",
                content=f"{scope} 原始正文",
            )
        )
    run, _ = store.create_run(
        session["id"], "请修改当前项目的测试约定", approval_mode="full_access"
    )
    model = ScriptedModel(
        [
            ModelResponse(
                tool_calls=(
                    ModelToolCall("find", "memory_search", {"query": "测试约定"}),
                )
            ),
            ModelResponse(
                tool_calls=(
                    ModelToolCall("read", "memory_read", {"path": "testing.md"}),
                )
            ),
            ModelResponse(
                tool_calls=(
                    ModelToolCall(
                        "edit",
                        "memory_edit",
                        {
                            "path": "testing.md",
                            "summary": "新测试约定",
                            "content": "新的约定",
                        },
                    ),
                )
            ),
            ModelResponse(text="完成"),
        ]
    )
    RuntimeEngine(store, model, lambda _: None).run(run["id"], threading.Event())
    search = tool_payload(model.contexts[1], "memory_search")
    assert {note["scope"] for note in search["data"]["documents"]} == {
        "global",
        "current",
    }
    assert all(
        set(note) == {"scope", "path", "revision", "summary"}
        for note in search["data"]["documents"]
    )
    read = tool_payload(model.contexts[2], "memory_read")
    assert read == {
        "outcome": "success",
        "data": {
            "scope": "current",
            "path": "testing.md",
            "revision": 1,
            "title": "testing",
            "summary": "current 测试约定",
            "kind": "feedback",
            "content": "current 原始正文",
        },
    }
    assert tool_payload(model.contexts[3], "memory_edit") == {
        "outcome": "success",
        "code": "memory_saved",
        "data": {"path": "testing.md", "revision": 2},
    }
    returned = [
        part
        for message in encode_context(model.contexts[2])
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    assert (
        next(
            json.loads(part.content)
            for part in returned
            if part.tool_name == "memory_read"
        )
        == read
    )
    guarded = next(
        item
        for item in model.contexts[2]
        if item.get("name") == "memory_read" and item.get("type") == "tool_result"
    )
    assert guarded["memoryEpochs"] and "epochs" not in read["data"]
    row = store.connection.execute(
        "SELECT t.* FROM tool_calls t JOIN items i ON i.id=t.item_id WHERE i.run_id=? AND t.tool_name='memory_edit'",
        (run["id"],),
    ).fetchone()
    assert json.loads(row["result_json"])["data"]["action"]["operationId"]
    assert json.loads(row["ui_result_json"])["data"]["action"]["documentId"]
    assert json.loads(row["model_result_json"]) == tool_payload(
        model.contexts[3], "memory_edit"
    )
    assert store.read_run(run["id"])["status"] == "succeeded"


@pytest.mark.integration
def test_long_model_read_pages_cover_the_entire_note_without_generic_readback(
    memory_store,
):
    store, session = memory_store
    text = '🙂🙂🙂🙂完整中文正文"\\\n' * 2400
    service = store.database.memory
    service.edit(
        MemoryEditRequest(
            operation_id="long",
            scope="global",
            path="long.md",
            summary="长笔记",
            content=text,
        )
    )
    responses, offset, offsets = [], 0, []
    while True:
        offsets.append(offset)
        read = service.read(
            MemoryReadRequest(
                scope="global", path="long.md", offset=offset, limit=16384
            )
        )
        projected = project_memory_tool_result(
            "memory_read", {"outcome": "success", "data": {"read": read.to_wire_dict()}}
        )
        responses.append(
            ModelResponse(
                tool_calls=(
                    ModelToolCall(
                        f"page-{len(responses)}",
                        "memory_read",
                        {
                            "scope": "global",
                            "path": "long.md",
                            "offset": offset,
                            "limit": 16384,
                            "revision": 1,
                        },
                    ),
                )
            )
        )
        if projected["data"].get("nextOffset") is None:
            break
        offset = projected["data"]["nextOffset"]
    model = ScriptedModel([*responses, ModelResponse(text="读取完成")])
    run, _ = store.create_run(session["id"], "分段读取长笔记")
    RuntimeEngine(store, model, lambda _: None).run(run["id"], threading.Event())
    pages = [
        tool_payload(model.contexts[index + 1], "memory_read")
        for index in range(len(responses))
    ]
    assert "".join(page["data"]["content"] for page in pages) == text
    assert all("readBack" not in page for page in pages)
    assert any(later - earlier < 16384 for earlier, later in zip(offsets, offsets[1:]))
    rows = store.connection.execute(
        "SELECT t.model_result_json FROM tool_calls t JOIN items i ON i.id=t.item_id WHERE i.run_id=?",
        (run["id"],),
    ).fetchall()
    assert all("readBack" not in json.loads(row[0]) for row in rows)
    assert store.read_run(run["id"])["status"] == "succeeded"


@pytest.mark.integration
def test_payload_epochs_cannot_replace_the_persisted_retrieval_guard(
    memory_store, monkeypatch
):
    store, session = memory_store
    service = store.database.memory
    service.edit(
        MemoryEditRequest(
            operation_id="private",
            scope="global",
            path="private.md",
            summary="私有规则",
            content="没有读取记录就不能进入新请求。",
        )
    )
    monkeypatch.setattr(service, "track_tool", lambda *_args, **_kwargs: None)
    run, _ = store.create_run(session["id"], "读取规则")
    model = ScriptedModel(
        [
            ModelResponse(
                tool_calls=(
                    ModelToolCall(
                        "read", "memory_read", {"scope": "global", "path": "private.md"}
                    ),
                )
            ),
            ModelResponse(text="继续任务"),
        ]
    )
    RuntimeEngine(store, model, lambda _: None).run(run["id"], threading.Event())
    payload = tool_payload(model.contexts[1], "memory_read")
    assert payload["code"] == "memory_snapshot_revoked"
    assert "没有读取记录就不能进入新请求。" not in str(model.contexts[1])


@pytest.mark.integration
def test_compact_fork_results_keep_their_private_guard_after_restart(memory_store):
    store, session = memory_store
    body = "分叉会话沿用原始读取记录。"
    store.database.memory.edit(
        MemoryEditRequest(
            session_id=session["id"],
            operation_id="fork-note",
            path="fork.md",
            summary="分叉约定",
            content=body,
        )
    )
    run, _ = store.create_run(session["id"], "读取分叉约定")
    model = ScriptedModel(
        [
            ModelResponse(
                tool_calls=(ModelToolCall("read", "memory_read", {"path": "fork.md"}),)
            ),
            ModelResponse(text="读取完成"),
        ]
    )
    RuntimeEngine(store, model, lambda _: None).run(run["id"], threading.Event())
    answer = store.read_session_snapshot(session["id"])["items"][-1]["id"]
    fork = (
        SessionHistoryRepository(store.database)
        .fork(
            SessionForkRequest(
                operation_id=str(uuid4()),
                source_session_id=session["id"],
                source_item_id=answer,
                target={"kind": "same_workspace"},
            ),
            session_id=str(uuid4()),
        )
        .value
    )
    data_directory = store.data_directory
    store.close()
    reopened = SessionStore(data_directory)
    reopened.initialize()
    try:
        continuation, _ = reopened.create_run(fork, "继续任务")
        continued = ScriptedModel([ModelResponse(text="继续完成")])
        RuntimeEngine(reopened, continued, lambda _: None).run(
            continuation["id"], threading.Event()
        )
        payload = tool_payload(continued.contexts[0], "memory_read")
        assert payload["data"]["content"] == body
        assert set(payload["data"]) == {
            "scope",
            "path",
            "revision",
            "title",
            "summary",
            "kind",
            "content",
        }
        guarded = next(
            item
            for item in continued.contexts[0]
            if item.get("type") == "tool_result" and item.get("name") == "memory_read"
        )
        assert guarded["memoryEpochs"]
        reopened.database.memory.edit(
            MemoryEditRequest(
                session_id=session["id"],
                operation_id="forget-fork",
                path="fork.md",
                action="delete",
                expected_revision=1,
            )
        )
        after, _ = reopened.create_run(fork, "再次继续")
        revoked = ScriptedModel([ModelResponse(text="继续其他工作")])
        RuntimeEngine(reopened, revoked, lambda _: None).run(
            after["id"], threading.Event()
        )
        assert (
            tool_payload(revoked.contexts[0], "memory_read")["code"]
            == "memory_snapshot_revoked"
        )
        assert body not in str(revoked.contexts[0])
    finally:
        reopened.close()

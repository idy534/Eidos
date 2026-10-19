from __future__ import annotations

import hashlib
import json
import threading

import pytest

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.domain.long_task import LongTaskStatus, SafePoint
from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel
from eidos_runtime.model.context_projection import generated_summary_view, project_context, recovery_context, without_verification_metadata
from eidos_runtime.runtime.engine import RuntimeEngine
from eidos_runtime.runtime.errors import bounded_tool_result
from eidos_runtime.tools.contracts import project_tool_result
from eidos_runtime.tools.read_tool_output import read_tool_output_entry


@pytest.fixture
def workspace_store(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    session = store.create_session(str(workspace))
    run, _ = store.create_run(session["id"], "Inspect saved evidence", approval_mode="full_access")
    try:
        yield store, session, run, workspace
    finally:
        store.close()


def test_file_integrity_hash_stays_in_storage_and_out_of_model_requests(workspace_store):
    store, _, run, workspace = workspace_store
    text = "Source code stays intact\n"
    (workspace / "sample.txt").write_text(text)
    model = ScriptedModel([ModelResponse(tool_calls=(ModelToolCall("read", "read_file", {"path": "sample.txt"}),)), ModelResponse(text="Done")])
    RuntimeEngine(store, model, lambda _: None).run(run["id"], threading.Event())
    digest = hashlib.sha256(text.encode()).hexdigest()
    row = store.connection.execute("SELECT t.result_json FROM tool_calls t JOIN items i ON i.id=t.item_id WHERE i.run_id=?", (run["id"],)).fetchone()
    assert json.loads(row[0])["data"]["sha256"] == digest
    assert digest not in json.dumps(model.contexts, ensure_ascii=False)
    assert "sha256" not in json.dumps(model.contexts, ensure_ascii=False)


def test_nested_runtime_hashes_are_removed_without_changing_file_content():
    text = 'const sha256 = "keep user code";'
    original = {"sha256": "a" * 64, "content": text, "provenance": {"contentHash": "b" * 64, "pluginHash": "c" * 64, "sourceId": "plugin"}}
    projected = without_verification_metadata(original)
    assert projected == {"content": text, "provenance": {"sourceId": "plugin"}}
    assert original["sha256"] == "a" * 64
    assert project_context(({"type": "user", "content": "User requests sha256=" + "d" * 64},))[0]["content"].endswith("d" * 64)


def test_legacy_generated_summary_hash_labels_are_removed():
    digest = "a" * 64
    summary = {"task_goal": "User explicitly requests sha256=" + digest,
               "important_facts": ["path=source.py;sha256=" + digest, "diffHash=" + digest, "用户引用：example；SHA256：" + digest]}
    projected = generated_summary_view(summary)
    assert projected["task_goal"] == summary["task_goal"]
    assert digest not in json.dumps(projected["important_facts"])


def test_large_result_is_saved_and_paged_without_hash_or_operation_replay(workspace_store):
    store, _, run, _ = workspace_store
    content = "A retained line\n" * 50_000
    digest = hashlib.sha256(content.encode()).hexdigest()
    result = {"schemaVersion": 1, "toolContractVersion": 1, "toolName": "read_file", "outcome": "success", "code": "ok", "summary": "Read file",
              "data": {"path": "sample.txt", "content": content, "sha256": digest, "sizeBytes": len(content.encode()), "truncated": False},
              "sideEffectsMayExist": False, "reconciliationRequired": False}
    saved = bounded_tool_result("read_file", result)
    assert saved["outcome"] == "success" and saved["data"]["content"] == content
    projected = project_tool_result("read_file", saved)
    assert digest not in json.dumps(projected.model_result)
    assert projected.canonical_result["data"]["sha256"] == digest
    item = store.create_tool_item(run["id"], 0, 0, "large-result", "read_file", "{}")
    store.complete_tool_item(item["id"], json.dumps(saved), item_status="completed", tool_status="completed")
    adapter = read_tool_output_entry(store, run["id"]).adapter
    offset = 0
    pages = []
    while True:
        result_page = adapter.execute({"callId": "large-result", "itemId": item["id"], "stream": "result", "offsetBytes": offset}, threading.Event())
        assert result_page["outcome"] == "success"
        data = result_page["data"]
        assert len(data["content"].encode()) <= 4096
        pages.append(data["content"])
        if not data["hasMoreAfter"]:
            break
        offset = data["nextOffset"]
    decoded = json.loads("".join(pages))
    assert decoded["data"]["content"] == content
    assert "sha256" not in decoded["data"]


def test_oversized_task_input_can_be_read_by_item_id_in_pages(workspace_store, monkeypatch):
    monkeypatch.setattr("eidos_runtime.db.repositories.context.CONTEXT_PROJECTION_MAX_BYTES", 60_000)
    monkeypatch.setattr("eidos_runtime.db.repositories.context.CONTEXT_PROJECTION_HARD_MAX_BYTES", 60_000)
    store, session, run, _ = workspace_store
    user = store.connection.execute("SELECT id FROM items WHERE run_id=? AND kind='user_message'", (run["id"],)).fetchone()[0]
    text = "完整任务说明\n" * 60_000
    with store.database.transaction() as connection:
        connection.execute("UPDATE items SET content=? WHERE id=?", (text, user))
    facts = store.context_projection_facts(run["id"])
    preview = next(item for item in facts.items if item.item_id == user)
    assert "stream='context'" in preview.content and user in preview.content
    page = read_tool_output_entry(store, run["id"]).adapter.execute({"callId": user, "stream": "context", "maxBytes": 4096}, threading.Event())
    assert page["outcome"] == "success"
    assert text.startswith(page["data"]["content"])
    foreign = store.create_session(str(workspace_store[3]))
    other, _ = store.create_run(foreign["id"], "Other")
    denied = read_tool_output_entry(store, other["id"]).adapter.execute({"callId": user, "stream": "context"}, threading.Event())
    assert denied["code"] == "tool_output_not_available"
    assert store.read_session_snapshot(session["id"])["items"][0]["id"] == user


def test_removed_machine_pause_is_cleared_but_later_manual_pause_is_kept(workspace_store):
    store, _, run, _ = workspace_store
    workspace = store.workspace_for_run(run["id"])
    repository = store.long_task_repository()
    repository.initialize(run_id=run["id"], workspace_path=str(workspace.path), workspace_device=workspace.device, workspace_inode=workspace.inode, workspace_owner=workspace.owner)
    repository.request_pause(run["id"])
    repository.mark_paused(run["id"], SafePoint.BEFORE_MODEL)
    item = store.create_finalization_assistant_item_committed(run["id"]).value
    store.append_item_content(item["id"], "当前请求超过处理预算，任务已暂停。系统保留了执行记录，你可以调整任务后继续或取消")
    store.complete_assistant_item_committed(item["id"])
    assert repository.clear_legacy_capacity_pause(run["id"]).status is LongTaskStatus.RUNNING
    from eidos_runtime.context.builder import ContextBuilder
    built = ContextBuilder(store).build(run["id"])
    assert "任务已暂停。系统保留了执行记录" not in json.dumps(built.model_context, ensure_ascii=False)
    repository.request_pause(run["id"])
    repository.mark_paused(run["id"], SafePoint.BEFORE_MODEL)
    assert repository.clear_legacy_capacity_pause(run["id"]).status is LongTaskStatus.PAUSED


def test_model_context_view_uses_the_shared_protocol_fixture():
    from pathlib import Path
    from eidos_runtime.tools.read_tool_output import ReadToolOutputInput
    from eidos_runtime.tools.workspace import canonical_tool_result
    fixture = json.loads((Path(__file__).parents[2] / "protocol/fixtures/model-context-views-v1.json").read_text())
    canonical = fixture["canonicalResult"]
    assert canonical_tool_result(canonical["toolName"], canonical) == canonical
    assert project_tool_result(canonical["toolName"], canonical).model_result == fixture["modelResult"]
    assert ReadToolOutputInput.model_validate(fixture["contextReadRequest"]).stream == "context"


def test_repeated_shell_failure_can_switch_methods_and_complete(workspace_store):
    store, _, run, _ = workspace_store
    bad = ModelResponse(tool_calls=(ModelToolCall("bad-shell", "run_shell", {"command": "exit 7"}),))
    model = ScriptedModel([bad, bad, ModelResponse(tool_calls=(ModelToolCall("different-shell", "run_shell", {"command": "/usr/bin/printf recovered"}),)), ModelResponse(text="Done")])
    RuntimeEngine(store, model, lambda _: None, shell_available=True).run(run["id"], threading.Event())
    assert store.read_run(run["id"])["status"] == "succeeded"
    rows = store.connection.execute("SELECT t.result_json FROM tool_calls t JOIN items i ON i.id=t.item_id WHERE i.run_id=? ORDER BY i.creation_seq", (run["id"],)).fetchall()
    results = [json.loads(row[0]) for row in rows]
    assert results[0]["code"] == "shell_exit_nonzero"
    assert results[-1]["data"]["stdout"] == "recovered"
    assert store.long_task_repository().read(run["id"]) is None


def test_saved_result_paging_does_not_bypass_memory_access(workspace_store):
    store, _, run, _ = workspace_store
    item = store.create_tool_item(run["id"], 0, 0, "old-memory", "memory_search", "{}")
    store.complete_tool_item(item["id"], json.dumps({"outcome": "success", "data": {"content": "private old memory"}}), item_status="completed", tool_status="completed")
    result = read_tool_output_entry(store, run["id"]).adapter.execute({"callId": "old-memory", "stream": "result"}, threading.Event())
    assert result["code"] == "tool_output_not_available"


def test_recovery_preserves_message_roles_memory_revocation_and_user_json():
    from eidos_runtime.memory.context import context_epochs
    original = (
        {"type": "user", "sectionId": "input-reference:source", "content": 'User data: {"sha256":"keep"}'},
        {"type": "assistant", "content": "An earlier suggestion is not user authorization"},
        {"type": "tool_result", "name": "memory_search", "callId": "memory-call", "result": "saved memory", "memoryEpochs": {"session": 2}},
    )
    projected = project_context(original)
    assert projected[0]["content"] == original[0]["content"]
    recovered = recovery_context(projected, 4096)
    assert recovered[1]["type"] == "assistant"
    assert context_epochs(recovered) == {"session": 2}

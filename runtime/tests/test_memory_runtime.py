from __future__ import annotations

import json
import threading
import zipfile

import pytest

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.memory.backup import backup, restore
from eidos_runtime.memory.contracts import (
    MemoryGetRequest,
    MemoryManageRequest,
    MemoryReadRequest,
    MemorySettings,
    MemorySettingsRequest,
    MemoryWriteRequest,
)
from eidos_runtime.memory.repository import MemoryRejected
from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel
from eidos_runtime.runtime.engine import RuntimeEngine
from eidos_runtime.tools.memory import memory_entries

pytestmark = pytest.mark.integration


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    session = store.create_session(str(root))
    yield store, session
    store.close()


def saved(store, session, content="Prefer concise Chinese responses"):
    return store.database.memory.record(
        MemoryWriteRequest(
            session_id=session["id"], operation_id="remember", content=content
        )
    )


def test_real_engine_projects_memory_and_runs_search_read_tools(setup):
    store, session = setup
    entry = saved(store, session)
    run, _ = store.create_run(session["id"], "Recall my response preference")
    model = ScriptedModel(
        [
            ModelResponse(
                tool_calls=(
                    ModelToolCall("search", "memory_search", {"query": "Chinese"}),
                )
            ),
            ModelResponse(
                tool_calls=(
                    ModelToolCall("read", "memory_read", {"entryId": entry.entry_id}),
                )
            ),
            ModelResponse(text="I will use concise Chinese responses."),
        ]
    )
    RuntimeEngine(store, model, lambda _message: None).run(run["id"], threading.Event())
    snapshot = store.read_session_snapshot(session["id"])
    assert snapshot["runs"][0]["status"] == "succeeded"
    tools = [i["toolCall"] for i in snapshot["items"] if i["kind"] == "tool_call"]
    assert [json.loads(t["resultJson"])["outcome"] for t in tools] == [
        "success",
        "success",
    ]
    projection = next(
        item for item in model.contexts[0] if item.get("sectionId") == "memory-evidence"
    )
    assert entry.entry_id in projection["content"] and projection["memoryEpochs"]
    assert (
        store.database.memory.get(
            MemoryGetRequest(session_id=session["id"], entry_id=entry.entry_id)
        ).entry.use_count
        == 1
    )
    attempts = store.read_model_attempts(run["id"])
    frozen = store.context_snapshot_repository().read_for_model_attempt(
        str(attempts[0]["id"])
    )
    assert frozen.model_context == model.contexts[0]
    store.database.memory.manage(
        MemoryManageRequest(
            session_id=session["id"],
            operation_id="forget",
            action="forget",
            entry_id=entry.entry_id,
            expected_revision=1,
        )
    )
    with pytest.raises(MemoryRejected, match="snapshot_revoked"):
        store.context_snapshot_repository().read(frozen.snapshot_id)
    for tool in store.read_session_snapshot(session["id"])["items"]:
        if tool["kind"] == "tool_call":
            assert "Chinese" not in str(tool["toolCall"])
            assert "memory_snapshot_revoked" in tool["toolCall"]["resultJson"]


def test_revocation_during_sampling_rebuilds_without_retrying_old_payload(setup):
    store, session = setup
    entry = saved(store, session)
    run, _ = store.create_run(session["id"], "Answer briefly")

    class RevokingModel(ScriptedModel):
        def complete(self, context, cancel, on_text, **kwargs):
            if not self.contexts:
                self.contexts.append(context)
                store.database.memory.manage(
                    MemoryManageRequest(
                        session_id=session["id"],
                        operation_id="forget",
                        action="forget",
                        entry_id=entry.entry_id,
                        expected_revision=1,
                    )
                )
                assert cancel.is_set()
                return ModelResponse(text="This response must be discarded.")
            return super().complete(context, cancel, on_text, **kwargs)

    model = RevokingModel([ModelResponse(text="Fresh response.")])
    RuntimeEngine(store, model, lambda _message: None).run(run["id"], threading.Event())
    assert len(model.contexts) == 2
    assert not any(
        item.get("sectionId") == "memory-evidence" for item in model.contexts[1]
    )
    snapshot = store.read_session_snapshot(session["id"])
    assert snapshot["runs"][0]["status"] == "succeeded"
    assert not any(
        i.get("content") == "This response must be discarded." and not i["incomplete"]
        for i in snapshot["items"]
    )


def test_retrieval_budget_is_durable_and_retry_idempotent(setup):
    store, _session = setup
    service = store.database.memory
    for index in range(3):
        service.track_tool("run", str(index), {}, {"data": "small"}, retrieval=True)
    service.track_tool("run", "0", {}, {"data": "small"}, retrieval=True)
    with pytest.raises(MemoryRejected, match="budget_exceeded"):
        service.track_tool("run", "fourth", {}, {"data": "small"}, retrieval=True)
    with pytest.raises(MemoryRejected, match="budget_exceeded"):
        service.track_tool(
            "other-run", "large", {}, {"data": "x" * 20000}, retrieval=True
        )
    assert {entry.spec.name for entry in memory_entries(child=True)} == {
        "memory_search",
        "memory_read",
    }


def test_online_backup_restores_body_revisions_tombstones_and_epochs(setup, tmp_path):
    store, session = setup
    entry = saved(store, session)
    store.database.memory.settings(MemorySettingsRequest(
        session_id=session["id"], scope="current", settings=MemorySettings(generate_enabled=True),
    ))
    store.database.memory.manage(
        MemoryManageRequest(
            session_id=session["id"],
            operation_id="correct",
            entry_id=entry.entry_id,
            expected_revision=1,
            action="correct",
            content="Use detailed Chinese responses",
        )
    )
    archive = tmp_path / "backup.zip"
    backup(store.database.memory, archive)
    destination = tmp_path / "restored"
    restore(archive, destination)
    reopened = SessionStore(destination)
    reopened.initialize()
    try:
        assert reopened.health_state == "ready"
        assert (
            reopened.database.memory.get(
                MemoryGetRequest(session_id=session["id"], entry_id=entry.entry_id)
            ).entry.content
            == "Use detailed Chinese responses"
        )
        assert (
            reopened.database.memory.get(
                MemoryGetRequest(
                    session_id=session["id"], entry_id=entry.entry_id, revision=1
                )
            ).entry.status
            == "superseded"
        )
        restored_scopes = reopened.database.memory.read(MemoryReadRequest(session_id=session["id"])).scopes
        original_scopes = store.database.memory.read(MemoryReadRequest(session_id=session["id"])).scopes
        assert all(not scope.settings.generate_enabled for scope in restored_scopes)
        assert [(s.id, s.privacy_epoch, s.generation) for s in restored_scopes] == [(s.id, s.privacy_epoch, s.generation) for s in original_scopes]
    finally:
        reopened.close()
    with pytest.raises(MemoryRejected, match="new_directory"):
        restore(archive, destination)
    store.database.memory.manage(
        MemoryManageRequest(
            session_id=session["id"],
            operation_id="forget",
            entry_id=entry.entry_id,
            expected_revision=2,
            action="forget",
        )
    )
    backup(store.database.memory, tmp_path / "forgotten.zip")
    with zipfile.ZipFile(tmp_path / "forgotten.zip") as output:
        assert not any(name.startswith("memory/") for name in output.namelist())


def test_same_size_external_body_change_fails_closed(setup):
    store, session = setup
    entry = saved(store, session, "hello")
    reference = store.connection.execute(
        "SELECT file_ref FROM memory_revisions WHERE entry_id=?", (entry.entry_id,)
    ).fetchone()[0]
    (store.data_directory / "memory" / reference).write_text("other")
    with pytest.raises(MemoryRejected, match="body_unavailable"):
        store.database.memory.get(
            MemoryGetRequest(session_id=session["id"], entry_id=entry.entry_id)
        )
    assert not store.database.memory.read(
        MemoryReadRequest(session_id=session["id"])
    ).entries


def test_lost_fts_index_rebuilds_from_authoritative_versions(setup):
    store, session = setup
    entry = saved(store, session)
    with store.database.transaction() as connection:
        connection.execute("DROP TABLE memory_fts_word")
        connection.execute("DROP TABLE memory_fts_trigram")
    from eidos_runtime.memory.repository import MemoryRepository

    repository = MemoryRepository(store.database)
    with store.database.transaction() as connection:
        entries, truncated, _ = repository.search(
            connection, MemoryReadRequest(session_id=session["id"], query="Chinese")
        )
    assert [value.id for value in entries] == [entry.entry_id] and not truncated


def test_orphan_collection_keeps_referenced_and_currently_prepared_files(setup):
    import os

    store, session = setup
    entry = saved(store, session)
    scope = store.database.memory.get(
        MemoryGetRequest(session_id=session["id"], entry_id=entry.entry_id)
    ).entry.scope_id
    service = store.database.memory
    orphan = service.repository.files.prepare(scope, "Unpublished crash payload")
    os.utime(service.repository.files.root / orphan, (1, 1))
    with service.prepared(scope, "A prepared payload") as prepared:
        os.utime(service.repository.files.root / prepared, (1, 1))
        assert service.collect_unreferenced_files() == 1
        assert service.repository.files.read(prepared) == "A prepared payload"
    assert (
        service.get(
            MemoryGetRequest(session_id=session["id"], entry_id=entry.entry_id)
        ).entry.content
        == "Prefer concise Chinese responses"
    )


def test_historical_search_reads_superseded_body_instead_of_latest_only(setup):
    store, session = setup
    entry = saved(store, session, "Use PostgreSQL for the 2025 project")
    service = store.database.memory
    service.manage(MemoryManageRequest(session_id=session["id"], operation_id="correct", entry_id=entry.entry_id, expected_revision=1, action="correct", content="Use SQLite for the 2026 project"))
    assert not service.read(MemoryReadRequest(session_id=session["id"], query="PostgreSQL")).entries
    historical = service.read(MemoryReadRequest(session_id=session["id"], query="PostgreSQL", include_history=True)).entries
    assert len(historical) == 1 and historical[0].status == "superseded" and historical[0].revision == 1


def test_fresh_context_redacts_revoked_tool_arguments_before_cleanup(setup):
    from eidos_runtime.context.builder import ContextBuilder

    store, session = setup
    entry = saved(store, session)
    run, _ = store.create_run(session["id"], "Search my saved preference")
    model = ScriptedModel([ModelResponse(tool_calls=(ModelToolCall("private-search", "memory_search", {"query": "Chinese"}),)), ModelResponse(text="Done")])
    RuntimeEngine(store, model, lambda _message: None).run(run["id"], threading.Event())
    scope = store.database.memory.get(MemoryGetRequest(session_id=session["id"], entry_id=entry.entry_id)).entry.scope_id
    with store.database.transaction() as connection:
        connection.execute("UPDATE memory_scopes SET privacy_epoch=privacy_epoch+1 WHERE id=?", (scope,))
    following, _ = store.create_run(session["id"], "Continue")
    context = ContextBuilder(store).build(following["id"]).model_context
    call = next(item for item in context if item.get("callId") == "private-search" and item["type"] == "tool_call")
    result = next(item for item in context if item.get("callId") == "private-search" and item["type"] == "tool_result")
    assert call["arguments"] == "{}"
    assert "memory_snapshot_revoked" in result["result"]


def test_memory_candidate_tool_requires_original_evidence_and_stays_pending(setup):
    store, session = setup
    run, source = store.create_run(session["id"], "Please use concise Chinese responses")
    model = ScriptedModel([ModelResponse(tool_calls=(ModelToolCall("candidate", "memory_record", {"content": "Prefer concise Chinese responses", "sourceItemIds": [source["id"]], "sourceQuotes": {source["id"]: "concise Chinese responses"}}),)), ModelResponse(text="Candidate proposed")])
    RuntimeEngine(store, model, lambda _message: None).run(run["id"], threading.Event())
    snapshot = store.read_session_snapshot(session["id"])
    assert snapshot["runs"][0]["status"] == "succeeded"
    tool = next(item["toolCall"] for item in snapshot["items"] if item["kind"] == "tool_call")
    result = json.loads(tool["resultJson"])
    assert result["outcome"] == "success" and result["data"]["action"]["status"] == "pending"
    state = store.database.memory.read(MemoryReadRequest(session_id=session["id"]))
    assert len(state.entries) == 1 and state.entries[0].status == "candidate"


@pytest.mark.parametrize("scope", ["current", "global"])
def test_temporary_switch_discards_independent_memory_response_and_snapshot(setup, scope):
    from eidos_runtime.memory.contracts import MemoryTemporaryRequest

    store, session = setup
    service = store.database.memory
    entry = service.record(MemoryWriteRequest(
        session_id=session["id"], operation_id="independent", scope=scope,
        content="Prefer concise Chinese responses",
    ))
    run, _ = store.create_run(session["id"], "Answer briefly")
    frozen_ids = []

    class SwitchingModel(ScriptedModel):
        def complete(self, context, cancel, on_text, **kwargs):
            if not self.contexts:
                self.contexts.append(context)
                attempts = store.read_model_attempts(run["id"])
                frozen = store.context_snapshot_repository().read_for_model_attempt(str(attempts[0]["id"]))
                frozen_ids.append(frozen.snapshot_id)
                service.set_temporary(MemoryTemporaryRequest(session_id=session["id"], temporary=True))
                assert cancel.is_set()
                return ModelResponse(text="Stale memory response.")
            return super().complete(context, cancel, on_text, **kwargs)

    model = SwitchingModel([ModelResponse(text="Fresh temporary response.")])
    RuntimeEngine(store, model, lambda _message: None).run(run["id"], threading.Event())
    assert len(model.contexts) == 2
    assert not any(item.get("sectionId") == "memory-evidence" for item in model.contexts[1])
    snapshot = store.read_session_snapshot(session["id"])
    assert snapshot["runs"][0]["status"] == "succeeded"
    assert not any(i.get("content") == "Stale memory response." and not i["incomplete"] for i in snapshot["items"])
    with pytest.raises(MemoryRejected, match="snapshot_revoked"):
        store.context_snapshot_repository().read(frozen_ids[0])
    # Re-enabling cannot resurrect a frozen request from before the switch.
    service.set_temporary(MemoryTemporaryRequest(session_id=session["id"], temporary=False))
    with pytest.raises(MemoryRejected, match="snapshot_revoked"):
        store.context_snapshot_repository().read(frozen_ids[0])
    assert service.get(MemoryGetRequest(session_id=session["id"], entry_id=entry.entry_id)).entry.content


def test_temporary_switch_revokes_tool_payload_even_without_projection(setup):
    from eidos_runtime.memory.contracts import MemoryTemporaryRequest

    store, session = setup
    saved(store, session)
    run, _ = store.create_run(session["id"], "Recall my preference")
    service = store.database.memory

    class SwitchingModel(ScriptedModel):
        def complete(self, context, cancel, on_text, **kwargs):
            if len(self.contexts) == 1:
                self.contexts.append(context)
                service.set_temporary(MemoryTemporaryRequest(session_id=session["id"], temporary=True))
                assert cancel.is_set()
                return ModelResponse(text="Stale tool response.")
            return super().complete(context, cancel, on_text, **kwargs)

    model = SwitchingModel([
        ModelResponse(tool_calls=(ModelToolCall("search", "memory_search", {"query": "Chinese"}),)),
        ModelResponse(text="Fresh response."),
    ])
    RuntimeEngine(store, model, lambda _message: None).run(run["id"], threading.Event())
    assert len(model.contexts) == 3
    tools = [i["toolCall"] for i in store.read_session_snapshot(session["id"])["items"] if i["kind"] == "tool_call"]
    assert "Chinese" not in str(tools)
    assert "memory_snapshot_revoked" in tools[0]["resultJson"]
    assert "Chinese" not in str([i for i in model.contexts[-1] if str(i.get("name", "")).startswith("memory_")])


def test_model_search_tool_can_continue_after_empty_short_word_page(setup):
    store, session = setup
    service = store.database.memory
    for index in range(500):
        service.record(MemoryWriteRequest(session_id=session["id"], operation_id=f"other-{index}", content=f"无关条目 {index}"))
    target = service.record(MemoryWriteRequest(session_id=session["id"], operation_id="target", content="请保持简洁"))
    # Discover the same opaque cursor that the actual tool must expose.
    cursor = service.read(MemoryReadRequest(session_id=session["id"], query="简洁", limit=5), for_use=True).next_cursor
    run, _ = store.create_run(session["id"], "检索简洁偏好")
    model = ScriptedModel([
        ModelResponse(tool_calls=(ModelToolCall("first", "memory_search", {"query": "简洁"}),)),
        ModelResponse(tool_calls=(ModelToolCall("next", "memory_search", {"query": "简洁", "cursor": cursor}),)),
        ModelResponse(text="已找到偏好。"),
    ])
    RuntimeEngine(store, model, lambda _message: None).run(run["id"], threading.Event())
    results = [json.loads(i["toolCall"]["resultJson"]) for i in store.read_session_snapshot(session["id"])["items"] if i["kind"] == "tool_call"]
    assert results[0]["outcome"] == "success"
    assert results[0]["data"]["entries"] == []
    assert results[0]["data"]["truncated"] and results[0]["data"]["next_cursor"] == cursor
    assert results[1]["outcome"] == "success"
    assert results[1]["data"]["entries"][0]["id"] == target.entry_id
    assert results[1]["data"].get("next_cursor") is None

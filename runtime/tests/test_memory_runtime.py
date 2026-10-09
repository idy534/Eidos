from __future__ import annotations

import json
import re
import threading
import zipfile

import pytest

from eidos_runtime.application.runs import RunApplication
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.memory.backup import backup, restore
from eidos_runtime.memory.contracts import (
    MemoryGetRequest,
    MemoryManageRequest,
    MemoryReadRequest,
    MemorySettings,
    MemorySettingsRequest,
    MemoryTemporaryRequest,
    MemoryWriteRequest,
)
from eidos_runtime.memory.repository import MemoryRejected
from eidos_runtime.model.client import ModelResponse, ModelToolCall, ModelUsage, ScriptedModel
from eidos_runtime.protocol.methods import ContextUsageRequestDto
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


def memory_state(instructions):
    match = re.search(r"<memory_state>\s*(.*?)\s*</memory_state>", instructions, re.S)
    assert match is not None
    return json.loads(match[1])


@pytest.mark.parametrize("input_tokens", [1850, 0, None])
def test_context_usage_survives_forgotten_memory_and_snapshot_cleanup(
    setup, monkeypatch, input_tokens
):
    store, session = setup
    entry = saved(store, session)
    run, _ = store.create_run(session["id"], "Answer briefly")
    model = ScriptedModel([ModelResponse(
        text="Done",
        usage=ModelUsage(input_tokens=input_tokens, output_tokens=10),
    )])
    runtime = RuntimeEngine(store, model, lambda _message: None)
    runtime.run(run["id"], threading.Event())
    snapshot = store.read_latest_context_snapshot(run["id"])
    assert snapshot is not None
    application = RunApplication(store=store, runtime=runtime)
    request = ContextUsageRequestDto(runId=run["id"])
    before = application.context_usage(request).context_usage
    assert before is not None

    service = store.database.memory
    service.manage(MemoryManageRequest(
        session_id=session["id"], operation_id="forget-usage",
        entry_id=entry.entry_id, expected_revision=1, action="forget",
    ))
    service.cleanup()
    row = store.connection.execute(
        "SELECT memory_revoked, snapshot_json FROM context_snapshots WHERE id=?",
        (snapshot.snapshot_id,),
    ).fetchone()
    assert row["memory_revoked"] == 1
    assert row["snapshot_json"] == "{}"
    with pytest.raises(MemoryRejected, match="memory_snapshot_revoked"):
        store.read_latest_context_snapshot(run["id"])

    def reject_blob_read(*args, **kwargs):
        raise AssertionError("context usage must not read request blobs")

    monkeypatch.setattr(store.database.json_blobs, "read", reject_blob_read)
    after = application.context_usage(request).context_usage
    assert after is not None
    assert after.active_tokens == before.active_tokens
    assert after.source == ("provider" if input_tokens else "estimated")
    assert after.active_tokens == (
        input_tokens if input_tokens else snapshot.plan.token_budget.projected_input_tokens
    )


@pytest.mark.parametrize(
    ("global_use", "current_use", "temporary", "read_scopes"),
    [
        (True, True, False, ["global", "project"]),
        (True, False, False, ["global"]),
        (False, False, False, []),
        (True, True, True, []),
    ],
)
def test_engine_advertises_memory_access_from_current_settings(
    setup, global_use, current_use, temporary, read_scopes
):
    store, session = setup
    service = store.database.memory
    service.settings(MemorySettingsRequest(
        session_id=session["id"], scope="global",
        settings=MemorySettings(use_enabled=global_use),
    ))
    service.settings(MemorySettingsRequest(
        session_id=session["id"], scope="current",
        settings=MemorySettings(use_enabled=current_use, generate_enabled=True),
    ))
    service.set_temporary(MemoryTemporaryRequest(
        session_id=session["id"], temporary=temporary,
    ))
    run, _ = store.create_run(session["id"], "Answer briefly")
    model = ScriptedModel([ModelResponse(text="Done")])
    RuntimeEngine(store, model, lambda _message: None).run(run["id"], threading.Event())

    tools = {tool.name for tool in model.tool_definitions_history[0] if tool.name.startswith("memory_")}
    expected = {"memory_search", "memory_read"} if read_scopes else set()
    if not temporary:
        expected.update({"memory_record", "memory_manage"})
    assert tools == expected
    assert memory_state(model.instructions_history[0]) == {
        "currentScope": "project",
        "readScopes": read_scopes,
        "automaticLearning": not temporary,
        "temporary": temporary,
    }
    assert store.read_run(run["id"])["status"] == "succeeded"


def test_explicit_save_remains_available_when_memory_use_is_disabled(setup):
    store, session = setup
    service = store.database.memory
    for scope in ("global", "current"):
        service.settings(MemorySettingsRequest(
            session_id=session["id"], scope=scope,
            settings=MemorySettings(use_enabled=False),
        ))
    run, _ = store.create_run(
        session["id"], "Remember that I prefer concise replies", approval_mode="full_access",
    )
    model = ScriptedModel([
        ModelResponse(tool_calls=(ModelToolCall("save", "memory_record", {
            "mode": "remember", "content": "Prefer concise replies",
            "sourceQuote": "I prefer concise replies",
        }),)),
        ModelResponse(text="Saved"),
    ])
    RuntimeEngine(store, model, lambda _message: None).run(run["id"], threading.Event())
    tool = next(i["toolCall"] for i in store.read_session_snapshot(session["id"])["items"] if i["kind"] == "tool_call")
    result = json.loads(tool["resultJson"])
    assert result["outcome"] == "success" and result["data"]["action"]["status"] == "applied"
    assert service.read(MemoryReadRequest(session_id=session["id"])).entries[0].status == "active"
    assert not any(item.get("sectionId") == "memory-evidence" for context in model.contexts for item in context)


@pytest.mark.parametrize("generate_enabled", [False, True])
def test_projectless_memory_state_uses_the_global_generation_setting(setup, generate_enabled):
    store, session = setup
    service = store.database.memory
    service.settings(MemorySettingsRequest(
        session_id=session["id"], scope="current", settings=MemorySettings(generate_enabled=True),
    ))
    service.settings(MemorySettingsRequest(
        session_id=session["id"], scope="global", settings=MemorySettings(generate_enabled=generate_enabled),
    ))
    root = store.data_directory / f".{store.data_directory.name}-projectless" / "memory"
    root.mkdir(parents=True)
    chat = store.typed_runtime_repository().create_session(str(root), projectless=True).value
    run, _ = store.create_run(chat.id, "Answer briefly")
    model = ScriptedModel([ModelResponse(text="Done")])
    RuntimeEngine(store, model, lambda _message: None).run(run["id"], threading.Event())
    assert memory_state(model.instructions_history[0]) == {
        "currentScope": "global", "readScopes": ["global"],
        "automaticLearning": generate_enabled, "temporary": False,
    }
    instructions = model.instructions_history[0]
    if generate_enabled:
        assert "mode=automatic" in instructions and "mode=candidate" in instructions
        assert "only confirmed" in instructions
    else:
        assert "Automatic and candidate writes are disabled" in instructions
        assert "memory_record(mode=automatic" not in instructions
        assert "use mode=candidate" not in instructions
    assert "mode=remember" in instructions
    assert store.read_run(run["id"])["status"] == "succeeded"


def test_memory_tools_and_settings_refresh_between_steps(setup):
    store, session = setup
    service = store.database.memory
    run, _ = store.create_run(session["id"], "Inspect the workspace")

    class SettingsModel(ScriptedModel):
        def complete(self, context, cancel, on_text, **kwargs):
            if not self.contexts:
                for scope in ("global", "current"):
                    service.settings(MemorySettingsRequest(
                        session_id=session["id"], scope=scope,
                        settings=MemorySettings(use_enabled=False, generate_enabled=scope == "current"),
                    ))
            return super().complete(context, cancel, on_text, **kwargs)

    model = SettingsModel([
        ModelResponse(tool_calls=(ModelToolCall("list", "list_files", {"path": "."}),)),
        ModelResponse(text="Done"),
    ])
    RuntimeEngine(store, model, lambda _message: None).run(run["id"], threading.Event())
    assert len(model.contexts) == 2
    assert memory_state(model.instructions_history[0])["readScopes"] == ["global", "project"]
    assert memory_state(model.instructions_history[1]) == {
        "currentScope": "project", "readScopes": [], "automaticLearning": True, "temporary": False,
    }
    assert {tool.name for tool in model.tool_definitions_history[1] if tool.name.startswith("memory_")} == {
        "memory_record", "memory_manage",
    }
    assert store.read_run(run["id"])["status"] == "succeeded"


def test_child_receives_read_access_without_memory_write_instructions(setup):
    from eidos_runtime.domain.collaboration import SpawnAgent
    from eidos_runtime.persistence.collaboration import CollaborationRepository

    store, session = setup
    store.database.memory.settings(MemorySettingsRequest(
        session_id=session["id"], scope="current", settings=MemorySettings(generate_enabled=True),
    ))
    parent, _ = store.create_run(session["id"], "Coordinate")
    item = store.create_tool_item(parent["id"], 1, 0, "spawn", "spawn_agent", "{}")
    child = CollaborationRepository(store.database).spawn(
        parent["id"], item["id"], SpawnAgent(task_name="inspect", message="Inspect", role="explorer"),
    )
    assert store.claim_next_run()["id"] == child.run_id
    model = ScriptedModel([ModelResponse(text="Done")])
    RuntimeEngine(store, model, lambda _message: None).run(child.run_id, threading.Event())
    assert {tool.name for tool in model.tool_definitions_history[0] if tool.name.startswith("memory_")} == {
        "memory_search", "memory_read",
    }
    instructions = model.instructions_history[0]
    assert "memory_record" not in instructions and "memory_manage" not in instructions
    assert memory_state(instructions)["automaticLearning"] is False
    assert store.read_run(child.run_id)["status"] == "succeeded"


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


@pytest.mark.parametrize("citation", ["latest", "item_id"])
def test_memory_candidate_tool_requires_original_evidence_and_stays_pending(setup, citation):
    store, session = setup
    store.database.memory.settings(MemorySettingsRequest(
        session_id=session["id"], scope="current",
        settings=MemorySettings(generate_enabled=True),
    ))
    run, source = store.create_run(session["id"], "Propose a pending memory that I prefer concise Chinese responses")
    quote = "concise Chinese responses"
    evidence = (
        {"sourceQuote": quote} if citation == "latest" else
        {"sourceItemIds": [source["id"]], "sourceQuotes": {source["id"]: quote}}
    )
    model = ScriptedModel([ModelResponse(tool_calls=(ModelToolCall("candidate", "memory_record", {"content": "Prefer concise Chinese responses", **evidence}),)), ModelResponse(text="Candidate proposed")])
    RuntimeEngine(store, model, lambda _message: None).run(run["id"], threading.Event())
    assert memory_state(model.instructions_history[0])["automaticLearning"] is True
    snapshot = store.read_session_snapshot(session["id"])
    assert snapshot["runs"][0]["status"] == "succeeded"
    tool = next(item["toolCall"] for item in snapshot["items"] if item["kind"] == "tool_call")
    result = json.loads(tool["resultJson"])
    assert result["outcome"] == "success" and result["data"]["action"]["status"] == "pending"
    state = store.database.memory.read(MemoryReadRequest(session_id=session["id"]))
    assert len(state.entries) == 1 and state.entries[0].status == "candidate"
    assert state.entries[0].evidence[0].item_id == source["id"]


def test_memory_candidate_tool_respects_disabled_generation(setup):
    store, session = setup
    run, _ = store.create_run(session["id"], "我喜欢吃香蕉")
    model = ScriptedModel([
        ModelResponse(tool_calls=(ModelToolCall("candidate", "memory_record", {
            "content": "用户喜欢吃香蕉。", "sourceQuote": "我喜欢吃香蕉",
        }),)),
        ModelResponse(text="No memory saved"),
    ])
    RuntimeEngine(store, model, lambda _message: None).run(run["id"], threading.Event())
    snapshot = store.read_session_snapshot(session["id"])
    tool = next(item["toolCall"] for item in snapshot["items"] if item["kind"] == "tool_call")
    assert json.loads(tool["resultJson"])["code"] == "memory_generation_disabled"
    assert store.database.memory.read(MemoryReadRequest(session_id=session["id"])).entries == []


def test_memory_candidate_tool_rejects_invented_quote(setup):
    store, session = setup
    store.database.memory.settings(MemorySettingsRequest(
        session_id=session["id"], scope="current",
        settings=MemorySettings(generate_enabled=True),
    ))
    run, _ = store.create_run(session["id"], "我喜欢吃香蕉")
    model = ScriptedModel([
        ModelResponse(tool_calls=(ModelToolCall("candidate", "memory_record", {
            "content": "用户喜欢吃香蕉。", "sourceQuote": "我喜欢吃苹果",
        }),)),
        ModelResponse(text="No memory saved"),
    ])
    RuntimeEngine(store, model, lambda _message: None).run(run["id"], threading.Event())
    snapshot = store.read_session_snapshot(session["id"])
    tool = next(item["toolCall"] for item in snapshot["items"] if item["kind"] == "tool_call")
    assert json.loads(tool["resultJson"])["code"] == "memory_evidence_quote_invalid"
    assert store.connection.execute(
        "SELECT count(*) FROM durable_intents WHERE tool_call_id=?", (tool["id"],)
    ).fetchone()[0] == 0
    assert store.database.memory.read(MemoryReadRequest(session_id=session["id"])).entries == []


def execute_memory_record(store, session, text, arguments):
    run, source = store.create_run(session["id"], text)
    model = ScriptedModel([
        ModelResponse(tool_calls=(ModelToolCall("save", "memory_record", arguments),)),
        ModelResponse(text="Done"),
    ])
    RuntimeEngine(store, model, lambda _message: None).run(run["id"], threading.Event())
    tool = next(
        item["toolCall"] for item in store.read_session_snapshot(session["id"])["items"]
        if item["kind"] == "tool_call" and item["runId"] == run["id"]
    )
    return json.loads(tool["resultJson"]), source


@pytest.mark.parametrize(("old_fact", "new_fact", "compound"), [
    ("用户名字是 Eddy", "以后都用中文回答", "用户名字是 Eddy，今后全部用中文回答"),
    ("本项目使用 PostgreSQL", "我习惯先写文档再写代码", "本项目使用 PostgreSQL，用户习惯先写文档再写代码"),
])
def test_record_publishes_sourced_model_proposal_without_secondary_review(setup, old_fact, new_fact, compound):
    store, session = setup
    saved(store, session, old_fact)
    store.database.memory.settings(MemorySettingsRequest(session_id=session["id"], scope="current", settings=MemorySettings(generate_enabled=True)))
    run, _ = store.create_run(session["id"], new_fact)
    model = ScriptedModel([
        ModelResponse(tool_calls=(ModelToolCall("save", "memory_record", {"mode": "automatic", "content": compound, "sourceQuote": new_fact}),)),
        ModelResponse(text="Saved"),
    ])
    RuntimeEngine(store, model, lambda message: None).run(run["id"], threading.Event())
    result = next(json.loads(item["toolCall"]["resultJson"]) for item in store.read_session_snapshot(session["id"])["items"] if item["kind"] == "tool_call")
    assert result["code"] == "memory_saved"
    assert len(model.contexts) == 2
    assert store.connection.execute("SELECT count(*) FROM durable_intents").fetchone()[0] == 1
    assert {e.content for e in store.database.memory.read(MemoryReadRequest(session_id=session["id"])).entries} == {old_fact, compound}


@pytest.mark.parametrize("mode", ["automatic", "candidate", "remember"])
def test_projectless_language_preference_saves_without_review_model(setup, mode):
    store, _ = setup
    root = store.data_directory / f".{store.data_directory.name}-projectless" / "language"
    root.mkdir(parents=True)
    session = store.typed_runtime_repository().create_session(str(root), projectless=True).value
    service = store.database.memory
    service.settings(MemorySettingsRequest(session_id=session.id, scope="global",
        settings=MemorySettings(generate_enabled=mode != "remember")))
    run, source = store.create_run(session.id, "以后都用中文回答", approval_mode="full_access")
    model = ScriptedModel([
        ModelResponse(tool_calls=(ModelToolCall("save", "memory_record", {
            "mode": mode, "scope": "current", "kind": "preference", "title": "回答语言：中文",
            "content": "用户（Eddy）要求此后一律用中文回答。", "sourceQuote": "以后都用中文回答",
        }),)),
        ModelResponse(text="已保存"),
    ])
    RuntimeEngine(store, model, lambda _message: None).run(run["id"], threading.Event())
    entry = service.read(MemoryReadRequest(session_id=session.id)).entries[0]
    assert entry.scope_id == "00000000-0000-4000-8000-000000000001"
    assert entry.status == ("candidate" if mode == "candidate" else "active")
    assert entry.evidence[0].item_id == source["id"]
    assert len(model.contexts) == 2
    assert store.connection.execute("SELECT count(*) FROM approvals").fetchone()[0] == (1 if mode == "remember" else 0)


@pytest.mark.parametrize("tool", ["memory_record", "memory_manage"])
def test_memory_write_keeps_source_revision_from_before_authorization(setup, monkeypatch, tool):
    from eidos_runtime.runtime.tool_execution import ToolExecutionController

    store, session = setup
    old = saved(store, session)
    text = "以后都用中文回答"
    run, source = store.create_run(session["id"], text, approval_mode="full_access")
    original = ToolExecutionController.authorize_side_effect

    def change_source_after_authorization(controller, **kwargs):
        result = original(controller, **kwargs)
        with store.database.transaction() as connection:
            connection.execute("UPDATE items SET content=? WHERE id=?", (text + "，只适用于当前项目", source["id"]))
        return result

    monkeypatch.setattr(ToolExecutionController, "authorize_side_effect", change_source_after_authorization)
    arguments = {"mode": "remember", "content": "用户要求以后都用中文回答", "sourceQuote": text} if tool == "memory_record" else {
        "action": "correct", "entryId": old.entry_id, "expectedRevision": 1, "content": "用户要求以后都用中文回答",
    }
    model = ScriptedModel([ModelResponse(tool_calls=(ModelToolCall("save", tool, arguments),)), ModelResponse(text="未保存")])
    RuntimeEngine(store, model, lambda _message: None).run(run["id"], threading.Event())
    result = next(json.loads(i["toolCall"]["resultJson"]) for i in store.read_session_snapshot(session["id"])["items"] if i["kind"] == "tool_call")
    assert result["code"] == "memory_source_conflict"
    assert store.database.memory.get(MemoryGetRequest(session_id=session["id"], entry_id=old.entry_id)).entry.content == "Prefer concise Chinese responses"
    assert store.connection.execute("SELECT count(*) FROM memory_actions").fetchone()[0] == 1


@pytest.mark.parametrize("action", ["reuse", "revise"])
def test_semantic_match_across_wording_and_kind_uses_same_identity(setup, action):
    store, session = setup
    service = store.database.memory
    service.settings(MemorySettingsRequest(session_id=session["id"], scope="current", settings=MemorySettings(generate_enabled=True)))
    original_run, original = store.create_run(session["id"], "我的编码习惯是先写文档再实现")
    old = service.record(MemoryWriteRequest(session_id=session["id"], operation_id="old-workflow", mode="automatic", kind="continuity", content="用户编码前先写文档", source_item_ids=[original["id"]]))
    store.fail_run(original_run["id"], "fixture_finished")
    text = "我还是先写说明再动手实现" if action == "reuse" else "我改变了编码顺序，以后先实现再写说明"
    content = "用户编码前先写文档" if action == "reuse" else "用户编码先实现后写说明"
    run, source = store.create_run(session["id"], text)
    model = ScriptedModel([
        ModelResponse(tool_calls=(ModelToolCall("save", "memory_record", {"mode": "automatic", "kind": "preference", "content": content, "sourceQuote": text, "targetEntryId": old.entry_id, "expectedRevision": 1}),)),
        ModelResponse(text="Done"),
    ])
    RuntimeEngine(store, model, lambda _message: None).run(run["id"], threading.Event())
    result = next(json.loads(i["toolCall"]["resultJson"]) for i in store.read_session_snapshot(session["id"])["items"] if i["kind"] == "tool_call")
    assert result["code"] == ("memory_unchanged" if action == "reuse" else "memory_saved")
    entries = service.read(MemoryReadRequest(session_id=session["id"])).entries
    assert len(entries) == 1 and entries[0].id == old.entry_id
    assert entries[0].revision == (1 if action == "reuse" else 2)
    if action == "revise":
        assert entries[0].content == content and entries[0].evidence[0].item_id == source["id"]


def test_memory_write_discards_revoked_access_during_preparation(setup, monkeypatch):
    from contextlib import contextmanager

    store, session = setup
    service = store.database.memory
    service.settings(MemorySettingsRequest(session_id=session["id"], scope="current", settings=MemorySettings(generate_enabled=True)))
    _, source = store.create_run(session["id"], "我偏好简短回答")
    with service.database.transaction() as connection:
        scope = service.repository.scopes(connection, session["id"], "current")[0]
        epochs = service.use_epochs(connection, session["id"], {scope.id: scope.privacy_epoch})
    original = service.prepared

    @contextmanager
    def revoke_during_prepare(*args):
        with original(*args) as reference:
            service.set_temporary(MemoryTemporaryRequest(session_id=session["id"], temporary=True))
            service.set_temporary(MemoryTemporaryRequest(session_id=session["id"], temporary=False))
            yield reference

    monkeypatch.setattr(service, "prepared", revoke_during_prepare)
    with pytest.raises(MemoryRejected, match="memory_snapshot_revoked"):
        service.record(MemoryWriteRequest(session_id=session["id"], operation_id="revoked-save", mode="automatic",
            content="用户偏好简短回答", source_item_ids=[source["id"]]), access_epochs=epochs)
    assert store.connection.execute("SELECT count(*) FROM memory_entries").fetchone()[0] == 0


def test_model_correction_uses_source_and_target_without_secondary_review(setup):
    store, session = setup
    old = saved(store, session, "用户偏好简短回答")
    run, _ = store.create_run(session["id"], "我现在希望回答给出详细解释", approval_mode="full_access")
    model = ScriptedModel([
        ModelResponse(tool_calls=(ModelToolCall("correct", "memory_manage", {"action": "correct", "entryId": old.entry_id, "expectedRevision": 1, "content": "用户希望回答给出详细解释"}),)),
        ModelResponse(text="Done"),
    ])
    RuntimeEngine(store, model, lambda _message: None).run(run["id"], threading.Event())
    result = next(json.loads(i["toolCall"]["resultJson"]) for i in store.read_session_snapshot(session["id"])["items"] if i["kind"] == "tool_call")
    assert result["code"] == "memory_correct_applied"
    assert len(model.contexts) == 2
    entries = store.database.memory.read(MemoryReadRequest(session_id=session["id"])).entries
    assert len(entries) == 1 and entries[0].id == old.entry_id and entries[0].revision == 2


def test_record_deduplicates_again_after_concurrent_publication(setup):
    store, session = setup
    service = store.database.memory
    service.settings(MemorySettingsRequest(session_id=session["id"], scope="current", settings=MemorySettings(generate_enabled=True)))
    _, source = store.create_run(session["id"], "用户习惯先写文档")
    request = MemoryWriteRequest(session_id=session["id"], operation_id="first-proposal", mode="automatic", content="用户习惯先写文档", source_item_ids=[source["id"]])
    other = service.record(request.model_copy(update={"operation_id": "concurrent-publication"}))
    result = service.record(request)
    assert result.code == "memory_unchanged" and result.entry_id == other.entry_id
    entries = service.read(MemoryReadRequest(session_id=session["id"])).entries
    assert len(entries) == 1 and entries[0].id == other.entry_id


@pytest.mark.parametrize("work_mode", ["execute", "plan"])
@pytest.mark.parametrize(("mode", "evidence_class", "status"), [
    ("automatic", "explicit_user", "active"),
    ("candidate", "inferred", "candidate"),
    ("candidate", "explicit_user", "candidate"),
])
def test_reusable_workflow_memory_depends_on_claim_state_not_run_mode(setup, work_mode, mode, evidence_class, status):
    store, session = setup
    service = store.database.memory
    service.settings(MemorySettingsRequest(session_id=session["id"], scope="current", settings=MemorySettings(generate_enabled=True)))
    text = "我写代码时习惯先写相关文档，再实现代码。"
    _, source = store.create_run(session["id"], text, work_mode=work_mode)
    result = service.record(MemoryWriteRequest(session_id=session["id"], operation_id="workflow-memory",
        content=text, source_item_ids=[source["id"]], mode=mode, evidence_class=evidence_class))
    entry = service.get(MemoryGetRequest(session_id=session["id"], entry_id=result.entry_id)).entry
    assert entry.kind == "preference" and entry.content == text
    assert entry.status == status
    assert bool(service.read(MemoryReadRequest(session_id=session["id"]), for_use=True).entries) == (status == "active")


@pytest.mark.parametrize(("mode", "target_mode", "code"), [
    ("automatic", "candidate", "memory_confirmed_evidence_required"),
    ("candidate", "automatic", "memory_candidate_target_invalid"),
])
def test_memory_reuse_cannot_cross_mode_statuses(setup, mode, target_mode, code):
    store, session = setup
    service = store.database.memory
    service.settings(MemorySettingsRequest(
        session_id=session["id"], scope="current", settings=MemorySettings(generate_enabled=True),
    ))
    _, source = store.create_run(session["id"], "我偏好短回答")
    target_request = MemoryWriteRequest(
        session_id=session["id"], operation_id="reuse-target", mode=target_mode,
        content="我偏好短回答", source_item_ids=[source["id"]],
    )
    target = service.record(target_request)
    with pytest.raises(MemoryRejected, match=code):
        service.record(target_request.model_copy(update={"mode": mode, "operation_id": "cross-mode-reuse",
            "target_entry_id": target.entry_id, "expected_revision": target.revision}))
    assert store.connection.execute("SELECT count(*) FROM memory_actions").fetchone()[0] == 1


def test_legacy_automatic_candidate_replays_without_new_publication(setup):
    store, session = setup
    service = store.database.memory
    service.settings(MemorySettingsRequest(
        session_id=session["id"], scope="current", settings=MemorySettings(generate_enabled=True),
    ))
    _, source = store.create_run(session["id"], "用户可能偏好短回答")
    request = MemoryWriteRequest(
        session_id=session["id"], operation_id="legacy-automatic-candidate",
        mode="candidate", evidence_class="inferred", content="用户可能偏好短回答",
        source_item_ids=[source["id"]],
    )
    pending = service.record(request)
    legacy = request.model_copy(update={"mode": "automatic"})
    with service.database.transaction() as connection:
        connection.execute(
            "UPDATE memory_actions SET action='automatic',request_json=? WHERE operation_id=?",
            (legacy.model_dump_json(), request.operation_id),
        )
    assert service.record(legacy) == pending
    assert service.get(MemoryGetRequest(session_id=session["id"], entry_id=pending.entry_id)).entry.status == "candidate"
    assert store.connection.execute("SELECT count(*) FROM memory_entries").fetchone()[0] == 1
    with pytest.raises(MemoryRejected, match="memory_confirmed_evidence_required"):
        service.record(legacy.model_copy(update={"operation_id": "new-automatic"}))


def test_automatic_name_save_is_immediate_and_deduplicated(setup):
    store, session = setup
    service = store.database.memory
    service.settings(MemorySettingsRequest(
        session_id=session["id"], scope="current", settings=MemorySettings(generate_enabled=True),
    ))
    arguments = {"mode": "automatic", "content": "用户的名字是 Eddy", "sourceQuote": "我的名字是 Eddy"}
    first, source = execute_memory_record(store, session, "我的名字是 Eddy", arguments)
    second, _ = execute_memory_record(store, session, "我的名字是 Eddy", arguments)
    assert first["data"]["action"]["status"] == "applied"
    assert second["data"]["action"]["entryId"] == first["data"]["action"]["entryId"]
    entries = service.read(MemoryReadRequest(session_id=session["id"]), for_use=True).entries
    assert len(entries) == 1 and entries[0].revision == 1
    assert entries[0].evidence[0].item_id == source["id"]
    assert store.connection.execute("SELECT count(*) FROM approvals").fetchone()[0] == 0
    assert store.connection.execute("SELECT count(*) FROM memory_jobs").fetchone()[0] == 0


@pytest.mark.parametrize("mode", ["automatic", "candidate"])
def test_record_resolves_fact_quote_from_earlier_user_message(setup, mode):
    store, session = setup
    store.database.memory.settings(MemorySettingsRequest(
        session_id=session["id"], scope="current", settings=MemorySettings(generate_enabled=True),
    ))
    earlier, source = store.create_run(session["id"], "我的名字是 Eddy")
    store.fail_run(earlier["id"], "fixture_finished")
    result, authorization = execute_memory_record(store, session, "你怎么不记住我叫什么？", {
        "mode": mode, "content": "用户的名字是 Eddy", "sourceQuote": "我的名字是 Eddy",
    })
    assert result["outcome"] == "success"
    entry = store.database.memory.read(MemoryReadRequest(session_id=session["id"])).entries[0]
    assert entry.evidence[0].item_id == source["id"] != authorization["id"]


@pytest.mark.parametrize(("settings", "arguments", "code"), [
    (MemorySettings(), {}, "memory_generation_disabled"),
    (MemorySettings(generate_enabled=True), {"scope": "global"}, "memory_candidate_scope_invalid"),
    (MemorySettings(generate_enabled=True), {"sourceQuote": "我叫 Nobody"}, "memory_evidence_quote_invalid"),
])
def test_automatic_record_rejects_without_committing_intent(setup, settings, arguments, code):
    store, session = setup
    store.database.memory.settings(MemorySettingsRequest(
        session_id=session["id"], scope="current", settings=settings,
    ))
    result, _ = execute_memory_record(store, session, "我的名字是 Eddy", {
        "mode": "automatic", "content": "用户的名字是 Eddy", "sourceQuote": "我的名字是 Eddy", **arguments,
    })
    assert result["code"] == code
    assert store.connection.execute("SELECT count(*) FROM durable_intents").fetchone()[0] == 0
    assert store.database.memory.read(MemoryReadRequest(session_id=session["id"])).entries == []


def test_automatic_record_updates_with_cas_and_respects_explicit_control(setup):
    store, session = setup
    service = store.database.memory
    service.settings(MemorySettingsRequest(
        session_id=session["id"], scope="current", settings=MemorySettings(generate_enabled=True),
    ))
    first, _ = execute_memory_record(store, session, "我的名字是 Eddy", {
        "mode": "automatic", "content": "用户的名字是 Eddy", "sourceQuote": "我的名字是 Eddy",
    })
    identifier = first["data"]["action"]["entryId"]
    update = {"mode": "automatic", "content": "用户的名字是 Eddie", "sourceQuote": "我的名字改为 Eddie",
              "targetEntryId": identifier, "expectedRevision": 1}
    result, _ = execute_memory_record(store, session, "我的名字改为 Eddie", update)
    assert result["data"]["action"]["entryId"] == identifier
    assert result["data"]["action"]["revision"] == 2
    stale, _ = execute_memory_record(store, session, "我的名字改为 Eddie", update)
    assert stale["code"] == "memory_revision_conflict"
    service.manage(MemoryManageRequest(session_id=session["id"], operation_id="pin-name",
        entry_id=identifier, expected_revision=2, action="pin"))
    update.update(content="用户的名字是 Ed", sourceQuote="我的名字改为 Ed", expectedRevision=3)
    protected, _ = execute_memory_record(store, session, "我的名字改为 Ed", update)
    assert protected["code"] == "memory_explicit_action_protected"
    assert service.get(MemoryGetRequest(session_id=session["id"], entry_id=identifier)).entry.content == "用户的名字是 Eddie"


def test_automatic_experience_uses_successful_tool_evidence(setup):
    store, session = setup
    from pathlib import Path
    Path(session["workspaceRoot"], "proof.py").write_text("pass\n")
    store.database.memory.settings(MemorySettingsRequest(
        session_id=session["id"], scope="current", settings=MemorySettings(generate_enabled=True),
    ))
    run, _ = store.create_run(session["id"], "Inspect the project")
    model = ScriptedModel([
        ModelResponse(tool_calls=(ModelToolCall("list", "list_files", {"path": "."}),)),
        ModelResponse(tool_calls=(ModelToolCall("save", "memory_record", {
            "mode": "automatic", "kind": "experience", "content": "proof.py is available",
            "evidenceClass": "observed_verified", "sourceQuote": "proof.py",
        }),)),
        ModelResponse(text="Done"),
    ])
    RuntimeEngine(store, model, lambda _message: None).run(run["id"], threading.Event())
    entry = store.database.memory.read(MemoryReadRequest(session_id=session["id"])).entries[0]
    assert entry.status == "active" and entry.evidence_class == "observed_verified"
    source = store.read_item(entry.evidence[0].item_id)
    assert source["kind"] == "tool_call" and source["toolCall"]["toolName"] == "list_files"


@pytest.mark.parametrize("evidence_class", ["explicit_user", "observed_verified"])
def test_unverified_or_inferred_claim_is_not_published_as_a_fact(setup, evidence_class):
    store, session = setup
    store.database.memory.settings(MemorySettingsRequest(
        session_id=session["id"], scope="current", settings=MemorySettings(generate_enabled=True),
    ))
    if evidence_class == "explicit_user":
        result, _ = execute_memory_record(store, session, "假设我叫 Eddy", {
            "mode": "automatic", "content": "用户可能叫 Eddy", "sourceQuote": "假设我叫 Eddy", "evidenceClass": "inferred",
        })
        assert result["code"] == "memory_confirmed_evidence_required"
        assert store.connection.execute("SELECT count(*) FROM durable_intents").fetchone()[0] == 0
        assert not store.database.memory.read(MemoryReadRequest(session_id=session["id"])).entries
        assert not store.database.memory.read(MemoryReadRequest(session_id=session["id"]), for_use=True).entries
    else:
        result, _ = execute_memory_record(store, session, "我说测试都通过了", {
            "mode": "automatic", "content": "测试已通过", "sourceQuote": "测试都通过了", "evidenceClass": "observed_verified",
        })
        assert result["outcome"] == "error"
        assert store.connection.execute("SELECT count(*) FROM durable_intents").fetchone()[0] == 0


def test_automatic_record_rechecks_consent_and_cancel_before_publication(setup, monkeypatch):
    store, session = setup
    service = store.database.memory
    service.settings(MemorySettingsRequest(session_id=session["id"], scope="current", settings=MemorySettings(generate_enabled=True)))
    run, source = store.create_run(session["id"], "我的名字是 Eddy")
    request = MemoryWriteRequest(session_id=session["id"], operation_id="save-race", mode="automatic",
        content="用户的名字是 Eddy", source_item_ids=[source["id"]])
    from contextlib import contextmanager
    original = service.prepared

    @contextmanager
    def disable_during_prepare(*args):
        with original(*args) as reference:
            service.settings(MemorySettingsRequest(session_id=session["id"], scope="current", settings=MemorySettings()))
            yield reference

    monkeypatch.setattr(service, "prepared", disable_during_prepare)
    with pytest.raises(MemoryRejected, match="memory_generation_disabled"):
        service.record(request)
    monkeypatch.setattr(service, "prepared", original)
    service.settings(MemorySettingsRequest(session_id=session["id"], scope="current", settings=MemorySettings(generate_enabled=True)))
    # A fresh source stays inside the new consent window.
    store.fail_run(run["id"], "fixture_finished")
    _, fresh = store.create_run(session["id"], "我的名字是 Eddy")
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(MemoryRejected, match="memory_canceled"):
        service.record(request.model_copy(update={"source_item_ids": [fresh["id"]]}), cancel=cancel)
    assert not service.read(MemoryReadRequest(session_id=session["id"])).entries


@pytest.mark.parametrize("mode", ["candidate", "remember"])
def test_legacy_record_operation_replays_without_changing_mode(setup, mode):
    store, session = setup
    service = store.database.memory
    service.settings(MemorySettingsRequest(session_id=session["id"], scope="current", settings=MemorySettings(generate_enabled=True)))
    _, source = store.create_run(session["id"], "我的名字是 Eddy")
    request = MemoryWriteRequest(session_id=session["id"], operation_id="legacy-save", mode=mode,
        content="用户的名字是 Eddy", source_item_ids=[source["id"]])
    result = service.record(request)
    old = request.model_dump(exclude={"mode", "evidence_class", "target_entry_id", "expected_revision"})
    with store.database.transaction() as connection:
        connection.execute("UPDATE memory_actions SET request_json=? WHERE operation_id=?", (json.dumps(old), request.operation_id))
    assert service.record(request) == result
    with pytest.raises(MemoryRejected, match="memory_operation_conflict"):
        service.record(request.model_copy(update={"mode": "automatic"}))


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


def test_backup_excludes_revoked_source_action_body(setup, tmp_path):
    import sqlite3

    store, session = setup
    run, item = store.create_run(session["id"], "private source preference")
    entry = store.database.memory.record(MemoryWriteRequest(session_id=session["id"], operation_id="private-source", content="private source preference", source_item_ids=[item["id"]]))
    store.fail_run(run["id"], "fixture_finished")
    store.delete_session(session["id"])
    archive = tmp_path / "cleaned.zip"
    backup(store.database.memory, archive)
    with zipfile.ZipFile(archive) as zipped:
        state = tmp_path / "copied.sqlite"
        state.write_bytes(zipped.read("state.sqlite"))
    with sqlite3.connect(state) as connection:
        request, result = connection.execute("SELECT request_json,result_json FROM memory_actions WHERE entry_id=?", (entry.entry_id,)).fetchone()
        assert request == "[privacy-revoked]"
        assert entry.entry_id in result
        assert not connection.execute("SELECT 1 FROM memory_actions WHERE request_json LIKE '%private source preference%'").fetchone()


def test_v18_backup_restores_with_session_use_migration(setup, tmp_path):
    import sqlite3
    from eidos_runtime.db.collaboration_migration import COLLABORATION_SCHEMA_SQL

    store, session = setup
    entry = saved(store, session)
    current = tmp_path / "current.zip"
    backup(store.database.memory, current)
    with zipfile.ZipFile(current) as source:
        members = {name: source.read(name) for name in source.namelist()}
    state = tmp_path / "v18.sqlite"
    state.write_bytes(members["state.sqlite"])
    with sqlite3.connect(state) as connection:
        # Reconstruct the old role CHECK as well as the old memory columns.
        assert connection.execute('SELECT COUNT(*) FROM agent_delegations').fetchone()[0] == 0
        connection.executescript('DROP TABLE agent_delegations;\n' + COLLABORATION_SCHEMA_SQL.split('CREATE TABLE agent_messages', 1)[0])
        connection.execute('ALTER TABLE agent_delegations ADD COLUMN required_for_completion INTEGER NOT NULL DEFAULT 1 CHECK(required_for_completion IN (0,1))')
        connection.execute("DROP TRIGGER memory_session_use_revoke")
        connection.execute("ALTER TABLE memory_sources DROP COLUMN use_epoch")
        connection.execute("ALTER TABLE memory_sources DROP COLUMN backfill_until")
        connection.execute("PRAGMA user_version=18")
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()
    members["state.sqlite"] = state.read_bytes()
    manifest = json.loads(members["manifest.json"])
    manifest["schemaVersion"] = 18
    members["manifest.json"] = json.dumps(manifest).encode()
    legacy = tmp_path / "legacy.zip"
    with zipfile.ZipFile(legacy, "w") as target:
        for name, data in members.items():
            target.writestr(name, data)
    destination = tmp_path / "migrated"
    restore(legacy, destination)
    reopened = SessionStore(destination)
    reopened.initialize()
    try:
        assert reopened.connection.execute("PRAGMA user_version").fetchone()[0] == 21
        assert reopened.database.memory.get(MemoryGetRequest(session_id=session["id"], entry_id=entry.entry_id)).entry.content == "Prefer concise Chinese responses"
    finally:
        reopened.close()


def test_child_memory_use_is_revoked_with_parent_temporary_switch(setup):
    from eidos_runtime.domain.collaboration import SpawnAgent
    from eidos_runtime.memory.contracts import MemoryTemporaryRequest
    from eidos_runtime.persistence.collaboration import CollaborationRepository

    store, session = setup
    saved(store, session)
    parent, _ = store.create_run(session["id"], "Coordinate")
    item = store.create_tool_item(parent["id"], 1, 0, "spawn", "spawn_agent", "{}")
    child = CollaborationRepository(store.database).spawn(parent["id"], item["id"], SpawnAgent(task_name="inspect", message="Inspect the context", role="explorer"))
    service = store.database.memory
    epochs = service.project(child.session_id, 100000, run_id=child.run_id).epochs
    assert "session:" + session["id"] in epochs
    with pytest.raises(MemoryRejected, match="snapshot_revoked"):
        with service.admit(epochs, threading.Event()) as cancel:
            service.set_temporary(MemoryTemporaryRequest(session_id=session["id"], temporary=True))
            assert cancel.is_set()
    assert service.read(MemoryReadRequest(session_id=child.session_id), for_use=True).entries == []

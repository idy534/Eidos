from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import threading

from pydantic import ValidationError
import pytest

from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.application.personalization import PersonalizationApplication
from eidos_runtime.context.builder import ContextBuilder
from eidos_runtime.db.backup import backup, restore
from eidos_runtime.db.personalization_migration import migrate_personalization
from eidos_runtime.db.schema import SCHEMA_VERSION, V43_SCHEMA_SQL
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.domain.collaboration import SpawnAgent
from eidos_runtime.domain.personalization import PersonalizationPreferences, PersonalizationSettings
from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel
from eidos_runtime.persistence.collaboration import CollaborationRepository
from eidos_runtime.persistence.personalization import PersonalizationRepository
from eidos_runtime.protocol.personalization import PersonalizationReadRequest, PersonalizationResponse, PersonalizationSaveRequest
from eidos_runtime.runtime.engine import RuntimeEngine
from eidos_runtime.runtime.resolution import RunResolutionSnapshot
from eidos_runtime.sandbox.sensitive import SensitiveScanner


@pytest.fixture
def store(tmp_path):
    owner = SessionStore(tmp_path / "data")
    owner.initialize()
    yield owner
    owner.close()


def create_run(store, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    session = store.create_session(str(workspace))
    run, _ = store.create_run(session["id"], "Please answer this task in English.")
    return run


def test_save_restart_conflict_noop_and_transaction_failure(store):
    repository = PersonalizationRepository(store.database)
    application = PersonalizationApplication(repository, lambda value: SensitiveScanner().scan_text(value).text)
    assert application.read().preferences == PersonalizationPreferences()
    preferences = PersonalizationPreferences(response_language="zh-CN", preferred_name="小李", custom_instructions="先给结论。")
    request = PersonalizationSaveRequest(expected_revision=0, preferences=preferences)
    saved = application.save(request)
    assert saved.revision == 1
    with pytest.raises(ApplicationError) as error:
        application.save(request)
    assert error.value.code == "PERSONALIZATION_CONFLICT"
    assert repository.save(preferences, 1).revision == 1
    events = store.connection.execute("SELECT payload_json FROM events WHERE event_type='personalization.settings_changed'").fetchall()
    assert len(events) == 1 and json.loads(events[0][0]) == {"id": "personalization"}
    store.connection.executescript("CREATE TRIGGER fail_personalization BEFORE UPDATE ON personalization_settings BEGIN SELECT RAISE(ABORT, 'fixture'); END;")
    with pytest.raises(sqlite3.Error):
        repository.save(PersonalizationPreferences(tone="formal"), 1)
    assert repository.read().preferences == preferences
    assert len(store.connection.execute("SELECT 1 FROM events WHERE event_type='personalization.settings_changed'").fetchall()) == 1
    directory = store.data_directory
    store.close()
    restarted = SessionStore(directory)
    restarted.initialize()
    try:
        assert PersonalizationRepository(restarted.database).read().preferences == preferences
    finally:
        restarted.close()


@pytest.mark.parametrize("field", ["customLanguage", "preferredName", "role", "familiarTopics", "customInstructions"])
@pytest.mark.parametrize("secret", ["sk-" + "A" * 20, "password=synthetic-fixture"])
def test_sensitive_preferences_are_rejected_before_any_database_write(store, field, secret):
    repository = PersonalizationRepository(store.database)
    application = PersonalizationApplication(repository, lambda value: SensitiveScanner().scan_text(value).text)
    with pytest.raises(ApplicationError) as error:
        application.save(PersonalizationSaveRequest.model_validate({"expectedRevision": 0, "preferences": {field: secret}}))
    assert error.value.code == "SENSITIVE_CONTENT_REJECTED"
    assert repository.read() == PersonalizationSettings()
    assert store.connection.execute("SELECT count(*) FROM personalization_settings").fetchone()[0] == 0
    assert store.connection.execute("SELECT count(*) FROM events WHERE event_type='personalization.settings_changed'").fetchone()[0] == 0


def test_sensitive_scan_failure_does_not_save_preferences(store):
    from eidos_runtime.sandbox.sensitive import SensitiveScanError

    def failed_scan(_value):
        raise SensitiveScanError("fixture unavailable")

    repository = PersonalizationRepository(store.database)
    with pytest.raises(ApplicationError) as error:
        PersonalizationApplication(repository, failed_scan).save(PersonalizationSaveRequest(expected_revision=0, preferences=PersonalizationPreferences(role="developer")))
    assert error.value.code == "SENSITIVE_SCAN_FAILED"
    assert repository.read() == PersonalizationSettings()


@pytest.mark.parametrize("entry", ["database", "layout"])
def test_v43_upgrade_preserves_existing_settings(tmp_path, entry):
    from eidos_runtime.db.database import Database
    from eidos_runtime.db.layout import _migrate_state_schema

    directory = tmp_path / "old-data"
    directory.mkdir(mode=0o700)
    path = directory / "state.sqlite"
    with sqlite3.connect(path) as connection:
        connection.executescript(V43_SCHEMA_SQL + "PRAGMA user_version=43;")
        connection.execute("UPDATE runtime_settings SET managed_worktree_limit=7")
    path.chmod(0o600)
    if entry == "layout":
        owner = Database(directory)
        owner._connection = sqlite3.connect(path)
        try:
            _migrate_state_schema(owner)
        finally:
            owner.close()
    owner = SessionStore(directory)
    owner.initialize()
    try:
        assert owner.connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert owner.connection.execute("SELECT managed_worktree_limit FROM runtime_settings").fetchone()[0] == 7
        assert PersonalizationRepository(owner.database).read() == PersonalizationSettings()
    finally:
        owner.close()


def test_migration_failure_rolls_back():
    connection = sqlite3.connect(":memory:")
    connection.executescript("CREATE TABLE personalization_settings(id); PRAGMA user_version=43;")
    with pytest.raises(sqlite3.Error):
        migrate_personalization(connection)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 43
    assert not connection.in_transaction
    connection.close()


def test_run_snapshot_freezes_preferences_and_survives_restart(store, tmp_path):
    repository = PersonalizationRepository(store.database)
    preferences = PersonalizationPreferences(response_language="zh-CN", tone="direct", response_length="concise", output_format="conclusion_first", preferred_name="小李", role="工程师", familiar_topics="Python", custom_instructions="MARKER: explain evidence.")
    repository.save(preferences, 0)
    run = create_run(store, tmp_path)
    first = ContextBuilder(store).build(run["id"])
    repository.save(PersonalizationPreferences(response_language="en", custom_instructions="NEW MARKER"), 1)
    second = ContextBuilder(store).build(run["id"])
    assert first.instructions == second.instructions
    assert "MARKER: explain evidence." not in first.instructions.system_text
    layers = {layer.id: layer for layer in first.instructions.user_context_layers}
    assert all(layer.role == "user" for key, layer in layers.items() if key.startswith("personalization-"))
    assert "Simplified Chinese" in layers["personalization-expression"].content
    assert "小李" in layers["personalization-profile"].content
    assert "MARKER: explain evidence." in layers["personalization-instructions"].content
    assert first.model_context[-1]["content"] == "Please answer this task in English."
    directory = store.data_directory
    store.close()
    restarted = SessionStore(directory)
    restarted.initialize()
    try:
        frozen = restarted.read_run_resolution_snapshot(run["id"]).personalization
        assert frozen.preferences == preferences
        assert ContextBuilder(restarted).build(run["id"]).instructions == first.instructions
        next_run = create_run(restarted, tmp_path)
        assert restarted.read_run_resolution_snapshot(next_run["id"]).personalization.revision == 2
    finally:
        restarted.close()


def test_legacy_run_without_personalization_keeps_its_original_defaults(store, tmp_path):
    run = create_run(store, tmp_path)
    snapshot = store.read_run_resolution_snapshot(run["id"])
    legacy = RunResolutionSnapshot.create(**snapshot.model_dump(exclude_none=True, exclude={"id", "snapshot_hash", "schema_version", "personalization"}))
    with store.database.transaction() as connection:
        connection.execute("UPDATE run_resolution_snapshots SET id=?, snapshot_hash=?, snapshot_json=? WHERE run_id=?", (legacy.id, legacy.snapshot_hash, legacy.model_dump_json(exclude_none=True), run["id"]))
    PersonalizationRepository(store.database).save(PersonalizationPreferences(custom_instructions="new global text"), 0)
    build = ContextBuilder(store).build(run["id"])
    assert all(not layer.id.startswith("personalization-") for layer in build.instructions.layers)


def test_child_agent_inherits_parent_frozen_preferences(store, tmp_path):
    repository = PersonalizationRepository(store.database)
    repository.save(PersonalizationPreferences(response_language="zh-TW"), 0)
    parent = create_run(store, tmp_path)
    repository.save(PersonalizationPreferences(response_language="en"), 1)
    item = store.create_tool_item(parent["id"], 1, 0, "spawn", "spawn_agent", "{}")
    child = CollaborationRepository(store.database).spawn(parent["id"], item["id"], SpawnAgent(task_name="inspect", message="Inspect the code.", role="explorer"))
    assert store.read_run_resolution_snapshot(child.run_id).personalization.preferences.response_language == "zh-TW"


def test_channel_child_inherits_only_shared_expression_preferences(store, tmp_path):
    from eidos_runtime.db.repositories.runs import RunRepository

    PersonalizationRepository(store.database).save(PersonalizationPreferences(response_language="zh-TW", preferred_name="PRIVATE NAME", role="PRIVATE ROLE", familiar_topics="PRIVATE TOPICS", custom_instructions="PRIVATE INSTRUCTIONS"), 0)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = store.create_session(str(workspace))
    parent, _ = RunRepository(store.database).create_run(session["id"], "channel task", channel_context=True)
    item = store.create_tool_item(parent["id"], 1, 0, "spawn", "spawn_agent", "{}")
    child = CollaborationRepository(store.database).spawn(parent["id"], item["id"], SpawnAgent(task_name="inspect", message="Inspect the code.", role="explorer"))
    for run_id in (parent["id"], child.run_id):
        snapshot = store.read_run_resolution_snapshot(run_id).personalization
        assert snapshot.preferences.response_language == "zh-TW"
        assert "PRIVATE" not in snapshot.model_dump_json()
        assert "PRIVATE" not in json.dumps(ContextBuilder(store).build(run_id).model_context)


@pytest.mark.parametrize("edited_input", [None, "Edited channel task"])
def test_channel_regeneration_and_edits_keep_private_preferences_out_of_context(tmp_path, edited_input):
    import io
    import time
    from uuid import uuid4
    from eidos_runtime.domain.channels import ChannelConfig, ChannelMessage
    from eidos_runtime.model.config import DEFAULT_MODEL_ID
    from eidos_runtime.protocol.channels import ChannelSaveRequest
    from eidos_runtime.protocol.response_server import ResponseActionRuntimeServer

    model = ScriptedModel([ModelResponse(text="Channel response"), ModelResponse(text="Revised response")])
    server = ResponseActionRuntimeServer(io.StringIO(), tmp_path / "data", model=model)
    try:
        server.handle({"jsonrpc": "2.0", "id": "client-init", "method": "initialize", "params": {"protocolVersion": 1, "client": {"name": "fixture", "version": "1"}}})
        assert server.channel_service is not None
        server.channel_service.active = lambda: False
        configured = server.model_config.create(provider_id="deepseek", model_id=DEFAULT_MODEL_ID, api_key="fixture-key")
        PersonalizationRepository(server.store.database).save(PersonalizationPreferences(response_language="zh-CN", preferred_name="PRIVATE NAME", custom_instructions="PRIVATE INSTRUCTIONS"), 0)
        application = server.channel_service.application
        application.save(ChannelSaveRequest(config=ChannelConfig(client_id="app", model_id=configured.id), expected_revision=0, client_secret="fixture-channel-secret"))
        connection = application.repository.connection()
        connection = connection.model_copy(update={"revision": 2, "config": connection.config.model_copy(update={"enabled": True})})
        application.repository.save(connection, 1)
        application.receive(connection, ChannelMessage(msg_id="fixture", corp_id="corp", robot_code="robot", conversation_id="group", conversation_type="2", sender_id="another-user", mentioned=True, text="Channel task"))
        application.process_pending()
        source_id = server.store.connection.execute("SELECT run_id FROM channel_inbound").fetchone()[0]
        deadline = time.monotonic() + 10
        while server.store.read_run(source_id)["status"] != "succeeded" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.store.read_run(source_id)["status"] == "succeeded"
        params = {"sourceRunId": source_id, "operationId": str(uuid4())}
        if edited_input is not None:
            params["userInput"] = edited_input
        server.handle({"jsonrpc": "2.0", "id": "client-revise", "method": "run/revise", "params": params})
        revision = server.store.connection.execute("SELECT run_id FROM run_revisions WHERE source_run_id=? ORDER BY created_at DESC LIMIT 1", (source_id,)).fetchone()
        assert revision is not None
        run_id = revision[0]
        deadline = time.monotonic() + 10
        while server.store.read_run(run_id)["status"] in {"queued", "running", "finalizing"} and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.store.read_run(run_id)["status"] == "succeeded"
        assert "PRIVATE" not in server.store.read_run_resolution_snapshot(run_id).personalization.model_dump_json()
        assert "PRIVATE" not in json.dumps(model.contexts)
    finally:
        server.close()


@pytest.mark.parametrize("recovery_level", [1, 3, 7])
def test_long_history_recovery_keeps_all_personalization_layers(store, tmp_path, recovery_level):
    preferences = PersonalizationPreferences(
        response_language="zh-CN", preferred_name="小李", familiar_topics="Python",
        custom_instructions="始终保留这条全局指令。" * 500 + " END OF GLOBAL INSTRUCTIONS",
    )
    PersonalizationRepository(store.database).save(preferences, 0)
    run = create_run(store, tmp_path)
    for index in range(15):
        item = store.create_assistant_item(run["id"], index + 1)
        store.append_item_content(item["id"], f"Earlier discussion {index} " * 20)
        store.complete_assistant_item_committed(item["id"])
    complete = ContextBuilder(store).build(run["id"])
    recovered = ContextBuilder(store).build(run["id"], recovery_level=recovery_level)
    for original in complete.model_context:
        if str(original.get("sectionId", "")).startswith("personalization-"):
            assert original in recovered.model_context
    assert "END OF GLOBAL INSTRUCTIONS" in json.dumps(recovered.model_context)
    assert recovered.budget.estimated_input_tokens >= len(preferences.custom_instructions) // 3


@pytest.mark.parametrize("recovery_level", [0, 1, 3, 7, 8])
def test_long_global_instructions_fit_small_models_with_tail_preserved(store, tmp_path, recovery_level):
    from eidos_runtime.model.config import DEFAULT_MODEL_ID, default_profile_snapshot

    instructions = "请使用清楚的中文说明事实。" * 1100 + " END OF GLOBAL INSTRUCTIONS"
    application = PersonalizationApplication(PersonalizationRepository(store.database), lambda value: SensitiveScanner().scan_text(value).text)
    application.save(PersonalizationSaveRequest(expected_revision=0, preferences=PersonalizationPreferences(
        preferred_name="小李", role="开发者", response_language="zh-CN", custom_instructions=instructions,
    )))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = store.create_session(str(workspace))
    profile = default_profile_snapshot(DEFAULT_MODEL_ID).model_copy(update={"context_window_tokens": 16384, "max_output_tokens": 1024})
    run, _ = store.create_run(session["id"], "请回答。", model_id=profile.model_id, model_profile=profile)
    build = ContextBuilder(store).build(run["id"], recovery_level=recovery_level)
    assert build.budget.fits
    sections = {item.get("sectionId"): item for item in build.model_context}
    assert {"personalization-expression", "personalization-profile", "personalization-instructions"} <= sections.keys()
    preview = str(sections["personalization-instructions"]["content"])
    assert "END OF GLOBAL INSTRUCTIONS" in preview
    assert "Content truncated for this request" in preview
    assert store.read_run_resolution_snapshot(run["id"]).personalization.preferences.custom_instructions == instructions


def test_model_receives_frozen_preferences_across_tool_steps(store, tmp_path):
    repository = PersonalizationRepository(store.database)
    repository.save(PersonalizationPreferences(custom_instructions="FROZEN PREFERENCE"), 0)
    run = create_run(store, tmp_path)
    (tmp_path / "workspace" / "sample.txt").write_text("sample")
    model = ScriptedModel([
        ModelResponse(tool_calls=(ModelToolCall("read", "read_file", {"path": "sample.txt"}),)),
        ModelResponse(text="done"),
    ])
    complete = model.complete

    def changed_settings(*args, **kwargs):
        if not model.contexts:
            repository.save(PersonalizationPreferences(custom_instructions="NEW PREFERENCE"), 1)
        return complete(*args, **kwargs)

    model.complete = changed_settings
    RuntimeEngine(store, model, lambda _: None).run(run["id"], threading.Event())
    assert store.read_run(run["id"])["status"] == "succeeded"
    assert len(model.contexts) == 2
    for context in model.contexts:
        text = json.dumps(context)
        assert "FROZEN PREFERENCE" in text and "NEW PREFERENCE" not in text


def test_backup_restores_personalization(store, tmp_path):
    saved = PersonalizationRepository(store.database).save(PersonalizationPreferences(preferred_name="小李", custom_instructions="Use short sentences."), 0)
    archive = tmp_path / "backup.zip"
    backup(store.database.memory, archive)
    restore(archive, tmp_path / "restored")
    reopened = SessionStore(tmp_path / "restored")
    reopened.initialize()
    try:
        assert PersonalizationRepository(reopened.database).read() == saved
    finally:
        reopened.close()


@pytest.mark.parametrize("with_context", [False, True])
def test_finalization_keeps_frozen_user_preferences_out_of_system_prompt(store, tmp_path, with_context):
    from eidos_runtime.runtime.events import RuntimeEvents
    from eidos_runtime.runtime.finalizer import RunFinalizer
    from eidos_runtime.runtime.state_machine import RuntimePhaseTracker
    from eidos_runtime.sandbox.sensitive import SensitiveScanner

    repository = PersonalizationRepository(store.database)
    repository.save(PersonalizationPreferences(custom_instructions="FROZEN USER PREFERENCE"), 0)
    run = create_run(store, tmp_path)
    build = ContextBuilder(store).build(run["id"])
    repository.save(PersonalizationPreferences(custom_instructions="NEW USER PREFERENCE"), 1)
    model = ScriptedModel([ModelResponse(text="done")])
    outcome = RunFinalizer(store, model, RuntimeEvents(lambda _: None), SensitiveScanner(), RuntimePhaseTracker()).finalize(
        run["id"], build.model_context if with_context else (), "context_still_over_budget", threading.Event(),
        instructions=build.instructions if with_context else None,
    )
    assert outcome.failure_reason is None
    assert "FROZEN USER PREFERENCE" not in model.instructions_history[0]
    text = json.dumps(model.contexts[0])
    assert text.count("FROZEN USER PREFERENCE") == 1
    assert "NEW USER PREFERENCE" not in text


@pytest.mark.parametrize("with_instructions", [False, True])
def test_finalization_snapshot_failure_cleans_up_or_uses_existing_instructions(store, tmp_path, monkeypatch, with_instructions):
    from unittest.mock import Mock
    from eidos_runtime.runtime.events import RuntimeEvents
    from eidos_runtime.runtime.finalizer import RunFinalizer
    from eidos_runtime.runtime.state_machine import RuntimePhaseTracker
    from eidos_runtime.sandbox.sensitive import SensitiveScanner

    run = create_run(store, tmp_path)
    build = ContextBuilder(store).build(run["id"])
    timer = Mock()
    monkeypatch.setattr("eidos_runtime.runtime.finalizer.threading.Timer", lambda *_args: timer)
    monkeypatch.setattr(store, "read_run_resolution_snapshot", Mock(side_effect=RuntimeError("fixture snapshot failure")))
    model = ScriptedModel([ModelResponse(text="done")])
    outcome = RunFinalizer(store, model, RuntimeEvents(lambda _: None), SensitiveScanner(), RuntimePhaseTracker()).finalize(
        run["id"], (), "context_still_over_budget", threading.Event(), instructions=build.instructions if with_instructions else None,
    )
    timer.cancel.assert_called_once()
    assert outcome.run["status"] == "stopped"
    assert (outcome.failure_reason is None) == with_instructions
    assert store.read_finalization_attempts(run["id"])[0]["status"] != "running"


@pytest.mark.parametrize("change", [{"tone": "unsupported"}, {"preferredName": 1}, {"role": "x" * 301}, {"customInstructions": "x" * 16385}, {"approvalMode": "full_access"}])
def test_preferences_are_bounded_and_cannot_change_permissions(change):
    with pytest.raises(ValidationError):
        PersonalizationPreferences.model_validate(change)


def test_protocol_fixture_matches_python_models():
    fixture = json.loads((Path(__file__).resolve().parents[2] / "protocol/fixtures/personalization-v1.json").read_text())
    for name, model in [("ReadRequests", PersonalizationReadRequest), ("Requests", PersonalizationSaveRequest), ("Responses", PersonalizationResponse)]:
        for value in fixture[f"valid{name}"]:
            model.model_validate_json(json.dumps(value))
        for value in fixture[f"invalid{name}"]:
            with pytest.raises(ValidationError):
                model.model_validate_json(json.dumps(value))

from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import threading
from types import SimpleNamespace
from uuid import uuid4
import tomllib

import pytest
from pydantic import ValidationError

from eidos_runtime.application.agent_catalog import AgentCatalog
from eidos_runtime.application.collaboration import CollaborationApplication
from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.application.input_context import InputContextApplication
from eidos_runtime.application.runs import RunApplication
from eidos_runtime.context.builder import ContextBuilder
from eidos_runtime.db.custom_agents_migration import migrate_custom_agents
from eidos_runtime.db.repositories.runs import EMPTY_EXTENSION_SNAPSHOT, RunRepository
from eidos_runtime.db.schema import V28_SCHEMA_SQL
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.domain.agents import (
    AgentDefinition,
    AgentModelSelection,
    definition_version,
)
from eidos_runtime.domain.collaboration import CollaborationRejected, SpawnAgent
from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel
from eidos_runtime.model.config import (
    ModelConfigError,
    ModelConfigStore,
    default_profile_snapshot,
)
from eidos_runtime.persistence.agent_catalog import AgentSnapshotRepository
from eidos_runtime.persistence.collaboration import CollaborationRepository
from eidos_runtime.protocol.input_context import InputPrepareRequest, InputReadRequest
from eidos_runtime.protocol.methods import RunStartRequestDto
from eidos_runtime.runtime.engine import RuntimeEngine


@pytest.fixture
def setup(tmp_path: Path):
    store = SessionStore(tmp_path / "data")
    store.initialize()
    assert store.health_state == "ready"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = store.create_session(str(workspace))
    root = store.data_directory / "agents"
    root.mkdir()
    models = ModelConfigStore(store.data_directory)
    models.initialize()
    models.create(
        provider_id="volcengine", model_id="deepseek-v4.1-flash", api_key="fixture-key"
    )
    catalog = AgentCatalog(store.data_directory, models)
    try:
        yield store, session["id"], root, catalog
    finally:
        store.close()


def write_definition(
    root: Path,
    *,
    name: str = "luna-work",
    instructions: str = "Only-child-special-context",
    model: bool = True,
    extra: str = "",
) -> Path:
    path = root / f"{name}.toml"
    path.write_text(
        f'schema_version = 1\nname = "{name}"\ndescription = "实现与验证"\nbase_role = "worker"\ninstructions = {json.dumps(instructions)}\n{extra}\n'
        + (
            '[model]\nprovider_id = "volcengine"\nconfig_id = "deepseek-v4.1-flash"\nreasoning_selection = "high"\n'
            if model
            else ""
        )
    )
    return path


def parent_run(store, session, catalog, **kwargs):
    return RunRepository(store.database).create_run(
        session, "delegate a task", agent_catalog=catalog.scan()[0], **kwargs
    )[0]


def spawn(store, parent, *, role="luna-work", index=0, app=None):
    item = store.create_tool_item(
        parent["id"], 1, index, f"spawn-{index}", "spawn_agent", "{}"
    )
    request = SpawnAgent(
        task_name=f"task-{index}", message="Inspect the assigned problem.", role=role
    )
    app = app or CollaborationApplication(
        store, lambda: None, lambda _run: None, lambda: None
    )
    return app.spawn(parent["id"], item["id"], request), item, request


def test_catalog_model_provider_and_instructions_are_resolved_without_credentials(
    setup,
):
    store, session, root, catalog = setup
    write_definition(root)
    response = catalog.list()
    assert response.agents[0].available
    assert response.agents[0].provider_id == "volcengine"
    assert "fixture-key" not in response.model_dump_json()
    parent = parent_run(store, session, catalog, approval_mode="auto_review")
    child, _, _ = spawn(store, parent)
    profile = store.read_model_profile(child.run_id)
    assert (profile.provider_id, profile.model_id, profile.reasoning_selection) == (
        "volcengine",
        "deepseek-v4.1-flash",
        "high",
    )
    assert child.base_role == "worker"
    assert child.definition_version == definition_version(
        catalog.scan()[0].definitions[0]
    )
    assert store.read_run(child.run_id)["approvalMode"] == "auto_review"
    parent_context = ContextBuilder(store).build(parent["id"])
    child_context = ContextBuilder(store).build(child.run_id)
    assert "Only-child-special-context" not in str(parent_context.model_context)
    assert "Only-child-special-context" in str(child_context.model_context)
    assert (
        next(
            layer
            for layer in child_context.instructions.layers
            if layer.id == "custom-agent"
        ).role
        == "user"
    )


def test_custom_child_runs_through_existing_engine(setup):
    store, session, root, catalog = setup
    write_definition(root, model=False)
    parent = parent_run(store, session, catalog)
    child, _, _ = spawn(store, parent)
    assert store.claim_next_run_committed().value["id"] == child.run_id
    model = ScriptedModel([ModelResponse(text="Evidence reviewed.")])
    app = CollaborationApplication(store, lambda: None, lambda _run: None, lambda: None)
    RuntimeEngine(store, model, lambda _event: None, collaboration=app).run(
        child.run_id, threading.Event()
    )
    assert store.read_run(child.run_id)["status"] == "succeeded"
    assert "Only-child-special-context" in str(model.contexts)
    names = {tool.name for tool in model.tool_definitions_history[0]}
    assert not names & {"spawn_agent", "request_permissions", "request_user_input"}


@pytest.mark.parametrize(
    "override", [None, AgentModelSelection(reasoning_selection="low")]
)
def test_inheritance_and_reasoning_only_use_parent_model(setup, override):
    _store, _session, _root, catalog = setup
    definition = AgentDefinition(
        schema_version=1,
        name="inherited",
        description="Use parent",
        instructions="Keep scope",
        model=override,
    )
    parent = default_profile_snapshot("deepseek-v4.1-flash").model_copy(
        update={"reasoning_selection": "high"}
    )
    resolved = catalog.resolve_model(definition, parent)
    assert resolved.model_id == parent.model_id
    assert resolved.provider_id == parent.provider_id
    assert resolved.reasoning_selection == ("high" if override is None else "low")


def test_provider_mismatch_cannot_silently_fall_back(setup):
    _store, _session, root, catalog = setup
    path = write_definition(root)
    path.write_text(
        path.read_text().replace(
            'provider_id = "volcengine"', 'provider_id = "deepseek"'
        )
    )
    snapshot, response = catalog.scan()
    assert not snapshot.definitions
    assert response.agents[0].available is False
    definition = AgentDefinition.model_validate(tomllib.loads(path.read_text()))
    with pytest.raises(ModelConfigError, match="提供商"):
        catalog.resolve_model(definition, default_profile_snapshot("deepseek-v4-flash"))


@pytest.mark.parametrize(
    "model",
    [
        dict(config_id="example"),
        dict(provider_id="example"),
        {},
        dict(reasoning_selection="ultra"),
    ],
)
def test_invalid_model_selection_is_rejected(model):
    with pytest.raises(ValidationError):
        AgentModelSelection.model_validate(model)


@pytest.mark.parametrize(
    "extra",
    [
        'approval_mode = "full_access"',
        'instructions_file = "agent.md"',
        'enabled = "yes"',
    ],
)
def test_invalid_file_does_not_break_other_agents(setup, extra):
    _store, _session, root, catalog = setup
    write_definition(root, name="valid")
    write_definition(root, name="invalid", extra=extra)
    snapshot, response = catalog.scan()
    assert [value.name for value in snapshot.definitions] == ["valid"]
    assert len(response.agents) == 2
    assert any(not value.available and value.reason for value in response.agents)


def test_duplicate_reserved_and_disabled_names_are_not_callable(setup):
    _store, _session, root, catalog = setup
    path = write_definition(root)
    (root / "duplicate.toml").write_text(path.read_text())
    write_definition(root, name="worker")
    write_definition(root, name="disabled", extra="enabled = false")
    snapshot, response = catalog.scan()
    assert snapshot.definitions == []
    assert sum(value.reason == "定义名称重复。" for value in response.agents) == 2


@pytest.mark.parametrize(
    "kind", ["symlink", "hardlink", "fifo", "oversized", "invalid_utf8"]
)
def test_definition_file_boundaries(setup, tmp_path, kind):
    _store, _session, root, catalog = setup
    destination = root / "unsafe.toml"
    if kind in {"symlink", "hardlink"}:
        source = tmp_path / "source"
        source.write_text("schema_version = 1")
        if kind == "symlink":
            destination.symlink_to(source)
        else:
            os.link(source, destination)
    elif kind == "fifo":
        os.mkfifo(destination)
    elif kind == "oversized":
        destination.write_bytes(b"x" * (64 * 1024 + 1))
    else:
        destination.write_bytes(b"\xff")
    snapshot, response = catalog.scan()
    assert not snapshot.definitions
    assert response.agents[0].available is False


def test_catalog_is_frozen_and_followup_keeps_definition_and_model(setup):
    store, session, root, catalog = setup
    path = write_definition(root)
    parent = parent_run(store, session, catalog, work_mode="plan")
    path.unlink()
    child, item, request = spawn(store, parent)
    assert store.read_run(child.run_id)["workMode"] == "plan"
    assert store.claim_next_run_committed().value["id"] == child.run_id
    store.fail_run(child.run_id, "fixture_finished")
    before = store.read_model_profile(child.run_id)
    followup = store.create_tool_item(
        parent["id"], 2, 1, "follow", "followup_task", "{}"
    )
    resumed = CollaborationRepository(store.database).followup(
        parent["id"], followup["id"], child.id, "Continue investigation"
    )
    assert store.read_model_profile(resumed.run_id) == before
    assert "Only-child-special-context" in str(
        ContextBuilder(store).build(resumed.run_id).model_context
    )
    # A repeated committed spawn remains idempotent even if credentials were removed.
    catalog.models.delete("deepseek-v4.1-flash")
    app = CollaborationApplication(store, lambda: None, lambda _run: None, lambda: None)
    assert app.spawn(parent["id"], item["id"], request).id == child.id


def test_input_reference_preview_freeze_and_historical_reuse(setup):
    store, session, root, catalog = setup
    path = write_definition(root)
    app = InputContextApplication(
        store, SimpleNamespace(), lambda value: value, catalog
    )
    reference = app.prepare(
        InputPrepareRequest(kind="agent", source="luna-work")
    ).reference
    preview = app.read(InputReadRequest(id=reference.id))
    assert "Only-child-special-context" in preview.text
    snapshots = [app.repository.read(reference.id)]
    frozen = catalog.freeze(snapshots)
    parent, _ = RunRepository(store.database).create_run(
        session, "Use selected agent", references=[reference], agent_catalog=frozen
    )
    context = ContextBuilder(store).build(parent["id"])
    assert any(
        value.get("sectionId") == "selected-agents" for value in context.model_context
    )
    assert "Only-child-special-context" not in str(context.model_context)
    write_definition(root, instructions="new constraints")
    with pytest.raises(ValueError, match="变化"):
        catalog.freeze(snapshots)
    assert (
        catalog.freeze(snapshots, previous=frozen, retained_ids={reference.id})
        == frozen
    )
    path.unlink()
    assert app.read(InputReadRequest(id=reference.id)).text == preview.text


def test_run_admission_commits_catalog_with_the_run_and_rejects_stale_selection(setup):
    store, session, root, catalog = setup
    write_definition(root, model=False)
    inputs = InputContextApplication(
        store, SimpleNamespace(), lambda value: value, catalog
    )
    selected = inputs.prepare(
        InputPrepareRequest(kind="agent", source="luna-work")
    ).reference
    environment = SimpleNamespace(
        model_is_configured=lambda: True,
        model_config=lambda _id: None,
        model_for=lambda _id: SimpleNamespace(
            profile_snapshot=default_profile_snapshot("deepseek-v4-flash")
        ),
        extension_snapshot=lambda: EMPTY_EXTENSION_SNAPSHOT,
        validate_input_selections=lambda _values: None,
        schedule_title_generation=lambda *_args: None,
    )
    runtime = SimpleNamespace(prepare_next=lambda: None)
    app = RunApplication(
        store=store,
        runtime=runtime,
        environment=environment,
        scan_text=lambda value: value,
        agent_catalog=catalog,
    )
    request = RunStartRequestDto(
        sessionId=session,
        userInput="Inspect",
        modelId="deepseek-v4-flash",
        references=[selected.id],
        operationId=str(uuid4()),
    )
    outcome = app.start(request)
    run_id = outcome.response.root["id"]
    assert (
        AgentSnapshotRepository(store.database).read(run_id).definitions[0].name
        == "luna-work"
    )
    write_definition(root, model=False, instructions="changed constraints")
    # Replaying admission returns the accepted Run even after a definition edit.
    assert app.start(request).response.root["id"] == run_id
    with pytest.raises(ApplicationError, match="变化"):
        app.start(request.model_copy(update={"operation_id": str(uuid4())}))
    assert (
        store.connection.execute("SELECT COUNT(*) FROM run_agent_catalogs").fetchone()[
            0
        ]
        == 1
    )


def test_catalog_blobs_survive_gc_and_storage_restart(setup):
    store, session, root, catalog = setup
    write_definition(root)
    parent = parent_run(store, session, catalog)
    frozen = AgentSnapshotRepository(store.database).read(parent["id"])
    store.collect_unreferenced_blobs()
    assert AgentSnapshotRepository(store.database).read(parent["id"]) == frozen
    data = store.data_directory
    store.close()
    reopened = SessionStore(data)
    reopened.initialize()
    try:
        assert AgentSnapshotRepository(reopened.database).read(parent["id"]) == frozen
    finally:
        reopened.close()


def test_v28_migration_keeps_builtin_rows_and_rolls_back_orphaned_rows(tmp_path):
    connection = sqlite3.connect(tmp_path / "legacy.sqlite")
    connection.executescript(V28_SCHEMA_SQL)
    for identifier in ("parent", "child"):
        connection.execute(
            "INSERT INTO sessions(id,workspace_root,created_at,updated_at) VALUES (?, '/workspace',1,1)",
            (identifier,),
        )
        connection.execute(
            "INSERT INTO runs(id,session_id,user_input,model_profile_json,status,created_at,updated_at) VALUES (?,?,'task','{}','queued',1,1)",
            (identifier + "-run", identifier),
        )
    connection.execute(
        "INSERT INTO items(id,session_id,run_id,ordinal,model_step_index,kind,status,created_at) VALUES ('spawn','parent','parent-run',1,1,'tool_call','completed',1)"
    )
    connection.execute(
        "INSERT INTO agent_delegations(id,parent_run_id,child_session_id,child_run_id,spawn_item_id,task_name,role,task,created_at) VALUES ('existing','parent-run','child','child-run','spawn','task','worker','old task',1)"
    )
    connection.execute("CREATE INDEX fixture_role_index ON agent_delegations(role)")
    original = connection.execute("SELECT * FROM agent_delegations").fetchone()
    connection.execute("PRAGMA user_version=28")
    connection.commit()
    connection.execute("PRAGMA foreign_keys=ON")
    migrate_custom_agents(connection)
    migrated = connection.execute("SELECT * FROM agent_delegations").fetchone()
    assert migrated[:-2] == original
    assert migrated[-2:] == (None, None)
    assert "fixture_role_index" in {
        row[1] for row in connection.execute("PRAGMA index_list(agent_delegations)")
    }
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 29
    assert "definition_json" in {
        row[1] for row in connection.execute("PRAGMA table_info(agent_delegations)")
    }
    connection.close()

    broken = sqlite3.connect(tmp_path / "broken.sqlite")
    broken.executescript(V28_SCHEMA_SQL)
    broken.execute(
        "INSERT INTO agent_delegations(id,parent_run_id,child_session_id,child_run_id,spawn_item_id,task_name,role,task,created_at) VALUES ('a','r','s','c','i','task','worker','task',1)"
    )
    broken.execute("PRAGMA user_version=28")
    broken.commit()
    with pytest.raises(sqlite3.IntegrityError, match="foreign key"):
        migrate_custom_agents(broken)
    assert broken.execute("PRAGMA user_version").fetchone()[0] == 28
    assert (
        broken.execute(
            "SELECT name FROM sqlite_master WHERE name='run_agent_catalogs'"
        ).fetchone()
        is None
    )
    assert (
        broken.execute("SELECT role FROM agent_delegations").fetchone()[0] == "worker"
    )
    broken.close()


def test_unknown_parent_side_effect_does_not_block_custom_agent_spawn(setup):
    store, session, root, catalog = setup
    write_definition(root, model=False)
    parent = parent_run(store, session, catalog)
    store.connection.execute(
        "UPDATE runs SET reconciliation_required=1 WHERE id=?", (parent["id"],)
    )
    store.connection.commit()
    child, _item, _request = spawn(store, parent)

    assert child.parent_run_id == parent["id"]
    assert store.read_run(parent["id"])["reconciliationRequired"]


def test_shared_custom_agent_fixture_matches_python_contracts():
    from eidos_runtime.domain.agents import AgentCatalogResponse
    from eidos_runtime.domain.collaboration import CollaborationState

    fixture = json.loads(
        (
            Path(__file__).resolve().parents[2] / "protocol/fixtures/custom-agents.json"
        ).read_text()
    )
    assert AgentDefinition.model_validate(fixture["definition"]).name == "luna-work"
    assert (
        AgentCatalogResponse.model_validate(fixture["catalog"]).agents[0].provider_id
        == "volcengine"
    )
    assert (
        CollaborationState.model_validate_json(json.dumps(fixture["state"]))
        .agents[0]
        .role
        == "luna-work"
    )
    with pytest.raises(ValidationError):
        AgentCatalogResponse.model_validate(fixture["invalidCatalog"])


def test_agent_definitions_and_frozen_catalog_survive_backup_restore(setup, tmp_path):
    from eidos_runtime.db.backup import backup, restore

    store, session, root, catalog = setup
    path = write_definition(root)
    parent = parent_run(store, session, catalog)
    frozen = AgentSnapshotRepository(store.database).read(parent["id"])
    archive = tmp_path / "agents.zip"
    backup(store.database.memory, archive)
    destination = tmp_path / "restored"
    restore(archive, destination)
    assert (destination / "agents" / path.name).read_text() == path.read_text()
    restored = SessionStore(destination)
    restored.initialize()
    try:
        assert AgentSnapshotRepository(restored.database).read(parent["id"]) == frozen
        # Credentials remain excluded from backups.
        assert not (destination / "models.json").exists()
    finally:
        restored.close()


def test_custom_children_keep_parent_cancellation_and_late_message_rules(setup):
    from eidos_runtime.runtime.supervisor import RunSupervisor

    store, session, root, catalog = setup
    write_definition(root, model=False)
    parent = parent_run(store, session, catalog)
    child, _, _ = spawn(store, parent)
    assert store.claim_next_run_committed().value["id"] == child.run_id
    supervisor = object.__new__(RunSupervisor)
    supervisor.store = store
    supervisor.lock = threading.RLock()
    supervisor._handles = {}
    supervisor._run_trace_contexts = {}
    supervisor.events = SimpleNamespace(
        publish=lambda *_args, **_kwargs: None, deliver_pending=lambda: None
    )
    supervisor.collaboration = CollaborationApplication(
        store, lambda: None, supervisor.cancel_agent, lambda: None
    )
    supervisor.cancel_run(parent["id"])
    assert store.read_run(child.run_id)["status"] == "canceled"
    with pytest.raises(CollaborationRejected, match="agent_run_not_active"):
        supervisor.collaboration.repository.send(
            child.run_id,
            store.get_user_item(child.run_id)["id"],
            "parent",
            "late result",
        )


def test_run_and_catalog_rollback_together_after_snapshot_insertion(setup, monkeypatch):
    store, session, root, catalog = setup
    write_definition(root, model=False)
    original = AgentSnapshotRepository.insert

    def fail_after_insert(connection, run_id, encoded):
        original(connection, run_id, encoded)
        raise ValueError("fixture snapshot failure")

    monkeypatch.setattr(
        AgentSnapshotRepository, "insert", staticmethod(fail_after_insert)
    )
    with pytest.raises(ValueError, match="snapshot failure"):
        parent_run(store, session, catalog)
    assert store.connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
    assert (
        store.connection.execute("SELECT COUNT(*) FROM run_agent_catalogs").fetchone()[
            0
        ]
        == 0
    )


@pytest.mark.parametrize("role", ["luna-work", "unregistered-agent"])
def test_parent_model_calls_custom_types_through_the_normal_tool_pipeline(setup, role):
    store, session, root, catalog = setup
    write_definition(root)
    parent = parent_run(store, session, catalog)
    model = ScriptedModel(
        [
            ModelResponse(
                tool_calls=(
                    ModelToolCall(
                        "custom-spawn",
                        "spawn_agent",
                        {
                            "role": role,
                            "taskName": "inspect",
                            "message": "Inspect the assigned files.",
                        },
                    ),
                )
            ),
            ModelResponse(text="The delegation has been checked."),
        ]
    )
    app = CollaborationApplication(store, lambda: None, lambda _run: None, lambda: None)
    RuntimeEngine(store, model, lambda _event: None, collaboration=app).run(
        parent["id"], threading.Event()
    )
    items = store.read_session_snapshot(session)["items"]
    call = next(
        item
        for item in items
        if item.get("toolCall", {}).get("toolName") == "spawn_agent"
    )
    result = json.loads(call["toolCall"]["resultJson"])
    if role == "luna-work":
        assert result["outcome"] == "success"
        child = app.repository.state(parent["id"]).agents[0]
        assert child.role == role
        assert store.read_model_profile(child.run_id).provider_id == "volcengine"
        assert store.read_run(parent["id"])["status"] == "waiting_agents"
    else:
        assert result["code"] == "agent_type_unavailable"
        assert app.repository.state(parent["id"]).agents == []
        assert store.read_run(parent["id"])["status"] == "succeeded"
        assert not store.read_run(parent["id"]).get("reconciliationRequired", False)

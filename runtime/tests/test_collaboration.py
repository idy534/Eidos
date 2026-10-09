from __future__ import annotations

import json
import sqlite3
import threading
import sys
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from eidos_runtime.application.collaboration import CollaborationApplication
from eidos_runtime.db.collaboration_migration import migrate_collaboration
from eidos_runtime.db.schema import SCHEMA_VERSION, V15_SCHEMA_SQL
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.domain.collaboration import (
    AgentRole,
    AgentSuspended,
    SpawnAgent,
    WaitAgents,
    CollaborationRejected,
)
from eidos_runtime.persistence.collaboration import CollaborationRepository
from eidos_runtime.sandbox.permissions import BasePermissionProfile
from eidos_runtime.runtime.run_resources import RunResources
from eidos_runtime.context.builder import ContextBuilder
from eidos_runtime.extensions.mcp import McpManager
from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel
from eidos_runtime.runtime.engine import RuntimeEngine
from eidos_runtime.tools.registry import ToolProvenance, ToolRegistryEntry, ToolSpec


def _parent(tmp_path: Path) -> tuple[SessionStore, dict[str, object], dict[str, object]]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    session = store.create_session(str(workspace))
    run, _ = store.create_run(str(session["id"]), "coordinate a read-only investigation")
    return store, session, run


def _spawn(
    store: SessionStore,
    parent: dict[str, object],
    index: int,
    *,
    role: AgentRole = "explorer",
) -> tuple[CollaborationRepository, object, str]:
    repository = CollaborationRepository(store.database)
    item = store.create_tool_item(
        str(parent["id"]), 1, index, f"spawn-call-{index}", "spawn_agent", "{}"
    )
    request = SpawnAgent(
        task_name=f"inspect-{index}",
        message=f"Read the relevant files and report evidence for task {index}.",
        role=role,
    )
    return repository, repository.spawn(str(parent["id"]), str(item["id"]), request), str(item["id"])


def test_v15_migration_creates_collaboration_tables_and_rolls_back_on_fk_failure(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.sqlite"
    connection = sqlite3.connect(database)
    connection.executescript(V15_SCHEMA_SQL)
    connection.execute(
        "INSERT INTO runs (id, session_id, user_input, model_profile_json, status, created_at, updated_at) "
        "VALUES ('orphan', 'missing-session', 'task', '{}', 'queued', 1, 1)"
    )
    connection.execute("PRAGMA user_version = 15")
    connection.commit()

    with pytest.raises(sqlite3.IntegrityError, match="foreign key violation"):
        migrate_collaboration(connection)

    assert connection.execute("PRAGMA user_version").fetchone()[0] == 15
    assert connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_waits'"
    ).fetchone() is None
    connection.close()

    clean = SessionStore(tmp_path / "clean-data")
    clean.initialize()
    try:
        assert SCHEMA_VERSION == 21
        assert clean.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_waits'"
        ).fetchone() is not None
    finally:
        clean.close()


def test_spawn_is_idempotent_and_keeps_child_session_out_of_normal_listing(tmp_path: Path) -> None:
    store, session, parent = _parent(tmp_path)
    try:
        repository, first, item_id = _spawn(store, parent, 0)
        second = repository.spawn(
            str(parent["id"]),
            item_id,
            SpawnAgent(
                task_name=first.task_name,
                message=first.task,
                role="explorer",
            ),
        )

        assert second.id == first.id
        assert second.run_id == first.run_id
        assert store.read_run(first.run_id)["status"] == "queued"
        assert store.read_session(first.session_id) is not None
        assert all(item["id"] != first.session_id for item in store.list_sessions()["items"])
        assert store.connection.execute(
            "SELECT COUNT(*) FROM agent_delegations WHERE parent_run_id=?",
            (parent["id"],),
        ).fetchone()[0] == 1
    finally:
        store.close()


def test_busy_followup_is_a_message_and_never_restarts_after_completion(tmp_path):
    store, _, parent = _parent(tmp_path)
    try:
        repository, child, _ = _spawn(store, parent, 0)
        item = store.create_tool_item(parent['id'], 2, 0, 'followup-call', 'followup_task', '{}')
        first = repository.followup(parent['id'], item['id'], child.id, 'Include the new constraint.')
        assert first.run_id == child.run_id
        assert repository.state(child.run_id).messages[-1].content == 'Include the new constraint.'
        assert store.claim_next_run()['id'] == child.run_id
        store.fail_run(child.run_id, 'fixture_done')
        repeated = repository.followup(parent['id'], item['id'], child.id, 'Include the new constraint.')
        assert repeated.run_id == child.run_id
        assert store.connection.execute('SELECT COUNT(*) FROM runs WHERE session_id=?', (child.session_id,)).fetchone()[0] == 1
    finally:
        store.close()


def test_omitted_role_creates_default_and_custom_roles_are_rejected(tmp_path: Path) -> None:
    from pydantic import ValidationError

    store, _session, parent = _parent(tmp_path)
    try:
        repository = CollaborationRepository(store.database)
        item = store.create_tool_item(parent['id'], 1, 0, 'spawn-default', 'spawn_agent', '{}')
        child = repository.spawn(parent['id'], item['id'], SpawnAgent(task_name='general', message='Inspect the task'))
        assert child.role == 'default'
        assert repository.child_role_for_run(child.run_id) == 'default'
        with pytest.raises(ValidationError):
            SpawnAgent(task_name='custom', message='Inspect the task', role='reviewer')
    finally:
        store.close()


def test_shared_collaboration_fixture_accepts_builtin_roles_and_omitted_default() -> None:
    from eidos_runtime.domain.collaboration import CollaborationState

    fixture = json.loads((Path(__file__).resolve().parents[2] / 'protocol/fixtures/collaboration.json').read_text())
    state = CollaborationState.model_validate_json(json.dumps(fixture['result']))
    assert [agent.role for agent in state.agents] == ['default', 'explorer', 'worker']
    assert SpawnAgent.model_validate(fixture['spawnArguments']).role == 'default'


@pytest.mark.parametrize('role', ['default', 'explorer', 'worker'])
def test_child_role_instructions_are_built_from_persisted_role(tmp_path: Path, role: AgentRole) -> None:
    store, _session, parent = _parent(tmp_path)
    try:
        repository, child, _item = _spawn(store, parent, 0, role=role)
        assert not any(layer.id == 'agent-role' for layer in ContextBuilder(store).build(parent['id']).instructions.layers)
        child_context = ContextBuilder(store).build(child.run_id)
        layer = next(layer for layer in child_context.instructions.layers if layer.id == 'agent-role')
        assert layer.source == f'eidos:agent-role:{role}'
        assert layer.role == 'developer'
        assert f'Your built-in role is {role}.' in layer.content
        assert 'send_message' in layer.content
        assert repository.child_role_for_run(child.run_id) == role
    finally:
        store.close()


@pytest.mark.parametrize('role', ['default', 'explorer', 'worker'])
@pytest.mark.parametrize('mode', ['manual', 'auto_review', 'full_access'])
@pytest.mark.platform
@pytest.mark.skipif(sys.platform != 'darwin', reason='Native Shell permissions require macOS')
def test_child_runs_shell_through_normal_runtime_and_commits_result(tmp_path: Path, role: AgentRole, mode: str) -> None:
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    (workspace / 'source.py').write_text('first\nsecond\n')
    store = SessionStore(tmp_path / 'data')
    store.initialize()
    try:
        session = store.create_session(str(workspace))
        parent, _ = store.create_run(session['id'], 'delegate statistics', approval_mode=mode)
        repository, child, _item = _spawn(store, parent, 0, role=role)
        assert store.claim_next_run()['id'] == child.run_id
        model = ScriptedModel([
            ModelResponse(tool_calls=(ModelToolCall('child-patch', 'apply_patch', {'patch': '*** Begin Patch\n*** Add File: child-output.txt\n+verified\n*** End Patch'}),)),
            ModelResponse(tool_calls=(ModelToolCall('child-shell', 'run_shell', {'command': 'wc -l < source.py', 'yieldTimeMs': 30000}),)),
            ModelResponse(text='The command counted two lines.'),
        ])
        app = CollaborationApplication(store, lambda: None, lambda _run: None, lambda: None)
        RuntimeEngine(store, model, lambda _event: None, shell_available=True, collaboration=app).run(child.run_id, threading.Event())
        assert store.read_run(child.run_id)['status'] == 'succeeded'
        assert (workspace / 'child-output.txt').read_text() == 'verified\n'
        items = store.read_session_snapshot(child.session_id)['items']
        shell = next(item for item in items if item.get('toolCall', {}).get('toolName') == 'run_shell')
        result = json.loads(shell['toolCall']['resultJson'])
        assert result['outcome'] == 'success', result
        assert result['data']['stdout'].strip() == '2'
        assert store.connection.execute(
            'SELECT d.status FROM durable_intents d JOIN tool_calls t ON t.id=d.tool_call_id WHERE t.item_id=?',
            (shell['id'],),
        ).fetchone()[0] == 'completed'
        assert repository.state(parent['id']).agents[0].role == role
    finally:
        store.close()


def test_agent_messages_keep_recent_context_and_archive_beyond_sixteen(tmp_path):
    store, _, parent = _parent(tmp_path)
    try:
        repository, child, _ = _spawn(store, parent, 0)
        for index in range(20):
            repository.send(parent['id'], f'message-{index}', child.id, f'Update {index}')
        assert [message.content for message in repository.state(child.run_id).messages] == [f'Update {index}' for index in range(4, 20)]
        assert store.connection.execute('SELECT COUNT(*) FROM agent_messages').fetchone()[0] == 20
    finally:
        store.close()


@pytest.mark.parametrize("role", ["default", "explorer", "worker"])
def test_child_followup_checks_unknown_effects_when_creating_a_new_run(tmp_path, role):
    store, _, parent = _parent(tmp_path)
    try:
        repository = CollaborationRepository(store.database)
        spawn = store.create_tool_item(parent['id'], 1, 0, 'spawn-worker', 'spawn_agent', '{}')
        child = repository.spawn(parent['id'], spawn['id'], SpawnAgent(task_name='worker', message='Work on the task', role=role))
        assert store.claim_next_run()['id'] == child.run_id
        store.connection.execute('UPDATE runs SET reconciliation_required=1 WHERE id=?', (parent['id'],))
        store.connection.commit()
        message = store.create_tool_item(parent['id'], 2, 0, 'busy-worker', 'followup_task', '{}')
        assert repository.followup(parent['id'], message['id'], child.id, 'Review evidence').run_id == child.run_id
        store.fail_run(child.run_id, 'fixture_done')
        item = store.create_tool_item(parent['id'], 3, 0, 'idle-worker', 'followup_task', '{}')
        with pytest.raises(CollaborationRejected, match='reconciliation_required'):
            repository.followup(parent['id'], item['id'], child.id, 'Start new changes')
        assert store.connection.execute('SELECT COUNT(*) FROM runs WHERE session_id=?', (child.session_id,)).fetchone()[0] == 1
    finally:
        store.close()


@pytest.mark.parametrize('role', ['default', 'explorer', 'worker'])
def test_spawn_transaction_rejects_parent_unknown_effects(tmp_path: Path, role: AgentRole) -> None:
    store, _session, parent = _parent(tmp_path)
    try:
        store.connection.execute('UPDATE runs SET reconciliation_required=1 WHERE id=?', (parent['id'],))
        store.connection.commit()
        with pytest.raises(CollaborationRejected, match='reconciliation_required'):
            _spawn(store, parent, 0, role=role)
        assert CollaborationRepository(store.database).state(parent['id']).agents == []
    finally:
        store.close()


def test_eight_child_run_limit_is_per_parent_and_does_not_block_other_runs(tmp_path: Path) -> None:
    store, _session, parent = _parent(tmp_path)
    try:
        repository = CollaborationRepository(store.database)
        children = [_spawn(store, parent, index)[1] for index in range(9)]
        claimed = [store.claim_next_run() for _ in range(8)]

        assert {str(run["id"]) for run in claimed if run is not None} == {
            child.run_id for child in children[:8]
        }
        assert store.claim_next_run() is None
        assert set(repository.child_runs(str(parent["id"]))) == {
            child.run_id for child in children
        }

        # A full delegation group must not consume another parent's capacity.
        session = store.create_session(str(_session["workspaceRoot"]))
        other, _ = store.enqueue_run(str(session["id"]), "independent task")
        assert store.claim_next_run()["id"] == other["id"]
        peers = [_spawn(store, other, index)[1] for index in range(9)]
        claimed = [store.claim_next_run() for _ in range(8)]
        assert {run["id"] for run in claimed if run is not None} == {
            child.run_id for child in peers[:8]
        }
        assert store.claim_next_run() is None

        # Freeing one local slot admits only that parent's queued child.
        store.fail_run(children[0].run_id, "test_child_finished")
        assert store.claim_next_run()["id"] == children[8].run_id
        assert store.read_run(peers[8].run_id)["status"] == "queued"
    finally:
        store.close()


@pytest.mark.parametrize("role", ["default", "explorer", "worker"])
def test_child_resources_inherit_workspace_shell_and_extension_tools(
    tmp_path: Path, role: AgentRole,
) -> None:
    store, _session, parent = _parent(tmp_path)
    try:
        _repository, child, _item_id = _spawn(store, parent, 0, role=role)
        application = CollaborationApplication(
            store,
            schedule=lambda: None,
            cancel=lambda _run_id: None,
            publish=lambda: None,
        )
        external = ToolRegistryEntry(
            ToolSpec(name="mcp__fixture__lookup", description="Look up configured evidence",
                     sideEffect="external", approvalRequired=True, timeoutSeconds=5,
                     visibility="deferred", inputSchema={"type": "object", "properties": {}, "additionalProperties": False},
                     resultSchema={"type": "object", "properties": {}, "additionalProperties": False}),
            ToolProvenance(kind="mcp", sourceId="fixture", sourceVersion="1", contentHash="a" * 64, pluginId="fixture", serverId="fixture"),
            Mock(),
        )
        with patch.object(McpManager, "start", return_value=(external,)) as start, patch.object(McpManager, "refresh_if_changed", return_value=None) as refresh, RunResources(
            store,
            child.run_id,
            store.read_run(child.run_id)["extensionSnapshot"],
            collaboration=application,
            supports_images=True,
        ) as resources:
            names = {entry.spec.name for entry in resources.registry.entries}
            resources.refresh()
            refresh.assert_called_once()
            snapshot = resources.dispatcher.snapshot()
            start.assert_called_once()

        assert child.role == role
        assert {"read_file", "list_files", "search_text", "view_image", "skill_read", "tool_search",
                "send_message", "run_shell", "write_stdin", "read_tool_output", "apply_patch", "declare_outputs", "mcp__fixture__lookup"} <= names
        assert "mcp__fixture__lookup" in snapshot.deferred_names
        assert not names & {"request_permissions", "request_user_input", "spawn_agent", "followup_task", "wait_agents", "list_agents", "stop_agent"}
    finally:
        store.close()


def test_worker_inherits_parent_approval_and_extension_snapshot(tmp_path: Path) -> None:
    store, _session, parent = _parent(tmp_path)
    try:
        repository = CollaborationRepository(store.database)
        item = store.create_tool_item(str(parent["id"]), 1, 42, "worker-call", "spawn_agent", "{}")
        worker = repository.spawn(
            str(parent["id"]), str(item["id"]),
            SpawnAgent(task_name="worker", message="Run the tests and fix the failure", role="worker"),
        )
        child_run = store.read_run(worker.run_id)
        assert worker.role == "worker"
        assert child_run.get("approvalMode", "manual") == store.read_run(str(parent["id"])).get("approvalMode", "manual")
        assert child_run["extensionSnapshot"] == store.read_run(str(parent["id"]))["extensionSnapshot"]
        application = CollaborationApplication(store, lambda: None, lambda _run: None, lambda: None)
        with RunResources(store, worker.run_id, child_run["extensionSnapshot"], collaboration=application) as resources:
            names = {entry.spec.name for entry in resources.registry.entries}
        assert {"run_shell", "apply_patch", "declare_outputs", "send_message"} <= names
        assert "spawn_agent" not in names
    finally:
        store.close()


@pytest.mark.parametrize("mode", ["manual", "auto_review", "full_access"])
@pytest.mark.parametrize("role", ["default", "explorer", "worker"])
def test_child_permission_snapshot_follows_parent_run(tmp_path: Path, mode: str, role: AgentRole) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    try:
        session = store.create_session(str(workspace))
        parent, _ = store.create_run(str(session["id"]), "delegate", approval_mode=mode)
        child = _spawn(store, parent, 0, role=role)[1]
        run = store.read_run(child.run_id)
        resolution = store.read_run_resolution_snapshot(child.run_id)
        policy = json.loads(resolution.sandbox_policy_json)
        profile = BasePermissionProfile.model_validate_json(resolution.permission_profile_json)
        assert run.get("approvalMode", "manual") == mode
        assert policy["approvalMode"] == mode
        assert profile.full_access is (mode == "full_access")
    finally:
        store.close()


@pytest.mark.parametrize("role", ["default", "explorer", "worker"])
def test_child_followup_keeps_role_and_parent_permission_mode(tmp_path: Path, role: AgentRole) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    try:
        session = store.create_session(str(workspace))
        parent, _ = store.create_run(str(session["id"]), "delegate", approval_mode="auto_review")
        repository = CollaborationRepository(store.database)
        spawn_item = store.create_tool_item(str(parent["id"]), 1, 5, "spawn-worker", "spawn_agent", "{}")
        worker = repository.spawn(str(parent["id"]), str(spawn_item["id"]),
            SpawnAgent(task_name="worker", message="Inspect and fix the issue", role=role))
        assert store.claim_next_run()["id"] == worker.run_id
        store.fail_run(worker.run_id, "done")
        followup_item = store.create_tool_item(str(parent["id"]), 2, 6, "followup-worker", "followup_task", "{}")
        resumed = repository.followup(str(parent["id"]), str(followup_item["id"]), worker.id, "Check the fix")
        assert resumed.role == role
        assert resumed.session_id == worker.session_id
        assert resumed.run_id != worker.run_id
        assert store.read_run(resumed.run_id)["approvalMode"] == "auto_review"
        role_layer = next(layer for layer in ContextBuilder(store).build(resumed.run_id).instructions.layers if layer.id == 'agent-role')
        assert role_layer.source == f'eidos:agent-role:{role}'
    finally:
        store.close()


def test_child_uses_parent_approved_run_grant_only_while_parent_is_active(tmp_path: Path) -> None:
    store, _session, parent = _parent(tmp_path)
    try:
        item = store.create_tool_item(str(parent["id"]), 1, 1, "permission-call", "request_permissions", "{}")
        with store.database.transaction() as connection:
            tool_call = connection.execute("SELECT id FROM tool_calls WHERE item_id=?", (item["id"],)).fetchone()
            connection.execute(
                """INSERT INTO approvals (id,tool_call_id,run_id,item_id,status,request_hash,request_json,created_at)
                   VALUES (?,?,?,?, 'approved',?,?,?)""",
                ("approved-parent-grant", tool_call["id"], parent["id"], item["id"], "hash",
                 json.dumps({"kind": "permission_request", "grantScope": "run",
                             "permissions": {"network": {"enabled": True}}}), 1),
            )
        child = _spawn(store, parent, 2)[1]
        assert store.run_permission_grants(child.run_id).network.enabled is True
        store.fail_run(str(parent["id"]), "done")
        assert store.run_permission_grants(child.run_id).network is None
    finally:
        store.close()


def test_wait_wakes_after_child_completion_and_after_timeout(tmp_path: Path) -> None:
    store, _session, parent = _parent(tmp_path)
    try:
        repository, child, _spawn_item_id = _spawn(store, parent, 0)
        wait_item = store.create_tool_item(
            str(parent["id"]), 1, 10, "wait-call", "wait_agents", "{}"
        )
        with pytest.raises(AgentSuspended):
            CollaborationApplication(
                store, lambda: None, lambda _run_id: None, lambda: None
            ).wait(
                str(parent["id"]), str(wait_item["id"]),
                WaitAgents(agent_ids=[child.id], timeout_ms=1000),
            )
        assert store.read_run(str(parent["id"]))["status"] == "waiting_agents"
        assert repository.wait_record(str(parent["id"])).status == "pending"

        assert store.claim_next_run()["id"] == child.run_id
        store.fail_run(child.run_id, "child_done")
        repository.wake()
        assert store.read_run(str(parent["id"]))["status"] == "queued"
        assert repository.wait_record(str(parent["id"])).status == "ready"
        repository.clear_wait(str(parent["id"]))
        assert repository.wait_record(str(parent["id"])) is None

        # A second wait uses a new tool item and can finish by its deadline.
        assert store.claim_next_run()["id"] == parent["id"]
        _repository, timeout_child, _spawn_item_id = _spawn(store, parent, 1)
        wait_item = store.create_tool_item(
            str(parent["id"]), 1, 11, "wait-call-timeout", "wait_agents", "{}"
        )
        with pytest.raises(AgentSuspended):
            CollaborationApplication(
                store, lambda: None, lambda _run_id: None, lambda: None
            ).wait(
                str(parent["id"]), str(wait_item["id"]),
                WaitAgents(agent_ids=[timeout_child.id], timeout_ms=1000),
            )
        store.connection.execute(
            "UPDATE agent_waits SET deadline_at=0 WHERE run_id=?", (parent["id"],)
        )
        store.connection.commit()
        repository.wake()
        assert store.read_run(str(parent["id"]))["status"] == "queued"
        assert repository.wait_record(str(parent["id"])).status == "ready"
    finally:
        store.close()


@pytest.mark.parametrize('role', ['default', 'explorer', 'worker'])
def test_parent_cancel_stops_active_children_and_rejects_late_child_message(
    tmp_path: Path, role: AgentRole,
) -> None:
    store, session, parent = _parent(tmp_path)
    try:
        repository, child, _item_id = _spawn(store, parent, 0, role=role)
        assert store.claim_next_run()["id"] == child.run_id

        class Events:
            def publish(self, *_args: object, **_kwargs: object) -> None:
                return None

            def deliver_pending(self) -> None:
                return None

        from eidos_runtime.runtime.supervisor import RunSupervisor

        supervisor = object.__new__(RunSupervisor)
        supervisor.store = store
        supervisor.lock = threading.RLock()
        supervisor._handles = {}
        supervisor._run_trace_contexts = {}
        supervisor.events = Events()
        supervisor.collaboration = CollaborationApplication(
            store, lambda: None, supervisor.cancel_agent, supervisor.events.deliver_pending
        )
        supervisor.cancel_run(str(parent["id"]))

        assert store.read_run(str(parent["id"]))["status"] == "canceled"
        assert store.read_run(child.run_id)["status"] == "canceled"
        with pytest.raises(ValueError, match="agent_run_not_active"):
            repository.send(child.run_id, str(store.get_user_item(child.run_id)["id"]), "parent", "late")
    finally:
        store.close()


def test_parent_session_delete_removes_managed_child_facts_only(tmp_path: Path) -> None:
    store, session, parent = _parent(tmp_path)
    workspace = Path(str(session["workspaceRoot"]))
    try:
        _repository, child, _item_id = _spawn(store, parent, 0)
        store.cancel_run(child.run_id)
        store.cancel_run(str(parent["id"]))
        store.delete_session(str(session["id"]))

        assert store.read_session(str(session["id"])) is None
        assert store.read_session(child.session_id) is None
        assert workspace.exists()
    finally:
        store.close()


def test_failed_parent_cancels_orphan_without_agent_wait(tmp_path: Path) -> None:
    from eidos_runtime.runtime.supervisor import RunSupervisor

    store, _session, parent = _parent(tmp_path)
    try:
        repository, child, _item_id = _spawn(store, parent, 0)
        supervisor = RunSupervisor(
            store, model_for=lambda _run_id: None, notify=lambda _event: None,
            scan_feedback=lambda _text: "", can_run=lambda: False,
            shell_available=lambda: False, sensitive=lambda: None,
        )
        store.fail_run(str(parent["id"]), "test_failure")

        assert repository.orphan_runs() == (child.run_id,)
        assert not repository.has_pending_waits()
        supervisor.schedule_next()

        assert store.read_run(child.run_id)["status"] == "canceled"
        assert repository.orphan_runs() == ()
    finally:
        store.close()


def test_failed_parent_requests_cancel_for_running_child(tmp_path: Path) -> None:
    from eidos_runtime.runtime.supervisor import RunHandle, RunSupervisor, RunWorkerState

    store, _session, parent = _parent(tmp_path)
    try:
        _repository, child, _item_id = _spawn(store, parent, 0)
        assert store.claim_next_run()["id"] == child.run_id
        supervisor = RunSupervisor(
            store, model_for=lambda _run_id: None, notify=lambda _event: None,
            scan_feedback=lambda _text: "", can_run=lambda: False,
            shell_available=lambda: False, sensitive=lambda: None,
        )
        cancellation = threading.Event()
        supervisor._handles[child.run_id] = RunHandle(
            child.run_id, threading.Thread(), cancellation, RunWorkerState.RUNNING
        )
        store.fail_run(str(parent["id"]), "test_failure")

        supervisor.schedule_next()

        assert cancellation.is_set()
        assert store.read_run(child.run_id)["cancelRequestedAt"] is not None
    finally:
        store.close()

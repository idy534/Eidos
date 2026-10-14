from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from eidos_runtime.db.database import Database
from eidos_runtime.db.errors import OperationConflictError, ResourceNotFoundError
from eidos_runtime.db.schema import SCHEMA_VERSION
from eidos_runtime.db.sidebar_migration import migrate_sidebar_pins
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.domain.sidebar import ProjectSidebarTarget, SessionSidebarTarget
from eidos_runtime.persistence.sidebar import SidebarPinLimitReachedError, SidebarRepository
from eidos_runtime.persistence.worktrees import ProjectWorktreeRepository
from eidos_runtime.protocol.server import RuntimeServer
from eidos_runtime.protocol.sidebar import SidebarPinsResponse, SidebarSetPinnedRequest
from eidos_runtime.runtime.events import RuntimeEvents


@pytest.fixture
def store(tmp_path: Path):
    value = SessionStore(tmp_path / "data")
    value.initialize()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = ProjectWorktreeRepository(value.database).get_or_create_project(str(workspace))
    created = value.typed_runtime_repository().create_session(str(workspace), project_id=project.id)
    session = {**value.read_session(created.value.id), "project": {"id": project.id}}
    yield value, session
    value.close()


def _set(repository, target, pinned=True, operation_id=None):
    return repository.set_pinned(target, pinned, operation_id=operation_id or str(uuid4()))


def test_mixed_order_idempotency_and_independent_session_metadata(store):
    value, session = store
    repo = SidebarRepository(value.database)
    project = ProjectSidebarTarget(kind="project", id=session["project"]["id"])
    target = SessionSidebarTarget(kind="session", id=session["id"])
    _set(repo, project)
    operation = str(uuid4())
    first = _set(repo, target, operation_id=operation)
    assert first.value.items == (project, target)
    assert value.read_session(session["id"])["updatedAt"] == session["updatedAt"]
    assert _set(repo, target, operation_id=operation).events == ()
    assert _set(repo, target).events == ()
    with pytest.raises(OperationConflictError):
        _set(repo, target, False, operation)
    _set(repo, project, False)
    _set(repo, project)
    assert repo.list_pins().items == (target, project)
    value.rename_session(session["id"], "new title")
    assert repo.list_pins().items == (target, project)


def test_pins_and_pending_notification_survive_restart_and_outbox_redelivery(store):
    value, session = store
    target = SessionSidebarTarget(kind="session", id=session["id"])
    mutation = _set(SidebarRepository(value.database), target)
    data = value.database.data_directory
    value.close()
    reopened = SessionStore(data)
    try:
        reopened.initialize()
        assert SidebarRepository(reopened.database).list_pins() == mutation.value
        notices = []
        events = RuntimeEvents(notices.append, store=reopened)
        events.deliver_pending()
        pins_notices = [notice for notice in notices if notice["method"] == "sidebar/pinsChanged"]
        assert pins_notices == [{"jsonrpc": "2.0", "method": "sidebar/pinsChanged",
                                "params": {"eventId": mutation.event_ids[0]}}]
        events.deliver_pending()
        assert [notice for notice in notices if notice["method"] == "sidebar/pinsChanged"] == pins_notices
    finally:
        reopened.close()


def test_pin_transaction_rolls_back_record_event_and_operation(store, monkeypatch):
    value, session = store
    repo = SidebarRepository(value.database)
    before = repo.list_pins()

    def fail(*args, **kwargs):
        raise RuntimeError("event write failed")

    monkeypatch.setattr("eidos_runtime.persistence.sidebar.append_event", fail)
    operation = str(uuid4())
    with pytest.raises(RuntimeError, match="event write failed"):
        _set(repo, SessionSidebarTarget(kind="session", id=session["id"]), operation_id=operation)
    assert repo.list_pins() == before
    assert value.connection.execute("SELECT 1 FROM operations WHERE id=?", (operation,)).fetchone() is None


def test_deleting_session_and_project_cleans_pins_in_the_same_transaction(store):
    value, session = store
    repo = SidebarRepository(value.database)
    project = ProjectSidebarTarget(kind="project", id=session["project"]["id"])
    _set(repo, project)
    _set(repo, SessionSidebarTarget(kind="session", id=session["id"]))
    deleted = value.typed_runtime_repository().delete_session(session["id"])
    assert len(deleted.events) == 1
    assert repo.list_pins().items == (project,)
    deleted_project = ProjectWorktreeRepository(value.database).delete_project(project.id)
    assert len(deleted_project.events) == 1
    assert repo.list_pins().items == ()
    assert value.connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_pin_unknown_target_is_rejected_and_limit_does_not_block_unpin(store, monkeypatch):
    value, session = store
    repo = SidebarRepository(value.database)
    with pytest.raises(ResourceNotFoundError):
        _set(repo, SessionSidebarTarget(kind="session", id=str(uuid4())))
    monkeypatch.setattr("eidos_runtime.persistence.sidebar.MAX_SIDEBAR_PINS", 1)
    target = SessionSidebarTarget(kind="session", id=session["id"])
    _set(repo, target)
    with pytest.raises(SidebarPinLimitReachedError):
        _set(repo, ProjectSidebarTarget(kind="project", id=session["project"]["id"]))
    _set(repo, target, False)
    assert repo.list_pins().items == ()


def test_pin_does_not_change_cancel_or_run_state(store):
    value, session = store
    run, _ = value.create_run(session["id"], "cancel this run")
    value.cancel_run(run["id"])
    before = value.read_run(run["id"])
    _set(SidebarRepository(value.database), SessionSidebarTarget(kind="session", id=session["id"]))
    assert value.read_run(run["id"]) == before


def test_delete_event_failure_keeps_session_pin_and_session_facts(store, monkeypatch):
    value, session = store
    repo = SidebarRepository(value.database)
    _set(repo, SessionSidebarTarget(kind="session", id=session["id"]))
    before = repo.list_pins()

    def fail(*args, **kwargs):
        raise RuntimeError("delete event failed")

    monkeypatch.setattr("eidos_runtime.persistence.sidebar.append_event", fail)
    with pytest.raises(RuntimeError, match="delete event failed"):
        value.typed_runtime_repository().delete_session(session["id"])
    assert value.read_session(session["id"]) is not None
    assert repo.list_pins() == before


def test_internal_child_session_is_not_a_sidebar_target(store):
    value, session = store
    parent_run, parent_item = value.create_run(session["id"], "parent")
    child = value.create_session(session["workspaceRoot"])
    child_run, _ = value.create_run(child["id"], "child")
    value.connection.execute(
        "INSERT INTO agent_delegations "
        "(id, parent_run_id, child_session_id, child_run_id, spawn_item_id, task_name, role, task, created_at) "
        "VALUES (?, ?, ?, ?, ?, 'child', 'explorer', 'read files', 1)",
        (str(uuid4()), parent_run["id"], child["id"], child_run["id"], parent_item["id"]),
    )
    with pytest.raises(ResourceNotFoundError):
        _set(SidebarRepository(value.database), SessionSidebarTarget(kind="session", id=child["id"]))


def test_v24_upgrade_preserves_sessions_and_starts_with_empty_pins(
    store, downgrade_session_fork_schema,
):
    value, session = store
    data = value.database.data_directory
    downgrade_session_fork_schema(value.connection)
    value.connection.execute("DROP TABLE sidebar_pins")
    value.connection.execute("DROP INDEX sidebar_pins_event_revision")
    value.connection.execute("PRAGMA user_version = 24")
    value.close()
    database = Database(data)
    try:
        database.initialize()
        assert database.health_state == "ready"
        assert database.connection().execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert database.connection().execute("SELECT id FROM sessions").fetchone()[0] == session["id"]
        assert SidebarRepository(database).list_pins().items == ()
    finally:
        database.close()


def test_migration_failure_rolls_back_schema_and_version(monkeypatch):
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA user_version = 24")
    monkeypatch.setattr("eidos_runtime.db.sidebar_migration.SIDEBAR_PINS_SCHEMA_SQL",
                        "CREATE TABLE sidebar_pins(id INTEGER);\nINSERT INTO missing_table VALUES (1);\n")
    with pytest.raises(sqlite3.OperationalError):
        migrate_sidebar_pins(connection)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 24
    assert connection.execute("SELECT 1 FROM sqlite_master WHERE name='sidebar_pins'").fetchone() is None
    assert not connection.in_transaction
    connection.close()


def test_sidebar_rpc_and_shared_contract_fixture(tmp_path):
    fixture = json.loads((Path(__file__).parents[2] / "protocol/fixtures/sidebar-v1.json").read_text())
    for request in fixture["validRequests"]:
        assert SidebarSetPinnedRequest.model_validate(request)
    for request in fixture["invalidRequests"]:
        with pytest.raises(ValidationError):
            SidebarSetPinnedRequest.model_validate(request)
    for response in fixture["validResponses"]:
        assert SidebarPinsResponse.model_validate(response)
    for response in fixture["invalidResponses"]:
        with pytest.raises(ValidationError):
            SidebarPinsResponse.model_validate(response)
    output = io.StringIO()
    server = RuntimeServer(output, data_directory=tmp_path / "data")
    server.store.initialize()
    server.initialized = True
    try:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        session = server.store.create_session(str(workspace))
        request = {"target": {"kind": "session", "id": session["id"]},
                   "pinned": True, "operationId": str(uuid4())}
        server.handle({"jsonrpc": "2.0", "id": "client-pin", "method": "sidebar/setPinned", "params": request})
        messages = [json.loads(line) for line in output.getvalue().splitlines()]
        result = next(message["result"] for message in messages if message.get("id") == "client-pin")
        assert result["items"] == [request["target"]]
        assert any(message.get("method") == "sidebar/pinsChanged" for message in messages)
        output.seek(0)
        output.truncate()
        server.handle({"jsonrpc": "2.0", "id": "client-draft", "method": "sidebar/setPinned",
                       "params": {**request, "target": {"kind": "session", "id": f"draft-{uuid4()}"}}})
        assert json.loads(output.getvalue())["error"]["code"] == -32602
    finally:
        server.close()

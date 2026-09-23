from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from eidos_runtime.application.collaboration import CollaborationApplication
from eidos_runtime.db.collaboration_migration import migrate_collaboration
from eidos_runtime.db.schema import SCHEMA_VERSION, V15_SCHEMA_SQL
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.domain.collaboration import (
    AgentSuspended,
    READ_ONLY_TOOLS,
    SpawnAgent,
    WaitAgents,
)
from eidos_runtime.persistence.collaboration import CollaborationRepository
from eidos_runtime.runtime.run_resources import RunResources


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
) -> tuple[CollaborationRepository, object, str]:
    repository = CollaborationRepository(store.database)
    item = store.create_tool_item(
        str(parent["id"]), 1, index, f"spawn-call-{index}", "spawn_agent", "{}"
    )
    request = SpawnAgent(
        task_name=f"inspect-{index}",
        message=f"Read the relevant files and report evidence for task {index}.",
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
        assert SCHEMA_VERSION == 16
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


def test_only_two_child_runs_can_be_claimed_at_once(tmp_path: Path) -> None:
    store, _session, parent = _parent(tmp_path)
    try:
        repository = CollaborationRepository(store.database)
        children = [_spawn(store, parent, index)[1] for index in range(3)]
        claimed = [store.claim_next_run(), store.claim_next_run()]

        assert {str(run["id"]) for run in claimed if run is not None} == {
            child.run_id for child in children[:2]
        }
        assert store.claim_next_run() is None
        assert set(repository.child_runs(str(parent["id"]))) == {
            child.run_id for child in children
        }
    finally:
        store.close()


def test_child_resources_expose_only_read_tools_and_parent_message_channel(
    tmp_path: Path,
) -> None:
    store, _session, parent = _parent(tmp_path)
    try:
        _repository, child, _item_id = _spawn(store, parent, 0)
        application = CollaborationApplication(
            store,
            schedule=lambda: None,
            cancel=lambda _run_id: None,
            publish=lambda: None,
        )
        with RunResources(
            store,
            child.run_id,
            store.read_run(child.run_id)["extensionSnapshot"],
            collaboration=application,
        ) as resources:
            names = {entry.spec.name for entry in resources.registry.entries}

        assert names <= READ_ONLY_TOOLS | {"send_message"}
        assert names == READ_ONLY_TOOLS | {"send_message"}
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


def test_parent_cancel_stops_active_children_and_rejects_late_child_message(
    tmp_path: Path,
) -> None:
    store, session, parent = _parent(tmp_path)
    try:
        repository, child, _item_id = _spawn(store, parent, 0)
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

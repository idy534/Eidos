from __future__ import annotations

import os
from pathlib import Path
import shutil
import sqlite3
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from eidos_runtime.db.errors import (
    RuntimeDependencyBindingConflictError,
    RuntimeDependencySnapshotConflictError,
    ResourceNotFoundError,
)
from eidos_runtime.db.schema import (
    SCHEMA_VERSION,
    V5_SCHEMA_VERSION,
    V5_SCHEMA_SQL,
    V6_SCHEMA_SQL,
)
from eidos_runtime.db.storage import DATABASE_NAME, SessionStore
from eidos_runtime.models.runtime_dependency_records import (
    RuntimeDependencyBindingRecord,
    RuntimeDependencySnapshotRecord,
)


def _snapshot(
    run_id: str,
    *,
    snapshot_json: str = '{"catalogVersion":1,"packages":[]}',
    created_at: int = 1000,
) -> RuntimeDependencySnapshotRecord:
    return RuntimeDependencySnapshotRecord(
        run_id=run_id,
        manifest_hash="a" * 64,
        catalog_hash="d" * 64,
        snapshot_json=snapshot_json,
        created_at=created_at,
    )


def _record(
    run_id: str,
    *,
    binding_id: str = "binding-1",
    requirements_hash: str = "b" * 64,
    status: str = "ready",
    diagnostics_json: str = "[]",
    qualified_skill_id: str | None = "system:documents",
    created_at: int = 1000,
) -> RuntimeDependencyBindingRecord:
    return RuntimeDependencyBindingRecord(
        run_id=run_id,
        binding_id=binding_id,
        manifest_hash="a" * 64,
        requirements_hash=requirements_hash,
        qualified_skill_id=qualified_skill_id,
        status=status,
        diagnostics_json=diagnostics_json,
        created_at=created_at,
    )


def _store(tmp_path: Path) -> tuple[SessionStore, dict[str, object]]:
    data = tmp_path / "data"
    workspace = tmp_path / "workspace"
    data.mkdir(mode=0o700)
    workspace.mkdir()
    store = SessionStore(data)
    store.initialize()
    assert store.health() == {"state": "ready"}
    return store, store.create_session(str(workspace))


def test_runtime_dependency_record_requires_bounded_canonical_json() -> None:
    with pytest.raises(ValidationError):
        _snapshot("run-1", snapshot_json='{"b":2,"a":1}')

    with pytest.raises(ValidationError):
        _snapshot("run-1", snapshot_json="[]")

    with pytest.raises(ValidationError):
        _record("run-1", diagnostics_json='{"b":2,"a":1}')

    with pytest.raises(ValidationError):
        _snapshot("run-1", snapshot_json='{"unsafe":9007199254740992}')


@pytest.mark.parametrize(
    "status", ["ready", "missing", "incompatible", "invalid"]
)
def test_runtime_dependency_record_preserves_diagnostics_for_each_status(
    status: str,
) -> None:
    record = _record(
        "run-1",
        status=status,
        diagnostics_json='[{"code":"dependency_unavailable"}]',
    )

    assert record.status == status
    assert record.diagnostics_json == '[{"code":"dependency_unavailable"}]'


def test_runtime_dependency_snapshot_commits_event_and_outbox_atomically(
    tmp_path: Path,
) -> None:
    store, session = _store(tmp_path)
    try:
        run, _item = store.create_run(session["id"], "resolve dependencies")
        record = _snapshot(run["id"])

        mutation = store.persist_runtime_dependency_snapshot(record)

        assert mutation.value == record
        assert mutation.event_ids
        assert len(mutation.events) == 1
        assert mutation.events[0]["eventType"] == "run.updated"
        assert mutation.events[0]["runId"] == run["id"]
        assert mutation.events[0]["payload"] == {
            "reason": "runtime_dependency_snapshot",
            "manifestHash": "a" * 64,
            "catalogHash": "d" * 64,
        }
        connection = store.connection
        assert connection is not None
        row = connection.execute(
            "SELECT run_id, manifest_hash, catalog_hash, snapshot_json "
            "FROM run_dependency_snapshots"
        ).fetchone()
        assert tuple(row) == (
            run["id"],
            "a" * 64,
            "d" * 64,
            '{"catalogVersion":1,"packages":[]}',
        )
        outbox = connection.execute(
            "SELECT status FROM event_outbox WHERE event_id = ?",
            (mutation.event_ids[0],),
        ).fetchone()
        assert outbox["status"] == "pending"
    finally:
        store.close()


def test_runtime_dependency_snapshot_is_one_per_run_and_immutable(
    tmp_path: Path,
) -> None:
    store, session = _store(tmp_path)
    try:
        run, _item = store.create_run(session["id"], "resolve dependencies")
        record = _snapshot(run["id"])

        first = store.persist_runtime_dependency_snapshot(record)
        replay = store.persist_runtime_dependency_snapshot(record)

        assert replay.value == first.value == record
        assert replay.events == ()
        with pytest.raises(RuntimeDependencySnapshotConflictError):
            store.persist_runtime_dependency_snapshot(
                _snapshot(run["id"], snapshot_json='{"catalogVersion":2}')
            )
        connection = store.connection
        assert connection is not None
        assert connection.execute(
            "SELECT COUNT(*) FROM run_dependency_snapshots WHERE run_id = ?",
            (run["id"],),
        ).fetchone()[0] == 1
    finally:
        store.close()


def test_runtime_dependency_snapshot_event_failure_rolls_back_snapshot_and_outbox(
    tmp_path: Path,
) -> None:
    store, session = _store(tmp_path)
    try:
        run, _item = store.create_run(session["id"], "resolve dependencies")
        connection = store.connection
        assert connection is not None
        events_before = connection.execute(
            "SELECT COUNT(*) FROM events"
        ).fetchone()[0]
        outbox_before = connection.execute(
            "SELECT COUNT(*) FROM event_outbox"
        ).fetchone()[0]

        with patch(
            "eidos_runtime.db.repositories.runtime_dependencies.append_event",
            side_effect=RuntimeError("fixture event failure"),
        ):
            with pytest.raises(RuntimeError, match="fixture event failure"):
                store.persist_runtime_dependency_snapshot(_snapshot(run["id"]))

        assert connection.execute(
            "SELECT COUNT(*) FROM run_dependency_snapshots"
        ).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == events_before
        assert connection.execute("SELECT COUNT(*) FROM event_outbox").fetchone()[0] == outbox_before
    finally:
        store.close()


def test_runtime_dependency_binding_commits_event_and_outbox_atomically(
    tmp_path: Path,
) -> None:
    store, session = _store(tmp_path)
    try:
        run, _item = store.create_run(session["id"], "resolve dependencies")
        store.persist_runtime_dependency_snapshot(_snapshot(run["id"]))
        record = _record(run["id"])

        mutation = store.persist_runtime_dependency_binding(record)

        assert mutation.value == record
        assert mutation.event_ids
        assert len(mutation.events) == 1
        assert mutation.events[0]["eventType"] == "run.updated"
        assert mutation.events[0]["payload"] == {
            "reason": "runtime_dependency_binding",
            "bindingId": "binding-1",
            "manifestHash": "a" * 64,
            "requirementsHash": "b" * 64,
            "status": "ready",
            "qualifiedSkillId": "system:documents",
        }
        connection = store.connection
        assert connection is not None
        row = connection.execute(
            "SELECT run_id, binding_id, status, diagnostics_json FROM run_dependency_bindings"
        ).fetchone()
        assert tuple(row) == (
            run["id"],
            "binding-1",
            "ready",
            "[]",
        )
        event = connection.execute(
            "SELECT event_type, run_id FROM events WHERE id = ?",
            (mutation.event_ids[0],),
        ).fetchone()
        assert tuple(event) == ("run.updated", run["id"])
        outbox = connection.execute(
            "SELECT status FROM event_outbox WHERE event_id = ?",
            (mutation.event_ids[0],),
        ).fetchone()
        assert outbox["status"] == "pending"
    finally:
        store.close()


def test_runtime_dependency_binding_reuses_immutable_record_without_second_event(
    tmp_path: Path,
) -> None:
    store, session = _store(tmp_path)
    try:
        run, _item = store.create_run(session["id"], "resolve dependencies")
        store.persist_runtime_dependency_snapshot(_snapshot(run["id"]))
        record = _record(run["id"])

        first = store.persist_runtime_dependency_binding(record)
        replay = store.persist_runtime_dependency_binding(record)

        assert replay.value == first.value == record
        assert replay.events == ()
        assert store.list_runtime_dependency_bindings(run["id"]) == (record,)
        connection = store.connection
        assert connection is not None
        assert connection.execute(
            "SELECT COUNT(*) FROM run_dependency_bindings"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = 'run.updated'"
        ).fetchone()[0] == 2
    finally:
        store.close()


def test_runtime_dependency_bindings_allow_distinct_skill_requirements(
    tmp_path: Path,
) -> None:
    store, session = _store(tmp_path)
    try:
        run, _item = store.create_run(session["id"], "resolve dependencies")
        store.persist_runtime_dependency_snapshot(_snapshot(run["id"]))

        first = _record(run["id"], binding_id="binding-1")
        second = _record(
            run["id"],
            binding_id="binding-2",
            requirements_hash="c" * 64,
        )
        store.persist_runtime_dependency_binding(first)
        store.persist_runtime_dependency_binding(second)

        assert store.list_runtime_dependency_bindings(run["id"]) == (first, second)
    finally:
        store.close()


def test_invalid_runtime_dependency_binding_is_persisted_with_its_status(
    tmp_path: Path,
) -> None:
    store, session = _store(tmp_path)
    try:
        run, _item = store.create_run(session["id"], "resolve dependencies")
        store.persist_runtime_dependency_snapshot(_snapshot(run["id"]))
        mutation = store.persist_runtime_dependency_binding(
            _record(
                run["id"],
                status="invalid",
                diagnostics_json='[{"code":"metadata_invalid"}]',
            )
        )

        assert mutation.events[0]["payload"] == {
            "reason": "runtime_dependency_binding_invalid",
            "bindingId": "binding-1",
            "manifestHash": "a" * 64,
            "requirementsHash": "b" * 64,
            "status": "invalid",
            "qualifiedSkillId": "system:documents",
        }
        assert store.read_runtime_dependency_binding("binding-1") == _record(
            run["id"],
            status="invalid",
            diagnostics_json='[{"code":"metadata_invalid"}]',
        )
        assert store.pending_outbox_events()[-1]["payload"]["status"] == "invalid"
    finally:
        store.close()


def test_runtime_dependency_binding_rejects_same_id_conflict_and_cross_run_id(
    tmp_path: Path,
) -> None:
    store, session = _store(tmp_path)
    try:
        first_run, _item = store.create_run(session["id"], "first")
        first = _record(first_run["id"])
        store.persist_runtime_dependency_snapshot(_snapshot(first_run["id"]))
        store.persist_runtime_dependency_binding(first)
        assert store.read_runtime_dependency_binding(first.binding_id) == first

        with pytest.raises(RuntimeDependencyBindingConflictError):
            store.persist_runtime_dependency_binding(
                _record(first_run["id"], diagnostics_json='[{"code":"changed"}]')
            )

        store.fail_run(first_run["id"], "fixture")
        second_run, _item = store.create_run(session["id"], "second")
        store.persist_runtime_dependency_snapshot(_snapshot(second_run["id"]))
        assert store.read_runtime_dependency_binding(first.binding_id) == first
        with pytest.raises(RuntimeDependencyBindingConflictError):
            store.persist_runtime_dependency_binding(
                _record(second_run["id"])
            )
    finally:
        store.close()


def test_runtime_dependency_binding_event_failure_rolls_back_binding_and_outbox(
    tmp_path: Path,
) -> None:
    store, session = _store(tmp_path)
    try:
        run, _item = store.create_run(session["id"], "resolve dependencies")
        store.persist_runtime_dependency_snapshot(_snapshot(run["id"]))
        connection = store.connection
        assert connection is not None
        events_before = connection.execute(
            "SELECT COUNT(*) FROM events"
        ).fetchone()[0]
        outbox_before = connection.execute(
            "SELECT COUNT(*) FROM event_outbox"
        ).fetchone()[0]

        with patch(
            "eidos_runtime.db.repositories.runtime_dependencies.append_event",
            side_effect=RuntimeError("fixture event failure"),
        ):
            with pytest.raises(RuntimeError, match="fixture event failure"):
                store.persist_runtime_dependency_binding(_record(run["id"]))

        assert connection.execute(
            "SELECT COUNT(*) FROM run_dependency_bindings"
        ).fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == events_before
        assert connection.execute("SELECT COUNT(*) FROM event_outbox").fetchone()[0] == outbox_before
    finally:
        store.close()


def test_runtime_dependency_binding_requires_existing_run_snapshot(
    tmp_path: Path,
) -> None:
    store, session = _store(tmp_path)
    try:
        run, _item = store.create_run(session["id"], "resolve dependencies")
        with pytest.raises(ResourceNotFoundError):
            store.persist_runtime_dependency_binding(_record(run["id"]))
    finally:
        store.close()


def test_session_delete_cleans_runtime_dependency_snapshots_and_bindings(
    tmp_path: Path,
) -> None:
    store, session = _store(tmp_path)
    try:
        run, _item = store.create_run(session["id"], "resolve dependencies")
        store.persist_runtime_dependency_snapshot(_snapshot(run["id"]))
        store.persist_runtime_dependency_binding(_record(run["id"]))
        store.fail_run(run["id"], "fixture")

        store.delete_session(session["id"])

        connection = store.connection
        assert connection is not None
        assert connection.execute(
            "SELECT COUNT(*) FROM run_dependency_snapshots"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM run_dependency_bindings"
        ).fetchone()[0] == 0
    finally:
        store.close()


def test_v6_to_v7_migration_creates_dependency_table_and_survives_reopen(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    database = data / DATABASE_NAME
    connection = sqlite3.connect(database)
    connection.executescript(V6_SCHEMA_SQL)
    connection.execute("PRAGMA user_version = 6")
    connection.commit()
    connection.close()
    os.chmod(database, 0o600)

    store = SessionStore(data)
    store.initialize()
    assert store.health() == {"state": "ready"}
    assert store.connection is not None
    assert store.connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    assert store.connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'run_dependency_bindings'"
    ).fetchone() is not None
    store.close()

    reopened = SessionStore(data)
    reopened.initialize()
    try:
        assert reopened.health() == {"state": "ready"}
        assert reopened.connection is not None
        assert reopened.connection.execute(
            "PRAGMA user_version"
        ).fetchone()[0] == SCHEMA_VERSION
    finally:
        reopened.close()


def test_v6_to_v7_migration_failure_rolls_back_and_can_retry_after_repair(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    database = data / DATABASE_NAME
    connection = sqlite3.connect(database)
    connection.executescript(V6_SCHEMA_SQL)
    connection.execute(
        "CREATE INDEX run_dependency_bindings_run ON sessions(updated_at)"
    )
    connection.execute("PRAGMA user_version = 6")
    connection.commit()
    connection.close()
    os.chmod(database, 0o600)

    failed = SessionStore(data)
    failed.initialize()
    assert failed.health() == {
        "state": "health_only",
        "code": "schema_migration_failed",
    }
    failed.close()

    check = sqlite3.connect(database)
    try:
        assert check.execute("PRAGMA user_version").fetchone()[0] == 6
        assert check.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'run_dependency_bindings'"
        ).fetchone() is None
        assert check.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name = 'run_dependency_bindings_run'"
        ).fetchone() is not None
        check.execute("DROP INDEX run_dependency_bindings_run")
        check.commit()
    finally:
        check.close()

    repaired = SessionStore(data)
    repaired.initialize()
    try:
        assert repaired.health() == {"state": "ready"}
        assert repaired.connection is not None
        assert repaired.connection.execute(
            "PRAGMA user_version"
        ).fetchone()[0] == SCHEMA_VERSION
    finally:
        repaired.close()


def test_v5_upgrade_keeps_split_repository_layout_and_adds_state_binding_table(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    database = data / DATABASE_NAME
    connection = sqlite3.connect(database)
    connection.executescript(V5_SCHEMA_SQL)
    connection.execute(f"PRAGMA user_version = {V5_SCHEMA_VERSION}")
    connection.commit()
    connection.close()
    os.chmod(database, 0o600)

    store = SessionStore(data)
    store.initialize()
    try:
        assert store.health() == {"state": "ready"}
        assert store.connection is not None
        assert store.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'repository_snapshots'"
        ).fetchone() is None
        assert store.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'run_dependency_bindings'"
        ).fetchone() is not None
        assert (data / "repository.sqlite").is_file()
        repository = sqlite3.connect(data / "repository.sqlite")
        try:
            assert repository.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name IN ('run_dependency_snapshots', 'run_dependency_bindings')"
            ).fetchone() is None
        finally:
            repository.close()
    finally:
        store.close()


def test_legacy_eidos_database_migrates_into_current_state_layout(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    legacy_database = data / "eidos.db"
    connection = sqlite3.connect(legacy_database)
    connection.executescript(V6_SCHEMA_SQL)
    connection.execute("PRAGMA user_version = 6")
    connection.commit()
    connection.close()
    os.chmod(legacy_database, 0o600)

    store = SessionStore(data)
    store.initialize()
    try:
        assert store.health() == {"state": "ready"}
        assert not legacy_database.exists()
        assert (data / DATABASE_NAME).is_file()
        assert store.connection is not None
        assert store.connection.execute(
            "PRAGMA user_version"
        ).fetchone()[0] == SCHEMA_VERSION
        assert store.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name = 'run_dependency_snapshots'"
        ).fetchone() is not None
    finally:
        store.close()


def test_state_directory_backup_restore_keeps_dependency_history(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    workspace = tmp_path / "workspace"
    data.mkdir(mode=0o700)
    workspace.mkdir()
    store = SessionStore(data)
    store.initialize()
    session = store.create_session(str(workspace))
    run, _item = store.create_run(session["id"], "resolve dependencies")
    snapshot = _snapshot(run["id"])
    binding = _record(run["id"])
    store.persist_runtime_dependency_snapshot(snapshot)
    store.persist_runtime_dependency_binding(binding)
    store.close()

    restored_data = tmp_path / "restored-data"
    shutil.copytree(data, restored_data)
    restored = SessionStore(restored_data)
    restored.initialize()
    try:
        assert restored.health() == {"state": "ready"}
        assert restored.read_runtime_dependency_snapshot(run["id"]) == snapshot
        assert restored.list_runtime_dependency_bindings(run["id"]) == (binding,)
    finally:
        restored.close()

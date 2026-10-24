from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import threading
import zipfile

import pytest

from eidos_runtime.db import context_query_migration
from eidos_runtime.db.database import Database
from eidos_runtime.db.errors import StorageError
from eidos_runtime.db.layout import _migrate_state_schema
from eidos_runtime.db.schema import SCHEMA_VERSION, V30_SCHEMA_SQL, V31_SCHEMA_SQL
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.model.client import ModelResponse, ScriptedModel
from eidos_runtime.db.backup import restore
from eidos_runtime.memory.repository import MemoryRejected
from eidos_runtime.runtime.engine import RuntimeEngine


pytestmark = pytest.mark.integration


@pytest.mark.parametrize("entry", ["database", "layout"])
@pytest.mark.parametrize("version", [30, 31])
def test_upgrade_preserves_history_and_reopens(tmp_path: Path, entry: str, version: int):
    root = tmp_path / "data"
    root.mkdir(mode=0o700)
    path = root / "state.sqlite"
    with sqlite3.connect(path) as connection:
        connection.executescript(V30_SCHEMA_SQL if version == 30 else V31_SCHEMA_SQL)
        connection.execute(f"PRAGMA user_version={version}")
        connection.execute(
            "INSERT INTO sessions(id,workspace_root,title,created_at,updated_at) "
            "VALUES('saved-session','/workspace','Saved history',1,1)"
        )
    path.chmod(0o600)
    if entry == "layout":
        owner = Database(root)
        owner._connection = sqlite3.connect(path)
        try:
            _migrate_state_schema(owner)
        finally:
            owner.close()
    for _ in range(2):
        owner = Database(root)
        owner.initialize()
        try:
            assert owner.health_state == "ready"
            connection = owner.connection()
            assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
            assert [row[2] for row in connection.execute("PRAGMA index_info(steps_run_ordinal)")] == ["run_id", "ordinal"]
            assert connection.execute("SELECT title FROM sessions WHERE id='saved-session'").fetchone()[0] == "Saved history"
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        finally:
            owner.close()


@pytest.mark.parametrize("entry", ["database", "layout"])
def test_failed_index_upgrade_rolls_back_and_can_retry(tmp_path: Path, monkeypatch, entry: str):
    root = tmp_path / "data"
    root.mkdir(mode=0o700)
    path = root / "state.sqlite"
    with sqlite3.connect(path) as connection:
        connection.executescript(V31_SCHEMA_SQL + "\nPRAGMA user_version=31;")
    path.chmod(0o600)
    original = context_query_migration.CONTEXT_QUERY_INDEX_SQL
    with monkeypatch.context() as patch:
        patch.setattr(context_query_migration, "CONTEXT_QUERY_INDEX_SQL", original + "\nINVALID SQL;")
        owner = Database(root)
        try:
            if entry == "database":
                owner.initialize()
                assert owner.health_code == "schema_migration_failed"
                assert owner.health_state == "health_only"
            else:
                owner._connection = sqlite3.connect(path)
                with pytest.raises(StorageError, match="schema_migration_failed"):
                    _migrate_state_schema(owner)
        finally:
            owner.close()
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 31
        assert connection.execute("SELECT 1 FROM sqlite_master WHERE name='steps_run_ordinal'").fetchone() is None
    owner = Database(root)
    owner.initialize()
    try:
        assert owner.health_state == "ready"
        assert owner.connection().execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    finally:
        owner.close()


def test_old_memory_backup_is_rejected_without_partial_restore(tmp_path: Path):
    path = tmp_path / "legacy.sqlite"
    with sqlite3.connect(path) as connection:
        connection.executescript(V31_SCHEMA_SQL + "\nPRAGMA user_version=31;")
    archive_path = tmp_path / "backup.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.write(path, "state.sqlite")
        archive.writestr("manifest.json", json.dumps({
            "format": "eidos-memory-backup-v1", "schemaVersion": 31,
            "privacyEpochs": {}, "memoryRefs": [],
        }))
    destination = tmp_path / "restored"
    with pytest.raises(MemoryRejected, match="format_unsupported"):
        restore(archive_path, destination)
    assert not destination.exists()


@pytest.fixture
def populated_history(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    try:
        source = store.create_session(str(workspace))
        run, _ = store.create_run(source["id"], "Source task")
        RuntimeEngine(store, ScriptedModel([ModelResponse(text="Source answer")]), lambda _: None).run(run["id"], threading.Event())
        target = store.create_session(str(workspace))
        other = store.create_session(str(workspace))
        other_run, _ = store.create_run(other["id"], "Unrelated task")
        RuntimeEngine(store, ScriptedModel([ModelResponse(text="Other answer")]), lambda _: None).run(other_run["id"], threading.Event())
        with store.database.transaction() as connection:
            connection.executemany(
                "INSERT INTO items(id,session_id,run_id,ordinal,model_step_index,kind,status,content,created_at,completed_at) "
                "SELECT ?,session_id,run_id,?,model_step_index,kind,status,content,created_at,completed_at "
                "FROM items WHERE run_id=? AND kind='assistant_message' ORDER BY creation_seq LIMIT 1",
                [(f"history-{n}", n + 10, run["id"]) for n in range(64)],
            )
        yield store, run, target, other_run
    finally:
        store.close()


def test_context_queries_stay_bounded_and_allow_other_session_reads(populated_history):
    store, run, target, other_run = populated_history
    expected = store.context_projection_facts(run["id"])
    with store.database.transaction() as connection:
        connection.executemany(
            "INSERT INTO steps(id,run_id,segment_id,ordinal,status,resolution_snapshot_id,created_at,completed_at) "
            "SELECT ?,run_id,segment_id,?,'completed',resolution_snapshot_id,created_at,completed_at "
            "FROM steps WHERE run_id=? ORDER BY creation_seq LIMIT 1",
            [(f"unrelated-step-{n}", n + 10, other_run["id"]) for n in range(2_000)],
        )
        connection.executemany(
            "INSERT INTO model_attempts(id,step_id,ordinal,status,started_at,completed_at) "
            "VALUES(?,?,1,'completed',1,2)",
            [(f"unrelated-attempt-{n}", f"unrelated-step-{n}") for n in range(2_000)],
        )

    # Count SQLite VM work instead of asserting a machine-dependent duration.
    # Without the index, per-Item replay lookups exhaust this budget by scanning
    # unrelated attempts while holding the shared connection owner's lock.
    ticks = 0
    querying = threading.Event()
    reading = threading.Event()

    def progress() -> int:
        nonlocal ticks
        ticks += 1
        if not querying.is_set():
            querying.set()
            assert reading.wait(5), "other Session reader did not start"
        return int(ticks > 250)

    def read_target():
        assert querying.wait(5), "context query did not start"
        reading.set()
        return store.read_session_snapshot(target["id"])

    with ThreadPoolExecutor(max_workers=2) as pool:
        connection.set_progress_handler(progress, 1_000)
        try:
            reader = pool.submit(read_target)
            context = pool.submit(store.context_projection_facts, run["id"])
            assert context.result(timeout=10) == expected
            assert reader.result(timeout=10)["session"]["id"] == target["id"]
            assert ticks <= 250
        finally:
            connection.set_progress_handler(None, 0)
    directory = store.data_directory
    store.close()
    reopened = SessionStore(directory)
    reopened.initialize()
    try:
        assert reopened.context_projection_facts(run["id"]) == expected
        assert reopened.read_session_snapshot(target["id"])["session"]["id"] == target["id"]
    finally:
        reopened.close()

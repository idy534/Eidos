from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sqlite3
import threading
import zipfile

import pytest

from eidos_runtime.db import event_query_migration, thread_history
from eidos_runtime.db.backup import FORMAT, restore
from eidos_runtime.db.database import Database
from eidos_runtime.db.errors import StorageError
from eidos_runtime.db.layout import _migrate_state_schema
from eidos_runtime.db.schema import SCHEMA_VERSION, V48_SCHEMA_SQL, V48_SCHEMA_VERSION
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.model.client import ModelResponse, ScriptedModel
from eidos_runtime.persistence.repository_intelligence import (
    RepositoryIntelligenceRepository,
    RepositoryWorkspaceIdentity,
)
from eidos_runtime.persistence.worktrees import ProjectWorktreeRepository
from eidos_runtime.runtime.engine import RuntimeEngine


EXPECTED_INDEXES = {
    "events_session_id": ["session_id", "id"],
    "events_session_type_id": ["session_id", "event_type", "id"],
    "events_run_type_id": ["run_id", "event_type", "id"],
    "context_plans_run_id": ["run_id"],
    "context_snapshots_run_id": ["run_id", "created_at", "id"],
    "model_attempts_context_snapshot": ["context_snapshot_id"],
}


@pytest.mark.parametrize("entry", ["database", "layout"])
def test_v48_upgrade_creates_event_and_context_indexes(tmp_path: Path, entry: str) -> None:
    root = tmp_path / "data"
    root.mkdir(mode=0o700)
    path = root / "state.sqlite"
    with sqlite3.connect(path) as connection:
        connection.executescript(V48_SCHEMA_SQL)
        connection.execute(f"PRAGMA user_version={V48_SCHEMA_VERSION}")
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
            for index_name, columns in EXPECTED_INDEXES.items():
                assert [
                    row[2] for row in connection.execute(f"PRAGMA index_info({index_name})")
                ] == columns
            assert (
                connection.execute(
                    "SELECT title FROM sessions WHERE id='saved-session'"
                ).fetchone()[0]
                == "Saved history"
            )
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        finally:
            owner.close()


@pytest.mark.parametrize("entry", ["database", "layout"])
def test_failed_v48_index_upgrade_rolls_back_and_can_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry: str
) -> None:
    root = tmp_path / "data"
    root.mkdir(mode=0o700)
    path = root / "state.sqlite"
    with sqlite3.connect(path) as connection:
        connection.executescript(f"{V48_SCHEMA_SQL}\nPRAGMA user_version={V48_SCHEMA_VERSION};")
    path.chmod(0o600)
    original = event_query_migration.EVENT_QUERY_INDEX_SQL
    with monkeypatch.context() as patch:
        patch.setattr(
            event_query_migration,
            "EVENT_QUERY_INDEX_SQL",
            original + "\nINVALID SQL;",
        )
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
        assert connection.execute("PRAGMA user_version").fetchone()[0] == V48_SCHEMA_VERSION
        for index_name in EXPECTED_INDEXES:
            assert (
                connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE name=?", (index_name,)
                ).fetchone()
                is None
            )
    owner = Database(root)
    owner.initialize()
    try:
        assert owner.health_state == "ready"
        assert owner.connection().execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    finally:
        owner.close()


def test_v48_backup_restores_and_upgrades_to_v49(tmp_path: Path) -> None:
    state_path = tmp_path / "v48.sqlite"
    with sqlite3.connect(state_path) as connection:
        connection.executescript(f"{V48_SCHEMA_SQL}\nPRAGMA user_version={V48_SCHEMA_VERSION};")
    archive_path = tmp_path / "backup.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.write(state_path, "state.sqlite")
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "format": FORMAT,
                    "schemaVersion": V48_SCHEMA_VERSION,
                    "memoryRefs": [],
                    "encrypted": False,
                }
            ),
        )
    destination = tmp_path / "restored"
    restore(archive_path, destination)
    with sqlite3.connect(destination / "state.sqlite") as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        for index_name, columns in EXPECTED_INDEXES.items():
            assert [
                row[2] for row in connection.execute(f"PRAGMA index_info({index_name})")
            ] == columns


def test_session_read_and_event_queries_stay_bounded_with_large_event_history(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    try:
        noisy = store.create_session(str(workspace))
        noisy_run, _ = store.create_run(noisy["id"], "Noisy task")
        RuntimeEngine(
            store, ScriptedModel([ModelResponse(text="Noisy answer")]), lambda _: None
        ).run(noisy_run["id"], threading.Event())

        target = store.create_session(str(workspace))
        target_run, _ = store.create_run(target["id"], "Target task")
        RuntimeEngine(
            store, ScriptedModel([ModelResponse(text="Target answer")]), lambda _: None
        ).run(target_run["id"], threading.Event())

        with store.database.transaction() as connection:
            connection.executemany(
                """
                INSERT INTO events(
                    event_contract_version, session_id, run_id,
                    event_type, occurred_at, payload_json
                ) VALUES (1, ?, ?, 'item/delta', ?, '{"delta":"chunk"}')
                """,
                [(noisy["id"], noisy_run["id"], index) for index in range(10_000)],
            )

        ticks = 0

        def progress() -> int:
            nonlocal ticks
            ticks += 1
            return int(ticks > 100)

        connection = store.database.connection()
        connection.set_progress_handler(progress, 1_000)
        try:
            snapshot = store.read_session_snapshot(target["id"])
            events_page = store.list_events(target["id"], after_event_id=0)
            assert snapshot["session"]["id"] == target["id"]
            assert len(events_page["items"]) >= 1
            assert ticks <= 25
        finally:
            connection.set_progress_handler(None, 0)
    finally:
        store.close()


def test_project_reads_hold_connection_lock_through_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "project"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    try:
        repo = ProjectWorktreeRepository(store.database)
        project = repo.get_or_create_project(workspace, name="Demo")
        connection = store.database.connection()

        def locked_connection() -> sqlite3.Connection:
            assert store.database.lock._is_owned(), "shared connection accessed without lock"
            return connection

        with monkeypatch.context() as patch:
            patch.setattr(store.database, "connection", locked_connection)
            loaded = repo.read_project(project.id)
            listed = repo.list_projects()
        assert loaded is not None and loaded.id == project.id
        assert [entry.id for entry in listed] == [project.id]
    finally:
        store.close()


def test_repository_read_status_uses_wal_reader_when_writer_holds_lock(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    stat_result = workspace.stat()
    identity = RepositoryWorkspaceIdentity(
        root=str(workspace.resolve()),
        device=stat_result.st_dev,
        inode=stat_result.st_ino,
        owner=os.getuid(),
    )
    store = SessionStore(tmp_path / "data")
    store.initialize()
    try:
        layout = store._persistence_layout
        assert layout is not None
        repo_db = layout.repository
        repository = RepositoryIntelligenceRepository(repo_db)  # type: ignore[arg-type]

        lock_acquired = threading.Event()
        release_lock = threading.Event()

        def hold_writer_lock() -> None:
            with repo_db.lock:
                lock_acquired.set()
                assert release_lock.wait(timeout=5)

        with ThreadPoolExecutor(max_workers=1) as pool:
            holder = pool.submit(hold_writer_lock)
            assert lock_acquired.wait(timeout=5)
            try:
                status = repository.read_status(identity)
                assert status.repository_id == identity.repository_id
                assert status.complete is False
                assert status.reconciliation_required is True
            finally:
                release_lock.set()
            holder.result(timeout=5)
    finally:
        store.close()


def test_live_thread_history_catch_up_skips_full_file_rescan_on_incremental_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    try:
        first = store.create_session(str(workspace))
        second = store.create_session(str(workspace))
        assert store.project_thread_history() >= 2

        verified: list[Path] = []
        original_verify = thread_history._verify_history_file

        def track_verify(path: Path) -> os.stat_result:
            verified.append(path)
            return original_verify(path)

        monkeypatch.setattr(thread_history, "_verify_history_file", track_verify)

        run, _ = store.create_run(first["id"], "Incremental event")
        assert run["sessionId"] == first["id"]
        assert second["id"] != first["id"]
        assert store.project_thread_history() >= 1
        assert verified == []
    finally:
        store.close()


def test_thread_history_rate_limits_full_repair_on_empty_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    try:
        layout = store._persistence_layout
        assert layout is not None and layout.thread_history_store is not None
        history = layout.thread_history_store
        now = [1000.0]
        history._monotonic = lambda: now[0]
        history._repaired_at = now[0]
        store.create_session(str(workspace))
        assert store.project_thread_history() >= 1

        repairs: list[int] = []
        original_repair = history._repair_uncommitted_tails

        def track_repair() -> None:
            repairs.append(1)
            original_repair()

        monkeypatch.setattr(history, "_repair_uncommitted_tails", track_repair)

        # A concurrent delivery already projected everything: not a reason to rescan.
        assert store.project_thread_history() == 0
        assert repairs == []

        now[0] += thread_history.REPAIR_INTERVAL_SECONDS
        assert store.project_thread_history() == 0
        assert repairs == [1]

        assert store.project_thread_history() == 0
        assert repairs == [1]
    finally:
        store.close()

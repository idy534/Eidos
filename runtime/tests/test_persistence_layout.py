from __future__ import annotations

from pathlib import Path
import json
import logging
import os
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from eidos_runtime.db.errors import StorageError
from eidos_runtime.db.layout import (
    AuxiliaryDatabase,
    LEGACY_STATE_DATABASE_NAME,
    LOGS_DATABASE_NAME,
    LOGS_SCHEMA_SQL,
    REPOSITORY_DATABASE_NAME,
    STATE_DATABASE_NAME,
    THREAD_HISTORY_DATABASE_NAME,
)
from eidos_runtime.db.json_blobs import JsonBlobCorruptionError, JsonBlobReference, JsonBlobStore
from eidos_runtime.db.runtime_logs import RuntimeLogStore
from eidos_runtime.db.database import Database
from eidos_runtime.db.schema import SCHEMA_SQL, SCHEMA_VERSION
from eidos_runtime.db.schema import V5_SCHEMA_SQL
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.db import thread_history
from eidos_runtime.persistence.queued_runs import QueuedRunRepository
from eidos_runtime.application.repository import RepositoryApplication
from eidos_runtime.persistence.repository_intelligence import (
    RepositoryIntelligenceRepository,
    RepositoryWorkspaceIdentity,
)
from eidos_runtime.persistence.errors import PersistenceCorruptionError
from eidos_runtime.repo_intelligence.index import RepositoryIndexer
from eidos_runtime.repo_intelligence.inventory import RepositoryInventoryBuilder
from eidos_runtime.repo_intelligence.map import RepositoryMapBuilder
from eidos_runtime.model.client import ModelResponse, ScriptedModel
from eidos_runtime.runtime.engine import RuntimeEngine


def _workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    source = root / "src"
    source.mkdir(parents=True)
    (source / "service.py").write_text(
        "def service(value: str) -> str:\n    return value.upper()\n",
        encoding="utf-8",
    )
    return root


@pytest.mark.parametrize("owner_name", ["state", "history"])
def test_history_reads_hold_connection_lock_through_row_materialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, owner_name: str,
) -> None:
    store = SessionStore(tmp_path / "data")
    store.initialize()
    try:
        session = store.create_session(str(_workspace(tmp_path)))
        layout = store._persistence_layout
        assert layout is not None
        owner = store.database if owner_name == "state" else layout.thread_history
        connection = owner.connection()

        def locked_connection() -> sqlite3.Connection:
            assert owner.lock._is_owned(), "shared connection accessed without its lock"
            return connection

        def locked_row(cursor: sqlite3.Cursor, values: tuple) -> sqlite3.Row:
            assert owner.lock._is_owned(), "result materialized outside the connection lock"
            return sqlite3.Row(cursor, values)

        with monkeypatch.context() as patch:
            patch.setattr(owner, "connection", locked_connection)
            patch.setattr(connection, "row_factory", locked_row)
            assert store.project_thread_history() >= 1
            assert store.project_thread_history() == 0
        assert store.read_session(session["id"])["id"] == session["id"]
    finally:
        store.close()


def test_history_file_io_does_not_hold_database_locks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = SessionStore(tmp_path / "data")
    store.initialize()
    try:
        store.create_session(str(_workspace(tmp_path)))
        layout = store._persistence_layout
        assert layout is not None
        checked = set()

        def check_io(name):
            original = getattr(thread_history, name)

            def call(*args, **kwargs):
                assert not store.database.lock._is_owned()
                assert not layout.thread_history.lock._is_owned()
                checked.add(name)
                return original(*args, **kwargs)

            return call

        for name in ("_append_at_committed_offset", "_verify_history_file", "_fsync_directory"):
            monkeypatch.setattr(thread_history, name, check_io(name))
        store.project_thread_history()
        path = next((tmp_path / "data" / "history" / "sessions").glob("*.jsonl"))
        committed_size = path.stat().st_size
        with path.open("ab") as stream:
            stream.write(b'{"uncommitted":true}\n')
        assert store.project_thread_history() == 0
        assert path.stat().st_size == committed_size
        assert checked == {"_append_at_committed_offset", "_verify_history_file", "_fsync_directory"}
    finally:
        store.close()


def test_queue_reads_and_history_projection_preserve_existing_session(
    tmp_path: Path,
) -> None:
    store = SessionStore(tmp_path / "data")
    store.initialize()
    try:
        session = store.create_session(str(_workspace(tmp_path)))
        active, _ = store.create_run(session["id"], "active")
        first, _ = store.enqueue_run(session["id"], "first")
        second, _ = store.enqueue_run(session["id"], "second")
        queue = QueuedRunRepository(store.database)
        start = threading.Barrier(2, timeout=5)

        def read_queue() -> None:
            start.wait()
            for _ in range(2000):
                assert [item.run_id for item in queue.list(session["id"]).items] == [first["id"], second["id"]]

        def project_history() -> None:
            start.wait()
            for _ in range(300):
                store.project_thread_history()

        with ThreadPoolExecutor(max_workers=2) as executor:
            reads = executor.submit(read_queue)
            projections = executor.submit(project_history)
            reads.result(timeout=15)
            projections.result(timeout=15)
        assert store.read_session(session["id"])["id"] == session["id"]
        assert store.read_run(active["id"])["status"] == "running"
        assert store.project_thread_history() == 0
        layout = store._persistence_layout
        assert layout is not None
        with store.database.lock:
            event_ids = [row[0] for row in store.connection.execute("SELECT id FROM events ORDER BY id").fetchall()]
        with layout.thread_history.lock:
            projected_ids = [row[0] for row in layout.thread_history.connection().execute(
                "SELECT event_id FROM history_events ORDER BY event_id",
            ).fetchall()]
        assert projected_ids == event_ids
    finally:
        store.close()


def test_fresh_store_creates_vertical_database_layout(tmp_path: Path) -> None:
    data = tmp_path / "data"
    store = SessionStore(data)

    store.initialize()
    try:
        assert store.health_state == "ready"
        assert (data / STATE_DATABASE_NAME).is_file()
        assert (data / REPOSITORY_DATABASE_NAME).is_file()
        assert (data / THREAD_HISTORY_DATABASE_NAME).is_file()
        assert (data / LOGS_DATABASE_NAME).is_file()
        assert not (data / "memories.sqlite").exists()
        assert not (data / LEGACY_STATE_DATABASE_NAME).exists()
    finally:
        store.close()


def test_legacy_eidos_database_becomes_state_database_without_losing_rows(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    legacy = data / LEGACY_STATE_DATABASE_NAME
    connection = sqlite3.connect(legacy)
    connection.executescript(SCHEMA_SQL)
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    connection.execute(
        "INSERT INTO sessions (id, workspace_root, created_at, updated_at) "
        "VALUES ('legacy-session', ?, 1, 1)",
        (str(tmp_path / "workspace"),),
    )
    connection.commit()
    connection.close()
    legacy.chmod(0o600)

    store = SessionStore(data)
    store.initialize()
    try:
        assert store.health_state == "ready"
        assert store.read_session("legacy-session")["id"] == "legacy-session"
        assert (data / STATE_DATABASE_NAME).is_file()
        assert not legacy.exists()
    finally:
        store.close()


def test_repository_facts_are_written_only_to_repository_database(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    root = _workspace(tmp_path)
    identity = RepositoryWorkspaceIdentity.from_root(root)
    inventory = RepositoryInventoryBuilder(root).build()
    index = RepositoryIndexer(root).build(inventory)
    repository_map = RepositoryMapBuilder(root).build(inventory)
    store = SessionStore(data)
    store.initialize()
    try:
        repository = store.repository_intelligence_repository()
        committed = repository.commit_complete(
            inventory, index, repository_map, identity
        )

        assert committed.complete is True
        state = sqlite3.connect(data / STATE_DATABASE_NAME)
        repository_db = sqlite3.connect(data / REPOSITORY_DATABASE_NAME)
        try:
            assert state.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'repository_snapshots'"
            ).fetchone() is None
            assert repository_db.execute(
                "SELECT COUNT(*) FROM repository_snapshots"
            ).fetchone()[0] == 1
        finally:
            state.close()
            repository_db.close()
    finally:
        store.close()

    reopened = SessionStore(data)
    reopened.initialize()
    try:
        restored = RepositoryIntelligenceRepository(
            reopened.repository_database
        ).read_latest_complete(inventory.repository_id, identity)
        assert restored == committed
    finally:
        reopened.close()


def test_repository_extraction_keeps_only_latest_restorable_generation(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    root = _workspace(tmp_path)
    identity = RepositoryWorkspaceIdentity.from_root(root)
    data.mkdir(mode=0o700)
    legacy_path = data / STATE_DATABASE_NAME
    legacy = sqlite3.connect(legacy_path)
    legacy.executescript(V5_SCHEMA_SQL)
    legacy.execute("PRAGMA user_version = 5")
    legacy.commit()
    legacy.close()
    legacy_path.chmod(0o600)
    state = Database(data)
    state.initialize()
    try:
        repository = RepositoryIntelligenceRepository(state)
        application = RepositoryApplication(root, repository=repository)
        first = application.build()
        (root / "src" / "service.py").write_text(
            "def service(value: str) -> str:\n    return value.lower()\n",
            encoding="utf-8",
        )
        second = application.build()
        assert first.persisted_snapshot is not None
        assert second.persisted_snapshot is not None
        assert (
            first.persisted_snapshot.snapshot_id
            != second.persisted_snapshot.snapshot_id
        )
        with state.transaction() as connection:
            connection.execute(
                """
                INSERT INTO repository_snapshots (
                    creation_seq, id, repository_id, workspace_root,
                    workspace_dev, workspace_inode, workspace_uid,
                    inventory_generation, index_generation,
                    inventory_snapshot_id, inventory_snapshot_hash,
                    index_snapshot_id, index_snapshot_hash,
                    repository_map_json, grammar_versions_json,
                    status, complete, created_at
                )
                SELECT 1, 'legacy-superseded-generation', repository_id,
                       workspace_root, workspace_dev, workspace_inode,
                       workspace_uid, 0, 0, 'legacy-inventory',
                       inventory_snapshot_hash, 'legacy-index',
                       index_snapshot_hash, repository_map_json,
                       grammar_versions_json, status, complete, created_at - 1
                FROM repository_snapshots
                WHERE id = ?
                """,
                (second.persisted_snapshot.snapshot_id,),
            )
        assert state.connection().execute(
            "SELECT COUNT(*) FROM repository_snapshots"
        ).fetchone()[0] == 2
    finally:
        state.close()

    store = SessionStore(data)
    store.initialize()
    try:
        repository_connection = sqlite3.connect(data / REPOSITORY_DATABASE_NAME)
        try:
            assert repository_connection.execute(
                "SELECT COUNT(*) FROM repository_snapshots"
            ).fetchone()[0] == 1
        finally:
            repository_connection.close()
        assert store.database.connection().execute(
            "PRAGMA freelist_count"
        ).fetchone()[0] == 0
        restored = store.repository_intelligence_repository().read_latest_complete(
            first.inventory.repository_id, identity
        )
        assert restored is not None
        assert restored.snapshot_id == second.persisted_snapshot.snapshot_id
    finally:
        store.close()


def test_repository_database_prunes_superseded_complete_generation(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    root = _workspace(tmp_path)
    store = SessionStore(data)
    store.initialize()
    try:
        application = RepositoryApplication(
            root, repository=store.repository_intelligence_repository()
        )
        first = application.build()
        (root / "src" / "service.py").write_text(
            "def service(value: str) -> str:\n    return value.casefold()\n",
            encoding="utf-8",
        )
        second = application.build()
        assert first.persisted_snapshot is not None
        assert second.persisted_snapshot is not None
        assert first.persisted_snapshot.snapshot_id != second.persisted_snapshot.snapshot_id

        connection = sqlite3.connect(data / REPOSITORY_DATABASE_NAME)
        try:
            assert connection.execute(
                "SELECT COUNT(*) FROM repository_snapshots"
            ).fetchone()[0] == 1
            assert connection.execute(
                "SELECT id FROM repository_snapshots"
            ).fetchone()[0] == second.persisted_snapshot.snapshot_id
        finally:
            connection.close()
    finally:
        store.close()


def test_thread_history_projects_state_events_to_restart_safe_jsonl(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    root = _workspace(tmp_path)
    store = SessionStore(data)
    store.initialize()
    try:
        session = store.create_session(str(root))
        projected = store.project_thread_history()
        assert projected >= 1

        history = sqlite3.connect(data / THREAD_HISTORY_DATABASE_NAME)
        history.row_factory = sqlite3.Row
        try:
            row = history.execute(
                "SELECT * FROM history_events WHERE session_id = ? "
                "ORDER BY event_id LIMIT 1",
                (session["id"],),
            ).fetchone()
            assert row is not None
            relative_path = row["relative_path"]
            assert row["record_bytes"] > 0
        finally:
            history.close()
        path = data / "history" / relative_path
        record = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
        assert record["eventType"] == "session.created"
        committed_size = path.stat().st_size
        with path.open("ab") as stream:
            stream.write(b'{"uncommitted":true}\n')
    finally:
        store.close()

    reopened = SessionStore(data)
    reopened.initialize()
    try:
        assert reopened.project_thread_history() == 0
        assert path.stat().st_size == committed_size
        history = sqlite3.connect(data / THREAD_HISTORY_DATABASE_NAME)
        try:
            assert history.execute(
                "SELECT COUNT(*) FROM history_events WHERE session_id = ?",
                (session["id"],),
            ).fetchone()[0] == 1
        finally:
            history.close()
    finally:
        reopened.close()


def test_runtime_logs_use_jsonl_with_logs_database_index(tmp_path: Path) -> None:
    data = tmp_path / "data"
    store = SessionStore(data)
    store.initialize()
    handler = store.create_log_handler()
    logger = logging.getLogger("eidos.test.persistence-layout")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    logger.addHandler(handler)
    try:
        logger.info("separate log payload")
    finally:
        logger.removeHandler(handler)
        handler.close()
        store.close()

    logs = sqlite3.connect(data / LOGS_DATABASE_NAME)
    logs.row_factory = sqlite3.Row
    try:
        row = logs.execute("SELECT * FROM log_segments").fetchone()
        assert row is not None
        assert row["record_count"] == 1
        assert row["stored_bytes"] > 0
        relative_path = row["relative_path"]
    finally:
        logs.close()
    records = (data / "logs" / relative_path).read_text(
        encoding="utf-8"
    ).splitlines()
    assert json.loads(records[0])["message"] == "separate log payload"
    handler.close()


def test_old_v1_logs_schema_allows_append_after_store_initialization(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    logs_root = data / "logs"
    segment_id = "0-123-" + ("a" * 32)
    relative_path = f"1970-01-01/{segment_id}.jsonl"
    segment_path = logs_root / relative_path
    segment_path.parent.mkdir(mode=0o700, parents=True)
    segment_path.write_bytes(b"legacy log\n")
    segment_path.chmod(0o600)
    logs_root.chmod(0o700)

    logs_path = data / LOGS_DATABASE_NAME
    connection = sqlite3.connect(logs_path)
    connection.executescript(
        """
        CREATE TABLE log_segments (
            id TEXT PRIMARY KEY,
            relative_path TEXT NOT NULL UNIQUE,
            first_timestamp INTEGER NOT NULL,
            last_timestamp INTEGER NOT NULL,
            record_count INTEGER NOT NULL,
            stored_bytes INTEGER NOT NULL,
            content_sha256 TEXT NOT NULL,
            state TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );

        CREATE INDEX log_segments_time
        ON log_segments(last_timestamp DESC);

        PRAGMA user_version = 1;
        """
    )
    legacy_digest = "d" * 64
    connection.execute(
        """
        INSERT INTO log_segments (
            id, relative_path, first_timestamp, last_timestamp,
            record_count, stored_bytes, content_sha256, state,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            segment_id,
            relative_path,
            0,
            0,
            1,
            len(b"legacy log\n"),
            legacy_digest,
            "sealed",
            0,
            0,
        ),
    )
    connection.commit()
    connection.close()
    logs_path.chmod(0o600)

    store = SessionStore(data)
    store.initialize()
    try:
        assert store.health_state == "ready"
        assert store._persistence_layout is not None
        assert store._persistence_layout.runtime_logs is not None
        assert store._persistence_layout.logs.connection().execute(
            "PRAGMA user_version"
        ).fetchone()[0] == 2
        assert store._persistence_layout.repository.connection().execute(
            "PRAGMA user_version"
        ).fetchone()[0] == 1
        assert store._persistence_layout.thread_history.connection().execute(
            "PRAGMA user_version"
        ).fetchone()[0] == 1
        legacy = store._persistence_layout.logs.connection().execute(
            "SELECT * FROM log_segments WHERE id = ?", (segment_id,)
        ).fetchone()
        assert legacy is not None
        assert legacy["relative_path"] == relative_path
        assert legacy["first_timestamp"] == 0
        assert legacy["last_timestamp"] == 0
        assert legacy["record_count"] == 1
        assert legacy["stored_bytes"] == len(b"legacy log\n")
        assert legacy["chain_sha256"] == legacy_digest
        assert legacy["state"] == "sealed"
        record = logging.getLogger(
            "eidos.test.persistence-layout.legacy-logs"
        ).makeRecord(
            "eidos.test.persistence-layout.legacy-logs",
            logging.INFO,
            __file__,
            1,
            "legacy schema append",
            (),
            None,
        )

        store._persistence_layout.runtime_logs.append(record)

        row = store._persistence_layout.logs.connection().execute(
            "SELECT record_count, stored_bytes FROM log_segments"
        ).fetchall()
        assert len(row) == 2
        assert sum(int(item["record_count"]) for item in row) == 2
        assert sum(int(item["stored_bytes"]) for item in row) > 0
    finally:
        store.close()


def test_new_v1_logs_schema_is_versioned_without_column_rename(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    logs_path = data / LOGS_DATABASE_NAME
    connection = sqlite3.connect(logs_path)
    connection.executescript(LOGS_SCHEMA_SQL)
    connection.execute("PRAGMA user_version = 1")
    connection.commit()
    connection.close()
    logs_path.chmod(0o600)

    store = SessionStore(data)
    store.initialize()
    try:
        assert store.health_state == "ready"
        assert store._persistence_layout is not None
        assert store._persistence_layout.runtime_logs is not None
        assert store._persistence_layout.logs.connection().execute(
            "PRAGMA user_version"
        ).fetchone()[0] == 2
        record = logging.getLogger(
            "eidos.test.persistence-layout.current-v1-logs"
        ).makeRecord(
            "eidos.test.persistence-layout.current-v1-logs",
            logging.INFO,
            __file__,
            1,
            "current v1 schema append",
            (),
            None,
        )
        store._persistence_layout.runtime_logs.append(record)
        assert store._persistence_layout.logs.connection().execute(
            "SELECT COUNT(*) FROM log_segments"
        ).fetchone()[0] == 1
    finally:
        store.close()


def test_runtime_logs_delete_oldest_sealed_segments_at_total_limit(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    database = AuxiliaryDatabase(
        data, name=LOGS_DATABASE_NAME, schema_sql=LOGS_SCHEMA_SQL
    )
    database.initialize()
    try:
        logs = RuntimeLogStore(
            data,
            database,
            max_segment_bytes=300,
            max_total_bytes=650,
        )
        logger = logging.getLogger("eidos.test.log-retention")
        for index in range(12):
            record = logger.makeRecord(
                logger.name,
                logging.INFO,
                __file__,
                1,
                "record-%d-%s",
                (index, "x" * 100),
                None,
            )
            logs.append(record)
        rows = database.connection().execute(
            "SELECT relative_path, stored_bytes, state FROM log_segments "
            "ORDER BY created_at, id"
        ).fetchall()
        assert len(rows) <= 3
        assert sum(int(row["stored_bytes"]) for row in rows) <= 950
        assert sum(row["state"] == "active" for row in rows) == 1
        assert all(
            (data / "logs" / str(row["relative_path"])).is_file()
            for row in rows
        )
    finally:
        database.close()


@pytest.mark.parametrize("zones", [("UTC0", "KST-9"), ("Asia/Shanghai", "Asia/Seoul", "Asia/Shanghai")])
def test_runtime_logs_survive_restart_after_timezone_change(tmp_path: Path, zones: tuple[str, ...]) -> None:
    original_timezone = os.environ.get("TZ")
    data = tmp_path / "data"
    segment_id = None
    try:
        for timezone in zones:
            os.environ["TZ"] = timezone
            time.tzset()
            store = SessionStore(data)
            store.initialize()
            try:
                assert store.health_state == "ready", store.health()
                logs = store._persistence_layout.runtime_logs
                if segment_id is None:
                    record = logging.LogRecord("timezone-test", logging.INFO, __file__, 1, "saved log", (), None)
                    record.created = 1792967400 if zones[0] == "UTC0" else 1793028600  # Cross midnight when the offset changes.
                    logs.append(record)
                    row = logs.database.connection().execute("SELECT * FROM log_segments").fetchone()
                    segment_id = row["id"]
                    logs.seal()
                else:
                    row = logs.database.connection().execute("SELECT * FROM log_segments WHERE id=?", (segment_id,)).fetchone()
                    assert row is not None
                    expected_day = "2026-10-25" if zones[0] == "UTC0" else "2026-10-26"
                    assert row["relative_path"].startswith(f"{expected_day}/")
                    assert "saved log" in (logs.root / row["relative_path"]).read_text()
            finally:
                store.close()
    finally:
        if original_timezone is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original_timezone
        time.tzset()


@pytest.mark.parametrize("relative_path", [
    "../../outside.jsonl", "/outside.jsonl", "2026-02-30/{id}.jsonl",
    "2026-10-25/../{id}.jsonl", "2026-10-25//{id}.jsonl",
    "2026-10-25/wrong.jsonl",
])
def test_runtime_logs_reject_index_path_that_escapes_log_root(
    tmp_path: Path, relative_path: str,
) -> None:
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    outside = tmp_path / "outside.jsonl"
    outside.write_text("must remain\n", encoding="utf-8")
    outside.chmod(0o600)
    database = AuxiliaryDatabase(
        data, name=LOGS_DATABASE_NAME, schema_sql=LOGS_SCHEMA_SQL
    )
    database.initialize()
    try:
        segment_id = "1-123-" + "a" * 32
        with database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO log_segments (
                    id, relative_path, first_timestamp, last_timestamp,
                    record_count, stored_bytes, chain_sha256, state,
                    created_at, updated_at
                ) VALUES (
                    ?, ?, 1, 1,
                    1, 12, ?, 'sealed', 1, 1
                )
                """,
                (segment_id, relative_path.format(id=segment_id), "0" * 64),
            )

        with pytest.raises(StorageError, match="log_segment_path_invalid"):
            RuntimeLogStore(data, database, max_total_bytes=1)
        assert outside.read_text(encoding="utf-8") == "must remain\n"
    finally:
        database.close()








def test_session_delete_removes_history_and_unreferenced_snapshot_blobs(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    root = _workspace(tmp_path)
    store = SessionStore(data)
    store.initialize()
    try:
        session = store.create_session(str(root))
        run, _item = store.create_run(session["id"], "inspect")
        RuntimeEngine(
            store,
            ScriptedModel([ModelResponse(text="done")]),
            lambda _message: None,
        ).run(run["id"], threading.Event())
        store.project_thread_history()
        history = sqlite3.connect(data / THREAD_HISTORY_DATABASE_NAME)
        history.row_factory = sqlite3.Row
        try:
            history_row = history.execute(
                "SELECT relative_path FROM history_files WHERE session_id = ?",
                (session["id"],),
            ).fetchone()
            assert history_row is not None
            history_path = data / "history" / history_row["relative_path"]
        finally:
            history.close()
        blob_paths = tuple((data / "blobs").glob("**/*.json.gz"))
        assert blob_paths
        assert history_path.is_file()

        store.delete_session(session["id"])

        assert not history_path.exists()
        assert not any(path.exists() for path in blob_paths)
        history = sqlite3.connect(data / THREAD_HISTORY_DATABASE_NAME)
        try:
            assert history.execute(
                "SELECT COUNT(*) FROM history_events WHERE session_id = ?",
                (session["id"],),
            ).fetchone()[0] == 0
        finally:
            history.close()
    finally:
        store.close()


def test_missing_context_blob_fails_closed_as_persistence_corruption(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    root = _workspace(tmp_path)
    store = SessionStore(data)
    store.initialize()
    try:
        session = store.create_session(str(root))
        run, _item = store.create_run(session["id"], "inspect")
        RuntimeEngine(
            store,
            ScriptedModel([ModelResponse(text="done")]),
            lambda _message: None,
        ).run(run["id"], threading.Event())
        row = store.database.connection().execute(
            "SELECT id, snapshot_json FROM context_snapshots "
            "WHERE run_id = ? ORDER BY created_at DESC LIMIT 1",
            (run["id"],),
        ).fetchone()
        assert row is not None
        reference = JsonBlobReference.from_json(str(row["snapshot_json"]))
        assert reference is not None
        (data / "blobs" / reference.relative_path).unlink()

        try:
            store.context_snapshot_repository().read(str(row["id"]))
        except PersistenceCorruptionError as error:
            assert error.code == "persistence_record_invalid"
        else:
            raise AssertionError("missing context blob was accepted")
    finally:
        store.close()


def test_invalid_blob_reference_enters_health_only_on_restart(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    root = _workspace(tmp_path)
    store = SessionStore(data)
    store.initialize()
    session = store.create_session(str(root))
    run, _item = store.create_run(session["id"], "inspect")
    RuntimeEngine(
        store,
        ScriptedModel([ModelResponse(text="done")]),
        lambda _message: None,
    ).run(run["id"], threading.Event())
    store.close()

    state = sqlite3.connect(data / STATE_DATABASE_NAME)
    state.execute(
        "UPDATE context_snapshots SET snapshot_json = ? WHERE run_id = ?",
        ('{"$eidosBlob":{"version":999}}', run["id"]),
    )
    state.commit()
    state.close()

    reopened = SessionStore(data)
    try:
        reopened.initialize()
        assert reopened.health_state == "health_only"
    finally:
        reopened.close()


def test_restart_defers_orphan_blob_collection_until_maintenance(tmp_path: Path) -> None:
    data = tmp_path / "data"
    store = SessionStore(data)
    store.initialize()
    reference = JsonBlobReference.from_json(
        store.database.json_blobs.put_json("orphan", '{"unused":true}')
    )
    assert reference is not None
    orphan = data / "blobs" / reference.relative_path
    store.close()

    reopened = SessionStore(data)
    try:
        reopened.initialize()
        assert reopened.health_state == "ready"
        assert orphan.is_file()
        reopened.collect_unreferenced_blobs()
        assert not orphan.exists()
    finally:
        reopened.close()


@pytest.mark.parametrize("already_redacted", [True, False])
def test_revoked_snapshots_do_not_restart_externalization_or_compaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, already_redacted: bool,
) -> None:
    from eidos_runtime.db import layout
    from eidos_runtime.memory.repository import MemoryRejected

    data = tmp_path / "data"
    store = SessionStore(data)
    store.initialize()
    session = store.create_session(str(_workspace(tmp_path)))
    run, _ = store.create_run(session["id"], "inspect")
    RuntimeEngine(
        store, ScriptedModel([ModelResponse(text="done")]), lambda _message: None,
    ).run(run["id"], threading.Event())
    tables = ("context_snapshots", "step_resolution_snapshots")
    with store.database.transaction() as connection:
        for table in tables:
            if already_redacted:
                connection.execute(
                    f"UPDATE {table} SET memory_revoked=1, snapshot_json='{{}}'"
                )
            else:
                connection.execute(f"UPDATE {table} SET memory_revoked=1")
            assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 1
    store.close()

    markers: list[bool] = []
    original_compaction = layout._retry_state_compaction

    def record_compaction(state: layout.StateDatabase) -> None:
        assert state.data_directory is not None
        markers.append((state.data_directory / layout.STATE_COMPACTION_MARKER_NAME).exists())
        original_compaction(state)

    monkeypatch.setattr(layout, "_retry_state_compaction", record_compaction)
    for restart in range(2):
        reopened = SessionStore(data)
        try:
            reopened.initialize()
            assert reopened.health_state == "ready"
            snapshot_id = reopened.database.connection().execute(
                "SELECT id FROM context_snapshots WHERE memory_revoked=1"
            ).fetchone()[0]
            with pytest.raises(MemoryRejected, match="memory_snapshot_revoked"):
                reopened.context_snapshot_repository().read(snapshot_id)
            assert reopened.read_step_resolution_snapshots(run["id"]) == ()
            for table in tables:
                stored = reopened.database.connection().execute(
                    f"SELECT snapshot_json FROM {table} WHERE memory_revoked=1"
                ).fetchone()[0]
                if already_redacted or restart:
                    assert stored == "{}"
                else:
                    assert JsonBlobReference.from_json(stored) is not None

            collections: list[bool] = []

            def collect() -> None:
                collections.append(True)
                reopened.collect_unreferenced_blobs()

            reopened.database.memory.on_cleanup = collect
            reopened.database.memory.cleanup()
            assert len(collections) == (1 if not already_redacted and restart == 0 else 0)
            for table in tables:
                assert reopened.database.connection().execute(
                    f"SELECT snapshot_json FROM {table} WHERE memory_revoked=1"
                ).fetchone()[0] == "{}"
        finally:
            reopened.close()
    assert markers == [False, False]


def test_snapshot_externalization_supports_legacy_tables_without_revocation(
    tmp_path: Path,
) -> None:
    from eidos_runtime.db.layout import _externalize_state_snapshots

    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    connection = sqlite3.connect(":memory:")
    for table in ("context_snapshots", "step_resolution_snapshots"):
        connection.execute(f"CREATE TABLE {table} (id TEXT PRIMARY KEY, snapshot_json TEXT)")
        connection.execute(f"INSERT INTO {table} VALUES ('legacy', '{{}}')")
    connection.commit()
    blobs = JsonBlobStore(data)

    class LegacyState:
        data_directory = data
        json_blobs = blobs
        lock = threading.RLock()

        def connection(self) -> sqlite3.Connection:
            return connection

        def transaction(self) -> sqlite3.Connection:
            return connection

    try:
        _externalize_state_snapshots(LegacyState(), blobs)
        for table in ("context_snapshots", "step_resolution_snapshots"):
            stored = connection.execute(f"SELECT snapshot_json FROM {table}").fetchone()[0]
            assert JsonBlobReference.from_json(stored) is not None
    finally:
        connection.close()


def test_snapshot_externalization_batches_queries_and_resumes_after_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from eidos_runtime.db.layout import _externalize_state_snapshots

    store = SessionStore(tmp_path / "data")
    store.initialize()
    try:
        session = store.create_session(str(_workspace(tmp_path)))
        run, _ = store.create_run(session["id"], "inspect")
        RuntimeEngine(
            store, ScriptedModel([ModelResponse(text="done")]), lambda _message: None,
        ).run(run["id"], threading.Event())
        seed = store.database.connection().execute("SELECT * FROM context_snapshots LIMIT 1").fetchone()
        assert seed is not None
        plain = store.context_snapshot_repository().read(seed["id"]).model_dump_json()
        with store.database.transaction() as connection:
            for index in range(513):
                values = dict(seed)
                values.update(id=f"legacy-{index}", model_attempt_id=f"legacy-attempt-{index}", snapshot_json=plain)
                connection.execute(
                    f"INSERT INTO context_snapshots ({','.join(values)}) VALUES ({','.join('?' for _ in values)})",
                    tuple(values.values()),
                )

        blobs = store.database.json_blobs
        original_put = blobs.put_json
        puts = 0

        def fail_second_put(kind: str, value: str) -> str:
            nonlocal puts
            puts += 1
            if puts == 2:
                raise OSError("interrupted migration")
            return original_put(kind, value)

        with monkeypatch.context() as context:
            context.setattr(blobs, "put_json", fail_second_put)
            with pytest.raises(OSError, match="interrupted migration"):
                _externalize_state_snapshots(store.database, blobs)
        migrated = store.database.connection().execute(
            "SELECT snapshot_json FROM context_snapshots WHERE id = 'legacy-0'"
        ).fetchone()[0]
        assert JsonBlobReference.from_json(migrated) is not None

        statements: list[str] = []
        store.database.connection().set_trace_callback(statements.append)
        try:
            _externalize_state_snapshots(store.database, blobs)
        finally:
            store.database.connection().set_trace_callback(None)
        assert sum(statement.startswith("SELECT id, snapshot_json FROM") for statement in statements) == 2
        assert not any("WHERE id =" in statement and statement.startswith("SELECT") for statement in statements)
        rows = store.database.connection().execute(
            "SELECT snapshot_json FROM context_snapshots WHERE id LIKE 'legacy-%'"
        ).fetchall()
        assert len(rows) == 513
        for row in rows:
            reference = JsonBlobReference.from_json(row[0])
            assert reference is not None
            assert blobs.read(reference) == plain
    finally:
        store.close()


def test_cancelled_blob_reference_scan_never_deletes_with_partial_references(tmp_path: Path) -> None:
    blobs = JsonBlobStore(tmp_path / "data")
    stored = blobs.put_json("context-item", '{"live":true}')
    orphan = blobs.put_json("orphan", '{"unused":true}')
    cancel = threading.Event()

    def references():
        yield stored
        cancel.set()
        yield stored

    assert blobs.garbage_collect(references(), cancel=cancel) == 0
    assert blobs.read_json(stored, expected_kind="context-item") == '{"live":true}'
    assert blobs.read_json(orphan, expected_kind="orphan") == '{"unused":true}'
    cancel.clear()
    assert blobs.garbage_collect([stored], cancel=cancel) == 1
    assert blobs.read_json(stored, expected_kind="context-item") == '{"live":true}'


def test_cancelled_blob_deletion_stops_and_keeps_remaining_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    blobs = JsonBlobStore(tmp_path / "data")
    for index in range(3):
        blobs.put_json("orphan", json.dumps({"index": index}))
    cancel = threading.Event()
    original_unlink = Path.unlink

    def stop_after_delete(path: Path, *args, **kwargs) -> None:
        original_unlink(path, *args, **kwargs)
        cancel.set()

    monkeypatch.setattr(Path, "unlink", stop_after_delete)
    assert blobs.garbage_collect([], cancel=cancel) == 1
    assert len(tuple(blobs.root.rglob("*.json.gz"))) == 2


def test_blob_maintenance_preserves_all_persisted_reference_sources(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "data")
    store.initialize()
    try:
        session = store.create_session(str(_workspace(tmp_path)))
        run, _ = store.create_run(session["id"], "inspect")
        RuntimeEngine(
            store, ScriptedModel([ModelResponse(text="done")]), lambda _message: None,
        ).run(run["id"], threading.Event())
        blobs = store.database.json_blobs
        environment = blobs.put_json("mcp-env", '{"FIXTURE":"local"}')
        input_reference = blobs.put_json("input-reference", '{"input":"local"}')
        replay = blobs.put_json("model-response-replay", '{"reasoning":"local"}')
        orphan = JsonBlobReference.from_json(blobs.put_json("orphan", '{"unused":true}'))
        assert orphan is not None
        with store.database.transaction() as connection:
            connection.execute(
                "INSERT INTO manual_mcp_servers VALUES ('fixture','/usr/bin/true','[]',?,'[]',?,'connector',10,10,?,1,1)",
                (environment, str(tmp_path), "0" * 64),
            )
            connection.execute(
                "INSERT INTO input_references VALUES ('input',?,'{}',1,1)", (input_reference,),
            )
            updated = connection.execute(
                "UPDATE model_attempts SET response_replay_json = ? "
                "WHERE id IN (SELECT model_attempt_id FROM context_snapshots WHERE run_id = ?)",
                (replay, run["id"]),
            )
            assert updated.rowcount == 1
        snapshot_id = store.database.connection().execute(
            "SELECT id FROM context_snapshots WHERE run_id = ?", (run["id"],),
        ).fetchone()[0]
        expected = store.context_snapshot_repository().read(snapshot_id)
        store.collect_unreferenced_blobs()
        assert not (blobs.root / orphan.relative_path).exists()
        assert blobs.read_json(environment, expected_kind="mcp-env") == '{"FIXTURE":"local"}'
        assert blobs.read_json(input_reference, expected_kind="input-reference") == '{"input":"local"}'
        assert blobs.read_json(replay, expected_kind="model-response-replay") == '{"reasoning":"local"}'
        assert store.context_snapshot_repository().read(snapshot_id) == expected
    finally:
        store.close()


def test_invalid_context_manifest_aborts_collection_before_deletion(tmp_path: Path) -> None:
    blobs = JsonBlobStore(tmp_path / "data")
    invalid = blobs.put_json("context-snapshot-v2", '{"invalid":true}')
    orphan = blobs.put_json("orphan", '{"unused":true}')
    with pytest.raises(JsonBlobCorruptionError, match="context manifest is invalid"):
        blobs.garbage_collect([invalid])
    assert blobs.read_json(orphan, expected_kind="orphan") == '{"unused":true}'


def test_restart_removes_history_left_after_state_session_delete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = tmp_path / "data"
    root = _workspace(tmp_path)
    store = SessionStore(data)
    store.initialize()
    session = store.create_session(str(root))
    store.project_thread_history()
    history = sqlite3.connect(data / THREAD_HISTORY_DATABASE_NAME)
    relative_path = history.execute(
        "SELECT relative_path FROM history_files WHERE session_id = ?",
        (session["id"],),
    ).fetchone()[0]
    history.close()
    history_path = data / "history" / relative_path
    assert history_path.is_file()
    layout = store._persistence_layout
    assert layout is not None and layout.thread_history_store is not None
    monkeypatch.setattr(
        layout.thread_history_store,
        "delete_session",
        lambda _session_id: (_ for _ in ()).throw(OSError("injected")),
    )
    with pytest.raises(OSError, match="injected"):
        store.delete_session(session["id"])
    store.close()

    reopened = SessionStore(data)
    try:
        reopened.initialize()
        assert reopened.health_state == "ready"
        assert not history_path.exists()
        history = sqlite3.connect(data / THREAD_HISTORY_DATABASE_NAME)
        try:
            assert history.execute(
                "SELECT COUNT(*) FROM history_events WHERE session_id = ?",
                (session["id"],),
            ).fetchone()[0] == 0
        finally:
            history.close()
    finally:
        reopened.close()

from __future__ import annotations

import hashlib
from pathlib import Path
import json
import logging
import sqlite3
import threading

import pytest

from eidos_runtime.db.errors import StorageError
from eidos_runtime.db.layout import (
    AuxiliaryDatabase,
    LEGACY_STATE_DATABASE_NAME,
    LOGS_DATABASE_NAME,
    LOGS_SCHEMA_SQL,
    MEMORIES_DATABASE_NAME,
    REPOSITORY_DATABASE_NAME,
    STATE_DATABASE_NAME,
    THREAD_HISTORY_DATABASE_NAME,
)
from eidos_runtime.db.json_blobs import JsonBlobReference
from eidos_runtime.db.runtime_logs import RuntimeLogStore
from eidos_runtime.db.database import Database
from eidos_runtime.db.schema import SCHEMA_SQL, SCHEMA_VERSION
from eidos_runtime.db.schema import V5_SCHEMA_SQL
from eidos_runtime.db.storage import SessionStore
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
        assert (data / MEMORIES_DATABASE_NAME).is_file()
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


def test_runtime_logs_reject_index_path_that_escapes_log_root(
    tmp_path: Path,
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
        with database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO log_segments (
                    id, relative_path, first_timestamp, last_timestamp,
                    record_count, stored_bytes, chain_sha256, state,
                    created_at, updated_at
                ) VALUES (
                    'malicious', '../../outside.jsonl', 1, 1,
                    1, 12, ?, 'sealed', 1, 1
                )
                """,
                ("0" * 64,),
            )

        with pytest.raises(StorageError, match="log_segment_path_invalid"):
            RuntimeLogStore(data, database, max_total_bytes=1)
        assert outside.read_text(encoding="utf-8") == "must remain\n"
    finally:
        database.close()


def test_memories_store_metadata_in_sqlite_and_content_on_disk(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    store = SessionStore(data)
    store.initialize()
    try:
        memory = store.memory_store().put(
            memory_id="memory-1",
            kind="run-summary",
            content="# Confirmed facts\n\nRepository storage is derived.\n",
            metadata={"runId": "run-1"},
        )
        assert store.memory_store().read("memory-1") == memory
        assert memory.content.startswith("# Confirmed facts")
    finally:
        store.close()

    metadata = sqlite3.connect(data / MEMORIES_DATABASE_NAME)
    metadata.row_factory = sqlite3.Row
    try:
        row = metadata.execute(
            "SELECT * FROM memory_records WHERE id = 'memory-1'"
        ).fetchone()
        assert row is not None
        assert row["kind"] == "run-summary"
        assert row["content_sha256"] == memory.content_sha256
        path = data / "memories" / row["relative_path"]
        assert path.read_text(encoding="utf-8") == memory.content
    finally:
        metadata.close()


def test_memory_updates_collect_content_after_last_reference_moves(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    store = SessionStore(data)
    store.initialize()
    try:
        memories = store.memory_store()
        first = memories.put(
            memory_id="memory-1",
            kind="run-summary",
            content="shared content",
            metadata={},
        )
        memories.put(
            memory_id="memory-2",
            kind="run-summary",
            content="shared content",
            metadata={},
        )
        first_path = (
            data
            / "memories"
            / "content"
            / first.content_sha256[:2]
            / f"{first.content_sha256}.md"
        )
        assert first_path.is_file()

        memories.put(
            memory_id="memory-1",
            kind="run-summary",
            content="replacement one",
            metadata={},
        )
        assert first_path.is_file()
        memories.put(
            memory_id="memory-2",
            kind="run-summary",
            content="replacement two",
            metadata={},
        )
        assert not first_path.exists()
    finally:
        store.close()


def test_memories_v1_migrates_to_shared_content_schema(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    state = Database(data)
    state.initialize()
    state.close()
    content = "legacy memory"
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    relative_path = f"content/{digest[:2]}/{digest}.md"
    content_path = data / "memories" / relative_path
    content_path.parent.mkdir(mode=0o700, parents=True)
    (data / "memories").chmod(0o700)
    (data / "memories" / "content").chmod(0o700)
    content_path.parent.chmod(0o700)
    content_path.write_text(content, encoding="utf-8")
    content_path.chmod(0o600)
    memory_database = data / MEMORIES_DATABASE_NAME
    connection = sqlite3.connect(memory_database)
    connection.executescript(
        """
        CREATE TABLE memory_records (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            relative_path TEXT NOT NULL UNIQUE,
            content_sha256 TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );
        CREATE INDEX memory_records_kind_time
        ON memory_records(kind, updated_at DESC);
        PRAGMA user_version = 1;
        """
    )
    connection.execute(
        "INSERT INTO memory_records VALUES (?, ?, ?, ?, '{}', 1, 1)",
        ("legacy", "run-summary", relative_path, digest),
    )
    connection.commit()
    connection.close()
    memory_database.chmod(0o600)

    store = SessionStore(data)
    store.initialize()
    try:
        assert store.health_state == "ready"
        assert store.memory_store().read("legacy").content == content
        duplicate = store.memory_store().put(
            memory_id="duplicate",
            kind="run-summary",
            content=content,
            metadata={},
        )
        assert duplicate.content_sha256 == digest
        assert store._persistence_layout is not None
        assert store._persistence_layout.memories.connection().execute(
            "PRAGMA user_version"
        ).fetchone()[0] == 2
    finally:
        store.close()


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

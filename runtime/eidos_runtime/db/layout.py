from __future__ import annotations

from contextlib import AbstractContextManager, contextmanager
import os
from pathlib import Path
import sqlite3
import stat
import threading
from typing import Callable, Iterator, Mapping, Protocol, TypeVar

from eidos_runtime.db.errors import StorageError
from eidos_runtime.db.schema import REPOSITORY_SCHEMA_SQL
from eidos_runtime.db.schema import (
    SCHEMA_VERSION,
    V5_SCHEMA_VERSION,
    V5_TO_V6_MIGRATION_SQL,
    V6_SCHEMA_VERSION,
    V6_TO_V7_MIGRATION_SQL,
)
from eidos_runtime.db.thread_history import ThreadHistoryStore
from eidos_runtime.db.runtime_logs import RuntimeLogStore
from eidos_runtime.db.memories import MemoryStore
from eidos_runtime.db.json_blobs import (
    JsonBlobReference,
    JsonBlobStore,
)


STATE_DATABASE_NAME = "state.sqlite"
LEGACY_STATE_DATABASE_NAME = "eidos.db"
REPOSITORY_DATABASE_NAME = "repository.sqlite"
THREAD_HISTORY_DATABASE_NAME = "thread_history.sqlite"
LOGS_DATABASE_NAME = "logs.sqlite"
MEMORIES_DATABASE_NAME = "memories.sqlite"
STATE_COMPACTION_MARKER_NAME = "state-compaction.pending"

_AUXILIARY_SCHEMA_VERSION = 1
LOGS_SCHEMA_VERSION = 2
_MEMORIES_SCHEMA_VERSION = 2
_REPOSITORY_TABLES = (
    "repository_snapshots",
    "repository_files",
    "repository_directories",
    "repository_index_generations",
    "repository_parsed_files",
    "repository_symbols",
    "repository_imports",
    "repository_references",
    "repository_chunks",
    "repository_diagnostics",
)
_REPOSITORY_FTS_COLUMNS = (
    "index_snapshot_id",
    "record_id",
    "path",
    "symbol",
    "body",
    "kind",
    "start_line",
    "end_line",
    "file_hash",
)

THREAD_HISTORY_SCHEMA_SQL = """
CREATE TABLE history_files (
    session_id TEXT PRIMARY KEY,
    relative_path TEXT NOT NULL UNIQUE,
    committed_bytes INTEGER NOT NULL DEFAULT 0 CHECK (committed_bytes >= 0),
    last_event_id INTEGER NOT NULL DEFAULT 0 CHECK (last_event_id >= 0),
    updated_at INTEGER NOT NULL
);

CREATE TABLE history_events (
    event_id INTEGER PRIMARY KEY,
    session_id TEXT,
    run_id TEXT,
    event_type TEXT NOT NULL,
    occurred_at INTEGER NOT NULL,
    file_offset INTEGER NOT NULL CHECK (file_offset >= 0),
    record_bytes INTEGER NOT NULL CHECK (record_bytes > 0),
    record_sha256 TEXT NOT NULL,
    relative_path TEXT NOT NULL
);

CREATE INDEX history_events_session_order
ON history_events(session_id, event_id);

CREATE INDEX history_events_run_order
ON history_events(run_id, event_id);
"""

LOGS_SCHEMA_SQL = """
CREATE TABLE log_segments (
    id TEXT PRIMARY KEY,
    relative_path TEXT NOT NULL UNIQUE,
    first_timestamp INTEGER NOT NULL,
    last_timestamp INTEGER NOT NULL,
    record_count INTEGER NOT NULL CHECK (record_count >= 0),
    stored_bytes INTEGER NOT NULL CHECK (stored_bytes >= 0),
    chain_sha256 TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('active', 'sealed')),
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX log_segments_time
ON log_segments(last_timestamp DESC);
"""

LOGS_V1_TO_V2_MIGRATION_SQL = """
ALTER TABLE log_segments RENAME COLUMN content_sha256 TO chain_sha256;
"""

LOGS_V1_CHAIN_TO_V2_MIGRATION_SQL = """
SELECT 1;
"""

MEMORIES_SCHEMA_SQL = """
CREATE TABLE memory_records (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX memory_records_kind_time
ON memory_records(kind, updated_at DESC);

CREATE INDEX memory_records_content
ON memory_records(content_sha256);
"""

MEMORIES_V1_TO_V2_SQL = """
CREATE TABLE memory_records_v2 (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

INSERT INTO memory_records_v2 SELECT * FROM memory_records;
DROP TABLE memory_records;
ALTER TABLE memory_records_v2 RENAME TO memory_records;

CREATE INDEX memory_records_kind_time
ON memory_records(kind, updated_at DESC);

CREATE INDEX memory_records_content
ON memory_records(content_sha256);
"""

T = TypeVar("T")


class StateDatabase(Protocol):
    data_directory: Path | None
    lock: threading.RLock
    json_blobs: JsonBlobStore

    def connection(self) -> sqlite3.Connection: ...

    def transaction(self) -> AbstractContextManager[sqlite3.Connection]: ...


class AuxiliaryDatabase:
    """One private SQLite database with a fixed, single-purpose schema."""

    def __init__(
        self,
        data_directory: Path,
        *,
        name: str,
        schema_sql: str,
        schema_version: int = _AUXILIARY_SCHEMA_VERSION,
        migrations: Mapping[int, str] | None = None,
    ) -> None:
        self.data_directory = data_directory
        self.name = name
        self.schema_sql = schema_sql
        self.schema_version = schema_version
        self.migrations = dict(migrations or {})
        self.lock = threading.RLock()
        self._connection: sqlite3.Connection | None = None

    @property
    def path(self) -> Path:
        return self.data_directory / self.name

    @property
    def raw_connection(self) -> sqlite3.Connection | None:
        return self._connection

    def initialize(self) -> None:
        with self.lock:
            if self._connection is not None:
                raise StorageError("storage is already initialized")
            _prepare_private_database(self.path)
            connection = sqlite3.connect(self.path, check_same_thread=False)
            try:
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute("PRAGMA journal_mode = WAL")
                connection.execute("PRAGMA busy_timeout = 5000")
                tables = _table_names(connection)
                revision = int(
                    connection.execute("PRAGMA user_version").fetchone()[0]
                )
                if not tables and revision == 0:
                    connection.execute("PRAGMA auto_vacuum = INCREMENTAL")
                    connection.executescript(
                        "BEGIN IMMEDIATE;\n"
                        + self.schema_sql
                        + f"\nPRAGMA user_version = {self.schema_version};\nCOMMIT;"
                    )
                elif revision != self.schema_version:
                    migration = self._migration_for(connection, revision)
                    if migration is None:
                        raise StorageError("schema_revision_unsupported")
                    try:
                        connection.executescript(
                            "BEGIN IMMEDIATE;\n"
                            + migration
                            + f"\nPRAGMA user_version = {self.schema_version};\n"
                            "COMMIT;"
                        )
                    except sqlite3.Error as error:
                        try:
                            connection.execute("ROLLBACK")
                        except sqlite3.Error:
                            pass
                        raise StorageError("schema_migration_failed") from error
                if int(connection.execute("PRAGMA auto_vacuum").fetchone()[0]) == 0:
                    connection.execute("PRAGMA auto_vacuum = INCREMENTAL")
                    connection.execute("VACUUM")
                _verify_connection(connection)
                self._connection = connection
            except Exception:
                connection.close()
                raise

    def _migration_for(
        self, _connection: sqlite3.Connection, revision: int
    ) -> str | None:
        return self.migrations.get(revision)

    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise StorageError("storage is not initialized")
        return self._connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.lock, self.connection() as connection:
            yield connection

    def execute_idempotent(
        self,
        action: Callable[[sqlite3.Connection], T],
        *,
        operation_id: str | None = None,
        operation_scope: str | None = None,
        operation_request: dict[str, object] | None = None,
    ) -> T:
        if (
            operation_id is not None
            or operation_scope is not None
            or operation_request is not None
        ):
            raise StorageError("auxiliary_database_operation_unsupported")
        with self.transaction() as connection:
            result = action(connection)
        with self.lock:
            self.connection().execute("PRAGMA incremental_vacuum(1024)")
        return result

    def close(self) -> None:
        with self.lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None


class RepositoryDatabase(AuxiliaryDatabase):
    """The rebuildable repository-intelligence database."""

    def __init__(self, data_directory: Path) -> None:
        super().__init__(
            data_directory,
            name=REPOSITORY_DATABASE_NAME,
            schema_sql=REPOSITORY_SCHEMA_SQL,
        )

    def initialize(self) -> None:
        _prepare_private_directory(self.data_directory)
        super().initialize()


class LogsDatabase(AuxiliaryDatabase):
    """The bounded JSONL log index with its own schema revisions."""

    def __init__(self, data_directory: Path) -> None:
        super().__init__(
            data_directory,
            name=LOGS_DATABASE_NAME,
            schema_sql=LOGS_SCHEMA_SQL,
            schema_version=LOGS_SCHEMA_VERSION,
            migrations={1: LOGS_V1_TO_V2_MIGRATION_SQL},
        )

    def _migration_for(
        self, connection: sqlite3.Connection, revision: int
    ) -> str | None:
        migration = super()._migration_for(connection, revision)
        if revision != 1:
            return migration
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(log_segments)")
        }
        if "chain_sha256" in columns and "content_sha256" not in columns:
            return LOGS_V1_CHAIN_TO_V2_MIGRATION_SQL
        return migration


class PersistenceLayout:
    """Own all non-state databases and one-time vertical data extraction."""

    def __init__(
        self, data_directory: Path, *, json_blobs: JsonBlobStore
    ) -> None:
        self.data_directory = data_directory
        self.json_blobs = json_blobs
        self.repository = RepositoryDatabase(data_directory)
        self.thread_history = AuxiliaryDatabase(
            data_directory,
            name=THREAD_HISTORY_DATABASE_NAME,
            schema_sql=THREAD_HISTORY_SCHEMA_SQL,
        )
        self.logs = LogsDatabase(data_directory)
        self.memories = AuxiliaryDatabase(
            data_directory,
            name=MEMORIES_DATABASE_NAME,
            schema_sql=MEMORIES_SCHEMA_SQL,
            schema_version=_MEMORIES_SCHEMA_VERSION,
            migrations={1: MEMORIES_V1_TO_V2_SQL},
        )
        self.thread_history_store: ThreadHistoryStore | None = None
        self.runtime_logs: RuntimeLogStore | None = None
        self.memory_store: MemoryStore | None = None

    def initialize(self, state: StateDatabase) -> None:
        _extract_repository_database(state, self.repository.path)
        _externalize_state_snapshots(state, self.json_blobs)
        _migrate_state_schema(state)
        self.garbage_collect_blobs(state)
        _retry_state_compaction(state)
        initialized: list[AuxiliaryDatabase] = []
        try:
            for database in (
                self.repository,
                self.thread_history,
                self.logs,
                self.memories,
            ):
                database.initialize()
                initialized.append(database)
            self.thread_history_store = ThreadHistoryStore(
                self.data_directory, self.thread_history
            )
            self.runtime_logs = RuntimeLogStore(
                self.data_directory, self.logs
            )
            self.memory_store = MemoryStore(
                self.data_directory, self.memories
            )
            self.thread_history_store.catch_up(state)
        except Exception:
            self.thread_history_store = None
            self.runtime_logs = None
            self.memory_store = None
            for database in reversed(initialized):
                database.close()
            raise

    def close(self) -> None:
        self.thread_history_store = None
        self.runtime_logs = None
        self.memory_store = None
        for database in (
            self.memories,
            self.logs,
            self.thread_history,
            self.repository,
        ):
            database.close()

    def garbage_collect_blobs(self, state: StateDatabase) -> int:
        with state.lock, self.json_blobs.lock:
            references: list[str] = []
            connection = state.connection()
            for table in ("context_snapshots", "step_resolution_snapshots"):
                if table not in _table_names(connection):
                    continue
                references.extend(
                    str(row[0])
                    for row in connection.execute(
                        f"SELECT snapshot_json FROM {table}"
                    )
                )
            return self.json_blobs.garbage_collect(references)


def migrate_legacy_state_database(data_directory: Path) -> None:
    """Move the legacy single-database file after checkpointing its WAL."""

    legacy = data_directory / LEGACY_STATE_DATABASE_NAME
    state = data_directory / STATE_DATABASE_NAME
    if state.exists():
        if legacy.exists():
            raise StorageError("storage_layout_conflict")
        return
    if not legacy.exists():
        return
    _verify_private_database(legacy)
    connection = sqlite3.connect(legacy)
    try:
        connection.execute("PRAGMA busy_timeout = 5000")
        checkpoint = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if checkpoint is None or int(checkpoint[0]) != 0:
            raise StorageError("state_checkpoint_failed")
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise StorageError("database_corrupt")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise StorageError("foreign_key_violation")
    finally:
        connection.close()
    os.replace(legacy, state)
    _fsync_directory(data_directory)
    for suffix in ("-wal", "-shm"):
        sidecar = data_directory / f"{LEGACY_STATE_DATABASE_NAME}{suffix}"
        if sidecar.exists() and not sidecar.is_symlink():
            sidecar.unlink()


def _extract_repository_database(
    state: StateDatabase,
    repository_path: Path,
) -> None:
    state_connection = state.connection()
    if "repository_snapshots" not in _table_names(state_connection):
        return
    row = state_connection.execute(
        "SELECT COUNT(*) FROM repository_snapshots"
    ).fetchone()
    if row is None or int(row[0]) == 0:
        return

    temporary = repository_path.with_name(repository_path.name + ".migrating")
    if temporary.exists():
        _verify_private_database(temporary)
        temporary.unlink()
    _prepare_private_database(temporary)
    target = sqlite3.connect(temporary)
    try:
        target.execute("PRAGMA foreign_keys = ON")
        target.execute("PRAGMA journal_mode = DELETE")
        target.execute("PRAGMA busy_timeout = 5000")
        target.executescript(REPOSITORY_SCHEMA_SQL)
        target.execute(f"PRAGMA user_version = {_AUXILIARY_SCHEMA_VERSION}")
        target.execute("ATTACH DATABASE ? AS legacy_state", (str(repository_path.parent / STATE_DATABASE_NAME),))
        with target:
            for table in _REPOSITORY_TABLES:
                if table == "repository_snapshots":
                    target.execute(
                        """
                        INSERT INTO main.repository_snapshots
                        SELECT creation_seq, id, repository_id, workspace_root,
                               workspace_dev, workspace_inode, workspace_uid,
                               inventory_generation, index_generation,
                               inventory_snapshot_id, inventory_snapshot_hash,
                               index_snapshot_id, index_snapshot_hash,
                               repository_map_json, grammar_versions_json,
                               CASE WHEN complete = 1
                                          AND repository_map_json IS NOT NULL
                                    THEN status ELSE 'incomplete' END,
                               CASE WHEN complete = 1
                                          AND repository_map_json IS NOT NULL
                                    THEN 1 ELSE 0 END,
                               created_at
                        FROM legacy_state.repository_snapshots
                        """
                    )
                    continue
                target.execute(
                    f"INSERT INTO main.{table} SELECT * FROM legacy_state.{table}"
                )
            columns = ", ".join(_REPOSITORY_FTS_COLUMNS)
            target.execute(
                f"INSERT INTO main.repository_fts ({columns}) "
                f"SELECT {columns} FROM legacy_state.repository_fts"
            )
            prune_repository_generations(target)
        target.execute("DETACH DATABASE legacy_state")
        _verify_connection(target)
        target.commit()
    except Exception:
        target.close()
        if temporary.exists():
            temporary.unlink()
        raise
    else:
        target.close()
    temporary.chmod(0o600)
    _fsync_file(temporary)
    os.replace(temporary, repository_path)
    _fsync_directory(repository_path.parent)

    marker = repository_path.parent / STATE_COMPACTION_MARKER_NAME
    _write_empty_marker(marker)


def _externalize_state_snapshots(
    state: StateDatabase,
    blobs: JsonBlobStore,
) -> None:
    if state.data_directory is None:
        raise StorageError("storage is not initialized")
    changed = False
    for table, kind in (
        ("context_snapshots", "context-snapshot"),
        ("step_resolution_snapshots", "step-resolution"),
    ):
        connection = state.connection()
        if table not in _table_names(connection):
            continue
        identifiers = tuple(
            str(row[0])
            for row in connection.execute(f"SELECT id FROM {table}")
        )
        for identifier in identifiers:
            row = state.connection().execute(
                f"SELECT snapshot_json FROM {table} WHERE id = ?",
                (identifier,),
            ).fetchone()
            if row is None:
                continue
            stored = str(row[0])
            reference = JsonBlobReference.from_json(stored)
            if reference is not None:
                if reference.kind != kind:
                    raise StorageError("snapshot_blob_reference_invalid")
                continue
            replacement = blobs.put_json(kind, stored)
            with state.transaction() as write_connection:
                updated = write_connection.execute(
                    f"UPDATE {table} SET snapshot_json = ? "
                    "WHERE id = ? AND snapshot_json = ?",
                    (replacement, identifier, stored),
                )
                if updated.rowcount == 1:
                    changed = True
    if changed:
        _write_empty_marker(
            state.data_directory / STATE_COMPACTION_MARKER_NAME
        )


def _migrate_state_schema(state: StateDatabase) -> None:
    revision = int(state.connection().execute("PRAGMA user_version").fetchone()[0])
    if revision == SCHEMA_VERSION:
        return
    if revision not in {V5_SCHEMA_VERSION, V6_SCHEMA_VERSION}:
        raise StorageError("schema_revision_unsupported")
    migration = V6_TO_V7_MIGRATION_SQL
    if revision == V5_SCHEMA_VERSION:
        migration = V5_TO_V6_MIGRATION_SQL + migration
    try:
        with state.lock:
            connection = state.connection()
            connection.execute("PRAGMA foreign_keys = OFF")
            connection.executescript(
                "BEGIN IMMEDIATE;\n"
                + migration
                + f"\nPRAGMA user_version = {SCHEMA_VERSION};\nCOMMIT;"
            )
            connection.execute("PRAGMA foreign_keys = ON")
    except sqlite3.Error as error:
        try:
            state.connection().execute("PRAGMA foreign_keys = ON")
        except sqlite3.Error:
            pass
        raise StorageError("schema_migration_failed") from error
    if state.data_directory is None:
        raise StorageError("storage is not initialized")
    _write_empty_marker(state.data_directory / STATE_COMPACTION_MARKER_NAME)


def prune_repository_generations(connection: sqlite3.Connection) -> None:
    connection.execute(
        "CREATE TEMP TABLE retained_repository_snapshots "
        "(id TEXT PRIMARY KEY) WITHOUT ROWID"
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO retained_repository_snapshots (id)
        SELECT candidate.id
        FROM repository_snapshots AS candidate
        WHERE candidate.creation_seq = (
            SELECT MAX(latest.creation_seq)
            FROM repository_snapshots AS latest
            WHERE latest.repository_id = candidate.repository_id
              AND latest.workspace_root = candidate.workspace_root
              AND latest.workspace_dev = candidate.workspace_dev
              AND latest.workspace_inode = candidate.workspace_inode
              AND latest.workspace_uid = candidate.workspace_uid
        )
        """
    )
    connection.execute(
        """
        INSERT OR IGNORE INTO retained_repository_snapshots (id)
        SELECT candidate.id
        FROM repository_snapshots AS candidate
        WHERE candidate.complete = 1
          AND candidate.creation_seq = (
              SELECT MAX(latest.creation_seq)
              FROM repository_snapshots AS latest
              WHERE latest.repository_id = candidate.repository_id
                AND latest.workspace_root = candidate.workspace_root
                AND latest.workspace_dev = candidate.workspace_dev
                AND latest.workspace_inode = candidate.workspace_inode
                AND latest.workspace_uid = candidate.workspace_uid
                AND latest.complete = 1
          )
        """
    )
    retained_generations = (
        "SELECT id FROM repository_index_generations "
        "WHERE repository_snapshot_id IN "
        "(SELECT id FROM retained_repository_snapshots)"
    )
    connection.execute(
        "DELETE FROM repository_fts WHERE index_snapshot_id NOT IN ("
        + retained_generations
        + ")"
    )
    for table in (
        "repository_parsed_files",
        "repository_symbols",
        "repository_imports",
        "repository_references",
        "repository_chunks",
    ):
        connection.execute(
            f"DELETE FROM {table} "
            f"WHERE repository_index_generation_id NOT IN ({retained_generations})"
        )
    connection.execute(
        "DELETE FROM repository_diagnostics "
        "WHERE repository_snapshot_id NOT IN "
        "(SELECT id FROM retained_repository_snapshots)"
    )
    connection.execute(
        "DELETE FROM repository_index_generations "
        "WHERE repository_snapshot_id NOT IN "
        "(SELECT id FROM retained_repository_snapshots)"
    )
    for table in ("repository_files", "repository_directories"):
        connection.execute(
            f"DELETE FROM {table} WHERE repository_snapshot_id NOT IN "
            "(SELECT id FROM retained_repository_snapshots)"
        )
    connection.execute(
        "DELETE FROM repository_snapshots WHERE id NOT IN "
        "(SELECT id FROM retained_repository_snapshots)"
    )
    connection.execute("DROP TABLE retained_repository_snapshots")


def _prepare_private_database(path: Path) -> None:
    if path.is_symlink():
        raise StorageError("database must not be a symlink")
    if not path.exists():
        descriptor = os.open(
            path,
            os.O_CREAT | os.O_EXCL | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        os.close(descriptor)
    _verify_private_database(path)


def _prepare_private_directory(path: Path) -> None:
    if path.is_symlink():
        raise StorageError("data directory must not be a symlink")
    if not path.exists():
        path.mkdir(mode=0o700, parents=True)
    metadata = path.stat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise StorageError("data directory owner or mode is invalid")


def _verify_private_database(path: Path) -> None:
    if path.is_symlink():
        raise StorageError("database must not be a symlink")
    metadata = path.stat()
    if not stat.S_ISREG(metadata.st_mode):
        raise StorageError("database must be a regular file")
    if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o600:
        raise StorageError("database owner or mode is invalid")


def _retry_state_compaction(state: StateDatabase) -> None:
    if state.data_directory is None:
        raise StorageError("storage is not initialized")
    marker = state.data_directory / STATE_COMPACTION_MARKER_NAME
    if not marker.exists():
        return
    if marker.is_symlink():
        raise StorageError("state_security_invalid")
    metadata = marker.stat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise StorageError("state_security_invalid")
    try:
        with state.lock:
            state.connection().execute("VACUUM")
    except sqlite3.Error:
        return
    marker.unlink()
    _fsync_directory(state.data_directory)


def _write_empty_marker(path: Path) -> None:
    if path.exists():
        if path.is_symlink():
            raise StorageError("state_security_invalid")
        return
    descriptor = os.open(
        path,
        os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(path.parent)


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def _verify_connection(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise StorageError("database_corrupt")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise StorageError("foreign_key_violation")


def _fsync_file(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = [
    "AuxiliaryDatabase",
    "LEGACY_STATE_DATABASE_NAME",
    "LOGS_DATABASE_NAME",
    "LOGS_SCHEMA_VERSION",
    "LogsDatabase",
    "MEMORIES_DATABASE_NAME",
    "PersistenceLayout",
    "REPOSITORY_DATABASE_NAME",
    "RepositoryDatabase",
    "STATE_DATABASE_NAME",
    "THREAD_HISTORY_DATABASE_NAME",
    "migrate_legacy_state_database",
    "prune_repository_generations",
]

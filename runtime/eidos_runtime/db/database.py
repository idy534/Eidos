from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import threading
import time
from typing import Callable, Generic, Iterator, TypeVar

from eidos_runtime.db.errors import (
    OperationConflictError,
    OperationInProgressError,
    StorageError,
)
from eidos_runtime.db.migration import migrate_schema
from eidos_runtime.db.schema import (
    SCHEMA_SQL,
    SCHEMA_VERSION,
)
from eidos_runtime.runtime.fault_injection import hit_fault


DATABASE_NAME = "eidos.db"
LOCK_NAME = "runtime.lock"
RESERVE_NAME = "emergency.reserve"
RESERVE_BYTES = 1024 * 1024

T = TypeVar("T")


@dataclass(frozen=True)
class CommittedMutation(Generic[T]):
    value: T
    events: tuple[dict[str, object], ...]

    @property
    def event_ids(self) -> tuple[int, ...]:
        return tuple(
            event_id
            for event in self.events
            if isinstance(
                event_id := event.get("eventId"), int
            )
            and not isinstance(event_id, bool)
        )


@dataclass(frozen=True)
class WorkspaceIdentity:
    path: Path
    device: int
    inode: int
    owner: int


class Database:
    def __init__(self, data_directory: Path | None = None) -> None:
        self.data_directory = data_directory
        self._connection: sqlite3.Connection | None = None
        self.lock = threading.RLock()
        self._lock_descriptor: int | None = None
        self.health_state = "starting"
        self.health_code: str | None = None

    def initialize(self) -> None:
        with self.lock:
            if self._connection is not None or self.health_state != "starting":
                raise StorageError("storage is already initialized")
            try:
                self._initialize()
            except (OSError, sqlite3.Error, StorageError) as error:
                self.mark_failed(error)

    def _initialize(self) -> None:
        data_directory = self.data_directory or _default_data_directory()
        if not data_directory.is_absolute():
            raise StorageError("data_directory_invalid")
        _prepare_private_directory(data_directory)
        data_directory = data_directory.resolve()
        self.data_directory = data_directory
        self._lock_descriptor = _acquire_state_lock(data_directory / LOCK_NAME)
        _prepare_reserve(data_directory / RESERVE_NAME)
        database_path = data_directory / DATABASE_NAME
        _prepare_private_database(database_path)

        connection = sqlite3.connect(database_path, check_same_thread=False)
        try:
            connection.row_factory = sqlite3.Row
            tables = _table_names(connection)
            revision = connection.execute("PRAGMA user_version").fetchone()[0]
            if (
                (tables and revision not in {SCHEMA_VERSION, SCHEMA_VERSION - 1})
                or (not tables and revision != 0)
            ):
                raise StorageError("schema_revision_unsupported")

            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA busy_timeout = 5000")
            _verify_pragmas(connection)
            if not tables:
                connection.executescript(
                    "BEGIN IMMEDIATE;\n"
                    + SCHEMA_SQL
                    + f"\nPRAGMA user_version = {SCHEMA_VERSION};\nCOMMIT;"
                )
            elif revision == SCHEMA_VERSION - 1:
                migrate_schema(
                    connection,
                    current_version=revision,
                    target_version=SCHEMA_VERSION,
                )
            _verify_integrity(connection)
            self._connection = connection
            self.health_state = "ready"
            self.health_code = None
        except Exception:
            connection.close()
            raise

    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise StorageError("storage is not initialized")
        return self._connection

    @property
    def raw_connection(self) -> sqlite3.Connection | None:
        return self._connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self.lock, self.connection() as connection:
            yield connection
            hit_fault("sqlite_commit_failure")

    def close(self) -> None:
        with self.lock:
            self._close_resources()

    def mark_failed(self, error: BaseException) -> None:
        self._close_resources()
        self.health_state = "health_only"
        self.health_code = _safe_health_code(error)

    def _close_resources(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None
        if self._lock_descriptor is not None:
            fcntl.flock(self._lock_descriptor, fcntl.LOCK_UN)
            os.close(self._lock_descriptor)
            self._lock_descriptor = None

    def health(self) -> dict[str, object]:
        result: dict[str, object] = {"state": self.health_state}
        if self.health_code is not None:
            result["code"] = self.health_code
        return result

    def execute_idempotent(
        self,
        action: Callable[[sqlite3.Connection], T],
        *,
        operation_id: str | None = None,
        operation_scope: str | None = None,
        operation_request: dict[str, object] | None = None,
    ) -> T:
        with self.transaction() as connection:
            return execute_idempotent(
                connection,
                action,
                operation_id=operation_id,
                operation_scope=operation_scope,
                operation_request=operation_request,
            )

    def execute_idempotent_committed(
        self,
        action: Callable[[sqlite3.Connection], CommittedMutation[T]],
        *,
        operation_id: str | None = None,
        operation_scope: str | None = None,
        operation_request: dict[str, object] | None = None,
        serialize_value: Callable[[T], object],
        deserialize_value: Callable[[object], T],
    ) -> CommittedMutation[T]:
        """Commit a typed mutation and retain only its durable value for replay.

        Event/outbox records are transactional facts.  An idempotent replay
        returns the same durable value without presenting already-committed
        events as newly emitted.
        """

        with self.transaction() as connection:
            return execute_idempotent_committed(
                connection,
                action,
                operation_id=operation_id,
                operation_scope=operation_scope,
                operation_request=operation_request,
                serialize_value=serialize_value,
                deserialize_value=deserialize_value,
            )

    def operation_result(
        self, operation_id: str, scope: str, request: dict[str, object]
    ) -> object | None:
        request_hash = canonical_hash(request)
        with self.lock:
            row = self.connection().execute(
                "SELECT * FROM operations WHERE id = ? AND scope = ?",
                (operation_id, scope),
            ).fetchone()
        if row is None:
            return None
        if row["request_hash"] != request_hash:
            raise OperationConflictError("operation id was reused")
        if row["status"] != "completed" or row["result_json"] is None:
            raise OperationInProgressError("operation is still in progress")
        return json.loads(row["result_json"])

    def workspace_overlaps_data(self, workspace: Path) -> bool:
        if self.data_directory is None:
            raise StorageError("storage is not initialized")
        workspace = workspace.resolve(strict=False)
        return (
            workspace == self.data_directory
            or workspace in self.data_directory.parents
            or self.data_directory in workspace.parents
        )


class Repository:
    def __init__(self, database: Database) -> None:
        self.database = database

    @property
    def lock(self) -> threading.RLock:
        return self.database.lock

    def _connection(self) -> sqlite3.Connection:
        return self.database.connection()

    def _write(
        self,
        action: Callable[[sqlite3.Connection], T],
        *,
        operation_id: str | None = None,
        operation_scope: str | None = None,
        operation_request: dict[str, object] | None = None,
    ) -> T:
        return self.database.execute_idempotent(
            action,
            operation_id=operation_id,
            operation_scope=operation_scope,
            operation_request=operation_request,
        )

    def _write_committed(
        self,
        action: Callable[[sqlite3.Connection], CommittedMutation[T]],
        *,
        operation_id: str | None = None,
        operation_scope: str | None = None,
        operation_request: dict[str, object] | None = None,
        serialize_value: Callable[[T], object],
        deserialize_value: Callable[[object], T],
    ) -> CommittedMutation[T]:
        return self.database.execute_idempotent_committed(
            action,
            operation_id=operation_id,
            operation_scope=operation_scope,
            operation_request=operation_request,
            serialize_value=serialize_value,
            deserialize_value=deserialize_value,
        )

    @staticmethod
    def _next_ordinal(connection: sqlite3.Connection, run_id: str) -> int:
        row = connection.execute(
            "SELECT COALESCE(MAX(ordinal), 0) + 1 AS next_ordinal FROM items WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        return row["next_ordinal"]

    def _workspace_overlaps_data(self, workspace: Path) -> bool:
        return self.database.workspace_overlaps_data(workspace)


def execute_idempotent(
    connection: sqlite3.Connection,
    action: Callable[[sqlite3.Connection], T],
    *,
    operation_id: str | None,
    operation_scope: str | None,
    operation_request: dict[str, object] | None,
) -> T:
    if operation_id is None:
        return action(connection)
    assert operation_scope is not None and operation_request is not None
    request_hash = canonical_hash(operation_request)
    existing = connection.execute(
        "SELECT * FROM operations WHERE id = ? AND scope = ?",
        (operation_id, operation_scope),
    ).fetchone()
    if existing is not None:
        if existing["request_hash"] != request_hash:
            raise OperationConflictError("operation id was reused")
        if existing["status"] != "completed" or existing["result_json"] is None:
            raise OperationInProgressError("operation is still in progress")
        return json.loads(existing["result_json"])
    now = now_ms()
    connection.execute(
        """
        INSERT INTO operations (id, scope, request_hash, status, created_at)
        VALUES (?, ?, ?, 'in_progress', ?)
        """,
        (operation_id, operation_scope, request_hash, now),
    )
    result = action(connection)
    connection.execute(
        """
        UPDATE operations
        SET status = 'completed', result_json = ?, completed_at = ?
        WHERE id = ? AND scope = ? AND status = 'in_progress'
        """,
        (
            json.dumps(result, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
            now_ms(),
            operation_id,
            operation_scope,
        ),
    )
    return result


def execute_idempotent_committed(
    connection: sqlite3.Connection,
    action: Callable[[sqlite3.Connection], CommittedMutation[T]],
    *,
    operation_id: str | None,
    operation_scope: str | None,
    operation_request: dict[str, object] | None,
    serialize_value: Callable[[T], object],
    deserialize_value: Callable[[object], T],
) -> CommittedMutation[T]:
    """Typed counterpart to :func:`execute_idempotent`.

    The operations table stores JSON for a durable value replay.  It does not
    store event identities: those are durable outbox facts created during the
    original transaction and must not be delivered twice.
    """

    if operation_id is None:
        return action(connection)
    assert operation_scope is not None and operation_request is not None
    request_hash = canonical_hash(operation_request)
    existing = connection.execute(
        "SELECT * FROM operations WHERE id = ? AND scope = ?",
        (operation_id, operation_scope),
    ).fetchone()
    if existing is not None:
        if existing["request_hash"] != request_hash:
            raise OperationConflictError("operation id was reused")
        if existing["status"] != "completed" or existing["result_json"] is None:
            raise OperationInProgressError("operation is still in progress")
        value = deserialize_value(json.loads(existing["result_json"]))
        return CommittedMutation(value, ())

    now = now_ms()
    connection.execute(
        """
        INSERT INTO operations (id, scope, request_hash, status, created_at)
        VALUES (?, ?, ?, 'in_progress', ?)
        """,
        (operation_id, operation_scope, request_hash, now),
    )
    mutation = action(connection)
    connection.execute(
        """
        UPDATE operations
        SET status = 'completed', result_json = ?, completed_at = ?
        WHERE id = ? AND scope = ? AND status = 'in_progress'
        """,
        (
            json.dumps(
                serialize_value(mutation.value),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
            now_ms(),
            operation_id,
            operation_scope,
        ),
    )
    return mutation


def canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def verify_integrity(connection: sqlite3.Connection) -> None:
    _verify_integrity(connection)


def _default_data_directory() -> Path:
    configured = os.environ.get("EIDOS_DATA_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".eidos"


def _safe_health_code(error: BaseException) -> str:
    if isinstance(error, StorageError):
        code = str(error)
        if code in {
            "state_locked",
            "schema_revision_unsupported",
            "schema_migration_failed",
            "database_corrupt",
            "foreign_key_violation",
            "storage_pragmas_invalid",
            "reserve_invalid",
        }:
            return code
        return "state_security_invalid"
    if isinstance(error, sqlite3.DatabaseError):
        return "database_corrupt"
    return "storage_io_error"


def _acquire_state_lock(path: Path) -> int:
    if path.is_symlink():
        raise StorageError("state_security_invalid")
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise StorageError("state_security_invalid")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise StorageError("state_locked") from None
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _prepare_reserve(path: Path) -> None:
    if path.is_symlink():
        raise StorageError("reserve_invalid")
    if not path.exists():
        descriptor = os.open(
            path,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            block = b"\xa5" * 64 * 1024
            for _ in range(RESERVE_BYTES // len(block)):
                os.write(descriptor, block)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    metadata = path.stat()
    allocated = getattr(metadata, "st_blocks", 0) * 512
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
        or metadata.st_size != RESERVE_BYTES
        or (allocated and allocated < RESERVE_BYTES)
    ):
        raise StorageError("reserve_invalid")


def _table_names(connection: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def _verify_pragmas(connection: sqlite3.Connection) -> None:
    foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
    journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
    busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]
    if foreign_keys != 1 or str(journal_mode).lower() != "wal" or busy_timeout < 5000:
        raise StorageError("storage_pragmas_invalid")


def _verify_integrity(connection: sqlite3.Connection) -> None:
    if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise StorageError("database_corrupt")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise StorageError("foreign_key_violation")


def _prepare_private_directory(path: Path) -> None:
    if path.is_symlink():
        raise StorageError("data directory must not be a symlink")
    if not path.exists():
        path.mkdir(mode=0o700, parents=True)
    metadata = path.stat()
    if not stat.S_ISDIR(metadata.st_mode):
        raise StorageError("data directory must be a directory")
    if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise StorageError("data directory owner or mode is invalid")


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
    metadata = path.stat()
    if not stat.S_ISREG(metadata.st_mode):
        raise StorageError("database must be a regular file")
    if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o600:
        raise StorageError("database owner or mode is invalid")

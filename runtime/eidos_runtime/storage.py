from __future__ import annotations

import base64
import os
from pathlib import Path
import sqlite3
import stat
import time
import uuid


DATABASE_NAME = "eidos.db"
DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 200
SESSION_CURSOR_PREFIX = "session-v1:"


class StorageError(RuntimeError):
    pass


class WorkspaceBoundaryError(ValueError):
    pass


class InvalidCursorError(ValueError):
    pass


class SessionStore:
    def __init__(self, data_directory: Path | None = None) -> None:
        self.data_directory = data_directory
        self.connection: sqlite3.Connection | None = None

    def initialize(self) -> None:
        if self.connection is not None:
            raise StorageError("storage is already initialized")

        data_directory = self.data_directory or _default_data_directory()
        if not data_directory.is_absolute():
            raise StorageError("data directory must be absolute")
        _prepare_private_directory(data_directory)
        database_path = data_directory.resolve() / DATABASE_NAME
        _prepare_private_database(database_path)

        connection = sqlite3.connect(database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                creation_seq INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT NOT NULL UNIQUE,
                workspace_root TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )
        connection.commit()
        self.connection = connection

    def close(self) -> None:
        if self.connection is None:
            return
        self.connection.close()
        self.connection = None

    def create_session(self, workspace_root: str) -> dict[str, object]:
        workspace = _canonical_workspace(workspace_root)
        session_id = str(uuid.uuid4())
        now = time.time_ns() // 1_000_000
        connection = self._connection()
        connection.execute(
            """
            INSERT INTO sessions (id, workspace_root, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            """,
            (session_id, str(workspace), now, now),
        )
        connection.commit()
        return {
            "id": session_id,
            "workspaceRoot": str(workspace),
            "createdAt": now,
            "updatedAt": now,
        }

    def list_sessions(
        self, *, limit: int = DEFAULT_LIST_LIMIT, cursor: str | None = None
    ) -> dict[str, object]:
        before_sequence = _decode_cursor(cursor) if cursor is not None else None
        sql = """
            SELECT creation_seq, id, workspace_root, created_at, updated_at
            FROM sessions
        """
        parameters: list[object] = []
        if before_sequence is not None:
            sql += " WHERE creation_seq < ?"
            parameters.append(before_sequence)
        sql += " ORDER BY creation_seq DESC LIMIT ?"
        parameters.append(limit + 1)

        rows = self._connection().execute(sql, parameters).fetchall()
        has_more = len(rows) > limit
        page = rows[:limit]
        result: dict[str, object] = {"items": [_session_from_row(row) for row in page]}
        if has_more:
            result["nextCursor"] = _encode_cursor(page[-1]["creation_seq"])
        return result

    def read_session(self, session_id: str) -> dict[str, object] | None:
        row = self._connection().execute(
            """
            SELECT id, workspace_root, created_at, updated_at
            FROM sessions
            WHERE id = ?
            """,
            (session_id,),
        ).fetchone()
        return _session_from_row(row) if row is not None else None

    def _connection(self) -> sqlite3.Connection:
        if self.connection is None:
            raise StorageError("storage is not initialized")
        return self.connection


def _default_data_directory() -> Path:
    configured = os.environ.get("EIDOS_DATA_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".eidos"


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
        flags = os.O_CREAT | os.O_EXCL | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, 0o600)
        os.close(descriptor)
    metadata = path.stat()
    if not stat.S_ISREG(metadata.st_mode):
        raise StorageError("database must be a regular file")
    if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) != 0o600:
        raise StorageError("database owner or mode is invalid")


def _canonical_workspace(value: str) -> Path:
    if not value or len(value) > 4096:
        raise WorkspaceBoundaryError("workspace path is invalid")
    path = Path(value)
    if not path.is_absolute() or path.is_symlink() or not path.is_dir():
        raise WorkspaceBoundaryError("workspace must be an existing absolute directory")
    return path.resolve()


def _session_from_row(row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": row["id"],
        "workspaceRoot": row["workspace_root"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _encode_cursor(sequence: int) -> str:
    payload = f"{SESSION_CURSOR_PREFIX}{sequence}".encode("ascii")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> int:
    if not cursor or len(cursor) > 128:
        raise InvalidCursorError("cursor is invalid")
    try:
        padding = "=" * (-len(cursor) % 4)
        decoded = base64.b64decode(
            cursor + padding,
            altchars=b"-_",
            validate=True,
        ).decode("ascii")
        if not decoded.startswith(SESSION_CURSOR_PREFIX):
            raise ValueError
        sequence = int(decoded.removeprefix(SESSION_CURSOR_PREFIX))
        if sequence <= 0:
            raise ValueError
        return sequence
    except (UnicodeDecodeError, ValueError):
        raise InvalidCursorError("cursor is invalid") from None

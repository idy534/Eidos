from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path, PurePosixPath
import sqlite3
import stat
import threading
import time
import uuid

from eidos_runtime.db.errors import StorageError
from eidos_runtime.db.thread_history import SqliteConnectionOwner


_MAX_LOG_MESSAGE_BYTES = 16 * 1024
_MAX_SEGMENT_BYTES = 8 * 1024 * 1024
_MAX_TOTAL_LOG_BYTES = 128 * 1024 * 1024


class RuntimeLogStore:
    """Bounded JSONL log writer with SQLite segment metadata."""

    def __init__(
        self,
        data_directory: Path,
        database: SqliteConnectionOwner,
        *,
        max_segment_bytes: int = _MAX_SEGMENT_BYTES,
        max_total_bytes: int = _MAX_TOTAL_LOG_BYTES,
    ) -> None:
        if max_segment_bytes <= 0 or max_total_bytes <= 0:
            raise ValueError("log size limits must be positive")
        self.root = data_directory / "logs"
        self.database = database
        self.max_segment_bytes = max_segment_bytes
        self.max_total_bytes = max_total_bytes
        self.lock = threading.RLock()
        _prepare_private_directory(self.root)
        self._reconcile_missing_segments()
        self._enforce_retention()

    def append(self, record: logging.LogRecord) -> None:
        timestamp = int(record.created * 1000)
        message = _bounded_text(record.getMessage(), _MAX_LOG_MESSAGE_BYTES)
        encoded = json.dumps(
            {
                "timestamp": timestamp,
                "level": record.levelname,
                "logger": record.name,
                "message": message,
                "processId": record.process,
                "threadName": record.threadName,
                "exceptionType": (
                    record.exc_info[0].__name__
                    if record.exc_info is not None
                    else None
                ),
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8") + b"\n"
        with self.lock:
            segment = self._active_segment(len(encoded), timestamp)
            path = _segment_path(self.root, segment)
            _prepare_private_directory(path.parent)
            stored_bytes = int(segment["stored_bytes"])
            _append(path, stored_bytes, encoded)
            previous_digest = str(segment["chain_sha256"])
            digest = hashlib.sha256(
                bytes.fromhex(previous_digest) + encoded
            ).hexdigest()
            with self.database.transaction() as connection:
                updated = connection.execute(
                    """
                    UPDATE log_segments
                    SET last_timestamp = ?, record_count = record_count + 1,
                        stored_bytes = ?, chain_sha256 = ?, updated_at = ?
                    WHERE id = ? AND state = 'active' AND stored_bytes = ?
                    """,
                    (
                        timestamp,
                        stored_bytes + len(encoded),
                        digest,
                        timestamp,
                        segment["id"],
                        stored_bytes,
                    ),
                )
                if updated.rowcount != 1:
                    raise StorageError("log_segment_concurrent_update")
            self._enforce_retention()

    def seal(self) -> None:
        with self.lock, self.database.transaction() as connection:
            connection.execute(
                "UPDATE log_segments SET state = 'sealed', updated_at = ? "
                "WHERE state = 'active'",
                (time.time_ns() // 1_000_000,),
            )

    def _active_segment(
        self, incoming_bytes: int, timestamp: int
    ) -> sqlite3.Row:
        row = self.database.connection().execute(
            "SELECT * FROM log_segments WHERE state = 'active' "
            "ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if (
            row is not None
            and int(row["stored_bytes"]) + incoming_bytes
            <= self.max_segment_bytes
        ):
            return row
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE log_segments SET state = 'sealed', updated_at = ? "
                "WHERE state = 'active'",
                (timestamp,),
            )
            segment_id = f"{timestamp}-{os.getpid()}-{uuid.uuid4().hex}"
            day = time.strftime("%Y-%m-%d", time.localtime(timestamp / 1000))
            relative_path = f"{day}/{segment_id}.jsonl"
            empty_digest = hashlib.sha256(b"").hexdigest()
            connection.execute(
                """
                INSERT INTO log_segments (
                    id, relative_path, first_timestamp, last_timestamp,
                    record_count, stored_bytes, chain_sha256, state,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, 0, 0, ?, 'active', ?, ?)
                """,
                (
                    segment_id,
                    relative_path,
                    timestamp,
                    timestamp,
                    empty_digest,
                    timestamp,
                    timestamp,
                ),
            )
        created = self.database.connection().execute(
            "SELECT * FROM log_segments WHERE id = ?", (segment_id,)
        ).fetchone()
        if created is None:
            raise StorageError("log_segment_create_failed")
        return created

    def _reconcile_missing_segments(self) -> None:
        rows = self.database.connection().execute(
            "SELECT id, relative_path, first_timestamp FROM log_segments"
        ).fetchall()
        missing = []
        for row in rows:
            path = _segment_path(self.root, row)
            if not path.exists():
                missing.append(str(row["id"]))
        if missing:
            with self.database.transaction() as connection:
                connection.executemany(
                    "DELETE FROM log_segments WHERE id = ?",
                    ((segment_id,) for segment_id in missing),
                )

    def _enforce_retention(self) -> None:
        rows = self.database.connection().execute(
            "SELECT id, relative_path, first_timestamp, stored_bytes "
            "FROM log_segments "
            "WHERE state = 'sealed' ORDER BY last_timestamp, id"
        ).fetchall()
        total_row = self.database.connection().execute(
            "SELECT COALESCE(SUM(stored_bytes), 0) FROM log_segments"
        ).fetchone()
        total = int(total_row[0]) if total_row is not None else 0
        for row in rows:
            if total <= self.max_total_bytes:
                return
            path = _segment_path(self.root, row)
            if path.exists():
                _verify_log_file(path)
                path.unlink()
                _fsync_directory(path.parent)
            with self.database.transaction() as connection:
                connection.execute(
                    "DELETE FROM log_segments WHERE id = ? AND state = 'sealed'",
                    (str(row["id"]),),
                )
            total -= int(row["stored_bytes"])


class RuntimeJsonlLogHandler(logging.Handler):
    def __init__(self, store: RuntimeLogStore) -> None:
        super().__init__(level=logging.INFO)
        self.store = store
        self._store_closed = False

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.store.append(record)
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        if self._store_closed:
            super().close()
            return
        self._store_closed = True
        try:
            try:
                self.store.seal()
            except StorageError:
                pass
        finally:
            super().close()


def _append(path: Path, committed: int, value: bytes) -> None:
    descriptor = os.open(
        path,
        os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size < committed
        ):
            raise StorageError("log_segment_file_invalid")
        if metadata.st_size > committed:
            os.ftruncate(descriptor, committed)
        os.lseek(descriptor, committed, os.SEEK_SET)
        view = memoryview(value)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise StorageError("log_segment_write_failed")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _bounded_text(value: str, maximum: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= maximum:
        return value
    return encoded[:maximum].decode("utf-8", errors="ignore")


def _segment_path(root: Path, row: sqlite3.Row) -> Path:
    segment_id = str(row["id"])
    parts = segment_id.split("-")
    if (
        len(parts) != 3
        or not parts[0].isdigit()
        or not parts[1].isdigit()
        or len(parts[2]) != 32
        or any(character not in "0123456789abcdef" for character in parts[2])
    ):
        raise StorageError("log_segment_path_invalid")
    timestamp = int(row["first_timestamp"])
    day = time.strftime("%Y-%m-%d", time.localtime(timestamp / 1000))
    expected = f"{day}/{segment_id}.jsonl"
    if str(row["relative_path"]) != expected:
        raise StorageError("log_segment_path_invalid")
    return root.joinpath(*PurePosixPath(expected).parts)


def _prepare_private_directory(path: Path) -> None:
    if path.is_symlink():
        raise StorageError("log_directory_invalid")
    if not path.exists():
        path.mkdir(mode=0o700, parents=True)
    metadata = path.stat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise StorageError("log_directory_invalid")


def _verify_log_file(path: Path) -> None:
    if path.is_symlink():
        raise StorageError("log_segment_file_invalid")
    metadata = path.stat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise StorageError("log_segment_file_invalid")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = ["RuntimeJsonlLogHandler", "RuntimeLogStore"]

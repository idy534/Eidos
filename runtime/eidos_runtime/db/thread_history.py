from __future__ import annotations

from contextlib import AbstractContextManager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sqlite3
import stat
import threading
from typing import Protocol, Sequence

from eidos_runtime.db.errors import StorageError


class SqliteConnectionOwner(Protocol):
    lock: threading.RLock

    def connection(self) -> sqlite3.Connection: ...

    def transaction(self) -> AbstractContextManager[sqlite3.Connection]: ...


class ThreadHistoryStore:
    """Restart-safe JSONL projection of authoritative state events."""

    def __init__(
        self,
        data_directory: Path,
        projection_database: SqliteConnectionOwner,
    ) -> None:
        self.root = data_directory / "history"
        self.database = projection_database
        self.lock = threading.RLock()
        _prepare_private_directory(self.root)
        _prepare_private_directory(self.root / "sessions")

    def catch_up(self, state: SqliteConnectionOwner) -> int:
        with self.lock:
            self._repair_uncommitted_tails()
            projected = 0
            while True:
                row = self.database.connection().execute(
                    "SELECT COALESCE(MAX(event_id), 0) FROM history_events"
                ).fetchone()
                after_event_id = int(row[0]) if row is not None else 0
                rows = state.connection().execute(
                    "SELECT * FROM events WHERE id > ? ORDER BY id LIMIT 500",
                    (after_event_id,),
                ).fetchall()
                if not rows:
                    self._remove_deleted_session_histories(state)
                    return projected
                for event in rows:
                    self._append_event(event)
                    projected += 1

    def delete_session(self, session_id: str) -> None:
        relative_path = _relative_path(session_id)
        path = self.root.joinpath(*PurePosixPath(relative_path).parts)
        with self.lock, self.database.transaction() as connection:
            connection.execute(
                "DELETE FROM history_events WHERE session_id = ?", (session_id,)
            )
            connection.execute(
                "DELETE FROM history_files WHERE session_id = ?", (session_id,)
            )
        if path.exists():
            _verify_history_file(path)
            path.unlink()
            _fsync_directory(path.parent)

    def _remove_deleted_session_histories(
        self, state: SqliteConnectionOwner
    ) -> None:
        rows = self.database.connection().execute(
            "SELECT session_id FROM history_files WHERE session_id != '_global'"
        ).fetchall()
        for row in rows:
            session_id = str(row["session_id"])
            exists = state.connection().execute(
                "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if exists is None:
                self.delete_session(session_id)

    def _append_event(self, event: sqlite3.Row) -> None:
        try:
            payload = json.loads(event["payload_json"])
        except (TypeError, json.JSONDecodeError) as error:
            raise StorageError("thread_history_event_invalid") from error
        if not isinstance(payload, dict):
            raise StorageError("thread_history_event_invalid")
        record = json.dumps(
            {
                "eventContractVersion": int(event["event_contract_version"]),
                "eventId": int(event["id"]),
                "eventType": str(event["event_type"]),
                "occurredAt": int(event["occurred_at"]),
                "sessionId": event["session_id"],
                "runId": event["run_id"],
                "payload": payload,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8") + b"\n"
        stream_id = str(event["session_id"] or "_global")
        relative_path = _relative_path(stream_id)
        projection = self.database.connection().execute(
            "SELECT committed_bytes FROM history_files WHERE session_id = ?",
            (stream_id,),
        ).fetchone()
        committed_bytes = int(projection[0]) if projection is not None else 0
        path = self.root.joinpath(*PurePosixPath(relative_path).parts)
        offset = _append_at_committed_offset(path, committed_bytes, record)
        digest = hashlib.sha256(record).hexdigest()
        with self.database.transaction() as connection:
            connection.execute(
                """
                INSERT INTO history_events (
                    event_id, session_id, run_id, event_type, occurred_at,
                    file_offset, record_bytes, record_sha256, relative_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(event["id"]),
                    event["session_id"],
                    event["run_id"],
                    str(event["event_type"]),
                    int(event["occurred_at"]),
                    offset,
                    len(record),
                    digest,
                    relative_path,
                ),
            )
            connection.execute(
                """
                INSERT INTO history_files (
                    session_id, relative_path, committed_bytes,
                    last_event_id, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    relative_path = excluded.relative_path,
                    committed_bytes = excluded.committed_bytes,
                    last_event_id = excluded.last_event_id,
                    updated_at = excluded.updated_at
                """,
                (
                    stream_id,
                    relative_path,
                    offset + len(record),
                    int(event["id"]),
                    int(event["occurred_at"]),
                ),
            )

    def _repair_uncommitted_tails(self) -> None:
        rows = self.database.connection().execute(
            "SELECT session_id, relative_path, committed_bytes FROM history_files"
        ).fetchall()
        reset_required = False
        for row in rows:
            stream_id = str(row["session_id"])
            relative_path = str(row["relative_path"])
            if relative_path != _relative_path(stream_id):
                raise StorageError("thread_history_path_invalid")
            path = self.root.joinpath(*PurePosixPath(relative_path).parts)
            committed_bytes = int(row["committed_bytes"])
            if not path.exists():
                if committed_bytes:
                    reset_required = True
                continue
            metadata = _verify_history_file(path)
            if metadata.st_size < committed_bytes:
                reset_required = True
            elif metadata.st_size > committed_bytes:
                descriptor = os.open(
                    path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
                )
                try:
                    os.ftruncate(descriptor, committed_bytes)
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        if not reset_required:
            self._remove_orphan_files(rows)
            return
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM history_events")
            connection.execute("DELETE FROM history_files")
        for row in rows:
            path = self.root.joinpath(
                *PurePosixPath(str(row["relative_path"])).parts
            )
            if path.exists() and not path.is_symlink():
                descriptor = os.open(
                    path, os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
                )
                try:
                    os.ftruncate(descriptor, 0)
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        self._remove_orphan_files(())

    def _remove_orphan_files(self, rows: Sequence[sqlite3.Row]) -> None:
        known = {
            str(row["relative_path"])
            for row in rows
        }
        sessions = self.root / "sessions"
        for path in sessions.glob("*.jsonl"):
            relative = path.relative_to(self.root).as_posix()
            if relative in known:
                continue
            _verify_history_file(path)
            path.unlink()
        _fsync_directory(sessions)


def _relative_path(stream_id: str) -> str:
    digest = hashlib.sha256(stream_id.encode("utf-8")).hexdigest()
    return f"sessions/{digest}.jsonl"


def _append_at_committed_offset(path: Path, committed: int, record: bytes) -> int:
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size < committed
        ):
            raise StorageError("thread_history_file_invalid")
        if metadata.st_size > committed:
            os.ftruncate(descriptor, committed)
        os.lseek(descriptor, committed, os.SEEK_SET)
        view = memoryview(record)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise StorageError("thread_history_write_failed")
            view = view[written:]
        os.fsync(descriptor)
        return committed
    finally:
        os.close(descriptor)


def _verify_history_file(path: Path) -> os.stat_result:
    if path.is_symlink():
        raise StorageError("thread_history_file_invalid")
    metadata = path.stat()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise StorageError("thread_history_file_invalid")
    return metadata


def _prepare_private_directory(path: Path) -> None:
    if path.is_symlink():
        raise StorageError("thread_history_directory_invalid")
    if not path.exists():
        path.mkdir(mode=0o700, parents=True)
    metadata = path.stat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise StorageError("thread_history_directory_invalid")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = ["ThreadHistoryStore"]

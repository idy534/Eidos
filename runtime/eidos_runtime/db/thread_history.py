from __future__ import annotations

from contextlib import AbstractContextManager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sqlite3
import stat
import threading
import time
from typing import Callable, Protocol, Sequence

from eidos_runtime.db.errors import StorageError


# Session deletes outside SessionStore leave history files for this pass.
REPAIR_INTERVAL_SECONDS = 60.0


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
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.root = data_directory / "history"
        self.database = projection_database
        self.lock = threading.RLock()
        self._monotonic = monotonic
        # None forces a full repair pass: at startup and after any failure.
        self._repaired_at: float | None = None
        _prepare_private_directory(self.root)
        _prepare_private_directory(self.root / "sessions")

    def catch_up(self, state: SqliteConnectionOwner) -> int:
        with self.lock:
            try:
                repaired_this_call = False
                if self._repaired_at is None:
                    self._repair(state)
                    repaired_this_call = True
                projected = 0
                while True:
                    with self.database.lock:
                        row = self.database.connection().execute(
                            "SELECT COALESCE(MAX(event_id), 0) FROM history_events"
                        ).fetchone()
                    after_event_id = int(row[0]) if row is not None else 0
                    # The history lock owns projection files, not the shared state
                    # connection. Hold its owner's lock through result materialization.
                    with state.lock:
                        rows = state.connection().execute(
                            "SELECT * FROM events WHERE id > ? ORDER BY id LIMIT 500",
                            (after_event_id,),
                        ).fetchall()
                    if not rows:
                        # Another delivery may already have projected these
                        # events, so an empty pass is not proof of idleness.
                        # Rate-limit the full scan instead of running it on
                        # every empty pass while Runs stream concurrently.
                        if (
                            projected == 0
                            and not repaired_this_call
                            and self._repaired_at is not None
                            and self._monotonic() - self._repaired_at
                            >= REPAIR_INTERVAL_SECONDS
                        ):
                            self._repair(state)
                        return projected
                    self._append_events(rows)
                    projected += len(rows)
                    if len(rows) < 500:
                        return projected
            except BaseException:
                self._repaired_at = None
                raise

    def _repair(self, state: SqliteConnectionOwner) -> None:
        self._repair_uncommitted_tails()
        self._remove_deleted_session_histories(state)
        self._repaired_at = self._monotonic()

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
        try:
            if path.exists():
                _verify_history_file(path)
                path.unlink()
                _fsync_directory(path.parent)
        except BaseException:
            self._repaired_at = None
            raise

    def _remove_deleted_session_histories(
        self, state: SqliteConnectionOwner
    ) -> None:
        with self.database.lock:
            rows = self.database.connection().execute(
                "SELECT session_id FROM history_files WHERE session_id != '_global'"
            ).fetchall()
        if not rows:
            return
        with state.lock:
            active_session_ids = {
                str(row[0])
                for row in state.connection()
                .execute("SELECT id FROM sessions")
                .fetchall()
            }
        for row in rows:
            session_id = str(row["session_id"])
            if session_id not in active_session_ids:
                self.delete_session(session_id)

    def _append_events(self, events: Sequence[sqlite3.Row]) -> None:
        by_stream: dict[str, list[tuple[sqlite3.Row, bytes, str, str]]] = {}
        for event in events:
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
            digest = hashlib.sha256(record).hexdigest()
            by_stream.setdefault(stream_id, []).append(
                (event, record, digest, relative_path)
            )

        stream_ids = tuple(by_stream.keys())
        placeholders = ",".join("?" for _ in stream_ids)
        with self.database.lock:
            existing_rows = self.database.connection().execute(
                f"SELECT session_id, committed_bytes FROM history_files WHERE session_id IN ({placeholders})",
                stream_ids,
            ).fetchall()
        committed_by_stream = {
            str(row["session_id"]): int(row["committed_bytes"])
            for row in existing_rows
        }

        event_inserts: list[tuple[int, object, object, str, int, int, int, str, str]] = []
        file_upserts: list[tuple[str, str, int, int, int]] = []
        for stream_id, entries in by_stream.items():
            relative_path = entries[0][3]
            committed_bytes = committed_by_stream.get(stream_id, 0)
            path = self.root.joinpath(*PurePosixPath(relative_path).parts)
            existed = path.exists()
            combined = b"".join(entry[1] for entry in entries)
            offset = _append_at_committed_offset(path, committed_bytes, combined)
            if not existed:
                _fsync_directory(path.parent)
            cursor = offset
            for event, record, digest, rel_path in entries:
                event_inserts.append(
                    (
                        int(event["id"]),
                        event["session_id"],
                        event["run_id"],
                        str(event["event_type"]),
                        int(event["occurred_at"]),
                        cursor,
                        len(record),
                        digest,
                        rel_path,
                    )
                )
                cursor += len(record)
            last_event = entries[-1][0]
            file_upserts.append(
                (
                    stream_id,
                    relative_path,
                    cursor,
                    int(last_event["id"]),
                    int(last_event["occurred_at"]),
                )
            )

        with self.database.transaction() as connection:
            connection.executemany(
                """
                INSERT INTO history_events (
                    event_id, session_id, run_id, event_type, occurred_at,
                    file_offset, record_bytes, record_sha256, relative_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                event_inserts,
            )
            connection.executemany(
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
                file_upserts,
            )

    def _repair_uncommitted_tails(self) -> None:
        with self.database.lock:
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
        unlinked = False
        for path in sessions.glob("*.jsonl"):
            relative = path.relative_to(self.root).as_posix()
            if relative in known:
                continue
            _verify_history_file(path)
            path.unlink()
            unlinked = True
        if unlinked:
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

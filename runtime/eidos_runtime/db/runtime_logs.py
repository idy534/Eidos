from __future__ import annotations

from datetime import date
import hashlib
import json
import logging
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import stat
import threading
import time
import traceback
import uuid

from opentelemetry import trace

from eidos_runtime.db.errors import StorageError
from eidos_runtime.db.thread_history import SqliteConnectionOwner
from eidos_runtime.sandbox.sensitive import default_scanner


_MAX_LOG_MESSAGE_BYTES = 16 * 1024
_MAX_EXCEPTION_MESSAGE_BYTES = 4 * 1024
_MAX_EXCEPTION_TRACEBACK_BYTES = 16 * 1024
_LOG_SECRET_ASSIGNMENT = re.compile(
    r'''(?i)(\b(?:api[_-]?key|password|passwd|token|secret)\b["']?\s*[:=]\s*)(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s,;]+)'''
)
_LOG_AUTHORIZATION = re.compile(r"(?im)\bauthorization\b[^\r\n]*")
_LOG_PRIVATE_KEY = re.compile(
    r"-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----[\s\S]*?(?:-----END(?: [A-Z0-9]+)? PRIVATE KEY-----|$)"
)
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
        message = _safe_log_text(record.getMessage(), _MAX_LOG_MESSAGE_BYTES)
        span_context = trace.get_current_span().get_span_context()
        correlation = (
            {
                "traceId": format(span_context.trace_id, "032x"),
                "spanId": format(span_context.span_id, "016x"),
            }
            if span_context.is_valid else {}
        )
        encoded = json.dumps(
            {
                "timestamp": timestamp,
                "level": record.levelname,
                "logger": record.name,
                "message": message,
                "processId": record.process,
                "threadName": record.threadName,
                **correlation,
                "exceptionType": (
                    record.exc_info[0].__name__
                    if record.exc_info is not None
                    else None
                ),
                **_exception_fields(record),
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


def _safe_log_text(value: str, max_bytes: int) -> str:
    try:
        text = _bounded_text(value, max_bytes)
        text = _LOG_PRIVATE_KEY.sub("[REDACTED:private_key]", text)
        text = _LOG_AUTHORIZATION.sub("Authorization: [REDACTED]", text)
        text = _LOG_SECRET_ASSIGNMENT.sub(r"\1[REDACTED]", text)
        text = default_scanner().redact_for_presentation(text, max_bytes=max_bytes).text
        return _bounded_text(text, max_bytes)
    except Exception:
        return "[REDACTED:diagnostic_unavailable]"


def _exception_fields(record: logging.LogRecord) -> dict[str, str]:
    if record.exc_info is None or record.exc_info[1] is None:
        return {}
    try:
        error = record.exc_info[1]
        message = _safe_log_text(str(error), _MAX_EXCEPTION_MESSAGE_BYTES)
        lines: list[str] = []
        seen: set[int] = set()
        current: BaseException | None = error
        while current is not None and id(current) not in seen and len(seen) < 8:
            seen.add(id(current))
            # Capture frame locations only. Source lines and locals may contain
            # credentials or full user files, so they never enter the log.
            frames = traceback.StackSummary.extract(
                traceback.walk_tb(current.__traceback__), limit=-32,
                lookup_lines=False, capture_locals=False,
            )
            lines.extend(
                f'  File "{frame.filename}", line {frame.lineno}, in {frame.name}'
                for frame in frames
            )
            lines.append(type(current).__name__ + ": " + _safe_log_text(
                str(current), _MAX_EXCEPTION_MESSAGE_BYTES,
            ))
            if current.__cause__ is not None:
                lines.append("Caused by:")
                current = current.__cause__
            elif not current.__suppress_context__:
                current = current.__context__
                if current is not None:
                    lines.append("During handling of:")
            else:
                current = None
        return {
            "exceptionMessage": message,
            "exceptionTraceback": _safe_log_text("\n".join(lines), _MAX_EXCEPTION_TRACEBACK_BYTES),
        }
    except Exception:
        return {"exceptionMessage": "[diagnostic unavailable]"}


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
    # The saved day belongs to the timezone at creation, which may have changed.
    relative_path = str(row["relative_path"])
    path_parts = PurePosixPath(relative_path).parts
    if len(path_parts) != 2:
        raise StorageError("log_segment_path_invalid")
    day = path_parts[0]
    try:
        if date.fromisoformat(day).isoformat() != day:
            raise ValueError("noncanonical date")
    except ValueError:
        raise StorageError("log_segment_path_invalid") from None
    expected = f"{day}/{segment_id}.jsonl"
    if relative_path != expected:
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

from __future__ import annotations

import base64
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
import uuid
from typing import Callable, TypeVar

from eidos_runtime.schemas import ItemDto, RunDto, SessionDto
from eidos_runtime.events import append_event, event_from_row
from eidos_runtime.model_config import DEFAULT_MODEL_ID, SUPPORTED_MODELS
from eidos_runtime.state_machine import EventType, RunStatus


DATABASE_NAME = "eidos.db"
LOCK_NAME = "runtime.lock"
RESERVE_NAME = "emergency.reserve"
SCHEMA_REVISION = 4
RESERVE_BYTES = 1024 * 1024
DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 200
SESSION_CURSOR_PREFIX = "session-v2:"
MAX_CONTEXT_BYTES = 768 * 1024
MAX_CONTEXT_ITEMS = 200
MAX_SNAPSHOT_BYTES = 768 * 1024
MAX_SNAPSHOT_TEXT_BYTES = 192 * 1024

SESSION_SELECT = """
    SELECT s.creation_seq, s.id, s.workspace_root, s.title,
           s.created_at, s.updated_at,
           CASE
             WHEN EXISTS (
               SELECT 1 FROM runs active
               WHERE active.session_id = s.id
                 AND active.status IN (
                   'queued', 'running', 'waiting_approval',
                   'waiting_user_input', 'finalizing'
                 )
             ) THEN 'in_progress'
             ELSE COALESCE((
               SELECT CASE latest.status
                 WHEN 'succeeded' THEN 'completed'
                 WHEN 'failed' THEN 'failed'
                 WHEN 'stopped' THEN 'failed'
                 WHEN 'interrupted' THEN 'failed'
                 WHEN 'canceled' THEN 'canceled'
                 ELSE 'new'
               END
               FROM runs latest
               WHERE latest.session_id = s.id
               ORDER BY latest.creation_seq DESC
               LIMIT 1
             ), 'new')
           END AS task_status
    FROM sessions s
"""


class StorageError(RuntimeError):
    pass


class WorkspaceBoundaryError(ValueError):
    pass


class InvalidCursorError(ValueError):
    pass


class ActiveRunError(RuntimeError):
    pass


class SessionActiveError(RuntimeError):
    pass


class ResourceNotFoundError(LookupError):
    pass


class InvalidRunStateError(RuntimeError):
    pass


class ContextLimitExceeded(RuntimeError):
    pass


class OperationConflictError(RuntimeError):
    pass


class OperationInProgressError(RuntimeError):
    pass


class SegmentLimitReached(RuntimeError):
    pass


class RunLimitReached(RuntimeError):
    pass


T = TypeVar("T")


@dataclass(frozen=True)
class WorkspaceIdentity:
    path: Path
    device: int
    inode: int
    owner: int


class SessionStore:
    def __init__(self, data_directory: Path | None = None) -> None:
        self.data_directory = data_directory
        self.connection: sqlite3.Connection | None = None
        self.lock = threading.RLock()
        self.lock_descriptor: int | None = None
        self.health_state = "starting"
        self.health_code: str | None = None

    def initialize(self) -> None:
        with self.lock:
            if self.connection is not None or self.health_state != "starting":
                raise StorageError("storage is already initialized")
            try:
                self._initialize()
            except (OSError, sqlite3.Error, StorageError) as error:
                self._close_resources()
                self.health_state = "health_only"
                self.health_code = _safe_health_code(error)

    def _initialize(self) -> None:
        data_directory = self.data_directory or _default_data_directory()
        if not data_directory.is_absolute():
            raise StorageError("data_directory_invalid")
        _prepare_private_directory(data_directory)
        data_directory = data_directory.resolve()
        self.data_directory = data_directory
        self.lock_descriptor = _acquire_state_lock(data_directory / LOCK_NAME)
        _prepare_reserve(data_directory / RESERVE_NAME)
        database_path = data_directory / DATABASE_NAME
        _prepare_private_database(database_path)

        connection = sqlite3.connect(database_path, check_same_thread=False)
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA busy_timeout = 5000")
            _verify_pragmas(connection)
            tables = _table_names(connection)
            revision = connection.execute("PRAGMA user_version").fetchone()[0]
            if revision == 0 and tables:
                if {"sessions", "runs", "items", "tool_calls"} <= tables:
                    revision = 1
                else:
                    raise StorageError("schema_revision_missing")
            if revision > SCHEMA_REVISION or revision < 0:
                raise StorageError("schema_revision_unsupported")
            if revision in {1, 2, 3}:
                _backup_database(connection, database_path, revision)
            if revision == 1:
                _migrate_v1_to_v2(connection)
                revision = 2
            if revision == 2:
                _migrate_v2_to_v3(connection)
                revision = 3
            if revision == 3:
                _migrate_v3_to_v4(connection)
                revision = 4
            elif revision not in {0, SCHEMA_REVISION}:
                raise StorageError("schema_revision_unsupported")

            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    creation_seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT NOT NULL UNIQUE,
                    workspace_root TEXT NOT NULL,
                    title TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS runs (
                    creation_seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT NOT NULL UNIQUE,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE RESTRICT,
                    user_input TEXT NOT NULL,
                    model_id TEXT NOT NULL DEFAULT 'deepseek-v4-flash',
                    status TEXT NOT NULL CHECK (status IN (
                        'queued', 'running', 'waiting_approval', 'waiting_user_input',
                        'finalizing', 'succeeded', 'failed', 'stopped', 'canceled',
                        'interrupted'
                    )),
                    model_step_count INTEGER NOT NULL DEFAULT 0,
                    consecutive_protocol_errors INTEGER NOT NULL DEFAULT 0,
                    consecutive_rejects INTEGER NOT NULL DEFAULT 0,
                    consecutive_sensitive_tool_inputs INTEGER NOT NULL DEFAULT 0,
                    enqueued_at INTEGER,
                    total_effective_ms INTEGER NOT NULL DEFAULT 0,
                    pause_reason TEXT,
                    stop_reason TEXT,
                    reconciliation_required INTEGER NOT NULL DEFAULT 0,
                    reconciliation_epoch INTEGER NOT NULL DEFAULT 0,
                    side_effects_may_exist INTEGER NOT NULL DEFAULT 0,
                    error_code TEXT,
                    created_at INTEGER NOT NULL,
                    started_at INTEGER,
                    updated_at INTEGER NOT NULL,
                    completed_at INTEGER
                );

                CREATE UNIQUE INDEX IF NOT EXISTS one_active_run
                ON runs ((1))
                WHERE status IN ('running', 'finalizing');

                CREATE TABLE IF NOT EXISTS items (
                    creation_seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT NOT NULL UNIQUE,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE RESTRICT,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
                    ordinal INTEGER NOT NULL,
                    model_step_index INTEGER,
                    kind TEXT NOT NULL CHECK (kind IN (
                        'user_message', 'assistant_message', 'file_change',
                        'command_execution', 'tool_call'
                    )),
                    status TEXT NOT NULL CHECK (status IN (
                        'in_progress', 'completed', 'failed', 'declined', 'canceled'
                    )),
                    content TEXT,
                    incomplete INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL,
                    completed_at INTEGER,
                    UNIQUE(run_id, ordinal)
                );

                CREATE TABLE IF NOT EXISTS tool_calls (
                    creation_seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT NOT NULL UNIQUE,
                    item_id TEXT NOT NULL UNIQUE REFERENCES items(id) ON DELETE RESTRICT,
                    model_step_index INTEGER NOT NULL,
                    batch_order INTEGER NOT NULL,
                    provider_call_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN (
                        'running', 'completed', 'failed', 'canceled'
                    )),
                    arguments_json TEXT NOT NULL,
                    result_json TEXT,
                    approval_status TEXT,
                    approval_decision TEXT,
                    approval_feedback TEXT,
                    approval_diff TEXT,
                    base_sha256 TEXT,
                    started_at INTEGER NOT NULL,
                    completed_at INTEGER
                );

                CREATE TABLE IF NOT EXISTS approvals (
                    creation_seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT NOT NULL UNIQUE,
                    tool_call_id TEXT NOT NULL UNIQUE REFERENCES tool_calls(id) ON DELETE RESTRICT,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
                    item_id TEXT NOT NULL UNIQUE REFERENCES items(id) ON DELETE RESTRICT,
                    status TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    decision TEXT,
                    feedback TEXT,
                    created_at INTEGER NOT NULL,
                    decided_at INTEGER
                );

                CREATE TABLE IF NOT EXISTS execution_segments (
                    creation_seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT NOT NULL UNIQUE,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
                    ordinal INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    step_count INTEGER NOT NULL DEFAULT 0,
                    effective_ms INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL,
                    started_at INTEGER,
                    completed_at INTEGER,
                    UNIQUE(run_id, ordinal)
                );

                CREATE TABLE IF NOT EXISTS steps (
                    creation_seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT NOT NULL UNIQUE,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
                    segment_id TEXT NOT NULL REFERENCES execution_segments(id) ON DELETE RESTRICT,
                    ordinal INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    observed_reconciliation_epoch INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL,
                    completed_at INTEGER,
                    UNIQUE(segment_id, ordinal)
                );

                CREATE TABLE IF NOT EXISTS model_attempts (
                    creation_seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT NOT NULL UNIQUE,
                    step_id TEXT NOT NULL REFERENCES steps(id) ON DELETE RESTRICT,
                    ordinal INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    started_at INTEGER NOT NULL,
                    completed_at INTEGER,
                    UNIQUE(step_id, ordinal)
                );

                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_contract_version INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    occurred_at INTEGER NOT NULL,
                    session_id TEXT,
                    run_id TEXT,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS operations (
                    id TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    result_json TEXT,
                    created_at INTEGER NOT NULL,
                    completed_at INTEGER,
                    PRIMARY KEY(id, scope)
                );

                CREATE TABLE IF NOT EXISTS durable_intents (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
                    tool_call_id TEXT NOT NULL REFERENCES tool_calls(id) ON DELETE RESTRICT,
                    execution_nonce TEXT NOT NULL UNIQUE,
                    arguments_hash TEXT NOT NULL,
                    preconditions_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    reconciled_at INTEGER
                );
                """
            )
            connection.execute(f"PRAGMA user_version = {SCHEMA_REVISION}")
            _ensure_session_identity_columns(connection)
            _ensure_tool_call_approval_columns(connection)
            _ensure_phase_two_columns(connection)
            _backfill_session_identities(connection)
            now = _now_ms()
            connection.execute(
                """
                UPDATE durable_intents
                SET status = 'interrupted'
                WHERE status = 'running'
                """
            )
            connection.execute(
                """
                UPDATE runs
                SET status = 'waiting_user_input',
                    pause_reason = 'side_effect_reconciliation_required',
                    reconciliation_required = 1,
                    reconciliation_epoch = reconciliation_epoch + 1,
                    side_effects_may_exist = 1,
                    updated_at = ?
                WHERE id IN (
                    SELECT run_id FROM durable_intents WHERE status = 'interrupted'
                ) AND status IN ('running', 'waiting_approval', 'finalizing')
                """,
                (now,),
            )
            connection.execute(
                """
                UPDATE tool_calls
                SET status = 'canceled', completed_at = ?
                WHERE status = 'running'
                  AND item_id IN (
                    SELECT items.id FROM items
                    JOIN runs ON runs.id = items.run_id
                    WHERE runs.status IN ('running', 'waiting_approval')
                  )
                """,
                (now,),
            )
            connection.execute(
                """
                UPDATE tool_calls SET approval_status = 'canceled'
                WHERE approval_status = 'pending'
                """
            )
            connection.execute(
                """
                UPDATE approvals SET status = 'invalidated', decided_at = ?
                WHERE status = 'pending'
                """,
                (now,),
            )
            connection.execute(
                """
                UPDATE items
                SET status = 'canceled', completed_at = ?
                WHERE status = 'in_progress'
                  AND run_id IN (
                    SELECT id FROM runs
                    WHERE status IN ('running', 'waiting_approval')
                  )
                """,
                (now,),
            )
            connection.execute(
                """
                UPDATE runs
                SET status = 'interrupted', error_code = 'RUNTIME_INTERRUPTED',
                    updated_at = ?, completed_at = ?
                WHERE status IN ('running', 'waiting_approval')
                """,
                (now, now),
            )
            connection.commit()
            _verify_integrity(connection)
            connection.close()
            connection = sqlite3.connect(database_path, check_same_thread=False)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA busy_timeout = 5000")
            _verify_pragmas(connection)
            _verify_integrity(connection)
            self.connection = connection
            self.health_state = "ready"
            self.health_code = None
        except Exception:
            connection.close()
            raise

    def close(self) -> None:
        with self.lock:
            self._close_resources()

    def _close_resources(self) -> None:
        if self.connection is not None:
            self.connection.close()
            self.connection = None
        if self.lock_descriptor is not None:
            fcntl.flock(self.lock_descriptor, fcntl.LOCK_UN)
            os.close(self.lock_descriptor)
            self.lock_descriptor = None

    def health(self) -> dict[str, object]:
        result: dict[str, object] = {"state": self.health_state}
        if self.health_code is not None:
            result["code"] = self.health_code
        return result

    def create_session(
        self, workspace_root: str, *, operation_id: str | None = None
    ) -> dict[str, object]:
        workspace = _canonical_workspace(workspace_root)
        if self._workspace_overlaps_data(workspace):
            raise WorkspaceBoundaryError("workspace overlaps runtime data")
        metadata = workspace.stat()
        session_id = str(uuid.uuid4())
        now = time.time_ns() // 1_000_000
        session = SessionDto.model_validate({
            "id": session_id,
            "workspaceRoot": str(workspace),
            "title": None,
            "taskStatus": "new",
            "createdAt": now,
            "updatedAt": now,
        }).to_json_value()
        def write(connection: sqlite3.Connection) -> dict[str, object]:
            connection.execute(
                """
                INSERT INTO sessions (
                    id, workspace_root, workspace_dev, workspace_inode,
                    workspace_uid, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    str(workspace),
                    metadata.st_dev,
                    metadata.st_ino,
                    metadata.st_uid,
                    now,
                    now,
                ),
            )
            append_event(
                connection,
                EventType.SESSION_CREATED,
                now,
                {"session": session},
                session_id=session_id,
            )
            return session
        return self._write(
            write,
            operation_id=operation_id,
            operation_scope="session/create",
            operation_request={"workspaceRoot": str(workspace)},
        )

    def list_sessions(
        self, *, limit: int = DEFAULT_LIST_LIMIT, cursor: str | None = None
    ) -> dict[str, object]:
        cursor_state = _decode_cursor(cursor) if cursor is not None else None
        sql = SESSION_SELECT
        with self.lock:
            connection = self._connection()
            if cursor_state is None:
                high_water = connection.execute(
                    "SELECT COALESCE(MAX(creation_seq), 0) FROM sessions"
                ).fetchone()[0]
                before_sequence = high_water + 1
            else:
                high_water, before_sequence = cursor_state
            rows = connection.execute(
                sql
                + " WHERE s.creation_seq <= ? AND s.creation_seq < ?"
                + " ORDER BY s.creation_seq DESC LIMIT ?",
                (high_water, before_sequence, limit + 1),
            ).fetchall()
        has_more = len(rows) > limit
        page = rows[:limit]
        result: dict[str, object] = {"items": [_session_from_row(row) for row in page]}
        if has_more:
            result["nextCursor"] = _encode_cursor(
                high_water, page[-1]["creation_seq"]
            )
        return result

    def read_session(self, session_id: str) -> dict[str, object] | None:
        with self.lock:
            row = self._connection().execute(
                SESSION_SELECT + " WHERE s.id = ?",
                (session_id,),
            ).fetchone()
        return _session_from_row(row) if row is not None else None

    def session_model_id(self, session_id: str) -> str | None:
        with self.lock:
            row = self._connection().execute(
                """
                SELECT model_id FROM runs
                WHERE session_id = ?
                ORDER BY creation_seq LIMIT 1
                """,
                (session_id,),
            ).fetchone()
        return str(row["model_id"]) if row is not None else None

    def rename_session(
        self,
        session_id: str,
        title: str,
        *,
        operation_id: str | None = None,
    ) -> dict[str, object]:
        if not title or len(title) > 60 or len(title.encode("utf-8")) > 120:
            raise ValueError("session title is invalid")
        now = _now_ms()

        def write(connection: sqlite3.Connection) -> dict[str, object]:
            updated = connection.execute(
                "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
                (title, now, session_id),
            )
            if updated.rowcount != 1:
                raise ResourceNotFoundError("session not found")
            append_event(
                connection,
                EventType.SESSION_TITLE_UPDATED,
                now,
                {"title": title},
                session_id=session_id,
            )
            row = connection.execute(
                SESSION_SELECT + " WHERE s.id = ?", (session_id,)
            ).fetchone()
            return _session_from_row(row)

        return self._write(
            write,
            operation_id=operation_id,
            operation_scope="session/rename",
            operation_request={"sessionId": session_id, "title": title},
        )

    def delete_session(
        self,
        session_id: str,
        *,
        operation_id: str | None = None,
    ) -> dict[str, object]:
        def write(connection: sqlite3.Connection) -> dict[str, object]:
            session = connection.execute(
                "SELECT id FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if session is None:
                raise ResourceNotFoundError("session not found")
            active = connection.execute(
                """
                SELECT 1 FROM runs
                WHERE session_id = ? AND status IN (
                    'queued', 'running', 'waiting_approval',
                    'waiting_user_input', 'finalizing'
                ) LIMIT 1
                """,
                (session_id,),
            ).fetchone()
            if active is not None:
                raise SessionActiveError("session has an active run")
            run_ids = "SELECT id FROM runs WHERE session_id = ?"
            connection.execute(
                f"DELETE FROM durable_intents WHERE run_id IN ({run_ids})",
                (session_id,),
            )
            connection.execute(
                f"DELETE FROM approvals WHERE run_id IN ({run_ids})", (session_id,)
            )
            connection.execute(
                """
                DELETE FROM model_attempts WHERE step_id IN (
                    SELECT steps.id FROM steps
                    JOIN runs ON runs.id = steps.run_id
                    WHERE runs.session_id = ?
                )
                """,
                (session_id,),
            )
            connection.execute(
                f"DELETE FROM steps WHERE run_id IN ({run_ids})", (session_id,)
            )
            connection.execute(
                f"DELETE FROM execution_segments WHERE run_id IN ({run_ids})",
                (session_id,),
            )
            connection.execute(
                """
                DELETE FROM tool_calls WHERE item_id IN (
                    SELECT id FROM items WHERE session_id = ?
                )
                """,
                (session_id,),
            )
            connection.execute("DELETE FROM items WHERE session_id = ?", (session_id,))
            connection.execute("DELETE FROM events WHERE session_id = ?", (session_id,))
            connection.execute("DELETE FROM runs WHERE session_id = ?", (session_id,))
            connection.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            return {"deletedSessionId": session_id}

        return self._write(
            write,
            operation_id=operation_id,
            operation_scope="session/delete",
            operation_request={"sessionId": session_id},
        )

    def create_run(
        self,
        session_id: str,
        user_input: str,
        *,
        operation_id: str | None = None,
        queued: bool = False,
        session_title: str | None = None,
        model_id: str = DEFAULT_MODEL_ID,
    ) -> tuple[dict[str, object], dict[str, object]]:
        if session_title is not None and (
            not session_title
            or len(session_title) > 60
            or len(session_title.encode("utf-8")) > 120
        ):
            raise ValueError("session title is invalid")
        if model_id not in SUPPORTED_MODELS:
            raise ValueError("model is unsupported")
        run_id = str(uuid.uuid4())
        item_id = str(uuid.uuid4())
        now = _now_ms()
        def write(
            connection: sqlite3.Connection,
        ) -> dict[str, object]:
            session = connection.execute(
                "SELECT id, workspace_root, title FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if session is None:
                raise ResourceNotFoundError("session not found")
            if self._workspace_overlaps_data(Path(session["workspace_root"])):
                raise WorkspaceBoundaryError("workspace overlaps runtime data")
            if session["title"] is None and session_title is not None:
                connection.execute(
                    "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
                    (session_title, now, session_id),
                )
                append_event(
                    connection,
                    EventType.SESSION_TITLE_UPDATED,
                    now,
                    {"title": session_title},
                    session_id=session_id,
                )
            status = "queued" if queued else "running"
            started_at = None if queued else now
            try:
                connection.execute(
                    """
                    INSERT INTO runs (
                        id, session_id, user_input, model_id, status, enqueued_at,
                        created_at, started_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id, session_id, user_input, model_id, status,
                        now if queued else None, now, started_at, now,
                    ),
                )
            except sqlite3.IntegrityError as error:
                if "one_active_run" in str(error) or "UNIQUE constraint failed" in str(error):
                    raise ActiveRunError("another run is active") from None
                raise
            connection.execute(
                """
                INSERT INTO items (
                    id, session_id, run_id, ordinal, kind, status,
                    content, created_at, completed_at
                ) VALUES (?, ?, ?, 1, 'user_message', 'completed', ?, ?, ?)
                """,
                (item_id, session_id, run_id, user_input, now, now),
            )
            connection.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (now, session_id),
            )
            run = _run_from_row(connection.execute(
                "SELECT * FROM runs WHERE id = ?", (run_id,)
            ).fetchone())
            append_event(
                connection,
                EventType.RUN_CREATED,
                now,
                {"run": run},
                session_id=session_id,
                run_id=run_id,
            )
            item_row = connection.execute(
                "SELECT * FROM items WHERE id = ?", (item_id,)
            ).fetchone()
            return {"run": run, "item": _item_from_row(item_row, None)}
        result = self._write(
            write,
            operation_id=operation_id,
            operation_scope="run/start",
            operation_request={
                "sessionId": session_id,
                "userInput": user_input,
                "modelId": model_id,
            },
        )
        return result["run"], result["item"]

    def enqueue_run(
        self,
        session_id: str,
        user_input: str,
        *,
        operation_id: str | None = None,
        session_title: str | None = None,
        model_id: str = DEFAULT_MODEL_ID,
    ) -> tuple[dict[str, object], dict[str, object]]:
        return self.create_run(
            session_id,
            user_input,
            operation_id=operation_id,
            queued=True,
            session_title=session_title,
            model_id=model_id,
        )

    def continue_run(
        self,
        run_id: str,
        user_input: str,
        *,
        operation_id: str | None = None,
    ) -> dict[str, object]:
        now = _now_ms()
        item_id = str(uuid.uuid4())
        segment_id = str(uuid.uuid4())

        def write(connection: sqlite3.Connection) -> dict[str, object]:
            run_row = connection.execute(
                "SELECT * FROM runs WHERE id = ? AND status = 'waiting_user_input'",
                (run_id,),
            ).fetchone()
            if run_row is None:
                raise InvalidRunStateError("run cannot continue")
            item_ordinal = self._next_ordinal(connection, run_id)
            segment_ordinal = connection.execute(
                "SELECT COUNT(*) + 1 FROM execution_segments WHERE run_id = ?",
                (run_id,),
            ).fetchone()[0]
            connection.execute(
                """
                INSERT INTO items (
                    id, session_id, run_id, ordinal, kind, status,
                    content, created_at, completed_at
                ) VALUES (?, ?, ?, ?, 'user_message', 'completed', ?, ?, ?)
                """,
                (
                    item_id, run_row["session_id"], run_id, item_ordinal,
                    user_input, now, now,
                ),
            )
            connection.execute(
                """
                INSERT INTO execution_segments (
                    id, run_id, ordinal, status, created_at
                ) VALUES (?, ?, ?, 'queued', ?)
                """,
                (segment_id, run_id, segment_ordinal, now),
            )
            connection.execute(
                """
                UPDATE runs
                SET status = 'queued', enqueued_at = ?, pause_reason = NULL,
                    consecutive_rejects = 0, updated_at = ?
                WHERE id = ? AND status = 'waiting_user_input'
                """,
                (now, now, run_id),
            )
            run = _run_from_row(connection.execute(
                "SELECT * FROM runs WHERE id = ?", (run_id,)
            ).fetchone())
            append_event(
                connection, EventType.SEGMENT_CREATED, now,
                {
                    "entity_id": segment_id, "previous": "created",
                    "current": "queued",
                },
                session_id=run_row["session_id"], run_id=run_id,
            )
            append_event(
                connection, EventType.RUN_STATUS_CHANGED, now,
                {
                    "previous": RunStatus.WAITING_USER_INPUT,
                    "current": RunStatus.QUEUED,
                    "reason": "user_input",
                },
                session_id=run_row["session_id"], run_id=run_id,
            )
            return run

        return self._write(
            write,
            operation_id=operation_id,
            operation_scope="run/continue",
            operation_request={"runId": run_id, "userInput": user_input},
        )

    def claim_next_run(self) -> dict[str, object] | None:
        now = _now_ms()
        segment_id = str(uuid.uuid4())
        with self.lock, self._connection() as connection:
            row = connection.execute(
                """
                SELECT id FROM runs
                WHERE status = 'queued'
                  AND NOT EXISTS (
                    SELECT 1 FROM runs WHERE status IN ('running', 'finalizing')
                  )
                ORDER BY enqueued_at ASC, creation_seq ASC LIMIT 1
                """
            ).fetchone()
            if row is None:
                return None
            updated = connection.execute(
                """
                UPDATE runs
                SET status = 'running', started_at = COALESCE(started_at, ?),
                    updated_at = ?
                WHERE id = ? AND status = 'queued'
                  AND NOT EXISTS (
                    SELECT 1 FROM runs
                    WHERE status IN ('running', 'finalizing') AND id != ?
                  )
                """,
                (now, now, row["id"], row["id"]),
            )
            if updated.rowcount != 1:
                return None
            existing_segment = connection.execute(
                """
                SELECT id FROM execution_segments
                WHERE run_id = ? AND status IN ('queued', 'running')
                ORDER BY ordinal DESC LIMIT 1
                """,
                (row["id"],),
            ).fetchone()
            if existing_segment is None:
                ordinal = connection.execute(
                    "SELECT COUNT(*) + 1 FROM execution_segments WHERE run_id = ?",
                    (row["id"],),
                ).fetchone()[0]
                connection.execute(
                    """
                    INSERT INTO execution_segments (
                        id, run_id, ordinal, status, created_at, started_at
                    ) VALUES (?, ?, ?, 'running', ?, ?)
                    """,
                    (segment_id, row["id"], ordinal, now, now),
                )
            else:
                connection.execute(
                    """
                    UPDATE execution_segments SET status = 'running',
                        started_at = COALESCE(started_at, ?)
                    WHERE id = ?
                    """,
                    (now, existing_segment["id"]),
                )
            run = _run_from_row(connection.execute(
                "SELECT * FROM runs WHERE id = ?", (row["id"],)
            ).fetchone())
            append_event(
                connection, EventType.RUN_STATUS_CHANGED, now,
                {"previous": RunStatus.QUEUED, "current": RunStatus.RUNNING},
                session_id=str(run["sessionId"]), run_id=str(run["id"]),
            )
            return run

    def read_run(self, run_id: str) -> dict[str, object]:
        with self.lock:
            row = self._connection().execute(
                "SELECT * FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise ResourceNotFoundError("run not found")
        return _run_from_row(row)

    def read_item(self, item_id: str) -> dict[str, object]:
        with self.lock:
            row = self._connection().execute(
                "SELECT * FROM items WHERE id = ?", (item_id,)
            ).fetchone()
            if row is None:
                raise ResourceNotFoundError("item not found")
            tool_row = self._connection().execute(
                "SELECT * FROM tool_calls WHERE item_id = ?", (item_id,)
            ).fetchone()
        return _item_from_row(row, tool_row)

    def read_session_snapshot(
        self,
        session_id: str,
        *,
        item_limit: int = 200,
        before_item_id: str | None = None,
    ) -> dict[str, object]:
        with self.lock:
            connection = self._connection()
            session_row = connection.execute(
                SESSION_SELECT + " WHERE s.id = ?", (session_id,)
            ).fetchone()
            if session_row is None:
                raise ResourceNotFoundError("session not found")
            before_sequence: int | None = None
            if before_item_id is not None:
                before_row = connection.execute(
                    """
                    SELECT creation_seq FROM items
                    WHERE id = ? AND session_id = ?
                    """,
                    (before_item_id, session_id),
                ).fetchone()
                if before_row is None:
                    raise ResourceNotFoundError("item not found")
                before_sequence = before_row["creation_seq"]
            run_rows = connection.execute(
                """
                SELECT * FROM runs WHERE session_id = ?
                ORDER BY creation_seq DESC LIMIT 100
                """,
                (session_id,),
            ).fetchall()
            item_sql = "SELECT * FROM items WHERE session_id = ?"
            item_parameters: list[object] = [session_id]
            if before_sequence is not None:
                item_sql += " AND creation_seq < ?"
                item_parameters.append(before_sequence)
            item_sql += " ORDER BY creation_seq DESC LIMIT ?"
            item_parameters.append(item_limit + 1)
            item_rows = connection.execute(item_sql, item_parameters).fetchall()
            tool_rows: list[sqlite3.Row] = []
            if item_rows:
                placeholders = ",".join("?" for _ in item_rows)
                tool_rows = connection.execute(
                    f"SELECT * FROM tool_calls WHERE item_id IN ({placeholders})",
                    [row["id"] for row in item_rows],
                ).fetchall()
            through_event_id = connection.execute(
                "SELECT COALESCE(MAX(id), 0) FROM events WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
        session = _session_from_row(session_row)
        selected_runs = [
            _run_from_row(row, include_user_input=False)
            for row in reversed(run_rows)
        ]
        tools_by_item = {row["item_id"]: row for row in tool_rows}
        has_more = len(item_rows) > item_limit
        selected_items: list[dict[str, object]] = []
        selected_bytes = _json_bytes(session) + _json_bytes(selected_runs) + 1024
        for row in item_rows[:item_limit]:
            item = _snapshot_item(row, tools_by_item.get(row["id"]))
            item_bytes = _json_bytes(item)
            if selected_bytes + item_bytes > MAX_SNAPSHOT_BYTES:
                has_more = True
                break
            selected_items.append(item)
            selected_bytes += item_bytes
        selected_items.reverse()
        snapshot: dict[str, object] = {
            "session": session,
            "runs": selected_runs,
            "items": selected_items,
            "throughEventId": through_event_id,
        }
        if has_more and selected_items:
            snapshot["previousItemId"] = selected_items[0]["id"]
        return snapshot

    def list_events(
        self, session_id: str, *, after_event_id: int, limit: int = 200
    ) -> dict[str, object]:
        if after_event_id < 0 or not 1 <= limit <= 500:
            raise ValueError("invalid event cursor")
        with self.lock:
            rows = self._connection().execute(
                """
                SELECT * FROM events
                WHERE session_id = ? AND id > ?
                ORDER BY id ASC LIMIT ?
                """,
                (session_id, after_event_id, limit + 1),
            ).fetchall()
        events = [event for row in rows[:limit] if (event := event_from_row(row)) is not None]
        return {
            "items": events,
            "hasMore": len(rows) > limit,
            "throughEventId": rows[min(len(rows), limit) - 1]["id"] if rows else after_event_id,
        }

    def get_user_item(self, run_id: str) -> dict[str, object]:
        with self.lock:
            row = self._connection().execute(
                """
                SELECT * FROM items
                WHERE run_id = ? AND kind = 'user_message'
                ORDER BY ordinal ASC LIMIT 1
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            raise ResourceNotFoundError("user item not found")
        return _item_from_row(row, None)

    def workspace_for_run(self, run_id: str) -> WorkspaceIdentity:
        with self.lock:
            row = self._connection().execute(
                """
                SELECT sessions.workspace_root, sessions.workspace_dev,
                       sessions.workspace_inode, sessions.workspace_uid
                FROM sessions
                JOIN runs ON runs.session_id = sessions.id
                WHERE runs.id = ?
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            raise ResourceNotFoundError("run not found")
        if any(
            row[field] is None
            for field in ("workspace_dev", "workspace_inode", "workspace_uid")
        ):
            raise StorageError("workspace identity is unavailable")
        return WorkspaceIdentity(
            path=Path(row["workspace_root"]),
            device=row["workspace_dev"],
            inode=row["workspace_inode"],
            owner=row["workspace_uid"],
        )

    def increment_model_step(self, run_id: str) -> int:
        with self.lock, self._connection() as connection:
            run = connection.execute(
                "SELECT * FROM runs WHERE id = ? AND status = 'running'", (run_id,)
            ).fetchone()
            if run is None:
                raise InvalidRunStateError("run is not active")
            if run["model_step_count"] >= 80:
                raise RunLimitReached("run step limit reached")
            if run["total_effective_ms"] >= 7_200_000:
                raise RunLimitReached("run time limit reached")
            segment = connection.execute(
                """
                SELECT * FROM execution_segments
                WHERE run_id = ? AND status = 'running'
                ORDER BY ordinal DESC LIMIT 1
                """,
                (run_id,),
            ).fetchone()
            if segment is None:
                segment_id = str(uuid.uuid4())
                ordinal = connection.execute(
                    "SELECT COUNT(*) + 1 FROM execution_segments WHERE run_id = ?",
                    (run_id,),
                ).fetchone()[0]
                now = _now_ms()
                connection.execute(
                    """
                    INSERT INTO execution_segments (
                        id, run_id, ordinal, status, created_at, started_at
                    ) VALUES (?, ?, ?, 'running', ?, ?)
                    """,
                    (segment_id, run_id, ordinal, now, now),
                )
                segment = connection.execute(
                    "SELECT * FROM execution_segments WHERE id = ?", (segment_id,)
                ).fetchone()
            if segment["step_count"] >= 20:
                raise SegmentLimitReached("segment step limit reached")
            if segment["effective_ms"] >= 1_800_000:
                raise SegmentLimitReached("segment time limit reached")
            now = _now_ms()
            step_id = str(uuid.uuid4())
            attempt_id = str(uuid.uuid4())
            step_ordinal = segment["step_count"] + 1
            connection.execute(
                """
                INSERT INTO steps (
                    id, run_id, segment_id, ordinal, status,
                    observed_reconciliation_epoch, created_at
                ) VALUES (?, ?, ?, ?, 'running', ?, ?)
                """,
                (
                    step_id, run_id, segment["id"], step_ordinal,
                    run["reconciliation_epoch"], now,
                ),
            )
            connection.execute(
                """
                INSERT INTO model_attempts (
                    id, step_id, ordinal, status, started_at
                ) VALUES (?, ?, 1, 'running', ?)
                """,
                (attempt_id, step_id, now),
            )
            updated = connection.execute(
                """
                UPDATE runs
                SET model_step_count = model_step_count + 1, updated_at = ?
                WHERE id = ? AND status = 'running'
                """,
                (_now_ms(), run_id),
            )
            if updated.rowcount != 1:
                raise InvalidRunStateError("run is not active")
            connection.execute(
                "UPDATE execution_segments SET step_count = step_count + 1 WHERE id = ?",
                (segment["id"],),
            )
            row = connection.execute(
                "SELECT model_step_count FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
        return row["model_step_count"]

    def add_effective_time(self, run_id: str, elapsed_ms: int) -> None:
        if elapsed_ms <= 0:
            return
        with self.lock, self._connection() as connection:
            connection.execute(
                "UPDATE runs SET total_effective_ms = total_effective_ms + ? WHERE id = ?",
                (elapsed_ms, run_id),
            )
            connection.execute(
                """
                UPDATE execution_segments
                SET effective_ms = effective_ms + ?
                WHERE id = (
                    SELECT id FROM execution_segments
                    WHERE run_id = ? AND status = 'running'
                    ORDER BY ordinal DESC LIMIT 1
                )
                """,
                (elapsed_ms, run_id),
            )

    def complete_current_step(
        self, run_id: str, status_value: str, *, reason: str | None = None
    ) -> None:
        if status_value not in {"completed", "failed", "canceled"}:
            raise ValueError("invalid step status")
        now = _now_ms()
        with self.lock, self._connection() as connection:
            step = connection.execute(
                """
                SELECT * FROM steps
                WHERE run_id = ? AND status = 'running'
                ORDER BY creation_seq DESC LIMIT 1
                """,
                (run_id,),
            ).fetchone()
            if step is None:
                return
            connection.execute(
                "UPDATE model_attempts SET status = ?, completed_at = ? WHERE step_id = ? AND status = 'running'",
                (status_value, now, step["id"]),
            )
            connection.execute(
                "UPDATE steps SET status = ?, completed_at = ? WHERE id = ? AND status = 'running'",
                (status_value, now, step["id"]),
            )
            run = connection.execute(
                "SELECT session_id FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
            append_event(
                connection, EventType.STEP_STATUS_CHANGED, now,
                {
                    "entity_id": step["id"], "previous": "running",
                    "current": status_value, "reason": reason,
                },
                session_id=run["session_id"], run_id=run_id,
            )
            if status_value == "completed":
                run_state = connection.execute(
                    "SELECT * FROM runs WHERE id = ?", (run_id,)
                ).fetchone()
                if (
                    run_state["reconciliation_required"]
                    and step["observed_reconciliation_epoch"]
                    == run_state["reconciliation_epoch"]
                ):
                    observations = connection.execute(
                        """
                        SELECT tool_calls.result_json
                        FROM tool_calls JOIN items ON items.id = tool_calls.item_id
                        WHERE items.run_id = ?
                          AND items.model_step_index = ?
                          AND tool_calls.tool_name IN (
                            'list_files', 'read_file', 'read_file_range', 'search_text'
                          )
                          AND tool_calls.status = 'completed'
                        """,
                        (run_id, run_state["model_step_count"]),
                    ).fetchall()
                    observed = any(
                        isinstance(result, dict) and result.get("outcome") == "success"
                        for row in observations
                        for result in [_load_json_object(row["result_json"])]
                    )
                    if observed:
                        cleared = connection.execute(
                            """
                            UPDATE runs SET reconciliation_required = 0, updated_at = ?
                            WHERE id = ? AND reconciliation_required = 1
                              AND reconciliation_epoch = ?
                            """,
                            (now, run_id, step["observed_reconciliation_epoch"]),
                        )
                        if cleared.rowcount == 1:
                            append_event(
                                connection, EventType.RECONCILIATION_CLEARED, now,
                                {
                                    "epoch": step["observed_reconciliation_epoch"],
                                    "reason": "read_only_observation",
                                },
                                session_id=run["session_id"], run_id=run_id,
                            )

    def create_assistant_item(
        self, run_id: str, model_step_index: int
    ) -> dict[str, object]:
        return self._create_item(run_id, "assistant_message", model_step_index)

    def append_item_content(self, item_id: str, delta: str) -> dict[str, object]:
        with self.lock, self._connection() as connection:
            updated = connection.execute(
                """
                UPDATE items SET content = COALESCE(content, '') || ?
                WHERE id = ? AND status = 'in_progress'
                """,
                (delta, item_id),
            )
            if updated.rowcount != 1:
                raise InvalidRunStateError("item is not active")
        return self.read_item(item_id)

    def complete_assistant_item(self, item_id: str) -> dict[str, object]:
        return self._complete_item(item_id, "completed")

    def mark_assistant_incomplete(self, item_id: str) -> dict[str, object]:
        now = _now_ms()
        with self.lock, self._connection() as connection:
            updated = connection.execute(
                """
                UPDATE items
                SET status = 'failed', incomplete = 1, completed_at = ?
                WHERE id = ? AND kind = 'assistant_message'
                  AND status = 'in_progress'
                """,
                (now, item_id),
            )
            if updated.rowcount != 1:
                raise InvalidRunStateError("assistant item is not active")
        return self.read_item(item_id)

    def complete_assistant_and_run(
        self, item_id: str, run_id: str
    ) -> tuple[dict[str, object], dict[str, object]]:
        now = _now_ms()
        with self.lock, self._connection() as connection:
            run_update = connection.execute(
                """
                UPDATE runs
                SET status = 'succeeded', updated_at = ?, completed_at = ?
                WHERE id = ? AND status = 'running'
                """,
                (now, now, run_id),
            )
            if run_update.rowcount != 1:
                raise InvalidRunStateError("run is not active")
            item_update = connection.execute(
                """
                UPDATE items SET status = 'completed', completed_at = ?
                WHERE id = ? AND run_id = ? AND status = 'in_progress'
                """,
                (now, item_id, run_id),
            )
            if item_update.rowcount != 1:
                raise InvalidRunStateError("assistant item is not active")
            connection.execute(
                """
                UPDATE execution_segments SET status = 'completed', completed_at = ?
                WHERE run_id = ? AND status = 'running'
                """,
                (now, run_id),
            )
            run = _run_from_row(connection.execute(
                "SELECT * FROM runs WHERE id = ?", (run_id,)
            ).fetchone())
            append_event(
                connection, EventType.RUN_STATUS_CHANGED, now,
                {"previous": RunStatus.RUNNING, "current": RunStatus.SUCCEEDED},
                session_id=str(run["sessionId"]), run_id=run_id,
            )
        return self.read_item(item_id), run

    def create_tool_item(
        self,
        run_id: str,
        model_step_index: int,
        batch_order: int,
        provider_call_id: str,
        tool_name: str,
        arguments_json: str,
    ) -> dict[str, object]:
        item_id = str(uuid.uuid4())
        tool_call_id = str(uuid.uuid4())
        now = _now_ms()
        item_kind = (
            "file_change"
            if tool_name in {"write_file", "apply_patch"}
            else "command_execution"
            if tool_name == "run_shell"
            else "tool_call"
        )
        with self.lock, self._connection() as connection:
            run = connection.execute(
                "SELECT session_id FROM runs WHERE id = ? AND status = 'running'",
                (run_id,),
            ).fetchone()
            if run is None:
                raise InvalidRunStateError("run is not active")
            ordinal = self._next_ordinal(connection, run_id)
            connection.execute(
                """
                INSERT INTO items (
                    id, session_id, run_id, ordinal, model_step_index,
                    kind, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'in_progress', ?)
                """,
                (
                    item_id,
                    run["session_id"],
                    run_id,
                    ordinal,
                    model_step_index,
                    item_kind,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO tool_calls (
                    id, item_id, model_step_index, batch_order, provider_call_id,
                    tool_name, status, arguments_json, started_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'running', ?, ?)
                """,
                (
                    tool_call_id,
                    item_id,
                    model_step_index,
                    batch_order,
                    provider_call_id,
                    tool_name,
                    arguments_json,
                    now,
                ),
            )
            append_event(
                connection, EventType.TOOL_CALL_STARTED, now,
                {"tool_call_id": tool_call_id},
                session_id=run["session_id"], run_id=run_id,
            )
        return self.read_item(item_id)

    def complete_tool_item(
        self,
        item_id: str,
        result_json: str,
        *,
        item_status: str = "completed",
        tool_status: str = "completed",
    ) -> dict[str, object]:
        if item_status not in {"completed", "failed", "declined", "canceled"}:
            raise ValueError("invalid item status")
        if tool_status not in {"completed", "failed", "canceled"}:
            raise ValueError("invalid tool status")
        now = _now_ms()
        with self.lock, self._connection() as connection:
            fact = connection.execute(
                """
                SELECT tool_calls.id AS tool_call_id, tool_calls.tool_name,
                       items.run_id, items.session_id
                FROM tool_calls JOIN items ON items.id = tool_calls.item_id
                WHERE items.id = ?
                """,
                (item_id,),
            ).fetchone()
            if fact is None:
                raise InvalidRunStateError("tool item is unavailable")
            tool_update = connection.execute(
                """
                UPDATE tool_calls
                SET status = ?, result_json = ?, completed_at = ?
                WHERE item_id = ? AND status = 'running'
                """,
                (tool_status, result_json, now, item_id),
            )
            item_update = connection.execute(
                """
                UPDATE items SET status = ?, completed_at = ?
                WHERE id = ? AND status = 'in_progress'
                """,
                (item_status, now, item_id),
            )
            if tool_update.rowcount != 1 or item_update.rowcount != 1:
                raise InvalidRunStateError("tool item is not active")
            try:
                result = json.loads(result_json)
            except json.JSONDecodeError:
                result = {}
            reconciliation_codes = {
                "file_commit_uncertain", "outcome_unknown", "nonzero_exit",
                "shell_exit_nonzero", "timeout", "tool_timeout", "interrupted",
                "background_process", "output_capture_failed",
                "workspace_change_manifest_incomplete", "shell_resource_limit_exceeded",
            }
            reconciliation_required = (
                result.get("reconciliationRequired") is True
                or result.get("code") in reconciliation_codes
            )
            intent_status = "uncertain" if reconciliation_required else "completed"
            connection.execute(
                """
                UPDATE durable_intents SET status = ?, reconciled_at = ?
                WHERE tool_call_id = ? AND status = 'running'
                """,
                (intent_status, now, fact["tool_call_id"]),
            )
            append_event(
                connection, EventType.TOOL_CALL_COMPLETED, now,
                {"tool_call_id": fact["tool_call_id"], "code": result.get("code")},
                session_id=fact["session_id"], run_id=fact["run_id"],
            )
            if reconciliation_required:
                connection.execute(
                    """
                    UPDATE runs
                    SET reconciliation_required = 1,
                        reconciliation_epoch = reconciliation_epoch + 1,
                        side_effects_may_exist = 1,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (now, fact["run_id"]),
                )
                epoch = connection.execute(
                    "SELECT reconciliation_epoch FROM runs WHERE id = ?",
                    (fact["run_id"],),
                ).fetchone()[0]
                append_event(
                    connection, EventType.RECONCILIATION_REQUIRED, now,
                    {"epoch": epoch, "reason": str(result.get("code", "outcome_unknown"))},
                    session_id=fact["session_id"], run_id=fact["run_id"],
                )
        return self.read_item(item_id)

    def begin_approval(
        self,
        item_id: str,
        diff: str,
        base_sha256: str | None,
    ) -> dict[str, object]:
        now = _now_ms()
        approval_id = str(uuid.uuid4())
        with self.lock, self._connection() as connection:
            row = connection.execute(
                """
                SELECT items.run_id, tool_calls.id AS tool_call_id,
                       tool_calls.arguments_json
                FROM items JOIN tool_calls ON tool_calls.item_id = items.id
                WHERE items.id = ? AND items.status = 'in_progress'
                """,
                (item_id,),
            ).fetchone()
            if row is None:
                raise InvalidRunStateError("tool item is not active")
            run_update = connection.execute(
                """
                UPDATE runs SET status = 'waiting_approval', updated_at = ?
                WHERE id = ? AND status = 'running'
                """,
                (now, row["run_id"]),
            )
            tool_update = connection.execute(
                """
                UPDATE tool_calls
                SET approval_status = 'pending', approval_diff = ?, base_sha256 = ?
                WHERE item_id = ? AND status = 'running'
                """,
                (diff, base_sha256, item_id),
            )
            if run_update.rowcount != 1 or tool_update.rowcount != 1:
                raise InvalidRunStateError("approval cannot start")
            connection.execute(
                """
                INSERT INTO approvals (
                    id, tool_call_id, run_id, item_id, status,
                    request_hash, created_at
                ) VALUES (?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    approval_id, row["tool_call_id"], row["run_id"], item_id,
                    _canonical_hash({
                        "argumentsJson": row["arguments_json"],
                        "diff": diff,
                        "baseSha256": base_sha256,
                    }),
                    now,
                ),
            )
            append_event(
                connection, EventType.APPROVAL_STATUS_CHANGED, now,
                {
                    "entity_id": approval_id, "previous": "created",
                    "current": "pending",
                },
                run_id=row["run_id"],
            )
        return self.read_item(item_id)

    def resolve_approval(
        self,
        item_id: str,
        decision: str,
        feedback: str | None,
        *,
        requeue: bool = False,
    ) -> dict[str, object]:
        if decision not in {"approve", "reject"}:
            raise ValueError("invalid approval decision")
        now = _now_ms()
        with self.lock, self._connection() as connection:
            row = connection.execute(
                "SELECT run_id FROM items WHERE id = ? AND status = 'in_progress'",
                (item_id,),
            ).fetchone()
            if row is None:
                raise InvalidRunStateError("tool item is not active")
            tool_update = connection.execute(
                """
                UPDATE tool_calls
                SET approval_status = 'resolved', approval_decision = ?,
                    approval_feedback = ?
                WHERE item_id = ? AND status = 'running'
                  AND approval_status = 'pending'
                """,
                (decision, feedback, item_id),
            )
            approval = connection.execute(
                "SELECT id FROM approvals WHERE item_id = ? AND status = 'pending'",
                (item_id,),
            ).fetchone()
            if approval is None:
                raise InvalidRunStateError("approval is no longer pending")
            next_status = "approved" if decision == "approve" else "rejected"
            approval_update = connection.execute(
                """
                UPDATE approvals
                SET status = ?, decision = ?, feedback = ?, decided_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (next_status, decision, feedback, now, approval["id"]),
            )
            run_state = connection.execute(
                "SELECT consecutive_rejects FROM runs WHERE id = ?",
                (row["run_id"],),
            ).fetchone()
            rejects = run_state["consecutive_rejects"] + (1 if decision == "reject" else 0)
            run_status = (
                "waiting_user_input" if rejects >= 2
                else "queued" if requeue else "running"
            )
            run_update = connection.execute(
                """
                UPDATE runs
                SET status = ?, consecutive_rejects = ?,
                    enqueued_at = CASE WHEN ? = 'queued' THEN ? ELSE enqueued_at END,
                    pause_reason = CASE WHEN ? = 'waiting_user_input'
                        THEN 'repeated_approval_rejection' ELSE pause_reason END,
                    updated_at = ?
                WHERE id = ? AND status = 'waiting_approval'
                """,
                (run_status, rejects, run_status, now, run_status, now, row["run_id"]),
            )
            if (
                tool_update.rowcount != 1
                or approval_update.rowcount != 1
                or run_update.rowcount != 1
            ):
                raise InvalidRunStateError("approval is no longer pending")
            append_event(
                connection, EventType.APPROVAL_STATUS_CHANGED, now,
                {
                    "entity_id": approval["id"], "previous": "pending",
                    "current": next_status,
                },
                run_id=row["run_id"],
            )
        return self.read_item(item_id)

    def clear_rejects(self, run_id: str) -> None:
        with self.lock, self._connection() as connection:
            connection.execute(
                "UPDATE runs SET consecutive_rejects = 0 WHERE id = ?",
                (run_id,),
            )

    def record_sensitive_tool_input(self, run_id: str) -> int:
        with self.lock, self._connection() as connection:
            connection.execute(
                """
                UPDATE runs SET consecutive_sensitive_tool_inputs =
                    consecutive_sensitive_tool_inputs + 1
                WHERE id = ? AND status = 'running'
                """,
                (run_id,),
            )
            row = connection.execute(
                "SELECT consecutive_sensitive_tool_inputs FROM runs WHERE id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            raise ResourceNotFoundError("run not found")
        return int(row["consecutive_sensitive_tool_inputs"])

    def clear_sensitive_tool_inputs(self, run_id: str) -> None:
        with self.lock, self._connection() as connection:
            connection.execute(
                "UPDATE runs SET consecutive_sensitive_tool_inputs = 0 WHERE id = ?",
                (run_id,),
            )

    def side_effects_blocked(self, run_id: str) -> bool:
        with self.lock:
            row = self._connection().execute(
                "SELECT reconciliation_required FROM runs WHERE id = ?",
                (run_id,),
            ).fetchone()
        if row is None:
            raise ResourceNotFoundError("run not found")
        return bool(row["reconciliation_required"])

    def begin_durable_intent(
        self,
        item_id: str,
        *,
        preconditions: dict[str, object],
    ) -> str:
        intent_id = str(uuid.uuid4())
        nonce = str(uuid.uuid4())
        now = _now_ms()
        with self.lock, self._connection() as connection:
            row = connection.execute(
                """
                SELECT items.run_id, items.session_id, tool_calls.id AS tool_call_id,
                       tool_calls.arguments_json
                FROM items JOIN tool_calls ON tool_calls.item_id = items.id
                JOIN approvals ON approvals.item_id = items.id
                WHERE items.id = ? AND items.status = 'in_progress'
                  AND tool_calls.status = 'running'
                  AND approvals.status = 'approved'
                """,
                (item_id,),
            ).fetchone()
            if row is None:
                raise InvalidRunStateError("approved intent is unavailable")
            connection.execute(
                """
                INSERT INTO durable_intents (
                    id, run_id, tool_call_id, execution_nonce,
                    arguments_hash, preconditions_json, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'running', ?)
                """,
                (
                    intent_id, row["run_id"], row["tool_call_id"], nonce,
                    hashlib.sha256(row["arguments_json"].encode("utf-8")).hexdigest(),
                    json.dumps(preconditions, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                    now,
                ),
            )
            append_event(
                connection, EventType.TOOL_CALL_STARTED, now,
                {"tool_call_id": row["tool_call_id"]},
                session_id=row["session_id"], run_id=row["run_id"],
            )
        return intent_id

    def has_read_evidence(
        self, run_id: str, path: str, sha256: str
    ) -> bool:
        with self.lock:
            rows = self._connection().execute(
                """
                SELECT tool_calls.arguments_json, tool_calls.result_json
                FROM tool_calls
                JOIN items ON items.id = tool_calls.item_id
                WHERE items.run_id = ? AND items.status = 'completed'
                  AND tool_calls.tool_name = 'read_file'
                  AND tool_calls.status = 'completed'
                ORDER BY tool_calls.creation_seq DESC
                """,
                (run_id,),
            ).fetchall()
        for row in rows:
            try:
                arguments = json.loads(row["arguments_json"])
                result = json.loads(row["result_json"])
            except (TypeError, json.JSONDecodeError):
                continue
            if (
                arguments == {"path": path}
                and result.get("outcome") == "success"
                and result.get("data", {}).get("sha256") == sha256
            ):
                return True
        return False

    def record_protocol_error(self, run_id: str) -> int:
        with self.lock, self._connection() as connection:
            updated = connection.execute(
                """
                UPDATE runs
                SET consecutive_protocol_errors = consecutive_protocol_errors + 1,
                    updated_at = ?
                WHERE id = ? AND status = 'running'
                """,
                (_now_ms(), run_id),
            )
            if updated.rowcount != 1:
                raise InvalidRunStateError("run is not active")
            row = connection.execute(
                "SELECT consecutive_protocol_errors FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
        return row["consecutive_protocol_errors"]

    def clear_protocol_errors(self, run_id: str) -> None:
        with self.lock, self._connection() as connection:
            connection.execute(
                """
                UPDATE runs SET consecutive_protocol_errors = 0, updated_at = ?
                WHERE id = ? AND status = 'running'
                """,
                (_now_ms(), run_id),
            )

    def pause_run(self, run_id: str, reason: str) -> dict[str, object]:
        now = _now_ms()
        with self.lock, self._connection() as connection:
            updated = connection.execute(
                """
                UPDATE runs
                SET status = 'waiting_user_input', pause_reason = ?, updated_at = ?
                WHERE id = ? AND status = 'running'
                """,
                (reason, now, run_id),
            )
            if updated.rowcount != 1:
                raise InvalidRunStateError("run cannot pause")
            connection.execute(
                """
                UPDATE execution_segments
                SET status = 'waiting_user_input', completed_at = ?
                WHERE run_id = ? AND status = 'running'
                """,
                (now, run_id),
            )
            run = _run_from_row(connection.execute(
                "SELECT * FROM runs WHERE id = ?", (run_id,)
            ).fetchone())
            append_event(
                connection, EventType.RUN_STATUS_CHANGED, now,
                {
                    "previous": RunStatus.RUNNING,
                    "current": RunStatus.WAITING_USER_INPUT,
                    "reason": reason,
                },
                session_id=str(run["sessionId"]), run_id=run_id,
            )
        return run

    def begin_finalization(self, run_id: str) -> dict[str, object]:
        now = _now_ms()
        with self.lock, self._connection() as connection:
            updated = connection.execute(
                "UPDATE runs SET status = 'finalizing', updated_at = ? WHERE id = ? AND status = 'running'",
                (now, run_id),
            )
            if updated.rowcount != 1:
                raise InvalidRunStateError("run cannot finalize")
            run = _run_from_row(connection.execute(
                "SELECT * FROM runs WHERE id = ?", (run_id,)
            ).fetchone())
            append_event(
                connection, EventType.RUN_STATUS_CHANGED, now,
                {"previous": RunStatus.RUNNING, "current": RunStatus.FINALIZING, "reason": "run_limit"},
                session_id=str(run["sessionId"]), run_id=run_id,
            )
        return run

    def stop_run(self, run_id: str, reason: str) -> dict[str, object]:
        now = _now_ms()
        with self.lock, self._connection() as connection:
            updated = connection.execute(
                """
                UPDATE runs SET status = 'stopped', stop_reason = ?,
                    updated_at = ?, completed_at = ?
                WHERE id = ? AND status = 'finalizing'
                """,
                (reason, now, now, run_id),
            )
            if updated.rowcount != 1:
                raise InvalidRunStateError("run cannot stop")
            connection.execute(
                """
                UPDATE execution_segments SET status = 'completed', completed_at = ?
                WHERE run_id = ? AND status = 'running'
                """,
                (now, run_id),
            )
            run = _run_from_row(connection.execute(
                "SELECT * FROM runs WHERE id = ?", (run_id,)
            ).fetchone())
            append_event(
                connection, EventType.RUN_STATUS_CHANGED, now,
                {"previous": RunStatus.FINALIZING, "current": RunStatus.STOPPED, "reason": reason},
                session_id=str(run["sessionId"]), run_id=run_id,
            )
        return run

    def fail_run(self, run_id: str, error_code: str) -> dict[str, object]:
        now = _now_ms()
        with self.lock, self._connection() as connection:
            previous = connection.execute(
                "SELECT status FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
            if previous is None or previous["status"] not in {"running", "waiting_approval"}:
                raise InvalidRunStateError("run is not active")
            connection.execute(
                """
                UPDATE tool_calls SET status = 'canceled', completed_at = ?
                WHERE status = 'running'
                  AND item_id IN (SELECT id FROM items WHERE run_id = ?)
                """,
                (now, run_id),
            )
            connection.execute(
                """
                UPDATE tool_calls SET approval_status = 'canceled'
                WHERE approval_status = 'pending'
                  AND item_id IN (SELECT id FROM items WHERE run_id = ?)
                """,
                (run_id,),
            )
            connection.execute(
                """
                UPDATE items SET status = 'canceled', completed_at = ?
                WHERE run_id = ? AND status = 'in_progress'
                """,
                (now, run_id),
            )
            updated = connection.execute(
                """
                UPDATE runs
                SET status = 'failed', error_code = ?, updated_at = ?, completed_at = ?
                WHERE id = ? AND status IN ('running', 'waiting_approval')
                """,
                (error_code, now, now, run_id),
            )
            if updated.rowcount != 1:
                raise InvalidRunStateError("run is not active")
            run = _run_from_row(connection.execute(
                "SELECT * FROM runs WHERE id = ?", (run_id,)
            ).fetchone())
            append_event(
                connection, EventType.RUN_STATUS_CHANGED, now,
                {"previous": RunStatus(previous["status"]), "current": RunStatus.FAILED, "reason": error_code},
                session_id=str(run["sessionId"]), run_id=run_id,
            )
        return run

    def cancel_run(
        self, run_id: str, *, operation_id: str | None = None
    ) -> dict[str, object]:
        def write(connection: sqlite3.Connection) -> dict[str, object]:
            now = _now_ms()
            row = connection.execute(
                "SELECT status FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise ResourceNotFoundError("run not found")
            if row["status"] == "canceled":
                return _run_from_row(connection.execute(
                    "SELECT * FROM runs WHERE id = ?", (run_id,)
                ).fetchone())
            if row["status"] not in {
                "queued", "running", "waiting_approval", "waiting_user_input"
            }:
                raise InvalidRunStateError("run cannot be canceled")
            connection.execute(
                """
                UPDATE tool_calls SET status = 'canceled', completed_at = ?
                WHERE status = 'running'
                  AND item_id IN (SELECT id FROM items WHERE run_id = ?)
                """,
                (now, run_id),
            )
            connection.execute(
                """
                UPDATE tool_calls SET approval_status = 'canceled'
                WHERE approval_status = 'pending'
                  AND item_id IN (SELECT id FROM items WHERE run_id = ?)
                """,
                (run_id,),
            )
            connection.execute(
                """
                UPDATE items SET status = 'canceled', completed_at = ?
                WHERE run_id = ? AND status = 'in_progress'
                """,
                (now, run_id),
            )
            connection.execute(
                """
                UPDATE runs
                SET status = 'canceled', updated_at = ?, completed_at = ?
                WHERE id = ?
                """,
                (now, now, run_id),
            )
            run = _run_from_row(connection.execute(
                "SELECT * FROM runs WHERE id = ?", (run_id,)
            ).fetchone())
            append_event(
                connection, EventType.RUN_STATUS_CHANGED, now,
                {"previous": RunStatus(row["status"]), "current": RunStatus.CANCELED},
                session_id=str(run["sessionId"]), run_id=run_id,
            )
            return run

        return self._write(
            write,
            operation_id=operation_id,
            operation_scope="run/cancel" if operation_id is not None else None,
            operation_request={"runId": run_id} if operation_id is not None else None,
        )

    def interrupt_run(self, run_id: str) -> dict[str, object]:
        now = _now_ms()
        with self.lock, self._connection() as connection:
            row = connection.execute(
                "SELECT status FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise ResourceNotFoundError("run not found")
            if row["status"] == "interrupted":
                return self.read_run(run_id)
            if row["status"] not in {"running", "waiting_approval"}:
                raise InvalidRunStateError("run cannot be interrupted")
            connection.execute(
                """
                UPDATE tool_calls SET status = 'canceled', completed_at = ?
                WHERE status = 'running'
                  AND item_id IN (SELECT id FROM items WHERE run_id = ?)
                """,
                (now, run_id),
            )
            connection.execute(
                """
                UPDATE tool_calls SET approval_status = 'canceled'
                WHERE approval_status = 'pending'
                  AND item_id IN (SELECT id FROM items WHERE run_id = ?)
                """,
                (run_id,),
            )
            connection.execute(
                """
                UPDATE items SET status = 'canceled', completed_at = ?
                WHERE run_id = ? AND status = 'in_progress'
                """,
                (now, run_id),
            )
            connection.execute(
                """
                UPDATE runs
                SET status = 'interrupted', error_code = 'RUNTIME_INTERRUPTED',
                    updated_at = ?, completed_at = ?
                WHERE id = ?
                """,
                (now, now, run_id),
            )
            run = _run_from_row(connection.execute(
                "SELECT * FROM runs WHERE id = ?", (run_id,)
            ).fetchone())
            append_event(
                connection, EventType.RUN_STATUS_CHANGED, now,
                {"previous": RunStatus(row["status"]), "current": RunStatus.INTERRUPTED, "reason": "runtime_interrupted"},
                session_id=str(run["sessionId"]), run_id=run_id,
            )
        return run

    def canceled_items_for_run(self, run_id: str) -> list[dict[str, object]]:
        with self.lock:
            connection = self._connection()
            rows = connection.execute(
                """
                SELECT * FROM items
                WHERE run_id = ? AND status = 'canceled'
                ORDER BY ordinal ASC
                """,
                (run_id,),
            ).fetchall()
            tool_rows = connection.execute(
                """
                SELECT tool_calls.* FROM tool_calls
                JOIN items ON items.id = tool_calls.item_id
                WHERE items.run_id = ? AND items.status = 'canceled'
                """,
                (run_id,),
            ).fetchall()
        tools_by_item = {row["item_id"]: row for row in tool_rows}
        return [
            _item_from_row(row, tools_by_item.get(row["id"])) for row in rows
        ]

    def model_context(self, session_id: str) -> tuple[dict[str, object], ...]:
        with self.lock:
            connection = self._connection()
            item_rows = connection.execute(
                """
                SELECT * FROM items
                WHERE session_id = ? AND status IN ('completed', 'failed', 'declined')
                ORDER BY creation_seq DESC LIMIT ?
                """,
                (session_id, MAX_CONTEXT_ITEMS + 1),
            ).fetchall()
            if len(item_rows) > MAX_CONTEXT_ITEMS:
                raise ContextLimitExceeded("model context item limit exceeded")
            tool_rows: list[sqlite3.Row] = []
            if item_rows:
                placeholders = ",".join("?" for _ in item_rows)
                tool_rows = connection.execute(
                    f"SELECT * FROM tool_calls WHERE item_id IN ({placeholders})",
                    [row["id"] for row in item_rows],
                ).fetchall()
        tools_by_item = {row["item_id"]: row for row in tool_rows}
        groups: list[list[dict[str, object]]] = []
        context_bytes = 0
        for row in item_rows:
            item = _item_from_row(row, tools_by_item.get(row["id"]))
            group: list[dict[str, object]] = []
            if item["kind"] == "user_message":
                group.append({"type": "user", "content": item.get("content", "")})
            elif item["kind"] == "assistant_message":
                group.append(
                    {"type": "assistant", "content": item.get("content", "")}
                )
            elif item["kind"] in {"tool_call", "file_change", "command_execution"}:
                tool_call = item["toolCall"]
                group.append(
                    {
                        "type": "tool_call",
                        "callId": tool_call["providerCallId"],
                        "name": tool_call["toolName"],
                        "arguments": tool_call["argumentsJson"],
                    }
                )
                group.append(
                    {
                        "type": "tool_result",
                        "callId": tool_call["providerCallId"],
                        "name": tool_call["toolName"],
                        "result": tool_call["resultJson"],
                    }
                )
            group_bytes = len(
                json.dumps(group, ensure_ascii=False, separators=(",", ":")).encode(
                    "utf-8"
                )
            )
            if context_bytes + group_bytes > MAX_CONTEXT_BYTES:
                raise ContextLimitExceeded("model context byte limit exceeded")
            groups.append(group)
            context_bytes += group_bytes
        context: list[dict[str, object]] = []
        for group in reversed(groups):
            context.extend(group)
        return tuple(context)

    def _create_item(
        self, run_id: str, kind: str, model_step_index: int | None
    ) -> dict[str, object]:
        item_id = str(uuid.uuid4())
        now = _now_ms()
        with self.lock, self._connection() as connection:
            run = connection.execute(
                "SELECT session_id FROM runs WHERE id = ? AND status IN ('running', 'finalizing')",
                (run_id,),
            ).fetchone()
            if run is None:
                raise InvalidRunStateError("run is not active")
            ordinal = self._next_ordinal(connection, run_id)
            connection.execute(
                """
                INSERT INTO items (
                    id, session_id, run_id, ordinal, model_step_index,
                    kind, status, content, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'in_progress', '', ?)
                """,
                (
                    item_id,
                    run["session_id"],
                    run_id,
                    ordinal,
                    model_step_index,
                    kind,
                    now,
                ),
            )
        return self.read_item(item_id)

    def _complete_item(self, item_id: str, status_value: str) -> dict[str, object]:
        with self.lock, self._connection() as connection:
            updated = connection.execute(
                """
                UPDATE items SET status = ?, completed_at = ?
                WHERE id = ? AND status = 'in_progress'
                """,
                (status_value, _now_ms(), item_id),
            )
            if updated.rowcount != 1:
                raise InvalidRunStateError("item is not active")
        return self.read_item(item_id)

    @staticmethod
    def _next_ordinal(connection: sqlite3.Connection, run_id: str) -> int:
        row = connection.execute(
            "SELECT COALESCE(MAX(ordinal), 0) + 1 AS next_ordinal FROM items WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        return row["next_ordinal"]

    def _connection(self) -> sqlite3.Connection:
        if self.connection is None:
            raise StorageError("storage is not initialized")
        return self.connection

    def _write(
        self,
        action: Callable[[sqlite3.Connection], T],
        *,
        operation_id: str | None = None,
        operation_scope: str | None = None,
        operation_request: dict[str, object] | None = None,
    ) -> T:
        with self.lock, self._connection() as connection:
            if operation_id is None:
                return action(connection)
            assert operation_scope is not None and operation_request is not None
            request_hash = _canonical_hash(operation_request)
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
            now = _now_ms()
            connection.execute(
                """
                INSERT INTO operations (
                    id, scope, request_hash, status, created_at
                ) VALUES (?, ?, ?, 'in_progress', ?)
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
                    _now_ms(), operation_id, operation_scope,
                ),
            )
            return result

    def operation_result(
        self, operation_id: str, scope: str, request: dict[str, object]
    ) -> object | None:
        request_hash = _canonical_hash(request)
        with self.lock:
            row = self._connection().execute(
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

    def _workspace_overlaps_data(self, workspace: Path) -> bool:
        data_directory = self.data_directory
        if data_directory is None:
            raise StorageError("storage is not initialized")
        workspace = workspace.resolve(strict=False)
        return (
            workspace == data_directory
            or workspace in data_directory.parents
            or data_directory in workspace.parents
        )


def _default_data_directory() -> Path:
    configured = os.environ.get("EIDOS_DATA_DIR")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".eidos"


def _safe_health_code(error: BaseException) -> str:
    if isinstance(error, StorageError):
        code = str(error)
        if code in {
            "state_locked", "schema_revision_missing", "schema_revision_unsupported",
            "database_corrupt", "foreign_key_violation", "storage_pragmas_invalid",
            "migration_failed", "backup_failed", "reserve_invalid",
        }:
            return code
        return "state_security_invalid"
    if isinstance(error, sqlite3.DatabaseError):
        return "database_corrupt"
    return "storage_io_error"


def _acquire_state_lock(path: Path) -> int:
    if path.is_symlink():
        raise StorageError("state_security_invalid")
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
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
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, 0o600)
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
        ).fetchall()
    }


def _verify_pragmas(connection: sqlite3.Connection) -> None:
    foreign_keys = connection.execute("PRAGMA foreign_keys").fetchone()[0]
    journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
    busy_timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]
    if foreign_keys != 1 or str(journal_mode).lower() != "wal" or busy_timeout < 5000:
        raise StorageError("storage_pragmas_invalid")


def _verify_integrity(connection: sqlite3.Connection) -> None:
    result = connection.execute("PRAGMA integrity_check").fetchone()[0]
    if result != "ok":
        raise StorageError("database_corrupt")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise StorageError("foreign_key_violation")


def _backup_database(
    connection: sqlite3.Connection, database_path: Path, revision: int
) -> None:
    stamp = _now_ms()
    backup_path = database_path.with_name(f"{database_path.name}.rev{revision}.{stamp}.bak")
    manifest_path = backup_path.with_suffix(backup_path.suffix + ".json")
    try:
        destination = sqlite3.connect(backup_path)
        try:
            connection.backup(destination)
        finally:
            destination.close()
        os.chmod(backup_path, 0o600)
        digest = hashlib.sha256(backup_path.read_bytes()).hexdigest()
        manifest = {
            "source": database_path.name,
            "backup": backup_path.name,
            "sourceRevision": revision,
            "sha256": digest,
            "createdAt": stamp,
        }
        descriptor = os.open(
            manifest_path,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            os.write(descriptor, json.dumps(manifest, sort_keys=True).encode("ascii"))
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        if hashlib.sha256(backup_path.read_bytes()).hexdigest() != digest:
            raise StorageError("backup_failed")
    except (OSError, sqlite3.Error, StorageError):
        raise StorageError("backup_failed") from None


def _migrate_v1_to_v2(connection: sqlite3.Connection) -> None:
    try:
        connection.commit()
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.executescript(
            """
            BEGIN IMMEDIATE;
            CREATE TABLE runs_v2 (
                creation_seq INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT NOT NULL UNIQUE,
                session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE RESTRICT,
                user_input TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN (
                    'queued', 'running', 'waiting_approval', 'waiting_user_input',
                    'finalizing', 'succeeded', 'failed', 'stopped', 'canceled',
                    'interrupted'
                )),
                model_step_count INTEGER NOT NULL DEFAULT 0,
                consecutive_protocol_errors INTEGER NOT NULL DEFAULT 0,
                consecutive_rejects INTEGER NOT NULL DEFAULT 0,
                consecutive_sensitive_tool_inputs INTEGER NOT NULL DEFAULT 0,
                enqueued_at INTEGER,
                total_effective_ms INTEGER NOT NULL DEFAULT 0,
                pause_reason TEXT,
                stop_reason TEXT,
                reconciliation_required INTEGER NOT NULL DEFAULT 0,
                reconciliation_epoch INTEGER NOT NULL DEFAULT 0,
                side_effects_may_exist INTEGER NOT NULL DEFAULT 0,
                error_code TEXT,
                created_at INTEGER NOT NULL,
                started_at INTEGER,
                updated_at INTEGER NOT NULL,
                completed_at INTEGER
            );
            INSERT INTO runs_v2 (
                creation_seq, id, session_id, user_input, status,
                model_step_count, consecutive_protocol_errors, error_code,
                created_at, started_at, updated_at, completed_at
            ) SELECT
                creation_seq, id, session_id, user_input, status,
                model_step_count, consecutive_protocol_errors, error_code,
                created_at, started_at, updated_at, completed_at
            FROM runs;
            DROP TABLE runs;
            ALTER TABLE runs_v2 RENAME TO runs;
            PRAGMA user_version = 2;
            COMMIT;
            """
        )
        connection.execute("PRAGMA foreign_keys = ON")
    except (sqlite3.Error, OSError):
        connection.rollback()
        connection.execute("PRAGMA foreign_keys = ON")
        raise StorageError("migration_failed") from None


def _migrate_v2_to_v3(connection: sqlite3.Connection) -> None:
    try:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(sessions)").fetchall()
        }
        if "title" not in columns:
            connection.execute("ALTER TABLE sessions ADD COLUMN title TEXT")
        connection.execute("PRAGMA user_version = 3")
        connection.commit()
    except sqlite3.Error:
        connection.rollback()
        raise StorageError("migration_failed") from None


def _migrate_v3_to_v4(connection: sqlite3.Connection) -> None:
    try:
        tables = _table_names(connection)
        if "runs" in tables:
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(runs)").fetchall()
            }
            if "model_id" not in columns:
                connection.execute(
                    """
                    ALTER TABLE runs ADD COLUMN model_id TEXT NOT NULL
                    DEFAULT 'deepseek-v4-flash'
                    """
                )
        connection.execute("PRAGMA user_version = 4")
        connection.commit()
    except sqlite3.Error:
        connection.rollback()
        raise StorageError("migration_failed") from None


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


def _ensure_session_identity_columns(connection: sqlite3.Connection) -> None:
    columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(sessions)").fetchall()
    }
    for name in ("workspace_dev", "workspace_inode", "workspace_uid"):
        if name not in columns:
            connection.execute(f"ALTER TABLE sessions ADD COLUMN {name} INTEGER")


def _ensure_tool_call_approval_columns(connection: sqlite3.Connection) -> None:
    columns = {
        row["name"]
        for row in connection.execute("PRAGMA table_info(tool_calls)").fetchall()
    }
    for name in (
        "approval_status",
        "approval_decision",
        "approval_feedback",
        "approval_diff",
        "base_sha256",
    ):
        if name not in columns:
            connection.execute(f"ALTER TABLE tool_calls ADD COLUMN {name} TEXT")


def _ensure_phase_two_columns(connection: sqlite3.Connection) -> None:
    item_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(items)").fetchall()
    }
    if "incomplete" not in item_columns:
        connection.execute(
            "ALTER TABLE items ADD COLUMN incomplete INTEGER NOT NULL DEFAULT 0"
        )


def _backfill_session_identities(connection: sqlite3.Connection) -> None:
    rows = connection.execute(
        """
        SELECT id, workspace_root FROM sessions
        WHERE workspace_dev IS NULL OR workspace_inode IS NULL OR workspace_uid IS NULL
        """
    ).fetchall()
    for row in rows:
        try:
            workspace = _canonical_workspace(row["workspace_root"])
            metadata = workspace.stat()
        except (OSError, WorkspaceBoundaryError):
            continue
        connection.execute(
            """
            UPDATE sessions
            SET workspace_root = ?, workspace_dev = ?, workspace_inode = ?, workspace_uid = ?
            WHERE id = ?
            """,
            (
                str(workspace),
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_uid,
                row["id"],
            ),
        )


def _canonical_workspace(value: str) -> Path:
    if not value or len(value) > 4096:
        raise WorkspaceBoundaryError("workspace path is invalid")
    path = Path(value)
    if not path.is_absolute() or path.is_symlink() or not path.is_dir():
        raise WorkspaceBoundaryError("workspace must be an existing absolute directory")
    return path.resolve()


def _session_from_row(row: sqlite3.Row) -> dict[str, object]:
    return SessionDto.model_validate({
        "id": row["id"],
        "workspaceRoot": row["workspace_root"],
        "title": row["title"],
        "taskStatus": row["task_status"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }).to_json_value()


def _json_bytes(value: object) -> int:
    return len(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _load_json_object(value: object) -> dict[str, object] | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _snapshot_item(
    row: sqlite3.Row, tool_row: sqlite3.Row | None
) -> dict[str, object]:
    item = _item_from_row(row, tool_row)
    content = item.get("content")
    if isinstance(content, str):
        item["content"] = _truncate_snapshot_text(content)
    tool_call = item.get("toolCall")
    if isinstance(tool_call, dict):
        projected = dict(tool_call)
        projected.pop("argumentsJson", None)
        projected.pop("approvalDiff", None)
        result = projected.get("resultJson")
        if isinstance(result, str):
            projected["resultJson"] = _truncate_snapshot_text(result)
        item["toolCall"] = projected
    return item


def _truncate_snapshot_text(value: str) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= MAX_SNAPSHOT_TEXT_BYTES:
        return value
    marker = "\n…[history truncated]"
    budget = MAX_SNAPSHOT_TEXT_BYTES - len(marker.encode("utf-8"))
    prefix = encoded[:budget]
    while True:
        try:
            return prefix.decode("utf-8") + marker
        except UnicodeDecodeError as error:
            prefix = prefix[: error.start]


def _run_from_row(
    row: sqlite3.Row, *, include_user_input: bool = True
) -> dict[str, object]:
    run: dict[str, object] = {
        "id": row["id"],
        "sessionId": row["session_id"],
        "modelId": row["model_id"],
        "status": row["status"],
        "modelStepCount": row["model_step_count"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }
    allowed_actions = {
        "queued": ["cancel"],
        "running": ["cancel"],
        "waiting_approval": ["approve", "reject", "cancel"],
        "waiting_user_input": ["continue", "cancel"],
    }.get(row["status"], [])
    if allowed_actions:
        run["allowedActions"] = allowed_actions
    if row["started_at"] is not None:
        run["startedAt"] = row["started_at"]
    if include_user_input:
        run["userInput"] = row["user_input"]
    if row["completed_at"] is not None:
        run["completedAt"] = row["completed_at"]
    if row["error_code"] is not None:
        run["errorCode"] = row["error_code"]
    if "pause_reason" in row.keys() and row["pause_reason"] is not None:
        run["pauseReason"] = row["pause_reason"]
    if "stop_reason" in row.keys() and row["stop_reason"] is not None:
        run["stopReason"] = row["stop_reason"]
    if "side_effects_may_exist" in row.keys():
        run["sideEffectsMayExist"] = bool(row["side_effects_may_exist"])
    return RunDto.model_validate(run).to_json_value()


def _item_from_row(
    row: sqlite3.Row, tool_row: sqlite3.Row | None
) -> dict[str, object]:
    item: dict[str, object] = {
        "id": row["id"],
        "sessionId": row["session_id"],
        "runId": row["run_id"],
        "ordinal": row["ordinal"],
        "kind": row["kind"],
        "status": row["status"],
        "createdAt": row["created_at"],
    }
    if row["model_step_index"] is not None:
        item["modelStepIndex"] = row["model_step_index"]
    if row["content"] is not None:
        item["content"] = row["content"]
    if "incomplete" in row.keys() and row["incomplete"]:
        item["incomplete"] = True
    if row["completed_at"] is not None:
        item["completedAt"] = row["completed_at"]
    if tool_row is not None:
        tool_call: dict[str, object] = {
            "id": tool_row["id"],
            "itemId": tool_row["item_id"],
            "modelStepIndex": tool_row["model_step_index"],
            "batchOrder": tool_row["batch_order"],
            "providerCallId": tool_row["provider_call_id"],
            "toolName": tool_row["tool_name"],
            "status": tool_row["status"],
            "argumentsJson": tool_row["arguments_json"],
            "startedAt": tool_row["started_at"],
        }
        if tool_row["result_json"] is not None:
            tool_call["resultJson"] = tool_row["result_json"]
        if tool_row["completed_at"] is not None:
            tool_call["completedAt"] = tool_row["completed_at"]
        if tool_row["approval_status"] is not None:
            tool_call["approvalStatus"] = tool_row["approval_status"]
        if tool_row["approval_decision"] is not None:
            tool_call["approvalDecision"] = tool_row["approval_decision"]
        if tool_row["approval_feedback"] is not None:
            tool_call["approvalFeedback"] = tool_row["approval_feedback"]
        if tool_row["approval_diff"] is not None:
            tool_call["approvalDiff"] = tool_row["approval_diff"]
        if tool_row["base_sha256"] is not None:
            tool_call["baseSha256"] = tool_row["base_sha256"]
        item["toolCall"] = tool_call
    return ItemDto.model_validate(item).to_json_value()


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


def _encode_cursor(high_water: int, before_sequence: int) -> str:
    payload = (
        SESSION_CURSOR_PREFIX
        + json.dumps(
            {
                "scope": "sessions",
                "order": "creation_seq_desc",
                "highWater": high_water,
                "before": before_sequence,
            },
            separators=(",", ":"),
            sort_keys=True,
        )
    ).encode("ascii")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> tuple[int, int]:
    if not cursor or len(cursor) > 512:
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
        state = json.loads(decoded.removeprefix(SESSION_CURSOR_PREFIX))
        if (
            not isinstance(state, dict)
            or set(state) != {"scope", "order", "highWater", "before"}
            or state["scope"] != "sessions"
            or state["order"] != "creation_seq_desc"
            or not isinstance(state["highWater"], int)
            or not isinstance(state["before"], int)
            or isinstance(state["highWater"], bool)
            or isinstance(state["before"], bool)
            or state["highWater"] < 0
            or not 0 < state["before"] <= state["highWater"]
        ):
            raise ValueError
        return state["highWater"], state["before"]
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        raise InvalidCursorError("cursor is invalid") from None

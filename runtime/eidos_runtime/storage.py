from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sqlite3
import stat
import threading
import time
import uuid


DATABASE_NAME = "eidos.db"
DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 200
SESSION_CURSOR_PREFIX = "session-v1:"
MAX_CONTEXT_BYTES = 768 * 1024
MAX_CONTEXT_ITEMS = 200
MAX_SNAPSHOT_BYTES = 768 * 1024


class StorageError(RuntimeError):
    pass


class WorkspaceBoundaryError(ValueError):
    pass


class InvalidCursorError(ValueError):
    pass


class ActiveRunError(RuntimeError):
    pass


class ResourceNotFoundError(LookupError):
    pass


class InvalidRunStateError(RuntimeError):
    pass


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

    def initialize(self) -> None:
        with self.lock:
            if self.connection is not None:
                raise StorageError("storage is already initialized")

            data_directory = self.data_directory or _default_data_directory()
            if not data_directory.is_absolute():
                raise StorageError("data directory must be absolute")
            _prepare_private_directory(data_directory)
            database_path = data_directory.resolve() / DATABASE_NAME
            _prepare_private_database(database_path)

            connection = sqlite3.connect(database_path, check_same_thread=False)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    creation_seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT NOT NULL UNIQUE,
                    workspace_root TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS runs (
                    creation_seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    id TEXT NOT NULL UNIQUE,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE RESTRICT,
                    user_input TEXT NOT NULL,
                    status TEXT NOT NULL CHECK (status IN (
                        'running', 'waiting_approval', 'succeeded', 'failed',
                        'canceled', 'interrupted'
                    )),
                    model_step_count INTEGER NOT NULL DEFAULT 0,
                    consecutive_protocol_errors INTEGER NOT NULL DEFAULT 0,
                    error_code TEXT,
                    created_at INTEGER NOT NULL,
                    started_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    completed_at INTEGER
                );

                CREATE UNIQUE INDEX IF NOT EXISTS one_active_run
                ON runs ((1))
                WHERE status IN ('running', 'waiting_approval');

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
                    started_at INTEGER NOT NULL,
                    completed_at INTEGER
                );
                """
            )
            _ensure_session_identity_columns(connection)
            _backfill_session_identities(connection)
            now = _now_ms()
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
            self.connection = connection

    def close(self) -> None:
        with self.lock:
            if self.connection is None:
                return
            self.connection.close()
            self.connection = None

    def create_session(self, workspace_root: str) -> dict[str, object]:
        workspace = _canonical_workspace(workspace_root)
        metadata = workspace.stat()
        session_id = str(uuid.uuid4())
        now = time.time_ns() // 1_000_000
        with self.lock, self._connection() as connection:
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

        with self.lock:
            rows = self._connection().execute(sql, parameters).fetchall()
        has_more = len(rows) > limit
        page = rows[:limit]
        result: dict[str, object] = {"items": [_session_from_row(row) for row in page]}
        if has_more:
            result["nextCursor"] = _encode_cursor(page[-1]["creation_seq"])
        return result

    def read_session(self, session_id: str) -> dict[str, object] | None:
        with self.lock:
            row = self._connection().execute(
                """
                SELECT id, workspace_root, created_at, updated_at
                FROM sessions
                WHERE id = ?
                """,
                (session_id,),
            ).fetchone()
        return _session_from_row(row) if row is not None else None

    def create_run(
        self, session_id: str, user_input: str
    ) -> tuple[dict[str, object], dict[str, object]]:
        run_id = str(uuid.uuid4())
        item_id = str(uuid.uuid4())
        now = _now_ms()
        with self.lock, self._connection() as connection:
            session = connection.execute(
                "SELECT id FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if session is None:
                raise ResourceNotFoundError("session not found")
            try:
                connection.execute(
                    """
                    INSERT INTO runs (
                        id, session_id, user_input, status, created_at,
                        started_at, updated_at
                    ) VALUES (?, ?, ?, 'running', ?, ?, ?)
                    """,
                    (run_id, session_id, user_input, now, now, now),
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
        return self.read_run(run_id), self.read_item(item_id)

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
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
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
        tools_by_item = {row["item_id"]: row for row in tool_rows}
        has_more = len(item_rows) > item_limit
        selected_items: list[dict[str, object]] = []
        selected_bytes = 0
        for row in item_rows[:item_limit]:
            item = _item_from_row(row, tools_by_item.get(row["id"]))
            item_bytes = len(
                json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode(
                    "utf-8"
                )
            )
            if selected_items and selected_bytes + item_bytes > MAX_SNAPSHOT_BYTES:
                has_more = True
                break
            selected_items.append(item)
            selected_bytes += item_bytes
        selected_items.reverse()
        snapshot: dict[str, object] = {
            "session": _session_from_row(session_row),
            "runs": [_run_from_row(row) for row in reversed(run_rows)],
            "items": selected_items,
        }
        if has_more and selected_items:
            snapshot["previousItemId"] = selected_items[0]["id"]
        return snapshot

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
            row = connection.execute(
                "SELECT model_step_count FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
        return row["model_step_count"]

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
        return self.read_item(item_id), self.read_run(run_id)

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
                ) VALUES (?, ?, ?, ?, ?, 'tool_call', 'in_progress', ?)
                """,
                (item_id, run["session_id"], run_id, ordinal, model_step_index, now),
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
        return self.read_item(item_id)

    def complete_tool_item(
        self, item_id: str, result_json: str
    ) -> dict[str, object]:
        now = _now_ms()
        with self.lock, self._connection() as connection:
            tool_update = connection.execute(
                """
                UPDATE tool_calls
                SET status = 'completed', result_json = ?, completed_at = ?
                WHERE item_id = ? AND status = 'running'
                """,
                (result_json, now, item_id),
            )
            item_update = connection.execute(
                """
                UPDATE items SET status = 'completed', completed_at = ?
                WHERE id = ? AND status = 'in_progress'
                """,
                (now, item_id),
            )
            if tool_update.rowcount != 1 or item_update.rowcount != 1:
                raise InvalidRunStateError("tool item is not active")
        return self.read_item(item_id)

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

    def fail_run(self, run_id: str, error_code: str) -> dict[str, object]:
        now = _now_ms()
        with self.lock, self._connection() as connection:
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
                UPDATE items SET status = 'canceled', completed_at = ?
                WHERE run_id = ? AND status = 'in_progress'
                """,
                (now, run_id),
            )
            updated = connection.execute(
                """
                UPDATE runs
                SET status = 'failed', error_code = ?, updated_at = ?, completed_at = ?
                WHERE id = ? AND status = 'running'
                """,
                (error_code, now, now, run_id),
            )
            if updated.rowcount != 1:
                raise InvalidRunStateError("run is not active")
        return self.read_run(run_id)

    def cancel_run(self, run_id: str) -> dict[str, object]:
        now = _now_ms()
        with self.lock, self._connection() as connection:
            row = connection.execute(
                "SELECT status FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise ResourceNotFoundError("run not found")
            if row["status"] == "canceled":
                return self.read_run(run_id)
            if row["status"] not in {"running", "waiting_approval"}:
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
        return self.read_run(run_id)

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
        return self.read_run(run_id)

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
                WHERE session_id = ? AND status = 'completed'
                ORDER BY creation_seq DESC LIMIT ?
                """,
                (session_id, MAX_CONTEXT_ITEMS),
            ).fetchall()
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
            elif item["kind"] == "tool_call":
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
            if groups and context_bytes + group_bytes > MAX_CONTEXT_BYTES:
                break
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


def _ensure_session_identity_columns(connection: sqlite3.Connection) -> None:
    columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(sessions)").fetchall()
    }
    for name in ("workspace_dev", "workspace_inode", "workspace_uid"):
        if name not in columns:
            connection.execute(f"ALTER TABLE sessions ADD COLUMN {name} INTEGER")


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
    return {
        "id": row["id"],
        "workspaceRoot": row["workspace_root"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _run_from_row(row: sqlite3.Row) -> dict[str, object]:
    run: dict[str, object] = {
        "id": row["id"],
        "sessionId": row["session_id"],
        "userInput": row["user_input"],
        "status": row["status"],
        "modelStepCount": row["model_step_count"],
        "createdAt": row["created_at"],
        "startedAt": row["started_at"],
        "updatedAt": row["updated_at"],
    }
    if row["completed_at"] is not None:
        run["completedAt"] = row["completed_at"]
    if row["error_code"] is not None:
        run["errorCode"] = row["error_code"]
    return run


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
        item["toolCall"] = tool_call
    return item


def _now_ms() -> int:
    return time.time_ns() // 1_000_000


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

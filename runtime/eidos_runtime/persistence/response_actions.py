from __future__ import annotations

import sqlite3
from threading import RLock
from typing import Literal, Protocol

from eidos_runtime.db.database import now_ms
from eidos_runtime.db.errors import InvalidRunStateError, ResourceNotFoundError


FeedbackValue = Literal["up", "down"]
RevisionKind = Literal["regenerate", "edit"]
TERMINAL_RUN_STATUSES = frozenset({
    "stopped", "succeeded", "failed", "canceled", "interrupted",
})


class ResponseActionStorePort(Protocol):
    @property
    def connection(self) -> sqlite3.Connection | None: ...

    @property
    def lock(self) -> RLock: ...


class ResponseActionRepository:
    """Persistence boundary for UI feedback and canonical run revisions."""

    def __init__(self, store: ResponseActionStorePort) -> None:
        self._store = store

    def set_feedback(
        self, item_id: str, value: FeedbackValue | None
    ) -> dict[str, object]:
        connection = self._connection()
        with self._store.lock, connection:
            row = connection.execute(
                "SELECT kind, status FROM items WHERE id = ?", (item_id,)
            ).fetchone()
            if row is None:
                raise ResourceNotFoundError("item not found")
            if row["kind"] != "assistant_message" or row["status"] != "completed":
                raise InvalidRunStateError("feedback requires a completed assistant item")
            if value is None:
                connection.execute(
                    "DELETE FROM response_feedback WHERE item_id = ?", (item_id,)
                )
                return {"itemId": item_id, "feedback": None}
            now = now_ms()
            connection.execute(
                """
                INSERT INTO response_feedback (item_id, value, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(item_id) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (item_id, value, now, now),
            )
        return {"itemId": item_id, "feedback": value}

    def state_for_session(self, session_id: str) -> dict[str, object]:
        with self._store.lock:
            connection = self._connection()
            session = connection.execute(
                "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if session is None:
                raise ResourceNotFoundError("session not found")
            feedback_rows = connection.execute(
                """
                SELECT response_feedback.item_id, response_feedback.value
                FROM response_feedback
                JOIN items ON items.id = response_feedback.item_id
                WHERE items.session_id = ?
                ORDER BY items.creation_seq ASC
                """,
                (session_id,),
            ).fetchall()
            revision_rows = connection.execute(
                """
                SELECT run_revisions.run_id, run_revisions.source_run_id,
                       run_revisions.revision_kind
                FROM run_revisions
                JOIN runs ON runs.id = run_revisions.run_id
                WHERE runs.session_id = ?
                ORDER BY runs.creation_seq ASC
                """,
                (session_id,),
            ).fetchall()
        return {
            "feedback": [
                {"itemId": str(row["item_id"]), "value": str(row["value"])}
                for row in feedback_rows
            ],
            "revisions": [
                {
                    "runId": str(row["run_id"]),
                    "sourceRunId": str(row["source_run_id"]),
                    "kind": str(row["revision_kind"]),
                }
                for row in revision_rows
            ],
        }

    def validate_revision_source(self, source_run_id: str) -> dict[str, object]:
        with self._store.lock:
            connection = self._connection()
            source = connection.execute(
                """
                SELECT id, session_id, user_input, model_id, status, creation_seq
                FROM runs WHERE id = ?
                """,
                (source_run_id,),
            ).fetchone()
            if source is None:
                raise ResourceNotFoundError("source run not found")
            if str(source["status"]) not in TERMINAL_RUN_STATUSES:
                raise InvalidRunStateError("source run is not terminal")
            if connection.execute(
                "SELECT 1 FROM run_revisions WHERE source_run_id = ?",
                (source_run_id,),
            ).fetchone() is not None:
                raise InvalidRunStateError("source run has already been revised")
            latest = connection.execute(
                """
                SELECT runs.id FROM runs
                WHERE runs.session_id = ?
                  AND NOT EXISTS (
                    SELECT 1 FROM run_revisions
                    WHERE run_revisions.source_run_id = runs.id
                  )
                ORDER BY runs.creation_seq DESC LIMIT 1
                """,
                (source["session_id"],),
            ).fetchone()
            if latest is None or str(latest["id"]) != source_run_id:
                raise InvalidRunStateError("only the latest visible run can be revised")
        return {
            "id": str(source["id"]),
            "sessionId": str(source["session_id"]),
            "userInput": str(source["user_input"]),
            "modelId": str(source["model_id"]),
            "status": str(source["status"]),
        }

    def record_revision(
        self,
        *,
        run_id: str,
        source_run_id: str,
        revision_kind: RevisionKind,
    ) -> None:
        connection = self._connection()
        with self._store.lock, connection:
            replacement = connection.execute(
                "SELECT id, session_id FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
            source = connection.execute(
                "SELECT id, session_id FROM runs WHERE id = ?", (source_run_id,)
            ).fetchone()
            if replacement is None or source is None:
                raise ResourceNotFoundError("revision run not found")
            if replacement["session_id"] != source["session_id"]:
                raise InvalidRunStateError("revision runs must share a session")
            already_revised = connection.execute(
                "SELECT 1 FROM run_revisions WHERE source_run_id = ?",
                (source_run_id,),
            ).fetchone()
            if already_revised is not None:
                raise InvalidRunStateError("source run has already been revised")
            latest_before_replacement = connection.execute(
                """
                SELECT runs.id FROM runs
                WHERE runs.session_id = ?
                  AND runs.id <> ?
                  AND NOT EXISTS (
                    SELECT 1 FROM run_revisions
                    WHERE run_revisions.source_run_id = runs.id
                  )
                ORDER BY runs.creation_seq DESC LIMIT 1
                """,
                (source["session_id"], run_id),
            ).fetchone()
            if (
                latest_before_replacement is None
                or str(latest_before_replacement["id"]) != source_run_id
            ):
                raise InvalidRunStateError("source run is no longer the latest visible run")
            connection.execute(
                """
                INSERT INTO run_revisions (
                    run_id, source_run_id, revision_kind, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (run_id, source_run_id, revision_kind, now_ms()),
            )

    def _connection(self) -> sqlite3.Connection:
        connection = self._store.connection
        if connection is None:
            raise RuntimeError("storage is not initialized")
        return connection


__all__ = [
    "FeedbackValue",
    "ResponseActionRepository",
    "ResponseActionStorePort",
    "RevisionKind",
]

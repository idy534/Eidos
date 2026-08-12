from __future__ import annotations

import sqlite3

from eidos_runtime.db.database import Database, now_ms
from eidos_runtime.db.errors import InvalidRunStateError, ResourceNotFoundError
from eidos_runtime.domain.review import ReviewComment


class ReviewCommentRepository:
    """SQLite authority for durable inline review comments."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def create(
        self,
        *,
        comment_id: str,
        session_id: str,
        path: str,
        scope: str,
        side: str,
        line: int,
        body: str,
        base_head: str,
        diff_hash: str,
        operation_id: str,
        operation_request: dict[str, object],
    ) -> ReviewComment:
        def action(connection: sqlite3.Connection) -> dict[str, object]:
            if connection.execute(
                "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
            ).fetchone() is None:
                raise ResourceNotFoundError("session not found")
            if connection.execute(
                "SELECT 1 FROM review_comments WHERE id = ?", (comment_id,)
            ).fetchone() is not None:
                raise InvalidRunStateError("review comment id was reused")
            now = now_ms()
            connection.execute(
                """
                INSERT INTO review_comments (
                    id, session_id, path, scope, side, line, body,
                    base_head, diff_hash, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)
                """,
                (
                    comment_id, session_id, path, scope, side, line, body,
                    base_head, diff_hash, now, now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM review_comments WHERE id = ?", (comment_id,)
            ).fetchone()
            assert row is not None
            return _comment_from_row(row).model_dump(mode="json")

        result = self._database.execute_idempotent(
            action,
            operation_id=operation_id,
            operation_scope="review/createComment",
            operation_request=operation_request,
        )
        return ReviewComment.model_validate(result)

    def create_result(
        self,
        operation_id: str,
        operation_request: dict[str, object],
    ) -> ReviewComment | None:
        result = self._database.operation_result(
            operation_id, "review/createComment", operation_request
        )
        return None if result is None else ReviewComment.model_validate(result)

    def list_for_session(self, session_id: str) -> tuple[ReviewComment, ...]:
        with self._database.lock:
            connection = self._database.connection()
            if connection.execute(
                "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
            ).fetchone() is None:
                raise ResourceNotFoundError("session not found")
            rows = connection.execute(
                """
                SELECT * FROM review_comments
                WHERE session_id = ?
                ORDER BY created_at ASC, id ASC
                """,
                (session_id,),
            ).fetchall()
        return tuple(_comment_from_row(row) for row in rows)

    def refresh_anchor_status(
        self,
        *,
        session_id: str,
        path: str,
        scope: str,
        base_head: str,
        diff_hash: str,
    ) -> None:
        now = now_ms()
        with self._database.transaction() as connection:
            connection.execute(
                """
                UPDATE review_comments
                SET status = CASE
                        WHEN base_head = ? AND diff_hash = ? THEN 'active'
                        ELSE 'stale'
                    END,
                    updated_at = CASE
                        WHEN status <> CASE
                            WHEN base_head = ? AND diff_hash = ? THEN 'active'
                            ELSE 'stale'
                        END THEN ?
                        ELSE updated_at
                    END
                WHERE session_id = ? AND path = ? AND scope = ?
                """,
                (
                    base_head, diff_hash, base_head, diff_hash, now,
                    session_id, path, scope,
                ),
            )

    def delete(
        self,
        *,
        session_id: str,
        comment_id: str,
        operation_id: str,
        operation_request: dict[str, object],
    ) -> str:
        def action(connection: sqlite3.Connection) -> dict[str, object]:
            row = connection.execute(
                "SELECT session_id FROM review_comments WHERE id = ?", (comment_id,)
            ).fetchone()
            if row is None or str(row["session_id"]) != session_id:
                raise ResourceNotFoundError("review comment not found")
            connection.execute("DELETE FROM review_comments WHERE id = ?", (comment_id,))
            return {"commentId": comment_id}

        result = self._database.execute_idempotent(
            action,
            operation_id=operation_id,
            operation_scope="review/deleteComment",
            operation_request=operation_request,
        )
        if not isinstance(result, dict) or result.get("commentId") != comment_id:
            raise RuntimeError("review delete replay is invalid")
        return comment_id


def _comment_from_row(row: sqlite3.Row) -> ReviewComment:
    return ReviewComment(
        id=str(row["id"]),
        session_id=str(row["session_id"]),
        path=str(row["path"]),
        scope=str(row["scope"]),
        side=str(row["side"]),
        line=int(row["line"]),
        body=str(row["body"]),
        base_head=str(row["base_head"]),
        diff_hash=str(row["diff_hash"]),
        status=str(row["status"]),
        created_at=int(row["created_at"]),
        updated_at=int(row["updated_at"]),
    )


__all__ = ["ReviewCommentRepository"]

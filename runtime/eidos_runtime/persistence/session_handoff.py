from __future__ import annotations

from datetime import UTC, datetime
import sqlite3

from eidos_runtime.db.database import Repository
from eidos_runtime.db.errors import ResourceNotFoundError, StorageError
from eidos_runtime.domain.handoff import (
    SessionHandoffOperation,
    SessionHandoffScope,
    SessionHandoffState,
)
from eidos_runtime.domain.session import SessionExecutionMode


class SessionHandoffRepository(Repository):
    """Durable typed facts for one Local/Worktree handoff."""

    def read(
        self, scope: SessionHandoffScope | str, operation_id: str
    ) -> SessionHandoffOperation | None:
        with self.lock:
            row = self._connection().execute(
                "SELECT * FROM session_handoff_operations "
                "WHERE scope = ? AND operation_id = ?",
                (_scope(scope), operation_id),
            ).fetchone()
        return _map(row)

    def prepare(
        self, operation: SessionHandoffOperation
    ) -> SessionHandoffOperation:
        existing = self.read(operation.scope, operation.operation_id)
        if existing is not None:
            if not _same_plan(existing, operation):
                raise StorageError("session_handoff_operation_conflict")
            return existing
        with self.lock, self._connection() as connection:
            try:
                placeholders = ", ".join("?" for _ in _values(operation))
                connection.execute(
                    f"""
                    INSERT INTO session_handoff_operations (
                        scope, operation_id, state, session_id, project_id,
                        source_mode, target_mode, source_root, target_root,
                        source_common_dir, target_common_dir,
                        associated_worktree_id, target_worktree_new,
                        target_base_ref, target_base_commit,
                        source_head, source_branch, source_dirty, source_fingerprint,
                        target_head, target_branch, target_dirty, target_fingerprint,
                        target_after_head, target_after_branch,
                        target_after_fingerprint, source_after_head,
                        source_after_branch, source_after_fingerprint, error_code,
                        created_at, updated_at
                    ) VALUES ({placeholders})
                    """,
                    _values(operation),
                )
            except sqlite3.IntegrityError as error:
                raise StorageError("session_handoff_operation_conflict") from error
        return operation

    def update_state(
        self,
        scope: SessionHandoffScope | str,
        operation_id: str,
        state: SessionHandoffState,
        *,
        error_code: str | None = None,
        target_after_head: str | None = None,
        target_after_branch: str | None = None,
        target_after_fingerprint: str | None = None,
        source_after_head: str | None = None,
        source_after_branch: str | None = None,
        source_after_fingerprint: str | None = None,
    ) -> SessionHandoffOperation:
        current = self.read(scope, operation_id)
        if current is None:
            raise ResourceNotFoundError("session handoff operation not found")
        if state.value not in _ALLOWED_TRANSITIONS.get(
            current.state.value, frozenset()
        ):
            raise StorageError("session_handoff_transition_invalid")
        now = _now_ms()
        target_after_head = (
            current.target_after_head
            if target_after_head is None
            else target_after_head
        )
        target_after_branch = (
            current.target_after_branch
            if target_after_branch is None
            else target_after_branch
        )
        target_after_fingerprint = (
            current.target_after_fingerprint
            if target_after_fingerprint is None
            else target_after_fingerprint
        )
        source_after_head = (
            current.source_after_head
            if source_after_head is None
            else source_after_head
        )
        source_after_branch = (
            current.source_after_branch
            if source_after_branch is None
            else source_after_branch
        )
        source_after_fingerprint = (
            current.source_after_fingerprint
            if source_after_fingerprint is None
            else source_after_fingerprint
        )
        with self.lock, self._connection() as connection:
            updated = connection.execute(
                """
                UPDATE session_handoff_operations
                SET state = ?, error_code = ?, target_after_head = ?,
                    target_after_branch = ?, target_after_fingerprint = ?,
                    source_after_head = ?, source_after_branch = ?,
                    source_after_fingerprint = ?, updated_at = ?
                WHERE scope = ? AND operation_id = ?
                """,
                (
                    state.value,
                    error_code,
                    target_after_head,
                    target_after_branch,
                    target_after_fingerprint,
                    source_after_head,
                    source_after_branch,
                    source_after_fingerprint,
                    now,
                    _scope(scope),
                    operation_id,
                ),
            )
            if updated.rowcount != 1:
                raise ResourceNotFoundError("session handoff operation not found")
        result = self.read(scope, operation_id)
        if result is None:
            raise ResourceNotFoundError("session handoff operation not found")
        return result

    def list_unfinished(self) -> tuple[SessionHandoffOperation, ...]:
        with self.lock:
            rows = self._connection().execute(
                "SELECT * FROM session_handoff_operations "
                "WHERE state NOT IN ('completed', 'cleanup_required') "
                "ORDER BY created_at ASC, scope ASC, operation_id ASC"
            ).fetchall()
        return tuple(value for row in rows if (value := _map(row)) is not None)

    def latest_for_session(
        self, session_id: str
    ) -> SessionHandoffOperation | None:
        with self.lock:
            row = self._connection().execute(
                "SELECT * FROM session_handoff_operations WHERE session_id = ? "
                "AND state = 'completed' ORDER BY created_at DESC LIMIT 1",
                (session_id,),
            ).fetchone()
        return _map(row)


_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "prepared": frozenset({"prepared", "source_captured", "cleanup_required"}),
    "source_captured": frozenset({"source_captured", "target_materialized", "cleanup_required"}),
    "target_materialized": frozenset({"target_materialized", "session_rebound", "cleanup_required"}),
    "session_rebound": frozenset({"session_rebound", "completed", "cleanup_required"}),
    "completed": frozenset({"completed"}),
    "cleanup_required": frozenset({"cleanup_required"}),
}


def _scope(value: SessionHandoffScope | str) -> str:
    return value.value if isinstance(value, SessionHandoffScope) else value


def _same_plan(
    existing: SessionHandoffOperation,
    candidate: SessionHandoffOperation,
) -> bool:
    return existing.plan == candidate.plan and existing.scope == candidate.scope


def _values(operation: SessionHandoffOperation) -> tuple[object, ...]:
    return (
        operation.scope.value,
        operation.operation_id,
        operation.state.value,
        operation.session_id,
        operation.project_id,
        operation.source_mode.value,
        operation.target_mode.value,
        operation.source_root,
        operation.target_root,
        operation.source_common_dir,
        operation.target_common_dir,
        operation.associated_worktree_id,
        int(operation.target_worktree_new),
        operation.target_base_ref,
        operation.target_base_commit,
        operation.source_head,
        operation.source_branch,
        int(operation.source_dirty),
        operation.source_fingerprint,
        operation.target_head,
        operation.target_branch,
        int(operation.target_dirty),
        operation.target_fingerprint,
        operation.target_after_head,
        operation.target_after_branch,
        operation.target_after_fingerprint,
        operation.source_after_head,
        operation.source_after_branch,
        operation.source_after_fingerprint,
        operation.error_code,
        _millis(operation.created_at),
        _millis(operation.updated_at),
    )


def _map(row: sqlite3.Row | None) -> SessionHandoffOperation | None:
    if row is None:
        return None
    return SessionHandoffOperation.model_validate({
        "scope": SessionHandoffScope(row["scope"]),
        "operation_id": row["operation_id"],
        "state": SessionHandoffState(row["state"]),
        "session_id": row["session_id"],
        "project_id": row["project_id"],
        "source_mode": SessionExecutionMode(row["source_mode"]),
        "target_mode": SessionExecutionMode(row["target_mode"]),
        "source_root": row["source_root"],
        "target_root": row["target_root"],
        "source_common_dir": row["source_common_dir"],
        "target_common_dir": row["target_common_dir"],
        "associated_worktree_id": row["associated_worktree_id"],
        "target_worktree_new": bool(row["target_worktree_new"]),
        "target_base_ref": row["target_base_ref"],
        "target_base_commit": row["target_base_commit"],
        "source_head": row["source_head"],
        "source_branch": row["source_branch"],
        "source_dirty": bool(row["source_dirty"]),
        "source_fingerprint": row["source_fingerprint"],
        "target_head": row["target_head"],
        "target_branch": row["target_branch"],
        "target_dirty": bool(row["target_dirty"]),
        "target_fingerprint": row["target_fingerprint"],
        "target_after_head": row["target_after_head"],
        "target_after_branch": row["target_after_branch"],
        "target_after_fingerprint": row["target_after_fingerprint"],
        "source_after_head": row["source_after_head"],
        "source_after_branch": row["source_after_branch"],
        "source_after_fingerprint": row["source_after_fingerprint"],
        "error_code": row["error_code"],
        "created_at": _timestamp(int(row["created_at"])),
        "updated_at": _timestamp(int(row["updated_at"])),
    })


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def _timestamp(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1000, tz=UTC)


def _millis(value: datetime) -> int:
    return int(value.timestamp() * 1000)


__all__ = ["SessionHandoffRepository"]

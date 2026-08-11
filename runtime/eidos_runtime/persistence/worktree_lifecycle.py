from __future__ import annotations

from datetime import UTC, datetime
import sqlite3

from eidos_runtime.db.database import Repository
from eidos_runtime.db.errors import ResourceNotFoundError, StorageError
from eidos_runtime.domain.worktree import (
    WorktreeLifecycleOperation,
    WorktreeLifecycleScope,
    WorktreeLifecycleState,
)


class WorktreeLifecycleRepository(Repository):
    """Durable facts for the managed Worktree lifecycle mutations.

    This repository stores a fixed set of lifecycle fields.  It is not a
    generic workflow or arbitrary payload executor.
    """

    def read(
        self,
        scope: WorktreeLifecycleScope | str,
        operation_id: str,
    ) -> WorktreeLifecycleOperation | None:
        with self.lock:
            row = self._connection().execute(
                """
                SELECT * FROM worktree_lifecycle_operations
                WHERE scope = ? AND operation_id = ?
                """,
                (_scope(scope), operation_id),
            ).fetchone()
        return _map(row)

    def prepare(
        self,
        operation: WorktreeLifecycleOperation,
    ) -> WorktreeLifecycleOperation:
        existing = self.read(operation.scope, operation.operation_id)
        if existing is not None:
            if not _same_plan(existing, operation):
                raise StorageError("worktree_lifecycle_conflict")
            return existing
        created_at = _millis(operation.created_at)
        updated_at = _millis(operation.updated_at)
        with self.lock, self._connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO worktree_lifecycle_operations (
                        scope, operation_id, state, project_id,
                        repository_root, worktree_id, worktree_root,
                        base_ref, branch, base_commit, session_id, run_id,
                        checkpoint_id, include_local_changes, source_head,
                        source_branch, source_dirty, source_fingerprint,
                        error_code, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        operation.scope.value,
                        operation.operation_id,
                        operation.state.value,
                        operation.project_id,
                        operation.repository_root,
                        operation.worktree_id,
                        operation.worktree_root,
                        operation.base_ref,
                        operation.branch,
                        operation.base_commit,
                        operation.session_id,
                        operation.run_id,
                        operation.checkpoint_id,
                        int(operation.include_local_changes),
                        operation.source_head,
                        operation.source_branch,
                        (
                            int(operation.source_dirty)
                            if operation.source_dirty is not None
                            else None
                        ),
                        operation.source_fingerprint,
                        operation.error_code,
                        created_at,
                        updated_at,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise StorageError("worktree_lifecycle_conflict") from error
        return operation

    def update_state(
        self,
        scope: WorktreeLifecycleScope | str,
        operation_id: str,
        state: WorktreeLifecycleState,
        *,
        error_code: str | None = None,
    ) -> WorktreeLifecycleOperation:
        now = _now_ms()
        with self.lock, self._connection() as connection:
            current = connection.execute(
                """
                SELECT state FROM worktree_lifecycle_operations
                WHERE scope = ? AND operation_id = ?
                """,
                (_scope(scope), operation_id),
            ).fetchone()
            if current is None:
                raise ResourceNotFoundError(
                    "worktree lifecycle operation not found"
                )
            if state.value not in _ALLOWED_TRANSITIONS.get(
                str(current["state"]), frozenset()
            ):
                raise StorageError("worktree_lifecycle_transition_invalid")
            updated = connection.execute(
                """
                UPDATE worktree_lifecycle_operations
                SET state = ?, error_code = ?, updated_at = ?
                WHERE scope = ? AND operation_id = ?
                """,
                (
                    state.value,
                    error_code,
                    now,
                    _scope(scope),
                    operation_id,
                ),
            )
            if updated.rowcount != 1:
                raise ResourceNotFoundError("worktree lifecycle operation not found")
        operation = self.read(scope, operation_id)
        if operation is None:
            raise ResourceNotFoundError("worktree lifecycle operation not found")
        return operation

    def list_unfinished(self) -> tuple[WorktreeLifecycleOperation, ...]:
        with self.lock:
            rows = self._connection().execute(
                """
                SELECT * FROM worktree_lifecycle_operations
                WHERE state NOT IN ('completed', 'cleanup_required')
                ORDER BY created_at ASC, scope ASC, operation_id ASC
                """
            ).fetchall()
        return tuple(value for row in rows if (value := _map(row)) is not None)

    def find_delete_for_session(
        self, session_id: str
    ) -> WorktreeLifecycleOperation | None:
        with self.lock:
            row = self._connection().execute(
                """
                SELECT * FROM worktree_lifecycle_operations
                WHERE scope = 'session/delete'
                  AND session_id = ?
                  AND state <> 'completed'
                ORDER BY created_at DESC, operation_id DESC
                LIMIT 1
                """,
                (session_id,),
            ).fetchone()
        return _map(row)


def _scope(value: WorktreeLifecycleScope | str) -> str:
    return value.value if isinstance(value, WorktreeLifecycleScope) else value


_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "prepared": frozenset({
        "worktree_created",
        "branch_attached",
        "worktree_deleted",
        "cleanup_required",
    }),
    "worktree_created": frozenset({"session_created", "worktree_deleted", "cleanup_required"}),
    "session_created": frozenset({"run_created", "completed", "cleanup_required"}),
    "run_created": frozenset({"checkpoint_action_created", "completed", "cleanup_required"}),
    "checkpoint_action_created": frozenset({"completed", "cleanup_required"}),
    "branch_attached": frozenset({"completed", "cleanup_required"}),
    "worktree_deleted": frozenset({
        "worktree_deleted",
        "completed",
        "cleanup_required",
    }),
    "completed": frozenset({"completed"}),
    "cleanup_required": frozenset({"cleanup_required"}),
}


def _same_plan(
    existing: WorktreeLifecycleOperation,
    candidate: WorktreeLifecycleOperation,
) -> bool:
    return all(
        getattr(existing, field) == getattr(candidate, field)
        for field in (
            "scope",
            "operation_id",
            "project_id",
            "repository_root",
            "worktree_id",
            "worktree_root",
            "base_ref",
            "branch",
            "base_commit",
            "session_id",
            "run_id",
            "checkpoint_id",
            "include_local_changes",
            "source_head",
            "source_branch",
            "source_dirty",
            "source_fingerprint",
        )
    )


def _map(row: sqlite3.Row | None) -> WorktreeLifecycleOperation | None:
    if row is None:
        return None
    return WorktreeLifecycleOperation.model_validate({
        "scope": WorktreeLifecycleScope(row["scope"]),
        "operation_id": row["operation_id"],
        "state": WorktreeLifecycleState(row["state"]),
        "project_id": row["project_id"],
        "repository_root": row["repository_root"],
        "worktree_id": row["worktree_id"],
        "worktree_root": row["worktree_root"],
        "base_ref": row["base_ref"],
        "branch": row["branch"],
        "base_commit": row["base_commit"],
        "session_id": row["session_id"],
        "run_id": row["run_id"],
        "checkpoint_id": row["checkpoint_id"],
        "include_local_changes": bool(row["include_local_changes"]),
        "source_head": row["source_head"],
        "source_branch": row["source_branch"],
        "source_dirty": (
            bool(row["source_dirty"])
            if row["source_dirty"] is not None
            else None
        ),
        "source_fingerprint": row["source_fingerprint"],
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


__all__ = ["WorktreeLifecycleRepository"]

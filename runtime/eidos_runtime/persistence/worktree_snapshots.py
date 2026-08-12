from __future__ import annotations

from datetime import UTC, datetime
import json
import sqlite3

from eidos_runtime.db.database import Repository
from eidos_runtime.db.errors import ResourceNotFoundError, StorageError
from eidos_runtime.domain.worktree_snapshot import (
    WorktreeSnapshot,
    WorktreeSnapshotState,
)
from eidos_runtime.domain.worktree import BranchOwnership


class WorktreeSnapshotRepository(Repository):
    """SQLite metadata authority for snapshot artifacts."""

    def insert(self, snapshot: WorktreeSnapshot) -> WorktreeSnapshot:
        with self.lock, self._connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO worktree_snapshots (
                        id, worktree_id, session_id, project_id, base_ref,
                        base_commit, head, branch, checkout_branch,
                        branch_ownership, dirty, staged_paths_json,
                        unstaged_paths_json, untracked_paths_json,
                        conflict_paths_json, source_fingerprint, artifact_path,
                        artifact_sha256, full_patch_sha256, staged_patch_sha256,
                        format_version, state, created_at, restored_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                              ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    _values(snapshot),
                )
            except sqlite3.IntegrityError as error:
                raise StorageError("snapshot_persistence_conflict") from error
        return snapshot

    def read(self, snapshot_id: str) -> WorktreeSnapshot | None:
        with self.lock:
            row = self._connection().execute(
                "SELECT * FROM worktree_snapshots WHERE id = ?", (snapshot_id,)
            ).fetchone()
        return _map(row)

    def latest_ready(self, worktree_id: str) -> WorktreeSnapshot | None:
        with self.lock:
            row = self._connection().execute(
                """
                SELECT * FROM worktree_snapshots
                WHERE worktree_id = ? AND state = 'ready'
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """,
                (worktree_id,),
            ).fetchone()
        return _map(row)

    def latest_ready_snapshot(self, worktree_id: str) -> WorktreeSnapshot | None:
        return self.latest_ready(worktree_id)

    def list_for_worktree(self, worktree_id: str) -> tuple[WorktreeSnapshot, ...]:
        with self.lock:
            rows = self._connection().execute(
                """
                SELECT * FROM worktree_snapshots
                WHERE worktree_id = ?
                ORDER BY created_at DESC, id DESC
                """,
                (worktree_id,),
            ).fetchall()
        return tuple(value for row in rows if (value := _map(row)) is not None)

    def list_ready(self) -> tuple[WorktreeSnapshot, ...]:
        with self.lock:
            rows = self._connection().execute(
                "SELECT * FROM worktree_snapshots WHERE state = 'ready' "
                "ORDER BY created_at ASC, id ASC"
            ).fetchall()
        return tuple(value for row in rows if (value := _map(row)) is not None)

    def list_all(self) -> tuple[WorktreeSnapshot, ...]:
        with self.lock:
            rows = self._connection().execute(
                "SELECT * FROM worktree_snapshots ORDER BY created_at ASC, id ASC"
            ).fetchall()
        return tuple(value for row in rows if (value := _map(row)) is not None)

    def mark_restored(self, snapshot_id: str) -> WorktreeSnapshot:
        now = _now_ms()
        with self.lock, self._connection() as connection:
            updated = connection.execute(
                """
                UPDATE worktree_snapshots
                SET state = 'restored', restored_at = ?, updated_at = ?
                WHERE id = ? AND state = 'ready'
                """,
                (now, now, snapshot_id),
            )
            if updated.rowcount != 1:
                existing = connection.execute(
                    "SELECT * FROM worktree_snapshots WHERE id = ?",
                    (snapshot_id,),
                ).fetchone()
                if existing is None:
                    raise ResourceNotFoundError("snapshot not found")
        snapshot = self.read(snapshot_id)
        if snapshot is None:
            raise ResourceNotFoundError("snapshot not found")
        return snapshot

    def mark_invalid(self, snapshot_id: str) -> WorktreeSnapshot:
        now = _now_ms()
        with self.lock, self._connection() as connection:
            updated = connection.execute(
                "UPDATE worktree_snapshots SET state = 'invalid', updated_at = ? "
                "WHERE id = ? AND state = 'ready'",
                (now, snapshot_id),
            )
            if updated.rowcount != 1:
                row = connection.execute(
                    "SELECT * FROM worktree_snapshots WHERE id = ?", (snapshot_id,)
                ).fetchone()
                if row is None:
                    raise ResourceNotFoundError("snapshot not found")
        snapshot = self.read(snapshot_id)
        if snapshot is None:
            raise ResourceNotFoundError("snapshot not found")
        return snapshot

    def delete(self, snapshot_id: str) -> None:
        with self.lock, self._connection() as connection:
            deleted = connection.execute(
                "DELETE FROM worktree_snapshots WHERE id = ?", (snapshot_id,)
            )
            if deleted.rowcount != 1:
                raise ResourceNotFoundError("snapshot not found")


def _values(snapshot: WorktreeSnapshot) -> tuple[object, ...]:
    return (
        snapshot.id,
        snapshot.worktree_id,
        snapshot.session_id,
        snapshot.project_id,
        snapshot.base_ref,
        snapshot.base_commit,
        snapshot.head,
        snapshot.branch,
        snapshot.checkout_branch,
        snapshot.branch_ownership.value,
        int(snapshot.dirty),
        json.dumps(list(snapshot.staged_paths), separators=(",", ":")),
        json.dumps(list(snapshot.unstaged_paths), separators=(",", ":")),
        json.dumps(list(snapshot.untracked_paths), separators=(",", ":")),
        json.dumps(list(snapshot.conflict_paths), separators=(",", ":")),
        snapshot.source_fingerprint,
        snapshot.artifact_path,
        snapshot.artifact_sha256,
        snapshot.full_patch_sha256,
        snapshot.staged_patch_sha256,
        snapshot.format_version,
        snapshot.state.value,
        _millis(snapshot.created_at),
        _millis(snapshot.restored_at) if snapshot.restored_at is not None else None,
        _millis(snapshot.updated_at),
    )


def _map(row: sqlite3.Row | None) -> WorktreeSnapshot | None:
    if row is None:
        return None
    return WorktreeSnapshot.model_validate(
        {
            "id": row["id"],
            "worktree_id": row["worktree_id"],
            "session_id": row["session_id"],
            "project_id": row["project_id"],
            "base_ref": row["base_ref"],
            "base_commit": row["base_commit"],
            "head": row["head"],
            "branch": row["branch"],
            "checkout_branch": row["checkout_branch"],
            "branch_ownership": BranchOwnership(row["branch_ownership"]),
            "dirty": bool(row["dirty"]),
            "staged_paths": tuple(json.loads(row["staged_paths_json"])),
            "unstaged_paths": tuple(json.loads(row["unstaged_paths_json"])),
            "untracked_paths": tuple(json.loads(row["untracked_paths_json"])),
            "conflict_paths": tuple(json.loads(row["conflict_paths_json"])),
            "source_fingerprint": row["source_fingerprint"],
            "artifact_path": row["artifact_path"],
            "artifact_sha256": row["artifact_sha256"],
            "full_patch_sha256": row["full_patch_sha256"],
            "staged_patch_sha256": row["staged_patch_sha256"],
            "format_version": int(row["format_version"]),
            "state": WorktreeSnapshotState(row["state"]),
            "created_at": _timestamp(int(row["created_at"])),
            "restored_at": (
                _timestamp(int(row["restored_at"]))
                if row["restored_at"] is not None
                else None
            ),
            "updated_at": _timestamp(int(row["updated_at"])),
        }
    )


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def _timestamp(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1000, tz=UTC)


def _millis(value: datetime) -> int:
    return int(value.timestamp() * 1000)


__all__ = ["WorktreeSnapshotRepository"]

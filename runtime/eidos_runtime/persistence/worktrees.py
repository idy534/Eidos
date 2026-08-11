from __future__ import annotations

from datetime import UTC, datetime
import sqlite3
from typing import TYPE_CHECKING

from eidos_runtime.db.database import Database, Repository
from eidos_runtime.db.errors import ResourceNotFoundError, StorageError
from eidos_runtime.domain.project import Project
from eidos_runtime.domain.worktree import Worktree, WorktreeState
from eidos_runtime.persistence.mappers.worktree import (
    project_from_row,
    worktree_from_row,
)

if TYPE_CHECKING:
    from eidos_runtime.git.models import GitRepositoryDiscovery


class ProjectWorktreeRepository(Repository):
    """Typed SQLite persistence for Project and Worktree lifecycle facts."""

    def __init__(self, database: Database) -> None:
        super().__init__(database)

    def get_or_create_project(self, discovery: GitRepositoryDiscovery) -> Project:
        project_id = _project_id(discovery.git_common_dir)
        now = _now_ms()
        candidate = Project(
            id=project_id,
            repository_root=discovery.repository_root,
            git_common_dir=discovery.git_common_dir,
            created_at=_timestamp(now),
            updated_at=_timestamp(now),
        )
        with self.lock, self._connection() as connection:
            existing = connection.execute(
                "SELECT * FROM projects WHERE git_common_dir = ?",
                (candidate.git_common_dir,),
            ).fetchone()
            if existing is not None:
                project = project_from_row(existing)
                if project.id != candidate.id:
                    raise StorageError("project_repository_mismatch")
                return project
            try:
                connection.execute(
                    """
                    INSERT INTO projects (
                        id, repository_root, git_common_dir, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        candidate.id,
                        candidate.repository_root,
                        candidate.git_common_dir,
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise StorageError("project_persistence_conflict") from error
        return candidate

    def read_project(self, project_id: str) -> Project | None:
        with self.lock:
            row = self._connection().execute(
                "SELECT * FROM projects WHERE id = ?", (project_id,)
            ).fetchone()
        return project_from_row(row) if row is not None else None

    def list_projects(self) -> tuple[Project, ...]:
        with self.lock:
            rows = self._connection().execute(
                "SELECT * FROM projects ORDER BY created_at ASC, id ASC"
            ).fetchall()
        return tuple(project_from_row(row) for row in rows)

    def insert_worktree(self, worktree: Worktree) -> Worktree:
        with self.lock, self._connection() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO worktrees (
                        id, project_id, worktree_root, git_dir, base_ref,
                        base_commit, branch, ownership, state, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        worktree.id,
                        worktree.project_id,
                        worktree.worktree_root,
                        worktree.git_dir,
                        worktree.base_ref,
                        worktree.base_commit,
                        worktree.branch,
                        worktree.ownership.value,
                        worktree.state.value,
                        _millis(worktree.created_at),
                        _millis(worktree.updated_at),
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise StorageError("worktree_persistence_conflict") from error
        return worktree

    def read_worktree(self, worktree_id: str) -> Worktree | None:
        with self.lock:
            row = self._connection().execute(
                "SELECT * FROM worktrees WHERE id = ?", (worktree_id,)
            ).fetchone()
        return worktree_from_row(row) if row is not None else None

    def worktree_is_bound(self, worktree_id: str) -> bool:
        with self.lock:
            row = self._connection().execute(
                "SELECT 1 FROM sessions WHERE worktree_id = ? LIMIT 1",
                (worktree_id,),
            ).fetchone()
        return row is not None

    def list_worktrees(self, project_id: str | None = None) -> tuple[Worktree, ...]:
        sql = "SELECT * FROM worktrees"
        parameters: tuple[object, ...] = ()
        if project_id is not None:
            sql += " WHERE project_id = ?"
            parameters = (project_id,)
        sql += " ORDER BY created_at ASC, id ASC"
        with self.lock:
            rows = self._connection().execute(sql, parameters).fetchall()
        return tuple(worktree_from_row(row) for row in rows)

    def update_state(self, worktree_id: str, state: WorktreeState) -> Worktree:
        now = _now_ms()
        with self.lock, self._connection() as connection:
            updated = connection.execute(
                "UPDATE worktrees SET state = ?, updated_at = ? WHERE id = ?",
                (state.value, now, worktree_id),
            )
            if updated.rowcount != 1:
                raise ResourceNotFoundError("worktree not found")
            row = connection.execute(
                "SELECT * FROM worktrees WHERE id = ?", (worktree_id,)
            ).fetchone()
        assert row is not None
        return worktree_from_row(row)


def _project_id(git_common_dir: str) -> str:
    import hashlib

    return f"project_{hashlib.sha256(git_common_dir.encode('utf-8')).hexdigest()}"


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def _timestamp(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1000, tz=UTC)


def _millis(value: datetime) -> int:
    return int(value.timestamp() * 1000)


__all__ = ["ProjectWorktreeRepository"]

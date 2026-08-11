from __future__ import annotations

from datetime import UTC, datetime
import os
from pathlib import Path
import sqlite3
from typing import TYPE_CHECKING

from eidos_runtime.db.database import Database, Repository
from eidos_runtime.db.errors import ResourceNotFoundError, StorageError
from eidos_runtime.domain.project import Project, direct_project_id
from eidos_runtime.domain.worktree import (
    BranchOwnership,
    Worktree,
    WorktreeState,
)
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

    def get_or_create_project(
        self,
        workspace_root: Path | str,
        git_discovery: GitRepositoryDiscovery | None = None,
    ) -> Project:
        if git_discovery is None:
            canonical_workspace = _canonical_workspace_root(workspace_root)
            project_id = direct_project_id(canonical_workspace)
            git_repository_root = None
            git_common_dir = None
        else:
            canonical_workspace = _canonical_workspace_root(
                git_discovery.repository_root
            )
            git_repository_root = canonical_workspace
            git_common_dir = _canonical_workspace_root(git_discovery.git_common_dir)
            project_id = _project_id(git_common_dir)
        now = _now_ms()
        candidate = Project(
            id=project_id,
            workspace_root=canonical_workspace,
            git_repository_root=git_repository_root,
            git_common_dir=git_common_dir,
            created_at=_timestamp(now),
            updated_at=_timestamp(now),
        )
        with self.lock, self._connection() as connection:
            existing = connection.execute(
                "SELECT * FROM projects WHERE workspace_root = ?",
                (candidate.workspace_root,),
            ).fetchone()
            if existing is None and candidate.git_common_dir is not None:
                existing = connection.execute(
                    "SELECT * FROM projects WHERE git_common_dir = ?",
                    (candidate.git_common_dir,),
                ).fetchone()
            if existing is not None:
                project = project_from_row(existing)
                if (
                    candidate.git_common_dir is not None
                    and project.has_git
                    and project.git_common_dir != candidate.git_common_dir
                ):
                    raise StorageError("project_repository_mismatch")
                if candidate.has_git and not project.has_git:
                    connection.execute(
                        """
                        UPDATE projects
                        SET git_repository_root = ?, git_common_dir = ?,
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            candidate.git_repository_root,
                            candidate.git_common_dir,
                            now,
                            project.id,
                        ),
                    )
                    project = candidate.model_copy(update={"id": project.id})
                return project
            try:
                connection.execute(
                    """
                    INSERT INTO projects (
                        id, workspace_root, git_repository_root, git_common_dir,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        candidate.id,
                        candidate.workspace_root,
                        candidate.git_repository_root,
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
                        base_commit, branch, branch_ownership, ownership, state,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        worktree.id,
                        worktree.project_id,
                        worktree.worktree_root,
                        worktree.git_dir,
                        worktree.base_ref,
                        worktree.base_commit,
                        worktree.branch,
                        worktree.branch_ownership.value,
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

    def update_branch(
        self,
        worktree_id: str,
        branch: str,
        ownership: BranchOwnership = BranchOwnership.USER,
    ) -> Worktree:
        now = _now_ms()
        with self.lock, self._connection() as connection:
            updated = connection.execute(
                """
                UPDATE worktrees
                SET branch = ?, branch_ownership = ?, updated_at = ?
                WHERE id = ? AND branch IS NULL
                """,
                (branch, ownership.value, now, worktree_id),
            )
            if updated.rowcount != 1:
                row = connection.execute(
                    "SELECT * FROM worktrees WHERE id = ?", (worktree_id,)
                ).fetchone()
                if row is None:
                    raise ResourceNotFoundError("worktree not found")
                existing = worktree_from_row(row)
                if (
                    existing.branch == branch
                    and existing.branch_ownership is ownership
                ):
                    return existing
                raise StorageError("worktree_branch_already_attached")
            row = connection.execute(
                "SELECT * FROM worktrees WHERE id = ?", (worktree_id,)
            ).fetchone()
        assert row is not None
        return worktree_from_row(row)


def _project_id(git_common_dir: str) -> str:
    import hashlib

    return f"project_{hashlib.sha256(git_common_dir.encode('utf-8')).hexdigest()}"


def _canonical_workspace_root(value: Path | str) -> str:
    resolved = Path(os.path.realpath(value))
    if not resolved.is_absolute():
        raise StorageError("workspace_root_invalid")
    return str(resolved)


def _now_ms() -> int:
    return int(datetime.now(UTC).timestamp() * 1000)


def _timestamp(value: int) -> datetime:
    return datetime.fromtimestamp(value / 1000, tz=UTC)


def _millis(value: datetime) -> int:
    return int(value.timestamp() * 1000)


__all__ = ["ProjectWorktreeRepository"]

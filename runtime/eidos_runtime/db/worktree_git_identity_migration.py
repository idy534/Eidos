from __future__ import annotations

import sqlite3


WORKTREE_GIT_IDENTITY_SCHEMA_SQL = """
ALTER TABLE worktrees ADD COLUMN git_dir_dev INTEGER;
ALTER TABLE worktrees ADD COLUMN git_dir_inode INTEGER;
ALTER TABLE worktrees ADD COLUMN git_dir_uid INTEGER;
"""


def migrate_worktree_git_identity(connection: sqlite3.Connection) -> None:
    try:
        connection.executescript(
            "BEGIN IMMEDIATE;\n" + WORKTREE_GIT_IDENTITY_SCHEMA_SQL
        )
        _backfill_worktree_git_identity(connection)
        connection.execute("PRAGMA user_version=47")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def _backfill_worktree_git_identity(connection: sqlite3.Connection) -> None:
    """Pin valid legacy Worktree gitdirs and leave uncertain rows unbound."""

    from pathlib import Path

    from eidos_runtime.git.backend import DulwichGitBackend
    from eidos_runtime.git.discovery import GitRepositoryDiscoveryService
    from eidos_runtime.git.errors import WorktreeError
    from eidos_runtime.workspace.reader import WorkspacePathError, capture_directory_identity

    discovery_service = GitRepositoryDiscoveryService(DulwichGitBackend())
    rows = connection.execute(
        """
        SELECT w.id, w.worktree_root, w.git_dir, w.git_common_dir, w.state,
               p.git_common_dir AS project_common_dir
        FROM worktrees AS w
        JOIN projects AS p ON p.id = w.project_id
        WHERE w.git_dir_dev IS NULL
           OR w.git_dir_inode IS NULL
           OR w.git_dir_uid IS NULL
        """
    ).fetchall()
    for row in rows:
        if row[4] == "deleted":
            continue
        expected_common = row[3] or row[6]
        if row[1] is None or row[2] is None or expected_common is None:
            continue
        try:
            root = Path(row[1])
            root_identity = capture_directory_identity(root)
            recorded_git_dir = Path(row[2])
            common_dir = Path(expected_common)
            capture_directory_identity(recorded_git_dir)
            capture_directory_identity(common_dir)
            discovered = discovery_service.resolve(root_identity.path)
            if (
                discovered is None
                or discovered.repository_root != str(root_identity.path)
                or Path(discovered.git_dir) != recorded_git_dir
                or Path(discovered.git_common_dir) != common_dir
            ):
                continue
            identity = capture_directory_identity(recorded_git_dir)
            if identity.path != recorded_git_dir:
                continue
        except (OSError, WorkspacePathError, WorktreeError, ValueError):
            continue
        connection.execute(
            "UPDATE worktrees SET git_dir_dev = ?, git_dir_inode = ?, "
            "git_dir_uid = ? WHERE id = ?",
            (identity.device, identity.inode, identity.owner, row[0]),
        )

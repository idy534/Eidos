from __future__ import annotations

import json
import sqlite3


PROJECT_BINDING_SCHEMA_SQL = """
ALTER TABLE worktree_lifecycle_operations ADD COLUMN workspace_roots_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE worktree_lifecycle_operations ADD COLUMN project_revision INTEGER;
UPDATE worktree_lifecycle_operations
SET workspace_roots_json = (
    SELECT sessions.workspace_roots_json
    FROM sessions
    WHERE sessions.id = worktree_lifecycle_operations.session_id
)
WHERE scope = 'session/create'
  AND snapshot_id IS NULL
  AND workspace_roots_json = '[]'
  AND session_id IS NOT NULL
  AND EXISTS (
      SELECT 1 FROM sessions
      WHERE sessions.id = worktree_lifecycle_operations.session_id
        AND sessions.workspace_roots_json <> '[]'
  );
UPDATE worktree_lifecycle_operations
SET workspace_roots_json = (
    SELECT source.workspace_roots_json
    FROM worktree_snapshots AS snapshot
    JOIN sessions AS source ON source.id = snapshot.session_id
    WHERE snapshot.id = worktree_lifecycle_operations.snapshot_id
      AND snapshot.state = 'ready'
      AND source.workspace_roots_json <> '[]'
)
WHERE scope IN ('session/create', 'checkpoint/fork')
  AND snapshot_id IS NOT NULL
  AND workspace_roots_json = '[]'
  AND EXISTS (
      SELECT 1
      FROM worktree_snapshots AS snapshot
      JOIN sessions AS source ON source.id = snapshot.session_id
      WHERE snapshot.id = worktree_lifecycle_operations.snapshot_id
        AND snapshot.state = 'ready'
        AND source.workspace_roots_json <> '[]'
  );
UPDATE worktree_lifecycle_operations
SET state = 'cleanup_required', error_code = 'workspace_binding_snapshot_unavailable'
WHERE scope IN ('session/create', 'checkpoint/fork')
  AND state NOT IN ('completed', 'cleanup_required')
  AND workspace_roots_json = '[]';
"""


def migrate_project_bindings(connection: sqlite3.Connection) -> None:
    try:
        connection.executescript("BEGIN IMMEDIATE;\n" + PROJECT_BINDING_SCHEMA_SQL)
        _backfill_session_git_metadata(connection)
        _backfill_lifecycle_roots(connection)
        connection.execute("PRAGMA user_version=46")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise


def _backfill_lifecycle_roots(connection: sqlite3.Connection) -> None:
    """Copy the fully pinned Session roots into existing lifecycle records."""

    connection.execute(
        """
        UPDATE worktree_lifecycle_operations
        SET workspace_roots_json = (
            SELECT sessions.workspace_roots_json
            FROM sessions
            WHERE sessions.id = worktree_lifecycle_operations.session_id
        )
        WHERE scope = 'session/create'
          AND snapshot_id IS NULL
          AND session_id IS NOT NULL
          AND EXISTS (
              SELECT 1 FROM sessions
              WHERE sessions.id = worktree_lifecycle_operations.session_id
                AND sessions.workspace_roots_json <> '[]'
          )
        """
    )
    connection.execute(
        """
        UPDATE worktree_lifecycle_operations
        SET workspace_roots_json = (
            SELECT source.workspace_roots_json
            FROM worktree_snapshots AS snapshot
            JOIN sessions AS source ON source.id = snapshot.session_id
            WHERE snapshot.id = worktree_lifecycle_operations.snapshot_id
              AND snapshot.state = 'ready'
        )
        WHERE scope IN ('session/create', 'checkpoint/fork')
          AND snapshot_id IS NOT NULL
          AND EXISTS (
              SELECT 1
              FROM worktree_snapshots AS snapshot
              JOIN sessions AS source ON source.id = snapshot.session_id
              WHERE snapshot.id = worktree_lifecycle_operations.snapshot_id
                AND snapshot.state = 'ready'
                AND source.workspace_roots_json <> '[]'
          )
        """
    )


def _backfill_session_git_metadata(connection: sqlite3.Connection) -> None:
    """Pin current Git metadata for v45 Sessions without changing root identity."""

    from pathlib import Path

    from eidos_runtime.domain.project_workspace import DirectoryIdentity, SessionRoot
    from eidos_runtime.git.backend import DulwichGitBackend
    from eidos_runtime.git.discovery import GitRepositoryDiscoveryService
    from eidos_runtime.git.errors import WorktreeError
    from eidos_runtime.workspace.reader import (
        WorkspacePathError,
        WorkspaceReader,
        capture_directory_identity,
    )

    discovery_service = GitRepositoryDiscoveryService(DulwichGitBackend())

    def clear_git_metadata(root: SessionRoot) -> SessionRoot:
        return root.model_copy(update={
            "git_dir": None,
            "git_dir_identity": None,
            "git_common_identity": None,
        })

    def metadata_identity(path: str) -> DirectoryIdentity:
        captured = capture_directory_identity(path)
        with WorkspaceReader(captured):
            return DirectoryIdentity(
                device=captured.device,
                inode=captured.inode,
                owner=captured.owner,
            )

    def backfill_root(root: SessionRoot) -> SessionRoot:
        if root.repository_root is None or (
            root.git_dir is not None
            and root.git_dir_identity is not None
            and root.git_common_dir is not None
            and root.git_common_identity is not None
        ):
            return root

        try:
            source_identity = capture_directory_identity(root.path)
            if (
                source_identity.path != Path(root.path)
                or (
                    source_identity.device,
                    source_identity.inode,
                    source_identity.owner,
                ) != (root.device, root.inode, root.owner)
            ):
                return clear_git_metadata(root)
            discovery = discovery_service.resolve(source_identity.path)
            if (
                discovery is None
                or discovery.repository_root != root.repository_root
                or (
                    root.git_dir is not None
                    and discovery.git_dir != root.git_dir
                )
                or (
                    root.git_common_dir is not None
                    and discovery.git_common_dir != root.git_common_dir
                )
            ):
                return clear_git_metadata(root)

            git_dir_identity = metadata_identity(discovery.git_dir)
            git_common_identity = metadata_identity(discovery.git_common_dir)
        except (OSError, WorktreeError, WorkspacePathError):
            return clear_git_metadata(root)

        return root.model_copy(update={
            "git_dir": discovery.git_dir,
            "git_common_dir": discovery.git_common_dir,
            "git_dir_identity": git_dir_identity,
            "git_common_identity": git_common_identity,
        })

    rows = connection.execute(
        "SELECT id, workspace_roots_json FROM sessions "
        "WHERE project_id IS NOT NULL AND workspace_roots_json <> '[]'"
    ).fetchall()
    for row in rows:
        roots = tuple(
            SessionRoot.model_validate(value)
            for value in json.loads(row[1])
        )
        updated = tuple(backfill_root(root) for root in roots)
        if updated != roots:
            connection.execute(
                "UPDATE sessions SET workspace_roots_json = ? WHERE id = ?",
                (
                    json.dumps(
                        [root.model_dump(mode="json") for root in updated],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                    row[0],
                ),
            )

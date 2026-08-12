from __future__ import annotations

import os
from pathlib import Path
import sqlite3

from eidos_runtime.db.schema import (
    SCHEMA_SQL,
    SCHEMA_VERSION,
    V21_SESSION_HANDOFF_SCHEMA_SQL,
    V22_WORKTREE_RETENTION_SCHEMA_SQL,
    V20_WORKTREE_BRANCH_OWNERSHIP_SCHEMA_SQL,
)
from eidos_runtime.db.storage import DATABASE_NAME, SessionStore


def test_v19_to_v20_preserves_worktrees_and_adds_phase3b_fields(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    database = data / DATABASE_NAME
    connection = sqlite3.connect(database)
    connection.executescript(
        SCHEMA_SQL
        .replace(V20_WORKTREE_BRANCH_OWNERSHIP_SCHEMA_SQL, "", 1)
        .replace(V21_SESSION_HANDOFF_SCHEMA_SQL, "", 1)
        .replace(V22_WORKTREE_RETENTION_SCHEMA_SQL, "", 1)
    )
    connection.execute(
        """
        INSERT INTO projects (
            id, workspace_root, git_repository_root, git_common_dir,
            created_at, updated_at
        ) VALUES ('project', '/repository', '/repository', '/repository/.git', 1, 1)
        """
    )
    connection.execute(
        """
        INSERT INTO worktrees (
            id, project_id, worktree_root, git_dir, base_ref, base_commit,
            branch, ownership, state, created_at, updated_at
        ) VALUES (
            'worktree', 'project', '/managed/worktree', '/managed/worktree/.git',
            'main', ?, 'eidos/legacy', 'managed', 'active', 2, 2
        )
        """,
        ("a" * 40,),
    )
    connection.execute(
        """
        INSERT INTO sessions (
            id, workspace_root, worktree_id, execution_mode,
            created_at, updated_at
        ) VALUES ('session', '/repository', 'worktree', 'worktree', 4, 4)
        """
    )
    connection.execute(
        """
        INSERT INTO worktree_lifecycle_operations (
            scope, operation_id, state, project_id, repository_root,
            worktree_id, worktree_root, base_ref, branch, base_commit,
            session_id, created_at, updated_at
        ) VALUES (
            'session/create', 'create', 'prepared', 'project', '/repository',
            'worktree', '/managed/worktree', 'main', 'eidos/legacy', ?,
            'session', 3, 3
        )
        """,
        ("a" * 40,),
    )
    connection.execute("PRAGMA user_version = 19")
    connection.commit()
    connection.close()
    os.chmod(database, 0o600)

    store = SessionStore(data)
    store.initialize()
    try:
        assert store.health() == {"state": "ready"}
        assert store.connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 22
        assert store.connection.execute(
            "SELECT branch_ownership FROM worktrees WHERE id = 'worktree'"
        ).fetchone()[0] == "legacy_managed"
        assert store.connection.execute(
            "SELECT associated_worktree_id FROM sessions WHERE id = 'session'"
        ).fetchone()[0] == "worktree"
        lifecycle = store.connection.execute(
            "SELECT expected_head, include_local_changes, source_head, source_branch, source_dirty "
            "FROM worktree_lifecycle_operations WHERE operation_id = 'create'"
        ).fetchone()
        assert tuple(lifecycle) == (None, 0, None, None, None)
        assert store.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert store.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        store.close()

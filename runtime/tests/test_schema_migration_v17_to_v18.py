from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sqlite3

from eidos_runtime.db.schema import (
    SCHEMA_VERSION,
    V12_BASE_SCHEMA_SQL,
    V10_CONTEXT_SCHEMA_SQL,
    V10_REPOSITORY_SCHEMA_SQL,
    V13_RESPONSE_ACTIONS_SCHEMA_SQL,
    V14_COMPACTION_QUALITY_SCHEMA_SQL,
    V15_WORKTREE_SCHEMA_SQL,
    V16_SESSION_WORKTREE_SCHEMA_SQL,
    V17_WORKTREE_LIFECYCLE_SCHEMA_SQL,
)
from eidos_runtime.db.storage import DATABASE_NAME, SessionStore


def _direct_project_id(workspace_root: str) -> str:
    return "project_" + hashlib.sha256(
        f"workspace\0{workspace_root}".encode("utf-8")
    ).hexdigest()


def _create_v17_database(
    data: Path,
    *,
    repository: Path,
    worktree_root: Path,
    direct_workspace_alias: Path,
) -> Path:
    database_path = data / DATABASE_NAME
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.executescript(
        V12_BASE_SCHEMA_SQL
        + V10_REPOSITORY_SCHEMA_SQL
        + V10_CONTEXT_SCHEMA_SQL
        + V13_RESPONSE_ACTIONS_SCHEMA_SQL
        + V14_COMPACTION_QUALITY_SCHEMA_SQL
        + V15_WORKTREE_SCHEMA_SQL
        + V16_SESSION_WORKTREE_SCHEMA_SQL
        + V17_WORKTREE_LIFECYCLE_SCHEMA_SQL
    )
    connection.execute(
        """
        INSERT INTO projects (
            id, repository_root, git_common_dir, created_at, updated_at
        ) VALUES ('git-project', ?, ?, 1, 2)
        """,
        (str(repository), str(repository / ".git")),
    )
    connection.execute(
        """
        INSERT INTO worktrees (
            id, project_id, worktree_root, git_dir, base_ref, base_commit,
            branch, ownership, state, created_at, updated_at
        ) VALUES ('worktree-1', 'git-project', ?, ?, 'main', ?,
                  'eidos/main-1', 'managed', 'active', 3, 4)
        """,
        (str(worktree_root), str(worktree_root / ".git"), "a" * 40),
    )
    connection.execute(
        """
        INSERT INTO sessions (
            id, workspace_root, workspace_dev, workspace_inode,
            workspace_uid, worktree_id, created_at, updated_at
        ) VALUES ('managed-session', ?, 1, 2, 3, 'worktree-1', 5, 6)
        """,
        (str(repository),),
    )
    connection.execute(
        """
        INSERT INTO sessions (
            id, workspace_root, workspace_dev, workspace_inode,
            workspace_uid, worktree_id, created_at, updated_at
        ) VALUES ('direct-session', ?, 10, 11, 12, NULL, 7, 8)
        """,
        (str(direct_workspace_alias),),
    )
    connection.execute(
        """
        INSERT INTO runs (
            id, session_id, user_input, model_profile_json, status,
            created_at, updated_at
        ) VALUES ('managed-run', 'managed-session', 'managed', '{}', 'succeeded', 9, 9)
        """
    )
    connection.execute(
        """
        INSERT INTO runs (
            id, session_id, user_input, model_profile_json, status,
            created_at, updated_at
        ) VALUES ('direct-run', 'direct-session', 'direct', '{}', 'succeeded', 10, 10)
        """
    )
    connection.execute("PRAGMA user_version = 17")
    connection.commit()
    connection.close()
    os.chmod(database_path, 0o600)
    return database_path


def test_v17_to_v18_preserves_git_bindings_and_creates_deterministic_direct_project(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / ".git").mkdir()
    direct_workspace = tmp_path / "direct-workspace"
    direct_workspace.mkdir()
    direct_alias = tmp_path / "direct-alias"
    direct_alias.symlink_to(direct_workspace, target_is_directory=True)
    worktree_root = tmp_path / "managed-worktree"
    worktree_root.mkdir()
    (worktree_root / ".git").write_text("gitdir: pointer\n", encoding="utf-8")
    _create_v17_database(
        data,
        repository=repository,
        worktree_root=worktree_root,
        direct_workspace_alias=direct_alias,
    )

    store = SessionStore(data)
    store.initialize()
    try:
        assert store.health() == {"state": "ready"}
        assert store.connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 19
        git_project = store.connection.execute(
            "SELECT * FROM projects WHERE id = 'git-project'"
        ).fetchone()
        assert git_project is not None
        assert git_project["workspace_root"] == str(repository)
        assert git_project["git_repository_root"] == str(repository)
        assert git_project["git_common_dir"] == str(repository / ".git")
        assert store.connection.execute(
            "SELECT project_id FROM worktrees WHERE id = 'worktree-1'"
        ).fetchone()[0] == "git-project"
        assert store.connection.execute(
            "SELECT worktree_id FROM sessions WHERE id = 'managed-session'"
        ).fetchone()[0] == "worktree-1"
        assert store.connection.execute(
            "SELECT execution_mode FROM sessions WHERE id = 'managed-session'"
        ).fetchone()[0] == "worktree"
        assert store.connection.execute(
            "SELECT execution_mode FROM sessions WHERE id = 'direct-session'"
        ).fetchone()[0] == "local"
        assert store.connection.execute(
            "SELECT branch FROM worktrees WHERE id = 'worktree-1'"
        ).fetchone()[0] == "eidos/main-1"

        direct_root = str(direct_workspace.resolve())
        direct_project = store.connection.execute(
            "SELECT * FROM projects WHERE workspace_root = ?", (direct_root,)
        ).fetchone()
        assert direct_project is not None
        assert direct_project["id"] == _direct_project_id(direct_root)
        direct_session = store.connection.execute(
            "SELECT workspace_root, worktree_id FROM sessions WHERE id = 'direct-session'"
        ).fetchone()
        assert tuple(direct_session) == (direct_root, None)
        assert store.connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 2
        assert store.connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 2
        assert store.connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert store.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        store.close()


def test_v17_to_v18_failure_keeps_the_v17_database_unchanged(
    tmp_path: Path,
    monkeypatch,
) -> None:
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / ".git").mkdir()
    worktree_root = tmp_path / "managed-worktree"
    worktree_root.mkdir()
    database_path = _create_v17_database(
        data,
        repository=repository,
        worktree_root=worktree_root,
        direct_workspace_alias=repository,
    )

    from eidos_runtime.db.migrations import v017_to_v018

    def fail(_connection: sqlite3.Connection) -> None:
        raise sqlite3.OperationalError("injected v18 failure")

    monkeypatch.setattr(v017_to_v018, "migrate", fail)
    store = SessionStore(data)
    store.initialize()
    try:
        assert store.health() == {
            "state": "health_only",
            "code": "schema_migration_failed",
        }
    finally:
        store.close()

    connection = sqlite3.connect(database_path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 17
        assert connection.execute(
            "SELECT COUNT(*) FROM projects WHERE id = 'git-project'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM sessions"
        ).fetchone()[0] == 2
    finally:
        connection.close()


def test_v17_to_v18_aggregates_direct_sessions_by_canonical_workspace(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / ".git").mkdir()
    direct_workspace = tmp_path / "direct-workspace"
    direct_workspace.mkdir()
    direct_alias = tmp_path / "direct-alias"
    direct_alias.symlink_to(direct_workspace, target_is_directory=True)
    database_path = _create_v17_database(
        data,
        repository=repository,
        worktree_root=tmp_path / "managed-worktree",
        direct_workspace_alias=direct_alias,
    )

    connection = sqlite3.connect(database_path)
    try:
        connection.execute(
            """
            INSERT INTO sessions (
                id, workspace_root, workspace_dev, workspace_inode,
                workspace_uid, worktree_id, created_at, updated_at
            ) VALUES (?, ?, 10, 11, 12, NULL, 21, 22)
            """,
            ("direct-session-real", str(direct_workspace)),
        )
        connection.execute(
            """
            INSERT INTO sessions (
                id, workspace_root, workspace_dev, workspace_inode,
                workspace_uid, worktree_id, created_at, updated_at
            ) VALUES (?, ?, 10, 11, 12, NULL, 31, 42)
            """,
            ("direct-session-alias-2", str(direct_alias)),
        )
        connection.commit()
    finally:
        connection.close()

    store = SessionStore(data)
    store.initialize()
    try:
        direct_root = str(direct_workspace.resolve())
        project = store.connection.execute(
            "SELECT * FROM projects WHERE workspace_root = ?", (direct_root,)
        ).fetchone()
        assert project is not None
        assert project["created_at"] == 7
        assert project["updated_at"] == 42
        assert store.connection.execute(
            "SELECT COUNT(*) FROM projects WHERE workspace_root = ?", (direct_root,)
        ).fetchone()[0] == 1
        assert {
            row[0]
            for row in store.connection.execute(
                "SELECT workspace_root FROM sessions WHERE worktree_id IS NULL"
            )
        } == {direct_root}
    finally:
        store.close()

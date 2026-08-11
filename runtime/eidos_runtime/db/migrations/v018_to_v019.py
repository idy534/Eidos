from __future__ import annotations

import sqlite3


FROM_VERSION = 18
TO_VERSION = 19


class InvalidV18SchemaError(RuntimeError):
    pass


def verify_v18_structure(connection: sqlite3.Connection) -> None:
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    if not {"sessions", "worktrees"} <= tables:
        raise InvalidV18SchemaError("v18 schema is incomplete")
    session_columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(sessions)")
    }
    worktree_columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(worktrees)")
    }
    if "worktree_id" not in session_columns:
        raise InvalidV18SchemaError("v18 session schema is incomplete")
    if not {
        "id",
        "project_id",
        "worktree_root",
        "git_dir",
        "base_ref",
        "base_commit",
        "branch",
        "ownership",
        "state",
    } <= worktree_columns:
        raise InvalidV18SchemaError("v18 worktree schema is incomplete")
    if "execution_mode" in session_columns:
        raise InvalidV18SchemaError("v18 session schema already has execution mode")


def migrate(connection: sqlite3.Connection) -> None:
    verify_v18_structure(connection)

    connection.execute(
        """
        CREATE TABLE worktrees_v19 (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
            worktree_root TEXT NOT NULL UNIQUE,
            git_dir TEXT NOT NULL,
            base_ref TEXT NOT NULL,
            base_commit TEXT NOT NULL,
            branch TEXT,
            ownership TEXT NOT NULL CHECK (ownership IN ('managed', 'adopted')),
            state TEXT NOT NULL CHECK (
                state IN ('active', 'missing', 'invalid', 'deleted')
            ),
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            UNIQUE(project_id, branch)
        )
        """
    )
    connection.execute(
        """
        INSERT INTO worktrees_v19 (
            id, project_id, worktree_root, git_dir, base_ref, base_commit,
            branch, ownership, state, created_at, updated_at
        )
        SELECT id, project_id, worktree_root, git_dir, base_ref, base_commit,
               branch, ownership, state, created_at, updated_at
        FROM worktrees
        """
    )
    connection.execute("DROP TABLE worktrees")
    connection.execute("ALTER TABLE worktrees_v19 RENAME TO worktrees")
    connection.execute(
        """
        CREATE INDEX worktrees_project_state
        ON worktrees(project_id, state, updated_at DESC)
        """
    )
    connection.execute(
        """
        CREATE INDEX worktrees_project_ownership
        ON worktrees(project_id, ownership, state)
        """
    )

    connection.execute(
        """
        ALTER TABLE sessions
        ADD COLUMN execution_mode TEXT NOT NULL DEFAULT 'local' CHECK (
            execution_mode IN ('local', 'worktree')
        )
        """
    )
    connection.execute(
        """
        UPDATE sessions
        SET execution_mode = CASE
            WHEN worktree_id IS NULL THEN 'local'
            ELSE 'worktree'
        END
        """
    )
    connection.execute(f"PRAGMA user_version = {TO_VERSION}")


__all__ = [
    "FROM_VERSION",
    "TO_VERSION",
    "InvalidV18SchemaError",
    "migrate",
    "verify_v18_structure",
]

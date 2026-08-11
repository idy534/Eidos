from __future__ import annotations

import sqlite3


FROM_VERSION = 20
TO_VERSION = 21


class InvalidV20SchemaError(RuntimeError):
    pass


def verify_v20_structure(connection: sqlite3.Connection) -> None:
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    if not {"sessions", "projects", "worktrees"} <= tables:
        raise InvalidV20SchemaError("v20 Session/Worktree schema is incomplete")
    session_columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(sessions)")
    }
    if not {"id", "worktree_id", "execution_mode"} <= session_columns:
        raise InvalidV20SchemaError("v20 Session table is incomplete")
    worktree_columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(worktrees)")
    }
    if "branch_ownership" not in worktree_columns:
        raise InvalidV20SchemaError("v20 Worktree branch ownership is missing")
    if "checkout_branch" in worktree_columns:
        raise InvalidV20SchemaError("v21 Worktree column already exists")
    if "associated_worktree_id" in session_columns:
        raise InvalidV20SchemaError("v21 Session column already exists")
    handoff = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name = 'session_handoff_operations'"
    ).fetchone()
    if handoff is not None:
        raise InvalidV20SchemaError("v21 handoff table already exists")


def migrate(connection: sqlite3.Connection) -> None:
    verify_v20_structure(connection)
    connection.execute("ALTER TABLE worktrees ADD COLUMN checkout_branch TEXT")
    connection.execute(
        "UPDATE worktrees SET checkout_branch = branch "
        "WHERE checkout_branch IS NULL AND branch IS NOT NULL"
    )
    connection.execute(
        "ALTER TABLE sessions ADD COLUMN associated_worktree_id TEXT "
        "REFERENCES worktrees(id) ON DELETE RESTRICT"
    )
    connection.execute(
        "UPDATE sessions SET associated_worktree_id = worktree_id "
        "WHERE execution_mode = 'worktree' AND worktree_id IS NOT NULL"
    )
    connection.execute(
        "CREATE INDEX sessions_associated_worktree_id "
        "ON sessions(associated_worktree_id)"
    )
    connection.execute(
        """
        CREATE TABLE session_handoff_operations (
            scope TEXT NOT NULL CHECK (
                scope IN ('session/handoff-local', 'session/handoff-worktree')
            ),
            operation_id TEXT NOT NULL,
            state TEXT NOT NULL CHECK (
                state IN (
                    'prepared', 'source_captured', 'target_materialized',
                    'session_rebound', 'completed', 'cleanup_required'
                )
            ),
            session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE RESTRICT,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
            source_mode TEXT NOT NULL CHECK (source_mode IN ('local', 'worktree')),
            target_mode TEXT NOT NULL CHECK (target_mode IN ('local', 'worktree')),
            source_root TEXT NOT NULL,
            target_root TEXT NOT NULL,
            source_common_dir TEXT NOT NULL,
            target_common_dir TEXT NOT NULL,
            associated_worktree_id TEXT,
            target_worktree_new INTEGER NOT NULL CHECK (target_worktree_new IN (0, 1)),
            target_base_ref TEXT,
            target_base_commit TEXT,
            source_head TEXT NOT NULL,
            source_branch TEXT,
            source_dirty INTEGER NOT NULL CHECK (source_dirty IN (0, 1)),
            source_fingerprint TEXT NOT NULL,
            target_head TEXT NOT NULL,
            target_branch TEXT,
            target_dirty INTEGER NOT NULL CHECK (target_dirty IN (0, 1)),
            target_fingerprint TEXT NOT NULL,
            target_after_head TEXT,
            target_after_branch TEXT,
            target_after_fingerprint TEXT,
            source_after_head TEXT,
            source_after_branch TEXT,
            source_after_fingerprint TEXT,
            error_code TEXT,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY (scope, operation_id)
        )
        """
    )
    connection.execute(
        "CREATE INDEX session_handoff_operations_state "
        "ON session_handoff_operations(state, updated_at)"
    )
    connection.execute(
        "CREATE INDEX session_handoff_operations_session "
        "ON session_handoff_operations(session_id, created_at DESC)"
    )
    connection.execute(f"PRAGMA user_version = {TO_VERSION}")


__all__ = [
    "FROM_VERSION",
    "TO_VERSION",
    "InvalidV20SchemaError",
    "migrate",
    "verify_v20_structure",
]

from __future__ import annotations

import sqlite3


FROM_VERSION = 21
TO_VERSION = 22


class InvalidV21SchemaError(RuntimeError):
    pass


def verify_v21_structure(connection: sqlite3.Connection) -> None:
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    if not {
        "sessions",
        "projects",
        "worktrees",
        "worktree_lifecycle_operations",
        "session_handoff_operations",
    } <= tables:
        raise InvalidV21SchemaError("v21 Worktree/Session schema is incomplete")
    worktree_columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(worktrees)")
    }
    if "last_used_at" in worktree_columns:
        raise InvalidV21SchemaError("v22 Worktree column already exists")
    if "runtime_settings" in tables or "worktree_snapshots" in tables:
        raise InvalidV21SchemaError("v22 retention tables already exist")
    lifecycle_columns = {
        str(row[1])
        for row in connection.execute(
            "PRAGMA table_info(worktree_lifecycle_operations)"
        )
    }
    if {"snapshot_id", "snapshot_head", "snapshot_fingerprint"} & lifecycle_columns:
        raise InvalidV21SchemaError("v22 lifecycle columns already exist")


def migrate(connection: sqlite3.Connection) -> None:
    verify_v21_structure(connection)
    connection.execute(
        "ALTER TABLE worktrees ADD COLUMN last_used_at INTEGER NOT NULL DEFAULT 0"
    )
    connection.execute(
        "UPDATE worktrees SET last_used_at = updated_at WHERE last_used_at = 0"
    )
    connection.execute(
        """
        CREATE TABLE runtime_settings (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            automatic_cleanup INTEGER NOT NULL CHECK (automatic_cleanup IN (0, 1)),
            managed_worktree_limit INTEGER NOT NULL CHECK (
                managed_worktree_limit BETWEEN 1 AND 100
            ),
            updated_at INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        """
        INSERT INTO runtime_settings (
            id, automatic_cleanup, managed_worktree_limit, updated_at
        ) VALUES (1, 1, 15, strftime('%s','now') * 1000)
        """
    )
    connection.execute(
        """
        CREATE TABLE worktree_snapshots (
            id TEXT PRIMARY KEY,
            worktree_id TEXT NOT NULL REFERENCES worktrees(id) ON DELETE RESTRICT,
            session_id TEXT REFERENCES sessions(id) ON DELETE RESTRICT,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
            base_ref TEXT NOT NULL,
            base_commit TEXT NOT NULL,
            head TEXT NOT NULL,
            branch TEXT,
            checkout_branch TEXT,
            branch_ownership TEXT NOT NULL CHECK (
                branch_ownership IN ('none', 'legacy_managed', 'user')
            ),
            dirty INTEGER NOT NULL CHECK (dirty IN (0, 1)),
            staged_paths_json TEXT NOT NULL,
            unstaged_paths_json TEXT NOT NULL,
            untracked_paths_json TEXT NOT NULL,
            conflict_paths_json TEXT NOT NULL,
            source_fingerprint TEXT NOT NULL,
            artifact_path TEXT NOT NULL,
            artifact_sha256 TEXT NOT NULL,
            full_patch_sha256 TEXT NOT NULL,
            staged_patch_sha256 TEXT NOT NULL,
            format_version INTEGER NOT NULL,
            state TEXT NOT NULL CHECK (state IN ('ready', 'restored', 'invalid')),
            created_at INTEGER NOT NULL,
            restored_at INTEGER,
            updated_at INTEGER NOT NULL
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX worktree_snapshots_latest
        ON worktree_snapshots(worktree_id, state, created_at DESC, id DESC)
        """
    )
    connection.execute(
        """
        CREATE TABLE worktree_lifecycle_operations_v22 (
            scope TEXT NOT NULL CHECK (
                scope IN (
                    'session/create', 'session/delete', 'checkpoint/fork',
                    'worktree/attach-branch', 'worktree/retention-cleanup',
                    'worktree/restore'
                )
            ),
            operation_id TEXT NOT NULL,
            state TEXT NOT NULL CHECK (
                state IN (
                    'prepared', 'worktree_created', 'session_created', 'run_created',
                    'checkpoint_action_created', 'branch_attached', 'snapshot_saved',
                    'state_materialized', 'worktree_rebound', 'worktree_deleted',
                    'completed', 'cleanup_required'
                )
            ),
            project_id TEXT,
            repository_root TEXT,
            worktree_id TEXT,
            worktree_root TEXT,
            base_ref TEXT,
            branch TEXT,
            base_commit TEXT,
            expected_head TEXT,
            session_id TEXT,
            run_id TEXT,
            checkpoint_id TEXT,
            include_local_changes INTEGER NOT NULL DEFAULT 0 CHECK (
                include_local_changes IN (0, 1)
            ),
            source_head TEXT,
            source_branch TEXT,
            source_dirty INTEGER CHECK (source_dirty IN (0, 1)),
            source_fingerprint TEXT,
            snapshot_id TEXT,
            snapshot_head TEXT,
            snapshot_fingerprint TEXT,
            error_code TEXT,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY (scope, operation_id)
        )
        """
    )
    connection.execute(
        """
        INSERT INTO worktree_lifecycle_operations_v22 (
            scope, operation_id, state, project_id,
            repository_root, worktree_id, worktree_root,
            base_ref, branch, base_commit, expected_head, session_id, run_id,
            checkpoint_id, include_local_changes, source_head,
            source_branch, source_dirty, source_fingerprint,
            error_code, created_at, updated_at
        )
        SELECT scope, operation_id, state, project_id,
               repository_root, worktree_id, worktree_root,
               base_ref, branch, base_commit, expected_head, session_id, run_id,
               checkpoint_id, include_local_changes, source_head,
               source_branch, source_dirty, source_fingerprint,
               error_code, created_at, updated_at
        FROM worktree_lifecycle_operations
        """
    )
    connection.execute("DROP TABLE worktree_lifecycle_operations")
    connection.execute(
        "ALTER TABLE worktree_lifecycle_operations_v22 "
        "RENAME TO worktree_lifecycle_operations"
    )
    connection.execute(
        "CREATE INDEX worktree_lifecycle_operations_state "
        "ON worktree_lifecycle_operations(state, updated_at)"
    )
    connection.execute(
        "CREATE INDEX worktree_lifecycle_operations_session "
        "ON worktree_lifecycle_operations(session_id, scope)"
    )
    connection.execute(f"PRAGMA user_version = {TO_VERSION}")


__all__ = [
    "FROM_VERSION",
    "TO_VERSION",
    "InvalidV21SchemaError",
    "migrate",
    "verify_v21_structure",
]

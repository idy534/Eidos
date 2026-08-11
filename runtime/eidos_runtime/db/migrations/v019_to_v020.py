from __future__ import annotations

import sqlite3


FROM_VERSION = 19
TO_VERSION = 20


class InvalidV19SchemaError(RuntimeError):
    pass


def verify_v19_structure(connection: sqlite3.Connection) -> None:
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    if not {"worktrees", "worktree_lifecycle_operations"} <= tables:
        raise InvalidV19SchemaError("v19 worktree schema is incomplete")
    worktree_columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(worktrees)")
    }
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
        raise InvalidV19SchemaError("v19 Worktree table is incomplete")
    lifecycle_columns = {
        str(row[1])
        for row in connection.execute(
            "PRAGMA table_info(worktree_lifecycle_operations)"
        )
    }
    if not {
        "scope",
        "operation_id",
        "state",
        "project_id",
        "repository_root",
        "worktree_id",
        "worktree_root",
        "base_ref",
        "branch",
        "base_commit",
        "session_id",
        "run_id",
        "checkpoint_id",
        "error_code",
        "created_at",
        "updated_at",
    } <= lifecycle_columns:
        raise InvalidV19SchemaError("v19 Worktree lifecycle table is incomplete")
    if "branch_ownership" in worktree_columns:
        raise InvalidV19SchemaError("v20 Worktree column already exists")
    if {
        "include_local_changes",
        "source_head",
        "source_branch",
        "source_dirty",
    } & lifecycle_columns:
        raise InvalidV19SchemaError("v20 lifecycle columns already exist")


def migrate(connection: sqlite3.Connection) -> None:
    verify_v19_structure(connection)

    connection.execute(
        """
        ALTER TABLE worktrees
        ADD COLUMN branch_ownership TEXT NOT NULL DEFAULT 'legacy_managed' CHECK (
            branch_ownership IN ('none', 'legacy_managed', 'user')
        )
        """
    )
    connection.execute(
        """
        UPDATE worktrees
        SET branch_ownership = CASE
            WHEN branch IS NULL THEN 'none'
            ELSE 'legacy_managed'
        END
        """
    )

    connection.execute(
        """
        CREATE TABLE worktree_lifecycle_operations_v20 (
            scope TEXT NOT NULL CHECK (
                scope IN (
                    'session/create',
                    'session/delete',
                    'checkpoint/fork',
                    'worktree/attach-branch'
                )
            ),
            operation_id TEXT NOT NULL,
            state TEXT NOT NULL CHECK (
                state IN (
                    'prepared',
                    'worktree_created',
                    'session_created',
                    'run_created',
                    'checkpoint_action_created',
                    'branch_attached',
                    'worktree_deleted',
                    'completed',
                    'cleanup_required'
                )
            ),
            project_id TEXT,
            repository_root TEXT,
            worktree_id TEXT,
            worktree_root TEXT,
            base_ref TEXT,
            branch TEXT,
            base_commit TEXT,
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
            error_code TEXT,
            created_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY (scope, operation_id)
        )
        """
    )
    connection.execute(
        """
        INSERT INTO worktree_lifecycle_operations_v20 (
            scope, operation_id, state, project_id,
            repository_root, worktree_id, worktree_root,
            base_ref, branch, base_commit, session_id, run_id,
            checkpoint_id, include_local_changes, source_head,
            source_branch, source_dirty, source_fingerprint, error_code,
            created_at, updated_at
        )
        SELECT scope, operation_id, state, project_id,
               repository_root, worktree_id, worktree_root,
               base_ref, branch, base_commit, session_id, run_id,
               checkpoint_id, 0, NULL, NULL, NULL, NULL,
               error_code, created_at, updated_at
        FROM worktree_lifecycle_operations
        """
    )
    connection.execute("DROP TABLE worktree_lifecycle_operations")
    connection.execute(
        "ALTER TABLE worktree_lifecycle_operations_v20 "
        "RENAME TO worktree_lifecycle_operations"
    )
    connection.execute(
        """
        CREATE INDEX worktree_lifecycle_operations_state
        ON worktree_lifecycle_operations(state, updated_at)
        """
    )
    connection.execute(
        """
        CREATE INDEX worktree_lifecycle_operations_session
        ON worktree_lifecycle_operations(session_id, scope)
        """
    )
    connection.execute(f"PRAGMA user_version = {TO_VERSION}")


__all__ = [
    "FROM_VERSION",
    "TO_VERSION",
    "InvalidV19SchemaError",
    "migrate",
    "verify_v19_structure",
]

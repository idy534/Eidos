from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sqlite3

from eidos_runtime.db.schema import V18_PROJECT_SCHEMA_SQL


FROM_VERSION = 17
TO_VERSION = 18


class InvalidV17SchemaError(RuntimeError):
    pass


def verify_v17_structure(connection: sqlite3.Connection) -> None:
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    if not {"sessions", "projects", "worktrees"} <= tables:
        raise InvalidV17SchemaError("v17 schema is incomplete")
    project_columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(projects)")
    }
    session_columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(sessions)")
    }
    if not {"repository_root", "git_common_dir"} <= project_columns:
        raise InvalidV17SchemaError("v17 project schema is incomplete")
    if "worktree_id" not in session_columns:
        raise InvalidV17SchemaError("v17 session schema is incomplete")


def migrate(connection: sqlite3.Connection) -> None:
    verify_v17_structure(connection)

    create_sql = V18_PROJECT_SCHEMA_SQL.replace(
        "CREATE TABLE projects (",
        "CREATE TABLE projects_v18 (",
        1,
    )
    connection.execute(create_sql)
    connection.execute(
        """
        INSERT INTO projects_v18 (
            id, workspace_root, git_repository_root, git_common_dir,
            created_at, updated_at
        )
        SELECT id, repository_root, repository_root, git_common_dir,
               created_at, updated_at
        FROM projects
        """
    )

    direct_roots: dict[str, tuple[int, int]] = {}
    for row in connection.execute(
        """
        SELECT id, workspace_root, MIN(created_at) AS created_at,
               MAX(updated_at) AS updated_at
        FROM sessions
        WHERE worktree_id IS NULL
        GROUP BY id, workspace_root
    """
    ):
        root = _canonical_workspace_root(str(row["workspace_root"]))
        direct_roots[root] = (int(row["created_at"]), int(row["updated_at"]))
        # V17 writes already canonicalized this field, but older direct rows
        # can contain an equivalent relative segment or symlink path. The
        # direct Project projection must use the same canonical identity.
        connection.execute(
            "UPDATE sessions SET workspace_root = ? WHERE id = ?",
            (root, row["id"]),
        )

    for workspace_root, (created_at, updated_at) in direct_roots.items():
        connection.execute(
            """
            INSERT OR IGNORE INTO projects_v18 (
                id, workspace_root, git_repository_root, git_common_dir,
                created_at, updated_at
            ) VALUES (?, ?, NULL, NULL, ?, ?)
            """,
            (
                direct_project_id(workspace_root),
                workspace_root,
                created_at,
                updated_at,
            ),
        )

    connection.execute("DROP TABLE projects")
    connection.execute("ALTER TABLE projects_v18 RENAME TO projects")
    connection.execute(f"PRAGMA user_version = {TO_VERSION}")


def direct_project_id(workspace_root: str) -> str:
    return "project_" + hashlib.sha256(
        f"workspace\0{workspace_root}".encode("utf-8")
    ).hexdigest()


def _canonical_workspace_root(value: str) -> str:
    path = Path(os.path.realpath(value))
    if not path.is_absolute():
        raise InvalidV17SchemaError("v17 workspace root is not absolute")
    return str(path)


__all__ = [
    "FROM_VERSION",
    "TO_VERSION",
    "InvalidV17SchemaError",
    "direct_project_id",
    "migrate",
    "verify_v17_structure",
]

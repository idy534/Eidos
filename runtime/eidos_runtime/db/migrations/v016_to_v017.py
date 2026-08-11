from __future__ import annotations

import sqlite3

from eidos_runtime.db.schema import V17_WORKTREE_LIFECYCLE_SCHEMA_SQL


FROM_VERSION = 16
TO_VERSION = 17

_REQUIRED_TABLES = frozenset({"sessions", "projects", "worktrees"})


class InvalidV16SchemaError(RuntimeError):
    pass


def verify_v16_structure(connection: sqlite3.Connection) -> None:
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    if not _REQUIRED_TABLES <= tables:
        raise InvalidV16SchemaError("v16 schema is incomplete")
    columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(sessions)")
    }
    if "worktree_id" not in columns:
        raise InvalidV16SchemaError("v16 session schema is incomplete")


def migrate(connection: sqlite3.Connection) -> None:
    verify_v16_structure(connection)
    for statement in V17_WORKTREE_LIFECYCLE_SCHEMA_SQL.split(";"):
        if statement.strip():
            connection.execute(statement)
    connection.execute(f"PRAGMA user_version = {TO_VERSION}")

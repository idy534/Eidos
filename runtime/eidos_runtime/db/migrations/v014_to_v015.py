from __future__ import annotations

import sqlite3

from eidos_runtime.db.schema import V15_WORKTREE_SCHEMA_SQL


FROM_VERSION = 14
TO_VERSION = 15

_REQUIRED_TABLES = frozenset({
    "compact_summaries",
    "response_feedback",
    "run_revisions",
})


class InvalidV14SchemaError(RuntimeError):
    pass


def verify_v14_structure(connection: sqlite3.Connection) -> None:
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    if not _REQUIRED_TABLES <= tables:
        raise InvalidV14SchemaError("v14 schema is incomplete")
    columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(compact_summaries)")
    }
    if "summary_metadata_json" not in columns:
        raise InvalidV14SchemaError("v14 compaction schema is incomplete")


def migrate(connection: sqlite3.Connection) -> None:
    verify_v14_structure(connection)
    for statement in V15_WORKTREE_SCHEMA_SQL.split(";"):
        if statement.strip():
            connection.execute(statement)
    connection.execute(f"PRAGMA user_version = {TO_VERSION}")

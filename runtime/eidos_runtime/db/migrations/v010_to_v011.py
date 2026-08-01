from __future__ import annotations

import sqlite3


FROM_VERSION = 10
TO_VERSION = 11

_REQUIRED_TABLES = frozenset({
    "runs",
    "model_profiles",
    "model_capability_snapshots",
    "run_model_snapshots",
    "run_resolution_snapshots",
    "model_attempts",
    "repository_snapshots",
    "context_snapshots",
})


class InvalidV10SchemaError(RuntimeError):
    pass


def verify_v10_structure(connection: sqlite3.Connection) -> None:
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    if not _REQUIRED_TABLES <= tables:
        raise InvalidV10SchemaError("v10 schema is incomplete")


def migrate(connection: sqlite3.Connection) -> None:
    verify_v10_structure(connection)
    connection.execute("DROP TABLE run_model_snapshots")
    connection.execute("DROP TABLE model_capability_snapshots")
    connection.execute("DROP TABLE model_profiles")
    connection.execute("ALTER TABLE runs DROP COLUMN model_profile_id")
    connection.execute(f"PRAGMA user_version = {TO_VERSION}")

from __future__ import annotations

import sqlite3


FROM_VERSION = 11
TO_VERSION = 12

_REQUIRED_TABLES = frozenset({
    "runs",
    "step_resolution_snapshots",
    "run_resolution_snapshots",
    "rule_resolution_snapshots",
    "steps",
})


class InvalidV11SchemaError(RuntimeError):
    pass


def verify_v11_structure(connection: sqlite3.Connection) -> None:
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    if not _REQUIRED_TABLES <= tables:
        raise InvalidV11SchemaError("v11 schema is incomplete")
    # Verify that the legacy model tables are absent (were dropped in V11)
    legacy_tables = {"model_profiles", "model_capability_snapshots", "run_model_snapshots"}
    if legacy_tables & tables:
        raise InvalidV11SchemaError("v11 schema still contains legacy model tables")


def migrate(connection: sqlite3.Connection) -> None:
    verify_v11_structure(connection)
    # Add effective_cwd column to runs (nullable, NULL = workspace root)
    connection.execute(
        "ALTER TABLE runs ADD COLUMN effective_cwd TEXT"
    )
    # Add resolved_instructions_hash to step_resolution_snapshots
    # (nullable for backwards-compat; NULL means pre-v12 snapshot)
    connection.execute(
        "ALTER TABLE step_resolution_snapshots "
        "ADD COLUMN resolved_instructions_hash TEXT"
    )
    # Add effective_cwd to step_resolution_snapshots
    # (nullable; NULL means pre-v12 snapshot used workspace root)
    connection.execute(
        "ALTER TABLE step_resolution_snapshots "
        "ADD COLUMN effective_cwd TEXT"
    )
    connection.execute(f"PRAGMA user_version = {TO_VERSION}")

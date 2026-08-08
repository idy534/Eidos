from __future__ import annotations

import sqlite3


FROM_VERSION = 12
TO_VERSION = 13

_RESPONSE_ACTIONS_SQL = """
CREATE TABLE response_feedback (
    item_id TEXT PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
    value TEXT NOT NULL CHECK (value IN ('up', 'down')),
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE run_revisions (
    run_id TEXT PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
    source_run_id TEXT NOT NULL UNIQUE REFERENCES runs(id) ON DELETE CASCADE,
    revision_kind TEXT NOT NULL CHECK (revision_kind IN ('regenerate', 'edit')),
    created_at INTEGER NOT NULL,
    CHECK (run_id <> source_run_id)
);

CREATE INDEX run_revisions_source
ON run_revisions(source_run_id);
"""

_REQUIRED_TABLES = frozenset({"runs", "items", "step_resolution_snapshots"})


class InvalidV12SchemaError(RuntimeError):
    pass


def verify_v12_structure(connection: sqlite3.Connection) -> None:
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    if not _REQUIRED_TABLES <= tables:
        raise InvalidV12SchemaError("v12 schema is incomplete")

    run_columns = {
        str(row[1]) for row in connection.execute("PRAGMA table_info(runs)")
    }
    step_columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(step_resolution_snapshots)")
    }
    if "effective_cwd" not in run_columns:
        raise InvalidV12SchemaError("v12 runs.effective_cwd is missing")
    if not {"resolved_instructions_hash", "effective_cwd"} <= step_columns:
        raise InvalidV12SchemaError("v12 step resolution columns are missing")


def migrate(connection: sqlite3.Connection) -> None:
    verify_v12_structure(connection)
    connection.executescript(_RESPONSE_ACTIONS_SQL)
    connection.execute(f"PRAGMA user_version = {TO_VERSION}")

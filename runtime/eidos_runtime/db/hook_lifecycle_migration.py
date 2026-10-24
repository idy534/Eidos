"""Local Hook trust and background delivery extend the existing Hook journal."""
from __future__ import annotations

import sqlite3


HOOK_LIFECYCLE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS hook_sources (
    id TEXT PRIMARY KEY,
    scope TEXT NOT NULL CHECK(scope IN ('user','project')),
    paths_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    definitions_json TEXT NOT NULL,
    trusted_hash TEXT,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
    error_code TEXT,
    updated_at INTEGER NOT NULL
);
"""

HOOK_INVOCATION_COLUMNS = {
    "binding_json": "TEXT",
    "background": "INTEGER NOT NULL DEFAULT 0 CHECK(background IN (0,1))",
    "delivered_run_id": "TEXT",
    "discarded_at": "INTEGER",
}
HOOK_INVOCATION_EXTENSION_SQL = "\n".join(
    f"ALTER TABLE hook_invocations ADD COLUMN {name} {declaration};"
    for name, declaration in HOOK_INVOCATION_COLUMNS.items()
)


def migrate_hook_lifecycle(connection: sqlite3.Connection) -> None:
    try:
        connection.executescript("BEGIN IMMEDIATE;\n" + HOOK_LIFECYCLE_SCHEMA_SQL)
        columns = {row[1] for row in connection.execute("PRAGMA table_info(hook_invocations)")}
        for name, declaration in HOOK_INVOCATION_COLUMNS.items():
            if name not in columns:
                connection.execute(f"ALTER TABLE hook_invocations ADD COLUMN {name} {declaration}")
        connection.execute("PRAGMA user_version=39")
        connection.commit()
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise

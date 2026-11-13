"""Schema v54: when each memory note was last read by the model.

The two columns only order the oldest-first listing and the Memory page detail.
They never change a note's revision, its scope generation or MEMORY.md.
"""

from __future__ import annotations

import sqlite3

MEMORY_USAGE_SCHEMA_SQL = """
ALTER TABLE memory_documents ADD COLUMN last_read_at INTEGER;
ALTER TABLE memory_documents ADD COLUMN read_count INTEGER NOT NULL DEFAULT 0;
"""


def migrate_memory_usage(connection: sqlite3.Connection) -> None:
    try:
        connection.executescript(
            "BEGIN IMMEDIATE;\n" + MEMORY_USAGE_SCHEMA_SQL
            + "\nPRAGMA user_version=54;\nCOMMIT;"
        )
    except sqlite3.Error:
        connection.rollback()
        raise

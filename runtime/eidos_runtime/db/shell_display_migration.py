from __future__ import annotations

import sqlite3

SHELL_DISPLAY_MIGRATION_SQL = """
ALTER TABLE tool_calls ADD COLUMN display_intent TEXT;
ALTER TABLE tool_calls ADD COLUMN display_command TEXT;
"""


def migrate_shell_display(connection: sqlite3.Connection) -> None:
    try:
        connection.executescript(
            "BEGIN IMMEDIATE;\n" + SHELL_DISPLAY_MIGRATION_SQL
            + "\nPRAGMA user_version=53;\nCOMMIT;"
        )
    except sqlite3.Error:
        connection.rollback()
        raise

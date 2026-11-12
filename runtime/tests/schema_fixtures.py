"""Remove newer features when a test builds a pre-v51 database in place."""
from __future__ import annotations

import sqlite3


def remove_shell_display_schema(connection: sqlite3.Connection) -> None:
    """Drop the optional v53 columns when constructing an older database."""
    columns = {row[1] for row in connection.execute("PRAGMA table_info(tool_calls)")}
    for name in ("display_intent", "display_command"):
        if name in columns:
            connection.execute(f"ALTER TABLE tool_calls DROP COLUMN {name}")


def remove_goal_schema(connection: sqlite3.Connection) -> None:
    """Drop the v51 objects and later ToolCall columns from a current database."""
    connection.execute("DROP TRIGGER IF EXISTS goal_item_no_learning")
    connection.execute("DROP TRIGGER IF EXISTS goal_run_no_learning")
    connection.execute("DROP TABLE IF EXISTS goal_continuations")
    connection.execute("DROP TABLE IF EXISTS goal_runs")
    connection.execute("DROP TABLE IF EXISTS session_goals")
    remove_shell_display_schema(connection)

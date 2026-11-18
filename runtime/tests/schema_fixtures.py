"""Remove newer features when a test builds a pre-v51 database in place."""
from __future__ import annotations

import sqlite3


def remove_agent_workspace_schema(connection: sqlite3.Connection) -> None:
    """Drop the v55 child workspace objects when constructing an older database."""
    connection.execute("DROP TABLE IF EXISTS workspace_integrations")
    connection.execute("DROP TABLE IF EXISTS workspace_deliveries")
    connection.execute("DROP INDEX IF EXISTS agent_messages_operation")
    for table, names in {
        "agent_messages": ("sender_kind", "operation_id"),
        "agent_delegations": ("workspace_mode", "worktree_id", "base_tree", "last_user_instruction"),
    }.items():
        columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        for name in names:
            if name in columns:
                connection.execute(f"ALTER TABLE {table} DROP COLUMN {name}")


def remove_memory_usage_schema(connection: sqlite3.Connection) -> None:
    """Drop the v54 memory read columns, and later ones, when constructing an older database."""
    remove_agent_workspace_schema(connection)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(memory_documents)")}
    for name in ("last_read_at", "read_count"):
        if name in columns:
            connection.execute(f"ALTER TABLE memory_documents DROP COLUMN {name}")


def remove_shell_display_schema(connection: sqlite3.Connection) -> None:
    """Drop the optional v53 columns, and later ones, when constructing an older database."""
    remove_memory_usage_schema(connection)
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

"""Remove the goal feature when a test builds a pre-v51 database in place."""
from __future__ import annotations

import sqlite3


def remove_goal_schema(connection: sqlite3.Connection) -> None:
    connection.execute("DROP TRIGGER goal_item_no_learning")
    connection.execute("DROP TRIGGER goal_run_no_learning")
    connection.execute("DROP TABLE goal_continuations")
    connection.execute("DROP TABLE goal_runs")
    connection.execute("DROP TABLE session_goals")

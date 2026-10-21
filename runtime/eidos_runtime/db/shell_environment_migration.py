from __future__ import annotations

import sqlite3


SHELL_ENVIRONMENT_SCHEMA_SQL = """
CREATE TABLE shell_environment_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    revision INTEGER NOT NULL CHECK (revision >= 0),
    inherit TEXT NOT NULL CHECK (inherit IN ('all', 'core', 'none'))
);
"""


def migrate_shell_environment(connection: sqlite3.Connection) -> None:
    try:
        connection.executescript(
            "BEGIN IMMEDIATE;\n"
            + SHELL_ENVIRONMENT_SCHEMA_SQL
            + "PRAGMA user_version=35;\nCOMMIT;"
        )
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise

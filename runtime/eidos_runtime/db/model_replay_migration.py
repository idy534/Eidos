"""Add the nullable internal response replay reference without changing history."""

import sqlite3

from eidos_runtime.db.schema import V21_TO_V22_MIGRATION_SQL


def migrate_model_replay(connection: sqlite3.Connection) -> None:
    try:
        connection.executescript(
            "BEGIN IMMEDIATE;\n"
            + V21_TO_V22_MIGRATION_SQL
            + "PRAGMA user_version = 22;\nCOMMIT;"
        )
    except BaseException:
        connection.rollback()
        raise

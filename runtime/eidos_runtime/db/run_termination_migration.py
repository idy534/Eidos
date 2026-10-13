import sqlite3

from eidos_runtime.db.schema import RUN_TERMINATION_SCHEMA_SQL


def migrate_run_termination(connection: sqlite3.Connection) -> None:
    try:
        connection.executescript(
            "BEGIN IMMEDIATE;\n" + RUN_TERMINATION_SCHEMA_SQL
            + "PRAGMA user_version = 24;\nCOMMIT;"
        )
    except BaseException:
        connection.rollback()
        raise

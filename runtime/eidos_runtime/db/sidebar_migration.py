import sqlite3

from eidos_runtime.db.schema import SIDEBAR_PINS_SCHEMA_SQL


def migrate_sidebar_pins(connection: sqlite3.Connection) -> None:
    try:
        connection.executescript(
            "BEGIN IMMEDIATE;\n" + SIDEBAR_PINS_SCHEMA_SQL
            + "PRAGMA user_version = 25;\nCOMMIT;"
        )
    except BaseException:
        connection.rollback()
        raise

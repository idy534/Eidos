"""Add creation reservations. Old Projectless Session facts are discarded at startup."""

import sqlite3

from eidos_runtime.db.schema import PROJECTLESS_WORKSPACE_SCHEMA_SQL


def migrate_projectless_workspaces(connection: sqlite3.Connection) -> None:
    try:
        connection.executescript(
            "BEGIN IMMEDIATE;\n" + PROJECTLESS_WORKSPACE_SCHEMA_SQL
            + "PRAGMA user_version = 23;\nCOMMIT;"
        )
    except BaseException:
        connection.rollback()
        raise

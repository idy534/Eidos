from __future__ import annotations

import sqlite3


PERSONALIZATION_SCHEMA_SQL = """
CREATE TABLE personalization_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    revision INTEGER NOT NULL CHECK (revision >= 1),
    preferences_json TEXT NOT NULL CHECK (json_valid(preferences_json))
);
"""


def migrate_personalization(connection: sqlite3.Connection) -> None:
    try:
        connection.executescript(
            "BEGIN IMMEDIATE;\n" + PERSONALIZATION_SCHEMA_SQL
            + "PRAGMA user_version=44;\nCOMMIT;"
        )
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise

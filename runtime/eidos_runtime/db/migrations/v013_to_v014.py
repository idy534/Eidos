from __future__ import annotations

import sqlite3


FROM_VERSION = 13
TO_VERSION = 14

_REQUIRED_TABLES = frozenset({"compact_summaries"})


class InvalidV13SchemaError(RuntimeError):
    pass


def verify_v13_structure(connection: sqlite3.Connection) -> None:
    tables = {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    if not _REQUIRED_TABLES <= tables:
        raise InvalidV13SchemaError("v13 schema is incomplete")
    columns = {
        str(row[1])
        for row in connection.execute("PRAGMA table_info(compact_summaries)")
    }
    if "source_item_ids_json" not in columns:
        raise InvalidV13SchemaError("v13 compact summary schema is incomplete")


def migrate(connection: sqlite3.Connection) -> None:
    verify_v13_structure(connection)
    connection.execute(
        "ALTER TABLE compact_summaries "
        "ADD COLUMN summary_metadata_json TEXT NOT NULL DEFAULT '{}'"
    )
    connection.execute(f"PRAGMA user_version = {TO_VERSION}")

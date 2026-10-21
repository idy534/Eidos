from __future__ import annotations

import sqlite3

WEB_SEARCH_SCHEMA_SQL = """
CREATE TABLE web_search_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    settings_json TEXT NOT NULL CHECK (json_valid(settings_json))
);
CREATE TABLE run_web_search_snapshots (
    run_id TEXT PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
    snapshot_json TEXT NOT NULL CHECK (json_valid(snapshot_json))
);
CREATE INDEX run_web_search_credential_ref
    ON run_web_search_snapshots(json_extract(snapshot_json, '$.credential_ref'));
"""


def migrate_web_search(connection: sqlite3.Connection) -> None:
    try:
        connection.executescript(
            "BEGIN IMMEDIATE;\n"
            + WEB_SEARCH_SCHEMA_SQL
            + "PRAGMA user_version=34;\nCOMMIT;"
        )
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise

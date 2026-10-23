from __future__ import annotations

import sqlite3


PLUGIN_VERSIONS_SCHEMA_SQL = """
CREATE TABLE plugin_versions (
    plugin_id TEXT NOT NULL REFERENCES plugins(id),
    version TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    PRIMARY KEY(plugin_id, version)
);
INSERT INTO plugin_versions SELECT id, version, content_hash FROM plugins;
"""


def migrate_plugin_versions(connection: sqlite3.Connection) -> None:
    try:
        connection.executescript("BEGIN IMMEDIATE;\n" + PLUGIN_VERSIONS_SCHEMA_SQL
                                 + "PRAGMA user_version=37;\nCOMMIT;")
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise

from __future__ import annotations

import sqlite3


MCP_CONNECTIONS_SCHEMA_SQL = """
CREATE TABLE mcp_connections (
    plugin_id TEXT NOT NULL,
    server_id TEXT NOT NULL,
    config_hash TEXT NOT NULL,
    generation TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('disconnected','connecting','connected','expired','error')),
    credential_ref TEXT,
    account_label TEXT,
    expires_at INTEGER,
    error_code TEXT,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY(plugin_id, server_id)
);
"""


def migrate_mcp_connections(connection: sqlite3.Connection) -> None:
    try:
        connection.executescript("BEGIN IMMEDIATE;\n" + MCP_CONNECTIONS_SCHEMA_SQL
                                 + "PRAGMA user_version=38;\nCOMMIT;")
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise

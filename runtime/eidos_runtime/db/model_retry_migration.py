from __future__ import annotations

import sqlite3

MODEL_RETRY_SCHEMA_SQL = """
ALTER TABLE model_attempts ADD COLUMN request_group_id TEXT;
ALTER TABLE model_attempts ADD COLUMN retry_index INTEGER NOT NULL DEFAULT 0 CHECK(retry_index BETWEEN 0 AND 9);
ALTER TABLE model_attempts ADD COLUMN retry_deadline_at INTEGER;
ALTER TABLE model_attempts ADD COLUMN assistant_item_id TEXT REFERENCES items(id) ON DELETE RESTRICT;
CREATE UNIQUE INDEX model_request_retry_index ON model_attempts(request_group_id,retry_index)
WHERE request_group_id IS NOT NULL;
"""


def migrate_model_retries(connection: sqlite3.Connection) -> None:
    try:
        connection.executescript(
            "BEGIN IMMEDIATE;\n"
            + MODEL_RETRY_SCHEMA_SQL
            + "\nPRAGMA user_version=31;\nCOMMIT;"
        )
    except sqlite3.Error:
        if connection.in_transaction:
            connection.rollback()
        raise

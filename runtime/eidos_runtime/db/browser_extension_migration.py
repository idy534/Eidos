from __future__ import annotations

import sqlite3


# Schema v50: the user's Chrome through the Playwright Extension becomes the
# recommended target. Without a saved extension token it falls back to the
# Eidos browser, so existing "eidos" rows move to it without changing behavior.
# The token itself lives in an owner-only secret file; SQLite keeps only its
# reference.
BROWSER_EXTENSION_SCHEMA_SQL = """
CREATE TABLE browser_settings_v50 (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    target TEXT NOT NULL CHECK (target IN ('eidos', 'user_chrome', 'chrome_extension')),
    extension_credential TEXT,
    updated_at INTEGER NOT NULL
);
INSERT INTO browser_settings_v50 (id, target, extension_credential, updated_at)
SELECT id, CASE target WHEN 'eidos' THEN 'chrome_extension' ELSE target END, NULL, updated_at
FROM browser_settings;
DROP TABLE browser_settings;
ALTER TABLE browser_settings_v50 RENAME TO browser_settings;
"""


def migrate_browser_extension(connection: sqlite3.Connection) -> None:
    try:
        connection.executescript(
            "BEGIN IMMEDIATE;\n"
            + BROWSER_EXTENSION_SCHEMA_SQL
            + "\nPRAGMA user_version=50;\nCOMMIT;"
        )
    except sqlite3.Error:
        if connection.in_transaction:
            connection.rollback()
        raise

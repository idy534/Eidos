"""Schema v58 records the native Windows shell sandbox."""

from __future__ import annotations

import sqlite3


V57_TOOL_ATTEMPTS_TABLE_SQL = """
CREATE TABLE tool_attempts (
    creation_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    tool_call_id TEXT NOT NULL REFERENCES tool_calls(id) ON DELETE RESTRICT,
    ordinal INTEGER NOT NULL CHECK (ordinal IN (0, 1)),
    sandbox_type TEXT NOT NULL CHECK (
        sandbox_type IN ('macos_seatbelt', 'none')
    ),
    sandbox_requested INTEGER NOT NULL CHECK (sandbox_requested IN (0, 1)),
    effective_permissions_json TEXT NOT NULL,
    profile_hash TEXT,
    escalation_reason TEXT,
    status TEXT NOT NULL CHECK (
        status IN ('running', 'completed', 'failed', 'canceled', 'uncertain')
    ),
    started_at INTEGER NOT NULL,
    completed_at INTEGER,
    result_code TEXT,
    UNIQUE(tool_call_id, ordinal)
);
"""
TOOL_ATTEMPTS_TABLE_SQL = V57_TOOL_ATTEMPTS_TABLE_SQL.replace(
    "('macos_seatbelt', 'none')", "('macos_seatbelt', 'windows_mxc', 'none')",
)
ONE_RUNNING_TOOL_ATTEMPT_INDEX_SQL = """
CREATE UNIQUE INDEX one_running_tool_attempt_per_tool_call
ON tool_attempts(tool_call_id)
WHERE status = 'running';
"""


def migrate_tool_attempt_sandbox(connection: sqlite3.Connection) -> None:
    try:
        connection.executescript(
            "BEGIN IMMEDIATE;\n"
            + TOOL_ATTEMPTS_TABLE_SQL.replace(
                "CREATE TABLE tool_attempts (", "CREATE TABLE tool_attempts_v58 (", 1,
            )
            + """
INSERT INTO tool_attempts_v58 SELECT * FROM tool_attempts;
UPDATE sqlite_sequence
SET seq = MAX(seq, COALESCE((SELECT seq FROM sqlite_sequence WHERE name = 'tool_attempts'), 0))
WHERE name = 'tool_attempts_v58';
INSERT INTO sqlite_sequence(name, seq)
SELECT 'tool_attempts_v58', seq FROM sqlite_sequence
WHERE name = 'tool_attempts'
  AND NOT EXISTS (SELECT 1 FROM sqlite_sequence WHERE name = 'tool_attempts_v58');
DROP TABLE tool_attempts;
ALTER TABLE tool_attempts_v58 RENAME TO tool_attempts;
"""
            + ONE_RUNNING_TOOL_ATTEMPT_INDEX_SQL
        )
        if connection.execute("PRAGMA foreign_key_check(tool_attempts)").fetchone() is not None:
            raise sqlite3.IntegrityError("tool attempts migration foreign key violation")
        connection.execute("PRAGMA user_version = 58")
        connection.commit()
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise

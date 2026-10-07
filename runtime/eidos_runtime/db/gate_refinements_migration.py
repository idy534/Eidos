"""Durable message delivery and actual Skill usage for schema 17."""
import sqlite3

GATE_REFINEMENTS_SCHEMA_SQL = """
ALTER TABLE agent_delegations ADD COLUMN required_for_completion INTEGER NOT NULL DEFAULT 1
    CHECK(required_for_completion IN (0, 1));
CREATE TABLE agent_message_receipts (
    message_id TEXT NOT NULL REFERENCES agent_messages(id) ON DELETE CASCADE,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    model_attempt_id TEXT NOT NULL REFERENCES model_attempts(id) ON DELETE CASCADE,
    delivered_at INTEGER NOT NULL,
    PRIMARY KEY(message_id, run_id)
);
CREATE TABLE run_skill_leases (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    qualified_id TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY(run_id, qualified_id)
);
CREATE INDEX run_skill_leases_skill ON run_skill_leases(qualified_id, run_id);
"""


def migrate_gate_refinements(connection: sqlite3.Connection) -> None:
    try:
        connection.executescript('BEGIN IMMEDIATE;\n' + GATE_REFINEMENTS_SCHEMA_SQL)
        if connection.execute('PRAGMA foreign_key_check').fetchone() is not None:
            raise sqlite3.IntegrityError('gate refinements foreign key violation')
        connection.execute('PRAGMA user_version = 17')
        connection.commit()
    except BaseException:
        connection.rollback()
        raise

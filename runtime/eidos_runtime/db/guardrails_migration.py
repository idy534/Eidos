from __future__ import annotations

import sqlite3

GUARDRAILS_SCHEMA_SQL = """
CREATE TABLE background_guardrail_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    revision INTEGER NOT NULL CHECK (revision >= 0),
    max_tokens_per_cycle INTEGER CHECK (max_tokens_per_cycle IS NULL OR max_tokens_per_cycle >= 1000),
    max_cost_credits_per_cycle REAL CHECK (max_cost_credits_per_cycle IS NULL OR max_cost_credits_per_cycle > 0),
    max_idle_rounds INTEGER CHECK (max_idle_rounds IS NULL OR max_idle_rounds >= 1)
);
CREATE TABLE automation_guardrail_state (
    automation_id TEXT PRIMARY KEY REFERENCES scheduled_tasks(id) ON DELETE CASCADE,
    cycle INTEGER NOT NULL DEFAULT 1 CHECK (cycle >= 1),
    consecutive_idle_runs INTEGER NOT NULL DEFAULT 0 CHECK (consecutive_idle_runs >= 0),
    cycle_after_run_seq INTEGER NOT NULL DEFAULT 0 CHECK (cycle_after_run_seq >= 0),
    cycle_started_at INTEGER NOT NULL,
    progress_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(progress_json))
);
"""


def migrate_guardrails(connection: sqlite3.Connection) -> None:
    try:
        connection.executescript(
            "BEGIN IMMEDIATE;\n"
            + GUARDRAILS_SCHEMA_SQL
            + "PRAGMA user_version=57;\nCOMMIT;"
        )
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise

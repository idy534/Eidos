from __future__ import annotations

import sqlite3


HOOKS_SCHEMA_SQL = """
CREATE TABLE plugin_hook_trust (
    plugin_id TEXT PRIMARY KEY REFERENCES plugins(id),
    content_hash TEXT NOT NULL,
    trusted_at INTEGER NOT NULL
);
CREATE TABLE hook_invocations (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    plugin_id TEXT NOT NULL,
    plugin_hash TEXT NOT NULL,
    hook_key TEXT NOT NULL,
    event TEXT NOT NULL,
    event_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('intent', 'running', 'completed', 'unknown')),
    input_json TEXT NOT NULL,
    permissions_json TEXT,
    result_json TEXT,
    output_json TEXT,
    completion_event_id INTEGER,
    created_at INTEGER NOT NULL,
    completed_at INTEGER,
    UNIQUE(run_id, plugin_id, hook_key, event_key)
);
CREATE INDEX hook_invocations_plugin ON hook_invocations(plugin_id, created_at DESC);
CREATE INDEX hook_invocations_run ON hook_invocations(run_id, event);
CREATE TABLE hook_pending_events (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    event_key TEXT NOT NULL,
    fields_json TEXT NOT NULL,
    PRIMARY KEY(run_id,event_key)
);
"""


def migrate_hooks(connection: sqlite3.Connection) -> None:
    try:
        connection.executescript("BEGIN IMMEDIATE;\n" + HOOKS_SCHEMA_SQL + "PRAGMA user_version=36;\nCOMMIT;")
    except BaseException:
        if connection.in_transaction:
            connection.rollback()
        raise

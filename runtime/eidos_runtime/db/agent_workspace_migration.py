"""Schema v55: isolated child workspaces, their deliveries and integrations.

A child agent may own a managed Worktree. Its base and each delivery are Git
trees pinned by Runtime refs; SQLite keeps the per-file entries and the
integration plan so a restarted Runtime reports every file outcome truthfully.
User messages to a child carry their own operation id for idempotent retries,
and the parent sees a preview of the latest one.
"""

from __future__ import annotations

import sqlite3

AGENT_WORKSPACE_SCHEMA_SQL = """
ALTER TABLE agent_delegations ADD COLUMN workspace_mode TEXT NOT NULL DEFAULT 'shared'
    CHECK (workspace_mode IN ('shared', 'worktree'));
ALTER TABLE agent_delegations ADD COLUMN worktree_id TEXT;
ALTER TABLE agent_delegations ADD COLUMN base_tree TEXT;
ALTER TABLE agent_delegations ADD COLUMN last_user_instruction TEXT;
ALTER TABLE agent_messages ADD COLUMN sender_kind TEXT NOT NULL DEFAULT 'agent'
    CHECK (sender_kind IN ('agent', 'user'));
ALTER TABLE agent_messages ADD COLUMN operation_id TEXT;
CREATE UNIQUE INDEX agent_messages_operation
ON agent_messages(operation_id) WHERE operation_id IS NOT NULL;

CREATE TABLE workspace_deliveries (
    id TEXT PRIMARY KEY,
    owner_kind TEXT NOT NULL CHECK (owner_kind IN ('agent', 'team_member')),
    owner_id TEXT NOT NULL,
    worktree_id TEXT NOT NULL,
    base_tree TEXT NOT NULL,
    result_tree TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    entries_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('ready', 'stale', 'integrated', 'partially_integrated', 'discarded')
    ),
    created_at INTEGER NOT NULL,
    integrated_at INTEGER
);
CREATE INDEX workspace_deliveries_owner
ON workspace_deliveries(owner_kind, owner_id, created_at DESC, id DESC);

CREATE TABLE workspace_integrations (
    id TEXT PRIMARY KEY,
    delivery_id TEXT NOT NULL REFERENCES workspace_deliveries(id) ON DELETE CASCADE,
    target_session_id TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('tool', 'desktop')),
    request_json TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('applying', 'completed')),
    file_results_json TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    completed_at INTEGER
);
CREATE INDEX workspace_integrations_delivery
ON workspace_integrations(delivery_id, created_at);
CREATE INDEX workspace_integrations_unfinished
ON workspace_integrations(state) WHERE state = 'applying';
"""


def migrate_agent_workspaces(connection: sqlite3.Connection) -> None:
    try:
        connection.executescript(
            "BEGIN IMMEDIATE;\n" + AGENT_WORKSPACE_SCHEMA_SQL
            + "\nPRAGMA user_version=55;\nCOMMIT;"
        )
    except sqlite3.Error:
        connection.rollback()
        raise

"""Schema v56: Agent Team coordination state.

A team belongs to the lead Session. Members are internal Sessions whose Runs
use the ordinary Runtime. Tasks, messages, deliveries and wake intents are
coordination facts only; execution facts stay in runs, items and tool calls.
``sessions.internal_kind`` marks every Session that Runtime owns for another
Session, so list, search and admission rules have one predicate. The unused
v55 ``workspace_integrations`` table is dropped: integration runs only as the
parent's file-change tool, whose ToolCall records already carry recovery.
"""

from __future__ import annotations

import sqlite3

TEAM_SCHEMA_SQL = """
DROP INDEX IF EXISTS workspace_integrations_unfinished;
DROP INDEX IF EXISTS workspace_integrations_delivery;
DROP TABLE IF EXISTS workspace_integrations;
ALTER TABLE sessions ADD COLUMN internal_kind TEXT
    CHECK (internal_kind IN ('agent_child', 'team_member'));
UPDATE sessions SET internal_kind = 'agent_child'
WHERE id IN (SELECT child_session_id FROM agent_delegations);
CREATE INDEX sessions_internal_kind ON sessions(internal_kind) WHERE internal_kind IS NOT NULL;

CREATE TABLE teams (
    id TEXT PRIMARY KEY,
    lead_session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('active', 'paused', 'dissolving', 'dissolved')),
    objective TEXT NOT NULL,
    run_settings_json TEXT NOT NULL,
    auto_wake_lead INTEGER NOT NULL DEFAULT 1 CHECK (auto_wake_lead IN (0, 1)),
    template_json TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE UNIQUE INDEX teams_one_open ON teams(lead_session_id) WHERE status != 'dissolved';

CREATE TABLE team_proposals (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    item_id TEXT NOT NULL UNIQUE,
    proposal_json TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('pending', 'confirmed', 'declined')),
    decision_json TEXT,
    operation_id TEXT UNIQUE,
    team_id TEXT REFERENCES teams(id) ON DELETE SET NULL,
    created_at INTEGER NOT NULL,
    decided_at INTEGER
);
CREATE INDEX team_proposals_session ON team_proposals(session_id, decision);

CREATE TABLE team_members (
    id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    brief TEXT NOT NULL,
    definition_json TEXT,
    model_json TEXT NOT NULL,
    session_id TEXT NOT NULL UNIQUE REFERENCES sessions(id) ON DELETE RESTRICT,
    workspace_mode TEXT NOT NULL CHECK (workspace_mode IN ('shared', 'worktree')),
    worktree_id TEXT,
    base_tree TEXT,
    status TEXT NOT NULL CHECK (status IN ('active', 'removed')),
    forked_context TEXT,
    created_at INTEGER NOT NULL,
    removed_at INTEGER,
    UNIQUE (team_id, name)
);

CREATE TABLE team_tasks (
    id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    key TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'work' CHECK (kind IN ('work', 'gate')),
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    acceptance TEXT NOT NULL DEFAULT '',
    owner_member_id TEXT REFERENCES team_members(id) ON DELETE SET NULL,
    reviewer TEXT,
    status TEXT NOT NULL CHECK (status IN ('open', 'in_progress', 'in_review', 'done', 'canceled')),
    review_required INTEGER NOT NULL DEFAULT 0 CHECK (review_required IN (0, 1)),
    milestone TEXT,
    paths_json TEXT NOT NULL DEFAULT '[]',
    evidence_json TEXT NOT NULL DEFAULT '[]',
    delivery_id TEXT,
    note TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    created_by TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE (team_id, key)
);
CREATE INDEX team_tasks_status ON team_tasks(team_id, status);

CREATE TABLE team_task_deps (
    task_id TEXT NOT NULL REFERENCES team_tasks(id) ON DELETE CASCADE,
    depends_on_task_id TEXT NOT NULL REFERENCES team_tasks(id) ON DELETE CASCADE,
    PRIMARY KEY (task_id, depends_on_task_id)
);

CREATE TABLE team_messages (
    id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    channel TEXT NOT NULL CHECK (channel IN ('board', 'direct')),
    author_kind TEXT NOT NULL CHECK (author_kind IN ('lead', 'member', 'user', 'system')),
    author_member_id TEXT,
    to_all INTEGER NOT NULL DEFAULT 0 CHECK (to_all IN (0, 1)),
    recipients_json TEXT NOT NULL,
    task_id TEXT,
    content TEXT NOT NULL,
    source_item_id TEXT UNIQUE,
    operation_id TEXT UNIQUE,
    created_at INTEGER NOT NULL,
    UNIQUE (team_id, seq)
);

CREATE TABLE team_message_deliveries (
    message_id TEXT NOT NULL REFERENCES team_messages(id) ON DELETE CASCADE,
    recipient TEXT NOT NULL,
    model_attempt_id TEXT,
    delivered_at INTEGER,
    PRIMARY KEY (message_id, recipient)
);
CREATE INDEX team_message_deliveries_pending ON team_message_deliveries(recipient, delivered_at);

CREATE TABLE team_wakes (
    id TEXT PRIMARY KEY,
    team_id TEXT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    target TEXT NOT NULL,
    reasons_json TEXT NOT NULL,
    user_message INTEGER NOT NULL DEFAULT 0 CHECK (user_message IN (0, 1)),
    status TEXT NOT NULL CHECK (status IN ('pending', 'accepted', 'obsolete')),
    run_id TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE UNIQUE INDEX team_wakes_one_pending ON team_wakes(team_id, target) WHERE status = 'pending';

CREATE TABLE team_runs (
    run_id TEXT PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
    team_id TEXT REFERENCES teams(id) ON DELETE CASCADE,
    member_id TEXT,
    wake_id TEXT,
    origin TEXT NOT NULL CHECK (origin IN ('lead', 'wake', 'user_message'))
);
CREATE INDEX team_runs_team ON team_runs(team_id, member_id);
"""


def migrate_teams(connection: sqlite3.Connection) -> None:
    try:
        connection.executescript(
            "BEGIN IMMEDIATE;\n" + TEAM_SCHEMA_SQL + "\nPRAGMA user_version=56;\nCOMMIT;"
        )
    except sqlite3.Error:
        connection.rollback()
        raise

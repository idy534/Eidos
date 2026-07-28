from __future__ import annotations


SCHEMA_VERSION = 5

SCHEMA_SQL = """
CREATE TABLE sessions (
    creation_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    workspace_root TEXT NOT NULL,
    workspace_dev INTEGER,
    workspace_inode INTEGER,
    workspace_uid INTEGER,
    title TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE runs (
    creation_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE RESTRICT,
    user_input TEXT NOT NULL,
    model_id TEXT NOT NULL DEFAULT 'deepseek-v4-flash',
    model_profile_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'queued', 'running', 'waiting_approval', 'waiting_user_input',
        'finalizing', 'succeeded', 'failed', 'stopped', 'canceled',
        'interrupted'
    )),
    model_step_count INTEGER NOT NULL DEFAULT 0,
    consecutive_protocol_errors INTEGER NOT NULL DEFAULT 0,
    consecutive_rejects INTEGER NOT NULL DEFAULT 0,
    consecutive_sensitive_tool_inputs INTEGER NOT NULL DEFAULT 0,
    enqueued_at INTEGER,
    total_effective_ms INTEGER NOT NULL DEFAULT 0,
    pause_reason TEXT,
    stop_reason TEXT,
    reconciliation_required INTEGER NOT NULL DEFAULT 0,
    reconciliation_epoch INTEGER NOT NULL DEFAULT 0,
    side_effects_may_exist INTEGER NOT NULL DEFAULT 0,
    extension_snapshot_json TEXT NOT NULL DEFAULT '{}',
    activated_tools_json TEXT NOT NULL DEFAULT '[]',
    compaction_count INTEGER NOT NULL DEFAULT 0,
    workspace_version INTEGER NOT NULL DEFAULT 0,
    last_diff_hash TEXT,
    error_code TEXT,
    cancel_requested_at INTEGER,
    cancel_completed_at INTEGER,
    cancel_failure_code TEXT,
    created_at INTEGER NOT NULL,
    started_at INTEGER,
    updated_at INTEGER NOT NULL,
    completed_at INTEGER
);

CREATE UNIQUE INDEX one_active_run
ON runs ((1))
WHERE status IN ('running', 'finalizing');

CREATE TABLE items (
    creation_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE RESTRICT,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
    ordinal INTEGER NOT NULL,
    model_step_index INTEGER,
    kind TEXT NOT NULL CHECK (kind IN (
        'user_message', 'assistant_message', 'file_change',
        'command_execution', 'tool_call'
    )),
    status TEXT NOT NULL CHECK (status IN (
        'in_progress', 'completed', 'failed', 'declined', 'canceled'
    )),
    content TEXT,
    incomplete INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    completed_at INTEGER,
    UNIQUE(run_id, ordinal)
);

CREATE TABLE tool_calls (
    creation_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    item_id TEXT NOT NULL UNIQUE REFERENCES items(id) ON DELETE RESTRICT,
    model_step_index INTEGER NOT NULL,
    batch_order INTEGER NOT NULL,
    provider_call_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'running', 'completed', 'failed', 'canceled'
    )),
    arguments_json TEXT NOT NULL,
    result_json TEXT,
    model_result_json TEXT,
    approval_status TEXT,
    approval_decision TEXT,
    approval_feedback TEXT,
    approval_diff TEXT,
    base_sha256 TEXT,
    provenance_json TEXT,
    tool_set_hash TEXT,
    started_at INTEGER NOT NULL,
    duration_ms INTEGER,
    completed_at INTEGER
);

CREATE TABLE approvals (
    creation_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    tool_call_id TEXT NOT NULL REFERENCES tool_calls(id) ON DELETE RESTRICT,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
    item_id TEXT NOT NULL REFERENCES items(id) ON DELETE RESTRICT,
    status TEXT NOT NULL CHECK (status IN (
        'pending', 'approved', 'rejected', 'invalidated', 'canceled'
    )),
    request_hash TEXT NOT NULL,
    decision TEXT,
    feedback TEXT,
    created_at INTEGER NOT NULL,
    decided_at INTEGER
);

CREATE UNIQUE INDEX one_pending_approval_per_item
ON approvals (item_id)
WHERE status = 'pending';

CREATE UNIQUE INDEX one_pending_approval_per_run
ON approvals (run_id)
WHERE status = 'pending';

CREATE TABLE execution_segments (
    creation_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
    ordinal INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'queued', 'running', 'waiting_user_input',
        'completed', 'failed', 'canceled'
    )),
    step_count INTEGER NOT NULL DEFAULT 0,
    effective_ms INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    started_at INTEGER,
    completed_at INTEGER,
    UNIQUE(run_id, ordinal)
);

CREATE TABLE steps (
    creation_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
    segment_id TEXT NOT NULL REFERENCES execution_segments(id) ON DELETE RESTRICT,
    ordinal INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'running', 'completed', 'failed', 'canceled'
    )),
    observed_reconciliation_epoch INTEGER NOT NULL DEFAULT 0,
    tool_snapshot_json TEXT,
    tool_set_hash TEXT,
    progress_signature_json TEXT,
    created_at INTEGER NOT NULL,
    completed_at INTEGER,
    UNIQUE(segment_id, ordinal)
);

CREATE UNIQUE INDEX one_running_segment_per_run
ON execution_segments(run_id)
WHERE status = 'running';

CREATE UNIQUE INDEX one_active_segment_per_run
ON execution_segments(run_id)
WHERE status IN ('queued', 'running', 'waiting_user_input');

CREATE UNIQUE INDEX one_running_step_per_run
ON steps(run_id)
WHERE status = 'running';

CREATE TABLE model_attempts (
    creation_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    step_id TEXT NOT NULL REFERENCES steps(id) ON DELETE RESTRICT,
    ordinal INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'running', 'completed', 'failed', 'canceled'
    )),
    provider_name TEXT,
    resolved_model_name TEXT,
    finish_reason TEXT,
    provider_response_id TEXT,
    usage_json TEXT,
    error_code TEXT,
    http_status INTEGER,
    ttft_ms INTEGER,
    duration_ms INTEGER,
    had_progress INTEGER NOT NULL DEFAULT 0,
    started_at INTEGER NOT NULL,
    completed_at INTEGER,
    UNIQUE(step_id, ordinal)
);

CREATE UNIQUE INDEX one_running_attempt_per_step
ON model_attempts(step_id)
WHERE status = 'running';

CREATE TABLE finalization_attempts (
    creation_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
    step_id TEXT REFERENCES steps(id) ON DELETE RESTRICT,
    status TEXT NOT NULL CHECK (status IN (
        'running', 'completed', 'timed_out', 'model_failed',
        'sensitive_rejected', 'canceled', 'interrupted'
    )),
    started_at INTEGER NOT NULL,
    completed_at INTEGER,
    error_code TEXT,
    error_message TEXT,
    model_id TEXT NOT NULL,
    output_item_id TEXT REFERENCES items(id) ON DELETE RESTRICT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE UNIQUE INDEX one_running_finalization_attempt_per_run
ON finalization_attempts(run_id)
WHERE status = 'running';

CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_contract_version INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    occurred_at INTEGER NOT NULL,
    session_id TEXT,
    run_id TEXT,
    payload_json TEXT NOT NULL
);

CREATE TABLE event_outbox (
    event_id INTEGER PRIMARY KEY REFERENCES events(id) ON DELETE RESTRICT,
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'delivered', 'failed')
    ),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_error_code TEXT,
    delivered_at INTEGER
);

CREATE UNIQUE INDEX one_pending_outbox_delivery_per_event
ON event_outbox(event_id)
WHERE status = 'pending';

CREATE TABLE operations (
    id TEXT NOT NULL,
    scope TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    result_json TEXT,
    created_at INTEGER NOT NULL,
    completed_at INTEGER,
    PRIMARY KEY(id, scope)
);

CREATE TABLE durable_intents (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
    tool_call_id TEXT NOT NULL REFERENCES tool_calls(id) ON DELETE RESTRICT,
    execution_nonce TEXT NOT NULL UNIQUE,
    arguments_hash TEXT NOT NULL,
    preconditions_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'running', 'completed', 'uncertain', 'interrupted'
    )),
    created_at INTEGER NOT NULL,
    reconciled_at INTEGER
);

CREATE TABLE plugins (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    description TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL CHECK (status IN ('installed', 'removed')),
    installed_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE mcp_server_states (
    plugin_id TEXT NOT NULL,
    server_id TEXT NOT NULL,
    consented INTEGER NOT NULL DEFAULT 0,
    error_code TEXT,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY(plugin_id, server_id)
);

CREATE TABLE compact_summaries (
    creation_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE RESTRICT,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
    task_goal TEXT NOT NULL,
    constraints_json TEXT NOT NULL,
    completed_actions_json TEXT NOT NULL,
    workspace_changes_json TEXT NOT NULL,
    important_facts_json TEXT NOT NULL,
    unresolved_problems_json TEXT NOT NULL,
    next_actions_json TEXT NOT NULL,
    source_item_ids_json TEXT NOT NULL,
    phase TEXT NOT NULL CHECK (phase IN ('pre_turn', 'mid_turn')),
    created_at INTEGER NOT NULL
);

CREATE TABLE input_mailbox (
    creation_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
    content TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'injected', 'canceled')),
    created_at INTEGER NOT NULL,
    injected_at INTEGER
);

CREATE TABLE IF NOT EXISTS async_operations (
    id TEXT PRIMARY KEY,
    request_id TEXT,
    operation_id TEXT NOT NULL,
    scope TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'accepted', 'running', 'completed', 'failed',
        'canceled', 'interrupted'
    )),
    result_json TEXT,
    error_code TEXT,
    created_at INTEGER NOT NULL,
    started_at INTEGER,
    completed_at INTEGER
);

CREATE UNIQUE INDEX IF NOT EXISTS one_running_async_operation_per_operation_id
ON async_operations(scope, operation_id)
WHERE status IN ('accepted', 'running');
"""

SCHEMA_V1_TO_V2_SQL = """
ALTER TABLE tool_calls ADD COLUMN duration_ms INTEGER;

CREATE TABLE finalization_attempts (
    creation_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
    step_id TEXT REFERENCES steps(id) ON DELETE RESTRICT,
    status TEXT NOT NULL CHECK (status IN (
        'running', 'completed', 'timed_out', 'model_failed',
        'sensitive_rejected', 'canceled', 'interrupted'
    )),
    started_at INTEGER NOT NULL,
    completed_at INTEGER,
    error_code TEXT,
    error_message TEXT,
    model_id TEXT NOT NULL,
    output_item_id TEXT REFERENCES items(id) ON DELETE RESTRICT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE UNIQUE INDEX one_running_finalization_attempt_per_run
ON finalization_attempts(run_id)
WHERE status = 'running';
"""

SCHEMA_V2_TO_V3_SQL = """
CREATE TABLE IF NOT EXISTS async_operations (
    id TEXT PRIMARY KEY,
    request_id TEXT,
    operation_id TEXT NOT NULL,
    scope TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'accepted', 'running', 'completed', 'failed',
        'canceled', 'interrupted'
    )),
    result_json TEXT,
    error_code TEXT,
    created_at INTEGER NOT NULL,
    started_at INTEGER,
    completed_at INTEGER
);

CREATE UNIQUE INDEX IF NOT EXISTS one_running_async_operation_per_operation_id
ON async_operations(scope, operation_id)
WHERE status IN ('accepted', 'running');

CREATE TRIGGER approvals_status_check_insert
BEFORE INSERT ON approvals
WHEN NEW.status NOT IN (
    'pending', 'approved', 'rejected', 'invalidated', 'canceled'
)
BEGIN SELECT RAISE(ABORT, 'invalid approvals status'); END;

CREATE TRIGGER approvals_status_check_update
BEFORE UPDATE OF status ON approvals
WHEN NEW.status NOT IN (
    'pending', 'approved', 'rejected', 'invalidated', 'canceled'
)
BEGIN SELECT RAISE(ABORT, 'invalid approvals status'); END;

CREATE TRIGGER execution_segments_status_check_insert
BEFORE INSERT ON execution_segments
WHEN NEW.status NOT IN (
    'queued', 'running', 'waiting_user_input',
    'completed', 'failed', 'canceled'
)
BEGIN SELECT RAISE(ABORT, 'invalid execution_segments status'); END;

CREATE TRIGGER execution_segments_status_check_update
BEFORE UPDATE OF status ON execution_segments
WHEN NEW.status NOT IN (
    'queued', 'running', 'waiting_user_input',
    'completed', 'failed', 'canceled'
)
BEGIN SELECT RAISE(ABORT, 'invalid execution_segments status'); END;

CREATE TRIGGER steps_status_check_insert
BEFORE INSERT ON steps
WHEN NEW.status NOT IN ('running', 'completed', 'failed', 'canceled')
BEGIN SELECT RAISE(ABORT, 'invalid steps status'); END;

CREATE TRIGGER steps_status_check_update
BEFORE UPDATE OF status ON steps
WHEN NEW.status NOT IN ('running', 'completed', 'failed', 'canceled')
BEGIN SELECT RAISE(ABORT, 'invalid steps status'); END;

CREATE TRIGGER model_attempts_status_check_insert
BEFORE INSERT ON model_attempts
WHEN NEW.status NOT IN ('running', 'completed', 'failed', 'canceled')
BEGIN SELECT RAISE(ABORT, 'invalid model_attempts status'); END;

CREATE TRIGGER model_attempts_status_check_update
BEFORE UPDATE OF status ON model_attempts
WHEN NEW.status NOT IN ('running', 'completed', 'failed', 'canceled')
BEGIN SELECT RAISE(ABORT, 'invalid model_attempts status'); END;

CREATE TRIGGER durable_intents_status_check_insert
BEFORE INSERT ON durable_intents
WHEN NEW.status NOT IN ('running', 'completed', 'uncertain', 'interrupted')
BEGIN SELECT RAISE(ABORT, 'invalid durable_intents status'); END;

CREATE TRIGGER durable_intents_status_check_update
BEFORE UPDATE OF status ON durable_intents
WHEN NEW.status NOT IN ('running', 'completed', 'uncertain', 'interrupted')
BEGIN SELECT RAISE(ABORT, 'invalid durable_intents status'); END;
"""

SCHEMA_V3_TO_V4_SQL = """
CREATE TABLE IF NOT EXISTS event_outbox (
    event_id INTEGER PRIMARY KEY REFERENCES events(id) ON DELETE RESTRICT,
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'delivered', 'failed')
    ),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_error_code TEXT,
    delivered_at INTEGER
);

CREATE UNIQUE INDEX IF NOT EXISTS one_pending_outbox_delivery_per_event
ON event_outbox(event_id)
WHERE status = 'pending';

INSERT OR IGNORE INTO event_outbox (
    event_id, status, attempt_count, delivered_at
)
SELECT id, 'delivered', 0, occurred_at FROM events;
"""

SCHEMA_V4_TO_V5_SQL = """
ALTER TABLE tool_calls ADD COLUMN model_result_json TEXT;
"""

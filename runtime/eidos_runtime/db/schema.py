from __future__ import annotations


SCHEMA_VERSION = 6
PREVIOUS_SCHEMA_VERSION = 5
LEGACY_SCHEMA_VERSION = 1

MODEL_ATTEMPT_DIAGNOSTICS_COLUMNS = (
    "    configured_provider_id TEXT,\n"
    "    response_state TEXT,\n"
    "    phase TEXT,\n"
    "    tool_call_count INTEGER NOT NULL DEFAULT 0,\n"
    "    response_text_sha256 TEXT,\n"
    "    response_text_bytes INTEGER NOT NULL DEFAULT 0,\n"
    "    protocol_diagnostics_json TEXT,\n"
)

_RAW_BASE_SCHEMA_SQL = """
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
    model_profile_id TEXT,
    model_profile_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'queued', 'running', 'waiting_approval', 'finalizing',
        'succeeded', 'failed', 'stopped', 'canceled', 'interrupted'
    )),
    model_step_count INTEGER NOT NULL DEFAULT 0,
    consecutive_protocol_errors INTEGER NOT NULL DEFAULT 0,
    consecutive_rejects INTEGER NOT NULL DEFAULT 0,
    consecutive_sensitive_tool_inputs INTEGER NOT NULL DEFAULT 0,
    enqueued_at INTEGER,
    total_effective_ms INTEGER NOT NULL DEFAULT 0,
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

CREATE TABLE model_profiles (
    creation_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL UNIQUE,
    profile_json TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE model_capability_snapshots (
    creation_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    profile_id TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    probed_at INTEGER NOT NULL
);

CREATE TABLE run_model_snapshots (
    run_id TEXT PRIMARY KEY REFERENCES runs(id) ON DELETE RESTRICT,
    profile_id TEXT NOT NULL,
    capability_snapshot_id TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    frozen_at INTEGER NOT NULL
);

CREATE TABLE run_resolution_snapshots (
    creation_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    run_id TEXT NOT NULL UNIQUE REFERENCES runs(id) ON DELETE RESTRICT,
    snapshot_hash TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE TABLE rule_resolution_snapshots (
    creation_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    snapshot_hash TEXT NOT NULL UNIQUE,
    snapshot_json TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

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
    ui_result_json TEXT,
    progress_fingerprint TEXT,
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
    request_json TEXT NOT NULL,
    attempt_ordinal INTEGER NOT NULL DEFAULT 0 CHECK (
        attempt_ordinal IN (0, 1)
    ),
    approval_kind TEXT NOT NULL DEFAULT 'tool' CHECK (
        approval_kind IN (
            'tool', 'default', 'additional_permissions', 'escalated'
        )
    ),
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

CREATE UNIQUE INDEX one_running_tool_attempt_per_tool_call
ON tool_attempts(tool_call_id)
WHERE status = 'running';

CREATE TABLE execution_segments (
    creation_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
    ordinal INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'queued', 'running', 'completed', 'failed', 'canceled'
    )),
    step_count INTEGER NOT NULL DEFAULT 0,
    effective_ms INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    started_at INTEGER,
    completed_at INTEGER,
    UNIQUE(run_id, ordinal)
);

CREATE TABLE step_resolution_snapshots (
    creation_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    run_snapshot_id TEXT NOT NULL
        REFERENCES run_resolution_snapshots(id) ON DELETE RESTRICT,
    rule_snapshot_id TEXT NOT NULL
        REFERENCES rule_resolution_snapshots(id) ON DELETE RESTRICT,
    snapshot_hash TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    created_at INTEGER NOT NULL
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
    resolution_snapshot_id TEXT NOT NULL
        REFERENCES step_resolution_snapshots(id) ON DELETE RESTRICT,
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
WHERE status IN ('queued', 'running');

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
    lease_id TEXT,
    wire_api TEXT,
    model_id TEXT,
    request_timeout REAL,
    retry_decision_json TEXT,
    usage_json TEXT,
    error_code TEXT,
    http_status INTEGER,
    ttft_ms INTEGER,
    duration_ms INTEGER,
    had_progress INTEGER NOT NULL DEFAULT 0,
    configured_provider_id TEXT,
    response_state TEXT,
    phase TEXT,
    tool_call_count INTEGER NOT NULL DEFAULT 0,
    response_text_sha256 TEXT,
    response_text_bytes INTEGER NOT NULL DEFAULT 0,
    protocol_diagnostics_json TEXT,
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

REPOSITORY_SCHEMA_SQL = """
CREATE TABLE repository_snapshots (
    creation_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    repository_id TEXT NOT NULL,
    workspace_root TEXT NOT NULL,
    workspace_dev INTEGER NOT NULL,
    workspace_inode INTEGER NOT NULL,
    workspace_uid INTEGER NOT NULL,
    inventory_generation INTEGER NOT NULL CHECK (inventory_generation >= 0),
    index_generation INTEGER,
    inventory_snapshot_id TEXT NOT NULL,
    inventory_snapshot_hash TEXT NOT NULL,
    index_snapshot_id TEXT,
    index_snapshot_hash TEXT,
    repository_map_json TEXT,
    grammar_versions_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('complete', 'incomplete')),
    complete INTEGER NOT NULL CHECK (complete IN (0, 1)),
    created_at INTEGER NOT NULL,
    CHECK (
        (complete = 1 AND status = 'complete'
         AND index_generation IS NOT NULL
         AND index_snapshot_id IS NOT NULL
         AND index_snapshot_hash IS NOT NULL
         AND repository_map_json IS NOT NULL)
        OR (complete = 0 AND status = 'incomplete')
    )
);

CREATE INDEX repository_snapshots_last_complete
ON repository_snapshots (
    repository_id,
    workspace_root,
    workspace_dev,
    workspace_inode,
    workspace_uid,
    complete,
    creation_seq DESC
);

CREATE TABLE repository_files (
    repository_snapshot_id TEXT NOT NULL
        REFERENCES repository_snapshots(id) ON DELETE RESTRICT,
    path TEXT NOT NULL,
    file_type TEXT NOT NULL,
    language TEXT,
    size_bytes INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    ctime_ns INTEGER,
    device INTEGER,
    inode INTEGER,
    content_hash TEXT,
    encoding TEXT NOT NULL,
    generated INTEGER NOT NULL CHECK (generated IN (0, 1)),
    vendor INTEGER NOT NULL CHECK (vendor IN (0, 1)),
    ignored INTEGER NOT NULL CHECK (ignored IN (0, 1)),
    git_status TEXT NOT NULL,
    generation INTEGER NOT NULL CHECK (generation >= 0),
    verification_state TEXT NOT NULL,
    PRIMARY KEY(repository_snapshot_id, path)
);

CREATE TABLE repository_directories (
    repository_snapshot_id TEXT NOT NULL
        REFERENCES repository_snapshots(id) ON DELETE RESTRICT,
    path TEXT NOT NULL,
    device INTEGER,
    inode INTEGER,
    ignored INTEGER NOT NULL CHECK (ignored IN (0, 1)),
    generation INTEGER NOT NULL CHECK (generation >= 0),
    PRIMARY KEY(repository_snapshot_id, path)
);

CREATE TABLE repository_index_generations (
    id TEXT PRIMARY KEY,
    repository_snapshot_id TEXT NOT NULL
        REFERENCES repository_snapshots(id) ON DELETE RESTRICT,
    repository_id TEXT NOT NULL,
    inventory_snapshot_id TEXT NOT NULL,
    inventory_generation INTEGER NOT NULL CHECK (inventory_generation >= 0),
    index_generation INTEGER NOT NULL CHECK (index_generation >= 0),
    snapshot_hash TEXT NOT NULL,
    parser_versions_json TEXT NOT NULL,
    complete INTEGER NOT NULL CHECK (complete IN (0, 1)),
    created_at INTEGER NOT NULL,
    UNIQUE(repository_snapshot_id, index_generation)
);

CREATE INDEX repository_index_generations_snapshot
ON repository_index_generations(repository_snapshot_id, complete, index_generation DESC);

CREATE TABLE repository_parsed_files (
    repository_index_generation_id TEXT NOT NULL
        REFERENCES repository_index_generations(id) ON DELETE RESTRICT,
    path TEXT NOT NULL,
    file_content_hash TEXT NOT NULL,
    inventory_generation INTEGER NOT NULL CHECK (inventory_generation >= 0),
    index_generation INTEGER NOT NULL CHECK (index_generation >= 0),
    language TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    byte_length INTEGER NOT NULL CHECK (byte_length >= 0),
    has_errors INTEGER NOT NULL CHECK (has_errors IN (0, 1)),
    PRIMARY KEY(repository_index_generation_id, path)
);

CREATE TABLE repository_symbols (
    repository_index_generation_id TEXT NOT NULL
        REFERENCES repository_index_generations(id) ON DELETE RESTRICT,
    id TEXT NOT NULL,
    path TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    scope TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    start_column INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    end_column INTEGER NOT NULL,
    file_content_hash TEXT NOT NULL,
    inventory_generation INTEGER NOT NULL CHECK (inventory_generation >= 0),
    index_generation INTEGER NOT NULL CHECK (index_generation >= 0),
    PRIMARY KEY(repository_index_generation_id, id)
);

CREATE INDEX repository_symbols_name
ON repository_symbols(repository_index_generation_id, name, path, start_line);

CREATE TABLE repository_imports (
    repository_index_generation_id TEXT NOT NULL
        REFERENCES repository_index_generations(id) ON DELETE RESTRICT,
    id TEXT NOT NULL,
    path TEXT NOT NULL,
    imported_name TEXT NOT NULL,
    source TEXT,
    start_line INTEGER NOT NULL,
    file_content_hash TEXT NOT NULL,
    inventory_generation INTEGER NOT NULL CHECK (inventory_generation >= 0),
    index_generation INTEGER NOT NULL CHECK (index_generation >= 0),
    PRIMARY KEY(repository_index_generation_id, id)
);

CREATE TABLE repository_references (
    repository_index_generation_id TEXT NOT NULL
        REFERENCES repository_index_generations(id) ON DELETE RESTRICT,
    id TEXT NOT NULL,
    path TEXT NOT NULL,
    name TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    start_column INTEGER NOT NULL,
    file_content_hash TEXT NOT NULL,
    inventory_generation INTEGER NOT NULL CHECK (inventory_generation >= 0),
    index_generation INTEGER NOT NULL CHECK (index_generation >= 0),
    PRIMARY KEY(repository_index_generation_id, id)
);

CREATE TABLE repository_chunks (
    repository_index_generation_id TEXT NOT NULL
        REFERENCES repository_index_generations(id) ON DELETE RESTRICT,
    id TEXT NOT NULL,
    path TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    byte_start INTEGER NOT NULL,
    byte_end INTEGER NOT NULL,
    text TEXT NOT NULL,
    file_content_hash TEXT NOT NULL,
    inventory_generation INTEGER NOT NULL CHECK (inventory_generation >= 0),
    index_generation INTEGER NOT NULL CHECK (index_generation >= 0),
    PRIMARY KEY(repository_index_generation_id, id)
);

CREATE TABLE repository_diagnostics (
    creation_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    repository_snapshot_id TEXT NOT NULL
        REFERENCES repository_snapshots(id) ON DELETE RESTRICT,
    repository_index_generation_id TEXT
        REFERENCES repository_index_generations(id) ON DELETE RESTRICT,
    source TEXT NOT NULL CHECK (source IN ('inventory', 'index')),
    path TEXT NOT NULL,
    code TEXT NOT NULL,
    message TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    inventory_generation INTEGER NOT NULL CHECK (inventory_generation >= 0),
    index_generation INTEGER NOT NULL CHECK (index_generation >= 0),
    CHECK (
        (source = 'inventory' AND repository_index_generation_id IS NULL)
        OR (source = 'index' AND repository_index_generation_id IS NOT NULL)
    )
);

CREATE INDEX repository_diagnostics_generation
ON repository_diagnostics(repository_snapshot_id, repository_index_generation_id, creation_seq);

CREATE VIRTUAL TABLE repository_fts USING fts5(
    index_snapshot_id UNINDEXED,
    record_id UNINDEXED,
    path,
    symbol,
    body,
    kind UNINDEXED,
    start_line UNINDEXED,
    end_line UNINDEXED,
    file_hash UNINDEXED
);
"""

CONTEXT_SCHEMA_SQL = """
CREATE TABLE repository_retrieval_snapshots (
    id TEXT PRIMARY KEY,
    inventory_snapshot_id TEXT NOT NULL,
    index_snapshot_id TEXT NOT NULL,
    snapshot_hash TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE TABLE run_repository_retrievals (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
    retrieval_snapshot_id TEXT NOT NULL
        REFERENCES repository_retrieval_snapshots(id) ON DELETE RESTRICT,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (run_id, retrieval_snapshot_id)
);

CREATE TABLE context_plans (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
    retrieval_snapshot_id TEXT
        REFERENCES repository_retrieval_snapshots(id) ON DELETE RESTRICT,
    model_profile_snapshot_hash TEXT NOT NULL,
    rule_snapshot_id TEXT NOT NULL,
    inventory_snapshot_id TEXT,
    index_snapshot_id TEXT,
    snapshot_hash TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE TABLE context_snapshots (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
    model_attempt_id TEXT NOT NULL UNIQUE,
    plan_id TEXT NOT NULL REFERENCES context_plans(id) ON DELETE RESTRICT,
    snapshot_hash TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE TABLE verified_compact_summaries (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
    summary_hash TEXT NOT NULL,
    verified_json TEXT NOT NULL,
    input_start INTEGER NOT NULL,
    input_end INTEGER NOT NULL,
    compaction_version INTEGER NOT NULL,
    verification_result TEXT NOT NULL CHECK (verification_result = 'verified'),
    verified_at INTEGER NOT NULL
);

CREATE TABLE checkpoints (
    creation_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
    item_ordinal INTEGER NOT NULL CHECK (item_ordinal >= 0),
    rule_snapshot_id TEXT,
    repository_snapshot_id TEXT,
    context_snapshot_id TEXT REFERENCES context_snapshots(id) ON DELETE RESTRICT,
    compact_summary_id TEXT REFERENCES verified_compact_summaries(id) ON DELETE RESTRICT,
    workspace_identity_hash TEXT NOT NULL,
    git_head TEXT,
    git_snapshot_id TEXT REFERENCES worktree_snapshots(id) ON DELETE RESTRICT,
    permission_snapshot_hash TEXT,
    model_profile_snapshot_hash TEXT NOT NULL,
    reconciliation_required INTEGER NOT NULL CHECK (reconciliation_required IN (0, 1)),
    created_at INTEGER NOT NULL
);

CREATE INDEX checkpoints_run_boundary
ON checkpoints(run_id, item_ordinal, creation_seq);

CREATE TABLE checkpoint_actions (
    id TEXT PRIMARY KEY,
    checkpoint_id TEXT NOT NULL REFERENCES checkpoints(id) ON DELETE RESTRICT,
    action TEXT NOT NULL CHECK (action IN ('rewind', 'fork')),
    source_run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
    target_run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
    created_at INTEGER NOT NULL
);
"""

_BASE_WITH_CONTEXT_SNAPSHOT_SQL = _RAW_BASE_SCHEMA_SQL.replace(
    "    retry_decision_json TEXT,",
    "    context_snapshot_id TEXT REFERENCES context_snapshots(id) ON DELETE RESTRICT,\n"
    "    retry_decision_json TEXT,",
)
_LEGACY_MODEL_TABLES_SQL = """
CREATE TABLE model_profiles (
    creation_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL UNIQUE,
    profile_json TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE model_capability_snapshots (
    creation_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    profile_id TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    probed_at INTEGER NOT NULL
);

CREATE TABLE run_model_snapshots (
    run_id TEXT PRIMARY KEY REFERENCES runs(id) ON DELETE RESTRICT,
    profile_id TEXT NOT NULL,
    capability_snapshot_id TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    frozen_at INTEGER NOT NULL
);

"""
_BASE_WITHOUT_LEGACY_MODEL_TABLES_SQL = _BASE_WITH_CONTEXT_SNAPSHOT_SQL.replace(
    _LEGACY_MODEL_TABLES_SQL, ""
).replace(
    "    model_profile_id TEXT,\n", ""
)
if _BASE_WITHOUT_LEGACY_MODEL_TABLES_SQL == _BASE_WITH_CONTEXT_SNAPSHOT_SQL:
    raise RuntimeError("legacy model tables were not removed from the current schema")

# Current base additions: effective_cwd on runs, resolved_instructions_hash +
# effective_cwd on step_resolution_snapshots.
BASE_SCHEMA_SQL = _BASE_WITHOUT_LEGACY_MODEL_TABLES_SQL.replace(
    "    error_code TEXT,\n"
    "    cancel_requested_at INTEGER,",
    "    effective_cwd TEXT,\n"
    "    error_code TEXT,\n"
    "    cancel_requested_at INTEGER,",
).replace(
    "    snapshot_hash TEXT NOT NULL,\n"
    "    snapshot_json TEXT NOT NULL,\n"
    "    created_at INTEGER NOT NULL\n"
    ");\n"
    "\n"
    "CREATE TABLE steps",
    "    snapshot_hash TEXT NOT NULL,\n"
    "    snapshot_json TEXT NOT NULL,\n"
    "    resolved_instructions_hash TEXT,\n"
    "    effective_cwd TEXT,\n"
    "    created_at INTEGER NOT NULL\n"
    ");\n"
    "\n"
    "CREATE TABLE steps",
)
if BASE_SCHEMA_SQL == _BASE_WITHOUT_LEGACY_MODEL_TABLES_SQL:
    raise RuntimeError("current schema additions are missing")

RESPONSE_ACTIONS_SCHEMA_SQL = """
CREATE TABLE response_feedback (
    item_id TEXT PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
    value TEXT NOT NULL CHECK (value IN ('up', 'down')),
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE run_revisions (
    run_id TEXT PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
    source_run_id TEXT NOT NULL UNIQUE REFERENCES runs(id) ON DELETE CASCADE,
    revision_kind TEXT NOT NULL CHECK (revision_kind IN ('regenerate', 'edit')),
    created_at INTEGER NOT NULL,
    CHECK (run_id <> source_run_id)
);

CREATE INDEX run_revisions_source
ON run_revisions(source_run_id);
"""

REVIEW_COMMENTS_SCHEMA_SQL = """
CREATE TABLE review_comments (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    scope TEXT NOT NULL CHECK (scope IN ('head', 'baseline')),
    side TEXT NOT NULL CHECK (side IN ('old', 'new')),
    line INTEGER NOT NULL CHECK (line > 0),
    body TEXT NOT NULL,
    base_head TEXT NOT NULL,
    diff_hash TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'stale')),
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX review_comments_session_path
ON review_comments(session_id, path, scope, status, created_at);
"""

COMPACTION_QUALITY_SCHEMA_SQL = """
ALTER TABLE compact_summaries
ADD COLUMN summary_metadata_json TEXT NOT NULL DEFAULT '{}';
"""

WORKTREE_TABLES_SCHEMA_SQL = """
CREATE TABLE worktrees (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    worktree_root TEXT NOT NULL UNIQUE,
    git_dir TEXT NOT NULL,
    base_ref TEXT NOT NULL,
    base_commit TEXT NOT NULL,
    branch TEXT,
    ownership TEXT NOT NULL CHECK (ownership IN ('managed', 'adopted')),
    state TEXT NOT NULL CHECK (
        state IN ('active', 'missing', 'invalid', 'deleted')
    ),
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE(project_id, branch)
);

CREATE INDEX worktrees_project_state
ON worktrees(project_id, state, updated_at DESC);

CREATE INDEX worktrees_project_ownership
ON worktrees(project_id, ownership, state);
"""

SESSION_WORKTREE_SCHEMA_SQL = """
ALTER TABLE sessions
ADD COLUMN worktree_id TEXT REFERENCES worktrees(id) ON DELETE RESTRICT;

CREATE INDEX sessions_worktree_id
ON sessions(worktree_id);
"""

WORKTREE_LIFECYCLE_SCHEMA_SQL = """
CREATE TABLE worktree_lifecycle_operations (
    scope TEXT NOT NULL CHECK (
        scope IN ('session/create', 'session/delete', 'checkpoint/fork')
    ),
    operation_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN (
            'prepared',
            'worktree_created',
            'session_created',
            'run_created',
            'checkpoint_action_created',
            'worktree_deleted',
            'completed',
            'cleanup_required'
        )
    ),
    project_id TEXT,
    repository_root TEXT,
    worktree_id TEXT,
    worktree_root TEXT,
    base_ref TEXT,
    branch TEXT,
    base_commit TEXT,
    session_id TEXT,
    run_id TEXT,
    checkpoint_id TEXT,
    error_code TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (scope, operation_id)
);

CREATE INDEX worktree_lifecycle_operations_state
ON worktree_lifecycle_operations(state, updated_at);

CREATE INDEX worktree_lifecycle_operations_session
ON worktree_lifecycle_operations(session_id, scope);
"""

WORKTREE_BRANCH_OWNERSHIP_SCHEMA_SQL = """
ALTER TABLE worktrees
ADD COLUMN branch_ownership TEXT NOT NULL DEFAULT 'legacy_managed' CHECK (
    branch_ownership IN ('none', 'legacy_managed', 'user')
);

UPDATE worktrees
SET branch_ownership = CASE
    WHEN branch IS NULL THEN 'none'
    ELSE 'legacy_managed'
END;

CREATE TABLE worktree_lifecycle_operations_branch_ownership (
    scope TEXT NOT NULL CHECK (
        scope IN (
            'session/create',
            'session/delete',
            'checkpoint/fork',
            'worktree/attach-branch'
        )
    ),
    operation_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN (
            'prepared',
            'worktree_created',
            'session_created',
            'run_created',
            'checkpoint_action_created',
            'branch_attached',
            'worktree_deleted',
            'completed',
            'cleanup_required'
        )
    ),
    project_id TEXT,
    repository_root TEXT,
    worktree_id TEXT,
    worktree_root TEXT,
    base_ref TEXT,
    branch TEXT,
    base_commit TEXT,
    expected_head TEXT,
    session_id TEXT,
    run_id TEXT,
    checkpoint_id TEXT,
    include_local_changes INTEGER NOT NULL DEFAULT 0 CHECK (
        include_local_changes IN (0, 1)
    ),
    source_head TEXT,
    source_branch TEXT,
    source_dirty INTEGER CHECK (source_dirty IN (0, 1)),
    source_fingerprint TEXT,
    error_code TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (scope, operation_id)
);

INSERT INTO worktree_lifecycle_operations_branch_ownership (
    scope, operation_id, state, project_id,
    repository_root, worktree_id, worktree_root,
    base_ref, branch, base_commit, expected_head, session_id, run_id,
    checkpoint_id, include_local_changes, source_head,
    source_branch, source_dirty, source_fingerprint, error_code,
    created_at, updated_at
)
SELECT scope, operation_id, state, project_id,
       repository_root, worktree_id, worktree_root,
       base_ref, branch, base_commit, NULL, session_id, run_id,
       checkpoint_id, 0, NULL, NULL, NULL,
       NULL, error_code, created_at, updated_at
FROM worktree_lifecycle_operations;

DROP TABLE worktree_lifecycle_operations;
ALTER TABLE worktree_lifecycle_operations_branch_ownership
RENAME TO worktree_lifecycle_operations;

CREATE INDEX worktree_lifecycle_operations_state
ON worktree_lifecycle_operations(state, updated_at);

CREATE INDEX worktree_lifecycle_operations_session
ON worktree_lifecycle_operations(session_id, scope);
"""

PROJECT_SCHEMA_SQL = """
CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    name TEXT,
    workspace_root TEXT NOT NULL UNIQUE,
    git_repository_root TEXT,
    git_common_dir TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    CHECK (
        (git_repository_root IS NULL AND git_common_dir IS NULL)
        OR (git_repository_root IS NOT NULL AND git_common_dir IS NOT NULL)
    )
);

"""

SESSION_EXECUTION_SCHEMA_SQL = """
ALTER TABLE sessions
ADD COLUMN execution_mode TEXT NOT NULL DEFAULT 'local' CHECK (
    execution_mode IN ('local', 'worktree')
);

UPDATE sessions
SET execution_mode = CASE
    WHEN worktree_id IS NULL THEN 'local'
    ELSE 'worktree'
END;
"""

SESSION_HANDOFF_SCHEMA_SQL = """
ALTER TABLE worktrees
ADD COLUMN checkout_branch TEXT;

UPDATE worktrees
SET checkout_branch = branch
WHERE checkout_branch IS NULL AND branch IS NOT NULL;

ALTER TABLE sessions
ADD COLUMN associated_worktree_id TEXT
REFERENCES worktrees(id) ON DELETE RESTRICT;

CREATE INDEX sessions_associated_worktree_id
ON sessions(associated_worktree_id);

CREATE TABLE session_handoff_operations (
    scope TEXT NOT NULL CHECK (
        scope IN ('session/handoff-local', 'session/handoff-worktree')
    ),
    operation_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN (
            'prepared', 'source_captured', 'target_materialized',
            'session_rebound', 'completed', 'cleanup_required'
        )
    ),
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE RESTRICT,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    source_mode TEXT NOT NULL CHECK (source_mode IN ('local', 'worktree')),
    target_mode TEXT NOT NULL CHECK (target_mode IN ('local', 'worktree')),
    source_root TEXT NOT NULL,
    target_root TEXT NOT NULL,
    source_common_dir TEXT NOT NULL,
    target_common_dir TEXT NOT NULL,
    associated_worktree_id TEXT,
    target_worktree_new INTEGER NOT NULL CHECK (target_worktree_new IN (0, 1)),
    target_base_ref TEXT,
    target_base_commit TEXT,
    source_head TEXT NOT NULL,
    source_branch TEXT,
    source_dirty INTEGER NOT NULL CHECK (source_dirty IN (0, 1)),
    source_fingerprint TEXT NOT NULL,
    target_head TEXT NOT NULL,
    target_branch TEXT,
    target_dirty INTEGER NOT NULL CHECK (target_dirty IN (0, 1)),
    target_fingerprint TEXT NOT NULL,
    target_after_head TEXT,
    target_after_branch TEXT,
    target_after_fingerprint TEXT,
    source_after_head TEXT,
    source_after_branch TEXT,
    source_after_fingerprint TEXT,
    error_code TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (scope, operation_id)
);

CREATE INDEX session_handoff_operations_state
ON session_handoff_operations(state, updated_at);

CREATE INDEX session_handoff_operations_session
ON session_handoff_operations(session_id, created_at DESC);
"""

WORKTREE_RETENTION_SCHEMA_SQL = """
ALTER TABLE worktrees
ADD COLUMN last_used_at INTEGER NOT NULL DEFAULT 0;

UPDATE worktrees
SET last_used_at = updated_at
WHERE last_used_at = 0;

CREATE TABLE runtime_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    automatic_cleanup INTEGER NOT NULL CHECK (automatic_cleanup IN (0, 1)),
    managed_worktree_limit INTEGER NOT NULL CHECK (
        managed_worktree_limit BETWEEN 1 AND 100
    ),
    updated_at INTEGER NOT NULL
);

INSERT INTO runtime_settings (
    id, automatic_cleanup, managed_worktree_limit, updated_at
) VALUES (1, 1, 15, strftime('%s','now') * 1000);

CREATE TABLE worktree_snapshots (
    id TEXT PRIMARY KEY,
    worktree_id TEXT REFERENCES worktrees(id) ON DELETE RESTRICT,
    workspace_root TEXT NOT NULL,
    session_id TEXT REFERENCES sessions(id) ON DELETE RESTRICT,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE RESTRICT,
    base_ref TEXT NOT NULL,
    base_commit TEXT NOT NULL,
    head TEXT NOT NULL,
    branch TEXT,
    checkout_branch TEXT,
    branch_ownership TEXT NOT NULL CHECK (
        branch_ownership IN ('none', 'legacy_managed', 'user')
    ),
    dirty INTEGER NOT NULL CHECK (dirty IN (0, 1)),
    staged_paths_json TEXT NOT NULL,
    unstaged_paths_json TEXT NOT NULL,
    untracked_paths_json TEXT NOT NULL,
    conflict_paths_json TEXT NOT NULL,
    source_fingerprint TEXT NOT NULL,
    artifact_path TEXT NOT NULL,
    artifact_sha256 TEXT NOT NULL,
    full_patch_sha256 TEXT NOT NULL,
    staged_patch_sha256 TEXT NOT NULL,
    format_version INTEGER NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('ready', 'restored', 'invalid')),
    created_at INTEGER NOT NULL,
    restored_at INTEGER,
    updated_at INTEGER NOT NULL
);

CREATE INDEX worktree_snapshots_latest
ON worktree_snapshots(worktree_id, state, created_at DESC, id DESC);

CREATE TABLE worktree_lifecycle_operations_retention (
    scope TEXT NOT NULL CHECK (
        scope IN (
            'session/create',
            'session/delete',
            'checkpoint/fork',
            'checkpoint/rewind',
            'worktree/attach-branch',
            'worktree/retention-cleanup',
            'worktree/restore'
        )
    ),
    operation_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (
        state IN (
            'prepared',
            'worktree_created',
            'session_created',
            'run_created',
            'checkpoint_action_created',
            'branch_attached',
            'snapshot_saved',
            'state_materialized',
            'worktree_rebound',
            'worktree_deleted',
            'completed',
            'cleanup_required'
        )
    ),
    project_id TEXT,
    repository_root TEXT,
    worktree_id TEXT,
    worktree_root TEXT,
    base_ref TEXT,
    branch TEXT,
    base_commit TEXT,
    expected_head TEXT,
    session_id TEXT,
    run_id TEXT,
    checkpoint_id TEXT,
    include_local_changes INTEGER NOT NULL DEFAULT 0 CHECK (
        include_local_changes IN (0, 1)
    ),
    source_head TEXT,
    source_branch TEXT,
    source_dirty INTEGER CHECK (source_dirty IN (0, 1)),
    source_fingerprint TEXT,
    snapshot_id TEXT,
    snapshot_head TEXT,
    snapshot_fingerprint TEXT,
    error_code TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (scope, operation_id)
);

INSERT INTO worktree_lifecycle_operations_retention (
    scope, operation_id, state, project_id,
    repository_root, worktree_id, worktree_root,
    base_ref, branch, base_commit, expected_head, session_id, run_id,
    checkpoint_id, include_local_changes, source_head,
    source_branch, source_dirty, source_fingerprint,
    error_code, created_at, updated_at
)
SELECT scope, operation_id, state, project_id,
       repository_root, worktree_id, worktree_root,
       base_ref, branch, base_commit, expected_head, session_id, run_id,
       checkpoint_id, include_local_changes, source_head,
       source_branch, source_dirty, source_fingerprint,
       error_code, created_at, updated_at
FROM worktree_lifecycle_operations;

DROP TABLE worktree_lifecycle_operations;
ALTER TABLE worktree_lifecycle_operations_retention
RENAME TO worktree_lifecycle_operations;

CREATE INDEX worktree_lifecycle_operations_state
ON worktree_lifecycle_operations(state, updated_at);

CREATE INDEX worktree_lifecycle_operations_session
ON worktree_lifecycle_operations(session_id, scope);
"""

SCHEMA_SQL = (
    BASE_SCHEMA_SQL
    + CONTEXT_SCHEMA_SQL
    + RESPONSE_ACTIONS_SCHEMA_SQL
    + REVIEW_COMMENTS_SCHEMA_SQL
    + COMPACTION_QUALITY_SCHEMA_SQL
    + PROJECT_SCHEMA_SQL
    + WORKTREE_TABLES_SCHEMA_SQL
    + SESSION_WORKTREE_SCHEMA_SQL
    + WORKTREE_LIFECYCLE_SCHEMA_SQL
    + SESSION_EXECUTION_SCHEMA_SQL
    + WORKTREE_BRANCH_OWNERSHIP_SCHEMA_SQL
    + SESSION_HANDOFF_SCHEMA_SQL
    + WORKTREE_RETENTION_SCHEMA_SQL
)

# Test/upgrade fixture for schema v5. Schema v5 still kept the rebuildable
# repository index in the state database.
V5_SCHEMA_SQL = (
    BASE_SCHEMA_SQL
    + REPOSITORY_SCHEMA_SQL
    + CONTEXT_SCHEMA_SQL
    + RESPONSE_ACTIONS_SCHEMA_SQL
    + REVIEW_COMMENTS_SCHEMA_SQL
    + COMPACTION_QUALITY_SCHEMA_SQL
    + PROJECT_SCHEMA_SQL
    + WORKTREE_TABLES_SCHEMA_SQL
    + SESSION_WORKTREE_SCHEMA_SQL
    + WORKTREE_LIFECYCLE_SCHEMA_SQL
    + SESSION_EXECUTION_SCHEMA_SQL
    + WORKTREE_BRANCH_OWNERSHIP_SCHEMA_SQL
    + SESSION_HANDOFF_SCHEMA_SQL
    + WORKTREE_RETENTION_SCHEMA_SQL
)

# Test/upgrade fixture for schema v4. It omits the v5 Model Attempt
# diagnostics columns.
V4_SCHEMA_SQL = V5_SCHEMA_SQL.replace(
    MODEL_ATTEMPT_DIAGNOSTICS_COLUMNS, ""
)

# Test/upgrade fixture for the immediately previous schema. Keep this derived
# from the current baseline so unrelated tables cannot drift between fixtures.
V2_SCHEMA_SQL = V4_SCHEMA_SQL.replace(
    "CREATE TABLE projects (\n"
    "    id TEXT PRIMARY KEY,\n"
    "    name TEXT,\n",
    "CREATE TABLE projects (\n"
    "    id TEXT PRIMARY KEY,\n",
).replace(
    "CREATE TABLE repository_retrieval_snapshots (\n"
    "    id TEXT PRIMARY KEY,\n",
    "CREATE TABLE repository_retrieval_snapshots (\n"
    "    id TEXT PRIMARY KEY,\n"
    "    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,\n",
).replace(
    "CREATE TABLE run_repository_retrievals (\n"
    "    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,\n"
    "    retrieval_snapshot_id TEXT NOT NULL\n"
    "        REFERENCES repository_retrieval_snapshots(id) ON DELETE RESTRICT,\n"
    "    created_at INTEGER NOT NULL,\n"
    "    PRIMARY KEY (run_id, retrieval_snapshot_id)\n"
    ");\n\n",
    "",
).replace(
    "    retrieval_snapshot_id TEXT\n",
    "    retrieval_snapshot_id TEXT NOT NULL\n",
).replace(
    "    inventory_snapshot_id TEXT,\n"
    "    index_snapshot_id TEXT,\n"
    "    snapshot_hash TEXT NOT NULL,\n"
    "    plan_json TEXT NOT NULL,",
    "    inventory_snapshot_id TEXT NOT NULL,\n"
    "    index_snapshot_id TEXT NOT NULL,\n"
    "    snapshot_hash TEXT NOT NULL,\n"
    "    plan_json TEXT NOT NULL,",
)

V1_TO_V2_MIGRATION_SQL = """
ALTER TABLE repository_snapshots
ADD COLUMN repository_map_json TEXT;
"""

V2_TO_V3_MIGRATION_SQL = """
CREATE TABLE repository_retrieval_snapshots_v3 (
    id TEXT PRIMARY KEY,
    inventory_snapshot_id TEXT NOT NULL,
    index_snapshot_id TEXT NOT NULL,
    snapshot_hash TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

INSERT INTO repository_retrieval_snapshots_v3 (
    id, inventory_snapshot_id, index_snapshot_id,
    snapshot_hash, snapshot_json, created_at
)
SELECT id, inventory_snapshot_id, index_snapshot_id,
       snapshot_hash, snapshot_json, created_at
FROM repository_retrieval_snapshots;

CREATE TABLE context_plans_v3 (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
    retrieval_snapshot_id TEXT
        REFERENCES repository_retrieval_snapshots(id) ON DELETE RESTRICT,
    model_profile_snapshot_hash TEXT NOT NULL,
    rule_snapshot_id TEXT NOT NULL,
    inventory_snapshot_id TEXT,
    index_snapshot_id TEXT,
    snapshot_hash TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE TABLE context_snapshots_v3 (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
    model_attempt_id TEXT NOT NULL UNIQUE,
    plan_id TEXT NOT NULL REFERENCES context_plans(id) ON DELETE RESTRICT,
    snapshot_hash TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

INSERT INTO context_plans_v3 SELECT * FROM context_plans;
INSERT INTO context_snapshots_v3 SELECT * FROM context_snapshots;

DROP TABLE context_snapshots;
DROP TABLE context_plans;
DROP TABLE repository_retrieval_snapshots;

ALTER TABLE repository_retrieval_snapshots_v3
RENAME TO repository_retrieval_snapshots;
ALTER TABLE context_plans_v3 RENAME TO context_plans;
ALTER TABLE context_snapshots_v3 RENAME TO context_snapshots;

CREATE TABLE run_repository_retrievals (
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE RESTRICT,
    retrieval_snapshot_id TEXT NOT NULL
        REFERENCES repository_retrieval_snapshots(id) ON DELETE RESTRICT,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (run_id, retrieval_snapshot_id)
);

INSERT INTO run_repository_retrievals (
    run_id, retrieval_snapshot_id, created_at
)
SELECT DISTINCT run_id, retrieval_snapshot_id, created_at
FROM context_plans
WHERE retrieval_snapshot_id IS NOT NULL;
"""

V3_TO_V4_MIGRATION_SQL = """
ALTER TABLE projects ADD COLUMN name TEXT;
"""

V4_TO_V5_MIGRATION_SQL = """
ALTER TABLE model_attempts ADD COLUMN configured_provider_id TEXT;
ALTER TABLE model_attempts ADD COLUMN response_state TEXT;
ALTER TABLE model_attempts ADD COLUMN phase TEXT;
ALTER TABLE model_attempts ADD COLUMN tool_call_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE model_attempts ADD COLUMN response_text_sha256 TEXT;
ALTER TABLE model_attempts ADD COLUMN response_text_bytes INTEGER NOT NULL DEFAULT 0;
ALTER TABLE model_attempts ADD COLUMN protocol_diagnostics_json TEXT;
"""

V5_TO_V6_MIGRATION_SQL = """
DROP TABLE repository_fts;
DROP TABLE repository_diagnostics;
DROP TABLE repository_chunks;
DROP TABLE repository_references;
DROP TABLE repository_imports;
DROP TABLE repository_symbols;
DROP TABLE repository_parsed_files;
DROP TABLE repository_index_generations;
DROP TABLE repository_directories;
DROP TABLE repository_files;
DROP TABLE repository_snapshots;
"""

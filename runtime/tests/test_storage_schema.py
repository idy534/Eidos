from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import sys
import subprocess
import tempfile
import unittest
import threading


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eidos_runtime.db.schema import (  # noqa: E402
    LEGACY_SCHEMA_VERSION,
    PREVIOUS_SCHEMA_VERSION,
    V46_SCHEMA_VERSION,
    SCHEMA_SQL,
    SCHEMA_VERSION,
    V9_SCHEMA_SQL,
    V9_SCHEMA_VERSION,
    V10_SCHEMA_SQL,
    V10_SCHEMA_VERSION,
    V11_SCHEMA_SQL,
    V11_SCHEMA_VERSION,
    V12_SCHEMA_SQL,
    V15_SCHEMA_SQL,
    V12_SCHEMA_VERSION,
    V7_SCHEMA_SQL,
    V7_SCHEMA_VERSION,
    V5_SCHEMA_VERSION,
    V4_SCHEMA_SQL,
    V2_SCHEMA_SQL,
)
from eidos_runtime.context.budget import estimate_context_budget  # noqa: E402
from eidos_runtime.context.plan import ContextPlanner  # noqa: E402
from eidos_runtime.model.client import (  # noqa: E402
    ModelProfileSnapshot,
    ModelResponse,
    ScriptedModel,
)
from eidos_runtime.runtime.engine import RuntimeEngine  # noqa: E402
from eidos_runtime.runtime.resolution import RuleResolutionSnapshot  # noqa: E402
from eidos_runtime.db.storage import DATABASE_NAME, SessionStore  # noqa: E402
from eidos_runtime.db.database import Database  # noqa: E402
from eidos_runtime.domain.project_workspace import SessionRoot  # noqa: E402
from eidos_runtime.git.backend import DulwichGitBackend  # noqa: E402
from eidos_runtime.persistence.project_workspaces import verify_session_roots  # noqa: E402
from eidos_runtime.workspace.reader import (  # noqa: E402
    WorkspacePathError,
    capture_workspace_identity,
)
from eidos_runtime.runtime.state_machine import (  # noqa: E402
    RunStatus,
    RuntimeState,
    SegmentStatus,
)


EXPECTED_TABLES = {
    "personalization_settings",
    "hook_sources",
    "mcp_connections",
    "plugin_versions",
    "plugin_hook_trust", "hook_invocations", "hook_pending_events",
    "shell_environment_settings", "web_search_settings", "run_web_search_snapshots",
    "search_documents", "search_pending", "search_index_state", "search_fts",
    "channel_connections", "channel_sessions", "channel_inbound", "channel_run_origins", "channel_deliveries",
    "scheduled_tasks", "scheduled_occurrences", "automation_run_origins",
    "sessions",
    "session_forks",
    "session_history_entries",
    "session_history_resources",
    "runs",
    "run_agent_catalogs",
    "items",
    "tool_calls",
    "approvals",
    "tool_attempts",
    "execution_segments",
    "steps",
    "model_attempts",
    "finalization_attempts",
    "events",
    "operations",
    "durable_intents",
    "plugins",
    "mcp_server_states",
    "manual_mcp_servers",
    "compact_summaries",
    "input_mailbox",
    "async_operations",
    "event_outbox",
    "rule_resolution_snapshots",
    "run_resolution_snapshots",
    "step_resolution_snapshots",
    "repository_retrieval_snapshots",
    "run_repository_retrievals",
    "context_plans",
    "context_snapshots",
    "verified_compact_summaries",
    "checkpoints",
    "checkpoint_actions",
    "response_feedback",
    "run_revisions",
    "review_comments",
    "projects",
    "project_roots",
    "sidebar_pins",
    "worktrees",
    "worktree_lifecycle_operations",
    "session_handoff_operations",
    "runtime_settings",
    "worktree_snapshots",
    "run_dependency_snapshots",
    "run_dependency_bindings",
    "skill_states",
    "input_references",
    "input_drafts",
    "plans",
    "plan_revisions",
    "user_input_requests",
    "agent_delegations",
    "agent_messages",
    "agent_waits",
    "agent_message_receipts",
    "run_skill_leases",
    "projectless_workspace_creations",
    "memory_scopes", "memory_sources", "memory_source_items", "memory_documents",
    "memory_revisions", "memory_evidence", "memory_actions", "memory_suppressions",
    "memory_indexes", "memory_snapshot_refs", "memory_tool_reads", "memory_metadata",
}

EXPECTED_COLUMNS = {
    "agent_delegations": {"required_for_completion"},
    "sessions": {
        "workspace_dev", "workspace_inode", "workspace_uid", "worktree_id",
        "associated_worktree_id",
    },
    "worktrees": {
        "checkout_branch", "last_used_at", "git_dir_dev", "git_dir_inode",
        "git_dir_uid",
    },
    "session_handoff_operations": {
        "scope", "operation_id", "state", "session_id", "project_id",
        "source_mode", "target_mode", "source_root", "target_root",
        "source_common_dir", "target_common_dir", "associated_worktree_id",
        "target_worktree_new", "source_head", "source_fingerprint",
        "target_head", "target_fingerprint", "error_code",
    },
    "compact_summaries": {"summary_metadata_json"},
    "items": {"incomplete", "input_references_json"},
    "review_comments": {
        "session_id", "path", "scope", "side", "line", "body",
        "base_head", "diff_hash", "status", "created_at", "updated_at",
    },
    "checkpoints": {"git_snapshot_id"},
    "runs": {
        "extension_snapshot_json",
        "activated_tools_json",
        "compaction_count",
        "workspace_version",
        "last_diff_hash",
        "model_profile_json",
        "cancel_requested_at",
        "cancel_completed_at",
        "cancel_failure_code",
        "approval_mode",
        "work_mode", "plan_id", "plan_revision",
    },
    "plans": {"id", "session_id", "run_id", "revision", "title", "markdown", "sha256", "status", "updated_at", "execution_run_id", "projected_sha256"},
    "plan_revisions": {"plan_id", "revision", "markdown", "sha256", "created_at"},
    "user_input_requests": {"id", "session_id", "run_id", "item_id", "questions_json", "status", "response_json", "created_at"},
    "steps": {
        "resolution_snapshot_id",
        "tool_snapshot_json",
        "tool_set_hash",
        "progress_signature_json",
    },
    "tool_calls": {
        "raw_arguments_json",
        "payload_kind",
        "approval_status",
        "approval_decision",
        "approval_feedback",
        "approval_diff",
        "base_sha256",
        "provenance_json",
        "tool_set_hash",
        "duration_ms",
        "result_json",
        "model_result_json",
        "ui_result_json",
        "progress_fingerprint",
    },
    "approvals": {
        "request_json", "attempt_ordinal", "approval_kind",
        "review_json",
    },
    "worktree_lifecycle_operations": {
        "expected_head", "snapshot_id", "snapshot_head", "snapshot_fingerprint", "request_hash"
    },
    "worktree_snapshots": {
        "id", "worktree_id", "workspace_root", "session_id", "project_id", "base_ref",
        "base_commit", "head", "artifact_path", "artifact_sha256", "state",
    },
    "runtime_settings": {
        "automatic_cleanup", "managed_worktree_limit", "updated_at"
    },
    "model_attempts": {
        "lease_id", "configured_provider_id", "wire_api", "model_id", "request_timeout",
        "retry_decision_json",
        "context_snapshot_id",
        "response_state", "phase", "tool_call_count",
        "response_text_sha256", "response_text_bytes",
        "protocol_diagnostics_json",
    },
    "tool_attempts": {
        "tool_call_id", "ordinal", "sandbox_type", "sandbox_requested",
        "effective_permissions_json", "profile_hash", "escalation_reason",
        "status", "result_code",
    },
    "projects": {"name"},
    "skill_states": {
        "qualified_id", "enabled", "removed", "source_kind", "directory_name",
        "directory_device", "directory_inode", "content_hash", "cleanup_pending",
        "updated_at",
    },
}


def _seed_context_lineage(
    connection: sqlite3.Connection,
    workspace: str,
) -> tuple[str, str, str]:
    profile = ModelProfileSnapshot(
        provider_id="provider", model_id="model", context_window_tokens=4096,
        max_output_tokens=512, request_timeout_seconds=30.0,
        supports_tools=True, supports_json_schema_output=True,
        supports_reasoning=False,
    )
    rules = RuleResolutionSnapshot.create(
        workspace_root=workspace, cwd=workspace, budget_bytes=1024,
        used_bytes=0, rules=(), shadowed=(), warnings=(),
    )
    context = ({"type": "user", "content": "legacy exact request"},)
    budget = estimate_context_budget(
        {"instructions": "", "messages": context, "tools": []},
        context_window_tokens=4096, request_max_output_tokens=512,
        message_count=1, tool_call_count=0, tool_result_count=0,
    )
    plan = ContextPlanner().capture(
        model_profile=profile,
        rule_snapshot=rules,
        model_context=context,
        instructions="",
        tool_definitions=(),
        token_budget=budget,
        inventory_snapshot_id="inventory-v2",
        index_snapshot_id="index-v2",
        repository_map_snapshot_id="map-v2",
        retrieval_snapshot_id="retrieval-v2",
    )
    snapshot = plan.for_model_attempt(
        "attempt-v2", model_context=context, instructions="", tool_definitions=()
    )
    connection.execute(
        "INSERT INTO sessions (id, workspace_root, created_at, updated_at) "
        "VALUES ('session-v2', ?, 1, 1)",
        (workspace,),
    )
    connection.execute(
        """
        INSERT INTO runs (
            id, session_id, user_input, model_profile_json, status,
            created_at, updated_at, completed_at
        ) VALUES ('run-v2', 'session-v2', 'legacy', '{}', 'succeeded', 1, 1, 1)
        """
    )
    connection.execute(
        "INSERT INTO run_resolution_snapshots "
        "(id, run_id, snapshot_hash, snapshot_json, created_at) "
        "VALUES ('run-resolution-v2', 'run-v2', 'hash', '{}', 1)"
    )
    connection.execute(
        "INSERT INTO rule_resolution_snapshots "
        "(id, snapshot_hash, snapshot_json, created_at) "
        "VALUES ('rule-v2', 'rule-hash-v2', '{}', 1)"
    )
    connection.execute(
        "INSERT INTO step_resolution_snapshots "
        "(id, run_snapshot_id, rule_snapshot_id, snapshot_hash, snapshot_json, created_at) "
        "VALUES ('step-resolution-v2', 'run-resolution-v2', 'rule-v2', 'hash', '{}', 1)"
    )
    connection.execute(
        "INSERT INTO execution_segments "
        "(id, run_id, ordinal, status, created_at, completed_at) "
        "VALUES ('segment-v2', 'run-v2', 1, 'completed', 1, 1)"
    )
    connection.execute(
        "INSERT INTO steps "
        "(id, run_id, segment_id, ordinal, status, resolution_snapshot_id, created_at, completed_at) "
        "VALUES ('step-v2', 'run-v2', 'segment-v2', 1, 'completed', 'step-resolution-v2', 1, 1)"
    )
    retrieval_columns = {
        row[1]
        for row in connection.execute(
            "PRAGMA table_info(repository_retrieval_snapshots)"
        )
    }
    if "run_id" in retrieval_columns:
        connection.execute(
            """
            INSERT INTO repository_retrieval_snapshots (
                id, run_id, inventory_snapshot_id, index_snapshot_id,
                snapshot_hash, snapshot_json, created_at
            ) VALUES ('retrieval-v2', 'run-v2', 'inventory-v2', 'index-v2',
                      'retrieval-hash-v2', '{}', 1)
            """
        )
    else:
        connection.execute(
            """
            INSERT INTO repository_retrieval_snapshots (
                id, inventory_snapshot_id, index_snapshot_id,
                snapshot_hash, snapshot_json, created_at
            ) VALUES ('retrieval-v2', 'inventory-v2', 'index-v2',
                      'retrieval-hash-v2', '{}', 1)
            """
        )
    connection.execute(
        """
        INSERT INTO context_plans (
            id, run_id, retrieval_snapshot_id, model_profile_snapshot_hash,
            rule_snapshot_id, inventory_snapshot_id, index_snapshot_id,
            snapshot_hash, plan_json, created_at
        ) VALUES (?, 'run-v2', 'retrieval-v2', ?, ?, 'inventory-v2',
                  'index-v2', ?, ?, 1)
        """,
        (
            plan.plan_id, plan.model_profile_snapshot_hash,
            plan.rule_resolution_snapshot_id, plan.snapshot_hash,
            plan.model_dump_json(),
        ),
    )
    connection.execute(
        """
        INSERT INTO context_snapshots (
            id, run_id, model_attempt_id, plan_id,
            snapshot_hash, snapshot_json, created_at
        ) VALUES (?, 'run-v2', 'attempt-v2', ?, ?, ?, 1)
        """,
        (
            snapshot.snapshot_id, plan.plan_id,
            snapshot.snapshot_hash, snapshot.model_dump_json(),
        ),
    )
    connection.execute(
        """
        INSERT INTO model_attempts (
            id, step_id, ordinal, status, context_snapshot_id,
            had_progress, started_at, completed_at
        ) VALUES ('attempt-v2', 'step-v2', 1, 'completed', ?, 0, 1, 1)
        """,
        (snapshot.snapshot_id,),
    )
    return snapshot.snapshot_id, snapshot.model_dump_json(), plan.model_dump_json()


def _assert_context_foreign_keys(connection: sqlite3.Connection) -> None:
    context_fk = [
        row for row in connection.execute("PRAGMA foreign_key_list(model_attempts)")
        if row[3] == "context_snapshot_id"
    ]
    assert len(context_fk) == 1
    assert tuple(context_fk[0][2:5]) == (
        "context_snapshots", "context_snapshot_id", "id"
    )
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


class StorageSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-schema-")
        self.data = Path(self.temporary.name) / "data"
        self.data.mkdir(mode=0o700)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_waiting_user_input_is_not_a_runtime_state(self) -> None:
        self.assertNotIn("waiting_user_input", SCHEMA_SQL)
        for status_type in (RunStatus, RuntimeState, SegmentStatus):
            self.assertNotIn(
                "waiting_user_input",
                {status.value for status in status_type},
            )

    def test_legacy_waiting_run_is_interrupted_on_startup(self) -> None:
        legacy_schema = V15_SCHEMA_SQL.replace(
            "'waiting_input'", "'waiting_user_input'"
        ).replace(
            "'queued', 'running', 'completed', 'failed', 'canceled'",
            "'queued', 'running', 'waiting_user_input', 'completed', 'failed', 'canceled'",
        )
        database = self.data / DATABASE_NAME
        connection = sqlite3.connect(database)
        connection.executescript(legacy_schema)
        connection.execute(
            """
            INSERT INTO sessions (
                id, workspace_root, created_at, updated_at
            ) VALUES ('session', '/workspace', 1, 1)
            """
        )
        connection.execute(
            """
            INSERT INTO runs (
                id, session_id, user_input, model_id, model_profile_json,
                status, extension_snapshot_json, created_at, updated_at
            ) VALUES (
                'run', 'session', 'legacy', 'deepseek-v4-flash', '{}',
                'waiting_user_input', '{}', 1, 1
            )
            """
        )
        connection.execute(
            "UPDATE runs SET extension_snapshot_json = ? WHERE id = 'run'",
            (json.dumps({
                "schemaVersion": 1,
                "extensionContractVersion": 1,
                "plugins": [],
                "skillCatalogHash": "",
                "mcpConfigHash": "",
            }),),
        )
        connection.execute(
            """
            INSERT INTO execution_segments (
                id, run_id, ordinal, status, created_at
            ) VALUES ('segment', 'run', 1, 'waiting_user_input', 1)
            """
        )
        connection.execute("PRAGMA user_version = 15")
        connection.commit()
        connection.close()
        os.chmod(database, 0o600)

        store = SessionStore(self.data)
        store.initialize()

        self.assertEqual(store.read_run("run")["status"], "interrupted")
        assert store.connection is not None
        self.assertEqual(
            store.connection.execute(
                "SELECT status FROM execution_segments WHERE id = 'segment'"
            ).fetchone()[0],
            "failed",
        )
        store.close()

    def test_empty_database_creates_the_complete_baseline(self) -> None:
        store = SessionStore(self.data)
        store.initialize()
        self.assertEqual(store.health(), {"state": "ready"})
        connection = store.connection
        assert connection is not None

        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
            if not row[0].startswith("repository_fts_")
            and not row[0].startswith("memory_fts_")
            and not row[0].startswith("search_fts_")
        }
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL"
            )
        }
        self.assertEqual(tables, EXPECTED_TABLES)
        self.assertEqual(
            indexes - {"memory_source_items_session", "memory_document_path", "memory_evidence_source"},
            {
                "hook_invocations_plugin", "hook_invocations_run",
                "channel_inbound_status", "channel_deliveries_pending",
                "one_active_run_per_session",
                "scheduled_occurrences_task", "scheduled_occurrences_preparing", "automation_run_origins_task",
                "one_pending_approval_per_item",
                "one_pending_approval_per_run",
                "one_running_tool_attempt_per_tool_call",
                "one_running_segment_per_run",
                "one_active_segment_per_run",
                "one_running_step_per_run",
                "steps_run_ordinal",
                "search_documents_session",
                "run_web_search_credential_ref",
                "one_running_attempt_per_step",
                "model_request_retry_index",
                "one_running_finalization_attempt_per_run",
                "one_running_async_operation_per_operation_id",
                "one_pending_outbox_delivery_per_event",
                "checkpoints_run_boundary",
                "sidebar_pins_event_revision",
                "run_revisions_source",
                "review_comments_session_path",
                "review_comments_workspace",
                "projects_workspace",
                "sessions_project",
                "worktrees_project_state",
                "worktrees_project_ownership",
                "sessions_worktree_id",
                "worktree_lifecycle_operations_state",
                "worktree_lifecycle_operations_session",
                "sessions_associated_worktree_id",
                "session_handoff_operations_state",
                "session_handoff_operations_session",
                "worktree_snapshots_latest",
                "run_dependency_snapshots_hash",
                "run_dependency_bindings_run",
                "plans_session",
                "one_pending_user_input",
                "agent_delegations_parent",
                "agent_messages_recipient",
                "run_skill_leases_skill",
            },
        )
        for table, expected in EXPECTED_COLUMNS.items():
            columns = {
                row[1]
                for row in connection.execute(f"PRAGMA table_info({table})")
            }
            self.assertTrue(expected <= columns, (table, expected - columns))
        retrieval_columns = {
            row[1] for row in connection.execute(
                "PRAGMA table_info(repository_retrieval_snapshots)"
            )
        }
        self.assertNotIn("run_id", retrieval_columns)
        retrieval_binding_fks = {
            (row[2], row[3], row[4])
            for row in connection.execute(
                "PRAGMA foreign_key_list(run_repository_retrievals)"
            )
        }
        self.assertEqual(retrieval_binding_fks, {
            ("runs", "run_id", "id"),
            (
                "repository_retrieval_snapshots",
                "retrieval_snapshot_id",
                "id",
            ),
        })
        session_worktree_fk = [
            row
            for row in connection.execute("PRAGMA foreign_key_list(sessions)")
            if row[3] == "worktree_id"
        ]
        self.assertEqual(len(session_worktree_fk), 1)
        self.assertEqual(tuple(session_worktree_fk[0][2:7]), (
            "worktrees", "worktree_id", "id", "NO ACTION", "RESTRICT"
        ))
        session_associated_fk = [
            row
            for row in connection.execute("PRAGMA foreign_key_list(sessions)")
            if row[3] == "associated_worktree_id"
        ]
        self.assertEqual(len(session_associated_fk), 1)
        self.assertEqual(tuple(session_associated_fk[0][2:7]), (
            "worktrees", "associated_worktree_id", "id", "NO ACTION", "RESTRICT"
        ))
        self.assertEqual(
            connection.execute("PRAGMA user_version").fetchone()[0],
            SCHEMA_VERSION,
        )
        self.assertEqual(SCHEMA_VERSION, 47)
        self.assertEqual(V46_SCHEMA_VERSION, 46)
        self.assertEqual(PREVIOUS_SCHEMA_VERSION, 46)
        self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        store.close()

    def test_v45_worktree_bindings_backfill_session_and_fork_snapshots(self) -> None:
        workspace = self.data.parent / "workspace"
        workspace.mkdir()
        subprocess.run(["git", "init", "-q", str(workspace)], check=True)
        store = SessionStore(self.data)
        store.initialize()
        session = store.create_session(str(workspace))
        connection = store.connection
        assert connection is not None
        project_id = "project-fork-source"
        connection.execute(
            "INSERT INTO projects (id, name, workspace_root, git_repository_root, "
            "git_common_dir, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 1, 1)",
            (
                project_id,
                "Fork source",
                str(workspace),
                str(workspace),
                str(workspace / ".git"),
            ),
        )
        workspace_identity = capture_workspace_identity(workspace)
        git = DulwichGitBackend().discover(workspace)
        source_roots = (
            SessionRoot(
                id="root-primary",
                path=str(workspace_identity.path),
                repository_root=git.repository_root,
                git_dir=git.git_dir,
                git_common_dir=git.git_common_dir,
                device=workspace_identity.device,
                inode=workspace_identity.inode,
                owner=workspace_identity.owner,
            ),
        )
        source_roots_json = json.dumps(
            [root.model_dump(mode="json") for root in source_roots],
            separators=(",", ":"),
        )
        connection.execute(
            "UPDATE sessions SET project_id = ?, workspace_roots_json = ? WHERE id = ?",
            (project_id, source_roots_json, session["id"]),
        )
        connection.execute(
            """
            INSERT INTO worktree_snapshots (
                id, worktree_id, workspace_root, session_id, project_id,
                base_ref, base_commit, head, branch, checkout_branch,
                branch_ownership, dirty, staged_paths_json, unstaged_paths_json,
                untracked_paths_json, conflict_paths_json, source_fingerprint,
                artifact_path, artifact_sha256, full_patch_sha256,
                staged_patch_sha256, format_version, state, created_at,
                restored_at, updated_at
            ) VALUES (
                'fork-snapshot-valid', NULL, ?, ?, ?, 'HEAD', ?, ?, NULL, NULL,
                'none', 0, '[]', '[]', '[]', '[]', 'source-fingerprint',
                '/snapshot', ?, ?, ?, 1, 'ready', 1, NULL, 1
            )
            """,
            (
                str(workspace),
                session["id"],
                project_id,
                "0" * 40,
                "0" * 40,
                "a" * 64,
                "b" * 64,
                "c" * 64,
            ),
        )
        connection.execute(
            """
            INSERT INTO worktree_lifecycle_operations (
                scope, operation_id, state, session_id, created_at, updated_at
            ) VALUES ('session/create', 'existing-session', 'session_created', ?, 1, 1)
            """,
            (session["id"],),
        )
        connection.execute(
            """
            INSERT INTO worktree_lifecycle_operations (
                scope, operation_id, state, session_id, created_at, updated_at
            ) VALUES ('session/create', 'pending-create', 'worktree_created', 'missing-session', 1, 1)
            """
        )
        connection.execute(
            """
            INSERT INTO worktree_lifecycle_operations (
                scope, operation_id, state, session_id, snapshot_id, created_at, updated_at
            ) VALUES ('session/create', 'pending-fork', 'worktree_created', 'fork-session', 'fork-snapshot', 1, 1)
            """
        )
        connection.execute(
            """
            INSERT INTO worktree_lifecycle_operations (
                scope, operation_id, state, session_id, snapshot_id, created_at, updated_at
            ) VALUES (
                'session/create', 'pending-fork-valid', 'worktree_created',
                'fork-target-session', 'fork-snapshot-valid', 1, 1
            )
            """
        )
        connection.execute(
            """
            INSERT INTO worktree_lifecycle_operations (
                scope, operation_id, state, session_id, snapshot_id, created_at, updated_at
            ) VALUES (
                'checkpoint/fork', 'pending-checkpoint-fork', 'prepared',
                'checkpoint-target-session', 'fork-snapshot-valid', 1, 1
            )
            """
        )
        connection.execute(
            """
            INSERT INTO worktree_lifecycle_operations (
                scope, operation_id, state, session_id, snapshot_id, created_at, updated_at
            ) VALUES (
                'checkpoint/fork', 'pending-checkpoint-fork-unknown', 'prepared',
                'checkpoint-target-unknown', 'missing-checkpoint-snapshot', 1, 1
            )
            """
        )
        connection.commit()
        store.close()

        database = self.data / DATABASE_NAME
        legacy = sqlite3.connect(database)
        legacy.execute(
            "ALTER TABLE worktree_lifecycle_operations DROP COLUMN project_revision"
        )
        legacy.execute(
            "ALTER TABLE worktree_lifecycle_operations DROP COLUMN workspace_roots_json"
        )
        legacy.execute("ALTER TABLE worktrees DROP COLUMN git_dir_uid")
        legacy.execute("ALTER TABLE worktrees DROP COLUMN git_dir_inode")
        legacy.execute("ALTER TABLE worktrees DROP COLUMN git_dir_dev")
        legacy.execute("PRAGMA user_version = 45")
        legacy.commit()
        legacy.close()

        migrated = SessionStore(self.data)
        migrated.initialize()
        connection = migrated.connection
        assert connection is not None, (migrated.health_state, migrated.health_code)
        existing = connection.execute(
            """
            SELECT state, workspace_roots_json, error_code
            FROM worktree_lifecycle_operations WHERE operation_id = 'existing-session'
            """
        ).fetchone()
        pending = connection.execute(
            """
            SELECT state, workspace_roots_json, error_code
            FROM worktree_lifecycle_operations WHERE operation_id = 'pending-create'
            """
        ).fetchone()
        pending_fork = connection.execute(
            """
            SELECT state, workspace_roots_json, error_code
            FROM worktree_lifecycle_operations WHERE operation_id = 'pending-fork'
            """
        ).fetchone()
        pending_fork_valid = connection.execute(
            """
            SELECT state, workspace_roots_json, error_code
            FROM worktree_lifecycle_operations WHERE operation_id = 'pending-fork-valid'
            """
        ).fetchone()
        pending_checkpoint_fork = connection.execute(
            """
            SELECT state, workspace_roots_json, error_code
            FROM worktree_lifecycle_operations
            WHERE operation_id = 'pending-checkpoint-fork'
            """
        ).fetchone()
        pending_checkpoint_fork_unknown = connection.execute(
            """
            SELECT state, workspace_roots_json, error_code
            FROM worktree_lifecycle_operations
            WHERE operation_id = 'pending-checkpoint-fork-unknown'
            """
        ).fetchone()
        source_roots_after_migration = connection.execute(
            "SELECT workspace_roots_json FROM sessions WHERE id = ?",
            (session["id"],),
        ).fetchone()[0]
        self.assertEqual(existing["state"], "session_created")
        self.assertEqual(
            existing["workspace_roots_json"], source_roots_after_migration
        )
        self.assertEqual(pending["state"], "cleanup_required")
        self.assertEqual(pending["workspace_roots_json"], "[]")
        self.assertEqual(
            pending["error_code"], "workspace_binding_snapshot_unavailable"
        )
        self.assertEqual(pending_fork["state"], "cleanup_required")
        self.assertEqual(
            pending_fork["error_code"], "workspace_binding_snapshot_unavailable"
        )
        self.assertEqual(pending_fork_valid["state"], "worktree_created")
        self.assertEqual(pending_checkpoint_fork["state"], "prepared")
        self.assertEqual(
            pending_checkpoint_fork["workspace_roots_json"],
            pending_fork_valid["workspace_roots_json"],
        )
        self.assertEqual(
            pending_checkpoint_fork_unknown["state"], "cleanup_required"
        )
        self.assertEqual(
            pending_checkpoint_fork_unknown["error_code"],
            "workspace_binding_snapshot_unavailable",
        )
        self.assertEqual(
            pending_fork_valid["workspace_roots_json"], source_roots_after_migration
        )
        migrated_roots = tuple(
            SessionRoot.model_validate(value)
            for value in json.loads(pending_fork_valid["workspace_roots_json"])
        )
        self.assertIsNotNone(migrated_roots[0].git_dir_identity)
        self.assertIsNotNone(migrated_roots[0].git_common_identity)
        verify_session_roots(migrated_roots)
        (workspace / ".git").rename(workspace / ".git-old")
        subprocess.run(["git", "init", "-q", str(workspace)], check=True)
        with self.assertRaises(WorkspacePathError):
            verify_session_roots(migrated_roots)
        self.assertEqual(
            connection.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION
        )
        migrated.close()

    def test_v9_run_index_migrates_with_overlapping_waiting_runs(self) -> None:
        database = self.data / DATABASE_NAME
        connection = sqlite3.connect(database)
        connection.executescript(V9_SCHEMA_SQL)
        connection.execute(
            "INSERT INTO sessions (id, workspace_root, created_at, updated_at) "
            "VALUES ('session-v9', '/workspace', 1, 1)"
        )
        connection.execute(
            "INSERT INTO runs (id, session_id, user_input, model_profile_json, "
            "status, created_at, updated_at) VALUES "
            "('run-v9', 'session-v9', 'legacy', '{}', 'waiting_approval', 1, 1)"
        )
        connection.execute(
            "INSERT INTO runs (id, session_id, user_input, model_profile_json, "
            "status, created_at, updated_at) VALUES "
            "('run-v9-second', 'session-v9', 'legacy second', '{}', "
            "'waiting_approval', 2, 2)"
        )
        connection.execute(
            "UPDATE runs SET extension_snapshot_json = ? WHERE id = 'run-v9'",
            (json.dumps({
                "schemaVersion": 1,
                "extensionContractVersion": 1,
                "plugins": [],
                "skillCatalogHash": "",
                "mcpConfigHash": "",
            }),),
        )
        connection.execute(f"PRAGMA user_version = {V9_SCHEMA_VERSION}")
        connection.commit()
        connection.close()
        os.chmod(database, 0o600)

        store = Database(self.data)
        store.initialize()

        self.assertEqual(store.health(), {"state": "ready"})
        connection = store.connection()
        assert connection is not None
        self.assertEqual(
            connection.execute("PRAGMA user_version").fetchone()[0],
            SCHEMA_VERSION,
        )
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'index' AND sql IS NOT NULL"
            )
        }
        self.assertNotIn("one_active_run", indexes)
        self.assertIn("one_active_run_per_session", indexes)
        index_sql = connection.execute(
            "SELECT sql FROM sqlite_master "
            "WHERE type = 'index' AND name = 'one_active_run_per_session'"
        ).fetchone()[0]
        self.assertNotIn("waiting_approval", index_sql)
        self.assertEqual(
            [
                row[0]
                for row in connection.execute(
                    "SELECT status FROM runs WHERE session_id = 'session-v9' "
                    "ORDER BY creation_seq"
                )
            ],
            ["waiting_approval", "waiting_approval"],
        )
        store.close()

    def test_v10_schema_adds_manual_mcp_table(self) -> None:
        database = self.data / DATABASE_NAME
        connection = sqlite3.connect(database)
        connection.executescript(V10_SCHEMA_SQL)
        connection.execute(f"PRAGMA user_version = {V10_SCHEMA_VERSION}")
        connection.commit()
        connection.close()
        os.chmod(database, 0o600)

        store = Database(self.data)
        store.initialize()

        connection = store.connection()
        self.assertEqual(
            connection.execute("PRAGMA user_version").fetchone()[0],
            SCHEMA_VERSION,
        )
        self.assertIsNotNone(
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                "AND name = 'manual_mcp_servers'"
            ).fetchone()
        )
        store.close()

    def test_v11_schema_adds_skill_state_table(self) -> None:
        database = self.data / DATABASE_NAME
        connection = sqlite3.connect(database)
        connection.executescript(V11_SCHEMA_SQL)
        connection.execute(f"PRAGMA user_version = {V11_SCHEMA_VERSION}")
        connection.commit()
        connection.close()
        os.chmod(database, 0o600)

        store = Database(self.data)
        store.initialize()

        connection = store.connection()
        self.assertEqual(
            connection.execute("PRAGMA user_version").fetchone()[0],
            SCHEMA_VERSION,
        )
        self.assertIsNotNone(
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                "AND name = 'skill_states'"
            ).fetchone()
        )
        store.close()

    def test_v12_schema_adds_approval_mode_and_review_metadata(self) -> None:
        database = self.data / DATABASE_NAME
        connection = sqlite3.connect(database)
        connection.executescript(V12_SCHEMA_SQL)
        connection.execute(f"PRAGMA user_version = {V12_SCHEMA_VERSION}")
        connection.commit()
        connection.close()
        os.chmod(database, 0o600)

        store = Database(self.data)
        store.initialize()

        connection = store.connection()
        self.assertEqual(
            connection.execute("PRAGMA user_version").fetchone()[0],
            SCHEMA_VERSION,
        )
        runs_columns = {
            row[1]: row[4]
            for row in connection.execute("PRAGMA table_info(runs)")
        }
        approval_columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(approvals)")
        }
        self.assertEqual(runs_columns["approval_mode"], "'manual'")
        self.assertIn("review_json", approval_columns)
        store.close()

    def test_session_active_run_index_excludes_waiting_approval(self) -> None:
        store = SessionStore(self.data)
        store.initialize()
        workspace = self.data.parent / "workspace"
        workspace.mkdir()
        session = store.create_session(str(workspace))
        connection = store.connection
        assert connection is not None
        connection.execute(
            "INSERT INTO runs (id, session_id, user_input, model_profile_json, "
            "status, created_at, updated_at) VALUES "
            "('run-waiting-1', ?, 'first', '{}', 'waiting_approval', 1, 1)",
            (session["id"],),
        )
        connection.execute(
            "INSERT INTO runs (id, session_id, user_input, model_profile_json, "
            "status, created_at, updated_at) VALUES "
            "('run-waiting-2', ?, 'second', '{}', 'waiting_approval', 1, 1)",
            (session["id"],),
        )
        self.assertEqual(
            connection.execute(
                "SELECT COUNT(*) FROM runs WHERE session_id = ?",
                (session["id"],),
            ).fetchone()[0],
            2,
        )
        store.close()

    def test_existing_future_database_is_rejected_without_mutation(self) -> None:
        database = self.data / DATABASE_NAME
        connection = sqlite3.connect(database)
        connection.executescript(SCHEMA_SQL)
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
        connection.commit()
        connection.close()
        os.chmod(database, 0o600)

        store = SessionStore(self.data)
        store.initialize()
        self.assertEqual(
            store.health(),
            {"state": "health_only", "code": "schema_revision_unsupported"},
        )
        check = sqlite3.connect(database)
        self.assertEqual(check.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION + 1)
        self.assertIsNotNone(
            check.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'worktree_snapshots'"
            ).fetchone()
        )
        check.close()
        store.close()

    def test_existing_database_with_unsupported_version_is_not_modified(self) -> None:
        database = self.data / DATABASE_NAME
        connection = sqlite3.connect(database)
        connection.executescript(
            f"CREATE TABLE legacy_marker (value TEXT); "
            f"PRAGMA user_version = {SCHEMA_VERSION + 1};"
        )
        connection.close()
        os.chmod(database, 0o600)

        store = SessionStore(self.data)
        store.initialize()

        self.assertEqual(
            store.health(),
            {"state": "health_only", "code": "schema_revision_unsupported"},
        )
        check = sqlite3.connect(database)
        self.assertEqual(
            check.execute("PRAGMA user_version").fetchone()[0],
            SCHEMA_VERSION + 1,
        )
        self.assertEqual(
            check.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'legacy_marker'"
            ).fetchone(),
            ("legacy_marker",),
        )
        self.assertEqual(check.execute("PRAGMA table_info(legacy_marker)").fetchall()[0][1], "value")
        check.close()

    def test_existing_database_without_version_is_rejected(self) -> None:
        database = self.data / DATABASE_NAME
        connection = sqlite3.connect(database)
        connection.execute("CREATE TABLE legacy_marker (value TEXT)")
        connection.close()
        os.chmod(database, 0o600)

        store = SessionStore(self.data)
        store.initialize()

        self.assertEqual(
            store.health(),
            {"state": "health_only", "code": "schema_revision_unsupported"},
        )
        check = sqlite3.connect(database)
        self.assertEqual(check.execute("PRAGMA user_version").fetchone()[0], 0)
        self.assertEqual(
            check.execute("SELECT COUNT(*) FROM sqlite_master WHERE type = 'table'").fetchone()[0],
            1,
        )
        check.close()

    def test_v2_context_lineage_and_model_attempt_binding_survive_migration(
        self,
    ) -> None:
        database = self.data / DATABASE_NAME
        connection = sqlite3.connect(database)
        connection.executescript(V2_SCHEMA_SQL)
        snapshot_id, snapshot_json, plan_json = _seed_context_lineage(
            connection, str(self.data.parent / "workspace")
        )
        connection.execute("PRAGMA user_version = 2")
        connection.commit()
        connection.close()
        os.chmod(database, 0o600)

        store = SessionStore(self.data)
        store.initialize()

        self.assertEqual(store.health(), {"state": "ready"})
        connection = store.connection
        assert connection is not None
        for column in (
            "retrieval_snapshot_id",
            "inventory_snapshot_id",
            "index_snapshot_id",
        ):
            row = next(
                row for row in connection.execute("PRAGMA table_info(context_plans)")
                if row[1] == column
            )
            self.assertEqual(row[3], 0)
        _assert_context_foreign_keys(connection)
        self.assertEqual(
            connection.execute(
                "SELECT context_snapshot_id FROM model_attempts WHERE id = 'attempt-v2'"
            ).fetchone()[0],
            snapshot_id,
        )
        stored_snapshot = connection.execute(
            "SELECT snapshot_json FROM context_snapshots WHERE id = ?",
            (snapshot_id,),
        ).fetchone()[0]
        self.assertEqual(
            json.loads(stored_snapshot)["$eidosBlob"]["kind"],
            "context-snapshot",
        )
        self.assertEqual(
            store.context_snapshot_repository().read(snapshot_id).model_dump_json(),
            snapshot_json,
        )
        self.assertEqual(
            connection.execute(
                "SELECT plan_json FROM context_plans LIMIT 1"
            ).fetchone()[0],
            plan_json,
        )
        self.assertIsNotNone(
            store.context_snapshot_repository().read_for_model_attempt("attempt-v2")
        )
        self.assertEqual(
            [tuple(row) for row in connection.execute(
                "SELECT run_id, retrieval_snapshot_id "
                "FROM run_repository_retrievals"
            ).fetchall()],
            [("run-v2", "retrieval-v2")],
        )

        next_workspace = self.data.parent / "workspace-next"
        next_workspace.mkdir()
        session = store.create_session(str(next_workspace))
        run, _item = store.create_run(session["id"], "continue after migration")
        RuntimeEngine(
            store,
            ScriptedModel([ModelResponse(text="done")]),
            lambda _message: None,
        ).run(run["id"], threading.Event())
        attempts = store.read_model_attempts(run["id"])
        self.assertEqual(len(attempts), 1)
        self.assertIsNotNone(attempts[0]["contextSnapshotId"])
        _assert_context_foreign_keys(connection)
        store.close()

    def test_v4_to_v5_adds_model_attempt_diagnostics_without_losing_rows(self) -> None:
        database = self.data / DATABASE_NAME
        connection = sqlite3.connect(database)
        connection.executescript(V4_SCHEMA_SQL)
        _seed_context_lineage(connection, str(self.data.parent / "workspace"))
        connection.execute("PRAGMA user_version = 4")
        connection.commit()
        connection.close()
        os.chmod(database, 0o600)

        store = SessionStore(self.data)
        store.initialize()

        self.assertEqual(store.health(), {"state": "ready"})
        connection = store.connection
        assert connection is not None
        self.assertEqual(
            connection.execute("PRAGMA user_version").fetchone()[0],
            SCHEMA_VERSION,
        )
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(model_attempts)")
        }
        self.assertTrue(EXPECTED_COLUMNS["model_attempts"].issubset(columns))
        attempt = connection.execute(
            "SELECT tool_call_count, response_text_bytes, protocol_diagnostics_json "
            "FROM model_attempts WHERE id = 'attempt-v2'"
        ).fetchone()
        self.assertEqual(tuple(attempt), (0, 0, None))
        store.close()

    def test_v7_to_v8_persists_payload_kind_and_backfills_only_legacy_custom(self) -> None:
        database = self.data / DATABASE_NAME
        connection = sqlite3.connect(database)
        connection.executescript(V7_SCHEMA_SQL)
        connection.executemany(
            "INSERT INTO sessions (id, workspace_root, created_at, updated_at) "
            "VALUES (?, '/workspace', 1, 1)",
            [("session-payload",)],
        )
        connection.execute(
            "INSERT INTO runs (id, session_id, user_input, model_profile_json, "
            "status, created_at, updated_at) VALUES "
            "('run-payload', 'session-payload', 'goal', '{}', 'succeeded', 1, 1)"
        )
        connection.executemany(
            "INSERT INTO items (id, session_id, run_id, ordinal, kind, status, "
            "created_at, completed_at) VALUES (?, 'session-payload', "
            "'run-payload', ?, 'tool_call', 'completed', 1, 1)",
            [("item-custom", 0), ("item-function", 1)],
        )
        connection.executemany(
            "INSERT INTO tool_calls (id, item_id, model_step_index, batch_order, "
            "provider_call_id, tool_name, status, arguments_json, started_at) "
            "VALUES (?, ?, 0, 0, ?, ?, 'completed', ?, 1)",
            [
                (
                    "tool-custom", "item-custom", "call-custom", "apply_patch",
                    json.dumps({"kind": "custom", "input": "*** Begin Patch"}),
                ),
                (
                    "tool-function", "item-function", "call-function", "read_file",
                    json.dumps({"kind": "custom", "input": "hello"}),
                ),
            ],
        )
        connection.execute(f"PRAGMA user_version = {V7_SCHEMA_VERSION}")
        connection.commit()
        connection.close()
        os.chmod(database, 0o600)

        store = SessionStore(self.data)
        store.initialize()

        connection = store.connection
        assert connection is not None
        rows = connection.execute(
            "SELECT tool_name, payload_kind FROM tool_calls ORDER BY creation_seq"
        ).fetchall()
        self.assertEqual([tuple(row) for row in rows], [
            ("apply_patch", "custom"),
            ("read_file", "function"),
        ])
        store.close()

    def test_v1_to_v2_to_v3_preserves_final_context_foreign_keys(self) -> None:
        database = self.data / DATABASE_NAME
        v1_schema = V2_SCHEMA_SQL.replace(
            "    repository_map_json TEXT,\n", ""
        ).replace(
            "         AND repository_map_json IS NOT NULL", ""
        )
        connection = sqlite3.connect(database)
        connection.executescript(v1_schema)
        snapshot_id, _snapshot_json, _plan_json = _seed_context_lineage(
            connection, str(self.data.parent / "workspace")
        )
        connection.execute(f"PRAGMA user_version = {LEGACY_SCHEMA_VERSION}")
        connection.commit()
        connection.close()
        os.chmod(database, 0o600)

        store = SessionStore(self.data)
        store.initialize()

        self.assertEqual(store.health(), {"state": "ready"})
        connection = store.connection
        assert connection is not None
        self.assertEqual(
            connection.execute("PRAGMA user_version").fetchone()[0],
            SCHEMA_VERSION,
        )
        self.assertEqual(
            connection.execute(
                "SELECT context_snapshot_id FROM model_attempts WHERE id = 'attempt-v2'"
            ).fetchone()[0],
            snapshot_id,
        )
        _assert_context_foreign_keys(connection)
        store.close()

    def test_invalid_v2_shape_fails_migration_without_mutation(self) -> None:
        database = self.data / DATABASE_NAME
        connection = sqlite3.connect(database)
        connection.execute("CREATE TABLE legacy_marker (value TEXT)")
        connection.execute(f"PRAGMA user_version = {V5_SCHEMA_VERSION}")
        connection.commit()
        connection.close()
        os.chmod(database, 0o600)

        store = SessionStore(self.data)
        store.initialize()

        self.assertEqual(
            store.health(),
            {"state": "health_only", "code": "schema_migration_failed"},
        )
        check = sqlite3.connect(database)
        self.assertEqual(
            check.execute("PRAGMA user_version").fetchone()[0],
            V5_SCHEMA_VERSION,
        )
        self.assertIsNotNone(
            check.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = 'legacy_marker'
                """
            ).fetchone()
        )
        self.assertEqual(
            check.execute("PRAGMA table_info(legacy_marker)").fetchall()[0][1],
            "value",
        )
        check.close()
        store.close()

    def test_repositories_share_one_database_manager(self) -> None:
        store = SessionStore(self.data)
        store.initialize()

        repositories = (
            store._sessions,
            store._runs,
            store._execution,
            store._extensions,
            store._context,
        )
        self.assertTrue(all(repository is not None for repository in repositories))
        self.assertTrue(
            all(repository.database is store._database for repository in repositories if repository)
        )
        store.close()


if __name__ == "__main__":
    unittest.main()

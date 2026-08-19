from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
import threading


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eidos_runtime.db.schema import (  # noqa: E402
    LEGACY_SCHEMA_VERSION,
    PREVIOUS_SCHEMA_VERSION,
    SCHEMA_SQL,
    SCHEMA_VERSION,
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
from eidos_runtime.runtime.state_machine import (  # noqa: E402
    RunStatus,
    RuntimeState,
    SegmentStatus,
)


EXPECTED_TABLES = {
    "sessions",
    "runs",
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
    "compact_summaries",
    "input_mailbox",
    "async_operations",
    "event_outbox",
    "rule_resolution_snapshots",
    "run_resolution_snapshots",
    "step_resolution_snapshots",
    "repository_snapshots",
    "repository_files",
    "repository_directories",
    "repository_index_generations",
    "repository_parsed_files",
    "repository_symbols",
    "repository_imports",
    "repository_references",
    "repository_chunks",
    "repository_diagnostics",
    "repository_fts",
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
    "worktrees",
    "worktree_lifecycle_operations",
    "session_handoff_operations",
    "runtime_settings",
    "worktree_snapshots",
}

EXPECTED_COLUMNS = {
    "sessions": {
        "workspace_dev", "workspace_inode", "workspace_uid", "worktree_id",
        "associated_worktree_id",
    },
    "worktrees": {"checkout_branch", "last_used_at"},
    "session_handoff_operations": {
        "scope", "operation_id", "state", "session_id", "project_id",
        "source_mode", "target_mode", "source_root", "target_root",
        "source_common_dir", "target_common_dir", "associated_worktree_id",
        "target_worktree_new", "source_head", "source_fingerprint",
        "target_head", "target_fingerprint", "error_code",
    },
    "compact_summaries": {"summary_metadata_json"},
    "items": {"incomplete"},
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
    },
    "steps": {
        "resolution_snapshot_id",
        "tool_snapshot_json",
        "tool_set_hash",
        "progress_signature_json",
    },
    "tool_calls": {
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
    },
    "worktree_lifecycle_operations": {
        "expected_head", "snapshot_id", "snapshot_head", "snapshot_fingerprint"
    },
    "worktree_snapshots": {
        "id", "worktree_id", "workspace_root", "session_id", "project_id", "base_ref",
        "base_commit", "head", "artifact_path", "artifact_sha256", "state",
    },
    "runtime_settings": {
        "automatic_cleanup", "managed_worktree_limit", "updated_at"
    },
    "model_attempts": {
        "lease_id", "wire_api", "model_id", "request_timeout",
        "retry_decision_json",
        "context_snapshot_id",
    },
    "repository_snapshots": {"repository_map_json"},
    "tool_attempts": {
        "tool_call_id", "ordinal", "sandbox_type", "sandbox_requested",
        "effective_permissions_json", "profile_hash", "escalation_reason",
        "status", "result_code",
    },
    "projects": {"name"},
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
    connection.execute(
        """
        INSERT INTO repository_retrieval_snapshots (
            id, run_id, inventory_snapshot_id, index_snapshot_id,
            snapshot_hash, snapshot_json, created_at
        ) VALUES ('retrieval-v2', 'run-v2', 'inventory-v2', 'index-v2',
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
        legacy_schema = SCHEMA_SQL.replace(
            "'queued', 'running', 'waiting_approval', 'finalizing',",
            "'queued', 'running', 'waiting_approval', 'waiting_user_input', 'finalizing',",
            1,
        ).replace(
            "'queued', 'running', 'completed', 'failed', 'canceled'",
            "'queued', 'running', 'waiting_user_input', 'completed', 'failed', 'canceled'",
            1,
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
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
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
        }
        indexes = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index' AND sql IS NOT NULL"
            )
        }
        self.assertEqual(tables, EXPECTED_TABLES)
        self.assertEqual(
            indexes,
            {
                "one_active_run",
                "one_pending_approval_per_item",
                "one_pending_approval_per_run",
                "one_running_tool_attempt_per_tool_call",
                "one_running_segment_per_run",
                "one_active_segment_per_run",
                "one_running_step_per_run",
                "one_running_attempt_per_step",
                "one_running_finalization_attempt_per_run",
                "one_running_async_operation_per_operation_id",
                "one_pending_outbox_delivery_per_event",
                "repository_snapshots_last_complete",
                "repository_index_generations_snapshot",
                "repository_symbols_name",
                "repository_diagnostics_generation",
                "checkpoints_run_boundary",
                "run_revisions_source",
                "review_comments_session_path",
                "worktrees_project_state",
                "worktrees_project_ownership",
                "sessions_worktree_id",
                "worktree_lifecycle_operations_state",
                "worktree_lifecycle_operations_session",
                "sessions_associated_worktree_id",
                "session_handoff_operations_state",
                "session_handoff_operations_session",
                "worktree_snapshots_latest",
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
        self.assertEqual(SCHEMA_VERSION, 4)
        self.assertEqual(PREVIOUS_SCHEMA_VERSION, 3)
        self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        store.close()

    def test_existing_v22_database_is_rejected_without_mutation(self) -> None:
        database = self.data / DATABASE_NAME
        connection = sqlite3.connect(database)
        connection.executescript(SCHEMA_SQL)
        connection.execute("PRAGMA user_version = 22")
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
        self.assertEqual(check.execute("PRAGMA user_version").fetchone()[0], 22)
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
        self.assertEqual(
            connection.execute(
                "SELECT snapshot_json FROM context_snapshots WHERE id = ?",
                (snapshot_id,),
            ).fetchone()[0],
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
        connection.execute(f"PRAGMA user_version = {PREVIOUS_SCHEMA_VERSION}")
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
            PREVIOUS_SCHEMA_VERSION,
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

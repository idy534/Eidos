from __future__ import annotations

import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eidos_runtime.db.schema import SCHEMA_VERSION  # noqa: E402
from eidos_runtime.db.storage import DATABASE_NAME, SessionStore  # noqa: E402


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
}

EXPECTED_COLUMNS = {
    "sessions": {"workspace_dev", "workspace_inode", "workspace_uid"},
    "items": {"incomplete"},
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
    "steps": {"tool_snapshot_json", "tool_set_hash", "progress_signature_json"},
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
    "tool_attempts": {
        "tool_call_id", "ordinal", "sandbox_type", "sandbox_requested",
        "effective_permissions_json", "profile_hash", "escalation_reason",
        "status", "result_code",
    },
}


class StorageSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-schema-")
        self.data = Path(self.temporary.name) / "data"
        self.data.mkdir(mode=0o700)

    def tearDown(self) -> None:
        self.temporary.cleanup()

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
            },
        )
        for table, expected in EXPECTED_COLUMNS.items():
            columns = {
                row[1]
                for row in connection.execute(f"PRAGMA table_info({table})")
            }
            self.assertTrue(expected <= columns, (table, expected - columns))
        self.assertEqual(
            connection.execute("PRAGMA user_version").fetchone()[0],
            SCHEMA_VERSION,
        )
        self.assertEqual(connection.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
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

    def test_previous_revision_is_rejected_without_mutation(self) -> None:
        database = self.data / DATABASE_NAME
        connection = sqlite3.connect(database)
        connection.execute("CREATE TABLE legacy_marker (value TEXT)")
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION - 1}")
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
        self.assertEqual(
            check.execute("PRAGMA user_version").fetchone()[0],
            SCHEMA_VERSION - 1,
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

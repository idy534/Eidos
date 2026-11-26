from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from eidos_runtime.db.schema import SCHEMA_VERSION, V57_SCHEMA_SQL, V57_SCHEMA_VERSION
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.db.tool_attempt_sandbox_migration import (
    ONE_RUNNING_TOOL_ATTEMPT_INDEX_SQL,
    V57_TOOL_ATTEMPTS_TABLE_SQL,
    migrate_tool_attempt_sandbox,
)


class ExecutionAttemptPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-attempts-")
        root = Path(self.temporary.name)
        self.workspace = root / "workspace"
        self.data = root / "data"
        self.workspace.mkdir()
        self.data.mkdir(mode=0o700)
        self.store = SessionStore(self.data)
        self.store.initialize()

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def create_tool_item(self) -> tuple[dict[str, object], dict[str, object]]:
        session = self.store.create_session(str(self.workspace))
        run, _item = self.store.enqueue_run(session["id"], "run command")
        claimed = self.store.claim_next_run_committed()
        assert claimed is not None
        self.store.increment_model_step(run["id"])
        item = self.store.create_tool_item(
            run["id"],
            1,
            0,
            "provider-call",
            "run_shell",
            '{"command":"true"}',
        )
        return run, item

    def test_completed_attempt_retains_effective_profile_and_result(self) -> None:
        _run, item = self.create_tool_item()
        profile_hash = "a" * 64
        permissions = {"networkEnabled": False, "entries": []}

        self.store.record_tool_attempt(
            item["id"],
            ordinal=0,
            sandbox_type="windows_mxc",
            sandbox_requested=True,
            effective_permissions=permissions,
            profile_hash=profile_hash,
            escalation_reason=None,
            status="running",
        )
        self.store.record_tool_attempt(
            item["id"],
            ordinal=0,
            sandbox_type="windows_mxc",
            sandbox_requested=True,
            effective_permissions=permissions,
            profile_hash=profile_hash,
            escalation_reason=None,
            status="completed",
            result_code="ok",
        )

        row = self.store.connection.execute(
            "SELECT * FROM tool_attempts"
        ).fetchone()
        self.assertEqual(row["sandbox_type"], "windows_mxc")
        self.assertEqual(row["status"], "completed")
        self.assertEqual(row["profile_hash"], profile_hash)
        self.assertEqual(json.loads(row["effective_permissions_json"]), permissions)
        self.assertEqual(row["result_code"], "ok")

    def test_restart_marks_running_attempt_uncertain_and_requires_reconciliation(self) -> None:
        run, item = self.create_tool_item()
        self.store.begin_approval(
            item["id"],
            "unsandboxed",
            None,
            request={"executionMode": "unsandboxed"},
            attempt_ordinal=1,
            approval_kind="escalated",
        )
        self.store.resolve_approval(item["id"], "approve", None)
        self.store.begin_durable_intent(
            item["id"], preconditions={"sandbox": "none"}
        )
        self.store.record_tool_attempt(
            item["id"],
            ordinal=1,
            sandbox_type="none",
            sandbox_requested=False,
            effective_permissions={"networkEnabled": False},
            profile_hash=None,
            escalation_reason="filesystem denied",
            status="running",
        )

        self.store.close()
        self.store = SessionStore(self.data)
        self.store.initialize()

        attempt = self.store.connection.execute(
            "SELECT status, result_code FROM tool_attempts"
        ).fetchone()
        recovered = self.store.read_run(run["id"])
        reconciliation_required = self.store.connection.execute(
            "SELECT reconciliation_required FROM runs WHERE id = ?",
            (run["id"],),
        ).fetchone()[0]
        self.assertEqual(tuple(attempt), ("uncertain", "runtime_interrupted"))
        self.assertEqual(recovered["status"], "interrupted")
        self.assertEqual(reconciliation_required, 1)
        self.assertTrue(recovered["sideEffectsMayExist"])

    def test_restart_invalidates_pending_escalation_approval(self) -> None:
        run, item = self.create_tool_item()
        self.store.begin_approval(
            item["id"],
            "unsandboxed",
            None,
            request={"executionMode": "unsandboxed"},
            attempt_ordinal=1,
            approval_kind="escalated",
        )

        self.store.close()
        self.store = SessionStore(self.data)
        self.store.initialize()

        approval = self.store.connection.execute(
            "SELECT status, attempt_ordinal, approval_kind FROM approvals"
        ).fetchone()
        self.assertEqual(tuple(approval), ("invalidated", 1, "escalated"))
        self.assertEqual(self.store.read_run(run["id"])["status"], "interrupted")

    def test_restart_migrates_v57_tool_attempts(self) -> None:
        self.store.connection.executescript(
            "DROP TABLE tool_attempts;\n"
            + V57_TOOL_ATTEMPTS_TABLE_SQL
            + ONE_RUNNING_TOOL_ATTEMPT_INDEX_SQL
            + f"PRAGMA user_version = {V57_SCHEMA_VERSION};"
        )
        self.store.close()
        self.store = SessionStore(self.data)
        self.store.initialize()
        self.assertEqual(
            self.store.connection.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION,
        )
        self.assertIn(
            "windows_mxc",
            self.store.connection.execute(
                "SELECT sql FROM sqlite_master WHERE name = 'tool_attempts'"
            ).fetchone()[0],
        )


class ToolAttemptSandboxMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:", isolation_level=None)
        self.addCleanup(self.connection.close)
        self.connection.executescript(
            "PRAGMA foreign_keys = ON;\n"
            + V57_SCHEMA_SQL
            + f"""
PRAGMA user_version = {V57_SCHEMA_VERSION};
INSERT INTO sessions (id, workspace_root, created_at, updated_at)
    VALUES ('session', '/workspace', 1, 1);
INSERT INTO runs (id, session_id, user_input, model_profile_json, status, created_at, updated_at)
    VALUES ('run', 'session', 'shell', '{{}}', 'running', 1, 1);
INSERT INTO items (id, session_id, run_id, ordinal, kind, status, created_at)
    VALUES ('item', 'session', 'run', 0, 'tool_call', 'in_progress', 1);
INSERT INTO tool_calls (id, item_id, model_step_index, batch_order, provider_call_id,
                        tool_name, arguments_json, status, started_at)
    VALUES ('tool', 'item', 1, 0, 'provider-call', 'run_shell', '{{}}', 'running', 1);
INSERT INTO tool_attempts
    (creation_seq, id, tool_call_id, ordinal, sandbox_type, sandbox_requested, effective_permissions_json,
     profile_hash, escalation_reason, status, started_at, completed_at, result_code)
VALUES (3, 'attempt-0', 'tool', 0, 'macos_seatbelt', 1, '{{}}', 'hash', NULL, 'uncertain', 1, 2, 'interrupted');
INSERT INTO tool_attempts
    (creation_seq, id, tool_call_id, ordinal, sandbox_type, sandbox_requested, effective_permissions_json,
     status, started_at)
VALUES (7, 'attempt-1', 'tool', 1, 'none', 0, '{{}}', 'completed', 3);
DELETE FROM tool_attempts WHERE id = 'attempt-1';
"""
        )

    def attempts(self) -> list[tuple]:
        return self.connection.execute("SELECT * FROM tool_attempts ORDER BY creation_seq").fetchall()

    def insert_attempt(self, sandbox_type: str, *, ordinal: int, attempt_id: str) -> int | None:
        return self.connection.execute(
            """
            INSERT INTO tool_attempts
                (id, tool_call_id, ordinal, sandbox_type, sandbox_requested, effective_permissions_json,
                 status, started_at)
            VALUES (?, 'tool', ?, ?, 1, '{}', 'running', 4)
            """,
            (attempt_id, ordinal, sandbox_type),
        ).lastrowid

    def test_upgrade_preserves_records_and_attempt_constraints(self) -> None:
        previous = self.attempts()
        migrate_tool_attempt_sandbox(self.connection)

        self.assertEqual(self.attempts(), previous)
        self.assertEqual(self.connection.execute("PRAGMA user_version").fetchone()[0], SCHEMA_VERSION)
        self.assertEqual(self.connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.assertEqual(self.insert_attempt("windows_mxc", ordinal=1, attempt_id="mxc"), 8)
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute("DELETE FROM tool_calls WHERE id = 'tool'")
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute("UPDATE tool_attempts SET status = 'running' WHERE id = 'attempt-0'")
        with self.assertRaises(sqlite3.IntegrityError):
            self.connection.execute("UPDATE tool_attempts SET sandbox_type = 'unknown' WHERE id = 'mxc'")

    def test_index_rebuild_failure_rolls_back(self) -> None:
        previous = self.attempts()

        def deny_index_creation(action: int, name: str | None, *_details: object) -> int:
            if action == sqlite3.SQLITE_CREATE_INDEX and name == "one_running_tool_attempt_per_tool_call":
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        self.connection.set_authorizer(deny_index_creation)
        with self.assertRaises(sqlite3.DatabaseError):
            migrate_tool_attempt_sandbox(self.connection)
        self.connection.set_authorizer(None)

        self.assertEqual(self.attempts(), previous)
        self.assertEqual(self.connection.execute("PRAGMA user_version").fetchone()[0], V57_SCHEMA_VERSION)
        self.assertFalse(self.connection.in_transaction)
        with self.assertRaises(sqlite3.IntegrityError):
            self.insert_attempt("windows_mxc", ordinal=1, attempt_id="mxc")
        self.assertEqual(self.insert_attempt("macos_seatbelt", ordinal=1, attempt_id="seatbelt"), 8)


if __name__ == "__main__":
    unittest.main()

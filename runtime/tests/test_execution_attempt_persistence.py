from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from eidos_runtime.db.storage import SessionStore


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
        claimed = self.store.claim_next_run()
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
            sandbox_type="macos_seatbelt",
            sandbox_requested=True,
            effective_permissions=permissions,
            profile_hash=profile_hash,
            escalation_reason=None,
            status="running",
        )
        self.store.record_tool_attempt(
            item["id"],
            ordinal=0,
            sandbox_type="macos_seatbelt",
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
        self.assertEqual(recovered["status"], "waiting_user_input")
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


if __name__ == "__main__":
    unittest.main()

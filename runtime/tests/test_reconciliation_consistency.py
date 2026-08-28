from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.db.errors import (  # noqa: E402
    ReconciliationRequiredError,
)
from eidos_runtime.db.storage import SessionStore  # noqa: E402
from eidos_runtime.model.client import ScriptedModel, ModelResponse  # noqa: E402
from eidos_runtime.runtime.engine import RuntimeEngine  # noqa: E402


class ReconciliationConsistencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-reconciliation-")
        root = Path(self.temporary.name)
        data = root / "data"
        workspace = root / "workspace"
        data.mkdir(mode=0o700)
        workspace.mkdir()
        self.store = SessionStore(data)
        self.store.initialize()
        session = self.store.create_session(str(workspace))
        self.run, _ = self.store.create_run(session["id"], "reconcile")

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_explicit_top_level_false_wins_over_nonzero_exit_code(self) -> None:
        item = self.store.create_tool_item(
            self.run["id"], 0, 0, "shell", "run_shell", "{}"
        )
        self.store.begin_durable_intent(
            item["id"], preconditions={}, approval_required=False
        )

        self.store.complete_tool_item(
            item["id"],
            json.dumps({
                "outcome": "error",
                "code": "nonzero_exit",
                "summary": "Command failed",
                "data": {},
                "sideEffectsMayExist": True,
                "reconciliationRequired": False,
            }),
            item_status="failed",
            tool_status="failed",
        )

        self.assertFalse(self.store.side_effects_blocked(self.run["id"]))
        self.assertEqual(
            self.store.connection.execute(
                "SELECT status FROM durable_intents WHERE tool_call_id = ?",
                (item["toolCall"]["id"],),
            ).fetchone()[0],
            "completed",
        )

    def test_explicit_top_level_true_keeps_reconciliation_barrier(self) -> None:
        item = self.store.create_tool_item(
            self.run["id"], 0, 0, "shell", "run_shell", "{}"
        )

        self.store.complete_tool_item(
            item["id"],
            json.dumps({
                "outcome": "error",
                "code": "nonzero_exit",
                "summary": "Command failed",
                "data": {},
                "sideEffectsMayExist": True,
                "reconciliationRequired": True,
            }),
            item_status="failed",
            tool_status="failed",
        )

        self.assertTrue(self.store.side_effects_blocked(self.run["id"]))

    def test_missing_reconciliation_field_keeps_conservative_barrier(self) -> None:
        item = self.store.create_tool_item(
            self.run["id"], 0, 0, "shell", "run_shell", "{}"
        )

        self.store.complete_tool_item(
            item["id"],
            json.dumps({
                "outcome": "error",
                "code": "nonzero_exit",
                "summary": "Command failed",
                "data": {},
                "sideEffectsMayExist": True,
            }),
            item_status="failed",
            tool_status="failed",
        )

        self.assertTrue(self.store.side_effects_blocked(self.run["id"]))

    def test_reconciliation_barrier_rolls_back_success_completion(self) -> None:
        step_index = self.store.increment_model_step(self.run["id"])
        assistant = self.store.create_assistant_item(self.run["id"], step_index)
        connection = self.store.connection
        connection.execute(
            "UPDATE runs SET reconciliation_required = 1, side_effects_may_exist = 1 "
            "WHERE id = ?",
            (self.run["id"],),
        )
        connection.commit()
        event_count = connection.execute(
            "SELECT COUNT(*) FROM events WHERE run_id = ?",
            (self.run["id"],),
        ).fetchone()[0]

        with self.assertRaisesRegex(
            ReconciliationRequiredError, "reconciliation_required"
        ):
            self.store.complete_assistant_and_run_committed(
                assistant["id"], self.run["id"]
            )

        self.assertEqual(self.store.read_run(self.run["id"])["status"], "running")
        self.assertEqual(
            self.store.read_item(assistant["id"])["status"], "in_progress"
        )
        self.assertEqual(
            connection.execute(
                "SELECT status FROM execution_segments WHERE run_id = ?",
                (self.run["id"],),
            ).fetchone()[0],
            "running",
        )
        self.assertEqual(
            connection.execute(
                "SELECT COUNT(*) FROM events WHERE run_id = ?",
                (self.run["id"],),
            ).fetchone()[0],
            event_count,
        )

    def test_engine_interrupts_instead_of_succeeding_behind_barrier(self) -> None:
        connection = self.store.connection
        connection.execute(
            "UPDATE runs SET reconciliation_required = 1, side_effects_may_exist = 1 "
            "WHERE id = ?",
            (self.run["id"],),
        )
        connection.commit()

        RuntimeEngine(
            self.store,
            ScriptedModel([ModelResponse(text="answer")]),
            lambda _message: None,
        ).run(self.run["id"], threading.Event())

        persisted = self.store.read_run(self.run["id"])
        self.assertEqual(persisted["status"], "interrupted")
        self.assertTrue(persisted["reconciliationRequired"])
        self.assertNotEqual(persisted["status"], "succeeded")

    def test_new_successful_read_clears_barrier_before_success_completion(self) -> None:
        uncertain = self.store.create_tool_item(
            self.run["id"], 0, 0, "shell", "run_shell", "{}"
        )
        self.store.complete_tool_item(
            uncertain["id"],
            json.dumps({
                "outcome": "error",
                "code": "outcome_unknown",
                "summary": "Command outcome is unknown",
                "data": {},
                "sideEffectsMayExist": True,
                "reconciliationRequired": True,
            }),
            item_status="failed",
            tool_status="failed",
        )
        self.assertTrue(self.store.side_effects_blocked(self.run["id"]))

        read_step = self.store.increment_model_step(self.run["id"])
        read = self.store.create_tool_item(
            self.run["id"], read_step, 0, "read", "read_file", "{}"
        )
        self.store.complete_tool_item(
            read["id"],
            json.dumps({
                "outcome": "success",
                "code": "ok",
                "summary": "File observed",
                "data": {},
                "sideEffectsMayExist": False,
                "reconciliationRequired": False,
            }),
        )
        self.store.complete_current_step(self.run["id"], "completed")
        self.assertFalse(self.store.side_effects_blocked(self.run["id"]))

        final_step = self.store.increment_model_step(self.run["id"])
        assistant = self.store.create_assistant_item(self.run["id"], final_step)
        self.store.complete_current_step(self.run["id"], "completed")
        _, completed = self.store.complete_assistant_and_run(
            assistant["id"], self.run["id"]
        )

        self.assertEqual(completed["status"], "succeeded")
        self.assertFalse(completed.get("reconciliationRequired", False))

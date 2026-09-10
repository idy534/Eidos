from __future__ import annotations

import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch as mock_patch

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel
from eidos_runtime.runtime.loop import RuntimeLoop


class LoopRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-loop-recovery-")
        root = Path(self.temporary.name)
        data = root / "data"
        workspace = root / "workspace"
        data.mkdir(mode=0o700)
        workspace.mkdir()
        (workspace / "README.md").write_text("fixture\n", encoding="utf-8")
        self.store = SessionStore(data)
        self.store.initialize()
        self.session = self.store.create_session(str(workspace))

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_unresolved_shell_stops_after_three_distinct_read_rounds(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "Inspect failed test evidence")
        original = self.store.create_tool_item(run["id"], 0, 0, "original-shell", "run_shell", "{}")
        self.store.begin_durable_intent(original["id"], preconditions={}, approval_required=False)
        self.store.complete_tool_item(original["id"], json.dumps({
            "outcome": "error", "code": "output_capture_failed",
            "summary": "Output scan failed", "data": {},
            "sideEffectsMayExist": True, "reconciliationRequired": True,
        }), item_status="failed", tool_status="completed")
        workspace = Path(self.temporary.name) / "workspace"
        for index in range(3):
            (workspace / f"evidence-{index}.txt").write_text(f"evidence {index}\n")
        model = ScriptedModel([
            ModelResponse(tool_calls=(ModelToolCall(
                f"read-{index}", "read_file", {"path": f"evidence-{index}.txt"}
            ),)) for index in range(3)
        ] + [ModelResponse(text="The output remains incomplete; test totals are unverified.")])

        RuntimeLoop(self.store, model, lambda _message: None).run(run["id"], threading.Event())

        stopped = self.store.read_run(run["id"])
        self.assertEqual(stopped["stopReason"], "reconciliation_required")
        self.assertTrue(stopped["reconciliationRequired"])
        self.assertEqual(stopped["modelStepCount"], 3)
        self.assertEqual(len(model.contexts), 4)
        state = " ".join(str(part.get("content", "")) for part in model.contexts[0])
        self.assertIn("original-shell", state)
        self.assertIn("output_capture_failed", state)
        self.assertIn("cannot clear Shell", state)

    def test_exact_duplicate_read_gets_generic_recovery_and_can_continue(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "Inspect startup flow")
        model = ScriptedModel([
            ModelResponse(tool_calls=(
                ModelToolCall("read-1", "read_file", {"path": "README.md"}),
            )),
            ModelResponse(tool_calls=(
                ModelToolCall("read-2", "read_file", {"path": "README.md"}),
            )),
            ModelResponse(text="Inspection can continue with the evidence already collected."),
        ])

        RuntimeLoop(self.store, model, lambda _message: None).run(
            run["id"], threading.Event()
        )

        completed = self.store.read_run(run["id"])
        self.assertEqual(completed["status"], "succeeded")
        self.assertEqual(completed["modelStepCount"], 3)
        snapshot = self.store.read_session_snapshot(self.session["id"])
        listed = [
            item for item in snapshot["items"]
            if item.get("toolCall", {}).get("toolName") == "read_file"
        ]
        self.assertEqual(len(listed), 1)
        recovery = next(
            item for item in model.contexts[2]
            if item.get("sectionId") == "runtime-loop-recovery"
        )
        self.assertIn("Do not repeat the same action", str(recovery.get("content")))

    def test_ignoring_recovery_and_repeating_again_still_stops(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "Repeat forever")
        model = ScriptedModel([
            ModelResponse(tool_calls=(
                ModelToolCall(f"list-{index}", "list_files", {}),
            ))
            for index in range(3)
        ] + [ModelResponse(text="Stopped after recovery was ignored.")])

        RuntimeLoop(self.store, model, lambda _message: None).run(
            run["id"], threading.Event()
        )

        stopped = self.store.read_run(run["id"])
        self.assertEqual(stopped["status"], "stopped")
        self.assertEqual(stopped["stopReason"], "repeated_tool_call")
        self.assertEqual(stopped["modelStepCount"], 3)
        self.assertEqual(len(model.contexts), 4)

    def test_recovery_continues_when_model_chooses_a_new_investigation_path(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "Change investigation path")
        model = ScriptedModel([
            ModelResponse(tool_calls=(
                ModelToolCall("read-1", "read_file", {"path": "README.md"}),
            )),
            ModelResponse(tool_calls=(
                ModelToolCall("read-2", "read_file", {"path": "README.md"}),
            )),
            ModelResponse(tool_calls=(
                ModelToolCall("search-1", "search_text", {"query": "fixture"}),
            )),
            ModelResponse(text="The alternate search completed the investigation."),
        ])

        RuntimeLoop(self.store, model, lambda _message: None).run(
            run["id"], threading.Event()
        )

        completed = self.store.read_run(run["id"])
        self.assertEqual(completed["status"], "succeeded")
        snapshot = self.store.read_session_snapshot(self.session["id"])
        tool_names = [
            item["toolCall"]["toolName"]
            for item in snapshot["items"]
            if item.get("toolCall")
        ]
        self.assertEqual(tool_names, ["read_file", "search_text"])

    def test_shell_commits_one_completed_item(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "Wait for the command")
        completed = {
            "schemaVersion": 1,
            "toolName": "run_shell",
            "outcome": "success",
            "code": "ok",
            "summary": "Command completed",
            "data": {
                "executionStatus": "exited",
                "exitCode": 0,
                "stdout": "ready\n",
                "stderr": "",
                "truncated": False,
                "termination": "exit",
                "durationMs": 1,
                "workspaceChanged": False,
            },
            "sideEffectsMayExist": False,
            "reconciliationRequired": False,
        }
        model = ScriptedModel([
            ModelResponse(tool_calls=(ModelToolCall(
                "run-1", "run_shell", {"command": "printf ready"},
            ),)),
            ModelResponse(text="The command completed."),
        ])

        with (
            mock_patch(
                "eidos_runtime.runtime.tool_runtime.is_seatbelt_ready",
                return_value=True,
            ),
            mock_patch(
                "eidos_runtime.runtime.shell_process_manager.ShellProcessManager.start",
                return_value=completed,
            ) as start,
        ):
            RuntimeLoop(
                self.store,
                model,
                lambda _message: None,
                shell_available=True,
            ).run(run["id"], threading.Event())

        self.assertEqual(self.store.read_run(run["id"])["status"], "succeeded")
        self.assertFalse(start.call_args.kwargs["wait_for_exit"])
        snapshot = self.store.read_session_snapshot(self.session["id"])
        shell_items = [
            item for item in snapshot["items"]
            if item.get("toolCall", {}).get("toolName") == "run_shell"
        ]
        self.assertEqual(len(shell_items), 1)
        final_result = json.loads(shell_items[0]["toolCall"]["resultJson"])
        self.assertEqual(final_result["data"]["executionStatus"], "exited")


if __name__ == "__main__":
    unittest.main()

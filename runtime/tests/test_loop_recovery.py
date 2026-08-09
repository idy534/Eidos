from __future__ import annotations

from pathlib import Path
import tempfile
import threading
import unittest

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


if __name__ == "__main__":
    unittest.main()

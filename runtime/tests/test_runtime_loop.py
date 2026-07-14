from __future__ import annotations

import json
from pathlib import Path
import tempfile
import threading
import unittest


import sys

RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.model import ModelResponse, ModelToolCall, ScriptedModel  # noqa: E402
from eidos_runtime.runtime_loop import RuntimeLoop  # noqa: E402
from eidos_runtime.storage import ActiveRunError, SessionStore  # noqa: E402
from eidos_runtime.tools import ToolExecutor  # noqa: E402


class RuntimeLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(prefix="eidos-loop-")
        root = Path(self.temporary_directory.name)
        self.data_directory = root / "data"
        self.workspace = root / "workspace"
        self.data_directory.mkdir(mode=0o700)
        self.workspace.mkdir()
        (self.workspace / "hello.txt").write_text("hello from workspace\n", encoding="utf-8")
        self.store = SessionStore(self.data_directory)
        self.store.initialize()
        self.session = self.store.create_session(str(self.workspace))

    def tearDown(self) -> None:
        self.store.close()
        self.temporary_directory.cleanup()

    def test_fake_model_reads_a_real_file_then_completes_with_final_answer(self) -> None:
        run, _user_item = self.store.create_run(self.session["id"], "Read hello.txt")
        model = ScriptedModel(
            [
                ModelResponse(
                    tool_calls=(
                        ModelToolCall("call-1", "read_file", {"path": "hello.txt"}),
                    )
                ),
                ModelResponse(text="The file says hello."),
            ]
        )
        notifications: list[dict[str, object]] = []

        RuntimeLoop(self.store, model, notifications.append).run(
            run["id"], threading.Event()
        )

        snapshot = self.store.read_session_snapshot(self.session["id"])
        self.assertEqual(snapshot["runs"][0]["status"], "succeeded")
        self.assertEqual(
            [item["kind"] for item in snapshot["items"]],
            ["user_message", "tool_call", "assistant_message"],
        )
        tool_item = snapshot["items"][1]
        self.assertEqual(tool_item["toolCall"]["toolName"], "read_file")
        result = json.loads(tool_item["toolCall"]["resultJson"])
        self.assertEqual(result["data"]["content"], "hello from workspace\n")
        self.assertEqual(model.contexts[1][-1]["type"], "tool_result")
        self.assertEqual(
            [notification["method"] for notification in notifications],
            [
                "run/started",
                "item/started",
                "item/completed",
                "item/started",
                "item/completed",
                "item/started",
                "item/delta",
                "item/completed",
                "run/completed",
            ],
        )

    def test_second_active_run_is_rejected(self) -> None:
        self.store.create_run(self.session["id"], "First")

        with self.assertRaises(ActiveRunError):
            self.store.create_run(self.session["id"], "Second")

    def test_cancel_prevents_a_late_model_result_from_succeeding_the_run(self) -> None:
        run, _user_item = self.store.create_run(self.session["id"], "Wait")
        model = BlockingModel()
        notifications: list[dict[str, object]] = []
        cancellation = threading.Event()
        worker = threading.Thread(
            target=RuntimeLoop(self.store, model, notifications.append).run,
            args=(run["id"], cancellation),
        )
        worker.start()
        self.assertTrue(model.started.wait(timeout=2))

        cancellation.set()
        canceled = self.store.cancel_run(run["id"])
        model.release.set()
        worker.join(timeout=2)

        self.assertFalse(worker.is_alive())
        self.assertEqual(canceled["status"], "canceled")
        persisted = self.store.read_run(run["id"])
        self.assertEqual(persisted["status"], "canceled")
        self.assertFalse(any(item["kind"] == "assistant_message" for item in self.store.read_session_snapshot(self.session["id"])["items"]))

    def test_initialize_marks_an_abandoned_run_interrupted_without_replay(self) -> None:
        run, _user_item = self.store.create_run(self.session["id"], "Interrupted")
        self.store.close()

        self.store = SessionStore(self.data_directory)
        self.store.initialize()

        persisted = self.store.read_run(run["id"])
        self.assertEqual(persisted["status"], "interrupted")
        self.assertEqual(persisted["errorCode"], "RUNTIME_INTERRUPTED")


class ToolExecutorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(prefix="eidos-tools-")
        self.workspace = Path(self.temporary_directory.name)
        (self.workspace / "src").mkdir()
        (self.workspace / "src" / "app.py").write_text(
            "print('needle')\n", encoding="utf-8"
        )
        (self.workspace / ".env").write_text("SECRET=value\n", encoding="utf-8")
        self.executor = ToolExecutor(self.workspace)

    def tearDown(self) -> None:
        self.executor.close()
        self.temporary_directory.cleanup()

    def test_list_read_and_literal_search_are_bounded_to_workspace(self) -> None:
        listed = self.executor.execute("list_files", {}, threading.Event())
        read = self.executor.execute(
            "read_file", {"path": "src/app.py"}, threading.Event()
        )
        searched = self.executor.execute(
            "search_text", {"query": "needle"}, threading.Event()
        )

        self.assertEqual(listed["outcome"], "success")
        self.assertIn("src/app.py", listed["data"]["paths"])
        self.assertEqual(read["data"]["content"], "print('needle')\n")
        self.assertEqual(searched["data"]["matches"][0]["path"], "src/app.py")

    def test_sensitive_and_escaping_paths_are_rejected(self) -> None:
        sensitive = self.executor.execute(
            "read_file", {"path": ".env"}, threading.Event()
        )
        escaping = self.executor.execute(
            "read_file", {"path": "../outside"}, threading.Event()
        )

        self.assertEqual(sensitive["code"], "sensitive_path")
        self.assertEqual(escaping["code"], "workspace_boundary_violation")

    def test_replacing_workspace_path_cannot_rebind_an_existing_executor(self) -> None:
        original = self.workspace / "original"
        outside = self.workspace / "outside"
        original.mkdir()
        outside.mkdir()
        (outside / "outside.txt").write_text("outside", encoding="utf-8")
        executor = ToolExecutor(original)
        try:
            original.rename(self.workspace / "moved")
            original.symlink_to(outside, target_is_directory=True)

            result = executor.execute(
                "read_file", {"path": "outside.txt"}, threading.Event()
            )

            self.assertEqual(result["code"], "file_unavailable")
        finally:
            executor.close()


class BlockingModel:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()

    def complete(self, context, cancel, on_text_delta):
        self.started.set()
        self.release.wait(timeout=2)
        return ModelResponse(text="Too late")


if __name__ == "__main__":
    unittest.main()

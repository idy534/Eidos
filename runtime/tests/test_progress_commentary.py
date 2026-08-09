from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import threading
import unittest


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.db.storage import SessionStore  # noqa: E402
from eidos_runtime.model.client import (  # noqa: E402
    ModelResponse,
    ModelToolCall,
    ScriptedModel,
)
from eidos_runtime.runtime.loop import RuntimeLoop  # noqa: E402


class ProgressCommentaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-commentary-")
        root = Path(self.temporary.name)
        self.data = root / "data"
        self.workspace = root / "workspace"
        self.data.mkdir(mode=0o700)
        self.workspace.mkdir()
        (self.workspace / "hello.txt").write_text(
            "hello from workspace\n", encoding="utf-8"
        )
        self.store = SessionStore(self.data)
        self.store.initialize()
        self.session = self.store.create_session(str(self.workspace))

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_commentary_is_completed_before_tool_call_and_run_continues(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "Inspect the CLI entry")
        model = ScriptedModel([
            ModelResponse(
                text="I'll inspect the CLI entry first.",
                tool_calls=(
                    ModelToolCall("search-1", "search_text", {"query": "hello"}),
                ),
            ),
            ModelResponse(text="The CLI inspection is complete."),
        ])
        notifications: list[dict[str, object]] = []

        RuntimeLoop(self.store, model, notifications.append).run(
            run["id"], threading.Event()
        )

        snapshot = self.store.read_session_snapshot(self.session["id"])
        items = snapshot["items"]
        self.assertEqual(
            [(item["kind"], item.get("content"), item["status"]) for item in items],
            [
                ("user_message", "Inspect the CLI entry", "completed"),
                ("assistant_message", "I'll inspect the CLI entry first.", "completed"),
                ("tool_call", None, "completed"),
                ("assistant_message", "The CLI inspection is complete.", "completed"),
            ],
        )
        self.assertEqual(items[1]["ordinal"] + 1, items[2]["ordinal"])
        commentary_completed = next(
            index
            for index, notification in enumerate(notifications)
            if notification["method"] == "item/completed"
            and notification["params"]["item"]["id"] == items[1]["id"]
        )
        tool_started = next(
            index
            for index, notification in enumerate(notifications)
            if notification["method"] == "item/started"
            and notification["params"]["item"]["id"] == items[2]["id"]
        )
        self.assertLess(commentary_completed, tool_started)
        self.assertEqual(snapshot["runs"][0]["status"], "succeeded")

    def test_multi_stage_commentary_precedes_each_tool_and_final_answer(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "Inspect in stages")
        model = ScriptedModel([
            ModelResponse(
                text="I'll map the workspace first.",
                tool_calls=(ModelToolCall("list-1", "list_files", {}),),
            ),
            ModelResponse(
                text="The workspace is mapped. Next I'll read the entry file.",
                tool_calls=(
                    ModelToolCall("read-1", "read_file", {"path": "hello.txt"}),
                ),
            ),
            ModelResponse(text="The staged inspection is complete."),
        ])

        RuntimeLoop(self.store, model, lambda _message: None).run(
            run["id"], threading.Event()
        )

        snapshot = self.store.read_session_snapshot(self.session["id"])
        items = snapshot["items"]
        self.assertEqual(
            [item["kind"] for item in items],
            [
                "user_message",
                "assistant_message",
                "tool_call",
                "assistant_message",
                "tool_call",
                "assistant_message",
            ],
        )
        self.assertEqual(
            [item.get("content") for item in items if item["kind"] == "assistant_message"],
            [
                "I'll map the workspace first.",
                "The workspace is mapped. Next I'll read the entry file.",
                "The staged inspection is complete.",
            ],
        )
        self.assertEqual(snapshot["runs"][0]["status"], "succeeded")
        self.assertTrue(any(
            entry.get("type") == "assistant"
            and entry.get("content") == "I'll map the workspace first."
            for entry in model.contexts[1]
        ))

    def test_provider_control_syntax_with_tool_call_is_not_persisted_or_emitted(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "Reject provider control")
        model = ScriptedModel([
            ModelResponse(
                text="<|DSML|tool_calls><|DSML|invoke name=\"read_file\">",
                tool_calls=(
                    ModelToolCall("read-unsafe", "read_file", {"path": "hello.txt"}),
                ),
            ),
            ModelResponse(text="Recovered without exposing provider control."),
        ])
        notifications: list[dict[str, object]] = []

        RuntimeLoop(self.store, model, notifications.append).run(
            run["id"], threading.Event()
        )

        snapshot = self.store.read_session_snapshot(self.session["id"])
        self.assertEqual(
            [item["kind"] for item in snapshot["items"]],
            ["user_message", "assistant_message"],
        )
        self.assertFalse(any(
            "DSML" in str(notification) for notification in notifications
        ))
        attempts = self.store.read_model_attempts(run["id"])
        self.assertEqual(attempts[0]["errorCode"], "provider_control_syntax")

    def test_empty_commentary_does_not_create_an_assistant_item(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "Read without commentary")
        model = ScriptedModel([
            ModelResponse(
                text="",
                tool_calls=(
                    ModelToolCall("read-1", "read_file", {"path": "hello.txt"}),
                ),
            ),
            ModelResponse(text="Read complete."),
        ])

        RuntimeLoop(self.store, model, lambda _message: None).run(
            run["id"], threading.Event()
        )

        snapshot = self.store.read_session_snapshot(self.session["id"])
        self.assertEqual(
            [item["kind"] for item in snapshot["items"]],
            ["user_message", "tool_call", "assistant_message"],
        )

    def test_tool_free_text_remains_the_final_answer_and_completes_run(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "Answer directly")
        model = ScriptedModel([ModelResponse(text="This is the final answer.")])

        RuntimeLoop(self.store, model, lambda _message: None).run(
            run["id"], threading.Event()
        )

        snapshot = self.store.read_session_snapshot(self.session["id"])
        self.assertEqual(snapshot["runs"][0]["status"], "succeeded")
        self.assertEqual(
            [(item["kind"], item.get("content")) for item in snapshot["items"]],
            [
                ("user_message", "Answer directly"),
                ("assistant_message", "This is the final answer."),
            ],
        )


if __name__ == "__main__":
    unittest.main()

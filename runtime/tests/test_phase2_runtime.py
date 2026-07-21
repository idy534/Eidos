from __future__ import annotations

from pathlib import Path
import json
import sys
import tempfile
import threading
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel  # noqa: E402
from eidos_runtime.runtime.loop import RuntimeEngine  # noqa: E402
from eidos_runtime.db.storage import SessionStore  # noqa: E402


class PhaseTwoRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(prefix="eidos-p2-runtime-")
        root = Path(self.temporary_directory.name)
        self.data = root / "data"
        self.workspace = root / "workspace"
        self.data.mkdir(mode=0o700)
        self.workspace.mkdir()
        self.store = SessionStore(self.data)
        self.store.initialize()
        self.session = self.store.create_session(str(self.workspace))

    def tearDown(self) -> None:
        self.store.close()
        self.temporary_directory.cleanup()

    def test_completed_run_persists_segment_step_and_attempt(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "finish")
        model = ScriptedModel([ModelResponse(text="done")])
        RuntimeEngine(self.store, model, lambda _message: None).run(
            run["id"], threading.Event()
        )
        connection = self.store.connection
        assert connection is not None
        self.assertEqual(
            tuple(connection.execute(
                "SELECT status, step_count FROM execution_segments"
            ).fetchone()),
            ("completed", 1),
        )
        self.assertEqual(connection.execute("SELECT status FROM steps").fetchone()[0], "completed")
        self.assertEqual(connection.execute("SELECT status FROM model_attempts").fetchone()[0], "completed")

    def test_segment_limit_pauses_without_an_extra_model_request(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "pause")
        self.store.increment_model_step(run["id"])
        self.store.complete_current_step(run["id"], "completed")
        connection = self.store.connection
        assert connection is not None
        connection.execute(
            "UPDATE execution_segments SET step_count = 20 WHERE run_id = ?",
            (run["id"],),
        )
        connection.commit()
        model = ScriptedModel([ModelResponse(text="must not run")])
        RuntimeEngine(self.store, model, lambda _message: None).run(
            run["id"], threading.Event()
        )
        paused = self.store.read_run(run["id"])
        self.assertEqual(paused["status"], "waiting_user_input")
        self.assertEqual(paused["pauseReason"], "segment_step_limit")
        self.assertEqual(model.contexts, [])

    def test_run_limit_uses_one_toolless_finalization_then_stops(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "stop")
        connection = self.store.connection
        assert connection is not None
        connection.execute("UPDATE runs SET model_step_count = 80 WHERE id = ?", (run["id"],))
        connection.commit()
        model = ScriptedModel([ModelResponse(text="final summary")])
        RuntimeEngine(self.store, model, lambda _message: None).run(
            run["id"], threading.Event()
        )
        stopped = self.store.read_run(run["id"])
        self.assertEqual(stopped["status"], "stopped")
        self.assertEqual(stopped["stopReason"], "max_total_steps")
        self.assertEqual(model.allow_tools_history, [False])

    def test_two_rejections_pause_and_user_input_creates_a_new_segment(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "change")
        for index in range(2):
            item = self.store.create_tool_item(
                run["id"], index + 1, 0, f"call-{index}", "write_file",
                json.dumps({"path": "a.txt", "content": "x"}),
            )
            self.store.begin_approval(item["id"], "diff", None)
            self.store.resolve_approval(item["id"], "reject", None)
            self.store.complete_tool_item(
                item["id"],
                json.dumps({"outcome": "declined", "code": "user_rejected"}),
                item_status="declined",
            )
        paused = self.store.read_run(run["id"])
        self.assertEqual(paused["status"], "waiting_user_input")
        self.assertEqual(paused["pauseReason"], "repeated_approval_rejection")
        continued = self.store.continue_run(run["id"], "try a different plan")
        self.assertEqual(continued["status"], "queued")
        connection = self.store.connection
        assert connection is not None
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM execution_segments WHERE run_id = ?", (run["id"],)).fetchone()[0],
            1,
        )

    def test_crashed_durable_intent_is_reconciled_without_replay(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "write")
        item = self.store.create_tool_item(
            run["id"], 1, 0, "call", "write_file",
            json.dumps({"path": "a.txt", "content": "x"}),
        )
        self.store.begin_approval(item["id"], "diff", None)
        self.store.resolve_approval(item["id"], "approve", None)
        self.store.begin_durable_intent(item["id"], preconditions={"path": "a.txt"})
        self.store.close()
        self.store = SessionStore(self.data)
        self.store.initialize()
        recovered = self.store.read_run(run["id"])
        self.assertEqual(recovered["status"], "waiting_user_input")
        self.assertTrue(recovered["sideEffectsMayExist"])
        self.assertTrue(self.store.side_effects_blocked(run["id"])
        )
        connection = self.store.connection
        assert connection is not None
        self.assertEqual(
            connection.execute("SELECT status FROM durable_intents").fetchone()[0],
            "interrupted",
        )

    def test_only_a_new_successful_read_step_clears_reconciliation_barrier(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "verify")
        write = self.store.create_tool_item(
            run["id"], 0, 0, "write", "write_file", "{}",
        )
        self.store.begin_approval(write["id"], "diff", None)
        self.store.resolve_approval(write["id"], "approve", None)
        self.store.begin_durable_intent(write["id"], preconditions={})
        self.store.complete_tool_item(
            write["id"],
            json.dumps({"outcome": "error", "code": "file_commit_uncertain"}),
            item_status="failed", tool_status="failed",
        )
        self.assertTrue(self.store.side_effects_blocked(run["id"]))
        step_index = self.store.increment_model_step(run["id"])
        read = self.store.create_tool_item(
            run["id"], step_index, 0, "read", "read_file", "{}",
        )
        self.store.complete_tool_item(
            read["id"], json.dumps({"outcome": "success", "code": None})
        )
        self.store.complete_current_step(run["id"], "completed")
        self.assertFalse(self.store.side_effects_blocked(run["id"]))

    def test_stream_failure_after_first_delta_retries_without_user_input(self) -> None:
        class InterruptedThenCompletedModel:
            calls = 0
            contexts = []

            def complete(
                self, context, _cancel, on_text_delta,
                allow_tools=True, tool_definitions=(),
            ):
                self.calls += 1
                self.contexts.append(context)
                if self.calls == 1:
                    on_text_delta("safe progress")
                    raise OSError("fixture")
                on_text_delta("done")
                return ModelResponse(text="done")

        run, _ = self.store.create_run(self.session["id"], "stream")
        model = InterruptedThenCompletedModel()
        RuntimeEngine(self.store, model, lambda _message: None).run(
            run["id"], threading.Event()
        )
        completed = self.store.read_run(run["id"])
        self.assertEqual(completed["status"], "succeeded")
        snapshot = self.store.read_session_snapshot(self.session["id"])
        messages = [
            item for item in snapshot["items"] if item["kind"] == "assistant_message"
        ]
        progress, final = messages
        self.assertEqual(progress["content"], "safe progress")
        self.assertTrue(progress["incomplete"])
        self.assertEqual(final["content"], "done")
        self.assertFalse(final.get("incomplete", False))
        self.assertEqual(model.calls, 2)
        self.assertEqual(model.contexts[1], model.contexts[0])
        future_context = self.store.model_context(self.session["id"])
        self.assertNotIn(
            "safe progress",
            [item.get("content") for item in future_context],
        )
        connection = self.store.connection
        assert connection is not None
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM steps").fetchone()[0], 1)
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM model_attempts").fetchone()[0],
            2,
        )

    def test_stream_failure_before_first_delta_retries_same_model_input(self) -> None:
        class InitiallyUnavailableModel:
            calls = 0
            contexts = []

            def complete(
                self, context, _cancel, on_text_delta,
                allow_tools=True, tool_definitions=(),
            ):
                self.calls += 1
                self.contexts.append(context)
                if self.calls == 1:
                    raise OSError("fixture")
                on_text_delta("done")
                return ModelResponse(text="done")

        run, _ = self.store.create_run(self.session["id"], "stream")
        model = InitiallyUnavailableModel()
        RuntimeEngine(self.store, model, lambda _message: None).run(
            run["id"], threading.Event()
        )

        self.assertEqual(self.store.read_run(run["id"])["status"], "succeeded")
        self.assertEqual(model.calls, 2)
        self.assertEqual(model.contexts[1], model.contexts[0])

    def test_repeated_stream_failures_pause_after_bounded_retries(self) -> None:
        class AlwaysInterruptedModel:
            calls = 0

            def complete(
                self, _context, _cancel, on_text_delta,
                allow_tools=True, tool_definitions=(),
            ):
                self.calls += 1
                on_text_delta(f"progress {self.calls}\n")
                raise OSError("fixture")

        run, _ = self.store.create_run(self.session["id"], "stream")
        model = AlwaysInterruptedModel()
        RuntimeEngine(self.store, model, lambda _message: None).run(
            run["id"], threading.Event()
        )

        paused = self.store.read_run(run["id"])
        self.assertEqual(paused["status"], "waiting_user_input")
        self.assertEqual(paused["pauseReason"], "model_stream_interrupted")
        self.assertEqual(model.calls, 6)
        connection = self.store.connection
        assert connection is not None
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM steps").fetchone()[0], 1)
        self.assertEqual(
            connection.execute("SELECT COUNT(*) FROM model_attempts").fetchone()[0],
            6,
        )

    def test_two_sensitive_model_tool_inputs_pause_without_tool_or_approval(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "write safely")
        model = ScriptedModel([
            ModelResponse(tool_calls=(ModelToolCall(
                "sensitive-1", "write_file",
                {"path": "a.txt", "content": "password=first"},
            ),)),
            ModelResponse(tool_calls=(ModelToolCall(
                "sensitive-2", "write_file",
                {"path": "b.txt", "content": "password=second"},
            ),)),
        ])
        RuntimeEngine(self.store, model, lambda _message: None).run(
            run["id"], threading.Event()
        )
        paused = self.store.read_run(run["id"])
        self.assertEqual(paused["status"], "waiting_user_input")
        self.assertEqual(paused["pauseReason"], "repeated_sensitive_tool_input")
        connection = self.store.connection
        assert connection is not None
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0], 0)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM approvals").fetchone()[0], 0)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM durable_intents").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()

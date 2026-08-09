from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import threading
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel
from eidos_runtime.runtime.loop import RuntimeEngine


class UnboundedTaskLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="eidos-unbounded-task-lifecycle-"
        )
        root = Path(self.temporary.name)
        self.data = root / "data"
        self.workspace = root / "workspace"
        self.data.mkdir(mode=0o700)
        self.workspace.mkdir()
        self.store = SessionStore(self.data)
        self.store.initialize()
        self.session = self.store.create_session(str(self.workspace))

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_more_than_eighty_progressive_model_steps_complete_naturally(self) -> None:
        responses: list[ModelResponse] = []
        for index in range(121):
            relative_path = f"evidence-{index:03d}.txt"
            (self.workspace / relative_path).write_text(
                f"verified evidence {index}\n",
                encoding="utf-8",
            )
            responses.append(ModelResponse(tool_calls=(
                ModelToolCall(
                    f"read-{index}",
                    "read_file",
                    {"path": relative_path},
                ),
            )))
        responses.append(ModelResponse(text="All progressive evidence was collected."))
        run, _ = self.store.create_run(self.session["id"], "inspect all evidence")

        RuntimeEngine(
            self.store,
            ScriptedModel(responses),
            lambda _message: None,
        ).run(run["id"], threading.Event())

        completed = self.store.read_run(run["id"])
        self.assertEqual(completed["status"], "succeeded")
        self.assertGreater(completed["modelStepCount"], 80)
        self.assertIsNone(completed.get("stopReason"))

    def test_more_than_twenty_steps_remain_in_one_execution_segment(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "continue one execution slice")

        for expected_step in range(1, 26):
            self.assertEqual(
                self.store.increment_model_step(run["id"]),
                expected_step,
            )
            self.store.complete_current_step(run["id"], "completed")

        assert self.store.connection is not None
        segments = self.store.connection.execute(
            """
            SELECT ordinal, status, step_count FROM execution_segments
            WHERE run_id = ? ORDER BY ordinal
            """,
            (run["id"],),
        ).fetchall()
        self.assertEqual(
            [tuple(segment) for segment in segments],
            [(1, "running", 25)],
        )
        self.assertEqual(self.store.read_run(run["id"])["status"], "running")

    def test_more_than_two_hours_of_effective_time_does_not_stop_the_run(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "continue after two hours")
        assert self.store.connection is not None
        self.store.connection.execute(
            "UPDATE runs SET total_effective_ms = 7_200_001 WHERE id = ?",
            (run["id"],),
        )
        self.store.connection.commit()

        RuntimeEngine(
            self.store,
            ScriptedModel([ModelResponse(text="Finished after sustained progress.")]),
            lambda _message: None,
        ).run(run["id"], threading.Event())

        completed = self.store.read_run(run["id"])
        self.assertEqual(completed["status"], "succeeded")
        self.assertIsNone(completed.get("stopReason"))
        total_effective_ms = self.store.connection.execute(
            "SELECT total_effective_ms FROM runs WHERE id = ?",
            (run["id"],),
        ).fetchone()[0]
        self.assertGreater(total_effective_ms, 7_200_000)

    def test_segment_effective_time_rollover_is_non_terminal(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "roll execution slice")
        self.store.increment_model_step(run["id"])
        self.store.complete_current_step(run["id"], "completed")
        self.store.add_effective_time(run["id"], 1_800_000)

        self.assertEqual(self.store.increment_model_step(run["id"]), 2)

        assert self.store.connection is not None
        segments = self.store.connection.execute(
            """
            SELECT ordinal, status, step_count, effective_ms
            FROM execution_segments WHERE run_id = ? ORDER BY ordinal
            """,
            (run["id"],),
        ).fetchall()
        self.assertEqual(
            [tuple(segment) for segment in segments],
            [
                (1, "completed", 1, 1_800_000),
                (2, "running", 1, 0),
            ],
        )
        self.assertEqual(self.store.read_run(run["id"])["status"], "running")


if __name__ == "__main__":
    unittest.main()

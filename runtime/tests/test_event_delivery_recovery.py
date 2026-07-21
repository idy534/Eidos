from __future__ import annotations

import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.db.storage import (  # noqa: E402
    CommittedMutation,
    DATABASE_NAME,
    SessionStore,
)
from eidos_runtime.model.client import (  # noqa: E402
    ModelResponse,
    ModelToolCall,
    ScriptedModel,
)
from eidos_runtime.protocol.server import (  # noqa: E402
    RuntimeOutputClosedError,
    RuntimeServer,
)
from eidos_runtime.runtime.approval import ApprovalDecision  # noqa: E402
from eidos_runtime.runtime.engine import RuntimeEngine  # noqa: E402
from eidos_runtime.runtime.events import RuntimeEvents  # noqa: E402
from eidos_runtime.runtime.supervisor import RunSupervisor  # noqa: E402


class EventDeliveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-delivery-")
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

    @staticmethod
    def _broken_notify(_message: dict[str, object]) -> None:
        raise BrokenPipeError("renderer disconnected")

    def test_delivery_failure_is_structured_and_projector_errors_still_raise(self) -> None:
        secret_marker = "payload-must-not-be-logged"
        run, _ = self.store.create_run(self.session["id"], secret_marker)
        mutation = self.store.fail_run_committed(run["id"], "fixture")

        with self.assertLogs("eidos.runtime", level="WARNING") as logs:
            result = RuntimeEvents(self._broken_notify).publish(
                mutation, run=mutation.value
            )

        self.assertEqual((result.attempted, result.delivered), (1, 0))
        self.assertEqual(result.failures[0].error_type, "BrokenPipeError")
        self.assertNotIn(secret_marker, "\n".join(logs.output))

        class ExplodingProjector:
            def project(self, *_args, **_kwargs):
                raise RuntimeError("projector bug")

        with self.assertRaisesRegex(RuntimeError, "projector bug"):
            RuntimeEvents(
                self._broken_notify, ExplodingProjector()  # type: ignore[arg-type]
            ).publish(mutation, run=mutation.value)

    def test_closed_runtime_output_has_an_explicit_exception(self) -> None:
        output = io.StringIO()
        server = RuntimeServer(output, self.data)
        output.close()

        with self.assertRaises(RuntimeOutputClosedError):
            server.send({"jsonrpc": "2.0", "method": "run/updated", "params": {}})

        server.close()

    def test_sampling_commits_assistant_and_run_despite_broken_notifications(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "sample")
        model = ScriptedModel([ModelResponse(text="done")])

        RuntimeEngine(self.store, model, self._broken_notify).run(
            run["id"], threading.Event()
        )

        completed = self.store.read_run(run["id"])
        self.assertEqual(completed["status"], "succeeded")
        self.assertNotEqual(completed.get("errorCode"), "INTERNAL_ERROR")
        snapshot = self.store.read_session_snapshot(self.session["id"])
        self.assertTrue(any(
            item["kind"] == "assistant_message" and item["status"] == "completed"
            for item in snapshot["items"]
        ))
        events = self.store.list_events(self.session["id"], after_event_id=0)
        self.assertTrue(any(
            event["eventType"] == "run.status_changed"
            and event["payload"]["current"] == "succeeded"
            for event in events["items"]
        ))

    def test_tool_execution_commits_once_and_sampling_continues(self) -> None:
        (self.workspace / "hello.txt").write_text("hello", encoding="utf-8")
        run, _ = self.store.create_run(self.session["id"], "read")
        model = ScriptedModel([
            ModelResponse(tool_calls=(
                ModelToolCall("call-read", "read_file", {"path": "hello.txt"}),
            )),
            ModelResponse(text="done"),
        ])

        RuntimeEngine(self.store, model, self._broken_notify).run(
            run["id"], threading.Event()
        )

        self.assertEqual(self.store.read_run(run["id"])["status"], "succeeded")
        assert self.store.connection is not None
        tool = self.store.connection.execute(
            "SELECT status, result_json FROM tool_calls WHERE provider_call_id = ?",
            ("call-read",),
        ).fetchall()
        self.assertEqual(len(tool), 1)
        self.assertEqual(tool[0]["status"], "completed")
        self.assertEqual(json.loads(tool[0]["result_json"])["outcome"], "success")
        self.assertEqual(len(model.contexts), 2)

    def test_approval_resolution_survives_broken_notifications(self) -> None:
        for decision in ("approve", "reject"):
            with self.subTest(decision=decision):
                run, _ = self.store.create_run(
                    self.session["id"], f"{decision} write"
                )
                model = ScriptedModel([
                    ModelResponse(tool_calls=(ModelToolCall(
                        f"call-{decision}",
                        "write_file",
                        {"path": f"{decision}.txt", "content": decision},
                    ),)),
                    ModelResponse(text="done"),
                ])

                RuntimeEngine(
                    self.store,
                    model,
                    self._broken_notify,
                    lambda _params, _cancel: ApprovalDecision(decision),
                ).run(run["id"], threading.Event())

                completed = self.store.read_run(run["id"])
                self.assertEqual(completed["status"], "succeeded")
                self.assertNotEqual(completed["status"], "waiting_approval")
                assert self.store.connection is not None
                row = self.store.connection.execute(
                    """
                    SELECT approvals.status, tool_calls.approval_decision
                    FROM approvals JOIN tool_calls ON tool_calls.item_id = approvals.item_id
                    WHERE approvals.run_id = ?
                    """,
                    (run["id"],),
                ).fetchone()
                self.assertEqual(row["status"], f"{decision}d" if decision == "approve" else "rejected")
                self.assertEqual(row["approval_decision"], decision)

    def test_finalization_and_terminal_watermark_survive_broken_notifications(self) -> None:
        run, _ = self.store.enqueue_run(self.session["id"], "finalize")
        claimed = self.store.claim_next_run()
        assert claimed is not None
        assert self.store.connection is not None
        self.store.connection.execute(
            "UPDATE runs SET model_step_count = 80 WHERE id = ?", (run["id"],)
        )
        self.store.connection.commit()

        RuntimeEngine(
            self.store,
            ScriptedModel([ModelResponse(text="final answer")]),
            self._broken_notify,
        ).run(run["id"], threading.Event())

        stopped = self.store.read_run(run["id"])
        self.assertEqual(stopped["status"], "stopped")
        rows = self.store.connection.execute(
            "SELECT kind, status FROM items WHERE run_id = ? ORDER BY ordinal",
            (run["id"],),
        ).fetchall()
        self.assertEqual(rows[-1]["kind"], "assistant_message")
        self.assertEqual(rows[-1]["status"], "completed")
        self.assertEqual(
            self.store.connection.execute(
                "SELECT status FROM execution_segments WHERE run_id = ?",
                (run["id"],),
            ).fetchone()["status"],
            "completed",
        )
        events = self.store.list_events(self.session["id"], after_event_id=0)
        terminal = [
            event for event in events["items"]
            if event["eventType"] == "run.status_changed"
            and event["payload"]["current"] == "stopped"
        ]
        self.assertEqual(len(terminal), 1)
        self.assertEqual(events["throughEventId"], events["items"][-1]["eventId"])


class FinalizingRecoveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-recovery-")
        root = Path(self.temporary.name)
        self.data = root / "data"
        workspace = root / "workspace"
        self.data.mkdir(mode=0o700)
        workspace.mkdir()
        self.store = SessionStore(self.data)
        self.store.initialize()
        self.session = self.store.create_session(str(workspace))

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _restart(self) -> None:
        self.store.close()
        self.store = SessionStore(self.data)
        self.store.initialize()

    def _finalizing_run(self, *, active_item: bool = False) -> tuple[dict[str, object], str | None]:
        run, _ = self.store.create_run(self.session["id"], "finalize")
        if active_item:
            self.store.increment_model_step(run["id"])
        self.store.begin_finalization(run["id"])
        item_id = None
        if active_item:
            from eidos_runtime.runtime.assistant_stream import AssistantStreamWriter

            writer = AssistantStreamWriter(
                self.store,
                RuntimeEvents(lambda _message: None),
                run["id"],
                None,
            )
            writer.write("pending")
            item_id = str(writer.item["id"])
        return run, item_id

    def test_finalizing_run_recovers_to_interrupted(self) -> None:
        run, _ = self._finalizing_run()

        self._restart()

        recovered = self.store.read_run(run["id"])
        self.assertEqual(recovered["status"], "interrupted")
        events = self.store.list_events(self.session["id"], after_event_id=0)
        self.assertTrue(any(
            event["eventType"] == "run.status_changed"
            and event["payload"]["current"] == "interrupted"
            for event in events["items"]
        ))

    def test_finalizing_item_step_segment_and_run_recover_atomically(self) -> None:
        run, item_id = self._finalizing_run(active_item=True)

        self._restart()

        assert item_id is not None
        self.assertEqual(self.store.read_item(item_id)["status"], "canceled")
        assert self.store.connection is not None
        self.assertEqual(
            self.store.connection.execute(
                "SELECT status FROM execution_segments WHERE run_id = ?",
                (run["id"],),
            ).fetchone()["status"],
            "failed",
        )
        self.assertEqual(
            self.store.connection.execute(
                "SELECT status FROM steps WHERE run_id = ?",
                (run["id"],),
            ).fetchone()["status"],
            "failed",
        )
        self.assertEqual(self.store.read_run(run["id"])["status"], "interrupted")

    def test_finalizing_with_interrupted_intent_requires_reconciliation(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "write")
        item = self.store.create_tool_item(
            run["id"], 1, 0, "call", "write_file", "{}"
        )
        self.store.begin_approval(item["id"], "diff", None)
        self.store.resolve_approval(item["id"], "approve", None)
        self.store.begin_durable_intent(item["id"], preconditions={})
        self.store.begin_finalization(run["id"])

        self._restart()

        recovered = self.store.read_run(run["id"])
        self.assertEqual(recovered["status"], "waiting_user_input")
        self.assertTrue(self.store.side_effects_blocked(run["id"]))
        self.assertTrue(recovered["sideEffectsMayExist"])

    def test_recovery_event_failure_rolls_back_the_whole_transaction(self) -> None:
        run, item_id = self._finalizing_run(active_item=True)
        self.store.close()
        self.store = SessionStore(self.data)

        with patch(
            "eidos_runtime.db.storage.append_event",
            side_effect=RuntimeError("fixture recovery event failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "fixture recovery event failure"):
                self.store.initialize()

        connection = sqlite3.connect(self.data / DATABASE_NAME)
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT status FROM runs WHERE id = ?", (run["id"],)
                ).fetchone()[0],
                "finalizing",
            )
            self.assertEqual(
                connection.execute(
                    "SELECT status FROM items WHERE id = ?", (item_id,)
                ).fetchone()[0],
                "in_progress",
            )
        finally:
            connection.close()

        self.store = SessionStore(self.data)
        self.store.initialize()

    def test_terminal_runs_are_unchanged_by_recovery(self) -> None:
        statuses: dict[str, str] = {}
        assert self.store.connection is not None
        for status in ("succeeded", "failed", "canceled", "stopped", "interrupted"):
            run, _ = self.store.create_run(self.session["id"], status)
            self.store.connection.execute(
                "UPDATE runs SET status = ? WHERE id = ?", (status, run["id"])
            )
            statuses[run["id"]] = status
        self.store.connection.commit()

        self._restart()

        self.assertEqual(
            {run_id: self.store.read_run(run_id)["status"] for run_id in statuses},
            statuses,
        )


class SupervisorFinalizingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-supervisor-")
        root = Path(self.temporary.name)
        data = root / "data"
        workspace = root / "workspace"
        data.mkdir(mode=0o700)
        workspace.mkdir()
        self.store = SessionStore(data)
        self.store.initialize()
        self.session = self.store.create_session(str(workspace))

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _supervisor(self) -> RunSupervisor:
        class ExplodingEngine:
            def __init__(self, *_args, **_kwargs) -> None:
                pass

            def run(self, _run_id: str, _cancel: threading.Event) -> None:
                raise RuntimeError("fixture worker failure")

        return RunSupervisor(
            self.store,
            lambda _model_id: object(),  # type: ignore[arg-type,return-value]
            EventDeliveryTests._broken_notify,
            lambda value: value,
            lambda: True,
            lambda: False,
            lambda: None,
            engine_factory=ExplodingEngine,  # type: ignore[arg-type]
        )

    def test_worker_exception_closes_finalizing_as_internal_error(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "finalizing")
        self.store.begin_finalization(run["id"])
        gate = threading.Event()
        gate.set()

        with self.assertLogs("eidos.runtime", level="ERROR"):
            self._supervisor()._run_worker(run["id"], threading.Event(), gate)

        failed = self.store.read_run(run["id"])
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["errorCode"], "INTERNAL_ERROR")

    def test_worker_exception_does_not_change_terminal_run(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "terminal")
        assert self.store.connection is not None
        self.store.connection.execute(
            "UPDATE runs SET status = 'stopped' WHERE id = ?", (run["id"],)
        )
        self.store.connection.commit()
        gate = threading.Event()
        gate.set()

        with self.assertLogs("eidos.runtime", level="ERROR"):
            self._supervisor()._run_worker(run["id"], threading.Event(), gate)

        self.assertEqual(self.store.read_run(run["id"])["status"], "stopped")


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import ast
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.db.storage import (  # noqa: E402
    CommittedMutation,
    InvalidRunStateError,
    SessionStore,
)
from eidos_runtime.runtime.event_projector import EventProjector  # noqa: E402
from eidos_runtime.runtime.events import RuntimeEvents  # noqa: E402
from eidos_runtime.runtime.state_machine import (  # noqa: E402
    RuntimePhaseTracker,
    RuntimeState,
)
from eidos_runtime.runtime.supervisor import RunSupervisor  # noqa: E402


class RuntimeStateAuthorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-authority-")
        root = Path(self.temporary.name)
        data = root / "data"
        self.workspace = root / "workspace"
        data.mkdir(mode=0o700)
        self.workspace.mkdir()
        self.store = SessionStore(data)
        self.store.initialize()
        self.session = self.store.create_session(str(self.workspace))

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_run_transition_and_event_roll_back_together(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "pause")
        before = self.store.list_events(self.session["id"], after_event_id=0)

        with patch(
            "eidos_runtime.db.transitions.append_event",
            side_effect=RuntimeError("fixture event failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "fixture event failure"):
                self.store.pause_run_committed(run["id"], "fixture")

        self.assertEqual(self.store.read_run(run["id"])["status"], "running")
        after = self.store.list_events(self.session["id"], after_event_id=0)
        self.assertEqual(after["throughEventId"], before["throughEventId"])

    def test_committed_notification_observes_persisted_fact(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "fail")
        mutation = self.store.fail_run_committed(run["id"], "fixture")
        observed: list[str] = []

        events = RuntimeEvents(
            lambda _message: observed.append(
                str(self.store.read_run(run["id"])["status"])
            ),
            EventProjector(),
        )
        events.publish(mutation, run=mutation.value)

        self.assertIsInstance(mutation, CommittedMutation)
        self.assertEqual(observed, ["failed"])

    def test_notification_failure_does_not_remove_committed_event(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "fail")
        mutation = self.store.fail_run_committed(run["id"], "fixture")
        events = RuntimeEvents(
            lambda _message: (_ for _ in ()).throw(OSError("client closed")),
            EventProjector(),
        )

        with self.assertLogs("eidos.runtime", level="WARNING"):
            delivery = events.publish(mutation, run=mutation.value)

        self.assertEqual(delivery.delivered, 0)
        self.assertEqual(delivery.failures[0].error_type, "OSError")

        listed = self.store.list_events(self.session["id"], after_event_id=0)
        self.assertTrue(any(
            event["eventType"] == "run.status_changed"
            and event["payload"]["current"] == "failed"
            for event in listed["items"]
        ))

    def test_approval_lifecycle_has_explicit_protocol_notifications(self) -> None:
        projector = EventProjector()
        run, _ = self.store.create_run(self.session["id"], "approval")
        item = self.store.create_tool_item(
            run["id"], 1, 0, "provider-approval", "write_file", "{}"
        )
        requested = self.store.begin_approval_committed(item["id"], "", None)
        requested_event = next(
            event for event in requested.events
            if event["eventType"] == "approval.status_changed"
        )
        requested_notification = projector.project(requested_event)[0]
        self.assertEqual(
            requested_notification["params"]["sessionId"],
            self.session["id"],
        )

        methods = [
            projector.project({
                "eventType": "approval.status_changed",
                "sessionId": self.session["id"],
                "runId": "run",
                "payload": {
                    "entity_id": "approval",
                    "previous": "created",
                    "current": status,
                },
            })[0]["method"]
            for status in ("pending", "approved", "canceled")
        ]

        self.assertEqual(
            methods,
            ["approval/requested", "approval/resolved", "approval/canceled"],
        )

    def test_persisted_run_status_wins_over_runtime_phase_tracker(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "pause")
        tracker = RuntimePhaseTracker(state=RuntimeState.CANCELED)

        mutation = self.store.pause_run_committed(run["id"], "fixture")
        tracker.track(RuntimeState.WAITING_USER_INPUT, "diagnostic only")

        self.assertEqual(mutation.value["status"], "waiting_user_input")
        self.assertEqual(tracker.state, RuntimeState.WAITING_USER_INPUT)

    def test_approval_decisions_are_compare_and_swap(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "approve")
        item = self.store.create_tool_item(
            run["id"], 1, 0, "provider-call", "write_file", "{}"
        )
        self.store.begin_approval(item["id"], "diff", None)
        barrier = threading.Barrier(4)
        outcomes: list[str] = []

        def decide(decision: str) -> None:
            barrier.wait()
            try:
                self.store.resolve_approval(item["id"], decision, None)
            except InvalidRunStateError:
                outcomes.append("lost")
            else:
                outcomes.append(decision)

        approve = threading.Thread(target=decide, args=("approve",))
        reject = threading.Thread(target=decide, args=("reject",))
        def cancel() -> None:
            barrier.wait()
            try:
                self.store.cancel_waiting_approval_committed(run["id"])
            except InvalidRunStateError:
                outcomes.append("lost")
            else:
                outcomes.append("cancel")

        cancellation = threading.Thread(target=cancel)
        approve.start()
        reject.start()
        cancellation.start()
        barrier.wait()
        approve.join()
        reject.join()
        cancellation.join()

        self.assertEqual(len([value for value in outcomes if value != "lost"]), 1)
        self.assertEqual(outcomes.count("lost"), 2)

    def test_supervisor_projects_worker_failure_once(self) -> None:
        class ExplodingEngine:
            def __init__(self, *_args, **_kwargs) -> None:
                pass

            def run(self, _run_id: str, _cancel: threading.Event) -> None:
                raise RuntimeError("fixture worker failure")

        run, _ = self.store.create_run(self.session["id"], "fail")
        notifications: list[dict[str, object]] = []
        supervisor = RunSupervisor(
            self.store,
            lambda _model_id: object(),  # type: ignore[arg-type,return-value]
            notifications.append,
            lambda value: value,
            lambda: True,
            lambda: False,
            lambda: None,
            engine_factory=ExplodingEngine,  # type: ignore[arg-type]
        )
        gate = threading.Event()
        gate.set()

        with self.assertLogs("eidos.runtime", level="ERROR"):
            supervisor._run_worker(run["id"], threading.Event(), gate)

        self.assertEqual(self.store.read_run(run["id"])["status"], "failed")
        self.assertEqual(
            sum(
                message.get("method") == "run/completed"
                for message in notifications
            ),
            1,
        )


class RuntimeSecondStageArchitectureTests(unittest.TestCase):
    def test_runtime_server_does_not_create_engine_or_worker_threads(self) -> None:
        path = RUNTIME_ROOT / "eidos_runtime" / "protocol" / "server.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        calls = {
            ast.unparse(node.func)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
        }
        self.assertNotIn("RuntimeEngine", calls)
        self.assertNotIn("threading.Thread", calls)

    def test_worker_failure_notifications_live_outside_runtime_server(self) -> None:
        path = RUNTIME_ROOT / "eidos_runtime" / "protocol" / "server.py"
        source = path.read_text(encoding="utf-8")
        self.assertNotIn("def _run_worker", source)
        self.assertNotIn("canceled_items_for_run", source)

    def test_event_projector_has_no_store_dependency(self) -> None:
        path = RUNTIME_ROOT / "eidos_runtime" / "runtime" / "event_projector.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        }
        self.assertNotIn("eidos_runtime.db.storage", imports)


if __name__ == "__main__":
    unittest.main()

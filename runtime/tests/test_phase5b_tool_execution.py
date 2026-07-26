from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eidos_runtime.db.storage import SessionStore  # noqa: E402
from eidos_runtime.model.client import ModelToolCall  # noqa: E402
from eidos_runtime.runtime.approval import (  # noqa: E402
    ApprovalCoordinator,
    ApprovalDecision,
)
from eidos_runtime.runtime.events import RuntimeEvents  # noqa: E402
from eidos_runtime.runtime.state_machine import RuntimePhaseTracker  # noqa: E402
from eidos_runtime.runtime.tool_dispatcher import ToolDispatchPlan  # noqa: E402
from eidos_runtime.runtime.tool_execution import (  # noqa: E402
    HandlerOutcome,
    PreparedToolExecution,
    ToolExecutionController,
    active_tool_execution_count,
)
from eidos_runtime.sandbox.sensitive import default_scanner  # noqa: E402


class _Handler:
    def __init__(self, result=None) -> None:
        self.calls = 0
        self.result = result or {
            "schemaVersion": 1,
            "toolName": "read_file",
            "outcome": "success",
            "code": "ok",
            "summary": "ok",
            "data": {},
            "sideEffectsMayExist": False,
            "reconciliationRequired": False,
        }

    def execute(self, _run_id, _item, _call, _cancel) -> HandlerOutcome:
        self.calls += 1
        return HandlerOutcome(self.result, "completed")


class _WaitForCancellationHandler(_Handler):
    def execute(self, _run_id, _item, _call, cancel) -> HandlerOutcome:
        self.calls += 1
        while not cancel.is_set():
            cancel.wait(0.005)
        return HandlerOutcome(self.result, "completed")


class _ApprovalHandler(_Handler):
    def __init__(self, approval: ApprovalCoordinator) -> None:
        super().__init__()
        self.approval = approval
        self.execute_side_effect = None

    def execute(self, run_id, item, _call, cancel) -> HandlerOutcome:
        self.calls += 1
        assert self.execute_side_effect is not None
        approval, verified = self.execute_side_effect(
            run_id=run_id,
            item=item,
            prepared=PreparedToolExecution(
                approval_description={
                    "kind": "file_change",
                    "summary": "Modify a.txt",
                    "diff": "",
                },
                intent_preconditions={"path": "a.txt"},
                transition_reason="file_approval",
            ),
            cancel=cancel,
            execute=lambda: self.result,
        )
        self.assert_approved(approval.decision)
        assert verified is not None
        return HandlerOutcome(verified.result, "completed")

    @staticmethod
    def assert_approved(decision: str) -> None:
        if decision != "approve":
            raise AssertionError("approval fixture rejected")


class _Dispatcher:
    def validate_execution(self, call, plan) -> bool:
        return call.name == "read_file" and plan.execution_kind in {
            "read", "file", "shell", "external", "eidos_state",
            "network_eidos_state",
        }


class ToolExecutionControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-phase5b-tool-")
        root = Path(self.temporary.name)
        data = root / "data"
        workspace = root / "workspace"
        data.mkdir(mode=0o700)
        workspace.mkdir()
        self.store = SessionStore(data)
        self.store.initialize()
        session = self.store.create_session(str(workspace))
        self.run, _ = self.store.create_run(session["id"], "tool")
        self.store.increment_model_step(self.run["id"])
        self.call = ModelToolCall("provider-call", "read_file", {"path": "a.txt"})

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _item(self, order: int = 0):
        return self.store.create_tool_item(
            self.run["id"],
            1,
            order,
            f"provider-call-{order}",
            "read_file",
            json.dumps({"path": "a.txt"}),
        )

    def _controller(self, handlers):
        return ToolExecutionController(
            self.store,
            _Dispatcher(),
            handlers,
            RuntimeEvents(lambda _message: None),
            default_scanner(),
        )

    def test_approval_wait_does_not_consume_tool_timeout(self) -> None:
        now = [0.0]

        def approve(_request, cancel):
            now[0] = 10.0
            self.assertFalse(cancel.is_set())
            return ApprovalDecision("approve")

        approval = ApprovalCoordinator(
            self.store,
            approve,
            RuntimeEvents(lambda _message: None),
            RuntimePhaseTracker(),
            lambda _run_id: None,
            lambda: None,
            lambda _run_id, _cancel: None,
            lambda _run_id, _cancel: None,
            requeue=False,
        )
        handler = _ApprovalHandler(approval)
        controller = ToolExecutionController(
            self.store,
            _Dispatcher(),
            {"file": handler},
            RuntimeEvents(lambda _message: None),
            default_scanner(),
            approval=approval,
            monotonic=lambda: now[0],
        )
        handler.execute_side_effect = controller.execute_side_effect

        outcome = controller.execute(
            run_id=self.run["id"],
            item=self._item(),
            call=self.call,
            plan=ToolDispatchPlan(True, "file", 5, "workspace"),
            cancel=threading.Event(),
            deadline=None,
        )

        self.assertEqual(outcome.result["outcome"], "success")
        self.assertEqual(handler.calls, 1)

    def test_all_tool_kinds_route_through_controller(self) -> None:
        kinds = (
            "read", "file", "shell", "external",
            "eidos_state", "network_eidos_state",
        )
        handlers = {kind: _Handler() for kind in kinds}
        controller = self._controller(handlers)
        for order, kind in enumerate(kinds):
            outcome = controller.execute(
                run_id=self.run["id"],
                item=self._item(order),
                call=self.call,
                plan=ToolDispatchPlan(False, kind, 5, "none"),
                cancel=threading.Event(),
                deadline=None,
            )
            self.assertEqual(outcome.result["outcome"], "success")
        self.assertTrue(all(handler.calls == 1 for handler in handlers.values()))

    def test_tool_spec_timeout_is_enforced_without_abandoning_thread(self) -> None:
        handler = _WaitForCancellationHandler()
        controller = self._controller({"read": handler})
        started = time.monotonic()

        outcome = controller.execute(
            run_id=self.run["id"],
            item=self._item(),
            call=self.call,
            plan=ToolDispatchPlan(False, "read", 1, "none"),
            cancel=threading.Event(),
            deadline=started + 0.03,
        )

        self.assertEqual(outcome.result["code"], "TOOL_TIMEOUT")
        self.assertLess(time.monotonic() - started, 0.2)
        self.assertEqual(active_tool_execution_count(), 0)

    def test_cancel_wins_over_timeout(self) -> None:
        cancel = threading.Event()
        cancel.set()
        outcome = self._controller({"read": _Handler()}).execute(
            run_id=self.run["id"],
            item=self._item(),
            call=self.call,
            plan=ToolDispatchPlan(False, "read", 1, "none"),
            cancel=cancel,
            deadline=time.monotonic() - 1,
        )
        self.assertEqual(outcome.result["code"], "TOOL_CANCELED")

    def test_timeout_discards_late_success(self) -> None:
        outcome = self._controller(
            {"read": _WaitForCancellationHandler()}
        ).execute(
            run_id=self.run["id"],
            item=self._item(),
            call=self.call,
            plan=ToolDispatchPlan(False, "read", 1, "none"),
            cancel=threading.Event(),
            deadline=time.monotonic() + 0.02,
        )

        persisted = self.store.read_item(str(outcome.item["id"]))
        result = json.loads(persisted["toolCall"]["resultJson"])
        self.assertEqual(result["code"], "TOOL_TIMEOUT")

    def test_terminal_commit_is_idempotent(self) -> None:
        item = self._item()
        controller = self._controller({"read": _Handler()})
        first = controller.execute(
            run_id=self.run["id"],
            item=item,
            call=self.call,
            plan=ToolDispatchPlan(False, "read", 5, "none"),
            cancel=threading.Event(),
            deadline=None,
        )
        second = controller.execute(
            run_id=self.run["id"],
            item=item,
            call=self.call,
            plan=ToolDispatchPlan(False, "read", 5, "none"),
            cancel=threading.Event(),
            deadline=None,
        )
        self.assertEqual(first.item, second.item)

    def test_side_effect_timeout_requires_reconciliation(self) -> None:
        outcome = self._controller(
            {"external": _WaitForCancellationHandler()}
        ).execute(
            run_id=self.run["id"],
            item=self._item(),
            call=self.call,
            plan=ToolDispatchPlan(True, "external", 1, "external"),
            cancel=threading.Event(),
            deadline=time.monotonic() + 0.02,
        )
        self.assertTrue(outcome.result["sideEffectsMayExist"])
        self.assertTrue(outcome.result["reconciliationRequired"])


if __name__ == "__main__":
    unittest.main()

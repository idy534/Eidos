from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eidos_runtime.db.errors import InvalidRunStateError  # noqa: E402
from eidos_runtime.db.storage import SessionStore  # noqa: E402
from eidos_runtime.model.client import ModelToolCall  # noqa: E402
from eidos_runtime.runtime.approval import (  # noqa: E402
    ApprovalCoordinator,
    ApprovalDecision,
)
from eidos_runtime.runtime.events import RuntimeEvents  # noqa: E402
from eidos_runtime.runtime.state_machine import (  # noqa: E402
    RuntimePhaseTracker,
)
from eidos_runtime.runtime.tool_dispatcher import (  # noqa: E402
    ToolDispatchPlan,
)
from eidos_runtime.runtime.tool_execution import (  # noqa: E402
    HandlerOutcome,
    PreparedToolExecution,
    ToolExecutionController,
)
from eidos_runtime.sandbox.sensitive import default_scanner  # noqa: E402


_SUCCESS = {
    "schemaVersion": 1,
    "toolName": "effect",
    "outcome": "success",
    "code": "ok",
    "summary": "ok",
    "data": {},
    "sideEffectsMayExist": False,
    "reconciliationRequired": False,
}


class _Dispatcher:
    @staticmethod
    def validate_execution(_call, _plan) -> bool:
        return True


class _ControlledEffect:
    def __init__(self) -> None:
        self.controller: ToolExecutionController | None = None
        self.effect_called = False

    def execute(self, run_id, item, _call, cancel) -> HandlerOutcome:
        assert self.controller is not None
        approval, verified = self.controller.execute_side_effect(
            run_id=run_id,
            item=item,
            prepared=PreparedToolExecution(
                approval_description={
                    "kind": "external_tool",
                    "summary": "effect",
                },
                intent_preconditions={"target": "fixture"},
                transition_reason="external_approval",
            ),
            cancel=cancel,
            execute=self._effect,
        )
        if approval.decision != "approve":
            return HandlerOutcome(
                {
                    **_SUCCESS,
                    "outcome": "declined",
                    "code": "user_rejected",
                },
                "declined",
                "failed",
            )
        assert verified is not None
        return HandlerOutcome(verified.result, "completed")

    def _effect(self) -> dict[str, object]:
        self.effect_called = True
        return _SUCCESS


class ToolStateMachineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="eidos-phase5c-tool-"
        )
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
        self.call = ModelToolCall("provider-call", "effect", {})

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _item(self) -> dict[str, object]:
        return self.store.create_tool_item(
            self.run["id"],
            1,
            0,
            "provider-call",
            "effect",
            json.dumps({}),
        )

    def _controller(
        self, decision: str
    ) -> tuple[ToolExecutionController, _ControlledEffect]:
        events = RuntimeEvents(lambda _message: None)
        approval = ApprovalCoordinator(
            self.store,
            lambda _request, _cancel: ApprovalDecision(decision),
            events,
            RuntimePhaseTracker(),
            lambda _run_id: None,
            lambda: None,
            lambda _run_id, _cancel: None,
            lambda _run_id, _cancel: None,
            requeue=False,
        )
        handler = _ControlledEffect()
        controller = ToolExecutionController(
            self.store,
            _Dispatcher(),
            {"external": handler},
            events,
            default_scanner(),
            approval=approval,
        )
        handler.controller = controller
        return controller, handler

    def _execute(
        self, controller: ToolExecutionController
    ) -> HandlerOutcome:
        return controller.execute(
            run_id=self.run["id"],
            item=self._item(),
            call=self.call,
            plan=ToolDispatchPlan(True, "external", 5, "external"),
            cancel=threading.Event(),
            deadline=None,
        )

    def test_side_effect_tool_cannot_execute_without_approval(self) -> None:
        controller, handler = self._controller("reject")

        outcome = self._execute(controller)

        self.assertFalse(handler.effect_called)
        self.assertEqual(outcome.result["code"], "user_rejected")

    def test_side_effect_tool_cannot_execute_without_durable_intent(
        self,
    ) -> None:
        controller, handler = self._controller("approve")

        with patch.object(
            self.store,
            "begin_durable_intent",
            side_effect=InvalidRunStateError("missing intent"),
        ):
            outcome = self._execute(controller)

        self.assertFalse(handler.effect_called)
        self.assertEqual(outcome.result["outcome"], "error")

    def test_contract_gate_reads_authorization_from_sqlite(self) -> None:
        controller, handler = self._controller("approve")
        calls = 0
        original = self.store.side_effect_authorized

        def checked(item_id: str) -> bool:
            nonlocal calls
            calls += 1
            return original(item_id)

        with patch.object(
            self.store, "side_effect_authorized", side_effect=checked
        ):
            outcome = self._execute(controller)

        self.assertTrue(handler.effect_called)
        self.assertEqual(outcome.result["outcome"], "success")
        self.assertEqual(calls, 1)


if __name__ == "__main__":
    unittest.main()

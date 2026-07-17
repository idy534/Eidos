from __future__ import annotations

from pathlib import Path
import sys
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eidos_runtime.state_machine import (  # noqa: E402
    ApprovalStatus,
    RunStatus,
    RuntimeState,
    StateMachine,
    ensure_transition,
)


class StateMachineTests(unittest.TestCase):
    def test_runtime_state_machine_records_valid_reasons(self) -> None:
        machine = StateMachine()
        machine.transition(RuntimeState.TOOL_EXECUTING, "tools")
        machine.transition(RuntimeState.WAITING_APPROVAL, "approval")
        machine.transition(RuntimeState.TOOL_EXECUTING, "approved")
        self.assertEqual(machine.state, RuntimeState.TOOL_EXECUTING)
        self.assertEqual(machine.history[-1][2], "approved")

    def test_allows_declared_transition_and_rejects_illegal_or_mixed_transition(self) -> None:
        ensure_transition(RunStatus.QUEUED, RunStatus.RUNNING)
        with self.assertRaises(ValueError):
            ensure_transition(RunStatus.QUEUED, RunStatus.SUCCEEDED)
        with self.assertRaises(ValueError):
            ensure_transition(RunStatus.RUNNING, ApprovalStatus.CANCELED)


if __name__ == "__main__":
    unittest.main()

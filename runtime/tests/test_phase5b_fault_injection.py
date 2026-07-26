from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import threading
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eidos_runtime.db.storage import SessionStore  # noqa: E402
from tests.fault_injection import (  # noqa: E402
    FAULT_POINTS,
    FaultInjectionHarness,
    assert_runtime_converged,
)


class FaultInjectionHarnessTests(unittest.TestCase):
    def test_phase5b_fault_points_are_declared(self) -> None:
        self.assertEqual(len(FAULT_POINTS), 18)

    def test_barrier_controls_the_fault_boundary(self) -> None:
        harness = FaultInjectionHarness()
        completed = threading.Event()
        thread = threading.Thread(
            target=lambda: (
                harness.trigger("cancel_claim_race"),
                completed.set(),
            )
        )
        thread.start()
        self.assertTrue(
            harness.wait_until_entered("cancel_claim_race")
        )
        self.assertFalse(completed.is_set())
        harness.release("cancel_claim_race")
        thread.join(1)
        self.assertTrue(completed.is_set())

    def test_failure_injection_raises_exact_error(self) -> None:
        harness = FaultInjectionHarness()
        failure = OSError("append failed")
        harness.fail_with("sqlite_append_event_failure", failure)
        with self.assertRaisesRegex(OSError, "append failed"):
            harness.trigger("sqlite_append_event_failure")

    def test_convergence_check_accepts_atomic_terminal_run(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-fault-") as root:
            data = Path(root) / "data"
            workspace = Path(root) / "workspace"
            data.mkdir(mode=0o700)
            workspace.mkdir()
            store = SessionStore(data)
            store.initialize()
            session = store.create_session(str(workspace))
            run, _ = store.create_run(session["id"], "fault")
            store.cancel_run_committed(run["id"])
            assert_runtime_converged(store)
            store.close()


if __name__ == "__main__":
    unittest.main()

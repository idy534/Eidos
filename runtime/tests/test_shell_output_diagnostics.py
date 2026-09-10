from pathlib import Path
import subprocess
import time
import unittest
from unittest.mock import Mock

from eidos_runtime.runtime.shell_process_manager import (
    ShellProcessManager,
    ShellProcessSession,
)
from eidos_runtime.sandbox.shell import ShellLaunchSpec
from eidos_runtime.tools.contracts import RunShellResultData


class ShellOutputDiagnosticsTests(unittest.TestCase):
    def _snapshot(
        self,
        *,
        error: str | None = None,
        running: bool = False,
        exit_code: int | None = 0,
    ) -> dict[str, object]:
        session = ShellProcessSession(
            session_id="shell-output-diagnostics",
            owner_run_id="run-output-diagnostics",
            process=Mock(spec=subprocess.Popen),
            process_group_id=1,
            launch=ShellLaunchSpec(
                argv=("/bin/sh",),
                cwd=Path("/"),
                environment={},
                sandboxed=True,
            ),
            started_at=time.monotonic(),
            execution_status="running" if running else "exited",
            termination="running" if running else "exit",
            exit_code=None if running else exit_code,
            drain_error=error,
        )
        if not running:
            session.done.set()
        return ShellProcessManager()._wait_and_snapshot(session, 0, "run_shell")

    def test_known_exit_with_output_read_failure_does_not_require_reconciliation(self) -> None:
        result = self._snapshot(error="output_read_failed", exit_code=1)
        data = RunShellResultData.model_validate(result["data"])

        self.assertEqual(result["code"], "output_capture_failed")
        self.assertEqual(result["outcome"], "error")
        self.assertEqual(data.exitCode, 1)
        self.assertEqual(data.termination, "exit")
        self.assertEqual(data.outputCaptureError, "output_read_failed")
        self.assertFalse(data.outputComplete)
        self.assertFalse(result["reconciliationRequired"])

    def test_security_and_persistence_failures_still_require_reconciliation(self) -> None:
        for error in ("sensitive_content_rejected", "output_persistence_failed"):
            with self.subTest(error=error):
                result = self._snapshot(error=error)
                data = RunShellResultData.model_validate(result["data"])

                self.assertEqual(result["code"], "output_capture_failed")
                self.assertEqual(data.outputCaptureError, error)
                self.assertFalse(data.outputComplete)
                self.assertTrue(result["reconciliationRequired"])

    def test_output_is_complete_only_after_an_error_free_exit(self) -> None:
        for running in (True, False):
            with self.subTest(running=running):
                result = self._snapshot(running=running)
                data = RunShellResultData.model_validate(result["data"])

                self.assertEqual(data.outputComplete, not running)
                self.assertIsNone(data.outputCaptureError)
                self.assertEqual(result["code"], "shell_running" if running else "ok")
                self.assertFalse(result["reconciliationRequired"])

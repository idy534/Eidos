from __future__ import annotations

from pathlib import Path
import tempfile
import threading
import time
import unittest


import sys

RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.shell import run_shell  # noqa: E402


class ShellExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-shell-test-")
        self.workspace = Path(self.temporary.name).resolve()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_timeout_terminates_the_process_group(self) -> None:
        started = time.monotonic()
        result = run_shell(
            self.workspace,
            "/bin/sleep 5",
            self.workspace,
            1,
            threading.Event(),
            lambda _delta: None,
        )

        self.assertEqual(result["code"], "timeout")
        self.assertLess(time.monotonic() - started, 3)

    def test_cancel_kills_descendants_before_they_can_write(self) -> None:
        cancel = threading.Event()
        timer = threading.Timer(0.1, cancel.set)
        timer.start()
        try:
            result = run_shell(
                self.workspace,
                "(/bin/sleep 1; /usr/bin/touch late.txt) & wait",
                self.workspace,
                5,
                cancel,
                lambda _delta: None,
            )
        finally:
            timer.cancel()
        time.sleep(1.1)

        self.assertEqual(result["code"], "canceled")
        self.assertFalse((self.workspace / "late.txt").exists())

    def test_shell_receives_disposable_home_and_no_host_credentials(self) -> None:
        result = run_shell(
            self.workspace,
            'printf "%s|%s" "$HOME" "${DEEPSEEK_API_KEY-unset}"',
            self.workspace,
            5,
            threading.Event(),
            lambda _delta: None,
        )

        stdout = result["data"]["stdout"]
        self.assertIn("eidos-shell-", stdout)
        self.assertTrue(stdout.endswith("|unset"))


if __name__ == "__main__":
    unittest.main()

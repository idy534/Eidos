from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.sandbox.shell import _terminate_group, run_shell  # noqa: E402
from eidos_runtime.db.storage import WorkspaceIdentity  # noqa: E402


class _PassthroughProfile:
    @staticmethod
    def command(command):
        return list(command)

    @staticmethod
    def environment():
        return {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}


class ShellLifecycleUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(prefix="eidos-shell-unit-")
        self.workspace = Path(self.temporary_directory.name) / "workspace"
        self.workspace.mkdir()
        metadata = self.workspace.stat()
        self.identity = WorkspaceIdentity(
            path=self.workspace.resolve(),
            device=metadata.st_dev,
            inode=metadata.st_ino,
            owner=metadata.st_uid,
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _run_shell(self, command: str, timeout: int) -> dict[str, object]:
        with patch(
            "eidos_runtime.sandbox.shell.SeatbeltProfile.create",
            return_value=_PassthroughProfile(),
        ):
            return run_shell(
                self.identity,
                command,
                self.identity,
                timeout,
                threading.Event(),
                lambda _delta: None,
            )

    def test_background_process_is_detected_without_native_sandbox(self) -> None:
        result = self._run_shell("sleep 3 &", 1)

        self.assertEqual(result["code"], "background_process")

    def test_closed_output_pipes_do_not_impose_an_artificial_one_second_timeout(self) -> None:
        result = self._run_shell("exec >/dev/null 2>&1; sleep 1.2", 3)

        self.assertEqual(result["outcome"], "success")
        self.assertGreaterEqual(result["data"]["durationMs"], 1_000)

    def test_process_group_permission_race_does_not_escape_cleanup(self) -> None:
        with patch("eidos_runtime.sandbox.shell.os.killpg", side_effect=PermissionError):
            _terminate_group(12345)


@unittest.skipUnless(sys.platform == "darwin", "Seatbelt is macOS-only")
class ShellProcessGroupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(prefix="eidos-shell-test-")
        self.workspace = Path(self.temporary_directory.name) / "workspace"
        self.workspace.mkdir()
        metadata = self.workspace.stat()
        self.identity = WorkspaceIdentity(
            path=self.workspace.resolve(),
            device=metadata.st_dev,
            inode=metadata.st_ino,
            owner=metadata.st_uid,
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_background_process_inheriting_pipes_is_killed_without_hanging(self) -> None:
        started = time.monotonic()

        result = run_shell(
            self.identity,
            "sleep 3 &",
            self.identity,
            1,
            threading.Event(),
            lambda _delta: None,
        )

        self.assertLess(time.monotonic() - started, 2.0)
        self.assertEqual(result["outcome"], "error")
        self.assertEqual(result["code"], "background_process")

    def test_redirected_background_process_cannot_write_after_result(self) -> None:
        result = run_shell(
            self.identity,
            "(sleep 1; printf late > late.txt) >/dev/null 2>&1 &",
            self.identity,
            5,
            threading.Event(),
            lambda _delta: None,
        )

        self.assertEqual(result["outcome"], "error")
        self.assertEqual(result["code"], "background_process")
        time.sleep(1.2)
        self.assertFalse((self.workspace / "late.txt").exists())

    def test_timeout_kills_process_group_even_when_term_is_ignored(self) -> None:
        result = run_shell(
            self.identity,
            "trap '' TERM; while :; do sleep 1; done",
            self.identity,
            1,
            threading.Event(),
            lambda _delta: None,
        )

        self.assertEqual(result["outcome"], "error")
        self.assertEqual(result["code"], "timeout")
        self.assertLess(result["data"]["durationMs"], 3_000)

    def test_command_that_closes_output_pipes_can_finish_normally(self) -> None:
        result = run_shell(
            self.identity,
            "exec >/dev/null 2>&1; sleep 1.2",
            self.identity,
            3,
            threading.Event(),
            lambda _delta: None,
        )

        self.assertEqual(result["outcome"], "success")
        self.assertGreaterEqual(result["data"]["durationMs"], 1_000)


if __name__ == "__main__":
    unittest.main()

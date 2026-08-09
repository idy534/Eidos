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
from eidos_runtime.sandbox.seatbelt import (  # noqa: E402
    SeatbeltProfile,
    is_seatbelt_ready,
)
from eidos_runtime.sandbox.permissions import (  # noqa: E402
    BasePermissionProfile,
    SandboxAttempt,
    SandboxType,
    materialize_effective_profile,
)
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

    def unsandboxed_attempt(self) -> SandboxAttempt:
        permissions = materialize_effective_profile(
            BasePermissionProfile.for_workspace(workspace_root=self.workspace)
        )
        return SandboxAttempt(
            ordinal=0,
            sandbox=SandboxType.NONE,
            sandboxRequested=False,
            permissions=permissions,
            sandboxCwd=str(self.workspace),
            workspaceRoots=(str(self.workspace),),
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

    def test_unsandboxed_attempt_bypasses_seatbelt_but_keeps_supervision(self) -> None:
        deltas = []
        with patch.object(
            SeatbeltProfile,
            "command",
            side_effect=AssertionError("sandbox-exec must not be used"),
        ):
            success = run_shell(
                self.identity,
                'printf "host-ok|${SSH_AUTH_SOCK-unset}"',
                self.identity,
                2,
                threading.Event(),
                deltas.append,
                attempt=self.unsandboxed_attempt(),
            )
            timeout = run_shell(
                self.identity,
                "trap '' TERM; while :; do sleep 1; done",
                self.identity,
                1,
                threading.Event(),
                lambda _delta: None,
                attempt=self.unsandboxed_attempt(),
            )
            cancel = threading.Event()
            timer = threading.Timer(0.1, cancel.set)
            timer.start()
            canceled = run_shell(
                self.identity,
                "sleep 5",
                self.identity,
                2,
                cancel,
                lambda _delta: None,
                attempt=self.unsandboxed_attempt(),
            )
            timer.join()

        self.assertEqual(success["outcome"], "success")
        self.assertEqual("".join(deltas), "host-ok|unset")
        self.assertTrue(success["sideEffectsMayExist"])
        self.assertEqual(timeout["code"], "timeout")
        self.assertEqual(canceled["code"], "canceled")
        self.assertLess(timeout["data"]["durationMs"], 3_000)

    def test_shell_supports_workspace_paths_with_spaces(self) -> None:
        workspace = self.workspace.parent / "workspace with spaces"
        workspace.mkdir()
        metadata = workspace.stat()
        identity = WorkspaceIdentity(
            path=workspace.resolve(),
            device=metadata.st_dev,
            inode=metadata.st_ino,
            owner=metadata.st_uid,
        )

        with patch(
            "eidos_runtime.sandbox.shell.SeatbeltProfile.create",
            return_value=_PassthroughProfile(),
        ):
            result = run_shell(
                identity,
                "pwd",
                identity,
                2,
                threading.Event(),
                lambda _delta: None,
            )

        self.assertEqual(result["outcome"], "success")
        self.assertEqual(result["data"]["stdout"].strip(), str(workspace.resolve()))


@unittest.skipUnless(sys.platform == "darwin", "Seatbelt is macOS-only")
class ShellProcessGroupTests(unittest.TestCase):
    def setUp(self) -> None:
        if not is_seatbelt_ready():
            self.skipTest(
                "Seatbelt process-group integration requires a currently usable sandbox-exec and static resources"
            )
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

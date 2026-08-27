from __future__ import annotations

from datetime import datetime, timezone
from collections.abc import Mapping
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.sandbox.host_shell import (  # noqa: E402
    CAPTURE_MARKER,
    HostShell,
    HostShellUnavailableError,
    ShellEnvironmentSnapshot,
    ShellEnvironmentSnapshotProvider,
)
from eidos_runtime.sandbox.shell import _terminate_group, run_shell  # noqa: E402
from eidos_runtime.sandbox.shell import prepare_shell_launch  # noqa: E402
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


class _FixedHostShellResolver:
    def __init__(self, shell: HostShell) -> None:
        self.shell = shell

    def resolve(self) -> HostShell:
        return self.shell


class _FixedSnapshotProvider:
    def __init__(self, snapshot: ShellEnvironmentSnapshot) -> None:
        self.snapshot = snapshot
        self.calls: list[tuple[HostShell, Path]] = []

    def get(
        self,
        shell: HostShell,
        cwd: Path,
        *,
        command_wrapper: object | None = None,
    ) -> ShellEnvironmentSnapshot:
        del command_wrapper
        self.calls.append((shell, cwd))
        return self.snapshot

    def fallback_environment(self, shell: HostShell) -> dict[str, str]:
        del shell
        return dict(self.snapshot.environment)


class _SnapshotRunner:
    def __init__(self, stdout: bytes) -> None:
        self.stdout = stdout
        self.calls: list[dict[str, object]] = []

    def __call__(
        self,
        argv: tuple[str, ...],
        cwd: Path,
        environment: Mapping[str, str],
        timeout_seconds: float,
        output_limit_bytes: int,
    ) -> subprocess.CompletedProcess[bytes]:
        self.calls.append({
            "argv": argv,
            "cwd": cwd,
            "environment": dict(environment),
            "timeout_seconds": timeout_seconds,
            "output_limit_bytes": output_limit_bytes,
        })
        return subprocess.CompletedProcess(argv, 0, stdout=self.stdout, stderr=b"")


def _snapshot_output(values: Mapping[str, str]) -> bytes:
    records = b"\0".join(
        f"{key}={value}".encode("utf-8") for key, value in values.items()
    )
    return CAPTURE_MARKER.encode("ascii") + b"\0" + records + b"\0"


class ShellLifecycleUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory(prefix="eidos-shell-unit-")
        self.workspace = Path(self.temporary_directory.name) / "workspace"
        self.workspace.mkdir()
        self.home = Path(self.temporary_directory.name) / "real-home"
        self.home.mkdir()
        self.tmpdir = Path(self.temporary_directory.name) / "real-tmp"
        self.tmpdir.mkdir()
        metadata = self.workspace.stat()
        self.identity = WorkspaceIdentity(
            path=self.workspace.resolve(),
            device=metadata.st_dev,
            inode=metadata.st_ino,
            owner=metadata.st_uid,
        )
        self.host_shell = HostShell(Path("/bin/sh"), "sh")
        self.host_snapshot = ShellEnvironmentSnapshot(
            shell=self.host_shell,
            environment={
                "HOME": str(self.home),
                "TMPDIR": str(self.tmpdir),
                "PATH": "/usr/bin:/bin",
                "SHELL": str(self.host_shell.executable),
                "USER": "test-user",
                "LOGNAME": "test-user",
                "LANG": "en_US.UTF-8",
            },
            captured_at=datetime(2026, 8, 26, tzinfo=timezone.utc),
            source="captured",
            diagnostic="captured",
        )
        self.host_resolver = _FixedHostShellResolver(self.host_shell)
        self.snapshot_provider = _FixedSnapshotProvider(self.host_snapshot)
        self.host_resolver_patch = patch(
            "eidos_runtime.sandbox.shell.HOST_SHELL_RESOLVER",
            self.host_resolver,
        )
        self.snapshot_provider_patch = patch(
            "eidos_runtime.sandbox.shell.SHELL_ENVIRONMENT_PROVIDER",
            self.snapshot_provider,
        )
        self.host_resolver_patch.start()
        self.snapshot_provider_patch.start()

    def tearDown(self) -> None:
        self.snapshot_provider_patch.stop()
        self.host_resolver_patch.stop()
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

    def test_prepare_shell_launch_uses_resolved_shell_without_login_mode(self) -> None:
        launch = prepare_shell_launch(
            profile=_PassthroughProfile(),
            command="printf shell",
            cwd=self.identity,
            attempt=None,
            shell=self.host_shell,
            environment=self.host_snapshot.environment,
        )

        self.assertEqual(
            launch.argv,
            (str(self.host_shell.executable), "-c", "printf shell"),
        )

    def test_run_shell_uses_snapshot_home_and_host_path_with_bundled_rg_last(self) -> None:
        binary = Path(__file__).resolve().parents[1] / "eidos_runtime" / "resources" / "bin" / "ripgrep" / "darwin-arm64" / "rg"
        if not binary.is_file():
            self.skipTest("bundled ripgrep is unavailable")
        command = (
            "printf '%s\\n' \"$HOME\" \"$TMPDIR\" \"$SHELL\" \"$PATH\" "
            "${GIT_CONFIG_GLOBAL-unset} ${GIT_CONFIG_SYSTEM-unset} "
            "${GIT_CONFIG_NOSYSTEM-unset} ${GIT_ASKPASS-unset} "
            "${GIT_TERMINAL_PROMPT-unset} ${GIT_OPTIONAL_LOCKS-unset} "
            "${PNPM_CONFIG_PM_ON_FAIL-unset}"
        )

        with (
            patch(
                "eidos_runtime.sandbox.shell.RipgrepBinaryResolver.resolve",
                return_value=binary,
            ),
            patch(
                "eidos_runtime.sandbox.shell.SeatbeltProfile.create",
                return_value=_PassthroughProfile(),
            ),
        ):
            result = run_shell(
                self.identity,
                command,
                self.identity,
                2,
                threading.Event(),
                lambda _delta: None,
            )

        self.assertEqual(result["outcome"], "success")
        lines = result["data"]["stdout"].splitlines()
        self.assertEqual(lines[0], str(self.home))
        self.assertEqual(lines[1], str(self.tmpdir))
        self.assertEqual(lines[2], str(self.host_shell.executable))
        path_entries = lines[3].split(":")
        self.assertEqual(path_entries[:2], ["/usr/bin", "/bin"])
        self.assertEqual(path_entries[-1], str(binary.parent))
        self.assertEqual(lines[4:], ["unset"] * 7)

    def test_run_shell_resolves_tools_from_snapshot_host_path(self) -> None:
        tool_directory = self.home / ".local" / "bin"
        tool_directory.mkdir(parents=True)
        tool = tool_directory / "host-tool"
        tool.write_text("#!/bin/sh\nprintf '%s\\n' host-tool\n", encoding="utf-8")
        tool.chmod(0o755)
        snapshot = ShellEnvironmentSnapshot(
            shell=self.host_shell,
            environment={
                **self.host_snapshot.environment,
                "PATH": os.pathsep.join((str(tool_directory), "/usr/bin", "/bin")),
            },
            captured_at=self.host_snapshot.captured_at,
            source=self.host_snapshot.source,
            diagnostic=self.host_snapshot.diagnostic,
        )
        self.snapshot_provider.snapshot = snapshot

        result = self._run_shell(
            "command -v host-tool; host-tool; printf '%s\\n' \"$PATH\"", 2
        )

        self.assertEqual(result["outcome"], "success")
        lines = result["data"]["stdout"].splitlines()
        self.assertEqual(
            lines[:2],
            [str(tool), "host-tool"],
        )
        path_entries = lines[2].split(os.pathsep)
        runtime_directories = {
            str(Path(sys.executable).parent),
            str(Path(sys.prefix) / "bin"),
            str(Path(sys.base_prefix) / "bin"),
        }
        self.assertTrue(runtime_directories.isdisjoint(path_entries))

    def test_two_runs_reuse_one_host_environment_capture(self) -> None:
        runner = _SnapshotRunner(_snapshot_output({
            "HOME": str(self.home),
            "TMPDIR": str(self.tmpdir),
            "PATH": "/usr/bin:/bin",
            "SHELL": str(self.host_shell.executable),
            "USER": "test-user",
            "LOGNAME": "test-user",
            "LANG": "en_US.UTF-8",
        }))
        provider = ShellEnvironmentSnapshotProvider(
            runner=runner,
            parent_environment={"HOME": str(self.home), "PATH": "/usr/bin:/bin"},
        )

        with (
            patch("eidos_runtime.sandbox.shell.SHELL_ENVIRONMENT_PROVIDER", provider),
            patch(
                "eidos_runtime.sandbox.shell.SeatbeltProfile.create",
                return_value=_PassthroughProfile(),
            ),
        ):
            first = run_shell(
                self.identity,
                "true",
                self.identity,
                2,
                threading.Event(),
                lambda _delta: None,
            )
            second = run_shell(
                self.identity,
                "true",
                self.identity,
                2,
                threading.Event(),
                lambda _delta: None,
            )

        self.assertEqual(first["outcome"], "success")
        self.assertEqual(second["outcome"], "success")
        self.assertEqual(len(runner.calls), 1)

    def test_invalid_tmpdir_uses_canonical_tmp_before_profile_creation(self) -> None:
        invalid_snapshot = ShellEnvironmentSnapshot(
            shell=self.host_shell,
            environment={
                "HOME": str(self.home),
                "TMPDIR": "relative-tmp",
                "PATH": "/usr/bin:/bin",
                "SHELL": str(self.host_shell.executable),
                "USER": "test-user",
                "LOGNAME": "test-user",
                "LANG": "en_US.UTF-8",
            },
            captured_at=self.host_snapshot.captured_at,
            source="captured",
            diagnostic="captured",
        )
        with (
            patch(
                "eidos_runtime.sandbox.shell.SHELL_ENVIRONMENT_PROVIDER.get",
                return_value=invalid_snapshot,
            ),
            patch(
                "eidos_runtime.sandbox.shell.SeatbeltProfile.create",
                return_value=_PassthroughProfile(),
            ) as create,
        ):
            result = run_shell(
                self.identity,
                "printf '%s' \"$TMPDIR\"",
                self.identity,
                2,
                threading.Event(),
                lambda _delta: None,
            )

        self.assertEqual(result["outcome"], "success")
        self.assertEqual(result["data"]["stdout"], str(Path("/tmp").resolve()))
        self.assertEqual(create.call_args.kwargs["sandbox_tmp"], Path("/tmp").resolve())

    def test_unavailable_host_shell_returns_not_started_without_exception_text(self) -> None:
        with patch.object(
            self.host_resolver,
            "resolve",
            side_effect=HostShellUnavailableError("secret shell detail"),
        ):
            result = run_shell(
                self.identity,
                "true",
                self.identity,
                2,
                threading.Event(),
                lambda _delta: None,
            )

        self.assertEqual(result["outcome"], "error")
        self.assertEqual(result["code"], "process_start_failed")
        self.assertEqual(result["data"]["termination"], "not_started")
        self.assertNotIn("secret shell detail", str(result))

    def test_popen_start_error_returns_process_start_failed(self) -> None:
        with (
            patch(
                "eidos_runtime.sandbox.shell.SeatbeltProfile.create",
                return_value=_PassthroughProfile(),
            ),
            patch(
                "eidos_runtime.sandbox.shell.subprocess.Popen",
                side_effect=OSError("secret startup detail"),
            ),
        ):
            result = run_shell(
                self.identity,
                "true",
                self.identity,
                2,
                threading.Event(),
                lambda _delta: None,
            )

        self.assertEqual(result["code"], "process_start_failed")
        self.assertEqual(result["data"]["termination"], "not_started")
        self.assertNotIn("secret startup detail", str(result))

    def test_os_read_error_after_process_start_is_not_not_started(self) -> None:
        real_popen = subprocess.Popen
        read_patch = patch(
            "eidos_runtime.sandbox.shell.os.read",
            side_effect=OSError("secret read detail"),
        )

        def start_process(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
            process = real_popen(*args, **kwargs)
            read_patch.start()
            return process

        try:
            with (
                patch(
                    "eidos_runtime.sandbox.shell.SeatbeltProfile.create",
                    return_value=_PassthroughProfile(),
                ),
                patch(
                    "eidos_runtime.sandbox.shell.subprocess.Popen",
                    side_effect=start_process,
                ),
            ):
                with self.assertRaises(OSError):
                    run_shell(
                        self.identity,
                        "printf output",
                        self.identity,
                        2,
                        threading.Event(),
                        lambda _delta: None,
                    )
        finally:
            read_patch.stop()

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

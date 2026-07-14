from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
from typing import Sequence


SANDBOX_EXECUTABLE = "/usr/bin/sandbox-exec"
PROFILE_PATH = Path(__file__).with_name("seatbelt.sbpl")
FILE_COMMIT_HELPER = Path(__file__).with_name("file_commit_helper.py")
SYSTEM_PYTHON = "/Library/Developer/CommandLineTools/usr/bin/python3"


@dataclass(frozen=True)
class SeatbeltProfile:
    workspace_root: Path
    sandbox_home: Path
    sandbox_tmp: Path
    git_directory: Path
    sensitive_path: Path

    @classmethod
    def create(
        cls,
        *,
        workspace_root: Path,
        sandbox_home: Path,
        sandbox_tmp: Path,
        sensitive_path: Path,
    ) -> SeatbeltProfile:
        workspace = _existing_directory(workspace_root, "workspace root")
        home = _existing_directory(sandbox_home, "sandbox home")
        temporary = _existing_directory(sandbox_tmp, "sandbox tmp")
        git_directory = workspace / ".git"
        if git_directory.is_symlink() or not git_directory.exists():
            raise ValueError("workspace .git must exist and must not be a symlink")
        git_directory = git_directory.resolve()

        sensitive = sensitive_path.resolve()
        if sensitive != workspace and workspace not in sensitive.parents:
            raise ValueError("sensitive path must be inside workspace")
        if sensitive_path.is_symlink():
            raise ValueError("sensitive path must not be a symlink")

        return cls(
            workspace_root=workspace,
            sandbox_home=home,
            sandbox_tmp=temporary,
            git_directory=git_directory,
            sensitive_path=sensitive,
        )

    def command(self, command: Sequence[str]) -> list[str]:
        if not command or any(not isinstance(argument, str) for argument in command):
            raise ValueError("sandbox command must contain string arguments")
        return [
            SANDBOX_EXECUTABLE,
            "-f",
            str(PROFILE_PATH),
            f"-DWORKSPACE_ROOT={self.workspace_root}",
            f"-DGIT_DIR={self.git_directory}",
            f"-DSENSITIVE_PATH={self.sensitive_path}",
            f"-DSANDBOX_HOME={self.sandbox_home}",
            f"-DSANDBOX_TMP={self.sandbox_tmp}",
            f"-DFILE_COMMIT_HELPER={FILE_COMMIT_HELPER}",
            "--",
            *command,
        ]

    def environment(self) -> dict[str, str]:
        return {
            "HOME": str(self.sandbox_home),
            "TMPDIR": str(self.sandbox_tmp),
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "LANG": "en_US.UTF-8",
            "LC_ALL": "en_US.UTF-8",
            "GIT_OPTIONAL_LOCKS": "0",
        }


@dataclass(frozen=True)
class SandboxCommandResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_seconds: float


@dataclass(frozen=True)
class SeatbeltSelfTestResult:
    available: bool
    passed_checks: tuple[str, ...]
    failures: tuple[str, ...]


def run_sandboxed(
    profile: SeatbeltProfile,
    command: Sequence[str],
    *,
    timeout_seconds: float = 2.0,
) -> SandboxCommandResult:
    started_at = time.monotonic()
    process = subprocess.Popen(
        profile.command(command),
        cwd=profile.workspace_root,
        env=profile.environment(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
        return SandboxCommandResult(
            returncode=process.returncode,
            stdout=stdout,
            stderr=stderr,
            timed_out=False,
            duration_seconds=time.monotonic() - started_at,
        )
    except subprocess.TimeoutExpired:
        _terminate_process_group(process)
        stdout, stderr = process.communicate()
        return SandboxCommandResult(
            returncode=process.returncode,
            stdout=stdout,
            stderr=stderr,
            timed_out=True,
            duration_seconds=time.monotonic() - started_at,
        )


def secure_workspace_move(
    workspace_root: Path,
    source: Path,
    target: Path,
    expected_sha256: str | None,
) -> str:
    """Perform the final rename under Seatbelt; never fall back unsandboxed."""
    if sys.platform != "darwin" or not Path(SANDBOX_EXECUTABLE).is_file():
        return "failed"
    workspace = workspace_root.absolute()
    try:
        source.absolute().relative_to(workspace)
        target.absolute().relative_to(workspace)
    except ValueError:
        return "failed"
    command = [
        SANDBOX_EXECUTABLE,
        "-f",
        str(PROFILE_PATH),
        f"-DWORKSPACE_ROOT={workspace}",
        f"-DGIT_DIR={workspace / '.git'}",
        f"-DSENSITIVE_PATH={workspace / '.env'}",
        f"-DSANDBOX_HOME={workspace / '.eidos-sandbox-home-unavailable'}",
        f"-DSANDBOX_TMP={workspace / '.eidos-sandbox-tmp-unavailable'}",
        f"-DFILE_COMMIT_HELPER={FILE_COMMIT_HELPER}",
        "--",
        SYSTEM_PYTHON,
        str(FILE_COMMIT_HELPER),
        str(source),
        str(target),
        expected_sha256 or "new",
    ]
    try:
        completed = subprocess.run(
            command,
            cwd=workspace,
            env={
                "HOME": "/var/empty",
                "TMPDIR": "/private/tmp",
                "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
                "LANG": "en_US.UTF-8",
                "LC_ALL": "en_US.UTF-8",
            },
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
            start_new_session=False,
        )
    except OSError:
        return "failed"
    except subprocess.TimeoutExpired:
        return "uncertain"
    return {
        0: "committed",
        10: "conflict",
        11: "uncertain",
        12: "failed",
    }.get(completed.returncode, "failed")


def run_seatbelt_self_test() -> SeatbeltSelfTestResult:
    if sys.platform != "darwin":
        return SeatbeltSelfTestResult(False, (), ("unsupported_platform",))
    if not Path(SANDBOX_EXECUTABLE).is_file() or not PROFILE_PATH.is_file():
        return SeatbeltSelfTestResult(False, (), ("seatbelt_unavailable",))

    passed: list[str] = []
    failures: list[str] = []

    def record(name: str, condition: bool) -> None:
        (passed if condition else failures).append(name)

    try:
        with tempfile.TemporaryDirectory(prefix="eidos-seatbelt-") as directory:
            root = Path(directory)
            workspace = root / "workspace"
            sandbox_home = root / "sandbox-home"
            sandbox_tmp = root / "sandbox-tmp"
            outside = root / "outside"
            git_directory = workspace / ".git"
            for path in (workspace, sandbox_home, sandbox_tmp, outside, git_directory):
                path.mkdir(parents=True)

            sensitive = workspace / ".env"
            sensitive.write_text("self-test-secret", encoding="utf-8")
            (git_directory / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
            sentinel = outside / "sentinel.txt"
            sentinel.write_text("outside-sentinel", encoding="utf-8")
            (workspace / "escape").symlink_to(outside, target_is_directory=True)

            profile = SeatbeltProfile.create(
                workspace_root=workspace,
                sandbox_home=sandbox_home,
                sandbox_tmp=sandbox_tmp,
                sensitive_path=sensitive,
            )
            workspace = profile.workspace_root
            sandbox_home = profile.sandbox_home
            sandbox_tmp = profile.sandbox_tmp
            git_directory = profile.git_directory
            sensitive = profile.sensitive_path
            outside = outside.resolve()
            sentinel = outside / "sentinel.txt"

            record("system_execute", _succeeded(profile, ["/usr/bin/true"]))
            workspace_file = workspace / "created.txt"
            record("workspace_write", _create_modify_delete(profile, workspace_file))
            home_file = sandbox_home / "home.txt"
            record("sandbox_home_write", _create_modify_delete(profile, home_file))
            tmp_file = sandbox_tmp / "tmp.txt"
            record("sandbox_tmp_write", _create_modify_delete(profile, tmp_file))

            record(
                "external_read_denied",
                _read_denied(profile, sentinel, "outside-sentinel"),
            )
            outside_write = outside / "blocked.txt"
            record(
                "external_write_denied",
                not _succeeded(profile, ["/usr/bin/touch", str(outside_write)])
                and not outside_write.exists(),
            )
            record(
                "sensitive_read_denied",
                _read_denied(profile, sensitive, "self-test-secret"),
            )
            record(
                "sensitive_write_denied",
                not _succeeded(profile, ["/usr/bin/touch", str(sensitive)]),
            )
            record("git_read_allowed", _succeeded(profile, ["/bin/cat", str(git_directory / "HEAD")]))
            git_write = git_directory / "blocked"
            record(
                "git_write_denied",
                not _succeeded(profile, ["/usr/bin/touch", str(git_write)])
                and not git_write.exists(),
            )
            symlink_write = workspace / "escape" / "blocked"
            record(
                "symlink_escape_denied",
                not _succeeded(profile, ["/usr/bin/touch", str(symlink_write)])
                and not symlink_write.exists(),
            )
            child_write = outside / "child-blocked"
            record(
                "child_inherits_policy",
                not _succeeded(
                    profile,
                    ["/bin/sh", "-c", '/usr/bin/touch "$1"', "eidos-self-test", str(child_write)],
                )
                and not child_write.exists(),
            )

            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
                listener.bind(("127.0.0.1", 0))
                listener.listen(1)
                port = listener.getsockname()[1]
                network_command = [
                    "/usr/bin/nc",
                    "-z",
                    "-w",
                    "1",
                    "127.0.0.1",
                    str(port),
                ]
                baseline = subprocess.run(
                    network_command,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=2,
                    check=False,
                )
                record("loopback_baseline", baseline.returncode == 0)
                record(
                    "loopback_denied",
                    not _succeeded(profile, network_command),
                )

            timeout_result = run_sandboxed(
                profile,
                ["/bin/sh", "-c", "/bin/sleep 5"],
                timeout_seconds=0.1,
            )
            record(
                "timeout_enforced",
                timeout_result.timed_out and timeout_result.duration_seconds < 2.0,
            )
    except Exception:
        failures.append("self_test_error")

    return SeatbeltSelfTestResult(not failures, tuple(passed), tuple(failures))


def _existing_directory(path: Path, label: str) -> Path:
    resolved = path.resolve()
    if path.is_symlink() or not resolved.is_dir():
        raise ValueError(f"{label} must be an existing non-symlink directory")
    return resolved


def _succeeded(profile: SeatbeltProfile, command: Sequence[str]) -> bool:
    return run_sandboxed(profile, command).returncode == 0


def _read_denied(profile: SeatbeltProfile, path: Path, forbidden_content: str) -> bool:
    result = run_sandboxed(profile, ["/bin/cat", str(path)])
    return result.returncode != 0 and forbidden_content not in result.stdout


def _create_modify_delete(profile: SeatbeltProfile, path: Path) -> bool:
    created = _succeeded(profile, ["/usr/bin/touch", str(path)]) and path.exists()
    modified = (
        _succeeded(
            profile,
            ["/bin/sh", "-c", 'printf eidos-self-test > "$1"', "eidos-self-test", str(path)],
        )
        and path.read_text(encoding="utf-8") == "eidos-self-test"
    )
    deleted = _succeeded(profile, ["/bin/rm", str(path)]) and not path.exists()
    return created and modified and deleted


def _terminate_process_group(process: subprocess.Popen[str]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

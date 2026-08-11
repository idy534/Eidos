from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from pathlib import Path
import selectors
import signal
import subprocess
import time
from typing import Sequence

from eidos_runtime.git.errors import (
    GitCommandFailedError,
    GitCommandTimeoutError,
)


DEFAULT_GIT_TIMEOUT_SECONDS = 15.0
DEFAULT_GIT_OUTPUT_BYTES = 128 * 1024
DEFAULT_GIT_DIFF_BYTES = 512 * 1024


@dataclass(frozen=True)
class GitCommandResult:
    stdout: str
    stderr: str
    returncode: int
    stdout_truncated: bool
    stderr_truncated: bool


class GitProcess:
    """The only Runtime subprocess seam for the fixed Git operations."""

    def __init__(
        self,
        *,
        git_executable: str = "git",
        timeout_seconds: float = DEFAULT_GIT_TIMEOUT_SECONDS,
        output_limit_bytes: int = DEFAULT_GIT_OUTPUT_BYTES,
        logger: logging.Logger | None = None,
    ) -> None:
        if not git_executable or timeout_seconds <= 0 or output_limit_bytes < 1:
            raise ValueError("Git process configuration is invalid")
        self.git_executable = git_executable
        self.timeout_seconds = timeout_seconds
        self.output_limit_bytes = output_limit_bytes
        self.logger = logger or logging.getLogger(__name__)

    def rev_parse_show_toplevel(self, cwd: Path) -> str:
        return self._one_line(
            "rev-parse-show-toplevel",
            cwd,
            ("rev-parse", "--show-toplevel"),
        )

    def rev_parse_git_dir(self, cwd: Path) -> str:
        return self._one_line(
            "rev-parse-git-dir",
            cwd,
            ("rev-parse", "--git-dir"),
        )

    def rev_parse_git_common_dir(self, cwd: Path) -> str:
        return self._one_line(
            "rev-parse-git-common-dir",
            cwd,
            ("rev-parse", "--git-common-dir"),
        )

    def resolve_ref(self, cwd: Path, ref: str) -> str:
        _validate_ref(ref)
        return self._one_line(
            "rev-parse-ref",
            cwd,
            ("rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"),
        )

    def try_resolve_ref(self, cwd: Path, ref: str) -> str | None:
        _validate_ref(ref)
        result = self._execute(
            "rev-parse-ref",
            cwd,
            ("rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"),
            output_limit_bytes=self.output_limit_bytes,
        )
        self._reject_truncated_result("rev-parse-ref", result)
        if result.returncode == 0:
            return self._single_line_result("rev-parse-ref", result)
        if result.returncode in (1, 128):
            return None
        raise GitCommandFailedError(
            "rev-parse-ref",
            returncode=result.returncode,
            stderr=result.stderr,
        )

    def symbolic_ref_short(self, cwd: Path) -> str | None:
        result = self._execute(
            "symbolic-ref-short",
            cwd,
            ("symbolic-ref", "--quiet", "--short", "HEAD"),
            output_limit_bytes=self.output_limit_bytes,
        )
        self._reject_truncated_result("symbolic-ref-short", result)
        if result.returncode == 0:
            return self._single_line_result("symbolic-ref-short", result)
        if result.returncode == 1:
            return None
        raise GitCommandFailedError(
            "symbolic-ref-short",
            returncode=result.returncode,
            stderr=result.stderr,
        )

    def worktree_list(self, cwd: Path) -> GitCommandResult:
        return self._run(
            "worktree-list",
            cwd,
            ("worktree", "list", "--porcelain", "-z"),
        )

    def worktree_add(
        self,
        cwd: Path,
        worktree_root: Path,
        branch: str,
        base_commit: str,
    ) -> None:
        _validate_branch(branch)
        _validate_ref(base_commit)
        self._run(
            "worktree-add",
            cwd,
            (
                "worktree",
                "add",
                "--quiet",
                "-b",
                branch,
                str(worktree_root),
                base_commit,
            ),
        )

    def worktree_remove(self, cwd: Path, worktree_root: Path) -> None:
        self._run(
            "worktree-remove",
            cwd,
            ("worktree", "remove", str(worktree_root)),
        )

    def worktree_prune(self, cwd: Path) -> None:
        self._run(
            "worktree-prune",
            cwd,
            ("worktree", "prune", "--verbose"),
        )

    def status_porcelain_v2(self, cwd: Path) -> GitCommandResult:
        return self._run(
            "status-porcelain-v2",
            cwd,
            (
                "status",
                "--porcelain=v2",
                "-z",
                "--untracked-files=all",
                "--no-renames",
            ),
        )

    def diff_head(self, cwd: Path) -> GitCommandResult:
        return self._run(
            "diff-head",
            cwd,
            ("diff", "--no-ext-diff", "--no-color", "--no-renames", "HEAD", "--"),
            output_limit_bytes=DEFAULT_GIT_DIFF_BYTES,
        )

    def diff_baseline(self, cwd: Path, base_commit: str) -> GitCommandResult:
        _validate_ref(base_commit)
        return self._run(
            "diff-baseline",
            cwd,
            (
                "diff",
                "--no-ext-diff",
                "--no-color",
                "--no-renames",
                base_commit,
                "--",
            ),
            output_limit_bytes=DEFAULT_GIT_DIFF_BYTES,
        )

    def diff_name_only(
        self,
        cwd: Path,
        *,
        scope: str,
        base_commit: str | None = None,
    ) -> GitCommandResult:
        if scope == "head":
            ref_args: tuple[str, ...] = ("HEAD", "--")
        elif scope == "baseline" and base_commit is not None:
            _validate_ref(base_commit)
            ref_args = (base_commit, "--")
        else:
            raise ValueError("Git diff scope is invalid")
        return self._run(
            f"diff-{scope}-names",
            cwd,
            (
                "diff",
                "--name-only",
                "-z",
                "--no-ext-diff",
                "--no-color",
                "--no-renames",
                *ref_args,
            ),
        )

    def untracked_files(self, cwd: Path) -> GitCommandResult:
        return self._run(
            "untracked-files",
            cwd,
            ("ls-files", "--others", "--exclude-standard", "-z"),
        )

    def diff_untracked(
        self,
        cwd: Path,
        relative_path: str,
        *,
        output_limit_bytes: int = DEFAULT_GIT_DIFF_BYTES,
    ) -> GitCommandResult:
        _validate_relative_path(relative_path)
        result = self._execute(
            "diff-untracked",
            cwd,
            (
                "diff",
                "--no-index",
                "--no-ext-diff",
                "--no-color",
                "--no-renames",
                "--",
                "/dev/null",
                relative_path,
            ),
            output_limit_bytes=output_limit_bytes,
        )
        if result.returncode not in (0, 1):
            self.logger.error(
                "git command failed",
                extra={
                    "operation": "diff-untracked",
                    "returncode": result.returncode,
                    "stderr_truncated": result.stderr_truncated,
                },
            )
            raise GitCommandFailedError(
                "diff-untracked",
                returncode=result.returncode,
                stderr=result.stderr,
            )
        return result

    def update_ref_delete(
        self,
        cwd: Path,
        branch: str,
        expected_base_commit: str,
    ) -> None:
        _validate_branch(branch)
        _validate_ref(expected_base_commit)
        self._run(
            "update-ref-delete",
            cwd,
            (
                "update-ref",
                "-d",
                f"refs/heads/{branch}",
                expected_base_commit,
            ),
        )

    def branch_exists(self, cwd: Path, branch: str) -> bool:
        _validate_branch(branch)
        return self.try_resolve_ref(cwd, f"refs/heads/{branch}") is not None

    def _one_line(
        self,
        operation: str,
        cwd: Path,
        args: Sequence[str],
    ) -> str:
        return self._single_line_result(
            operation,
            self._run(operation, cwd, args),
        )

    def _single_line_result(
        self,
        operation: str,
        result: GitCommandResult,
    ) -> str:
        self._reject_truncated_result(operation, result)
        lines = result.stdout.splitlines()
        if (
            "\x00" in result.stdout
            or len(lines) != 1
            or not lines[0].strip()
        ):
            self.logger.error(
                "git command output is incomplete",
                extra={
                    "operation": operation,
                    "returncode": result.returncode,
                    "stdout_truncated": result.stdout_truncated,
                    "stderr_truncated": result.stderr_truncated,
                },
            )
            raise GitCommandFailedError(
                operation,
                returncode=result.returncode,
                stderr=result.stderr,
            )
        return lines[0].strip()

    def _reject_truncated_result(
        self,
        operation: str,
        result: GitCommandResult,
    ) -> None:
        if not result.stdout_truncated and not result.stderr_truncated:
            return
        self.logger.error(
            "git command output is incomplete",
            extra={
                "operation": operation,
                "returncode": result.returncode,
                "stdout_truncated": result.stdout_truncated,
                "stderr_truncated": result.stderr_truncated,
            },
        )
        raise GitCommandFailedError(
            operation,
            returncode=result.returncode,
            stderr=result.stderr,
        )

    def _run(
        self,
        operation: str,
        cwd: Path,
        args: Sequence[str],
        *,
        output_limit_bytes: int | None = None,
    ) -> GitCommandResult:
        result = self._execute(
            operation,
            cwd,
            args,
            output_limit_bytes=output_limit_bytes or self.output_limit_bytes,
        )
        if result.returncode != 0:
            self.logger.error(
                "git command failed",
                extra={
                    "operation": operation,
                    "returncode": result.returncode,
                    "stderr_truncated": result.stderr_truncated,
                },
            )
            raise GitCommandFailedError(
                operation,
                returncode=result.returncode,
                stderr=result.stderr,
            )
        return result

    def _execute(
        self,
        operation: str,
        cwd: Path,
        args: Sequence[str],
        *,
        output_limit_bytes: int,
    ) -> GitCommandResult:
        if not cwd.is_absolute() or not cwd.is_dir():
            raise GitCommandFailedError(operation, returncode=None)
        if any(not isinstance(argument, str) or "\x00" in argument for argument in args):
            raise ValueError("Git argv contains an invalid argument")
        argv = [self.git_executable, *args]
        environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LANG": "C",
            "LC_ALL": "C",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_PAGER": "cat",
            "GIT_TERMINAL_PROMPT": "0",
        }
        started = time.monotonic()
        try:
            process = subprocess.Popen(
                argv,
                cwd=str(cwd),
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
                shell=False,
            )
        except OSError as error:
            self.logger.error(
                "git command failed to start",
                extra={"operation": operation},
            )
            raise GitCommandFailedError(operation, returncode=None) from error
        try:
            stdout, stderr, stdout_truncated, stderr_truncated = (
                _communicate_bounded(
                    process,
                    timeout_seconds=self.timeout_seconds,
                    output_limit_bytes=output_limit_bytes,
                )
            )
        except subprocess.TimeoutExpired:
            _terminate_process_group(process)
            _close_process_pipes(process)
            self.logger.warning(
                "git command timeout",
                extra={
                    "operation": operation,
                    "duration": time.monotonic() - started,
                },
            )
            raise GitCommandTimeoutError(operation) from None
        returncode = process.returncode
        bounded_stdout = stdout.decode("utf-8", errors="replace")
        bounded_stderr = stderr.decode("utf-8", errors="replace")
        self.logger.debug(
            "git command completed",
            extra={
                "operation": operation,
                "duration": time.monotonic() - started,
                "result": "success" if returncode == 0 else "failed",
            },
        )
        return GitCommandResult(
            stdout=bounded_stdout,
            stderr=bounded_stderr,
            returncode=returncode,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
        )


def _communicate_bounded(
    process: subprocess.Popen[bytes],
    *,
    timeout_seconds: float,
    output_limit_bytes: int,
) -> tuple[bytes, bytes, bool, bool]:
    selector = selectors.DefaultSelector()
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    truncated = {"stdout": False, "stderr": False}
    streams = {
        "stdout": process.stdout,
        "stderr": process.stderr,
    }
    for name, stream in streams.items():
        if stream is not None:
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)
    deadline = time.monotonic() + timeout_seconds
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(process.args, timeout_seconds)
            ready = selector.select(remaining)
            if not ready:
                raise subprocess.TimeoutExpired(process.args, timeout_seconds)
            for key, _ in ready:
                stream = key.fileobj
                chunk = os.read(stream.fileno(), 16 * 1024)
                if not chunk:
                    selector.unregister(stream)
                    stream.close()
                    continue
                buffer = buffers[key.data]
                available = output_limit_bytes - len(buffer)
                if available > 0:
                    buffer.extend(chunk[:available])
                if len(chunk) > max(available, 0):
                    truncated[key.data] = True
        remaining = max(deadline - time.monotonic(), 0)
        try:
            process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            raise subprocess.TimeoutExpired(process.args, timeout_seconds) from None
    finally:
        selector.close()
    return (
        bytes(buffers["stdout"]),
        bytes(buffers["stderr"]),
        truncated["stdout"],
        truncated["stderr"],
    )


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        process.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except ProcessLookupError:
                pass


def _close_process_pipes(process: subprocess.Popen[bytes]) -> None:
    for stream in (process.stdout, process.stderr):
        if stream is not None:
            stream.close()


def _validate_ref(ref: str) -> None:
    if not ref or len(ref) > 4096 or "\x00" in ref:
        raise ValueError("Git ref is invalid")


def _validate_branch(branch: str) -> None:
    _validate_ref(branch)
    if branch.startswith("-") or ".." in branch or branch.endswith("."):
        raise ValueError("Git branch is invalid")


def _validate_relative_path(relative_path: str) -> None:
    if (
        not relative_path
        or "\x00" in relative_path
        or relative_path.startswith("/")
        or relative_path in {".", ".."}
        or relative_path.startswith("../")
        or "/../" in relative_path
        or relative_path.endswith("/..")
    ):
        raise ValueError("Git relative path is invalid")


__all__ = ["GitCommandResult", "GitProcess"]

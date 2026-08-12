from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import tempfile
import time
from collections.abc import Collection, Sequence

from eidos_runtime.git.errors import GitCommandFailedError, GitCommandTimeoutError


DEFAULT_GIT_TIMEOUT_SECONDS = 15.0
DEFAULT_GIT_OUTPUT_BYTES = 128 * 1024
DEFAULT_GIT_PATCH_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class _NativeCommandResult:
    stdout: str
    stderr: str
    returncode: int
    stdout_truncated: bool
    stderr_truncated: bool


class HardenedGitRunner:
    """Run one explicitly supplied native Git argv with bounded hardening."""

    def __init__(
        self,
        *,
        git_executable: str = "/usr/bin/git",
        timeout_seconds: float = DEFAULT_GIT_TIMEOUT_SECONDS,
        output_limit_bytes: int = DEFAULT_GIT_OUTPUT_BYTES,
        logger: logging.Logger | None = None,
    ) -> None:
        if not git_executable or timeout_seconds <= 0 or output_limit_bytes < 1:
            raise ValueError("Git runner configuration is invalid")
        self.git_executable = git_executable
        self.timeout_seconds = timeout_seconds
        self.output_limit_bytes = output_limit_bytes
        self.logger = logger or logging.getLogger(__name__)

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        operation: str,
        output_limit_bytes: int | None = None,
        config_overrides: Sequence[str] = (),
        apply_default_hardening: bool = True,
        allow_returncodes: Collection[int] = (),
    ) -> _NativeCommandResult:
        if not cwd.is_absolute() or not cwd.is_dir():
            raise GitCommandFailedError(operation, returncode=None)
        if any(not isinstance(argument, str) or "\x00" in argument for argument in args):
            raise ValueError("Git argv contains an invalid argument")
        if any(
            not isinstance(argument, str) or "\x00" in argument
            for argument in config_overrides
        ):
            raise ValueError("Git config override contains an invalid argument")
        argv = [self.git_executable]
        if apply_default_hardening:
            argv.extend(
                (
                    "-c",
                    "core.hooksPath=/dev/null",
                    "-c",
                    "core.fsmonitor=false",
                    "-c",
                    "credential.helper=",
                    "-c",
                    "core.askPass=",
                )
            )
        argv.extend(config_overrides)
        argv.extend(args)
        environment = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "LANG": "C",
            "LC_ALL": "C",
            "HOME": "/var/empty",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_PAGER": "cat",
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_ASKPASS": "/usr/bin/false",
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
                "native git operation failed to start",
                extra={"operation": operation},
            )
            raise GitCommandFailedError(operation, returncode=None) from error

        try:
            stdout, stderr, stdout_truncated, stderr_truncated = _communicate_bounded(
                process,
                timeout_seconds=self.timeout_seconds,
                output_limit_bytes=output_limit_bytes or self.output_limit_bytes,
            )
        except subprocess.TimeoutExpired:
            _terminate_process_group(process)
            _close_process_pipes(process)
            self.logger.warning(
                "native git operation timed out",
                extra={
                    "operation": operation,
                    "duration": time.monotonic() - started,
                },
            )
            raise GitCommandTimeoutError(operation) from None

        result = _NativeCommandResult(
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
            returncode=process.returncode,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
        )
        if result.stdout_truncated or result.stderr_truncated:
            raise GitCommandFailedError(
                operation,
                returncode=result.returncode,
                stderr=result.stderr,
            )
        if result.returncode not in allow_returncodes and result.returncode != 0:
            self.logger.error(
                "native git operation failed",
                extra={
                    "operation": operation,
                    "returncode": result.returncode,
                },
            )
            raise GitCommandFailedError(
                operation,
                returncode=result.returncode,
                stderr=result.stderr,
            )
        self.logger.debug(
            "native git operation completed",
            extra={
                "operation": operation,
                "duration": time.monotonic() - started,
                "result": "success" if result.returncode == 0 else "allowed",
            },
        )
        return result


class NativeWorktreeCreator:
    """Create a linked worktree through the smallest native Git seam."""

    def __init__(
        self,
        *,
        runner: HardenedGitRunner | None = None,
    ) -> None:
        self._runner = runner or HardenedGitRunner()

    def create(
        self,
        repository_root: Path,
        worktree_root: Path,
        branch: str | None,
        base_commit: str,
    ) -> None:
        if branch is not None:
            _validate_branch(branch)
        _validate_ref(base_commit)
        overrides = self._filter_config_overrides(repository_root)
        add_args = (
            ("--detach", str(worktree_root), base_commit)
            if branch is None
            else ("-b", branch, str(worktree_root), base_commit)
        )
        self._runner.run(
            (
                "worktree",
                "add",
                "--quiet",
                *add_args,
            ),
            cwd=repository_root,
            operation="worktree-add",
            config_overrides=overrides,
        )

    def _filter_config_overrides(self, cwd: Path) -> tuple[str, ...]:
        """Disable executable clean/process filters for worktree checkout."""

        return filter_config_overrides(self._runner, cwd)


class NativeWorktreeChangeTransfer:
    """Capture and apply dirty state through Git's patch and index semantics."""

    def __init__(
        self,
        *,
        runner: HardenedGitRunner | None = None,
    ) -> None:
        self._runner = runner or HardenedGitRunner()

    def capture(self, repository_root: Path) -> tuple[str, str]:
        overrides = filter_config_overrides(self._runner, repository_root)
        staged = self._runner.run(
            _DIFF_ARGS + ("--cached",),
            cwd=repository_root,
            operation="worktree-capture-staged",
            output_limit_bytes=DEFAULT_GIT_PATCH_BYTES,
            config_overrides=overrides,
        ).stdout
        full = self._runner.run(
            _DIFF_ARGS + ("HEAD", "--"),
            cwd=repository_root,
            operation="worktree-capture-full",
            output_limit_bytes=DEFAULT_GIT_PATCH_BYTES,
            config_overrides=overrides,
        ).stdout
        untracked_listing = self._runner.run(
            (
                "ls-files",
                "--others",
                "--exclude-standard",
                "-z",
                "--",
            ),
            cwd=repository_root,
            operation="worktree-capture-untracked-list",
            output_limit_bytes=DEFAULT_GIT_PATCH_BYTES,
            config_overrides=overrides,
        ).stdout
        if untracked_listing and not untracked_listing.endswith("\x00"):
            raise GitCommandFailedError(
                "worktree-capture-untracked-list",
                returncode=0,
                stderr="untracked path output is incomplete",
            )
        untracked_patches: list[str] = []
        total_patch_bytes = len(full.encode("utf-8"))
        for relative in (path for path in untracked_listing.split("\x00") if path):
            _validate_untracked_path(repository_root, relative)
            patch = self._runner.run(
                _NO_INDEX_DIFF_ARGS + ("--", "/dev/null", relative),
                cwd=repository_root,
                operation="worktree-capture-untracked",
                output_limit_bytes=DEFAULT_GIT_PATCH_BYTES,
                config_overrides=overrides,
                allow_returncodes=(1,),
            ).stdout
            total_patch_bytes += len(patch.encode("utf-8"))
            if total_patch_bytes > DEFAULT_GIT_PATCH_BYTES:
                raise GitCommandFailedError(
                    "worktree-capture-untracked",
                    returncode=1,
                    stderr="worktree patch exceeds the size limit",
                )
            untracked_patches.append(patch)
        full += "".join(untracked_patches)
        return full, staged

    def apply(
        self,
        worktree_root: Path,
        *,
        full_patch: str,
        staged_patch: str,
    ) -> None:
        if full_patch:
            self._apply_patch(worktree_root, full_patch, cached=False)
        if staged_patch:
            self._apply_patch(worktree_root, staged_patch, cached=True)

    def _apply_patch(self, worktree_root: Path, patch: str, *, cached: bool) -> None:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=worktree_root, prefix=".eidos-patch-", delete=False
        ) as temporary:
            patch_path = Path(temporary.name)
            temporary.write(patch)
            temporary.flush()
            os.fsync(temporary.fileno())
        try:
            cached_args = ("--cached",) if cached else ()
            self._runner.run(
                (
                    "apply",
                    "--binary",
                    "--whitespace=nowarn",
                    *cached_args,
                    str(patch_path),
                ),
                cwd=worktree_root,
                operation="worktree-apply-staged" if cached else "worktree-apply",
                output_limit_bytes=DEFAULT_GIT_OUTPUT_BYTES,
            )
        finally:
            try:
                patch_path.unlink()
            except OSError:
                pass


class NativeBranchAttacher:
    """Attach one new branch to an already-created detached Worktree."""

    def __init__(self, *, runner: HardenedGitRunner | None = None) -> None:
        self._runner = runner or HardenedGitRunner()

    def attach(self, worktree_root: Path, branch: str) -> None:
        _validate_branch(branch)
        self._runner.run(
            ("check-ref-format", "--branch", branch),
            cwd=worktree_root,
            operation="worktree-branch-validate",
        )
        self._runner.run(
            ("switch", "-c", branch),
            cwd=worktree_root,
            operation="worktree-branch-attach",
        )


class NativeWorktreeCleaner:
    """Clear a newly-created Worktree before normal non-force removal."""

    def __init__(self, *, runner: HardenedGitRunner | None = None) -> None:
        self._runner = runner or HardenedGitRunner()

    def clean(self, worktree_root: Path) -> None:
        overrides = filter_config_overrides(self._runner, worktree_root)
        self._runner.run(
            ("reset", "--hard", "HEAD"),
            cwd=worktree_root,
            operation="worktree-compensation-reset",
            config_overrides=overrides,
        )
        self._runner.run(
            ("clean", "-fdx", "--"),
            cwd=worktree_root,
            operation="worktree-compensation-clean",
        )


class NativeWorktreeRetentionCleaner:
    """Clear an Eidos-owned Worktree after its snapshot is durable."""

    def __init__(self, *, runner: HardenedGitRunner | None = None) -> None:
        self._runner = runner or HardenedGitRunner()

    def clean(self, worktree_root: Path) -> None:
        overrides = filter_config_overrides(self._runner, worktree_root)
        self._runner.run(
            ("reset", "--hard", "HEAD"),
            cwd=worktree_root,
            operation="worktree-retention-reset",
            config_overrides=overrides,
        )
        self._runner.run(
            ("clean", "-fdx", "--"),
            cwd=worktree_root,
            operation="worktree-retention-clean",
        )


class NativeWorktreeHandoffCleaner:
    """Clear transient Worktree changes while preserving ignored resources."""

    def __init__(self, *, runner: HardenedGitRunner | None = None) -> None:
        self._runner = runner or HardenedGitRunner()

    def clean(self, worktree_root: Path) -> None:
        overrides = filter_config_overrides(self._runner, worktree_root)
        self._runner.run(
            ("reset", "--hard", "HEAD"),
            cwd=worktree_root,
            operation="worktree-handoff-reset",
            config_overrides=overrides,
        )
        self._runner.run(
            ("clean", "-fd", "--"),
            cwd=worktree_root,
            operation="worktree-handoff-clean",
        )


class NativeWorktreeCheckout:
    """Move a checkout without force, reset, branch deletion, or cleanup."""

    def __init__(self, *, runner: HardenedGitRunner | None = None) -> None:
        self._runner = runner or HardenedGitRunner()

    def detach(self, worktree_root: Path) -> None:
        self._runner.run(
            ("switch", "--detach", "HEAD"),
            cwd=worktree_root,
            operation="worktree-detach",
        )

    def switch_branch(self, worktree_root: Path, branch: str) -> None:
        _validate_branch(branch)
        self._runner.run(
            ("switch", "--no-guess", branch),
            cwd=worktree_root,
            operation="worktree-switch-branch",
        )

    def switch_detached(self, worktree_root: Path, commit: str) -> None:
        _validate_ref(commit)
        self._runner.run(
            ("switch", "--detach", commit),
            cwd=worktree_root,
            operation="worktree-switch-detached",
        )


_DIFF_ARGS = (
    "diff",
    "--binary",
    "--full-index",
    "--no-renames",
    "--no-ext-diff",
    "--no-textconv",
)

_NO_INDEX_DIFF_ARGS = (
    "diff",
    "--no-index",
    "--binary",
    "--full-index",
    "--no-ext-diff",
    "--no-textconv",
)


def filter_config_overrides(
    runner: HardenedGitRunner,
    cwd: Path,
) -> tuple[str, ...]:
    """Return config overrides that disable executable clean/process filters."""

    result = runner.run(
        (
            "config",
            "--includes",
            "--null",
            "--name-only",
            "--get-regexp",
            r"^filter\..*\.(clean|process)$",
        ),
        cwd=cwd,
        operation="config-filter-list",
        apply_default_hardening=False,
        allow_returncodes=(1,),
    )
    if result.returncode == 1:
        return ()
    if result.stdout and not result.stdout.endswith("\x00"):
        raise GitCommandFailedError(
            "config-filter-list",
            returncode=result.returncode,
            stderr="filter config output is incomplete",
        )
    keys = [key for key in result.stdout.split("\x00") if key]
    names: set[str] = set()
    for key in keys:
        match = re.fullmatch(
            r"filter\.([A-Za-z0-9][A-Za-z0-9_.-]*)\.(clean|process)",
            key,
        )
        if match is None:
            raise GitCommandFailedError(
                "config-filter-list",
                returncode=result.returncode,
                stderr="filter config key is invalid",
            )
        names.add(match.group(1))
    overrides: list[str] = []
    for name in sorted(names):
        overrides.extend(
            (
                "-c",
                f"filter.{name}.clean=",
                "-c",
                f"filter.{name}.process=",
                "-c",
                f"filter.{name}.required=false",
            )
        )
    return tuple(overrides)


def _communicate_bounded(
    process: subprocess.Popen[bytes],
    *,
    timeout_seconds: float,
    output_limit_bytes: int,
) -> tuple[bytes, bytes, bool, bool]:
    selector = selectors.DefaultSelector()
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    truncated = {"stdout": False, "stderr": False}
    streams = {"stdout": process.stdout, "stderr": process.stderr}
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
    if (
        branch.startswith("-")
        or ".." in branch
        or branch.endswith(".")
        or branch.endswith("/")
        or branch.startswith("/")
        or "@{" in branch
        or any(character in "~^:?*[\\" for character in branch)
        or any(character.isspace() or ord(character) < 32 for character in branch)
        or any(part in {".", ".."} for part in branch.split("/"))
        or any(part.endswith(".lock") for part in branch.split("/"))
    ):
        raise ValueError("Git branch is invalid")


def _validate_untracked_path(repository_root: Path, relative: str) -> Path:
    path = Path(relative)
    if (
        not relative
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(part == ".git" for part in path.parts)
    ):
        raise GitCommandFailedError(
            "worktree-capture-untracked",
            returncode=None,
            stderr="untracked path is outside the repository",
        )
    try:
        source_root = repository_root.resolve(strict=True)
        source_path = (repository_root / path).resolve(strict=False)
    except OSError as error:
        raise GitCommandFailedError(
            "worktree-capture-untracked",
            returncode=None,
            stderr="untracked path could not be resolved",
        ) from error
    try:
        source_path.relative_to(source_root)
    except ValueError as error:
        raise GitCommandFailedError(
            "worktree-capture-untracked",
            returncode=None,
            stderr="untracked path escapes the repository",
        ) from error
    actual = repository_root / path
    try:
        source_stat = actual.lstat()
    except OSError as error:
        raise GitCommandFailedError(
            "worktree-capture-untracked",
            returncode=None,
            stderr="untracked path could not be observed",
        ) from error
    if stat.S_ISLNK(source_stat.st_mode):
        try:
            link_target = actual.resolve(strict=True)
            link_relative = link_target.relative_to(source_root)
            if any(part == ".git" for part in link_relative.parts):
                raise ValueError("untracked symlink points to Git metadata")
        except (OSError, ValueError) as error:
            raise GitCommandFailedError(
                "worktree-capture-untracked",
                returncode=None,
                stderr="untracked symlink escapes the repository",
            ) from error
    elif not stat.S_ISREG(source_stat.st_mode):
        raise GitCommandFailedError(
            "worktree-capture-untracked",
            returncode=None,
            stderr="untracked path is not a regular file or symlink",
        )
    return actual


__all__ = [
    "HardenedGitRunner",
    "NativeBranchAttacher",
    "NativeWorktreeChangeTransfer",
    "NativeWorktreeCleaner",
    "NativeWorktreeHandoffCleaner",
    "NativeWorktreeCheckout",
    "NativeWorktreeCreator",
    "filter_config_overrides",
]

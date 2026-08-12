from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import logging
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import threading
import time
from collections.abc import Collection, Sequence

from eidos_runtime.git.errors import (
    GitCommandFailedError,
    GitCommandTimeoutError,
    GitConflictError,
    GitIdentityUnavailableError,
    GitNothingStagedError,
    GitRemoteCanceledError,
    GitRemoteUnsupportedError,
    GitUpstreamNotFoundError,
)


DEFAULT_GIT_TIMEOUT_SECONDS = 15.0
DEFAULT_GIT_OUTPUT_BYTES = 128 * 1024
DEFAULT_GIT_PATCH_BYTES = 64 * 1024 * 1024
DEFAULT_GIT_REMOTE_TIMEOUT_SECONDS = 120.0


class GitExecutionProfile(StrEnum):
    OBSERVE = "observe"
    LOCAL_MUTATION = "local_mutation"
    REMOTE = "remote"


@dataclass(frozen=True)
class GitCliResult:
    stdout: bytes
    stderr: bytes
    returncode: int
    stdout_truncated: bool
    stderr_truncated: bool


@dataclass(frozen=True)
class GitCliDiff:
    patch: bytes
    changed_paths: tuple[str, ...]
    truncated: bool


class HardenedGitRunner:
    """Run one explicitly supplied native Git argv with bounded hardening."""

    def __init__(
        self,
        *,
        git_executable: str = "/usr/bin/git",
        timeout_seconds: float = DEFAULT_GIT_TIMEOUT_SECONDS,
        output_limit_bytes: int = DEFAULT_GIT_OUTPUT_BYTES,
        logger: logging.Logger | None = None,
        user_home: Path | None = None,
    ) -> None:
        if not git_executable or timeout_seconds <= 0 or output_limit_bytes < 1:
            raise ValueError("Git runner configuration is invalid")
        self.git_executable = git_executable
        self.timeout_seconds = timeout_seconds
        self.output_limit_bytes = output_limit_bytes
        self.logger = logger or logging.getLogger(__name__)
        self.user_home = (user_home or Path.home()).resolve(strict=False)

    def run(
        self,
        args: Sequence[str],
        *,
        cwd: Path,
        operation: str,
        stdin: bytes | None = None,
        output_limit_bytes: int | None = None,
        config_overrides: Sequence[str] = (),
        apply_default_hardening: bool = True,
        allow_returncodes: Collection[int] = (),
        raise_on_truncation: bool = True,
        read_user_global_config: bool = False,
        profile: GitExecutionProfile = GitExecutionProfile.OBSERVE,
        timeout_seconds: float | None = None,
        cancel: threading.Event | None = None,
    ) -> GitCliResult:
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
                )
            )
            if profile is not GitExecutionProfile.REMOTE:
                argv.extend(("-c", "credential.helper=", "-c", "core.askPass="))
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
            "GIT_LITERAL_PATHSPECS": "1",
        }
        if read_user_global_config or profile in {
            GitExecutionProfile.LOCAL_MUTATION,
            GitExecutionProfile.REMOTE,
        }:
            environment["HOME"] = str(self.user_home)
            environment.pop("GIT_CONFIG_GLOBAL")
        if profile is GitExecutionProfile.REMOTE:
            environment["GIT_SSH_COMMAND"] = "/usr/bin/ssh -o BatchMode=yes"
            ssh_auth_sock = _validated_ssh_auth_sock(os.environ.get("SSH_AUTH_SOCK"))
            if ssh_auth_sock is not None:
                environment["SSH_AUTH_SOCK"] = ssh_auth_sock
        command_timeout = (
            DEFAULT_GIT_REMOTE_TIMEOUT_SECONDS
            if timeout_seconds is None and profile is GitExecutionProfile.REMOTE
            else self.timeout_seconds
            if timeout_seconds is None
            else timeout_seconds
        )
        if command_timeout <= 0:
            raise ValueError("Git command timeout must be positive")
        started = time.monotonic()
        try:
            process = subprocess.Popen(
                argv,
                cwd=str(cwd),
                env=environment,
                stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
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
                timeout_seconds=command_timeout,
                output_limit_bytes=(
                    self.output_limit_bytes
                    if output_limit_bytes is None
                    else output_limit_bytes
                ),
                stdin=stdin,
                cancel=cancel,
            )
        except _GitProcessCanceled:
            _terminate_process_group(process)
            _close_process_pipes(process)
            raise GitRemoteCanceledError() from None
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

        result = GitCliResult(
            stdout=stdout,
            stderr=stderr,
            returncode=process.returncode,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
        )
        if (
            raise_on_truncation
            and (result.stdout_truncated or result.stderr_truncated)
        ):
            raise GitCommandFailedError(
                operation,
                returncode=result.returncode,
                stderr=result.stderr.decode("utf-8", errors="replace"),
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
                stderr=result.stderr.decode("utf-8", errors="replace"),
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


class GitCli:
    """Typed adapter for the small set of Git operations owned by Git itself.

    This class does not reconstruct Git state.  It passes patches and command
    output through the hardened process boundary and leaves Git semantics to
    ``/usr/bin/git``.
    """

    def __init__(self, *, runner: HardenedGitRunner | None = None) -> None:
        self._runner = runner or HardenedGitRunner()

    def worktree_add(
        self,
        repository_root: Path,
        worktree_root: Path,
        branch: str | None,
        base_commit: str,
    ) -> None:
        overrides = filter_config_overrides(self._runner, repository_root)
        add_args = (
            ("--detach", str(worktree_root), base_commit)
            if branch is None
            else ("-b", branch, str(worktree_root), base_commit)
        )
        self._runner.run(
            ("worktree", "add", "--quiet", *add_args),
            cwd=repository_root,
            operation="worktree-add",
            config_overrides=overrides,
        )

    def clean_destructive(self, worktree_root: Path) -> None:
        self._runner.run(
            ("clean", "-fdx", "--"),
            cwd=worktree_root,
            operation="worktree-destructive-clean",
        )

    def status_porcelain(self, cwd: Path) -> bytes:
        result = self._runner.run(
            (
                "status",
                "--porcelain=v1",
                "--no-renames",
                "--untracked-files=all",
                "-z",
                "--",
            ),
            cwd=cwd,
            operation="status",
            config_overrides=filter_config_overrides(self._runner, cwd),
            output_limit_bytes=DEFAULT_GIT_PATCH_BYTES,
        )
        return result.stdout

    def stage(self, cwd: Path, paths: Sequence[str]) -> None:
        self._runner.run(
            ("add", "--all", "--", *paths),
            cwd=cwd,
            operation="stage",
            profile=GitExecutionProfile.LOCAL_MUTATION,
        )

    def remote_status(self, cwd: Path):
        from eidos_runtime.git.models import GitRemote, GitRemoteObservation, GitUpstream

        remotes_result = self._runner.run(
            ("remote",), cwd=cwd, operation="remote-list"
        )
        remotes = tuple(
            GitRemote(name=name)
            for name in sorted(
                line for line in _strict_lines(remotes_result.stdout, "remote-list") if line
            )
        )
        if any(not _valid_remote_name(remote.name) for remote in remotes):
            raise GitCommandFailedError("remote-list", returncode=None)
        branch_result = self._runner.run(
            ("symbolic-ref", "--quiet", "--short", "HEAD"),
            cwd=cwd,
            operation="remote-branch",
            allow_returncodes=(1,),
        )
        branch = (
            _single_line(branch_result.stdout, "remote-branch")
            if branch_result.returncode == 0
            else None
        )
        if branch is None:
            return GitRemoteObservation(branch=None, remotes=remotes)
        upstream_result = self._runner.run(
            (
                "for-each-ref",
                "--format=%(upstream:remotename)%00%(upstream:remoteref)",
                "--count=1",
                f"refs/heads/{branch}",
            ),
            cwd=cwd,
            operation="remote-upstream",
        )
        pair = _optional_nul_pair(upstream_result.stdout, "remote-upstream")
        if pair is None or not pair[0] or not pair[1]:
            return GitRemoteObservation(branch=branch, remotes=remotes)
        remote_name, upstream_ref = pair
        if remote_name not in {remote.name for remote in remotes}:
            raise GitCommandFailedError("remote-upstream", returncode=None)
        upstream_branch = upstream_ref.removeprefix("refs/heads/")
        if upstream_branch == upstream_ref or not upstream_branch:
            raise GitCommandFailedError("remote-upstream", returncode=None)
        upstream_commit = self._runner.run(
            ("rev-parse", "--verify", "@{upstream}"),
            cwd=cwd,
            operation="remote-upstream",
            allow_returncodes=(128,),
        )
        if upstream_commit.returncode != 0:
            raise GitUpstreamNotFoundError()
        counts = self._runner.run(
            ("rev-list", "--left-right", "--count", "HEAD...@{upstream}"),
            cwd=cwd,
            operation="remote-ahead-behind",
        )
        fields = _single_line(counts.stdout, "remote-ahead-behind").split()
        if len(fields) != 2 or not all(field.isdecimal() for field in fields):
            raise GitCommandFailedError("remote-ahead-behind", returncode=None)
        return GitRemoteObservation(
            branch=branch,
            remotes=remotes,
            upstream=GitUpstream(remote=remote_name, branch=upstream_branch),
            ahead=int(fields[0]),
            behind=int(fields[1]),
        )

    def fetch(self, cwd: Path, remote: str, *, cancel: threading.Event) -> None:
        self.validate_remote_transport(cwd, remote)
        self._runner.run(
            ("fetch", "--", remote),
            cwd=cwd,
            operation="fetch",
            profile=GitExecutionProfile.REMOTE,
            cancel=cancel,
        )

    def validate_remote_transport(self, cwd: Path, remote: str) -> None:
        if not _valid_remote_name(remote):
            raise GitRemoteUnsupportedError()
        custom_transport = self._runner.run(
            ("config", "--get", f"remote.{remote}.vcs"),
            cwd=cwd,
            operation="remote-transport",
            profile=GitExecutionProfile.REMOTE,
            allow_returncodes=(1,),
        )
        if custom_transport.returncode == 0:
            raise GitRemoteUnsupportedError()
        resolved_url = self._runner.run(
            ("remote", "get-url", remote),
            cwd=cwd,
            operation="remote-url",
            profile=GitExecutionProfile.REMOTE,
        )
        _validate_remote_transport(_single_line(resolved_url.stdout, "remote-url"))

    def unstage(self, cwd: Path, paths: Sequence[str]) -> None:
        head = self._runner.run(
            ("rev-parse", "--verify", "HEAD"),
            cwd=cwd,
            operation="unstage-head",
            allow_returncodes=(128,),
        )
        if head.returncode == 0:
            self._runner.run(
                ("restore", "--staged", "--", *paths),
                cwd=cwd,
                operation="unstage",
                config_overrides=filter_config_overrides(self._runner, cwd),
            )
            return
        self._runner.run(
            ("rm", "--cached", "-r", "--ignore-unmatch", "--", *paths),
            cwd=cwd,
            operation="unstage",
            config_overrides=filter_config_overrides(self._runner, cwd),
        )

    def commit(self, cwd: Path, message: str) -> None:
        conflicts = self._runner.run(
            ("diff", "--cached", "--quiet", "--diff-filter=U", "--"),
            cwd=cwd,
            operation="commit-conflict-check",
            allow_returncodes=(1,),
        )
        if conflicts.returncode == 1:
            raise GitConflictError()
        staged = self._runner.run(
            ("diff", "--cached", "--quiet", "--exit-code", "--"),
            cwd=cwd,
            operation="commit-staged-check",
            allow_returncodes=(1,),
        )
        if staged.returncode == 0:
            raise GitNothingStagedError()
        name = self._commit_identity_value(cwd, "user.name")
        email = self._commit_identity_value(cwd, "user.email")
        if name is None or email is None:
            raise GitIdentityUnavailableError()
        overrides = (
            "-c",
            f"user.name={name}",
            "-c",
            f"user.email={email}",
            *filter_config_overrides(self._runner, cwd),
        )
        self._runner.run(
            ("commit", "--quiet", "--message", message),
            cwd=cwd,
            operation="commit",
            config_overrides=overrides,
        )

    def _commit_identity_value(self, cwd: Path, key: str) -> str | None:
        local = self._config_value(cwd, key, global_scope=False)
        return local if local is not None else self._config_value(
            cwd, key, global_scope=True
        )

    def _config_value(
        self, cwd: Path, key: str, *, global_scope: bool
    ) -> str | None:
        result = self._runner.run(
            (
                "config",
                *(("--global",) if global_scope else ()),
                "--no-includes",
                "--null",
                "--get",
                key,
            ),
            cwd=cwd,
            operation="commit-identity",
            apply_default_hardening=False,
            allow_returncodes=(1,),
            read_user_global_config=global_scope,
        )
        if result.returncode == 1:
            return None
        if not result.stdout.endswith(b"\0") or result.stdout.count(b"\0") != 1:
            raise GitCommandFailedError("commit-identity", returncode=None)
        value = result.stdout[:-1].decode("utf-8", errors="strict")
        return value if value.strip() else None

    def capture_working_tree_patch(
        self,
        cwd: Path,
        *,
        base_commit: str = "HEAD",
        output_limit_bytes: int = DEFAULT_GIT_PATCH_BYTES,
    ) -> bytes:
        patch, truncated = self._capture_patch(
            cwd,
            base_commit=base_commit,
            include_untracked=True,
            output_limit_bytes=output_limit_bytes,
            raise_on_truncation=True,
        )
        if truncated:
            raise GitCommandFailedError(
                "worktree-capture",
                returncode=None,
                stderr="Git patch output exceeds the size limit",
            )
        return patch

    def capture_staged_patch(
        self,
        cwd: Path,
        *,
        base_commit: str = "HEAD",
        output_limit_bytes: int = DEFAULT_GIT_PATCH_BYTES,
    ) -> bytes:
        result = self._runner.run(
            (
                "diff",
                "--binary",
                "--no-ext-diff",
                "--no-textconv",
                "--cached",
                base_commit,
                "--",
            ),
            cwd=cwd,
            operation="worktree-capture-staged",
            config_overrides=filter_config_overrides(self._runner, cwd),
            output_limit_bytes=output_limit_bytes,
        )
        return result.stdout

    def diff(
        self,
        cwd: Path,
        *,
        base_commit: str,
        include_untracked: bool,
        output_limit_bytes: int,
        path: str | None = None,
    ) -> GitCliDiff:
        patch = self._capture_patch(
            cwd,
            base_commit=base_commit,
            include_untracked=include_untracked,
            output_limit_bytes=output_limit_bytes,
            raise_on_truncation=False,
            path=path,
        )
        changed_paths = self._changed_paths(
            cwd,
            base_commit=base_commit,
            include_untracked=include_untracked,
            output_limit_bytes=output_limit_bytes,
            path=path,
        )
        return GitCliDiff(
            patch=patch[0],
            changed_paths=changed_paths,
            truncated=patch[1],
        )

    def apply_working_tree_patch(
        self,
        cwd: Path,
        *,
        full_patch: bytes,
        staged_patch: bytes,
    ) -> None:
        overrides = filter_config_overrides(self._runner, cwd)
        if full_patch:
            self._runner.run(
                ("apply", "--binary", "--"),
                cwd=cwd,
                operation="worktree-apply",
                stdin=full_patch,
                config_overrides=overrides,
            )
        if staged_patch:
            self._runner.run(
                ("apply", "--binary", "--cached", "--"),
                cwd=cwd,
                operation="worktree-apply-staged",
                stdin=staged_patch,
                config_overrides=overrides,
            )

    def _capture_patch(
        self,
        cwd: Path,
        *,
        base_commit: str,
        include_untracked: bool,
        output_limit_bytes: int,
        raise_on_truncation: bool,
        path: str | None = None,
    ) -> tuple[bytes, bool]:
        if output_limit_bytes < 1:
            raise ValueError("Git patch output limit must be positive")
        overrides = filter_config_overrides(self._runner, cwd)
        result = self._runner.run(
            (
                "diff",
                "--binary",
                "--no-ext-diff",
                "--no-textconv",
                base_commit,
                "--",
                *((path,) if path is not None else ()),
            ),
            cwd=cwd,
            operation="worktree-diff",
            config_overrides=overrides,
            output_limit_bytes=output_limit_bytes,
            raise_on_truncation=raise_on_truncation,
        )
        output = bytearray(result.stdout)
        truncated = result.stdout_truncated
        if include_untracked and not truncated:
            for relative in self.untracked_paths(cwd):
                if path is not None and relative != path:
                    continue
                remaining = output_limit_bytes - len(output)
                if remaining < 1:
                    if raise_on_truncation:
                        raise GitCommandFailedError(
                            "worktree-diff",
                            returncode=None,
                            stderr="Git patch output exceeds the size limit",
                        )
                    truncated = True
                    break
                untracked = self._untracked_patch(
                    cwd,
                    relative,
                    output_limit_bytes=remaining,
                    raise_on_truncation=raise_on_truncation,
                    config_overrides=overrides,
                )
                output.extend(untracked[0])
                if untracked[1]:
                    truncated = True
                    break
        return bytes(output[:output_limit_bytes]), truncated

    def untracked_paths(self, cwd: Path) -> tuple[str, ...]:
        result = self._runner.run(
            (
                "ls-files",
                "--others",
                "--exclude-standard",
                "-z",
                "--",
            ),
            cwd=cwd,
            operation="untracked-paths",
            output_limit_bytes=DEFAULT_GIT_PATCH_BYTES,
        )
        return tuple(
            sorted(
                os.fsdecode(path)
                for path in result.stdout.split(b"\0")
                if path and path != b".git"
            )
        )

    def gitlink_paths(
        self,
        cwd: Path,
        paths: Sequence[str],
    ) -> tuple[str, ...]:
        if not paths:
            return ()
        result = self._runner.run(
            ("ls-files", "--stage", "-z", "--", *paths),
            cwd=cwd,
            operation="gitlink-paths",
            output_limit_bytes=DEFAULT_GIT_PATCH_BYTES,
        )
        gitlinks: list[str] = []
        for record in result.stdout.split(b"\0"):
            if not record:
                continue
            try:
                metadata, raw_path = record.split(b"\t", 1)
                mode = metadata.split(b" ", 1)[0]
            except ValueError as error:
                raise GitCommandFailedError(
                    "gitlink-paths",
                    returncode=None,
                    stderr="Git index output is invalid",
                ) from error
            if mode == b"160000":
                gitlinks.append(os.fsdecode(raw_path))
        return tuple(sorted(set(gitlinks)))

    def _untracked_patch(
        self,
        cwd: Path,
        relative: str,
        *,
        output_limit_bytes: int,
        raise_on_truncation: bool,
        config_overrides: Sequence[str],
    ) -> tuple[bytes, bool]:
        result = self._runner.run(
            (
                "diff",
                "--no-index",
                "--binary",
                "--no-ext-diff",
                "--no-textconv",
                "--",
                "/dev/null",
                relative,
            ),
            cwd=cwd,
            operation="worktree-diff-untracked",
            config_overrides=config_overrides,
            output_limit_bytes=output_limit_bytes,
            allow_returncodes=(1,),
            raise_on_truncation=raise_on_truncation,
        )
        return result.stdout, result.stdout_truncated

    def _changed_paths(
        self,
        cwd: Path,
        *,
        base_commit: str,
        include_untracked: bool,
        output_limit_bytes: int,
        path: str | None = None,
    ) -> tuple[str, ...]:
        result = self._runner.run(
            (
                "diff",
                "--name-only",
                "--no-renames",
                "--no-ext-diff",
                "--no-textconv",
                "-z",
                base_commit,
                "--",
                *((path,) if path is not None else ()),
            ),
            cwd=cwd,
            operation="worktree-diff-paths",
            config_overrides=filter_config_overrides(self._runner, cwd),
            output_limit_bytes=max(output_limit_bytes, DEFAULT_GIT_OUTPUT_BYTES),
        )
        paths = {os.fsdecode(path) for path in result.stdout.split(b"\0") if path}
        if include_untracked:
            paths.update(
                relative
                for relative in self.untracked_paths(cwd)
                if path is None or relative == path
            )
        return tuple(sorted(paths))


def filter_config_overrides(
    runner: HardenedGitRunner,
    cwd: Path,
) -> tuple[str, ...]:
    """Return overrides that disable all configured executable filter stages."""

    result = runner.run(
        (
            "config",
            "--includes",
            "--null",
            "--name-only",
            "--get-regexp",
            r"^filter\..*\.(clean|process|smudge)$",
        ),
        cwd=cwd,
        operation="config-filter-list",
        apply_default_hardening=False,
        allow_returncodes=(1,),
    )
    if result.returncode == 1:
        return ()
    if result.stdout and not result.stdout.endswith(b"\x00"):
        raise GitCommandFailedError(
            "config-filter-list",
            returncode=result.returncode,
            stderr="filter config output is incomplete",
        )
    keys = [os.fsdecode(key) for key in result.stdout.split(b"\x00") if key]
    names: set[str] = set()
    for key in keys:
        match = re.fullmatch(
            r"filter\.([A-Za-z0-9][A-Za-z0-9_.-]*)\.(clean|process|smudge)",
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
                f"filter.{name}.smudge=",
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
    stdin: bytes | None,
    cancel: threading.Event | None,
) -> tuple[bytes, bytes, bool, bool]:
    selector = selectors.DefaultSelector()
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    truncated = {"stdout": False, "stderr": False}
    streams = {"stdout": process.stdout, "stderr": process.stderr}
    for name, stream in streams.items():
        if stream is not None:
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)
    stdin_offset = 0
    if process.stdin is not None and stdin is not None:
        os.set_blocking(process.stdin.fileno(), False)
        selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
    deadline = time.monotonic() + timeout_seconds
    try:
        while selector.get_map():
            if cancel is not None and cancel.is_set():
                raise _GitProcessCanceled
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(process.args, timeout_seconds)
            ready = selector.select(min(remaining, 0.05) if cancel is not None else remaining)
            if not ready:
                if cancel is not None:
                    continue
                raise subprocess.TimeoutExpired(process.args, timeout_seconds)
            for key, _ in ready:
                stream = key.fileobj
                if key.data == "stdin":
                    if stdin_offset >= len(stdin or b""):
                        selector.unregister(stream)
                        stream.close()
                        continue
                    try:
                        written = os.write(
                            stream.fileno(),
                            (stdin or b"")[stdin_offset : stdin_offset + 16 * 1024],
                        )
                    except BrokenPipeError:
                        written = 0
                    stdin_offset += written
                    if stdin_offset >= len(stdin or b"") or written == 0:
                        selector.unregister(stream)
                        stream.close()
                    continue
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


class _GitProcessCanceled(Exception):
    pass


def _validated_ssh_auth_sock(value: str | None) -> str | None:
    if not value or "\x00" in value:
        return None
    try:
        metadata = os.stat(value, follow_symlinks=False)
    except OSError:
        return None
    if not stat.S_ISSOCK(metadata.st_mode) or metadata.st_uid != os.getuid():
        return None
    return value


def _strict_lines(output: bytes, operation: str) -> tuple[str, ...]:
    try:
        text = output.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise GitCommandFailedError(operation, returncode=None) from error
    if text and not text.endswith("\n"):
        raise GitCommandFailedError(operation, returncode=None)
    return tuple(text.splitlines())


def _single_line(output: bytes, operation: str) -> str:
    lines = _strict_lines(output, operation)
    if len(lines) != 1 or not lines[0]:
        raise GitCommandFailedError(operation, returncode=None)
    return lines[0]


def _optional_nul_pair(output: bytes, operation: str) -> tuple[str, str] | None:
    if not output:
        return None
    if not output.endswith(b"\n") or output.count(b"\n") != 1:
        raise GitCommandFailedError(operation, returncode=None)
    value = output[:-1]
    if value.count(b"\x00") != 1:
        raise GitCommandFailedError(operation, returncode=None)
    try:
        left, right = value.decode("utf-8", errors="strict").split("\x00")
    except UnicodeDecodeError as error:
        raise GitCommandFailedError(operation, returncode=None) from error
    return left, right


def _valid_remote_name(value: str) -> bool:
    return bool(value) and not value.startswith("-") and not any(
        character.isspace() or ord(character) < 32 for character in value
    )


def _validate_remote_transport(url: str) -> None:
    if url.startswith(("https://", "ssh://", "file://")):
        return
    if "://" in url or "::" in url:
        raise GitRemoteUnsupportedError()
    if re.fullmatch(r"(?:[^/@:\s]+@)?[^/:\s]+:.+", url):
        return
    if url and not url.startswith("-"):
        return
    raise GitRemoteUnsupportedError()


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
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is not None:
            stream.close()


__all__ = [
    "GitCli",
    "GitCliDiff",
    "GitCliResult",
    "GitExecutionProfile",
    "HardenedGitRunner",
    "filter_config_overrides",
]

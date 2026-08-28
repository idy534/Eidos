from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import threading
import time

from eidos_runtime.extensions.skill_access import SkillAccessRecord
from eidos_runtime.sandbox.host_shell import (
    HOST_SHELL_RESOLVER,
    SHELL_ENVIRONMENT_PROVIDER,
    HostShell,
    HostShellUnavailableError,
)
from eidos_runtime.sandbox.seatbelt import (
    SeatbeltProfile,
    SeatbeltUnavailableError,
)
from eidos_runtime.db.storage import WorkspaceIdentity
from eidos_runtime.runtime.resource_registry import (
    ResourceRegistry,
    RuntimeResourceKind,
)
from eidos_runtime.runtime.fault_injection import hit_fault
from eidos_runtime.sandbox.permissions import SandboxAttempt, SandboxType
from eidos_runtime.workspace.search_driver import RipgrepBinaryResolver, SearchDriverError


MAX_OUTPUT_BYTES = 256 * 1024
MAX_OUTPUT_METADATA_BYTES = 9_007_199_254_740_991
TERMINATION_GRACE_SECONDS = 0.5
POST_TERMINATION_DRAIN_SECONDS = 1.0
_HOST_ALTERING_ENV_NAMES = frozenset(
    (
        "GIT_CONFIG_GLOBAL",
        "GIT_CONFIG_SYSTEM",
        "GIT_CONFIG_NOSYSTEM",
        "GIT_ASKPASS",
        "GIT_TERMINAL_PROMPT",
        "GIT_OPTIONAL_LOCKS",
        "GIT_PAGER",
        "PNPM_CONFIG_PM_ON_FAIL",
    )
)


@dataclass(frozen=True)
class ShellLaunchSpec:
    argv: tuple[str, ...]
    cwd: Path
    environment: dict[str, str]
    sandboxed: bool
    shell_kind: str | None = None
    environment_source: str | None = None


def run_shell(
    workspace: WorkspaceIdentity,
    command: str,
    cwd: WorkspaceIdentity,
    timeout_seconds: int,
    cancel: threading.Event,
    on_delta: Callable[[str], None],
    resource_registry: ResourceRegistry | None = None,
    owner_id: str = "shell",
    attempt: SandboxAttempt | None = None,
    active_skill_roots: Sequence[Path] = (),
    skill_invocation: SkillAccessRecord | None = None,
) -> dict[str, object]:
    started = time.monotonic()
    workspace_fd = -1
    cwd_fd = -1
    try:
        workspace_fd = _open_verified_directory(workspace)
        cwd_fd = _open_verified_directory(cwd)
        if cwd.path != workspace.path and workspace.path not in cwd.path.parents:
            raise ValueError("shell cwd is outside workspace")
        return _run_verified_shell(
            workspace,
            command,
            cwd,
            timeout_seconds,
            cancel,
            on_delta,
            started,
            resource_registry,
            owner_id,
            attempt,
            active_skill_roots,
            skill_invocation,
        )
    except HostShellUnavailableError:
        return process_start_failed_result(started, skill_invocation=skill_invocation)
    except ShellProcessStartError:
        return process_start_failed_result(started, skill_invocation=skill_invocation)
    except ValueError:
        result = {
            "schemaVersion": 1,
            "toolName": "run_shell",
            "outcome": "error",
            "code": "workspace_identity_changed",
            "summary": "Command was not started because the workspace changed",
            "data": {
                "exitCode": None,
                "stdout": "",
                "stderr": "",
                "truncated": False,
                "termination": "not_started",
                "durationMs": int((time.monotonic() - started) * 1000),
                "originalBytes": 0,
                "omittedBytes": 0,
            },
            "sideEffectsMayExist": False,
        }
        result["summary"] = _summary_with_termination(
            str(result["summary"]), "not_started"
        )
        return _attach_skill_invocation(result, skill_invocation)
    except SeatbeltUnavailableError:
        return sandbox_unavailable_result(started, skill_invocation=skill_invocation)
    finally:
        if cwd_fd >= 0:
            os.close(cwd_fd)
        if workspace_fd >= 0:
            os.close(workspace_fd)


def sandbox_unavailable_result(
    started: float | None = None,
    *,
    skill_invocation: SkillAccessRecord | None = None,
) -> dict[str, object]:
    duration_ms = (
        0
        if started is None
        else max(0, int((time.monotonic() - started) * 1000))
    )
    result = {
        "schemaVersion": 1,
        "toolName": "run_shell",
        "outcome": "error",
        "code": "sandbox_unavailable",
        "summary": (
            "Command was not started because the Shell sandbox is unavailable "
            "(termination=not_started)"
        ),
        "data": {
            "exitCode": None,
            "stdout": "",
            "stderr": "",
            "truncated": False,
            "termination": "not_started",
            "durationMs": duration_ms,
            "originalBytes": 0,
            "omittedBytes": 0,
        },
        "sideEffectsMayExist": False,
    }
    return _attach_skill_invocation(result, skill_invocation)


def process_start_failed_result(
    started: float | None = None,
    *,
    skill_invocation: SkillAccessRecord | None = None,
) -> dict[str, object]:
    duration_ms = (
        0
        if started is None
        else max(0, int((time.monotonic() - started) * 1000))
    )
    result = {
        "schemaVersion": 1,
        "toolName": "run_shell",
        "outcome": "error",
        "code": "process_start_failed",
        "summary": (
            "Command was not started because the host shell is unavailable "
            "(termination=not_started)"
        ),
        "data": {
            "exitCode": None,
            "stdout": "",
            "stderr": "",
            "truncated": False,
            "termination": "not_started",
            "durationMs": duration_ms,
            "originalBytes": 0,
            "omittedBytes": 0,
        },
        "sideEffectsMayExist": False,
    }
    return _attach_skill_invocation(result, skill_invocation)


class ShellProcessStartError(RuntimeError):
    """Raised for a shell environment or process startup failure."""


def _effective_shell_environment(
    snapshot_environment: Mapping[str, str],
    shell: HostShell,
) -> dict[str, str]:
    environment = dict(snapshot_environment)
    environment["SHELL"] = str(shell.executable)

    home = _existing_environment_directory(environment.get("HOME"))
    if home is None:
        raise ShellProcessStartError
    environment["HOME"] = str(home)

    temporary = _existing_environment_directory(environment.get("TMPDIR"))
    if temporary is None:
        temporary = _canonical_environment_directory("/tmp")
    if temporary is None:
        raise ShellProcessStartError
    environment["TMPDIR"] = str(temporary)

    path_entries = environment.get("PATH", "").split(os.pathsep)
    try:
        bundled_parent = RipgrepBinaryResolver().resolve().parent.resolve(
            strict=False
        )
    except SearchDriverError:
        bundled_parent = None
    if bundled_parent is not None:
        bundled_parent_text = str(bundled_parent)
        path_entries = [
            entry for entry in path_entries if entry != bundled_parent_text
        ]
        path_entries.append(bundled_parent_text)
    environment["PATH"] = os.pathsep.join(path_entries)
    for name in _HOST_ALTERING_ENV_NAMES:
        environment.pop(name, None)
    return environment


def _existing_environment_directory(value: object) -> Path | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        path = Path(value)
        if not path.is_absolute():
            return None
        canonical = path.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None
    return path if canonical.is_dir() else None


def _canonical_environment_directory(value: str) -> Path | None:
    try:
        path = Path(value)
        if not path.is_absolute():
            return None
        canonical = path.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None
    return canonical if canonical.is_dir() else None


def _run_verified_shell(
    workspace: WorkspaceIdentity,
    command: str,
    cwd: WorkspaceIdentity,
    timeout_seconds: int,
    cancel: threading.Event,
    on_delta: Callable[[str], None],
    started: float,
    resource_registry: ResourceRegistry | None,
    owner_id: str,
    attempt: SandboxAttempt | None,
    active_skill_roots: Sequence[Path],
    skill_invocation: SkillAccessRecord | None,
) -> dict[str, object]:
    host_shell = HOST_SHELL_RESOLVER.resolve()
    sandboxed = attempt is None or attempt.sandbox is SandboxType.MACOS_SEATBELT
    if sandboxed:
        fallback_environment = _effective_shell_environment(
            SHELL_ENVIRONMENT_PROVIDER.fallback_environment(host_shell),
            host_shell,
        )
        preflight_profile = _create_seatbelt_profile(
            workspace=workspace,
            environment=fallback_environment,
            attempt=attempt,
            active_skill_roots=active_skill_roots,
        )
        preflight_profile.command([str(host_shell.executable), "-c", ":"])
    snapshot = SHELL_ENVIRONMENT_PROVIDER.get(
        host_shell,
        cwd.path,
        command_wrapper=preflight_profile.command if sandboxed else None,
    )
    environment = _effective_shell_environment(snapshot.environment, host_shell)
    profile = _create_seatbelt_profile(
        workspace=workspace,
        environment=environment,
        attempt=attempt,
        active_skill_roots=active_skill_roots,
    )
    _verify_directory_path(workspace)
    _verify_directory_path(cwd)
    launch = prepare_shell_launch(
        profile=profile,
        command=command,
        cwd=cwd,
        attempt=attempt,
        shell=host_shell,
        environment=environment,
        environment_source=snapshot.source,
    )
    result = run_shell_process(
        launch,
        timeout_seconds=timeout_seconds,
        cancel=cancel,
        on_delta=on_delta,
        started=started,
        resource_registry=resource_registry,
        owner_id=owner_id,
    )
    return _attach_skill_invocation(result, skill_invocation)


def _create_seatbelt_profile(
    *,
    workspace: WorkspaceIdentity,
    environment: Mapping[str, str],
    attempt: SandboxAttempt | None,
    active_skill_roots: Sequence[Path],
) -> SeatbeltProfile:
    return SeatbeltProfile.create(
        workspace_root=workspace.path,
        sandbox_home=Path(environment["HOME"]),
        sandbox_tmp=Path(environment["TMPDIR"]),
        git_worktree_dir=workspace.git_dir,
        git_common_dir=workspace.git_common_dir,
        effective_permissions=(
            attempt.permissions
            if attempt is not None
            and attempt.sandbox is SandboxType.MACOS_SEATBELT
            else None
        ),
        active_skill_roots=active_skill_roots,
    )


def prepare_shell_launch(
    *,
    profile: SeatbeltProfile,
    command: str,
    cwd: WorkspaceIdentity,
    attempt: SandboxAttempt | None,
    shell: HostShell,
    environment: Mapping[str, str],
    environment_source: str | None = None,
) -> ShellLaunchSpec:
    sandboxed = attempt is None or attempt.sandbox is SandboxType.MACOS_SEATBELT
    argv = (
        profile.command([str(shell.executable), "-c", command])
        if sandboxed
        else [str(shell.executable), "-c", command]
    )
    return ShellLaunchSpec(
        argv=tuple(argv),
        cwd=cwd.path,
        environment=dict(environment),
        sandboxed=sandboxed,
        shell_kind=shell.kind,
        environment_source=environment_source,
    )


def run_shell_process(
    launch: ShellLaunchSpec,
    *,
    timeout_seconds: int,
    cancel: threading.Event,
    on_delta: Callable[[str], None],
    started: float,
    resource_registry: ResourceRegistry | None,
    owner_id: str,
) -> dict[str, object]:
    try:
        process = subprocess.Popen(
            launch.argv,
            cwd=launch.cwd,
            env=launch.environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as error:
        raise ShellProcessStartError from error
    selector = selectors.DefaultSelector()
    assert process.stdout is not None and process.stderr is not None
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    outputs = {"stdout": bytearray(), "stderr": bytearray()}
    total = 0
    observed_bytes = 0
    truncated = False
    termination = "exit"
    termination_started_at: float | None = None
    process_group = process.pid
    resource = (
        resource_registry.register(
            RuntimeResourceKind.SHELL_PROCESS,
            owner_id=owner_id,
            cancel=lambda: _terminate_group(process_group),
            is_quiescent=lambda: not _process_group_exists(process_group),
        )
        if resource_registry is not None
        else None
    )
    if resource is not None:
        resource.start()
    try:
        while selector.get_map() or process.poll() is None:
            if cancel.is_set():
                if termination == "exit":
                    termination = "canceled"
                    termination_started_at = time.monotonic()
                    _terminate_group(process_group)
            elif time.monotonic() - started >= timeout_seconds:
                if termination == "exit":
                    termination = "timeout"
                    termination_started_at = time.monotonic()
                    _terminate_group(process_group)
            ready = selector.select(timeout=0.1) if selector.get_map() else ()
            if not selector.get_map() and process.poll() is None:
                time.sleep(0.05)
            for key, _mask in ready:
                chunk = os.read(key.fileobj.fileno(), 64 * 1024)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                observed_bytes = min(
                    MAX_OUTPUT_METADATA_BYTES,
                    observed_bytes + len(chunk),
                )
                remaining = max(0, MAX_OUTPUT_BYTES - total)
                accepted = chunk[:remaining]
                if accepted:
                    outputs[key.data].extend(accepted)
                    total += len(accepted)
                    on_delta(accepted.decode("utf-8", errors="replace"))
                if len(accepted) < len(chunk):
                    truncated = True
            if process.poll() is not None and termination == "exit":
                if _process_group_exists(process_group):
                    termination = "background_process"
                    termination_started_at = time.monotonic()
                    _terminate_group(process_group)
                elif not selector.get_map():
                    break
            if (
                termination_started_at is not None
                and time.monotonic() - termination_started_at
                >= POST_TERMINATION_DRAIN_SECONDS
            ):
                for key in tuple(selector.get_map().values()):
                    selector.unregister(key.fileobj)
                break
        try:
            returncode = process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            _terminate_group(process_group)
            returncode = process.wait(timeout=1)
    finally:
        selector.close()
        if process.poll() is None or _process_group_exists(process_group):
            _terminate_group(process_group)
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
        process.stdout.close()
        process.stderr.close()
        if resource is not None:
            resource.close()
    stdout = bytes(outputs["stdout"]).decode("utf-8", errors="replace")
    stderr = bytes(outputs["stderr"]).decode("utf-8", errors="replace")
    hit_fault("shell_modify_then_fail")
    outcome = "success" if returncode == 0 and termination == "exit" else "error"
    code = (
        "ok"
        if outcome == "success"
        else termination
        if termination != "exit"
        else "nonzero_exit"
    )
    data: dict[str, object] = {
        "exitCode": returncode,
        "stdout": stdout,
        "stderr": stderr,
        "truncated": truncated,
        "termination": termination,
        "durationMs": int((time.monotonic() - started) * 1000),
        "originalBytes": observed_bytes,
        "omittedBytes": min(
            MAX_OUTPUT_METADATA_BYTES,
            max(0, observed_bytes - total),
        ),
        "shellKind": launch.shell_kind,
        "environmentSource": launch.environment_source,
    }
    if truncated:
        data["truncationReason"] = "output_limit"
    return {
        "schemaVersion": 1,
        "toolName": "run_shell",
        "outcome": outcome,
        "code": code,
        "summary": (
            "Command completed"
            if outcome == "success"
            else _shell_failure_summary(code, returncode, termination)
        ),
        "data": data,
        "sideEffectsMayExist": True,
    }


def _shell_failure_summary(
    code: str,
    returncode: int,
    termination: str,
) -> str:
    if code == "nonzero_exit":
        return f"Command failed (exit code {returncode}; termination={termination})"
    return f"Command did not succeed (termination={termination})"


def _summary_with_termination(summary: str, termination: str) -> str:
    if f"termination={termination}" in summary:
        return summary
    return f"{summary} (termination={termination})"


def _terminate_group(process_group: int) -> None:
    hit_fault("shell_ignore_sigterm")
    try:
        os.killpg(process_group, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    deadline = time.monotonic() + TERMINATION_GRACE_SECONDS
    while time.monotonic() < deadline:
        if not _process_group_exists(process_group):
            return
        time.sleep(0.02)
    try:
        os.killpg(process_group, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        return
    deadline = time.monotonic() + TERMINATION_GRACE_SECONDS
    while time.monotonic() < deadline:
        if not _process_group_exists(process_group):
            return
        time.sleep(0.02)


def _process_group_exists(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _attach_skill_invocation(
    result: dict[str, object],
    invocation: SkillAccessRecord | None,
) -> dict[str, object]:
    if invocation is None:
        return result
    data = result.get("data")
    if not isinstance(data, dict):
        data = {}
        result["data"] = data
    data.update(invocation.result_data())
    return result


def _open_verified_directory(identity: WorkspaceIdentity) -> int:
    flags = os.O_RDONLY | os.O_DIRECTORY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(identity.path, flags)
    except OSError:
        raise ValueError("workspace_identity_changed") from None
    metadata = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or (metadata.st_dev, metadata.st_ino, metadata.st_uid)
        != (identity.device, identity.inode, identity.owner)
    ):
        os.close(descriptor)
        raise ValueError("workspace_identity_changed")
    return descriptor


def _verify_directory_path(identity: WorkspaceIdentity) -> None:
    descriptor = _open_verified_directory(identity)
    try:
        metadata = os.stat(identity.path, follow_symlinks=False)
        if (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_uid,
        ) != (identity.device, identity.inode, identity.owner):
            raise ValueError("workspace_identity_changed")
    finally:
        os.close(descriptor)

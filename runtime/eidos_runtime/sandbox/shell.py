from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import tempfile
import threading
import time
from typing import Callable

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


MAX_OUTPUT_BYTES = 256 * 1024
TERMINATION_GRACE_SECONDS = 0.5
POST_TERMINATION_DRAIN_SECONDS = 1.0


@dataclass(frozen=True)
class ShellLaunchSpec:
    argv: tuple[str, ...]
    cwd: Path
    environment: dict[str, str]
    sandboxed: bool


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
        )
    except ValueError:
        return {
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
            },
            "sideEffectsMayExist": False,
        }
    except SeatbeltUnavailableError:
        return sandbox_unavailable_result(started)
    finally:
        if cwd_fd >= 0:
            os.close(cwd_fd)
        if workspace_fd >= 0:
            os.close(workspace_fd)


def sandbox_unavailable_result(started: float | None = None) -> dict[str, object]:
    duration_ms = (
        0
        if started is None
        else max(0, int((time.monotonic() - started) * 1000))
    )
    return {
        "schemaVersion": 1,
        "toolName": "run_shell",
        "outcome": "error",
        "code": "sandbox_unavailable",
        "summary": "Command was not started because the Shell sandbox is unavailable",
        "data": {
            "exitCode": None,
            "stdout": "",
            "stderr": "",
            "truncated": False,
            "termination": "not_started",
            "durationMs": duration_ms,
        },
        "sideEffectsMayExist": False,
    }


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
) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="eidos-shell-") as temporary:
        root = Path(temporary)
        home = root / "home"
        temp = root / "tmp"
        home.mkdir()
        temp.mkdir()
        profile = SeatbeltProfile.create(
            workspace_root=workspace.path,
            sandbox_home=home,
            sandbox_tmp=temp,
            sensitive_path=workspace.path / ".env",
            git_worktree_dir=workspace.git_dir,
            git_common_dir=workspace.git_common_dir,
            effective_permissions=(
                attempt.permissions
                if attempt is not None
                and attempt.sandbox is SandboxType.MACOS_SEATBELT
                else None
            ),
        )
        _verify_directory_path(workspace)
        _verify_directory_path(cwd)
        launch = prepare_shell_launch(
            profile=profile,
            command=command,
            cwd=cwd,
            attempt=attempt,
        )
        return run_shell_process(
            launch,
            timeout_seconds=timeout_seconds,
            cancel=cancel,
            on_delta=on_delta,
            started=started,
            resource_registry=resource_registry,
            owner_id=owner_id,
        )


def prepare_shell_launch(
    *,
    profile: SeatbeltProfile,
    command: str,
    cwd: WorkspaceIdentity,
    attempt: SandboxAttempt | None,
) -> ShellLaunchSpec:
    sandboxed = attempt is None or attempt.sandbox is SandboxType.MACOS_SEATBELT
    argv = (
        profile.command(["/bin/sh", "-c", command])
        if sandboxed
        else ["/bin/sh", "-c", command]
    )
    return ShellLaunchSpec(
        argv=tuple(argv),
        cwd=cwd.path,
        environment=profile.environment(),
        sandboxed=sandboxed,
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
    process = subprocess.Popen(
        launch.argv,
        cwd=launch.cwd,
        env=launch.environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    selector = selectors.DefaultSelector()
    assert process.stdout is not None and process.stderr is not None
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    outputs = {"stdout": bytearray(), "stderr": bytearray()}
    total = 0
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
    return {
        "schemaVersion": 1,
        "toolName": "run_shell",
        "outcome": outcome,
        "code": code,
        "summary": (
            "Command completed"
            if outcome == "success"
            else "Command did not succeed"
        ),
        "data": {
            "exitCode": returncode,
            "stdout": stdout,
            "stderr": stderr,
            "truncated": truncated,
            "termination": termination,
            "durationMs": int((time.monotonic() - started) * 1000),
        },
        "sideEffectsMayExist": True,
    }


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

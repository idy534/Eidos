from __future__ import annotations

from contextlib import ExitStack
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from eidos_runtime.infrastructure.mxc_status import MxcProcessStatus, parse_mxc_status
from eidos_runtime.sandbox.shell import apply_mxc_status
from eidos_runtime.sandbox.windows_mxc import MxcHelper, MxcUnavailableError, _catalog_helper, run_mxc_self_test


@pytest.mark.parametrize(("payload", "code", "expected"), [
    (b'{"event":"failed","code":"policy_rejected"}\n', 1, MxcProcessStatus(failure_code="policy_rejected")),
    (b'{"event":"started"}\n{"event":"exited","exitCode":0}\n', 0, MxcProcessStatus(exit_code=0)),
    (b'{"event":"started"}\n{"event":"exited","exitCode":4294967295}\n', -1, MxcProcessStatus(exit_code=4294967295)),
    (b'{"event":"started"}\n', 1, MxcProcessStatus()),
    (b'{"event":"started"}\n{"event":"failed","code":"launch_failed"}\n', 1, MxcProcessStatus()),
    (b'{"event":"failed","code":"launch_failed"}', 1, MxcProcessStatus()),
    (b'{"event":"failed","code":"launch_failed","code":"policy_rejected"}\n', 1, MxcProcessStatus()),
    (b'{"event":"started"}\n{"event":"exited","exitCode":0}\n', 1, MxcProcessStatus()),
])
def test_mxc_status_requires_one_complete_failure_or_definite_exit(
    payload: bytes, code: int, expected: MxcProcessStatus,
) -> None:
    assert parse_mxc_status(payload, code) == expected


def test_missing_mxc_exit_status_records_uncertain_effects() -> None:
    result = apply_mxc_status(
        {"outcome": "success", "data": {"exitCode": 0, "termination": "exit"}}, MxcProcessStatus(),
    )
    assert result["code"] == "shell_execution_unknown"
    assert result["sideEffectsMayExist"] is True
    assert result["reconciliationRequired"] is True
    assert result["data"] == {"exitCode": None, "termination": "unknown"}


def test_mxc_prelaunch_rejection_records_no_effects() -> None:
    result = apply_mxc_status(
        {"outcome": "error", "data": {"exitCode": 1, "termination": "exit"}},
        MxcProcessStatus(failure_code="policy_rejected"),
    )
    assert result["code"] == "sandbox_unavailable"
    assert result["sideEffectsMayExist"] is False
    assert result["reconciliationRequired"] is False
    assert result["data"] == {"exitCode": None, "termination": "not_started"}


def test_old_windows_build_has_no_default_sandbox(monkeypatch: pytest.MonkeyPatch) -> None:
    from eidos_runtime.sandbox import windows_mxc

    monkeypatch.setattr(windows_mxc, "_ready_helper", None)
    monkeypatch.setattr(windows_mxc.local_files, "WINDOWS", True)
    monkeypatch.setattr(sys, "getwindowsversion", lambda: SimpleNamespace(build=22631), raising=False)
    outcome = run_mxc_self_test(None)
    assert not outcome.available
    assert outcome.failures == ("unsupported_platform",)


@pytest.mark.platform
@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows NTFS handle validation")
def test_helper_hash_is_checked_before_bootstrap(tmp_path: Path) -> None:
    helper = tmp_path / "eidos-sandbox-win.exe"
    helper.write_bytes(b"changed executable")
    with pytest.raises(MxcUnavailableError, match="mxc_helper_hash_mismatch"):
        MxcHelper(helper, "0" * 64, tmp_path).probe()


_FAKE_MXC = r"""
import msvcrt
import os
import sys
request = msvcrt.open_osfhandle(int(sys.argv[-3]), os.O_RDONLY | os.O_BINARY)
status = msvcrt.open_osfhandle(int(sys.argv[-1]), os.O_WRONLY | os.O_BINARY)
with os.fdopen(request, "rb") as stream:
    payload = stream.read()
with os.fdopen(status, "wb") as stream:
    stream.write(b'{"event":"started"}\n')
    sys.stdout.buffer.write(payload[:5] + sys.stdin.buffer.read())
    if sys.argv[2] == "complete":
        stream.write(b'{"event":"exited","exitCode":0}\n')
"""


@pytest.mark.platform
@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows Job and handle semantics")
@pytest.mark.parametrize("complete", [True, False])
def test_mxc_job_owns_an_independent_request_and_status_pipe(tmp_path: Path, complete: bool) -> None:
    from eidos_runtime.infrastructure.windows_processes import MxcJobProcess

    fake = tmp_path / "mxc_stub.py"
    fake.write_text(_FAKE_MXC, encoding="utf-8")
    closed = []
    resources = ExitStack()
    resources.callback(closed.append, True)
    process = MxcJobProcess(
        (sys.executable, "-I", str(fake), "run", "complete" if complete else "incomplete"),
        cwd=tmp_path, env=dict(os.environ),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        request=b"hello" * 24_000, launch_resources=resources,
    )
    try:
        assert process.has_members()
        process.release()
        stdout, stderr = process.communicate(b"hook input", timeout=8)
        assert stderr == b""
        assert stdout == b"hellohook input"
        expected = MxcProcessStatus(exit_code=0) if complete else MxcProcessStatus()
        assert process.mxc_status() == expected
    finally:
        process.close_job()
    assert closed == [True]
    assert process.mxc_status() == expected


@pytest.mark.platform
@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows PowerShell")
def test_windows_without_mxc_requires_explicit_approval(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from eidos_runtime.db.storage import SessionStore
    from eidos_runtime.infrastructure.windows_files import configure_process_file_owner
    from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel
    from eidos_runtime.runtime.approval import ApprovalDecision
    from eidos_runtime.runtime.engine import RuntimeEngine
    from eidos_runtime.sandbox import windows_mxc

    configure_process_file_owner()
    monkeypatch.setattr(windows_mxc, "_ready_helper", None)
    monkeypatch.setenv("EIDOS_WINDOWS_SANDBOX", "off")
    capability = run_mxc_self_test(None)
    assert not capability.available
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    marker = workspace / "approved"
    command = "[IO.File]::WriteAllText((Join-Path (Get-Location).ProviderPath 'approved'), 'ok')"
    approvals: list[dict[str, object]] = []

    def approve(request: dict[str, object], _cancel: threading.Event) -> ApprovalDecision:
        assert not marker.exists()
        approvals.append(request)
        return ApprovalDecision("approve")

    store = SessionStore(tmp_path / "data")
    try:
        store.initialize()
        session = store.create_session(str(workspace))
        run, _ = store.create_run(session["id"], "Write a temporary test file with approval")
        model = ScriptedModel([
            ModelResponse(tool_calls=(ModelToolCall("restricted", "run_shell", {"command": command}),)),
            ModelResponse(tool_calls=(ModelToolCall("approved", "run_shell", {
                "command": command, "sandboxPermissions": "require_escalated",
                "justification": "Write a temporary test file",
            }),)),
            ModelResponse(text="Done."),
        ])
        RuntimeEngine(store, model, lambda _message: None, request_approval=approve).run(run["id"], threading.Event())
        items = store.read_session_snapshot(session["id"])["items"]
        results = [json.loads(item["toolCall"]["resultJson"]) for item in items
                   if (item.get("toolCall") or {}).get("toolName") == "run_shell"]
        assert [result["code"] for result in results] == ["sandbox_unavailable", "ok"]
        assert results[1]["data"]["sandboxed"] is False
        assert len(approvals) == 1
        assert marker.read_text(encoding="utf-8") == "ok"
        assert [row[0] for row in store.connection.execute("SELECT sandbox_type FROM tool_attempts")] == ["none"]
    finally:
        store.close()


@pytest.mark.platform
@pytest.mark.skipif(sys.platform != "win32", reason="requires a Windows ProcessContainer host")
def test_bundled_mxc_self_test_and_cancellation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import _winapi

    from eidos_runtime.db.storage import SessionStore
    from eidos_runtime.infrastructure.windows_files import configure_process_file_owner
    from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel
    from eidos_runtime.runtime.engine import RuntimeEngine
    from eidos_runtime.runtime.runtime_dependencies import discover_runtime_dependency_catalog
    from eidos_runtime.runtime.shell_process_manager import ShellProcessManager
    from eidos_runtime.sandbox.permissions import (
        BasePermissionProfile, SandboxAttempt, SandboxType, materialize_effective_profile,
    )
    from eidos_runtime.sandbox.shell import prepare_shell_launch_for_execution
    from eidos_runtime.sandbox import windows_mxc
    from eidos_runtime.workspace.reader import capture_directory_identity

    configure_process_file_owner()
    catalog = discover_runtime_dependency_catalog()
    try:
        _catalog_helper(catalog).probe()
    except MxcUnavailableError as error:
        if str(error) in {"mxc_helper_missing", "mxc_psec_unavailable", "mxc_fs_deny_unsupported"}:
            pytest.skip(str(error))
        raise
    monkeypatch.setattr(windows_mxc, "_ready_helper", None)
    monkeypatch.delenv("EIDOS_WINDOWS_SANDBOX", raising=False)
    capability = run_mxc_self_test(catalog)
    assert capability.available, capability.failures

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    try:
        store.initialize()
        session = store.create_session(str(workspace))
        run, _ = store.create_run(session["id"], "Write inside the MXC workspace")
        model = ScriptedModel([
            ModelResponse(tool_calls=(ModelToolCall("mxc-command", "run_shell", {
                "command": "[IO.File]::WriteAllText((Join-Path (Get-Location).ProviderPath 'runtime-ran'), 'ok')",
                "display_intent": "Write a temporary test file",
            }),)),
            ModelResponse(text="Done."),
        ])
        RuntimeEngine(store, model, lambda _message: None, shell_available=True).run(run["id"], threading.Event())
        items = store.read_session_snapshot(session["id"])["items"]
        call = next(item["toolCall"] for item in items if (item.get("toolCall") or {}).get("toolName") == "run_shell")
        result = json.loads(call["resultJson"])
        assert result["outcome"] == "success", result
        assert result["data"]["sandboxed"] is True
        assert (workspace / "runtime-ran").read_text(encoding="utf-8") == "ok"
        attempt_row = store.connection.execute("SELECT sandbox_type, status, result_code FROM tool_attempts").fetchone()
        assert tuple(attempt_row) == ("windows_mxc", "completed", "ok")
    finally:
        store.close()

    identity = capture_directory_identity(workspace)
    permissions = materialize_effective_profile(BasePermissionProfile.for_workspace(workspace_root=workspace))
    attempt = SandboxAttempt(
        ordinal=0, sandbox=SandboxType.WINDOWS_MXC, sandboxRequested=True,
        permissions=permissions, sandboxCwd=str(workspace), workspaceRoots=(str(workspace),),
    )
    command = """
if ([Console]::ReadLine() -ne 'start child') { exit 42 }
$start = New-Object System.Diagnostics.ProcessStartInfo
$start.FileName = Join-Path $PSHOME 'powershell.exe'
$start.Arguments = '-NoProfile -NonInteractive -Command Start-Sleep -Seconds 30'
$start.UseShellExecute = $false
$start.CreateNoWindow = $true
$child = [Diagnostics.Process]::Start($start)
Write-Output $child.Id
Start-Sleep -Seconds 30
"""
    launch = prepare_shell_launch_for_execution(identity, command, identity, attempt)
    cancel = threading.Event()
    manager = ShellProcessManager(owner_run_id="mxc-cancel")
    try:
        result = manager.start(launch, yield_time_ms=5, cancel=cancel)
        session_id = result["data"]["sessionId"]
        manager.write_stdin(session_id, "start child\n", yield_time_ms=0)
        deadline = time.monotonic() + 8
        output = manager.read_output(session_id)["data"]["stdout"]
        while "\n" not in output:
            assert time.monotonic() < deadline
            time.sleep(0.02)
            output = manager.read_output(session_id)["data"]["stdout"]
        child = _winapi.OpenProcess(0x00100000, False, int(output.strip()))  # SYNCHRONIZE
        try:
            assert _winapi.WaitForSingleObject(child, 0) == _winapi.WAIT_TIMEOUT
            result = manager.interrupt(session_id, yield_time_ms=4000)
            assert _winapi.WaitForSingleObject(child, 4000) == _winapi.WAIT_OBJECT_0
            assert result["data"]["executionStatus"] == "exited"
            assert result["code"] == "canceled"
            assert result["reconciliationRequired"] is True
        finally:
            _winapi.CloseHandle(child)
    finally:
        manager.cleanup()

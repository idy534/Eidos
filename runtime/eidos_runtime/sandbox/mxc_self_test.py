"""Verify the bundled MXC launch path against temporary local resources."""
from __future__ import annotations

import json
import socket
import tempfile
import threading
import time

from eidos_runtime.domain.shell_environment import ShellEnvironmentPolicy
from eidos_runtime.infrastructure import local_files
from eidos_runtime.sandbox.host_shell import HOST_SHELL_RESOLVER
from eidos_runtime.sandbox.permissions import (
    BasePermissionProfile, SandboxAttempt, SandboxType, materialize_effective_profile,
)
from eidos_runtime.sandbox.shell import prepare_shell_launch, run_shell_process
from eidos_runtime.sandbox.windows_mxc import MxcHelper, MxcLaunch
from eidos_runtime.workspace.reader import capture_directory_identity


_CHECKS = (
    "cwd", "workspace_write", "external_read", "external_write_denied", "private_read_denied",
    "git_read", "git_write_denied", "metadata_creation_denied", "tmp_write", "stdin",
    "child_inherits_policy", "loopback_denied",
)
_COMMAND = r"""
$ProgressPreference = 'SilentlyContinue'
$workspace = Join-Path $root 'workspace'
$outside = Join-Path $root 'outside'
$private = Join-Path $root 'private'
function ReadProbe([string]$target) {
    try { return [IO.File]::ReadAllText($target) -eq 'eidos-mxc' } catch { return $false }
}
function WriteProbe([string]$target) {
    try { [IO.File]::WriteAllText($target, 'eidos-mxc'); return $true } catch { return $false }
}
function DirectoryProbe([string]$target) {
    try { [void][IO.Directory]::CreateDirectory($target); return $true } catch { return $false }
}
$connected = $false
$client = New-Object Net.Sockets.TcpClient
try {
    $pending = $client.BeginConnect('127.0.0.1', $port, $null, $null)
    if ($pending.AsyncWaitHandle.WaitOne(750)) {
        $client.EndConnect($pending)
        $connected = $client.Connected
    }
} catch { } finally { $client.Dispose() }
$childAllowed = Join-Path $workspace 'child'
$childDenied = Join-Path $outside 'child'
$allowedLiteral = $childAllowed.Replace("'", "''")
$deniedLiteral = $childDenied.Replace("'", "''")
$childScript = "[IO.File]::WriteAllText('$allowedLiteral', 'eidos-mxc'); try { [IO.File]::WriteAllText('$deniedLiteral', 'eidos-mxc'); exit 7 } catch { exit 0 }"
$encoded = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($childScript))
$start = New-Object System.Diagnostics.ProcessStartInfo
$start.FileName = Join-Path $PSHOME 'powershell.exe'
$start.Arguments = "-NoLogo -NoProfile -NonInteractive -EncodedCommand $encoded"
$start.WorkingDirectory = $workspace
$start.UseShellExecute = $false
$start.CreateNoWindow = $true
$child = [Diagnostics.Process]::Start($start)
try {
    $child.WaitForExit()
    $childExit = $child.ExitCode
} finally { $child.Dispose() }
[ordered]@{
    cwd = (Get-Location).ProviderPath -eq $workspace
    workspace_write = (WriteProbe (Join-Path $workspace 'write'))
    external_read = (ReadProbe (Join-Path $outside 'read'))
    external_write_denied = -not (WriteProbe (Join-Path $outside 'write'))
    private_read_denied = -not (ReadProbe (Join-Path $private 'token'))
    git_read = (ReadProbe (Join-Path $workspace '.git\test'))
    git_write_denied = -not (WriteProbe (Join-Path $workspace '.git\write'))
    metadata_creation_denied = -not (DirectoryProbe (Join-Path $workspace '.agents')) -and
                               -not (DirectoryProbe (Join-Path $workspace '.eidos'))
    tmp_write = (WriteProbe (Join-Path $env:TEMP 'write'))
    stdin = [Console]::In.ReadToEnd() -eq 'eidos-mxc'
    child_inherits_policy = $childExit -eq 0 -and (ReadProbe $childAllowed) -and -not [IO.File]::Exists($childDenied)
    loopback_denied = -not $connected
} | ConvertTo-Json -Compress
exit 0
"""


def check_mxc_execution(helper: MxcHelper) -> tuple[str, ...]:
    with tempfile.TemporaryDirectory(prefix="eidos-mxc-check-") as directory, socket.socket() as loopback:
        root = local_files.canonical_path(directory)
        workspace = root / "workspace"
        outside = root / "outside"
        private = root / "private"
        for path in (workspace, workspace / ".git", outside, private):
            path.mkdir()
        for path in (workspace / ".git" / "test", outside / "read", private / "token"):
            path.write_text("eidos-mxc", encoding="utf-8")
        loopback.bind(("127.0.0.1", 0))
        loopback.listen(1)
        port = loopback.getsockname()[1]
        command = f"$root = '{str(root).replace(chr(39), chr(39) * 2)}'\n$port = {port}\n" + _COMMAND
        identity = capture_directory_identity(workspace)
        permissions = materialize_effective_profile(BasePermissionProfile.for_workspace(
            workspace_root=workspace, hard_confidentiality_paths=(private,),
        ))
        attempt = SandboxAttempt(
            ordinal=0, sandbox=SandboxType.WINDOWS_MXC, sandboxRequested=True, permissions=permissions,
            sandboxCwd=str(workspace), workspaceRoots=(str(workspace),),
        )
        launch = prepare_shell_launch(
            profile=None, command=command, cwd=identity, attempt=attempt,
            shell=HOST_SHELL_RESOLVER.resolve(), environment={},
            environment_policy=ShellEnvironmentPolicy(inherit="none"),
            mxc=MxcLaunch(helper, permissions, identity, identity),
        )
        result = run_shell_process(
            launch, timeout_seconds=5, cancel=threading.Event(), on_delta=lambda _text: None,
            started=time.monotonic(), resource_registry=None, owner_id="mxc-self-test", stdin_bytes=b"eidos-mxc",
        )
        if result.get("outcome") != "success":
            return ("mxc_self_test_command_failed",)
        try:
            checks = json.loads(result["data"]["stdout"])
        except (KeyError, TypeError, ValueError):
            return ("mxc_self_test_result_invalid",)
        if not isinstance(checks, dict):
            return ("mxc_self_test_result_invalid",)
        return tuple(check for check in _CHECKS if checks.get(check) is not True)

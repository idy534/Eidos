from pathlib import Path
import threading
import json
from unittest.mock import patch

import pytest

from eidos_runtime.sandbox.permissions import BasePermissionProfile, SandboxAttempt, SandboxType, materialize_effective_profile
from eidos_runtime.sandbox.shell import ShellLaunchSpec, ShellProcessStartError, prepare_shell_launch_for_execution
from eidos_runtime.runtime.shell_process_manager import ShellProcessManager
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel
from eidos_runtime.runtime.engine import RuntimeEngine
from eidos_runtime.workspace.reader import capture_workspace_identity


def test_full_access_accepts_verified_external_cwd(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    external = tmp_path / "external"
    workspace.mkdir()
    external.mkdir()
    base = BasePermissionProfile.for_workspace(workspace_root=workspace).with_full_access()
    attempt = SandboxAttempt(
        ordinal=0, sandbox=SandboxType.NONE, sandboxRequested=False,
        permissions=materialize_effective_profile(base), sandboxCwd=str(workspace),
        workspaceRoots=(str(workspace),),
    )
    expected = ShellLaunchSpec(argv=("/bin/sh", "-c", ":"), cwd=external, environment={}, sandboxed=False)
    with patch("eidos_runtime.sandbox.shell._prepare_verified_shell_launch", return_value=expected):
        assert prepare_shell_launch_for_execution(
            capture_workspace_identity(workspace), ":", capture_workspace_identity(external), attempt,
        ) == expected
    with pytest.raises(ValueError, match="outside workspace"):
        prepare_shell_launch_for_execution(
            capture_workspace_identity(workspace), ":", capture_workspace_identity(external), None,
        )


def test_failed_spawn_never_reports_execution_started(tmp_path: Path) -> None:
    started = []
    launch = ShellLaunchSpec(argv=("/missing-eidos-shell",), cwd=tmp_path, environment={}, sandboxed=False)
    with pytest.raises(ShellProcessStartError):
        ShellProcessManager().start(launch, on_started=lambda: started.append(True))
    assert started == []


@pytest.mark.integration
def test_full_access_shell_external_cwd_can_finish_run(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    external = tmp_path / "external"
    workspace.mkdir()
    external.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    session = store.create_session(str(workspace))
    run, _ = store.create_run(session["id"], "Read the external working directory.", approval_mode="full_access")
    model = ScriptedModel([
        ModelResponse(tool_calls=(ModelToolCall("external-pwd", "run_shell", {"command": "pwd", "cwd": str(external)}),)),
        ModelResponse(text="The working directory has been checked."),
    ])
    try:
        RuntimeEngine(store, model, lambda _: None).run(run["id"], threading.Event())
        assert store.read_run(run["id"])["status"] == "succeeded"
        tool = next(item["toolCall"] for item in store.read_session_snapshot(session["id"])["items"] if "toolCall" in item)
        result = json.loads(tool["resultJson"])
        assert result["data"]["exitCode"] == 0
        assert str(external.resolve()) in result["data"]["stdout"]
        assert result["reconciliationRequired"] is False
        assert len(model.contexts) == 2
    finally:
        store.close()

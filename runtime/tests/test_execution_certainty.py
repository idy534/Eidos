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
from eidos_runtime.workspace.reader import WorkspacePathError, WorkspaceReader, capture_workspace_identity


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


def test_recovery_reads_only_targets_and_accepts_verified_absence(tmp_path: Path) -> None:
    (tmp_path / "target.txt").write_text("current contents")
    (tmp_path / "unrelated").mkdir()
    (tmp_path / "unrelated" / "unsafe-link").symlink_to("/etc")
    with WorkspaceReader(tmp_path) as reader:
        observations = reader.observe_files(frozenset({"target.txt", "missing/target.txt"}), cancel=threading.Event())
    assert {item.path for item in observations} == {"target.txt", "missing/target.txt"}
    assert next(item for item in observations if item.path == "target.txt").sha256 is not None
    assert next(item for item in observations if item.path == "missing/target.txt").version is None


def test_recovery_keeps_target_symlinks_and_hardlinks_closed(tmp_path: Path) -> None:
    (tmp_path / "regular.txt").write_text("content")
    (tmp_path / "link.txt").symlink_to("regular.txt")
    with WorkspaceReader(tmp_path) as reader:
        with pytest.raises(WorkspacePathError):
            reader.observe_files(frozenset({"link.txt"}), cancel=threading.Event())
    (tmp_path / "hard.txt").hardlink_to(tmp_path / "regular.txt")
    with WorkspaceReader(tmp_path) as reader:
        with pytest.raises(WorkspacePathError, match="unsupported_file_hardlink"):
            reader.observe_files(frozenset({"hard.txt"}), cancel=threading.Event())


def test_recovery_detects_a_target_changing_after_observation(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("before")
    with WorkspaceReader(tmp_path) as reader:
        original_stat = reader.stat_file

        def changed(path, **kwargs):
            target.write_text("after")
            return original_stat(path, **kwargs)

        with patch.object(reader, "stat_file", side_effect=changed):
            with pytest.raises(WorkspacePathError, match="workspace_changed"):
                reader.observe_files(frozenset({"target.txt"}), cancel=threading.Event())

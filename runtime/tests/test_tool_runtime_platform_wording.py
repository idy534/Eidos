from __future__ import annotations

import json
from pathlib import Path
import threading

import pytest

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.model.client import ModelToolCall
from eidos_runtime.runtime import tool_runtime
from eidos_runtime.runtime.approval import ApprovalCoordinator, ApprovalDecision
from eidos_runtime.runtime.events import RuntimeEvents
from eidos_runtime.runtime.shell_process_manager import ShellProcessManager
from eidos_runtime.runtime.state_machine import RuntimePhaseTracker
from eidos_runtime.runtime.tool_dispatcher import ToolDispatcher
from eidos_runtime.runtime.tool_execution import ToolExecutionController
from eidos_runtime.runtime.tool_runtime import ShellToolHandler, _HandlerDependencies
from eidos_runtime.sandbox.permissions import BasePermissionProfile
from eidos_runtime.sandbox.sensitive import default_scanner
from eidos_runtime.tools.workspace import ToolExecutor


class _ShellContext:
    def __init__(self) -> None:
        self.handler: ShellToolHandler | None = None

    def invoke_shell(
        self,
        runtime: object,
        run_id: str,
        item: dict[str, object],
        call: ModelToolCall,
        cancel: threading.Event,
    ):
        assert self.handler is not None
        return self.handler.execute(run_id, item, call, cancel, runtime)


def _capture_escalated_shell_approval(
    tmp_path: Path, platform_name: str, monkeypatch: pytest.MonkeyPatch,
) -> dict[str, object]:
    monkeypatch.setattr(tool_runtime.sys, "platform", platform_name)

    data = tmp_path / "data"
    workspace = tmp_path / "workspace"
    data.mkdir(mode=0o700)
    workspace.mkdir()
    store = SessionStore(data)
    store.initialize()
    executor = ToolExecutor(workspace)
    manager = ShellProcessManager(owner_run_id="pending")
    captured: list[dict[str, object]] = []

    def approval_request(payload: dict[str, object], _cancel: threading.Event) -> ApprovalDecision:
        captured.append(payload)
        return ApprovalDecision("reject")

    try:
        session = store.create_session(str(workspace))
        run, _ = store.create_run(session["id"], "platform wording test")
        run_id = str(run["id"])
        store.increment_model_step(run_id)
        manager = ShellProcessManager(owner_run_id=run_id)
        dispatcher = ToolDispatcher(executor.registry)
        events = RuntimeEvents(lambda _msg: None)
        state = RuntimePhaseTracker()
        approval = ApprovalCoordinator(
            store,
            approval_request,
            events,
            state,
            lambda _run_id: None,
            lambda: None,
            lambda _run_id, _cancel: None,
            lambda _run_id, _cancel: None,
            requeue=False,
        )
        context = _ShellContext()
        controller = ToolExecutionController(
            store,
            dispatcher,
            context,
            events,
            default_scanner(),
            approval=approval,
        )
        dependencies = _HandlerDependencies(
            store,
            dispatcher,
            events,
            default_scanner(),
            False,
            controller.execute_side_effect,
            controller.authorize_side_effect,
            controller.execute_workspace_side_effect,
            controller.authorize_workspace_side_effect,
            run_exclusive_side_effect=controller.run_exclusive_side_effect,
            shell_process_manager=manager,
            base_permissions=BasePermissionProfile.for_workspace(workspace_root=workspace),
        )
        context.handler = ShellToolHandler(dependencies)
        arguments: dict[str, object] = {
            "command": "echo check",
            "cwd": ".",
            "yieldTimeMs": 5_000,
            "networkAccess": "default",
            "gitWriteAccess": "default",
            "sandboxPermissions": "require_escalated",
            "additionalPermissions": None,
            "justification": "Test unsandboxed approval wording",
        }
        item = store.create_tool_item(
            run_id, 1, 0, "shell-call", "run_shell", json.dumps(arguments),
        )
        call = ModelToolCall("shell-call", "run_shell", arguments)
        controller.execute(
            run_id=run_id,
            item=item,
            call=call,
            plan=dispatcher.plan(call),
            cancel=threading.Event(),
            deadline=None,
        )
        assert len(captured) == 1
        assert isinstance(captured[0], dict)
        return captured[0]
    finally:
        manager.close()
        executor.close()
        store.close()


def test_host_platform_label_matches_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool_runtime.sys, "platform", "win32")
    assert tool_runtime._host_platform_label() == "Windows"

    monkeypatch.setattr(tool_runtime.sys, "platform", "darwin")
    assert tool_runtime._host_platform_label() == "macOS"

    monkeypatch.setattr(tool_runtime.sys, "platform", "linux")
    monkeypatch.setattr(tool_runtime.platform, "system", lambda: "Linux")
    assert tool_runtime._host_platform_label() == "Linux"

    monkeypatch.setattr(tool_runtime.platform, "system", lambda: "")
    assert tool_runtime._host_platform_label() == "host OS"


def test_unsandboxed_shell_approval_uses_windows_wording_on_win32(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    params = _capture_escalated_shell_approval(tmp_path, "win32", monkeypatch)
    assert params["summary"] == "Run shell command without the Windows sandbox"
    assert "Windows 用户" in str(params["riskWarning"])
    assert "macOS" not in str(params["summary"])
    assert "macOS" not in str(params["riskWarning"])


def test_unsandboxed_shell_approval_preserves_macos_wording_on_darwin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    params = _capture_escalated_shell_approval(tmp_path, "darwin", monkeypatch)
    assert params["summary"] == "Run shell command without the macOS sandbox"
    assert "macOS 用户" in str(params["riskWarning"])

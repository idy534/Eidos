from __future__ import annotations

import json
from pathlib import Path
import threading

import pytest

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.domain.hooks import HookBinding, HookDefinition
from eidos_runtime.extensions.codex_hooks import parse_hooks
from eidos_runtime.extensions.plugins import PluginCatalog
from eidos_runtime.extensions.skills import SkillCatalog
from eidos_runtime.persistence.hooks import HookRepository
from eidos_runtime.runtime import hooks as runtime_hooks
from eidos_runtime.runtime.events import RuntimeEvents
from eidos_runtime.runtime.hooks import HookService
from eidos_runtime.runtime.resource_registry import ResourceRegistry
from eidos_runtime.sandbox.sensitive import SensitiveScanner
from eidos_runtime.sandbox.shell import ShellLaunchSpec


def _create_hook_service(
    tmp_path: Path, events: dict[str, object],
) -> tuple[SessionStore, HookService, HookBinding, Path]:
    source = tmp_path / "source"
    (source / "hooks").mkdir(parents=True)
    (source / "plugin.json").write_text(
        json.dumps({"name": "hook-demo", "version": "1.0.0", "description": "Windows hook fixture"}),
        encoding="utf-8",
    )
    (source / "hooks" / "hooks.json").write_text(
        json.dumps({"hooks": events}),
        encoding="utf-8",
    )
    store = SessionStore(tmp_path / "data")
    store.initialize()
    catalog = PluginCatalog(store)
    plugin = catalog.import_directory(source)
    catalog.set_enabled("hook-demo", True)
    catalog.set_hook_trust("hook-demo", plugin["contentHash"], True)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = store.create_session(str(workspace))
    run, _ = store.create_run(
        session["id"],
        "Hook Windows fixture",
        approval_mode="full_access",
        extension_snapshot=SkillCatalog(catalog).extension_snapshot(),
    )
    binding = HookBinding.model_validate(store.read_run_extension_snapshot(run["id"])["hooks"][0])
    service = HookService(
        store,
        run["id"],
        resources=ResourceRegistry(),
        sensitive=SensitiveScanner(),
        events=RuntimeEvents(lambda _: None, store=store),
    )
    return store, service, binding, workspace


def test_parse_hooks_preserves_command_windows_and_platform_selection() -> None:
    parsed = parse_hooks(
        {
            "hooks": {
                "PreToolUse": [
                    {"hooks": [{"type": "command", "command": "echo posix", "commandWindows": "Write-Output win"}]},
                    {"hooks": [{"type": "command", "command": "echo posix-only"}]},
                    {"hooks": [{"type": "command", "commandWindows": "Write-Output win-only"}]},
                ],
            },
        },
        source="plugin:demo",
    )
    assert len(parsed) == 3
    both, posix_only, windows_only = parsed

    assert both.command == "echo posix"
    assert both.command_windows == "Write-Output win"
    assert both.command_for_platform(windows=True) == "Write-Output win"
    assert both.command_for_platform(windows=False) == "echo posix"

    assert posix_only.command_for_platform(windows=True) == "echo posix-only"
    assert posix_only.command_for_platform(windows=False) == "echo posix-only"

    assert windows_only.command == ""
    assert windows_only.command_for_platform(windows=True) == "Write-Output win-only"
    assert windows_only.command_for_platform(windows=False) == ""

    with pytest.raises(ValueError, match="hook_command_missing"):
        parse_hooks({"hooks": {"PreToolUse": [{"hooks": [{"type": "command"}]}]}}, source="plugin:bad")

    direct = HookDefinition(key="k", event="PreToolUse", command="posix-cmd", command_windows=None)
    assert direct.command_for_platform(windows=True) == "posix-cmd"


def test_hook_service_executes_command_windows_when_running_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, service, binding, workspace = _create_hook_service(
        tmp_path,
        {"PreToolUse": [{"hooks": [{"type": "command", "command": "echo posix", "commandWindows": "echo win-override"}]}]},
    )
    seen_commands: list[str] = []
    try:
        monkeypatch.setattr(runtime_hooks, "_hook_host_is_windows", lambda: True)

        def fake_prepare(ws, command, cwd, attempt, **_kwargs):
            del ws, cwd, attempt
            seen_commands.append(command)
            return ShellLaunchSpec(argv=("echo",), cwd=workspace, environment={}, sandboxed=False)

        def fake_run(launch, **kwargs):
            del launch
            kwargs["on_started"]()
            return {"code": "ok", "outcome": "success", "data": {"stdout": "{}", "stderr": "", "exitCode": 0, "termination": "exit"}}

        monkeypatch.setattr(runtime_hooks, "prepare_shell_launch_for_execution", fake_prepare)
        monkeypatch.setattr(runtime_hooks, "run_shell_process", fake_run)

        result = service._execute(binding, "tool:call-1", "{}", threading.Event())
        assert result.status == "completed"
        assert seen_commands == ["echo win-override"]
    finally:
        store.close()


def test_hook_service_skips_windows_only_hook_cleanly_on_posix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, service, binding, _workspace = _create_hook_service(
        tmp_path,
        {"PreToolUse": [{"hooks": [{"type": "command", "commandWindows": "Write-Output only-win"}]}]},
    )
    try:
        monkeypatch.setattr(runtime_hooks, "_hook_host_is_windows", lambda: False)

        def fail_prepare(*_args, **_kwargs):
            raise AssertionError("shell must not be prepared when host platform command is absent")

        monkeypatch.setattr(runtime_hooks, "prepare_shell_launch_for_execution", fail_prepare)

        result = service._execute(binding, "tool:call-win-only", "{}", threading.Event())
        assert result.status == "skipped"
        assert result.error_code == "hook_platform_command_missing"
        latest = HookRepository(store.database).latest("hook-demo")
        assert latest is not None
        assert latest.status == "skipped"
        assert latest.error_code == "hook_platform_command_missing"
    finally:
        store.close()

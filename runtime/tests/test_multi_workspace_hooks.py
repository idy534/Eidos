from __future__ import annotations

from pathlib import Path
import threading
import time

import pytest

from eidos_runtime.application.projects import ProjectApplication
from eidos_runtime.application.sessions import SessionApplication
from eidos_runtime.extensions.skills import SkillCatalog
from eidos_runtime.git import WorktreeManager
from eidos_runtime.persistence.hooks import HookRepository
from eidos_runtime.protocol.methods import SessionCreateRequestDto
from eidos_runtime.runtime.contracts import RuntimeCancelled
from eidos_runtime.runtime.events import RuntimeEvents
from eidos_runtime.runtime.hooks import HookService
from eidos_runtime.runtime.resource_registry import ResourceRegistry
from eidos_runtime.sandbox.sensitive import SensitiveScanner
from test_codex_hooks import _fixture, _command
from test_multi_workspace_execution import _create_project


@pytest.mark.parametrize("kind", ["normal", "timeout", "cancel", "mcp", "incomplete"])
def test_hook_observes_shared_root_changes_and_invalidates_incomplete_scans(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    secondary = tmp_path / "secondary"
    secondary.mkdir()
    target = secondary / "changed.txt"
    script = f"from pathlib import Path\nimport time\nPath({str(target)!r}).write_text('changed')\n"
    if kind in {"timeout", "cancel"}:
        script += "time.sleep(10)\n"
    command = _command()
    command[0]["hooks"][0]["timeout"] = 1 if kind == "timeout" else 5
    event = [{"hooks": [{"type": "mcp_tool", "server": "fixture", "tool": "write", "input": {}}]}] if kind == "mcp" else command
    store, catalog, _, _old_run, primary = _fixture(tmp_path, {"UserPromptSubmit": event}, script)
    cancel = threading.Event()
    worker = None
    resources = ResourceRegistry()
    try:
        manager = WorktreeManager(store.database, managed_root=tmp_path / "managed")
        projects = ProjectApplication(manager.repository)
        project = _create_project(projects, [("primary", primary), ("secondary", secondary)], "primary")
        sessions = SessionApplication(store, scan_text=lambda value: value, worktree_manager=manager)
        session = sessions.create(SessionCreateRequestDto(projectId=project.id)).root
        run, _ = store.create_run(session["id"], "hook", approval_mode="full_access", extension_snapshot=SkillCatalog(catalog).extension_snapshot())
        service = HookService(store, run["id"], resources=resources, sensitive=SensitiveScanner(), events=RuntimeEvents(lambda _: None, store=store))
        if kind == "mcp":
            def write(*_args):
                target.write_text("changed")
                return {"outcome": "success", "code": "ok", "data": {"text": "{}"}}
            service.mcp_execute = write
        if kind == "incomplete":
            from eidos_runtime.sandbox.workspace_index import WorkspaceIndex, WorkspaceIndexIncomplete
            original = WorkspaceIndex.refresh
            def capture(index, *args, **kwargs):
                if index.identity.path == secondary:
                    raise WorkspaceIndexIncomplete()
                return original(index, *args, **kwargs)
            monkeypatch.setattr(WorkspaceIndex, "refresh", capture)
        if kind == "cancel":
            def stop_when_written():
                deadline = time.monotonic() + 5
                while not target.exists() and time.monotonic() < deadline:
                    time.sleep(.01)
                cancel.set()
            worker = threading.Thread(target=stop_when_written)
            worker.start()
        before = store.connection.execute("SELECT workspace_version FROM runs WHERE id=?", (run["id"],)).fetchone()[0]
        try:
            service.dispatch("UserPromptSubmit", "multi-hook", cancel, fields={"prompt": "hook"})
        except RuntimeCancelled:
            assert kind == "cancel"
        result = HookRepository(store.database).latest("hook-demo")
        assert target.read_text() == "changed"
        assert result is not None
        assert result.workspace_observation_complete is (kind != "incomplete")
        if kind != "incomplete":
            assert result.workspace_changed
            assert str(target) in result.workspace_changes
        else:
            assert not result.workspace_changes
        assert store.connection.execute("SELECT workspace_version FROM runs WHERE id=?", (run["id"],)).fetchone()[0] == before + 1
    finally:
        if worker is not None:
            worker.join(timeout=6)
        resources.ensure_empty()
        store.close()


def test_hook_manifest_uses_the_verified_fd_when_a_root_is_replaced(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from eidos_runtime.sandbox.workspace_index import WorkspaceIndex
    from eidos_runtime.sandbox.workspace_manifest import capture_workspace_roots_manifest
    from eidos_runtime.workspace.reader import capture_workspace_identity
    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "inside.txt").write_text("inside")
    (outside / "outside.txt").write_text("outside")
    identity = capture_workspace_identity(root)
    original = WorkspaceIndex.refresh
    def replace_then_scan(index, *args, **kwargs):
        root.rename(tmp_path / "displaced")
        root.symlink_to(outside, target_is_directory=True)
        return original(index, *args, **kwargs)
    monkeypatch.setattr(WorkspaceIndex, "refresh", replace_then_scan)
    result = capture_workspace_roots_manifest((identity,))
    assert not result.complete
    assert not any("outside.txt" in entry.path for entry in result.entries)

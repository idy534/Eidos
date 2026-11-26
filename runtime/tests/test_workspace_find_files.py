from __future__ import annotations

import io
import json
import os
from pathlib import Path
import threading

import pytest

from eidos_runtime.application.workspace import WorkspaceExplorerApplication
from eidos_runtime.db.repositories import SessionRepository
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.git.manager import WorktreeManager
from eidos_runtime.protocol.methods import WorkspaceFindFilesRequestDto
from eidos_runtime.protocol.server import RuntimeServer
from eidos_runtime.workspace.reader import WorkspacePathError, WorkspaceReader


def test_workspace_reader_find_files_searches_recursively_with_fuzzy_and_ignore_rules(
    tmp_path: Path,
) -> None:
    workspace = (tmp_path / "workspace").resolve()
    workspace.mkdir()
    (workspace / ".git").mkdir()
    (workspace / ".git" / "config").write_text("[core]\n", encoding="utf-8")
    (workspace / ".gitignore").write_text("ignored_dir/\n*.log\n", encoding="utf-8")
    (workspace / "ignored_dir").mkdir()
    (workspace / "ignored_dir" / "secret.ts").write_text("export {};\n", encoding="utf-8")
    (workspace / "debug.log").write_text("log\n", encoding="utf-8")
    (workspace / "src" / "components").mkdir(parents=True)
    target = workspace / "src" / "components" / "AppShell.tsx"
    target.write_text("export function AppShell() {}\n", encoding="utf-8")
    (workspace / "src" / "utils" / "helpers.ts").parent.mkdir(parents=True, exist_ok=True)
    (workspace / "src" / "utils" / "helpers.ts").write_text("export const x = 1;\n", encoding="utf-8")

    symlink_path = workspace / "src" / "linked.tsx"
    try:
        os.symlink(target, symlink_path)
    except (OSError, NotImplementedError):
        pass

    with WorkspaceReader(workspace) as reader:
        all_result = reader.find_files("", limit=50)
        paths = [entry.relative_path for entry in all_result.entries]
        assert "src/components/AppShell.tsx" in paths
        assert "src/utils/helpers.ts" in paths
        assert all(p != ".git" and not p.startswith(".git/") for p in paths)
        assert all("ignored_dir" not in p for p in paths)
        assert "debug.log" not in paths
        assert "src/linked.tsx" not in paths

        exact = reader.find_files("AppShell.tsx")
        assert exact.entries[0].relative_path == "src/components/AppShell.tsx"

        fuzzy = reader.find_files("AppShll")
        assert any(entry.relative_path == "src/components/AppShell.tsx" for entry in fuzzy.entries)

        limited = reader.find_files("", limit=2)
        assert len(limited.entries) == 2
        assert limited.truncated is True

        with pytest.raises(WorkspacePathError, match="canceled"):
            canceled = threading.Event()
            canceled.set()
            reader.find_files("App", cancel=canceled)


def test_workspace_find_files_application_and_rpc_dispatch(tmp_path: Path) -> None:
    data = tmp_path / "data"
    workspace = (tmp_path / "workspace").resolve()
    workspace.mkdir()
    (workspace / "nested" / "deep").mkdir(parents=True)
    (workspace / "nested" / "deep" / "TargetFile.py").write_text("print(1)\n", encoding="utf-8")

    store = SessionStore(data)
    store.initialize()
    try:
        session = store.create_session(str(workspace))
        session_id = str(session["id"])
        sessions_repo = SessionRepository(store.database)
        worktree_manager = WorktreeManager(store.database)
        app_service = WorkspaceExplorerApplication(
            sessions_repo, worktree_manager=worktree_manager, scan_text=lambda *_a, **_kw: None
        )
        try:
            response = app_service.find_files(
                WorkspaceFindFilesRequestDto.model_validate({
                    "sessionId": session_id,
                    "query": "targetfile",
                    "limit": 20,
                })
            )
            payload = response.to_json_value()
            assert payload["truncated"] is False
            entries = payload["entries"]
            assert isinstance(entries, list)
            assert any(
                isinstance(item, dict) and item.get("relativePath") == "nested/deep/TargetFile.py"
                for item in entries
            )
        finally:
            app_service.close()
    finally:
        store.close()

    output = io.StringIO()
    # The store creates its private data directory; a pre-made 0755 one is rejected.
    server = RuntimeServer(output, tmp_path / "rpc_data")
    server.store.initialize()
    server.model_config.initialize()
    server.initialized = True
    try:
        session = server.store.create_session(str(workspace))
        server.handle({
            "jsonrpc": "2.0",
            "id": "client-find-1",
            "method": "workspace/findFiles",
            "params": {"sessionId": session["id"], "query": "Target", "limit": 10},
        })
        lines = [json.loads(line) for line in output.getvalue().splitlines() if line.strip()]
        found = next(item for item in lines if item.get("id") == "client-find-1")
        assert found["result"]["entries"][0]["relativePath"] == "nested/deep/TargetFile.py"
    finally:
        server.close()

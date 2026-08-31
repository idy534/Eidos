from __future__ import annotations

from pathlib import Path
import subprocess
import threading

import pytest

from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.application.workspace import WorkspaceExplorerApplication
from eidos_runtime.application.sessions import SessionApplication
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.git import WorktreeManager
from eidos_runtime.protocol.methods import (
    WorkspaceListDirectoryRequestDto,
    WorkspaceReadFilePreviewRequestDto,
    SessionCreateRequestDto,
)
from eidos_runtime.repo_intelligence.watcher import RepositoryChange


def _application(
    tmp_path: Path,
) -> tuple[SessionStore, WorkspaceExplorerApplication, str, Path]:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    session = store.create_session(str(workspace))
    manager = WorktreeManager(
        store.database,
        managed_root=tmp_path / "managed-worktrees",
    )
    application = WorkspaceExplorerApplication(
        store.typed_runtime_repository(),
        worktree_manager=manager,
        scan_text=lambda value: value,
    )
    return store, application, str(session["id"]), workspace


def test_list_directory_is_lazy_sorted_and_supports_unicode_and_spaces(
    tmp_path: Path,
) -> None:
    store, application, session_id, workspace = _application(tmp_path)
    try:
        (workspace / "nested folder").mkdir()
        (workspace / "nested folder" / "child.txt").write_text("child\n", encoding="utf-8")
        (workspace / "说明.md").write_text("# 说明\n", encoding="utf-8")

        root = application.list_directory(
            WorkspaceListDirectoryRequestDto(sessionId=session_id, path=".")
        ).root
        nested = application.list_directory(
            WorkspaceListDirectoryRequestDto(
                sessionId=session_id,
                path="nested folder",
            )
        ).root

        assert root == {
            "path": ".",
            "entries": [
                {
                    "name": "nested folder",
                    "relativePath": "nested folder",
                    "kind": "directory",
                },
                {
                    "name": "说明.md",
                    "relativePath": "说明.md",
                    "kind": "file",
                    "sizeBytes": len("# 说明\n".encode()),
                },
            ],
            "truncated": False,
        }
        assert nested["entries"] == [
            {
                "name": "child.txt",
                "relativePath": "nested folder/child.txt",
                "kind": "file",
                "sizeBytes": 6,
            }
        ]
    finally:
        store.close()


def test_directory_listing_rejects_symlink_escape_and_truncates_large_directory(
    tmp_path: Path,
) -> None:
    store, application, session_id, workspace = _application(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret\n", encoding="utf-8")
    (workspace / "escape").symlink_to(outside, target_is_directory=True)
    for index in range(5):
        (workspace / f"file-{index}.txt").write_text(str(index), encoding="utf-8")
    try:
        result = application.list_directory(
            WorkspaceListDirectoryRequestDto(
                sessionId=session_id,
                path=".",
                limit=3,
            )
        ).root
        assert len(result["entries"]) == 3
        assert result["truncated"] is True
        assert all(entry["name"] != "escape" for entry in result["entries"])

        with pytest.raises(ApplicationError, match="WORKSPACE_BOUNDARY_VIOLATION"):
            application.list_directory(
                WorkspaceListDirectoryRequestDto(
                    sessionId=session_id,
                    path="../outside",
                )
            )
    finally:
        store.close()


def test_text_markdown_and_binary_preview_are_typed_and_bounded(tmp_path: Path) -> None:
    store, application, session_id, workspace = _application(tmp_path)
    try:
        (workspace / "main.py").write_text("print('ok')\n", encoding="utf-8")
        (workspace / "README.md").write_text("# Hello\n", encoding="utf-8")
        (workspace / "binary.dat").write_bytes(b"\x01\x02\x03")

        code = application.read_file_preview(
            WorkspaceReadFilePreviewRequestDto(sessionId=session_id, path="main.py")
        ).root
        markdown = application.read_file_preview(
            WorkspaceReadFilePreviewRequestDto(sessionId=session_id, path="README.md")
        ).root
        binary = application.read_file_preview(
            WorkspaceReadFilePreviewRequestDto(sessionId=session_id, path="binary.dat")
        ).root

        assert code["kind"] == "code"
        assert code["language"] == "python"
        assert code["content"] == "print('ok')\n"
        assert markdown["kind"] == "markdown"
        assert markdown["content"] == "# Hello\n"
        assert binary == {
            "path": "binary.dat",
            "kind": "unavailable",
            "sizeBytes": 3,
            "truncated": False,
            "reason": "binary",
        }
    finally:
        store.close()


def test_preview_distinguishes_missing_file_from_boundary_violation(tmp_path: Path) -> None:
    store, application, session_id, workspace = _application(tmp_path)
    try:
        (workspace / "present.txt").write_text("present\n", encoding="utf-8")

        with pytest.raises(ApplicationError) as missing:
            application.read_file_preview(
                WorkspaceReadFilePreviewRequestDto(
                    sessionId=session_id,
                    path="old/present.txt",
                )
            )
        assert missing.value.code == "WORKSPACE_FILE_NOT_FOUND"

        with pytest.raises(ApplicationError) as missing_leaf:
            application.read_file_preview(
                WorkspaceReadFilePreviewRequestDto(
                    sessionId=session_id,
                    path="missing.txt",
                )
            )
        assert missing_leaf.value.code == "WORKSPACE_FILE_NOT_FOUND"

        with pytest.raises(ApplicationError) as boundary:
            application.read_file_preview(
                WorkspaceReadFilePreviewRequestDto(
                    sessionId=session_id,
                    path="../outside.txt",
                )
            )
        assert boundary.value.code == "WORKSPACE_BOUNDARY_VIOLATION"

        with pytest.raises(ApplicationError) as absolute:
            application.read_file_preview(
                WorkspaceReadFilePreviewRequestDto(
                    sessionId=session_id,
                    path=str(workspace / "present.txt"),
                )
            )
        assert absolute.value.code == "WORKSPACE_BOUNDARY_VIOLATION"
    finally:
        store.close()


def test_listing_starts_existing_watcher_and_emits_relative_invalidation(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    session = store.create_session(str(workspace))
    manager = WorktreeManager(store.database, managed_root=tmp_path / "managed")
    received = threading.Event()
    notifications: list[tuple[str, tuple[RepositoryChange, ...]]] = []

    class FakeWatchController:
        def __init__(self, root: Path) -> None:
            assert root == workspace.resolve()

        def run(self, stop: threading.Event, on_invalidate) -> None:
            on_invalidate((RepositoryChange(path="src/main.py", change="modified"),))
            received.set()
            stop.wait()

    application = WorkspaceExplorerApplication(
        store.typed_runtime_repository(),
        worktree_manager=manager,
        scan_text=lambda value: value,
        on_changes=lambda session_id, changes: notifications.append((session_id, changes)),
        watch_factory=FakeWatchController,
    )
    try:
        application.list_directory(
            WorkspaceListDirectoryRequestDto(sessionId=session["id"], path=".")
        )
        assert received.wait(timeout=1)
        assert notifications == [
            (
                session["id"],
                (RepositoryChange(path="src/main.py", change="modified"),),
            )
        ]
    finally:
        application.close()
        store.close()


def test_managed_session_reads_its_worktree_execution_root(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repository, check=True)
    subprocess.run(
        ["git", "config", "user.name", "Eidos Tests"], cwd=repository, check=True
    )
    subprocess.run(
        ["git", "config", "user.email", "eidos@example.com"],
        cwd=repository,
        check=True,
    )
    (repository / "tracked.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=repository, check=True)
    store = SessionStore(tmp_path / "data")
    store.initialize()
    manager = WorktreeManager(store.database, managed_root=tmp_path / "managed")
    sessions = SessionApplication(
        store,
        scan_text=lambda value: value,
        worktree_manager=manager,
    )
    explorer = WorkspaceExplorerApplication(
        store.typed_runtime_repository(),
        worktree_manager=manager,
        scan_text=lambda value: value,
    )
    try:
        created = sessions.create(
            SessionCreateRequestDto(
                workspaceRoot=str(repository),
                executionMode="worktree",
            )
        ).root
        worktree = Path(created["worktree"]["worktreeRoot"])
        (worktree / "worktree-only.txt").write_text("only here\n", encoding="utf-8")

        listing = explorer.list_directory(
            WorkspaceListDirectoryRequestDto(sessionId=created["id"], path=".")
        ).root

        assert {entry["name"] for entry in listing["entries"]} == {
            "tracked.txt",
            "worktree-only.txt",
        }
        assert not (repository / "worktree-only.txt").exists()
    finally:
        explorer.close()
        store.close()

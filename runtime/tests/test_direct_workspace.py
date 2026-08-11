from __future__ import annotations

from pathlib import Path
import threading

from eidos_runtime.application.checkpoints import CheckpointApplication
from eidos_runtime.application.sessions import SessionApplication
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.git import WorktreeManager
from eidos_runtime.protocol.methods import (
    CheckpointCreateRequestDto,
    CheckpointForkRequestDto,
    SessionCreateRequestDto,
    SessionDeleteRequestDto,
    SessionListRequestDto,
)
from eidos_runtime.tools.runtime_workspace import ToolExecutor


def test_non_git_directory_creates_a_direct_workspace_session(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "report"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    manager = WorktreeManager(
        store.database,
        managed_root=tmp_path / "managed-worktrees",
    )
    application = SessionApplication(
        store,
        scan_text=lambda value: value,
        worktree_manager=manager,
    )

    try:
        created = application.create(
            SessionCreateRequestDto(workspaceRoot=str(workspace))
        ).root

        assert created["project"]["workspaceRoot"] == str(workspace.resolve())
        assert created["project"]["gitAvailable"] is False
        assert created.get("worktree") is None
        session_row = store.connection.execute(
            "SELECT worktree_id FROM sessions WHERE id = ?",
            (created["id"],),
        ).fetchone()
        assert session_row["worktree_id"] is None
    finally:
        store.close()


def _setup(tmp_path: Path) -> tuple[SessionStore, WorktreeManager, SessionApplication]:
    store = SessionStore(tmp_path / "data")
    store.initialize()
    manager = WorktreeManager(
        store.database,
        managed_root=tmp_path / "managed-worktrees",
    )
    return store, manager, SessionApplication(
        store,
        scan_text=lambda value: value,
        worktree_manager=manager,
    )


def test_direct_threads_share_project_identity_and_survive_restart(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "report"
    workspace.mkdir()
    store, _manager, application = _setup(tmp_path)
    try:
        first = application.create(
            SessionCreateRequestDto(workspaceRoot=str(workspace))
        ).root
        second = application.create(
            SessionCreateRequestDto(workspaceRoot=str(workspace))
        ).root
        assert first["project"]["id"] == second["project"]["id"]
        assert first["id"] != second["id"]
        assert store.connection.execute(
            "SELECT COUNT(*) FROM projects WHERE workspace_root = ?",
            (str(workspace.resolve()),),
        ).fetchone()[0] == 1
    finally:
        store.close()

    restarted = SessionStore(tmp_path / "data")
    restarted.initialize()
    manager = WorktreeManager(
        restarted.database,
        managed_root=tmp_path / "managed-worktrees",
    )
    application = SessionApplication(
        restarted,
        scan_text=lambda value: value,
        worktree_manager=manager,
    )
    try:
        sessions = application.list(SessionListRequestDto()).root["items"]
        assert {item["project"]["id"] for item in sessions} == {
            first["project"]["id"]
        }
        assert {item["workspaceRoot"] for item in sessions} == {
            str(workspace.resolve())
        }
        assert all(item.get("worktree") is None for item in sessions)
    finally:
        restarted.close()


def test_direct_run_freezes_filesystem_identity_and_keeps_tools_available(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "report"
    workspace.mkdir()
    store, _manager, application = _setup(tmp_path)
    try:
        session = application.create(
            SessionCreateRequestDto(workspaceRoot=str(workspace))
        ).root
        run, _item = store.create_run(str(session["id"]), "inspect")
        identity = store.workspace_for_run(str(run["id"]))
        assert identity.path == workspace.resolve()
        assert identity.git_dir is None
        assert identity.git_common_dir is None
        with ToolExecutor(identity) as executor:
            (workspace / "input.txt").write_text("hello\n", encoding="utf-8")
            read = executor.execute_read(
                "read_file", "read", {"path": "input.txt"}, threading.Event()
            )
            assert read["data"]["content"] == "hello\n"
            change = executor.prepare_file_change(
                "write_file",
                {"path": "output.txt", "content": "written\n"},
                threading.Event(),
            )
            assert not isinstance(change, dict)
            executor.commit_file_change("write_file", change, threading.Event())
            shell = executor.prepare_shell(".", threading.Event())
            assert shell.path == workspace.resolve()
        assert (workspace / "output.txt").read_text(encoding="utf-8") == "written\n"
    finally:
        store.close()


def test_direct_delete_removes_only_session_data(tmp_path: Path) -> None:
    workspace = tmp_path / "report"
    workspace.mkdir()
    marker = workspace / "keep.txt"
    marker.write_text("keep\n", encoding="utf-8")
    store, _manager, application = _setup(tmp_path)
    try:
        session = application.create(
            SessionCreateRequestDto(workspaceRoot=str(workspace))
        ).root
        application.delete(SessionDeleteRequestDto(sessionId=str(session["id"])))
        assert store.connection.execute(
            "SELECT COUNT(*) FROM sessions WHERE id = ?", (session["id"],)
        ).fetchone()[0] == 0
        assert workspace.is_dir()
        assert marker.read_text(encoding="utf-8") == "keep\n"
    finally:
        store.close()


def test_direct_checkpoint_and_fork_share_workspace_without_git_state(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "report"
    workspace.mkdir()
    store, manager, application = _setup(tmp_path)
    checkpoints = CheckpointApplication(
        store,
        store.checkpoint_repository(),
        worktree_manager=manager,
    )
    try:
        parent = application.create(
            SessionCreateRequestDto(workspaceRoot=str(workspace))
        ).root
        run, _item = store.enqueue_run(str(parent["id"]), "continue")
        checkpoint = checkpoints.create(
            CheckpointCreateRequestDto(runId=str(run["id"]))
        ).checkpoint
        assert checkpoint.git_head is None
        assert checkpoint.workspace_identity_hash

        fork = checkpoints.fork(
            CheckpointForkRequestDto(checkpointId=checkpoint.id)
        )
        parent_projection = store.typed_runtime_repository().read_session_projection(
            str(parent["id"])
        )
        fork_run = store.read_run(fork.run.id)
        fork_projection = store.typed_runtime_repository().read_session_projection(
            str(fork_run["sessionId"])
        )
        assert parent_projection is not None
        assert fork_projection is not None
        assert fork_projection.session.id != parent_projection.session.id
        assert fork_projection.session.worktree_id is None
        assert fork_projection.project.id == parent_projection.project.id
        assert fork_projection.project.workspace_root == str(workspace.resolve())
        assert store.connection.execute(
            "SELECT COUNT(*) FROM checkpoint_actions WHERE checkpoint_id = ?",
            (checkpoint.id,),
        ).fetchone()[0] == 1
    finally:
        store.close()

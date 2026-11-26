from __future__ import annotations

import io
import json
from pathlib import Path
import subprocess

from eidos_runtime.application.checkpoints import CheckpointApplication
from eidos_runtime.application.sessions import SessionApplication
from eidos_runtime.application.worktree_retention import WorktreeRetentionService
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.git import WorktreeManager
from eidos_runtime.protocol.methods import SessionCreateRequestDto, SessionDeleteRequestDto
from eidos_runtime.protocol.server import RuntimeServer


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.name", "Eidos Tests")
    _git(path, "config", "user.email", "eidos-tests@example.com")
    (path / "tracked.txt").write_text("v1\n", encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-qm", "initial")
    return path


def test_checkpoint_capture_and_rewind_restores_git_state(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "repo")
    (repo / "tracked.txt").write_text("dirty-before-run\n", encoding="utf-8")
    (repo / "extra.txt").write_text("keep-me\n", encoding="utf-8")

    store = SessionStore(tmp_path / "data")
    store.initialize()
    try:
        manager = WorktreeManager(store.database, managed_root=tmp_path / "worktrees")
        sessions = SessionApplication(
            store, scan_text=lambda v: v, worktree_manager=manager
        )
        session = sessions.create(
            SessionCreateRequestDto(workspaceRoot=str(repo), executionMode="local")
        )
        run, _user_item = store.create_run(session.id, "edit files")
        retention = WorktreeRetentionService(store.database, manager)
        checkpoints = CheckpointApplication(
            store.database, manager, retention.snapshot_service
        )

        captured = checkpoints.capture_run_checkpoint(str(run["id"]))
        assert captured is not None
        assert captured.git_snapshot_id is not None
        assert captured.git_head == _git(repo, "rev-parse", "HEAD")

        # Simulate post-run edits, new commits, deletions, and untracked junk
        (repo / "tracked.txt").write_text("post-run-change\n", encoding="utf-8")
        (repo / "extra.txt").unlink()
        (repo / "added.txt").write_text("unwanted\n", encoding="utf-8")
        _git(repo, "add", ".")
        _git(repo, "commit", "-qm", "post-run commit")

        listed = checkpoints.list_checkpoints(session.id)
        assert len(listed.checkpoints) == 1
        assert listed.checkpoints[0].id == captured.id

        # Active run blocks rewind; once terminalized, rewind succeeds cleanly.
        try:
            checkpoints.rewind_checkpoint(session.id, captured.id)
            raise AssertionError("expected SESSION_ACTIVE")
        except Exception as error:
            assert getattr(error, "code", None) == "SESSION_ACTIVE"

        with store.database.lock, store.database.connection() as conn:
            conn.execute(
                "UPDATE runs SET status = 'succeeded' WHERE id = ?",
                (str(run["id"]),),
            )

        rewound = checkpoints.rewind_checkpoint(session.id, captured.id)
        assert rewound.checkpoint_id == captured.id
        assert rewound.git_head == captured.git_head
        assert _git(repo, "rev-parse", "HEAD") == captured.git_head
        assert (repo / "tracked.txt").read_text(encoding="utf-8") == "dirty-before-run\n"
        assert (repo / "extra.txt").read_text(encoding="utf-8") == "keep-me\n"
        assert not (repo / "added.txt").exists()

        actions = checkpoints._repository.list_actions(captured.id)
        assert len(actions) == 1
        assert actions[0].action == "rewind"
    finally:
        store.close()


def _snapshot_rows(store: SessionStore) -> list[tuple[str, str | None]]:
    with store.database.lock:
        return [
            (str(row["id"]), row["session_id"])
            for row in store.database.connection().execute(
                "SELECT id, session_id FROM worktree_snapshots ORDER BY id"
            )
        ]


def _checkpointed_local_session(tmp_path: Path, *, release_before_delete: bool):
    repo = _init_repo(tmp_path / "repo")
    store = SessionStore(tmp_path / "data")
    store.initialize()
    manager = WorktreeManager(store.database, managed_root=tmp_path / "worktrees")
    retention = WorktreeRetentionService(store.database, manager)
    checkpoints = CheckpointApplication(store.database, manager, retention.snapshot_service)
    sessions = SessionApplication(
        store,
        scan_text=lambda v: v,
        worktree_manager=manager,
        before_delete=checkpoints.release_for_session if release_before_delete else None,
    )
    session = sessions.create(
        SessionCreateRequestDto(workspaceRoot=str(repo), executionMode="local")
    )
    run, _user_item = store.create_run(session.id, "edit files")
    captured = checkpoints.capture_run_checkpoint(str(run["id"]))
    assert captured is not None and captured.git_snapshot_id is not None
    with store.database.lock, store.database.connection() as conn:
        conn.execute("UPDATE runs SET status = 'succeeded' WHERE id = ?", (str(run["id"]),))
    return repo, store, manager, sessions, session, captured


def test_session_with_checkpoint_deletes_and_releases_its_snapshot(tmp_path: Path) -> None:
    repo, store, manager, sessions, session, captured = _checkpointed_local_session(
        tmp_path, release_before_delete=True
    )
    try:
        assert [row[0] for row in _snapshot_rows(store)] == [captured.git_snapshot_id]

        deleted = sessions.delete(SessionDeleteRequestDto(sessionId=session.id))

        assert deleted.deleted_session_id == session.id
        assert _snapshot_rows(store) == []
        assert tuple(manager.git.list_snapshot_anchors(repo)) == ()
    finally:
        store.close()


def test_session_delete_detaches_snapshot_when_release_did_not_run(tmp_path: Path) -> None:
    _repo, store, _manager, sessions, session, captured = _checkpointed_local_session(
        tmp_path, release_before_delete=False
    )
    try:
        sessions.delete(SessionDeleteRequestDto(sessionId=session.id))

        assert _snapshot_rows(store) == [(captured.git_snapshot_id, None)]
    finally:
        store.close()


def test_checkpoint_skips_non_git_workspace_and_handles_rpc(tmp_path: Path) -> None:
    plain_dir = tmp_path / "plain"
    plain_dir.mkdir()
    (plain_dir / "note.txt").write_text("plain\n", encoding="utf-8")

    output = io.StringIO()
    server = RuntimeServer(output, tmp_path / "rpc_data")
    server.store.initialize()
    server.model_config.initialize()
    server.worktree_manager = WorktreeManager(server.store.database)
    server.worktree_retention = WorktreeRetentionService(
        server.store.database, server.worktree_manager
    )
    server.initialized = True
    try:
        session = server.store.create_session(str(plain_dir))
        run, _user_item = server.store.create_run(session["id"], "hello")
        apps = server._applications_or_error()
        assert apps.checkpoints.capture_run_checkpoint(str(run["id"])) is None

        server.handle({
            "jsonrpc": "2.0",
            "id": "client-chk-1",
            "method": "checkpoint/list",
            "params": {"sessionId": session["id"]},
        })
        responses = [json.loads(line) for line in output.getvalue().splitlines() if line.strip()]
        assert responses[-1]["id"] == "client-chk-1"
        assert responses[-1]["result"]["checkpoints"] == []
    finally:
        server.store.close()

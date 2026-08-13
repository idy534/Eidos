from __future__ import annotations

from pathlib import Path
import subprocess
import threading
from uuid import uuid4

from eidos_runtime.application.checkpoints import CheckpointApplication
from eidos_runtime.application.sessions import SessionApplication
from eidos_runtime.application.workspace import WorkspaceExplorerApplication
from eidos_runtime.application.worktree_retention import WorktreeRetentionService
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.git import WorktreeManager
from eidos_runtime.protocol.methods import (
    CheckpointCreateRequestDto,
    CheckpointForkRequestDto,
    CheckpointRewindRequestDto,
    SessionCreateBranchRequestDto,
    SessionCreateRequestDto,
    SessionGitCommitRequestDto,
    SessionGitDiffRequestDto,
    SessionGitFetchRequestDto,
    SessionGitMergeAbortRequestDto,
    SessionGitMergeRequestDto,
    SessionGitPullRequestDto,
    SessionGitPushRequestDto,
    SessionGitRebaseAbortRequestDto,
    SessionGitRebaseRequestDto,
    SessionGitStageRequestDto,
    SessionGitStatusRequestDto,
    WorkspaceListDirectoryRequestDto,
    WorkspaceReadFilePreviewRequestDto,
)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _remote_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    remote = tmp_path / "remote.git"
    remote.mkdir()
    _git(remote, "init", "--bare", "-q", "--initial-branch=main")
    source = tmp_path / "source"
    source.mkdir()
    _git(source, "init", "-q", "-b", "main")
    _git(source, "config", "user.name", "Eidos Tests")
    _git(source, "config", "user.email", "eidos-tests@example.com")
    (source / "README.md").write_text("base\n", encoding="utf-8")
    _git(source, "add", "README.md")
    _git(source, "commit", "-qm", "initial")
    _git(source, "remote", "add", "origin", str(remote))
    _git(source, "push", "-qu", "origin", "main")
    peer = tmp_path / "peer"
    _git(tmp_path, "clone", "-q", str(remote), str(peer))
    _git(peer, "config", "user.name", "Eidos Tests")
    _git(peer, "config", "user.email", "eidos-tests@example.com")
    return remote, source, peer


def test_complete_git_development_workflow_converges_across_boundaries(
    tmp_path: Path,
) -> None:
    _remote, source, peer = _remote_fixture(tmp_path)
    store = SessionStore(tmp_path / "data")
    store.initialize()
    manager = WorktreeManager(
        store.database,
        managed_root=tmp_path / "managed-worktrees",
    )
    sessions = SessionApplication(
        store,
        scan_text=lambda value: value,
        worktree_manager=manager,
    )
    retention = WorktreeRetentionService(store.database, manager)
    checkpoints = CheckpointApplication(
        store,
        store.checkpoint_repository(),
        worktree_manager=manager,
        retention=retention,
    )
    explorer = WorkspaceExplorerApplication(
        store.typed_runtime_repository(),
        worktree_manager=manager,
        scan_text=lambda value: value,
    )
    try:
        session = sessions.create(
            SessionCreateRequestDto(
                workspaceRoot=str(source),
                executionMode="worktree",
            )
        ).root
        session_id = str(session["id"])
        root = Path(session["worktree"]["worktreeRoot"])
        baseline = str(session["worktree"]["baseCommit"])
        sessions.create_branch(
            SessionCreateBranchRequestDto(
                sessionId=session_id,
                branch="feature/integration",
            )
        )

        (root / "integration.txt").write_text("first\n", encoding="utf-8")
        listing = explorer.list_directory(
            WorkspaceListDirectoryRequestDto(
                sessionId=session_id,
                path=".",
            )
        ).root
        preview = explorer.read_file_preview(
            WorkspaceReadFilePreviewRequestDto(
                sessionId=session_id,
                path="integration.txt",
            )
        ).root
        assert any(
            entry["relativePath"] == "integration.txt" for entry in listing["entries"]
        )
        assert preview["content"] == "first\n"

        status = sessions.git_status(
            SessionGitStatusRequestDto(sessionId=session_id)
        ).root
        file_diff = sessions.git_diff(
            SessionGitDiffRequestDto(
                sessionId=session_id,
                scope="head",
                path="integration.txt",
            )
        ).root
        assert status["untrackedFiles"] == ["integration.txt"]
        assert file_diff["changedFiles"] == ["integration.txt"]
        sessions.git_stage(
            SessionGitStageRequestDto(
                sessionId=session_id,
                paths=["integration.txt"],
            )
        )
        committed = sessions.git_commit(
            SessionGitCommitRequestDto(
                sessionId=session_id,
                message="integration change",
            )
        ).root
        assert committed["commit"] == _git(root, "rev-parse", "HEAD")
        baseline_diff = sessions.git_diff(
            SessionGitDiffRequestDto(
                sessionId=session_id,
                scope="baseline",
            )
        ).root
        assert baseline_diff["baseCommit"] == baseline
        assert baseline_diff["changedFiles"] == ["integration.txt"]

        pushed = (
            sessions.prepare_git_push(
                SessionGitPushRequestDto(
                    operationId=str(uuid4()),
                    sessionId=session_id,
                    remote="origin",
                ),
                request_id="integration-push",
            )
            .run(threading.Event())
            .root
        )
        assert pushed["ahead"] == 0 and pushed["behind"] == 0
        _git(peer, "fetch", "-q", "origin")
        _git(
            peer,
            "switch",
            "-q",
            "-c",
            "feature/integration",
            "origin/feature/integration",
        )
        (peer / "remote.txt").write_text("remote\n", encoding="utf-8")
        _git(peer, "add", "remote.txt")
        _git(peer, "commit", "-qm", "remote change")
        _git(peer, "push", "-q", "origin", "feature/integration")

        head_before_fetch = _git(root, "rev-parse", "HEAD")
        index_before_fetch = _git(root, "write-tree")
        fetched = (
            sessions.prepare_git_fetch(
                SessionGitFetchRequestDto(
                    operationId=str(uuid4()),
                    sessionId=session_id,
                ),
                request_id="integration-fetch",
            )
            .run(threading.Event())
            .root
        )
        assert fetched["behind"] == 1
        assert _git(root, "rev-parse", "HEAD") == head_before_fetch
        assert _git(root, "write-tree") == index_before_fetch
        assert not (root / "remote.txt").exists()
        pulled = (
            sessions.prepare_git_pull(
                SessionGitPullRequestDto(
                    operationId=str(uuid4()),
                    sessionId=session_id,
                ),
                request_id="integration-pull",
            )
            .run(threading.Event())
            .root
        )
        assert pulled["behind"] == 0
        assert pulled["status"]["baseCommit"] == baseline
        assert (root / "remote.txt").read_text(encoding="utf-8") == "remote\n"

        _git(peer, "switch", "-qc", "conflict-target")
        (peer / "README.md").write_text("target\n", encoding="utf-8")
        _git(peer, "commit", "-qam", "target conflict")
        _git(peer, "push", "-q", "origin", "conflict-target")
        (root / "README.md").write_text("local\n", encoding="utf-8")
        _git(root, "commit", "-qam", "local conflict")
        sessions.prepare_git_fetch(
            SessionGitFetchRequestDto(
                operationId=str(uuid4()),
                sessionId=session_id,
            ),
            request_id="integration-fetch-conflict",
        ).run(threading.Event())
        merged = sessions.git_merge(
            SessionGitMergeRequestDto(
                operationId=str(uuid4()),
                sessionId=session_id,
                target="origin/conflict-target",
            )
        ).root
        assert merged["operationState"] == "merge"
        assert merged["conflictFiles"] == ["README.md"]
        sessions.git_merge_abort(
            SessionGitMergeAbortRequestDto(
                operationId=str(uuid4()),
                sessionId=session_id,
            )
        )
        rebased = sessions.git_rebase(
            SessionGitRebaseRequestDto(
                operationId=str(uuid4()),
                sessionId=session_id,
                target="origin/conflict-target",
            )
        ).root
        assert rebased["operationState"] == "rebase"
        assert rebased["conflictFiles"] == ["README.md"]
        sessions.git_rebase_abort(
            SessionGitRebaseAbortRequestDto(
                operationId=str(uuid4()),
                sessionId=session_id,
            )
        )

        (root / "integration.txt").write_text("staged\n", encoding="utf-8")
        _git(root, "add", "integration.txt")
        (root / "integration.txt").write_text("staged\nunstaged\n", encoding="utf-8")
        (root / "checkpoint.txt").write_text("checkpoint\n", encoding="utf-8")
        run, _item = store.enqueue_run(session_id, "checkpoint")
        checkpoint = checkpoints.create(
            CheckpointCreateRequestDto(
                operationId=str(uuid4()),
                runId=str(run["id"]),
            )
        ).checkpoint
        store.connection.execute(
            "UPDATE runs SET status = 'succeeded' WHERE id = ?", (run["id"],)
        )
        (root / "integration.txt").write_text("later\n", encoding="utf-8")
        (root / "checkpoint.txt").unlink()
        checkpoints.rewind(
            CheckpointRewindRequestDto(
                operationId=str(uuid4()),
                checkpointId=checkpoint.id,
            )
        )
        fork = checkpoints.fork(
            CheckpointForkRequestDto(
                operationId=str(uuid4()),
                checkpointId=checkpoint.id,
            )
        )

        assert _git(root, "show", ":integration.txt") == "staged"
        assert (root / "integration.txt").read_text(encoding="utf-8") == (
            "staged\nunstaged\n"
        )
        assert (root / "checkpoint.txt").read_text(encoding="utf-8") == ("checkpoint\n")
        fork_projection = store.typed_runtime_repository().read_session_projection(
            fork.run.session_id
        )
        assert fork_projection is not None and fork_projection.worktree is not None
        fork_root = Path(fork_projection.worktree.worktree_root)
        assert _git(fork_root, "show", ":integration.txt") == "staged"
        assert (fork_root / "integration.txt").read_text(encoding="utf-8") == (
            "staged\nunstaged\n"
        )
        assert (fork_root / "checkpoint.txt").read_text(encoding="utf-8") == (
            "checkpoint\n"
        )
    finally:
        store.close()

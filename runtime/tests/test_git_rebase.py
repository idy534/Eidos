from __future__ import annotations

from pathlib import Path
import subprocess
import uuid

import pytest

from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.application.sessions import SessionApplication
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.git import DulwichGitBackend, WorktreeManager
from eidos_runtime.git.native import GitCli, HardenedGitRunner
from eidos_runtime.protocol.methods import (
    SessionCreateBranchRequestDto,
    SessionCreateRequestDto,
    SessionGitRebaseAbortRequestDto,
    SessionGitRebaseContinueRequestDto,
    SessionGitRebaseRequestDto,
    SessionGitStageRequestDto,
)


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )
    return completed.stdout.strip()


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-q", "-b", "main")
    _git(repository, "config", "user.name", "Eidos Tests")
    _git(repository, "config", "user.email", "eidos-tests@example.com")
    (repository / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git(repository, "add", "--", "tracked.txt")
    _git(repository, "commit", "-qm", "initial")
    return repository


def _commit(repository: Path, path: str, content: str, message: str) -> str:
    (repository / path).write_text(content, encoding="utf-8")
    _git(repository, "add", "--", path)
    _git(repository, "commit", "-qm", message)
    return _git(repository, "rev-parse", "HEAD")


def _application(
    tmp_path: Path,
) -> tuple[SessionStore, WorktreeManager, SessionApplication]:
    store = SessionStore(tmp_path / "data")
    store.initialize()
    manager = WorktreeManager(
        store.database, managed_root=tmp_path / "managed-worktrees"
    )
    return store, manager, SessionApplication(
        store, scan_text=lambda value: value, worktree_manager=manager
    )


def _local_session(application: SessionApplication, repository: Path) -> str:
    session = application.create(
        SessionCreateRequestDto(
            workspaceRoot=str(repository), executionMode="local"
        )
    ).root
    return str(session["id"])


def test_rebase_replays_multiple_commits_and_preserves_messages(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _git(repository, "switch", "-qc", "feature")
    first = _commit(repository, "one.txt", "one\n", "feature one")
    second = _commit(repository, "two.txt", "two\n", "feature two")
    _git(repository, "switch", "-q", "main")
    main_head = _commit(repository, "main.txt", "main\n", "main advance")
    _git(repository, "switch", "-q", "feature")
    hook_marker = tmp_path / "post-rewrite-ran"
    hook = repository / ".git" / "hooks" / "post-rewrite"
    hook.write_text(f"#!/bin/sh\ntouch '{hook_marker}'\n", encoding="utf-8")
    hook.chmod(0o755)
    store, _manager, application = _application(tmp_path)
    try:
        session_id = _local_session(application, repository)

        operation_id = str(uuid.uuid4())
        request = SessionGitRebaseRequestDto(
            operationId=operation_id,
            sessionId=session_id,
            target="main",
        )
        rebased = application.git_rebase(request).root

        assert rebased["operationState"] == "none"
        assert rebased["conflictFiles"] == []
        assert rebased["branch"] == "feature"
        assert rebased["head"] not in {first, second}
        assert _git(repository, "rev-list", "--count", f"{main_head}..HEAD") == "2"
        assert _git(repository, "log", "-2", "--format=%s").splitlines() == [
            "feature two",
            "feature one",
        ]
        assert _git(repository, "status", "--porcelain") == ""
        assert not hook_marker.exists()
        assert application.git_rebase(request).root == rebased
        assert _git(repository, "rev-parse", "HEAD") == rebased["head"]
        with pytest.raises(ApplicationError) as reused:
            application.git_rebase(
                SessionGitRebaseRequestDto(
                    operationId=operation_id,
                    sessionId=session_id,
                    target="feature",
                )
            )
        assert reused.value.code == "OPERATION_ID_REUSED"
    finally:
        store.close()


def test_rebase_conflict_can_be_staged_and_continued_without_editor(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    _git(repository, "switch", "-qc", "feature")
    _commit(repository, "tracked.txt", "feature\n", "feature change")
    _git(repository, "switch", "-q", "main")
    _commit(repository, "tracked.txt", "main\n", "main change")
    _git(repository, "switch", "-q", "feature")
    store, _manager, application = _application(tmp_path)
    try:
        session_id = _local_session(application, repository)
        request = SessionGitRebaseRequestDto(
            operationId=str(uuid.uuid4()),
            sessionId=session_id,
            target="main",
        )

        conflict = application.git_rebase(request).root

        assert conflict["operationState"] == "rebase"
        assert conflict["conflictFiles"] == ["tracked.txt"]
        assert application.git_rebase(request).root == conflict

        (repository / "tracked.txt").write_text("resolved\n", encoding="utf-8")
        application.git_stage(
            SessionGitStageRequestDto(
                operationId=str(uuid.uuid4()),
                sessionId=session_id,
                paths=["tracked.txt"],
            )
        )
        continue_request = SessionGitRebaseContinueRequestDto(
            operationId=str(uuid.uuid4()), sessionId=session_id
        )
        continued = application.git_rebase_continue(continue_request).root

        assert continued["operationState"] == "none"
        assert continued["conflictFiles"] == []
        assert continued["branch"] == "feature"
        assert _git(repository, "log", "-1", "--format=%s") == "feature change"
        assert (repository / "tracked.txt").read_text(encoding="utf-8") == "resolved\n"
        assert application.git_rebase_continue(continue_request).root == continued
    finally:
        store.close()


def test_rebase_abort_restores_original_head_branch_and_status(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _git(repository, "switch", "-qc", "feature")
    original_head = _commit(repository, "tracked.txt", "feature\n", "feature")
    _git(repository, "switch", "-q", "main")
    _commit(repository, "tracked.txt", "main\n", "main")
    _git(repository, "switch", "-q", "feature")
    store, _manager, application = _application(tmp_path)
    try:
        session_id = _local_session(application, repository)
        application.git_rebase(
            SessionGitRebaseRequestDto(
                operationId=str(uuid.uuid4()),
                sessionId=session_id,
                target="main",
            )
        )
        abort_request = SessionGitRebaseAbortRequestDto(
            operationId=str(uuid.uuid4()), sessionId=session_id
        )

        aborted = application.git_rebase_abort(abort_request).root

        assert aborted["head"] == original_head
        assert aborted["branch"] == "feature"
        assert aborted["operationState"] == "none"
        assert aborted["status"]["dirty"] is False
        assert _git(repository, "status", "--porcelain") == ""
        assert application.git_rebase_abort(abort_request).root == aborted
    finally:
        store.close()


def test_rebase_preflight_rejects_active_run_and_uncertain_operation(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    _git(repository, "switch", "-qc", "feature")
    _commit(repository, "feature.txt", "feature\n", "feature")
    _git(repository, "switch", "-q", "main")
    _commit(repository, "main.txt", "main\n", "main")
    _git(repository, "switch", "-q", "feature")
    store, _manager, application = _application(tmp_path)
    try:
        session_id = _local_session(application, repository)
        store.create_run(session_id, "active")
        operation_id = str(uuid.uuid4())
        with pytest.raises(ApplicationError) as busy:
            application.git_rebase(
                SessionGitRebaseRequestDto(
                    operationId=operation_id,
                    sessionId=session_id,
                    target="main",
                )
            )
        assert busy.value.code == "GIT_WORKFLOW_BUSY"
        connection = store.connection
        assert connection is not None
        assert connection.execute(
            "SELECT COUNT(*) FROM operations WHERE id = ?", (operation_id,)
        ).fetchone()[0] == 0
    finally:
        store.close()

    uncertain_root = tmp_path / "uncertain"
    uncertain_root.mkdir()
    repository = _repository(uncertain_root)
    store, _manager, application = _application(uncertain_root)
    try:
        session_id = _local_session(application, repository)
        operation_id = str(uuid.uuid4())
        request = {"sessionId": session_id, "target": "main"}
        store.prepare_operation(operation_id, "session/gitRebase", request)
        head = _git(repository, "rev-parse", "HEAD")

        with pytest.raises(ApplicationError) as uncertain:
            application.git_rebase(
                SessionGitRebaseRequestDto(
                    operationId=operation_id,
                    sessionId=session_id,
                    target="main",
                )
            )
        assert uncertain.value.code == "OPERATION_IN_PROGRESS"
        assert _git(repository, "rev-parse", "HEAD") == head
    finally:
        store.close()


def test_managed_rebase_preserves_task_baseline(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    store, _manager, application = _application(tmp_path)
    try:
        session = application.create(
            SessionCreateRequestDto(
                workspaceRoot=str(repository), executionMode="worktree"
            )
        ).root
        root = Path(session["worktree"]["worktreeRoot"])
        baseline = session["worktree"]["baseCommit"]
        application.create_branch(
            SessionCreateBranchRequestDto(
                sessionId=session["id"], branch="feature/rebase"
            )
        )
        _commit(root, "feature.txt", "feature\n", "feature")
        _commit(repository, "main.txt", "main\n", "main")

        rebased = application.git_rebase(
            SessionGitRebaseRequestDto(
                operationId=str(uuid.uuid4()),
                sessionId=session["id"],
                target="main",
            )
        ).root

        assert rebased["operationState"] == "none"
        assert rebased["status"]["baseCommit"] == baseline
        assert rebased["branch"] == "feature/rebase"
    finally:
        store.close()


def test_rebase_requires_branch_clean_workspace_valid_target_and_identity(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    _git(repository, "switch", "-qc", "feature")
    _commit(repository, "feature.txt", "feature\n", "feature")
    _git(repository, "switch", "-q", "main")
    _commit(repository, "main.txt", "main\n", "main")
    _git(repository, "switch", "-q", "feature")
    store, manager, application = _application(tmp_path)
    try:
        session_id = _local_session(application, repository)
        (repository / "tracked.txt").write_text("dirty\n", encoding="utf-8")
        with pytest.raises(ApplicationError) as dirty:
            application.git_rebase(
                SessionGitRebaseRequestDto(
                    operationId=str(uuid.uuid4()),
                    sessionId=session_id,
                    target="main",
                )
            )
        assert dirty.value.code == "GIT_WORKTREE_DIRTY"
        _git(repository, "restore", "--", "tracked.txt")

        with pytest.raises(ApplicationError) as target:
            application.git_rebase(
                SessionGitRebaseRequestDto(
                    operationId=str(uuid.uuid4()),
                    sessionId=session_id,
                    target="missing-target",
                )
            )
        assert target.value.code == "GIT_REBASE_TARGET_INVALID"

        _git(repository, "checkout", "--detach", "-q")
        with pytest.raises(ApplicationError) as branch:
            application.git_rebase(
                SessionGitRebaseRequestDto(
                    operationId=str(uuid.uuid4()),
                    sessionId=session_id,
                    target="main",
                )
            )
        assert branch.value.code == "GIT_BRANCH_REQUIRED"

        _git(repository, "switch", "-q", "feature")
        _git(repository, "config", "--unset", "user.name")
        _git(repository, "config", "--unset", "user.email")
        empty_home = tmp_path / "empty-home"
        empty_home.mkdir()
        manager.git = DulwichGitBackend(
            git_cli=GitCli(runner=HardenedGitRunner(user_home=empty_home))
        )
        with pytest.raises(ApplicationError) as identity:
            application.git_rebase(
                SessionGitRebaseRequestDto(
                    operationId=str(uuid.uuid4()),
                    sessionId=session_id,
                    target="main",
                )
            )
        assert identity.value.code == "GIT_IDENTITY_UNAVAILABLE"
    finally:
        store.close()

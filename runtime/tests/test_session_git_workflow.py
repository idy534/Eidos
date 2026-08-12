from __future__ import annotations

from pathlib import Path
import subprocess

import pytest
from pydantic import ValidationError

from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.application.sessions import SessionApplication
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.git import DulwichGitBackend, WorktreeManager
from eidos_runtime.git.errors import GitCommandTimeoutError
from eidos_runtime.git.native import GitCli, HardenedGitRunner
from eidos_runtime.protocol.methods import (
    SessionCreateBranchRequestDto,
    SessionCreateRequestDto,
    SessionGitCommitRequestDto,
    SessionGitDiffRequestDto,
    SessionGitStageRequestDto,
    SessionGitStatusRequestDto,
    SessionGitUnstageRequestDto,
)


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-q", "-b", "main")
    _git(repository, "config", "user.name", "Eidos Tests")
    _git(repository, "config", "user.email", "eidos-tests@example.com")
    (repository / "tracked.txt").write_text("base\n", encoding="utf-8")
    (repository / "other.txt").write_text("other\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "initial")
    return repository


def _application(
    tmp_path: Path,
) -> tuple[SessionStore, WorktreeManager, SessionApplication]:
    store = SessionStore(tmp_path / "data")
    store.initialize()
    manager = WorktreeManager(
        store.database,
        managed_root=tmp_path / "managed-worktrees",
    )
    return (
        store,
        manager,
        SessionApplication(
            store,
            scan_text=lambda value: value,
            worktree_manager=manager,
        ),
    )


def _create_session(
    application: SessionApplication,
    repository: Path,
    *,
    execution_mode: str,
) -> dict[str, object]:
    return application.create(
        SessionCreateRequestDto(
            workspaceRoot=str(repository),
            executionMode=execution_mode,
        )
    ).root


def test_structured_status_and_file_diff_are_runtime_owned(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    store, _manager, application = _application(tmp_path)
    try:
        session = _create_session(application, repository, execution_mode="local")
        (repository / "tracked.txt").write_text("staged\n", encoding="utf-8")
        _git(repository, "add", "tracked.txt")
        (repository / "tracked.txt").write_text("unstaged\n", encoding="utf-8")
        (repository / "new file.txt").write_text("untracked\n", encoding="utf-8")

        status = application.git_status(
            SessionGitStatusRequestDto(sessionId=session["id"])
        ).root
        diff = application.git_diff(
            SessionGitDiffRequestDto(
                sessionId=session["id"], scope="head", path="new file.txt"
            )
        ).root

        assert status["stagedFiles"] == ["tracked.txt"]
        assert status["unstagedFiles"] == ["tracked.txt"]
        assert status["untrackedFiles"] == ["new file.txt"]
        assert status["conflictFiles"] == []
        assert status["stagedCount"] == len(status["stagedFiles"])
        assert diff["changedFiles"] == ["new file.txt"]
        assert "untracked" in diff["unifiedDiff"]
        assert "tracked.txt" not in diff["unifiedDiff"]
    finally:
        store.close()


def test_local_stage_unstage_and_commit_return_refreshed_status(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    store, _manager, application = _application(tmp_path)
    try:
        session = _create_session(application, repository, execution_mode="local")
        original_head = _git(repository, "rev-parse", "HEAD")
        (repository / "tracked.txt").write_text("staged\n", encoding="utf-8")
        (repository / "new file.txt").write_text("new\n", encoding="utf-8")

        staged = application.git_stage(
            SessionGitStageRequestDto(
                sessionId=session["id"], paths=["tracked.txt", "new file.txt"]
            )
        ).root
        assert staged["head"] == original_head
        assert staged["branch"] == "main"
        assert staged["status"]["stagedFiles"] == ["new file.txt", "tracked.txt"]

        unstaged = application.git_unstage(
            SessionGitUnstageRequestDto(
                sessionId=session["id"], paths=["new file.txt"]
            )
        ).root
        assert unstaged["status"]["untrackedFiles"] == ["new file.txt"]

        committed = application.git_commit(
            SessionGitCommitRequestDto(
                sessionId=session["id"], message="commit tracked"
            )
        ).root
        assert committed["commit"] != original_head
        assert committed["commit"] == committed["head"]
        assert committed["status"]["stagedFiles"] == []
        assert committed["status"]["untrackedFiles"] == ["new file.txt"]
    finally:
        store.close()


def test_managed_detached_commit_is_rejected_then_attached_commit_succeeds(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    store, _manager, application = _application(tmp_path)
    try:
        session = _create_session(application, repository, execution_mode="worktree")
        worktree_root = Path(session["worktree"]["worktreeRoot"])
        (worktree_root / "tracked.txt").write_text("managed\n", encoding="utf-8")
        application.git_stage(
            SessionGitStageRequestDto(
                sessionId=session["id"], paths=["tracked.txt"]
            )
        )
        with pytest.raises(ApplicationError) as detached:
            application.git_commit(
                SessionGitCommitRequestDto(
                    sessionId=session["id"], message="must attach first"
                )
            )
        assert detached.value.code == "GIT_BRANCH_REQUIRED"

        attached = _create_session(application, repository, execution_mode="worktree")
        attached_root = Path(attached["worktree"]["worktreeRoot"])
        application.create_branch(
            SessionCreateBranchRequestDto(
                sessionId=attached["id"], branch="feature/local-workflow"
            )
        )
        (attached_root / "tracked.txt").write_text("attached\n", encoding="utf-8")
        application.git_stage(
            SessionGitStageRequestDto(
                sessionId=attached["id"], paths=["tracked.txt"]
            )
        )
        result = application.git_commit(
            SessionGitCommitRequestDto(
                sessionId=attached["id"], message="attached commit"
            )
        ).root
        assert result["branch"] == "feature/local-workflow"
        assert result["commit"] == _git(attached_root, "rev-parse", "HEAD")
    finally:
        store.close()


def test_baseline_diff_survives_a_new_commit(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    store, _manager, application = _application(tmp_path)
    try:
        session = _create_session(application, repository, execution_mode="worktree")
        worktree_root = Path(session["worktree"]["worktreeRoot"])
        baseline = session["worktree"]["baseCommit"]
        application.create_branch(
            SessionCreateBranchRequestDto(
                sessionId=session["id"], branch="feature/baseline"
            )
        )
        (worktree_root / "tracked.txt").write_text("committed\n", encoding="utf-8")
        application.git_stage(
            SessionGitStageRequestDto(
                sessionId=session["id"], paths=["tracked.txt"]
            )
        )
        application.git_commit(
            SessionGitCommitRequestDto(
                sessionId=session["id"], message="new head"
            )
        )

        head_diff = application.git_diff(
            SessionGitDiffRequestDto(sessionId=session["id"], scope="head")
        ).root
        baseline_diff = application.git_diff(
            SessionGitDiffRequestDto(sessionId=session["id"], scope="baseline")
        ).root

        assert head_diff["changedFiles"] == []
        assert baseline_diff["baseCommit"] == baseline
        assert baseline_diff["changedFiles"] == ["tracked.txt"]
        assert "committed" in baseline_diff["unifiedDiff"]
    finally:
        store.close()


def test_git_mutations_reject_active_run_invalid_paths_and_empty_selection(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    store, _manager, application = _application(tmp_path)
    try:
        session = _create_session(application, repository, execution_mode="local")
        (repository / "tracked.txt").write_text("changed\n", encoding="utf-8")
        store.create_run(session["id"], "active")
        with pytest.raises(ApplicationError) as busy:
            application.git_stage(
                SessionGitStageRequestDto(
                    sessionId=session["id"], paths=["tracked.txt"]
                )
            )
        assert busy.value.code == "GIT_WORKFLOW_BUSY"
    finally:
        store.close()

    with pytest.raises(ValidationError):
        SessionGitStageRequestDto(sessionId=session["id"], paths=[])
    with pytest.raises(ValidationError):
        SessionGitStageRequestDto(sessionId=session["id"], paths=["../outside"])
    with pytest.raises(ValidationError):
        SessionGitUnstageRequestDto(sessionId=session["id"], paths=["/outside"])


def test_git_workflow_errors_are_stable(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    store, _manager, application = _application(tmp_path)
    try:
        session = _create_session(application, repository, execution_mode="local")
        with pytest.raises(ApplicationError) as empty:
            application.git_commit(
                SessionGitCommitRequestDto(sessionId=session["id"], message="empty")
            )
        assert empty.value.code == "GIT_NOTHING_STAGED"

        non_git = tmp_path / "not-git"
        non_git.mkdir()
        direct = store.create_session(str(non_git))
        with pytest.raises(ApplicationError) as unavailable:
            application.git_status(
                SessionGitStatusRequestDto(sessionId=direct["id"])
            )
        assert unavailable.value.code == "GIT_NOT_REPOSITORY"
    finally:
        store.close()


def test_commit_identity_conflict_and_timeout_map_to_stable_codes(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    store, manager, application = _application(tmp_path)
    try:
        session = _create_session(application, repository, execution_mode="local")
        _git(repository, "config", "--unset", "user.name")
        _git(repository, "config", "--unset", "user.email")
        empty_home = tmp_path / "empty-home"
        empty_home.mkdir()
        manager.git = DulwichGitBackend(
            git_cli=GitCli(runner=HardenedGitRunner(user_home=empty_home))
        )
        (repository / "tracked.txt").write_text("identity\n", encoding="utf-8")
        application.git_stage(
            SessionGitStageRequestDto(
                sessionId=session["id"], paths=["tracked.txt"]
            )
        )
        with pytest.raises(ApplicationError) as identity:
            application.git_commit(
                SessionGitCommitRequestDto(
                    sessionId=session["id"], message="identity missing"
                )
            )
        assert identity.value.code == "GIT_IDENTITY_UNAVAILABLE"

        original_stage = manager.git.stage

        def timeout(_cwd: Path, _paths: tuple[str, ...]) -> object:
            raise GitCommandTimeoutError("stage")

        manager.git.stage = timeout  # type: ignore[method-assign]
        with pytest.raises(ApplicationError) as timed_out:
            application.git_stage(
                SessionGitStageRequestDto(
                    sessionId=session["id"], paths=["tracked.txt"]
                )
            )
        assert timed_out.value.code == "GIT_COMMAND_TIMEOUT"
        manager.git.stage = original_stage  # type: ignore[method-assign]
    finally:
        store.close()

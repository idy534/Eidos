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
    SessionGitMergeAbortRequestDto,
    SessionGitMergeRequestDto,
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


def _session(application: SessionApplication, repository: Path) -> str:
    result = application.create(
        SessionCreateRequestDto(
            workspaceRoot=str(repository), executionMode="local"
        )
    ).root
    return str(result["id"])


def test_native_merge_fast_forwards_and_creates_merge_commit(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    backend = DulwichGitBackend()
    _git(repository, "switch", "-qc", "feature")
    feature_head = _commit(repository, "feature.txt", "feature\n", "feature")
    _git(repository, "switch", "-q", "main")

    backend.merge(repository, "feature")

    assert backend.head(repository) == feature_head
    assert backend.status(repository).dirty is False

    _git(repository, "switch", "-qc", "topic")
    _commit(repository, "topic.txt", "topic\n", "topic")
    _git(repository, "switch", "-q", "main")
    before = _commit(repository, "main.txt", "main\n", "main")
    hook_marker = tmp_path / "merge-hook-ran"
    hook = repository / ".git" / "hooks" / "pre-merge-commit"
    hook.write_text(f"#!/bin/sh\ntouch '{hook_marker}'\n", encoding="utf-8")
    hook.chmod(0o755)

    backend.merge(repository, "topic")

    merge_head = backend.head(repository)
    assert merge_head != before
    assert len(_git(repository, "show", "-s", "--format=%P", merge_head).split()) == 2
    assert backend.status(repository).dirty is False
    assert not hook_marker.exists()


def test_merge_conflict_is_typed_and_abort_restores_head_and_status(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    store, _manager, application = _application(tmp_path)
    try:
        session_id = _session(application, repository)
        original_head = _git(repository, "rev-parse", "HEAD")
        _git(repository, "switch", "-qc", "topic")
        _commit(repository, "tracked.txt", "topic\n", "topic")
        _git(repository, "switch", "-q", "main")
        _commit(repository, "tracked.txt", "main\n", "main")
        before_merge = _git(repository, "rev-parse", "HEAD")
        operation_id = str(uuid.uuid4())
        request = SessionGitMergeRequestDto(
            operationId=operation_id,
            sessionId=session_id,
            target="topic",
        )

        conflict = application.git_merge(request).root

        assert conflict["head"] == before_merge
        assert conflict["operationState"] == "merge"
        assert conflict["conflictFiles"] == ["tracked.txt"]
        assert conflict["status"]["conflictFiles"] == ["tracked.txt"]
        assert application.git_merge(request).root == conflict

        abort_request = SessionGitMergeAbortRequestDto(
            operationId=str(uuid.uuid4()), sessionId=session_id
        )
        aborted = application.git_merge_abort(abort_request).root

        assert aborted["head"] == before_merge
        assert aborted["operationState"] == "none"
        assert aborted["conflictFiles"] == []
        assert aborted["status"]["dirty"] is False
        assert _git(repository, "rev-parse", "HEAD") == before_merge
        assert _git(repository, "status", "--porcelain") == ""
        assert original_head != before_merge
        assert application.git_merge_abort(abort_request).root == aborted
    finally:
        store.close()


def test_merge_requires_branch_clean_workspace_target_and_identity(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    store, manager, application = _application(tmp_path)
    try:
        session_id = _session(application, repository)
        _git(repository, "switch", "-qc", "topic")
        _commit(repository, "topic.txt", "topic\n", "topic")
        _git(repository, "switch", "-q", "main")

        (repository / "tracked.txt").write_text("dirty\n", encoding="utf-8")
        with pytest.raises(ApplicationError) as dirty:
            application.git_merge(
                SessionGitMergeRequestDto(
                    operationId=str(uuid.uuid4()),
                    sessionId=session_id,
                    target="topic",
                )
            )
        assert dirty.value.code == "GIT_WORKTREE_DIRTY"
        _git(repository, "restore", "--", "tracked.txt")

        with pytest.raises(ApplicationError) as target:
            application.git_merge(
                SessionGitMergeRequestDto(
                    operationId=str(uuid.uuid4()),
                    sessionId=session_id,
                    target="missing-target",
                )
            )
        assert target.value.code == "GIT_MERGE_TARGET_INVALID"

        _git(repository, "checkout", "--detach", "-q")
        with pytest.raises(ApplicationError) as branch:
            application.git_merge(
                SessionGitMergeRequestDto(
                    operationId=str(uuid.uuid4()),
                    sessionId=session_id,
                    target="topic",
                )
            )
        assert branch.value.code == "GIT_BRANCH_REQUIRED"

        _git(repository, "switch", "-q", "main")
        _commit(repository, "main.txt", "main\n", "main")
        _git(repository, "config", "--unset", "user.name")
        _git(repository, "config", "--unset", "user.email")
        empty_home = tmp_path / "empty-home"
        empty_home.mkdir()
        manager.git = DulwichGitBackend(
            git_cli=GitCli(runner=HardenedGitRunner(user_home=empty_home))
        )
        with pytest.raises(ApplicationError) as identity:
            application.git_merge(
                SessionGitMergeRequestDto(
                    operationId=str(uuid.uuid4()),
                    sessionId=session_id,
                    target="topic",
                )
            )
        assert identity.value.code == "GIT_IDENTITY_UNAVAILABLE"
    finally:
        store.close()


def test_merge_operation_id_replays_once_and_preflight_does_not_prepare(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    store, _manager, application = _application(tmp_path)
    try:
        session_id = _session(application, repository)
        _git(repository, "switch", "-qc", "topic")
        _commit(repository, "topic.txt", "topic\n", "topic")
        _git(repository, "switch", "-q", "main")
        _commit(repository, "main.txt", "main\n", "main")
        operation_id = str(uuid.uuid4())
        request = SessionGitMergeRequestDto(
            operationId=operation_id, sessionId=session_id, target="topic"
        )

        first = application.git_merge(request).root
        first_head = _git(repository, "rev-parse", "HEAD")
        assert application.git_merge(request).root == first
        assert _git(repository, "rev-parse", "HEAD") == first_head

        with pytest.raises(ApplicationError) as reused:
            application.git_merge(
                SessionGitMergeRequestDto(
                    operationId=operation_id,
                    sessionId=session_id,
                    target="main",
                )
            )
        assert reused.value.code == "OPERATION_ID_REUSED"

        store.create_run(session_id, "active")
        busy_id = str(uuid.uuid4())
        with pytest.raises(ApplicationError) as busy:
            application.git_merge(
                SessionGitMergeRequestDto(
                    operationId=busy_id,
                    sessionId=session_id,
                    target="topic",
                )
            )
        assert busy.value.code == "GIT_WORKFLOW_BUSY"
        connection = store.connection
        assert connection is not None
        assert connection.execute(
            "SELECT COUNT(*) FROM operations WHERE id = ?", (busy_id,)
        ).fetchone()[0] == 0
    finally:
        store.close()


def test_prepared_merge_is_not_reexecuted_when_result_is_uncertain(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    store, _manager, application = _application(tmp_path)
    try:
        session_id = _session(application, repository)
        _git(repository, "switch", "-qc", "topic")
        target = _commit(repository, "topic.txt", "topic\n", "topic")
        _git(repository, "switch", "-q", "main")
        original_head = _git(repository, "rev-parse", "HEAD")
        operation_id = str(uuid.uuid4())
        store.prepare_operation(
            operation_id,
            "session/gitMerge",
            {"sessionId": session_id, "target": target},
        )

        with pytest.raises(ApplicationError) as in_progress:
            application.git_merge(
                SessionGitMergeRequestDto(
                    operationId=operation_id,
                    sessionId=session_id,
                    target=target,
                )
            )

        assert in_progress.value.code == "OPERATION_IN_PROGRESS"
        assert _git(repository, "rev-parse", "HEAD") == original_head
    finally:
        store.close()


def test_attached_managed_worktree_merge_preserves_baseline(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    _git(repository, "switch", "-qc", "topic")
    topic_head = _commit(repository, "topic.txt", "topic\n", "topic")
    _git(repository, "switch", "-q", "main")
    store, _manager, application = _application(tmp_path)
    try:
        session = application.create(
            SessionCreateRequestDto(
                workspaceRoot=str(repository), executionMode="worktree"
            )
        ).root
        baseline = session["worktree"]["baseCommit"]
        application.create_branch(
            SessionCreateBranchRequestDto(
                sessionId=session["id"], branch="feature/merge"
            )
        )

        merged = application.git_merge(
            SessionGitMergeRequestDto(
                operationId=str(uuid.uuid4()),
                sessionId=session["id"],
                target="topic",
            )
        ).root

        assert merged["head"] == topic_head
        assert merged["branch"] == "feature/merge"
        assert merged["status"]["baseCommit"] == baseline
        assert merged["operationState"] == "none"
    finally:
        store.close()

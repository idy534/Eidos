from __future__ import annotations

from pathlib import Path
import subprocess
import threading
from uuid import uuid4

import pytest
from pydantic import ValidationError

from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.application.repository import RepositoryApplicationFactory
from eidos_runtime.application.sessions import SessionApplication
from eidos_runtime.context.project_rules import ProjectRuleResolver
from eidos_runtime.db.errors import StorageError
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.domain.worktree import WorktreeState
from eidos_runtime.git import WorktreeManager
from eidos_runtime.git.errors import WorktreeError
from eidos_runtime.protocol.methods import (
    SessionCreateRequestDto,
    SessionDeleteRequestDto,
    SessionGitDiffRequestDto,
    SessionGitStatusRequestDto,
    SessionListRequestDto,
    SessionReadRequestDto,
)
from eidos_runtime.tools.runtime_workspace import ToolExecutor
from eidos_runtime.tools.workspace import FileChange


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
    _git(repository, "config", "user.email", "eidos-tests@example.com")
    _git(repository, "config", "user.name", "Eidos Tests")
    (repository / "README.md").write_text("base\n", encoding="utf-8")
    _git(repository, "add", "README.md")
    _git(repository, "commit", "-qm", "initial")
    (repository / "nested").mkdir()
    return repository


def _application(tmp_path: Path) -> tuple[SessionStore, WorktreeManager, SessionApplication]:
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
    return store, manager, application


def _create(
    application: SessionApplication,
    repository_seed: Path,
    *,
    operation_id: str | None = None,
) -> dict[str, object]:
    request = SessionCreateRequestDto(
        workspaceRoot=str(repository_seed),
        operationId=operation_id,
    )
    return application.create(request).root


def test_session_create_binds_worktree_and_run_uses_its_identity(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    store, manager, application = _application(tmp_path)
    try:
        session = _create(application, repository / "nested")
        row = store.connection.execute(
            """
            SELECT sessions.workspace_root, sessions.worktree_id,
                   worktrees.worktree_root
            FROM sessions
            JOIN worktrees ON worktrees.id = sessions.worktree_id
            WHERE sessions.id = ?
            """,
            (session["id"],),
        ).fetchone()
        assert row is not None
        assert row["workspace_root"] == str(repository.resolve())
        assert row["worktree_id"]

        worktree = manager.open(row["worktree_id"])
        run, _ = store.create_run(session["id"], "inspect")
        identity = store.workspace_for_run(run["id"])
        metadata = Path(worktree.worktree_root).stat()

        assert identity.path == Path(worktree.worktree_root).resolve()
        assert (identity.device, identity.inode, identity.owner) == (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_uid,
        )
        snapshot = store.read_run_resolution_snapshot(run["id"])
        assert snapshot.workspace_identity.path == worktree.worktree_root
    finally:
        store.close()


def test_managed_session_create_list_and_read_share_worktree_projection(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    store, manager, application = _application(tmp_path)
    try:
        created = _create(application, repository)
        listed = application.list(SessionListRequestDto()).root["items"][0]
        snapshot = application.read_snapshot(
            SessionReadRequestDto(sessionId=created["id"])
        ).root["session"]

        row = store.connection.execute(
            "SELECT worktree_id FROM sessions WHERE id = ?", (created["id"],)
        ).fetchone()
        assert row is not None
        worktree = manager.open(row["worktree_id"])
        project = manager.project(worktree.project_id)
        expected = {
            "worktreeId": worktree.id,
            "projectId": project.id,
            "repositoryRoot": project.repository_root,
            "worktreeRoot": worktree.worktree_root,
            "baseRef": worktree.base_ref,
            "baseCommit": worktree.base_commit,
            "branch": worktree.branch,
            "state": "active",
        }

        assert created["worktree"] == expected
        assert listed["worktree"] == expected
        assert snapshot["worktree"] == expected
        assert created["workspaceRoot"] == project.repository_root
    finally:
        store.close()


def test_two_managed_sessions_isolate_files_and_execution_tools(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    store, manager, application = _application(tmp_path)
    try:
        first = _create(application, repository)
        second = _create(application, repository)
        rows = {
            row["id"]: row
            for row in store.connection.execute(
            """
            SELECT id, worktree_id FROM sessions
            WHERE id IN (?, ?)
            """,
            (first["id"], second["id"]),
            ).fetchall()
        }
        assert len(rows) == 2
        first_worktree = manager.open(rows[first["id"]]["worktree_id"])
        second_worktree = manager.open(rows[second["id"]]["worktree_id"])
        assert first_worktree.id != second_worktree.id
        assert first_worktree.worktree_root != second_worktree.worktree_root

        first_run, _ = store.create_run(first["id"], "write")
        identity = store.workspace_for_run(first_run["id"])
        assert identity.path == Path(first_worktree.worktree_root)
        with ToolExecutor(identity) as executor:
            change = executor.prepare_file_change(
                "write_file",
                {"path": "a.txt", "content": "from first\n"},
                threading.Event(),
            )
            assert isinstance(change, FileChange)
            executor.commit_file_change("write_file", change, threading.Event())
            read = executor.execute(
                "read_file", {"path": "a.txt"}, threading.Event()
            )
            shell_cwd = executor.prepare_shell(".", threading.Event())

        assert read["data"]["content"] == "from first\n"
        assert shell_cwd.path == Path(first_worktree.worktree_root)
        assert (Path(first_worktree.worktree_root) / "a.txt").exists()
        assert not (Path(second_worktree.worktree_root) / "a.txt").exists()
        assert not (repository / "a.txt").exists()

        (Path(first_worktree.worktree_root) / "EIDOS.md").write_text(
            "managed rule\n", encoding="utf-8"
        )
        rules = ProjectRuleResolver().resolve(identity.path, identity.path)
        assert rules.workspace_root == str(identity.path)
        assert rules.rules[0].absolute_path == str(
            Path(first_worktree.worktree_root) / "EIDOS.md"
        )

        repository_application = RepositoryApplicationFactory(
            store.repository_intelligence_repository
        ).for_workspace(identity.path)
        assert repository_application.inventory_builder.root == identity.path
    finally:
        store.close()


def test_session_git_status_and_diff_are_resolved_from_the_managed_binding(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    store, _manager, application = _application(tmp_path)
    try:
        first = _create(application, repository)
        second = _create(application, repository)
        first_root = Path(first["worktree"]["worktreeRoot"])
        (first_root / "README.md").write_text("changed\n", encoding="utf-8")
        (first_root / "a.txt").write_text("from first\n", encoding="utf-8")

        first_status = application.git_status(
            SessionGitStatusRequestDto(sessionId=first["id"])
        ).root
        second_status = application.git_status(
            SessionGitStatusRequestDto(sessionId=second["id"])
        ).root
        head_diff = application.git_diff(
            SessionGitDiffRequestDto(sessionId=first["id"], scope="head")
        ).root
        baseline_diff = application.git_diff(
            SessionGitDiffRequestDto(sessionId=first["id"], scope="baseline")
        ).root
        second_diff = application.git_diff(
            SessionGitDiffRequestDto(sessionId=second["id"], scope="head")
        ).root

        assert first_status["worktreeId"] == first["worktree"]["worktreeId"]
        assert first_status["dirty"] is True
        assert first_status["unstagedCount"] == 1
        assert first_status["untrackedCount"] == 1
        assert second_status["dirty"] is False
        assert {"README.md", "a.txt"} <= set(head_diff["changedFiles"])
        assert "a.txt" in head_diff["unifiedDiff"]
        assert baseline_diff["scope"] == "baseline"
        assert "a.txt" in baseline_diff["unifiedDiff"]
        assert second_diff["changedFiles"] == []
        assert second_diff["unifiedDiff"] == ""
    finally:
        store.close()


def test_legacy_session_git_review_is_rejected_with_a_stable_error(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "legacy-workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    application = SessionApplication(store, scan_text=lambda value: value)
    try:
        session = store.create_session(str(workspace))
        with pytest.raises(ApplicationError) as error:
            application.git_status(
                SessionGitStatusRequestDto(sessionId=session["id"])
            )
        assert error.value.code == "GIT_WORKTREE_NOT_MANAGED"
    finally:
        store.close()


def test_session_git_review_requests_reject_filesystem_paths() -> None:
    with pytest.raises(ValidationError):
        SessionGitStatusRequestDto.model_validate(
            {"sessionId": str(uuid4()), "worktreeRoot": "/caller/path"}
        )


def test_legacy_session_still_uses_workspace_root(tmp_path: Path) -> None:
    workspace = tmp_path / "legacy-workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    try:
        session = store.create_session(str(workspace))
        row = store.connection.execute(
            "SELECT worktree_id FROM sessions WHERE id = ?", (session["id"],)
        ).fetchone()
        assert row["worktree_id"] is None
        run, _ = store.create_run(session["id"], "legacy")
        assert store.workspace_for_run(run["id"]).path == workspace.resolve()
    finally:
        store.close()


def test_same_operation_id_replays_without_creating_another_worktree(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    store, _manager, application = _application(tmp_path)
    operation_id = str(uuid4())
    try:
        first = _create(application, repository, operation_id=operation_id)
        second = _create(application, repository, operation_id=operation_id)
        assert second == first
        assert store.connection.execute(
            "SELECT COUNT(*) FROM sessions"
        ).fetchone()[0] == 1
        assert store.connection.execute(
            "SELECT COUNT(*) FROM worktrees"
        ).fetchone()[0] == 1
    finally:
        store.close()


def test_session_persistence_failure_compensates_clean_worktree_without_force(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository(tmp_path)
    store, manager, application = _application(tmp_path)

    def fail_session_insert(*_args: object, **_kwargs: object) -> object:
        raise StorageError("injected_session_insert_failure")

    monkeypatch.setattr(application._repository, "create_session", fail_session_insert)
    try:
        with pytest.raises(ApplicationError, match="injected_session_insert_failure"):
            _create(application, repository)

        assert store.connection.execute(
            "SELECT COUNT(*) FROM sessions"
        ).fetchone()[0] == 0
        worktrees = manager.repository.list_worktrees(
            manager.repository.list_projects()[0].id
        )
        assert len(worktrees) == 1
        assert worktrees[0].state.value == "deleted"
        assert not Path(worktrees[0].worktree_root).exists()
        branch = subprocess.run(
            ["git", "show-ref", "--verify", f"refs/heads/{worktrees[0].branch}"],
            cwd=repository,
            capture_output=True,
            text=True,
        )
        assert branch.returncode != 0
    finally:
        store.close()


def test_managed_session_clean_delete_removes_worktree_and_preserves_branch(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    store, manager, application = _application(tmp_path)
    try:
        session = _create(application, repository)
        worktree_id = str(session["worktree"]["worktreeId"])
        worktree = manager.open(worktree_id)

        deleted = application.delete(
            SessionDeleteRequestDto(sessionId=str(session["id"]))
        )

        assert deleted.root == {"deletedSessionId": session["id"]}
        assert store.typed_runtime_repository().read_session(str(session["id"])) is None
        assert manager.repository.read_worktree(worktree_id).state.value == "deleted"
        assert not Path(worktree.worktree_root).exists()
        assert _git(
            repository, "rev-parse", f"refs/heads/{worktree.branch}"
        ) == worktree.base_commit
    finally:
        store.close()


def test_managed_session_dirty_delete_is_rejected_without_side_effects(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    store, manager, application = _application(tmp_path)
    try:
        session = _create(application, repository)
        worktree_id = str(session["worktree"]["worktreeId"])
        worktree = manager.open(worktree_id)
        (Path(worktree.worktree_root) / "dirty.txt").write_text(
            "unsaved\n", encoding="utf-8"
        )

        with pytest.raises(ApplicationError) as error:
            application.delete(
                SessionDeleteRequestDto(sessionId=str(session["id"]))
            )

        assert error.value.code == "WORKTREE_DIRTY"
        assert store.typed_runtime_repository().read_session(str(session["id"])) is not None
        assert manager.repository.read_worktree(worktree_id).state.value == "active"
        assert Path(worktree.worktree_root).exists()
    finally:
        store.close()


def test_managed_session_delete_retry_converges_after_git_removal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository(tmp_path)
    store, manager, application = _application(tmp_path)
    try:
        session = _create(application, repository)
        worktree_id = str(session["worktree"]["worktreeId"])
        worktree_root = Path(str(session["worktree"]["worktreeRoot"]))
        real_delete = application._repository.delete_session
        attempts = 0

        def fail_once(*args: object, **kwargs: object) -> object:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise StorageError("injected session delete failure")
            return real_delete(*args, **kwargs)

        monkeypatch.setattr(application._repository, "delete_session", fail_once)

        with pytest.raises(ApplicationError) as first_error:
            application.delete(
                SessionDeleteRequestDto(sessionId=str(session["id"]))
            )
        assert first_error.value.code == "SESSION_PERSISTENCE_FAILED"
        assert not worktree_root.exists()
        assert manager.repository.read_worktree(worktree_id).state.value == "deleted"
        assert store.typed_runtime_repository().read_session(str(session["id"])) is not None

        retried = application.delete(
            SessionDeleteRequestDto(sessionId=str(session["id"]))
        )
        assert retried.root == {"deletedSessionId": session["id"]}
        assert store.typed_runtime_repository().read_session(str(session["id"])) is None
    finally:
        store.close()


def test_managed_session_delete_operation_replays_after_session_removal(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    store, _manager, application = _application(tmp_path)
    operation_id = str(uuid4())
    try:
        session = _create(application, repository)
        request = SessionDeleteRequestDto(
            sessionId=str(session["id"]), operationId=operation_id
        )

        first = application.delete(request)
        replay = application.delete(request)

        assert replay == first
        assert store.connection.execute(
            "SELECT COUNT(*) FROM operations WHERE id = ?", (operation_id,)
        ).fetchone()[0] == 1
    finally:
        store.close()


def test_concurrent_delete_same_operation_id_never_removes_two_worktrees(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository(tmp_path)
    store, manager, application = _application(tmp_path)
    operation_id = str(uuid4())
    first_entered = threading.Event()
    second_entered = threading.Event()
    release_first = threading.Event()
    real_delete = manager.delete
    delete_calls = 0

    def blocked_delete(worktree_id: str) -> object:
        nonlocal delete_calls
        delete_calls += 1
        if delete_calls == 1:
            first_entered.set()
            assert release_first.wait(timeout=5)
        else:
            second_entered.set()
        return real_delete(worktree_id)

    monkeypatch.setattr(manager, "delete", blocked_delete)
    try:
        sessions = [_create(application, repository), _create(application, repository)]
        results: list[object] = []
        errors: list[ApplicationError] = []

        def delete_session(session_id: str) -> None:
            try:
                results.append(application.delete(SessionDeleteRequestDto(
                    sessionId=session_id,
                    operationId=operation_id,
                )))
            except ApplicationError as error:
                errors.append(error)

        first = threading.Thread(
            target=delete_session, args=(str(sessions[0]["id"]),)
        )
        second = threading.Thread(
            target=delete_session, args=(str(sessions[1]["id"]),)
        )
        first.start()
        assert first_entered.wait(timeout=5)
        second.start()
        second_entered.wait(timeout=0.25)
        release_first.set()
        first.join(timeout=5)
        second.join(timeout=5)

        assert not first.is_alive() and not second.is_alive()
        assert len(results) == 1
        assert [error.code for error in errors] == ["OPERATION_ID_REUSED"]
        assert delete_calls == 1
        remaining = [
            session
            for session in sessions
            if store.typed_runtime_repository().read_session(str(session["id"]))
            is not None
        ]
        assert len(remaining) == 1
        assert Path(str(remaining[0]["worktree"]["worktreeRoot"])).exists()
    finally:
        release_first.set()
        store.close()


def test_concurrent_delete_same_session_and_operation_replays_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository(tmp_path)
    store, manager, application = _application(tmp_path)
    operation_id = str(uuid4())
    first_entered = threading.Event()
    release_first = threading.Event()
    real_delete = manager.delete

    def blocked_delete(worktree_id: str) -> object:
        first_entered.set()
        assert release_first.wait(timeout=5)
        return real_delete(worktree_id)

    monkeypatch.setattr(manager, "delete", blocked_delete)
    try:
        session = _create(application, repository)
        request = SessionDeleteRequestDto(
            sessionId=str(session["id"]), operationId=operation_id
        )
        results: list[object] = []
        errors: list[ApplicationError] = []

        def delete_session() -> None:
            try:
                results.append(application.delete(request))
            except ApplicationError as error:
                errors.append(error)

        first = threading.Thread(target=delete_session)
        second = threading.Thread(target=delete_session)
        first.start()
        assert first_entered.wait(timeout=5)
        second.start()
        release_first.set()
        first.join(timeout=5)
        second.join(timeout=5)

        assert not first.is_alive() and not second.is_alive()
        assert errors == []
        assert len(results) == 2
        assert results[0] == results[1]
    finally:
        release_first.set()
        store.close()


def test_session_create_rollback_preserves_a_runtime_branch_that_advanced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository(tmp_path)
    store, manager, application = _application(tmp_path)

    def advance_then_fail(
        _repository_root: str,
        *,
        worktree_id: str | None = None,
        operation_id: str | None = None,
    ) -> object:
        del operation_id
        assert worktree_id is not None
        worktree = manager.repository.read_worktree(worktree_id)
        assert worktree is not None
        root = Path(worktree.worktree_root)
        (root / "advanced.txt").write_text("advanced\n", encoding="utf-8")
        _git(root, "add", "advanced.txt")
        _git(root, "commit", "-qm", "advance before failed Session insert")
        raise StorageError("injected session insert failure after branch advance")

    monkeypatch.setattr(application._repository, "create_session", advance_then_fail)
    try:
        with pytest.raises(ApplicationError) as error:
            _create(application, repository)
        assert error.value.code == "SESSION_PERSISTENCE_FAILED"

        worktrees = manager.repository.list_worktrees(
            manager.repository.list_projects()[0].id
        )
        assert len(worktrees) == 1
        worktree = worktrees[0]
        assert worktree.state.value == "deleted"
        assert not Path(worktree.worktree_root).exists()
        assert _git(repository, "rev-parse", f"refs/heads/{worktree.branch}") != (
            worktree.base_commit
        )
    finally:
        store.close()


def test_create_rollback_refuses_a_worktree_still_bound_to_a_session(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    store, manager, application = _application(tmp_path)
    try:
        session = _create(application, repository)
        worktree_id = str(session["worktree"]["worktreeId"])
        worktree_root = Path(str(session["worktree"]["worktreeRoot"]))

        with pytest.raises(WorktreeError) as error:
            manager.rollback_create(worktree_id)

        assert error.value.code == "worktree_still_bound"
        assert worktree_root.exists()
        assert manager.repository.read_worktree(worktree_id).state is WorktreeState.ACTIVE
    finally:
        store.close()

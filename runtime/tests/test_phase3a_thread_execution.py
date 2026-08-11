from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from eidos_runtime.application.checkpoints import CheckpointApplication
from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.application.sessions import SessionApplication
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.git import WorktreeManager
from eidos_runtime.protocol.methods import (
    CheckpointCreateRequestDto,
    CheckpointForkRequestDto,
    GitContextRequestDto,
    SessionCreateRequestDto,
    SessionDeleteRequestDto,
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
    _git(repository, "config", "user.email", "eidos-tests@example.com")
    _git(repository, "config", "user.name", "Eidos Tests")
    (repository / "README.md").write_text("main\n", encoding="utf-8")
    _git(repository, "add", "README.md")
    _git(repository, "commit", "-qm", "initial")
    main_commit = _git(repository, "rev-parse", "HEAD")
    _git(repository, "switch", "-q", "-c", "feature/x")
    (repository / "README.md").write_text("feature\n", encoding="utf-8")
    _git(repository, "commit", "-qam", "feature")
    _git(repository, "switch", "-q", "main")
    assert _git(repository, "rev-parse", "main") == main_commit
    return repository


def _setup(
    tmp_path: Path,
) -> tuple[SessionStore, WorktreeManager, SessionApplication]:
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
    workspace: Path,
    *,
    execution_mode: str,
    base_ref: str | None = None,
) -> dict[str, object]:
    return application.create(
        SessionCreateRequestDto(
            workspaceRoot=str(workspace),
            executionMode=execution_mode,
            **({"baseRef": base_ref} if base_ref is not None else {}),
        )
    ).root


def test_git_project_supports_explicit_local_and_detached_worktree_sessions(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    store, manager, application = _setup(tmp_path)
    try:
        local = _create(application, repository, execution_mode="local")
        worktree = _create(
            application,
            repository,
            execution_mode="worktree",
            base_ref="main",
        )

        assert local["executionMode"] == "local"
        assert local.get("worktree") is None
        assert worktree["executionMode"] == "worktree"
        assert worktree["worktree"]["branch"] is None

        worktree_id = str(worktree["worktree"]["worktreeId"])
        persisted = manager.repository.read_worktree(worktree_id)
        assert persisted is not None
        assert persisted.branch is None
        assert manager.validate(worktree_id).valid
        assert _git(Path(persisted.worktree_root), "branch", "--show-current") == ""
        assert _git(Path(persisted.worktree_root), "rev-parse", "HEAD") == persisted.base_commit
        assert _git(repository, "branch", "--list", "eidos/*") == ""
        assert tuple(store.connection.execute(
            "SELECT execution_mode, worktree_id FROM sessions WHERE id = ?",
            (worktree["id"],),
        ).fetchone()) == ("worktree", worktree_id)
    finally:
        store.close()


def test_non_git_project_rejects_worktree_mode_but_accepts_local(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store, _manager, application = _setup(tmp_path)
    try:
        local = _create(application, workspace, execution_mode="local")
        assert local["executionMode"] == "local"
        assert local.get("worktree") is None

        with pytest.raises(ApplicationError) as error:
            _create(application, workspace, execution_mode="worktree")
        assert error.value.code == "WORKTREE_REQUIRES_GIT"
    finally:
        store.close()


def test_starting_ref_is_resolved_to_an_immutable_base_commit(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    store, manager, application = _setup(tmp_path)
    try:
        main_commit = _git(repository, "rev-parse", "main")
        feature_commit = _git(repository, "rev-parse", "feature/x")
        main = _create(
            application,
            repository,
            execution_mode="worktree",
            base_ref="main",
        )
        feature = _create(
            application,
            repository,
            execution_mode="worktree",
            base_ref="feature/x",
        )

        assert main["worktree"]["baseRef"] == "main"
        assert main["worktree"]["baseCommit"] == main_commit
        assert feature["worktree"]["baseRef"] == "feature/x"
        assert feature["worktree"]["baseCommit"] == feature_commit

        with pytest.raises(ApplicationError) as error:
            _create(
                application,
                repository,
                execution_mode="worktree",
                base_ref="missing/ref",
            )
        assert error.value.code == "BASE_REF_NOT_FOUND"
        assert store.connection.execute("SELECT COUNT(*) FROM worktrees").fetchone()[0] == 2
        assert manager.repository.list_worktrees()[0].branch is None
    finally:
        store.close()


def test_detached_source_uses_head_as_starting_ref(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    detached_head = _git(repository, "rev-parse", "feature/x")
    _git(repository, "checkout", "--detach", "feature/x")
    store, manager, application = _setup(tmp_path)
    try:
        created = _create(application, repository, execution_mode="worktree")
        worktree = manager.repository.read_worktree(str(created["worktree"]["worktreeId"]))
        assert worktree is not None
        assert worktree.base_ref == "HEAD"
        assert worktree.base_commit == detached_head
    finally:
        store.close()


def test_same_base_commit_can_provision_parallel_detached_worktrees(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    store, manager, application = _setup(tmp_path)
    try:
        first = _create(
            application,
            repository,
            execution_mode="worktree",
            base_ref="main",
        )
        second = _create(
            application,
            repository,
            execution_mode="worktree",
            base_ref="main",
        )
        first_worktree = manager.repository.read_worktree(str(first["worktree"]["worktreeId"]))
        second_worktree = manager.repository.read_worktree(str(second["worktree"]["worktreeId"]))
        assert first_worktree is not None and second_worktree is not None
        assert first_worktree.base_commit == second_worktree.base_commit
        assert first_worktree.worktree_root != second_worktree.worktree_root
        assert first_worktree.branch is None and second_worktree.branch is None
        assert _git(repository, "status", "--short") == ""
    finally:
        store.close()


def test_local_and_worktree_runs_resolve_different_execution_roots(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    store, manager, application = _setup(tmp_path)
    try:
        local = _create(application, repository, execution_mode="local")
        managed = _create(application, repository, execution_mode="worktree")
        local_run, _ = store.create_run(str(local["id"]), "local")
        managed_run, _ = store.create_run(
            str(managed["id"]), "managed", queued=True
        )
        managed_worktree = manager.repository.read_worktree(
            str(managed["worktree"]["worktreeId"])
        )
        assert managed_worktree is not None
        assert store.workspace_for_run(str(local_run["id"])).path == repository.resolve()
        assert store.workspace_for_run(str(managed_run["id"])).path == Path(
            managed_worktree.worktree_root
        ).resolve()
    finally:
        store.close()


def test_detached_worktree_validation_rejects_external_branch_attachment(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    store, manager, application = _setup(tmp_path)
    try:
        created = _create(application, repository, execution_mode="worktree")
        worktree = manager.repository.read_worktree(str(created["worktree"]["worktreeId"]))
        assert worktree is not None
        _git(Path(worktree.worktree_root), "switch", "-c", "external/branch")
        validation = manager.validate(worktree.id)
        assert validation.valid is False
        assert validation.code == "worktree_invalid"
        assert manager.repository.read_worktree(worktree.id).branch is None
    finally:
        store.close()


def test_checkpoint_managed_fork_is_detached_at_checkpoint_head(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    store, manager, sessions = _setup(tmp_path)
    checkpoints = CheckpointApplication(
        store,
        store.checkpoint_repository(),
        worktree_manager=manager,
    )
    try:
        parent = _create(application=sessions, workspace=repository, execution_mode="worktree")
        parent_root = Path(parent["worktree"]["worktreeRoot"])
        (parent_root / "checkpoint.txt").write_text("checkpoint\n", encoding="utf-8")
        _git(parent_root, "add", "checkpoint.txt")
        _git(parent_root, "commit", "-qm", "checkpoint")
        run, _ = store.create_run(str(parent["id"]), "fork", queued=True)
        checkpoint = checkpoints.create(
            CheckpointCreateRequestDto(runId=str(run["id"]))
        ).checkpoint

        result = checkpoints.fork(
            CheckpointForkRequestDto(checkpointId=checkpoint.id)
        )
        fork_projection = store.typed_runtime_repository().read_session_projection(
            result.run.session_id
        )
        assert fork_projection is not None and fork_projection.worktree is not None
        assert fork_projection.worktree.branch is None
        fork_root = Path(fork_projection.worktree.worktree_root)
        assert _git(fork_root, "branch", "--show-current") == ""
        assert _git(fork_root, "rev-parse", "HEAD") == checkpoint.git_head
    finally:
        store.close()


def test_session_delete_detached_worktree_does_not_delete_any_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository(tmp_path)
    store, manager, application = _setup(tmp_path)
    try:
        created = _create(application, repository, execution_mode="worktree")
        worktree_id = str(created["worktree"]["worktreeId"])
        monkeypatch.setattr(
            manager.git,
            "delete_branch_if_equals",
            lambda *_args: pytest.fail("detached delete must not delete a branch"),
        )
        application.delete(SessionDeleteRequestDto(sessionId=str(created["id"])))
        assert manager.repository.read_worktree(worktree_id).state.value == "deleted"
        assert _git(repository, "branch", "--list", "eidos/*") == ""
    finally:
        store.close()


def test_project_git_context_uses_runtime_git_backend(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    store, _manager, application = _setup(tmp_path)
    try:
        context = application.git_context(
            GitContextRequestDto(workspaceRoot=str(repository))
        )
        assert context.root == {
            "gitAvailable": True,
            "currentBranch": "main",
            "head": _git(repository, "rev-parse", "HEAD"),
            "branches": ["feature/x", "main"],
        }
    finally:
        store.close()

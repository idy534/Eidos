from __future__ import annotations

from pathlib import Path
import subprocess
import threading

import pytest

from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.application.runs import RunApplication
from eidos_runtime.application.session_lifecycle import SessionLifecycleCoordinator
from eidos_runtime.application.sessions import SessionApplication
from eidos_runtime.application.worktree_retention import WorktreeRetentionService
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.domain.worktree import WorktreeState
from eidos_runtime.git import WorktreeManager
from eidos_runtime.git.errors import GitCommandTimeoutError
from eidos_runtime.protocol.methods import (
    RunStartRequestDto,
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
    (repository / "README.md").write_text("base\n", encoding="utf-8")
    _git(repository, "add", "README.md")
    _git(repository, "commit", "-qm", "initial")
    return repository


class _Model:
    profile_snapshot = None


class _Environment:
    def model_is_configured(self) -> bool:
        return True

    def model_for(self, _model_id: str) -> _Model:
        return _Model()

    def freeze_model_config(self, _run_id: str, _config: object) -> None:
        pass

    def discard_model_config(self, _run_id: str) -> None:
        pass

    def extension_snapshot(self) -> dict[str, object]:
        return {
            "schemaVersion": 1,
            "extensionContractVersion": 1,
            "plugins": [],
            "skillCatalogHash": "0" * 64,
            "mcpConfigHash": "0" * 64,
        }

    def schedule_title_generation(
        self, _session_id: str, _user_input: str, _model_id: str
    ) -> None:
        pass


class _Runtime:
    def __init__(self, store: SessionStore) -> None:
        self.store = store

    def prepare_next(self) -> object | None:
        return self.store.claim_next_run()

    def release(self, _start: object | None) -> None:
        pass

    def abort(self, _start: object | None) -> None:
        pass

    def cancel_run(
        self, run_id: str, *, operation_id: str | None = None
    ) -> dict[str, object]:
        return self.store.cancel_run(run_id, operation_id=operation_id)


def _setup(
    tmp_path: Path,
) -> tuple[
    SessionStore,
    WorktreeManager,
    SessionApplication,
    RunApplication,
    SessionLifecycleCoordinator,
]:
    store = SessionStore(tmp_path / "data")
    store.initialize()
    manager = WorktreeManager(
        store.database,
        managed_root=tmp_path / "managed-worktrees",
    )
    lifecycle = SessionLifecycleCoordinator()
    sessions = SessionApplication(
        store,
        scan_text=lambda value: value,
        worktree_manager=manager,
        lifecycle=lifecycle,
    )
    runs = RunApplication(
        store=store,
        runtime=_Runtime(store),
        environment=_Environment(),
        scan_text=lambda value: value,
        worktree_manager=manager,
        session_repository=store.typed_runtime_repository(),
        lifecycle_coordinator=lifecycle,
    )
    return store, manager, sessions, runs, lifecycle


def _managed_session(
    sessions: SessionApplication, repository: Path
) -> dict[str, object]:
    return sessions.create(
        SessionCreateRequestDto(
            workspaceRoot=str(repository), executionMode="worktree"
        )
    ).root


def _start(runs: RunApplication, session_id: str) -> object:
    return runs.start(
        RunStartRequestDto(
            sessionId=session_id,
            userInput="inspect",
            modelId="deepseek-v4-flash",
        )
    )


def test_run_admission_rejects_a_missing_managed_worktree(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    store, manager, sessions, runs, _lifecycle = _setup(tmp_path)
    try:
        session = _managed_session(sessions, repository)
        root = Path(str(session["worktree"]["worktreeRoot"]))
        root.rename(tmp_path / "displaced-worktree")

        with pytest.raises(ApplicationError) as error:
            _start(runs, str(session["id"]))

        assert error.value.code == "GIT_WORKTREE_MISSING"
        assert store.connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
        worktree_id = str(session["worktree"]["worktreeId"])
        assert manager.repository.read_worktree(worktree_id).state is WorktreeState.MISSING
    finally:
        store.close()


def test_run_admission_requires_restore_after_retention_cleanup(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    store, manager, sessions, runs, _lifecycle = _setup(tmp_path)
    try:
        session = _managed_session(sessions, repository)
        worktree_id = str(session["worktree"]["worktreeId"])
        WorktreeRetentionService(store.database, manager).cleanup_worktree(
            worktree_id, reason="retention"
        )

        with pytest.raises(ApplicationError) as error:
            _start(runs, str(session["id"]))

        assert error.value.code == "WORKTREE_RESTORE_REQUIRED"
        assert store.connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
    finally:
        store.close()


def test_run_admission_rejects_a_worktree_with_unfinished_retention_cleanup(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    store, manager, sessions, runs, _lifecycle = _setup(tmp_path)
    try:
        session = _managed_session(sessions, repository)
        worktree_id = str(session["worktree"]["worktreeId"])
        retention = WorktreeRetentionService(store.database, manager)
        retention._find_or_prepare_cleanup(manager.read_worktree(worktree_id))

        with pytest.raises(ApplicationError) as error:
            _start(runs, str(session["id"]))

        assert error.value.code == "WORKTREE_RECOVERY_REQUIRED"
        assert store.connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
    finally:
        store.close()


def test_run_admission_rejects_git_observation_timeout_without_state_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository(tmp_path)
    store, manager, sessions, runs, _lifecycle = _setup(tmp_path)
    try:
        session = _managed_session(sessions, repository)
        worktree_id = str(session["worktree"]["worktreeId"])

        def timeout(_cwd: Path) -> object:
            raise GitCommandTimeoutError("worktree-list")

        monkeypatch.setattr(manager.git, "worktree_list", timeout)
        with pytest.raises(ApplicationError) as error:
            _start(runs, str(session["id"]))

        assert error.value.code == "GIT_OBSERVATION_UNAVAILABLE"
        assert store.connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
        assert manager.repository.read_worktree(worktree_id).state is WorktreeState.ACTIVE
    finally:
        store.close()


def test_run_admission_rejects_repository_and_branch_mismatch(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    store, manager, sessions, runs, _lifecycle = _setup(tmp_path)
    try:
        repository_mismatch = _managed_session(sessions, repository)
        project_id = str(repository_mismatch["worktree"]["projectId"])
        with store.connection:
            store.connection.execute(
                "UPDATE projects SET git_common_dir = ? WHERE id = ?",
                (str(tmp_path / "foreign-git-common-dir"), project_id),
            )
        with pytest.raises(ApplicationError) as repository_error:
            _start(runs, str(repository_mismatch["id"]))
        assert repository_error.value.code == "GIT_WORKTREE_INVALID"
        assert store.connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0

        project = manager.repository.read_project(project_id)
        assert project is not None
        discovery = manager.discover(repository)
        with store.connection:
            store.connection.execute(
                "UPDATE projects SET git_common_dir = ? WHERE id = ?",
                (discovery.git_common_dir, project_id),
            )
        branch_mismatch = _managed_session(sessions, repository)
        branch_root = Path(str(branch_mismatch["worktree"]["worktreeRoot"]))
        _git(branch_root, "switch", "-c", "foreign/branch")
        with pytest.raises(ApplicationError) as branch_error:
            _start(runs, str(branch_mismatch["id"]))
        assert branch_error.value.code == "GIT_WORKTREE_INVALID"
        assert store.connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
    finally:
        store.close()


def test_managed_session_delete_rejects_conflicts_and_observation_timeout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository(tmp_path)
    store, manager, sessions, _runs, _lifecycle = _setup(tmp_path)
    try:
        conflicted = _managed_session(sessions, repository)
        conflicted_root = Path(str(conflicted["worktree"]["worktreeRoot"]))
        (conflicted_root / "README.md").write_text("worktree\n", encoding="utf-8")
        _git(conflicted_root, "add", "README.md")
        _git(conflicted_root, "commit", "-qm", "worktree change")
        (repository / "README.md").write_text("main\n", encoding="utf-8")
        _git(repository, "add", "README.md")
        _git(repository, "commit", "-qm", "main change")
        merge = subprocess.run(
            ["git", "merge", "main"],
            cwd=conflicted_root,
            capture_output=True,
            text=True,
        )
        assert merge.returncode != 0

        with pytest.raises(ApplicationError) as conflict_error:
            sessions.delete(
                SessionDeleteRequestDto(sessionId=str(conflicted["id"]))
            )
        assert conflict_error.value.code == "WORKTREE_DIRTY"
        assert conflicted_root.exists()

        observed = _managed_session(sessions, repository)
        observed_id = str(observed["worktree"]["worktreeId"])

        def timeout(_cwd: Path) -> object:
            raise GitCommandTimeoutError("worktree-list")

        monkeypatch.setattr(manager.git, "worktree_list", timeout)
        with pytest.raises(ApplicationError) as observation_error:
            sessions.delete(
                SessionDeleteRequestDto(sessionId=str(observed["id"]))
            )
        assert observation_error.value.code == "GIT_OBSERVATION_UNAVAILABLE"
        assert store.typed_runtime_repository().read_session(str(observed["id"])) is not None
        assert manager.repository.read_worktree(observed_id).state is WorktreeState.ACTIVE
    finally:
        store.close()


def test_run_admission_compare_and_insert_rejects_path_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository(tmp_path)
    store, _manager, sessions, runs, _lifecycle = _setup(tmp_path)
    try:
        session = _managed_session(sessions, repository)
        root = Path(str(session["worktree"]["worktreeRoot"]))
        displaced = tmp_path / "displaced-worktree"
        real_enqueue = store.enqueue_run

        def replace_then_enqueue(*args: object, **kwargs: object) -> object:
            root.rename(displaced)
            root.mkdir()
            return real_enqueue(*args, **kwargs)

        monkeypatch.setattr(store, "enqueue_run", replace_then_enqueue)
        with pytest.raises(ApplicationError) as error:
            _start(runs, str(session["id"]))

        assert error.value.code == "WORKSPACE_IDENTITY_CHANGED"
        assert store.connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
        assert store.connection.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0
    finally:
        store.close()


def test_active_run_blocks_managed_session_delete_before_git_removal(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    store, manager, sessions, _runs, _lifecycle = _setup(tmp_path)
    try:
        session = _managed_session(sessions, repository)
        store.enqueue_run(str(session["id"]), "already active")
        worktree_id = str(session["worktree"]["worktreeId"])
        root = Path(str(session["worktree"]["worktreeRoot"]))

        with pytest.raises(ApplicationError) as error:
            sessions.delete(
                SessionDeleteRequestDto(sessionId=str(session["id"]))
            )

        assert error.value.code == "SESSION_HAS_ACTIVE_RUN"
        assert root.exists()
        assert manager.repository.read_worktree(worktree_id).state is WorktreeState.ACTIVE
    finally:
        store.close()


def test_run_start_and_session_delete_are_serialized_per_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository(tmp_path)
    store, manager, sessions, runs, _lifecycle = _setup(tmp_path)
    try:
        session = _managed_session(sessions, repository)
        entered_enqueue = threading.Event()
        allow_enqueue = threading.Event()
        real_enqueue = store.enqueue_run

        def blocked_enqueue(*args: object, **kwargs: object) -> object:
            entered_enqueue.set()
            assert allow_enqueue.wait(timeout=5)
            return real_enqueue(*args, **kwargs)

        monkeypatch.setattr(store, "enqueue_run", blocked_enqueue)
        run_errors: list[BaseException] = []
        delete_errors: list[BaseException] = []

        def start_run() -> None:
            try:
                outcome = _start(runs, str(session["id"]))
                outcome.mark_response_delivered()
            except BaseException as error:
                run_errors.append(error)

        def delete_session() -> None:
            try:
                sessions.delete(
                    SessionDeleteRequestDto(sessionId=str(session["id"]))
                )
            except BaseException as error:
                delete_errors.append(error)

        run_thread = threading.Thread(target=start_run)
        delete_thread = threading.Thread(target=delete_session)
        run_thread.start()
        assert entered_enqueue.wait(timeout=5)
        delete_thread.start()
        allow_enqueue.set()
        run_thread.join(timeout=5)
        delete_thread.join(timeout=5)

        assert not run_thread.is_alive()
        assert not delete_thread.is_alive()
        assert run_errors == []
        assert len(delete_errors) == 1
        assert isinstance(delete_errors[0], ApplicationError)
        assert delete_errors[0].code == "SESSION_HAS_ACTIVE_RUN"
        worktree_id = str(session["worktree"]["worktreeId"])
        assert manager.repository.read_worktree(worktree_id).state is WorktreeState.ACTIVE
        assert store.connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1
    finally:
        store.close()

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import time
from uuid import uuid4

import pytest

from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.application.checkpoints import CheckpointApplication
from eidos_runtime.application.sessions import SessionApplication
from eidos_runtime.application.worktree_retention import WorktreeRetentionService
from eidos_runtime.db.schema import SCHEMA_VERSION
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.git import WorktreeManager
from eidos_runtime.protocol.methods import (
    CheckpointCreateRequestDto,
    CheckpointForkRequestDto,
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


def _application(tmp_path: Path) -> tuple[
    SessionStore, WorktreeManager, SessionApplication
]:
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


def test_schema_v18_contains_durable_worktree_lifecycle_table(
    tmp_path: Path,
) -> None:
    store = SessionStore(tmp_path / "data")
    store.initialize()
    try:
        assert SCHEMA_VERSION == 1
        table = store.connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name = 'worktree_lifecycle_operations'
            """
        ).fetchone()
        assert table is not None
        assert store.connection.execute(
            "PRAGMA foreign_key_check"
        ).fetchall() == []
        assert store.connection.execute(
            "PRAGMA integrity_check"
        ).fetchone()[0] == "ok"
    finally:
        store.close()


def test_same_session_create_operation_id_has_one_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository(tmp_path)
    store, manager, application = _application(tmp_path)
    try:
        original_create = manager.create_prepared
        calls = 0

        def delayed_create(*args: object, **kwargs: object) -> object:
            nonlocal calls
            calls += 1
            time.sleep(0.1)
            return original_create(*args, **kwargs)

        monkeypatch.setattr(manager, "create_prepared", delayed_create)
        request = SessionCreateRequestDto(
            workspaceRoot=str(repository),
            executionMode="worktree",
            operationId=str(uuid4()),
        )
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(application.create, (request, request)))

        assert calls == 1
        assert results[0].root["id"] == results[1].root["id"]
        assert store.connection.execute(
            "SELECT COUNT(*) FROM worktrees"
        ).fetchone()[0] == 1
        assert store.connection.execute(
            "SELECT COUNT(*) FROM sessions"
        ).fetchone()[0] == 1
    finally:
        store.close()


def test_same_checkpoint_fork_operation_id_has_one_fork(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository(tmp_path)
    store, manager, sessions = _application(tmp_path)
    checkpoints = CheckpointApplication(
        store,
        store.checkpoint_repository(),
        worktree_manager=manager,
        retention=WorktreeRetentionService(store.database, manager),
    )
    parent = sessions.create(
        SessionCreateRequestDto(
            workspaceRoot=str(repository), executionMode="worktree"
        )
    ).root
    parent_run, _item = store.enqueue_run(str(parent["id"]), "fork")
    checkpoint = checkpoints.create(
        CheckpointCreateRequestDto(runId=str(parent_run["id"]))
    ).checkpoint
    request = CheckpointForkRequestDto(
        checkpointId=checkpoint.id,
        operationId=str(uuid4()),
    )
    try:
        original_create = manager.create_prepared
        calls = 0

        def delayed_create(*args: object, **kwargs: object) -> object:
            nonlocal calls
            calls += 1
            time.sleep(0.1)
            return original_create(*args, **kwargs)

        monkeypatch.setattr(manager, "create_prepared", delayed_create)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(checkpoints.fork, (request, request)))

        assert calls == 1
        assert results[0].run.id == results[1].run.id
        assert store.connection.execute(
            "SELECT COUNT(*) FROM worktrees"
        ).fetchone()[0] == 2
        assert store.connection.execute(
            "SELECT COUNT(*) FROM sessions"
        ).fetchone()[0] == 2
        assert store.connection.execute(
            "SELECT COUNT(*) FROM runs"
        ).fetchone()[0] == 2
        assert store.connection.execute(
            "SELECT COUNT(*) FROM checkpoint_actions WHERE action = 'fork'"
        ).fetchone()[0] == 1
    finally:
        store.close()


@pytest.mark.parametrize(
    "crash_point", ["prepared", "git", "worktree", "session"]
)
def test_session_create_restart_recovery_reuses_prepared_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    crash_point: str,
) -> None:
    repository = _repository(tmp_path)
    store, manager, application = _application(tmp_path)
    operation_id = str(uuid4())
    request = SessionCreateRequestDto(
        workspaceRoot=str(repository),
        executionMode="worktree",
        operationId=operation_id,
    )
    try:
        if crash_point == "prepared":
            def crash_after_prepare(*args: object, **kwargs: object) -> object:
                raise SystemExit("crash after durable prepare")

            monkeypatch.setattr(manager, "create_prepared", crash_after_prepare)
        elif crash_point == "git":
            original = manager.git.worktree_add

            def crash_after_git(*args: object, **kwargs: object) -> None:
                original(*args, **kwargs)
                raise SystemExit("crash after git worktree add")

            monkeypatch.setattr(manager.git, "worktree_add", crash_after_git)
        elif crash_point == "worktree":
            original = manager.repository.insert_worktree

            def crash_after_worktree(*args: object, **kwargs: object) -> object:
                original(*args, **kwargs)
                raise SystemExit("crash after Worktree persistence")

            monkeypatch.setattr(
                manager.repository, "insert_worktree", crash_after_worktree
            )
        else:
            original = application._repository.create_session

            def crash_after_session(*args: object, **kwargs: object) -> object:
                original(*args, **kwargs)
                raise SystemExit("crash after Session persistence")

            monkeypatch.setattr(
                application._repository, "create_session", crash_after_session
            )
        with pytest.raises(SystemExit):
            application.create(request)
    finally:
        store.close()

    restarted = SessionStore(tmp_path / "data")
    restarted.initialize()
    restarted_manager = WorktreeManager(
        restarted.database,
        managed_root=tmp_path / "managed-worktrees",
    )
    restarted_manager.recover()
    restarted_application = SessionApplication(
        restarted,
        scan_text=lambda value: value,
        worktree_manager=restarted_manager,
    )
    try:
        result = restarted_application.create(request).root
        assert restarted.connection.execute(
            "SELECT COUNT(*) FROM worktrees"
        ).fetchone()[0] == 1
        assert restarted.connection.execute(
            "SELECT COUNT(*) FROM sessions"
        ).fetchone()[0] == 1
        assert result["worktree"]["worktreeId"]
    finally:
        restarted.close()


@pytest.mark.parametrize("crash_point", ["git", "manager", "session"])
def test_session_delete_restart_recovery_finishes_only_durable_intent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    crash_point: str,
) -> None:
    repository = _repository(tmp_path)
    store, manager, application = _application(tmp_path)
    session = application.create(
        SessionCreateRequestDto(
            workspaceRoot=str(repository), executionMode="worktree"
        )
    ).root
    operation_id = str(uuid4())
    request = SessionDeleteRequestDto(
        sessionId=str(session["id"]),
        operationId=operation_id,
    )
    try:
        if crash_point == "git":
            original = manager.git.worktree_remove

            def crash_after_git(*args: object, **kwargs: object) -> None:
                original(*args, **kwargs)
                raise SystemExit("crash after git worktree removal")

            monkeypatch.setattr(manager.git, "worktree_remove", crash_after_git)
        elif crash_point == "manager":
            original = manager.delete

            def crash_after_delete(*args: object, **kwargs: object) -> object:
                original(*args, **kwargs)
                raise SystemExit("crash after Worktree delete")

            monkeypatch.setattr(manager, "delete", crash_after_delete)
        else:
            original = application._repository.delete_session

            def crash_after_session_delete(*args: object, **kwargs: object) -> object:
                original(*args, **kwargs)
                raise SystemExit("crash after Session delete")

            monkeypatch.setattr(
                application._repository,
                "delete_session",
                crash_after_session_delete,
            )
        with pytest.raises(SystemExit):
            application.delete(request)
    finally:
        store.close()

    restarted = SessionStore(tmp_path / "data")
    restarted.initialize()
    restarted_manager = WorktreeManager(
        restarted.database,
        managed_root=tmp_path / "managed-worktrees",
    )
    restarted_manager.recover()
    restarted_application = SessionApplication(
        restarted,
        scan_text=lambda value: value,
        worktree_manager=restarted_manager,
    )
    try:
        result = restarted_application.delete(request).root
        assert result == {"deletedSessionId": session["id"]}
        assert restarted.connection.execute(
            "SELECT COUNT(*) FROM sessions"
        ).fetchone()[0] == 0
        assert restarted.connection.execute(
            "SELECT COUNT(*) FROM worktrees WHERE state = 'deleted'"
        ).fetchone()[0] == 1
    finally:
        restarted.close()


def test_session_delete_rejects_operation_reuse_after_completed_lifecycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository(tmp_path)
    store, _manager, application = _application(tmp_path)
    first = application.create(
        SessionCreateRequestDto(
            workspaceRoot=str(repository), executionMode="worktree"
        )
    ).root
    second = application.create(
        SessionCreateRequestDto(
            workspaceRoot=str(repository), executionMode="worktree"
        )
    ).root
    operation_id = str(uuid4())
    first_request = SessionDeleteRequestDto(
        sessionId=str(first["id"]),
        operationId=operation_id,
    )
    original_record = application._record_delete_operation

    def crash_before_operation_result(*args: object, **kwargs: object) -> object:
        raise SystemExit("crash after completed lifecycle")

    monkeypatch.setattr(
        application,
        "_record_delete_operation",
        crash_before_operation_result,
    )
    with pytest.raises(SystemExit):
        application.delete(first_request)
    monkeypatch.setattr(application, "_record_delete_operation", original_record)
    store.close()

    restarted = SessionStore(tmp_path / "data")
    restarted.initialize()
    restarted_manager = WorktreeManager(
        restarted.database,
        managed_root=tmp_path / "managed-worktrees",
    )
    restarted_manager.recover()
    restarted_application = SessionApplication(
        restarted,
        scan_text=lambda value: value,
        worktree_manager=restarted_manager,
    )
    try:
        with pytest.raises(ApplicationError) as error:
            restarted_application.delete(
                SessionDeleteRequestDto(
                    sessionId=str(second["id"]),
                    operationId=operation_id,
                )
            )

        assert error.value.code == "OPERATION_ID_REUSED"
        assert restarted_application._repository.read_session_projection(
            str(second["id"])
        ) is not None
        second_worktree = restarted_manager.open(
            str(second["worktree"]["worktreeId"])
        )
        assert Path(second_worktree.worktree_root).is_dir()
    finally:
        restarted.close()


def test_session_delete_rejects_operation_reuse_after_worktree_deleted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository(tmp_path)
    store, _manager, application = _application(tmp_path)
    first = application.create(
        SessionCreateRequestDto(
            workspaceRoot=str(repository), executionMode="worktree"
        )
    ).root
    second = application.create(
        SessionCreateRequestDto(
            workspaceRoot=str(repository), executionMode="worktree"
        )
    ).root
    operation_id = str(uuid4())
    first_request = SessionDeleteRequestDto(
        sessionId=str(first["id"]),
        operationId=operation_id,
    )
    original_delete = application._repository.delete_session

    def crash_before_session_delete(*args: object, **kwargs: object) -> object:
        raise SystemExit("crash after worktree deleted")

    monkeypatch.setattr(
        application._repository,
        "delete_session",
        crash_before_session_delete,
    )
    with pytest.raises(SystemExit):
        application.delete(first_request)
    monkeypatch.setattr(application._repository, "delete_session", original_delete)
    store.close()

    restarted = SessionStore(tmp_path / "data")
    restarted.initialize()
    restarted_manager = WorktreeManager(
        restarted.database,
        managed_root=tmp_path / "managed-worktrees",
    )
    restarted_manager.recover()
    restarted_application = SessionApplication(
        restarted,
        scan_text=lambda value: value,
        worktree_manager=restarted_manager,
    )
    try:
        with pytest.raises(ApplicationError) as error:
            restarted_application.delete(
                SessionDeleteRequestDto(
                    sessionId=str(second["id"]),
                    operationId=operation_id,
                )
            )

        assert error.value.code == "OPERATION_ID_REUSED"
        assert restarted_application._repository.read_session_projection(
            str(second["id"])
        ) is not None
        second_worktree = restarted_manager.open(
            str(second["worktree"]["worktreeId"])
        )
        assert Path(second_worktree.worktree_root).is_dir()
    finally:
        restarted.close()


def test_deleted_worktree_without_delete_intent_does_not_delete_session(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    store, manager, application = _application(tmp_path)
    try:
        session = application.create(
            SessionCreateRequestDto(
                workspaceRoot=str(repository), executionMode="worktree"
            )
        ).root
        worktree_id = str(session["worktree"]["worktreeId"])
        manager.delete(worktree_id)
        request = SessionDeleteRequestDto(sessionId=str(session["id"]))
        with pytest.raises(Exception) as error:
            application.delete(request)
        assert getattr(error.value, "code", None) == "WORKTREE_RECOVERY_REQUIRED"
        assert store.connection.execute(
            "SELECT COUNT(*) FROM sessions"
        ).fetchone()[0] == 1
    finally:
        store.close()


@pytest.mark.parametrize(
    "crash_point", ["prepared", "git", "worktree", "session", "run", "action"]
)
def test_checkpoint_fork_restart_recovery_is_singleton(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    crash_point: str,
) -> None:
    repository = _repository(tmp_path)
    store, manager, sessions = _application(tmp_path)
    checkpoints = CheckpointApplication(
        store,
        store.checkpoint_repository(),
        worktree_manager=manager,
        retention=WorktreeRetentionService(store.database, manager),
    )
    parent = sessions.create(
        SessionCreateRequestDto(
            workspaceRoot=str(repository), executionMode="worktree"
        )
    ).root
    parent_run, _item = store.enqueue_run(str(parent["id"]), "fork")
    checkpoint = checkpoints.create(
        CheckpointCreateRequestDto(runId=str(parent_run["id"]))
    ).checkpoint
    operation_id = str(uuid4())
    request = CheckpointForkRequestDto(
        checkpointId=checkpoint.id,
        operationId=operation_id,
    )
    try:
        if crash_point == "prepared":
            def crash_after_prepare(*args: object, **kwargs: object) -> object:
                raise SystemExit("crash after fork durable prepare")

            monkeypatch.setattr(manager, "create_prepared", crash_after_prepare)
        elif crash_point == "git":
            original = manager.git.worktree_add

            def crash_after_git(*args: object, **kwargs: object) -> None:
                original(*args, **kwargs)
                raise SystemExit("crash after fork git worktree add")

            monkeypatch.setattr(manager.git, "worktree_add", crash_after_git)
        elif crash_point == "worktree":
            original = manager.repository.insert_worktree

            def crash_after_worktree(*args: object, **kwargs: object) -> object:
                original(*args, **kwargs)
                raise SystemExit("crash after fork Worktree persistence")

            monkeypatch.setattr(
                manager.repository, "insert_worktree", crash_after_worktree
            )
        elif crash_point == "session":
            original = checkpoints._sessions.create_session

            def crash_after_session(*args: object, **kwargs: object) -> object:
                original(*args, **kwargs)
                raise SystemExit("crash after fork Session persistence")

            monkeypatch.setattr(
                checkpoints._sessions, "create_session", crash_after_session
            )
        elif crash_point == "run":
            original = store.enqueue_run

            def crash_after_run(*args: object, **kwargs: object) -> object:
                original(*args, **kwargs)
                raise SystemExit("crash after fork Run persistence")

            monkeypatch.setattr(store, "enqueue_run", crash_after_run)
        else:
            original = checkpoints._repository.record_action

            def crash_after_action(*args: object, **kwargs: object) -> None:
                original(*args, **kwargs)
                raise SystemExit("crash after checkpoint action")

            monkeypatch.setattr(
                checkpoints._repository, "record_action", crash_after_action
            )
        with pytest.raises(SystemExit):
            checkpoints.fork(request)
    finally:
        store.close()

    restarted = SessionStore(tmp_path / "data")
    restarted.initialize()
    restarted_manager = WorktreeManager(
        restarted.database,
        managed_root=tmp_path / "managed-worktrees",
    )
    restarted_manager.recover()
    restarted_checkpoints = CheckpointApplication(
        restarted,
        restarted.checkpoint_repository(),
        worktree_manager=restarted_manager,
        retention=WorktreeRetentionService(restarted.database, restarted_manager),
    )
    try:
        result = restarted_checkpoints.fork(request)
        assert result.run.id
        assert restarted.connection.execute(
            "SELECT COUNT(*) FROM worktrees"
        ).fetchone()[0] == 2
        assert restarted.connection.execute(
            "SELECT COUNT(*) FROM sessions"
        ).fetchone()[0] == 2
        assert restarted.connection.execute(
            "SELECT COUNT(*) FROM runs"
        ).fetchone()[0] == 2
        assert restarted.connection.execute(
            "SELECT COUNT(*) FROM checkpoint_actions WHERE action = 'fork'"
        ).fetchone()[0] == 1
    finally:
        restarted.close()

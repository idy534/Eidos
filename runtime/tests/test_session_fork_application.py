from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest

from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.application.session_fork import SessionForkApplication
from eidos_runtime.application.session_lifecycle import SessionLifecycleCoordinator
from eidos_runtime.application.sessions import SessionApplication
from eidos_runtime.application.worktree_retention import WorktreeRetentionService
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.git.manager import WorktreeManager
from eidos_runtime.persistence.session_history import SessionHistoryRepository
from eidos_runtime.protocol.methods import SessionCreateRequestDto
from eidos_runtime.protocol.session_fork import SessionForkRequest
from eidos_runtime.runtime.events import RuntimeEvents


def completed_answer(store: SessionStore, session_id: str) -> str:
    run, _ = store.enqueue_run(session_id, "question")
    item_id = str(uuid4())
    with store.database.transaction() as connection:
        connection.execute(
            "INSERT INTO items (id,session_id,run_id,ordinal,kind,status,content,created_at,completed_at) "
            "VALUES (?,?,?,2,'assistant_message','completed','answer',2,2)",
            (item_id, session_id, run["id"]),
        )
        connection.execute(
            "UPDATE runs SET status='succeeded',completed_at=2 WHERE id=?", (run["id"],)
        )
    return item_id


def applications(tmp_path: Path):
    store = SessionStore(tmp_path / "data")
    store.initialize()
    manager = WorktreeManager(store.database, managed_root=tmp_path / "managed")
    retention = WorktreeRetentionService(store.database, manager)
    lifecycle = SessionLifecycleCoordinator()
    sessions = SessionApplication(
        store,
        scan_text=lambda text: text,
        worktree_manager=manager,
        retention=retention,
        lifecycle=lifecycle,
    )
    forks = SessionForkApplication(
        SessionHistoryRepository(store.database),
        sessions,
        lifecycle,
        RuntimeEvents(lambda _: None, store=store),
        manager,
        retention.snapshot_service,
    )
    return store, sessions, forks


def git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_projectless_fork_preserves_the_same_directory_and_remains_projectless(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("EIDOS_PROJECTLESS_ROOT", str(tmp_path / "conversations"))
    store, sessions, forks = applications(tmp_path)
    try:
        source = sessions.create(SessionCreateRequestDto()).root
        root = Path(source["workspaceRoot"])
        (root / "artifact.txt").write_text("keep")
        item = completed_answer(store, source["id"])
        request = SessionForkRequest(
            operationId=str(uuid4()),
            sourceSessionId=source["id"],
            sourceItemId=item,
            target={"kind": "same_workspace"},
        )
        target = forks.fork(request).session
        assert target.projectless
        assert target.project is None
        assert target.workspace_root == source["workspaceRoot"]
        assert target.forked_from.item_id == item
        assert forks.fork(request).session.id == target.id
        store.delete_session(source["id"])
        assert (root / "artifact.txt").read_text() == "keep"
        assert len(store.read_session_snapshot(target.id)["inheritedHistory"]) == 2
    finally:
        store.close()


@pytest.mark.integration
@pytest.mark.parametrize("execution_mode", ["local", "worktree"])
def test_worktree_fork_copies_current_changes_without_creating_a_run_or_modifying_source(
    tmp_path, execution_mode
):
    root = tmp_path / "repository"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "Eidos Tests")
    git(root, "config", "user.email", "tests@example.com")
    (root / "a.txt").write_text("base\n")
    git(root, "add", "a.txt")
    git(root, "commit", "-qm", "base")
    store, sessions, forks = applications(tmp_path)
    try:
        source = sessions.create(
            SessionCreateRequestDto(
                workspaceRoot=str(root), executionMode=execution_mode
            )
        ).root
        execution_root = Path(
            source["worktree"]["worktreeRoot"] if execution_mode == "worktree" else root
        )
        (execution_root / "a.txt").write_text("staged\n")
        git(execution_root, "add", "a.txt")
        (execution_root / "a.txt").write_text("working\n")
        (execution_root / "new.txt").write_text("untracked\n")
        item = completed_answer(store, source["id"])
        before = git(execution_root, "status", "--porcelain")
        request = SessionForkRequest(
            operationId=str(uuid4()),
            sourceSessionId=source["id"],
            sourceItemId=item,
            target={"kind": "new_worktree", "state": "current_workspace"},
        )
        target = forks.fork(request).session
        target_root = Path(target.worktree.worktree_root)
        assert target_root != execution_root
        assert (target_root / "a.txt").read_text() == "working\n"
        assert (target_root / "new.txt").read_text() == "untracked\n"
        assert git(target_root, "diff", "--cached") == git(
            execution_root, "diff", "--cached"
        )
        assert git(target_root, "diff") == git(execution_root, "diff")
        assert git(execution_root, "status", "--porcelain") == before
        assert forks.fork(request).session.id == target.id
        assert store.read_session_snapshot(target.id)["runs"] == []
    finally:
        store.close()


def test_fork_request_rejects_unknown_target_fields_and_unfinished_answers(tmp_path):
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SessionForkRequest(
            operationId=str(uuid4()),
            sourceSessionId="source",
            sourceItemId="item",
            target={"kind": "same_workspace", "approvalMode": "full_access"},
        )
    store, sessions, forks = applications(tmp_path)
    root = tmp_path / "workspace"
    root.mkdir()
    try:
        source = sessions.create(SessionCreateRequestDto(workspaceRoot=str(root))).root
        run, user = store.enqueue_run(source["id"], "pending")
        with pytest.raises(ApplicationError) as failure:
            forks.fork(
                SessionForkRequest(
                    operationId=str(uuid4()),
                    sourceSessionId=source["id"],
                    sourceItemId=user["id"],
                    target={"kind": "same_workspace"},
                )
            )
        assert failure.value.code == "INVALID_STATE"
        assert store.read_run(run["id"])["status"] == "queued"
    finally:
        store.close()


def test_fork_is_named_and_listed_before_its_first_run_and_keeps_its_title(tmp_path):
    from eidos_runtime.protocol.methods import (
        SessionRenameRequestDto,
        SessionListRequestDto,
    )
    from eidos_runtime.db.repositories.sessions import SessionRepository

    root = tmp_path / "workspace"
    root.mkdir()
    store, sessions, forks = applications(tmp_path)
    try:
        source = sessions.create(SessionCreateRequestDto(workspaceRoot=str(root))).root
        sessions.rename(
            SessionRenameRequestDto(sessionId=source["id"], title="原来的任务")
        )
        item = completed_answer(store, source["id"])
        target = forks.fork(
            SessionForkRequest(
                operationId=str(uuid4()),
                sourceSessionId=source["id"],
                sourceItemId=item,
                target={"kind": "same_workspace"},
            )
        ).session
        assert target.title == "原来的任务"
        assert target.task_status == "new"
        assert (
            next(
                session
                for session in sessions.list(SessionListRequestDto()).items
                if session.id == target.id
            ).title
            == target.title
        )
        assert store.read_session_snapshot(target.id)["runs"] == []
        assert target.id not in SessionRepository(
            store.database
        ).list_empty_session_ids_for_project(target.project.id)
        store.enqueue_run(target.id, "new question")
        SessionRepository(store.database).finish_title_generation_committed(
            target.id, "自动生成的新名称"
        )
        assert store.read_session(target.id)["title"] == "原来的任务"
        created = store.connection.execute(
            "SELECT payload_json FROM events WHERE session_id=? AND event_type='session.created'",
            (target.id,),
        ).fetchone()
        import json

        assert json.loads(created[0])["session"]["title"] == "原来的任务"
    finally:
        store.close()


def test_v26_fork_titles_are_backfilled_on_restart_without_renaming_existing_titles(
    tmp_path,
):
    import json
    from eidos_runtime.db.repositories.sessions import SessionRepository

    store, sessions, forks = applications(tmp_path)
    root = tmp_path / "workspace"
    root.mkdir()
    try:
        source = sessions.create(SessionCreateRequestDto(workspaceRoot=str(root))).root
        store.rename_session(source["id"], "原标题")
        item = completed_answer(store, source["id"])
        parent = forks.fork(
            SessionForkRequest(
                operationId=str(uuid4()),
                sourceSessionId=source["id"],
                sourceItemId=item,
                target={"kind": "same_workspace"},
            )
        ).session
        inherited_answer = store.read_session_snapshot(parent.id)["inheritedHistory"][
            -1
        ]["item"]["id"]
        child = forks.fork(
            SessionForkRequest(
                operationId=str(uuid4()),
                sourceSessionId=parent.id,
                sourceItemId=inherited_answer,
                target={"kind": "same_workspace"},
            )
        ).session
        renamed = forks.fork(
            SessionForkRequest(
                operationId=str(uuid4()),
                sourceSessionId=source["id"],
                sourceItemId=item,
                target={"kind": "same_workspace"},
            )
        ).session
        with store.database.transaction() as connection:
            connection.execute(
                "UPDATE sessions SET title=NULL WHERE id IN (?,?)",
                (parent.id, child.id),
            )
            connection.execute(
                "UPDATE sessions SET title='用户改名' WHERE id=?", (renamed.id,)
            )
            connection.execute("PRAGMA user_version=26")
        assert parent.id not in SessionRepository(
            store.database
        ).list_empty_session_ids_for_project(parent.project.id)
        data = store.database.data_directory
        store.close()
        store = SessionStore(data)
        store.initialize()
        assert store.health_state == "ready"
        assert store.read_session(parent.id)["title"] == "原标题"
        assert store.read_session(child.id)["title"] == "原标题"
        assert store.read_session(renamed.id)["title"] == "用户改名"
        changed = store.connection.execute(
            "SELECT payload_json FROM events WHERE event_type='session.title_updated' AND session_id=?",
            (child.id,),
        ).fetchall()
        assert [json.loads(row[0])["title"] for row in changed] == ["原标题"]
        assert store.connection.execute("PRAGMA user_version").fetchone()[0] == 27
    finally:
        store.close()


def test_v26_fork_title_migration_rolls_back_title_events_and_version_on_failure(
    tmp_path,
):
    from eidos_runtime.db.session_fork_migration import migrate_session_fork_titles
    from eidos_runtime.runtime.fault_injection import injected_faults

    store, sessions, forks = applications(tmp_path)
    root = tmp_path / "workspace"
    root.mkdir()
    try:
        source = sessions.create(SessionCreateRequestDto(workspaceRoot=str(root))).root
        target = forks.fork(
            SessionForkRequest(
                operationId=str(uuid4()),
                sourceSessionId=source["id"],
                sourceItemId=completed_answer(store, source["id"]),
                target={"kind": "same_workspace"},
            )
        ).session
        with store.database.transaction() as connection:
            connection.execute(
                "UPDATE sessions SET title=NULL WHERE id=?", (target.id,)
            )
            connection.execute("PRAGMA user_version=26")
        before = store.pending_outbox_count()

        class Failure:
            def hit(self, point):
                if point == "sqlite_append_event_failure":
                    raise sqlite3.OperationalError("fixture event failure")

        with injected_faults(Failure()), pytest.raises(sqlite3.OperationalError):
            migrate_session_fork_titles(store.connection)
        assert store.read_session(target.id).get("title") is None
        assert store.pending_outbox_count() == before
        assert store.connection.execute("PRAGMA user_version").fetchone()[0] == 26
    finally:
        store.close()


def test_v26_untitled_fork_with_deleted_source_gets_a_durable_fallback_title(tmp_path):
    from eidos_runtime.db.session_fork_migration import migrate_session_fork_titles

    root = tmp_path / "workspace"
    root.mkdir()
    store, sessions, forks = applications(tmp_path)
    try:
        source = sessions.create(SessionCreateRequestDto(workspaceRoot=str(root))).root
        target = forks.fork(
            SessionForkRequest(
                operationId=str(uuid4()),
                sourceSessionId=source["id"],
                sourceItemId=completed_answer(store, source["id"]),
                target={"kind": "same_workspace"},
            )
        ).session
        store.delete_session(source["id"])
        with store.database.transaction() as connection:
            connection.execute(
                "UPDATE sessions SET title=NULL WHERE id=?", (target.id,)
            )
            connection.execute("PRAGMA user_version=26")
        migrate_session_fork_titles(store.connection)
        assert store.read_session(target.id)["title"] == "新会话"
        assert len(store.read_session_snapshot(target.id)["inheritedHistory"]) == 2
    finally:
        store.close()


@pytest.mark.integration
def test_pending_worktree_fork_reuses_its_identity_and_snapshot_after_restart(
    tmp_path, monkeypatch
):
    from eidos_runtime.db.errors import StorageError
    from eidos_runtime.domain.worktree import (
        WorktreeLifecycleScope,
        WorktreeLifecycleState,
    )

    root = tmp_path / "repository"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "Eidos Tests")
    git(root, "config", "user.email", "tests@example.com")
    (root / "a.txt").write_text("base\n")
    git(root, "add", "a.txt")
    git(root, "commit", "-qm", "base")
    store, sessions, forks = applications(tmp_path)
    try:
        source = sessions.create(SessionCreateRequestDto(workspaceRoot=str(root))).root
        item = completed_answer(store, source["id"])
        (root / "a.txt").write_text("captured\n")
        request = SessionForkRequest(
            operationId=str(uuid4()),
            sourceSessionId=source["id"],
            sourceItemId=item,
            target={"kind": "new_worktree"},
        )
        with monkeypatch.context() as patch:

            def fail(*args, **kwargs):
                raise StorageError("fixture metadata failure")

            patch.setattr(forks.history, "fork", fail)
            with pytest.raises(ApplicationError) as failure:
                forks.fork(request)
            assert failure.value.code == "SESSION_FORK_FAILED"
        operation = forks.manager.lifecycle.read(
            WorktreeLifecycleScope.SESSION_CREATE, forks._environment_id(request)
        )
        assert operation.state == WorktreeLifecycleState.WORKTREE_CREATED
        assert (
            store.connection.execute("SELECT count(*) FROM sessions").fetchone()[0] == 1
        )
        with pytest.raises(ApplicationError) as conflict:
            forks.fork(
                request.model_copy(
                    update={
                        "target": SessionForkRequest(
                            operationId=request.operation_id,
                            sourceSessionId=source["id"],
                            sourceItemId=item,
                            target={"kind": "same_workspace"},
                        ).target
                    }
                )
            )
        assert conflict.value.code == "OPERATION_ID_REUSED"
        store.close()
        store, sessions, forks = applications(tmp_path)
        forks.manager.recover_lifecycle()
        (root / "a.txt").write_text("source changed later\n")
        target = forks.fork(request).session
        assert target.id == operation.session_id
        assert target.worktree.worktree_id == operation.worktree_id
        assert (
            Path(target.worktree.worktree_root) / "a.txt"
        ).read_text() == "captured\n"
        assert (root / "a.txt").read_text() == "source changed later\n"
        assert (
            forks.manager.lifecycle.read(
                WorktreeLifecycleScope.SESSION_CREATE, operation.operation_id
            ).state
            == WorktreeLifecycleState.COMPLETED
        )
        assert (
            store.connection.execute("SELECT count(*) FROM sessions").fetchone()[0] == 2
        )
    finally:
        store.close()


def test_v25_upgrade_is_additive_and_failed_migration_rolls_back(tmp_path, monkeypatch):
    from eidos_runtime.db.schema import V25_SCHEMA_SQL
    from eidos_runtime.db.session_fork_migration import migrate_session_forks

    connection = sqlite3.connect(tmp_path / "legacy.sqlite")
    connection.executescript(V25_SCHEMA_SQL + "PRAGMA user_version=25;")
    import eidos_runtime.db.session_fork_migration as migration

    original = migration.SESSION_FORK_SCHEMA_SQL
    monkeypatch.setattr(
        migration, "SESSION_FORK_SCHEMA_SQL", original + "INVALID MIGRATION;"
    )
    with pytest.raises(sqlite3.Error):
        migrate_session_forks(connection)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 25
    assert (
        connection.execute(
            "SELECT name FROM sqlite_master WHERE name='session_forks'"
        ).fetchone()
        is None
    )
    monkeypatch.setattr(migration, "SESSION_FORK_SCHEMA_SQL", original)
    migrate_session_forks(connection)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 26
    assert "request_hash" in {
        row[1]
        for row in connection.execute(
            "PRAGMA table_info(worktree_lifecycle_operations)"
        )
    }
    connection.close()

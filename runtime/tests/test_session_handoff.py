from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.application.sessions import SessionApplication
from eidos_runtime.db.schema import SCHEMA_VERSION
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.git import WorktreeManager
from eidos_runtime.protocol.methods import (
    SessionCreateRequestDto,
    SessionCreateBranchRequestDto,
    SessionGitDiffRequestDto,
    SessionGitStatusRequestDto,
    SessionHandoffRequestDto,
    SessionDeleteRequestDto,
)
from eidos_runtime.domain.handoff import SessionHandoffState


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
    return repository


def _ignored_handoff_repository(tmp_path: Path) -> Path:
    repository = _repository(tmp_path)
    (repository / ".gitignore").write_text(
        ".env\n"
        ".env.local\n"
        ".worktreeinclude\n"
        "node_modules/\n"
        "EIDOS.override.md\n"
        "AGENTS.override.md\n",
        encoding="utf-8",
    )
    _git(repository, "add", ".gitignore")
    _git(repository, "commit", "-qm", "ignore local Worktree environment")
    (repository / ".worktreeinclude").write_text(
        ".env\n.env.local\nnode_modules/**\n", encoding="utf-8"
    )
    (repository / ".env").write_text("TOKEN=local\n", encoding="utf-8")
    (repository / ".env.local").write_text("MODE=test\n", encoding="utf-8")
    (repository / "node_modules").mkdir()
    (repository / "node_modules" / "example").write_text(
        "installed\n", encoding="utf-8"
    )
    (repository / "EIDOS.override.md").write_text(
        "Eidos override\n", encoding="utf-8"
    )
    (repository / "AGENTS.override.md").write_text(
        "Agents override\n", encoding="utf-8"
    )
    return repository


def _setup(tmp_path: Path) -> tuple[
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


def test_schema_and_handoff_request_contract_are_phase3c_shapes(
    tmp_path: Path,
) -> None:
    store = SessionStore(tmp_path / "data")
    store.initialize()
    try:
        assert SCHEMA_VERSION == 3
        columns = {
            str(row[1])
            for row in store.connection.execute("PRAGMA table_info(sessions)")
        }
        assert "associated_worktree_id" in columns
        assert store.connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name = 'session_handoff_operations'"
        ).fetchone() is not None
    finally:
        store.close()

    request = SessionHandoffRequestDto(
        sessionId="00000000-0000-0000-0000-000000000001",
        target="local",
        operationId="00000000-0000-0000-0000-000000000002",
    )
    assert request.target == "local"


def test_local_session_git_review_reads_the_active_local_checkout(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    store, _manager, application = _setup(tmp_path)
    try:
        session = application.create(
            SessionCreateRequestDto(workspaceRoot=str(repository))
        ).root
        status = application.git_status(
            SessionGitStatusRequestDto(sessionId=str(session["id"]))
        ).root
        diff = application.git_diff(
            SessionGitDiffRequestDto(
                sessionId=str(session["id"]), scope="head"
            )
        ).root
        assert status["worktreeId"] is None
        assert status["baseRef"] is None
        assert status["baseCommit"] is None
        assert diff["baseCommit"] == status["head"]
    finally:
        store.close()


def test_local_to_worktree_and_back_reuses_the_same_session_and_worktree(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    store, manager, application = _setup(tmp_path)
    try:
        session = application.create(
            SessionCreateRequestDto(workspaceRoot=str(repository))
        ).root
        session_id = str(session["id"])

        worktree_result = application.handoff(
            SessionHandoffRequestDto(
                sessionId=session_id,
                target="worktree",
                operationId="00000000-0000-0000-0000-000000000010",
            )
        ).root
        first_worktree_id = str(worktree_result["worktree"]["worktreeId"])
        assert worktree_result["id"] == session_id
        assert worktree_result["sessionId"] == session_id
        assert worktree_result["workspaceRoot"] == str(repository.resolve())
        assert worktree_result["worktreeId"] == first_worktree_id
        assert worktree_result["executionMode"] == "worktree"

        replayed = application.handoff(
            SessionHandoffRequestDto(
                sessionId=session_id,
                target="worktree",
                operationId="00000000-0000-0000-0000-000000000010",
            )
        ).root
        assert replayed["id"] == session_id
        assert replayed["worktreeId"] == first_worktree_id

        local_result = application.handoff(
            SessionHandoffRequestDto(
                sessionId=session_id,
                target="local",
                operationId="00000000-0000-0000-0000-000000000011",
            )
        ).root
        assert local_result["id"] == session_id
        assert local_result["executionMode"] == "local"
        assert local_result["sessionId"] == session_id
        assert local_result["worktreeId"] is None
        assert local_result.get("worktree") is None
        assert local_result["associatedWorktreeId"] == first_worktree_id

        returned = application.handoff(
            SessionHandoffRequestDto(
                sessionId=session_id,
                target="worktree",
                operationId="00000000-0000-0000-0000-000000000012",
            )
        ).root
        assert returned["id"] == session_id
        assert returned["worktree"]["worktreeId"] == first_worktree_id
        assert manager.repository.list_worktrees()[0].id == first_worktree_id
    finally:
        store.close()


@pytest.mark.parametrize(
    "run_status", ["queued", "running", "waiting_approval", "finalizing"]
)
def test_handoff_rejects_an_active_run_without_changing_binding(
    tmp_path: Path,
    run_status: str,
) -> None:
    repository = _repository(tmp_path)
    store, _manager, application = _setup(tmp_path)
    try:
        session = application.create(
            SessionCreateRequestDto(workspaceRoot=str(repository))
        ).root
        run, _item = store.enqueue_run(str(session["id"]), "active")
        if run_status != "queued":
            with store.connection:
                store.connection.execute(
                    "UPDATE runs SET status = ? WHERE id = ?",
                    (run_status, run["id"]),
                )

        with pytest.raises(ApplicationError) as error:
            application.handoff(
                SessionHandoffRequestDto(
                    sessionId=str(session["id"]),
                    target="worktree",
                    operationId="00000000-0000-0000-0000-000000000020",
                )
            )
        assert error.value.code == "SESSION_HAS_ACTIVE_RUN"
        assert tuple(store.connection.execute(
            "SELECT execution_mode, worktree_id, associated_worktree_id "
            "FROM sessions WHERE id = ?",
            (session["id"],),
        ).fetchone()) == ("local", None, None)
    finally:
        store.close()


def test_local_dirty_state_transfers_to_a_new_worktree(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    (repository / "binary.bin").write_bytes(b"before\x00binary\n")
    _git(repository, "add", "binary.bin")
    _git(repository, "commit", "-qm", "binary")
    (repository / "README.md").write_text("unstaged\n", encoding="utf-8")
    (repository / "staged.txt").write_text("staged\n", encoding="utf-8")
    _git(repository, "add", "staged.txt")
    (repository / "untracked.txt").write_text("untracked\n", encoding="utf-8")
    (repository / "binary.bin").write_bytes(b"after\x00binary\x00\n")

    store, manager, application = _setup(tmp_path)
    try:
        session = application.create(
            SessionCreateRequestDto(workspaceRoot=str(repository))
        ).root
        result = application.handoff(
            SessionHandoffRequestDto(
                sessionId=str(session["id"]),
                target="worktree",
                operationId="00000000-0000-0000-0000-000000000030",
            )
        ).root
        worktree = manager.repository.read_worktree(
            str(result["worktree"]["worktreeId"])
        )
        assert worktree is not None
        root = Path(worktree.worktree_root)
        assert (root / "README.md").read_text(encoding="utf-8") == "unstaged\n"
        assert (root / "staged.txt").read_text(encoding="utf-8") == "staged\n"
        assert (root / "untracked.txt").read_text(encoding="utf-8") == "untracked\n"
        assert (root / "binary.bin").read_bytes() == b"after\x00binary\x00\n"
        status = manager.source_snapshot(root, include_local_changes=True).status
        assert status.staged_paths == ("staged.txt",)
        assert status.untracked_paths == ("untracked.txt",)
        assert "README.md" in status.unstaged_paths
        assert "binary.bin" in status.unstaged_paths
    finally:
        store.close()


def test_handoff_keeps_ignored_worktree_environment_across_round_trip(
    tmp_path: Path,
) -> None:
    repository = _ignored_handoff_repository(tmp_path)
    store, manager, application = _setup(tmp_path)
    try:
        session = application.create(
            SessionCreateRequestDto(workspaceRoot=str(repository))
        ).root
        worktree_result = application.handoff(
            SessionHandoffRequestDto(
                sessionId=str(session["id"]),
                target="worktree",
                operationId="00000000-0000-0000-0000-000000000035",
            )
        ).root
        worktree = manager.repository.read_worktree(
            str(worktree_result["worktree"]["worktreeId"])
        )
        assert worktree is not None
        worktree_root = Path(worktree.worktree_root)
        for relative in (
            ".env",
            ".env.local",
            "node_modules/example",
            "EIDOS.override.md",
            "AGENTS.override.md",
        ):
            assert (worktree_root / relative).exists()

        (worktree_root / "README.md").write_text(
            "worktree tracked change\n", encoding="utf-8"
        )
        (worktree_root / "untracked.txt").write_text(
            "worktree untracked change\n", encoding="utf-8"
        )

        local_result = application.handoff(
            SessionHandoffRequestDto(
                sessionId=str(session["id"]),
                target="local",
                operationId="00000000-0000-0000-0000-000000000036",
            )
        ).root
        assert local_result["executionMode"] == "local"
        assert (repository / "README.md").read_text(encoding="utf-8") == (
            "worktree tracked change\n"
        )
        assert (repository / "untracked.txt").read_text(encoding="utf-8") == (
            "worktree untracked change\n"
        )
        assert not (worktree_root / "untracked.txt").exists()
        assert (worktree_root / "README.md").read_text(encoding="utf-8") == (
            "main\n"
        )
        for relative in (
            ".env",
            ".env.local",
            "node_modules/example",
            "EIDOS.override.md",
            "AGENTS.override.md",
        ):
            assert (worktree_root / relative).exists()

        returned = application.handoff(
            SessionHandoffRequestDto(
                sessionId=str(session["id"]),
                target="worktree",
                operationId="00000000-0000-0000-0000-000000000037",
            )
        ).root
        assert returned["worktree"]["worktreeId"] == worktree.id
        for relative in (
            ".env",
            ".env.local",
            "node_modules/example",
            "EIDOS.override.md",
            "AGENTS.override.md",
        ):
            assert (worktree_root / relative).exists()
    finally:
        store.close()


def test_worktree_commits_and_dirty_state_move_to_local_detached_head(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    store, manager, application = _setup(tmp_path)
    try:
        session = application.create(
            SessionCreateRequestDto(workspaceRoot=str(repository))
        ).root
        worktree_result = application.handoff(
            SessionHandoffRequestDto(
                sessionId=str(session["id"]),
                target="worktree",
                operationId="00000000-0000-0000-0000-000000000040",
            )
        ).root
        worktree = manager.repository.read_worktree(
            str(worktree_result["worktree"]["worktreeId"])
        )
        assert worktree is not None
        root = Path(worktree.worktree_root)
        (root / "committed.txt").write_text("committed\n", encoding="utf-8")
        _git(root, "add", "committed.txt")
        _git(root, "commit", "-qm", "worktree commit")
        committed_head = _git(root, "rev-parse", "HEAD")
        (root / "dirty.txt").write_text("dirty\n", encoding="utf-8")

        local_result = application.handoff(
            SessionHandoffRequestDto(
                sessionId=str(session["id"]),
                target="local",
                operationId="00000000-0000-0000-0000-000000000041",
            )
        ).root
        assert local_result["executionMode"] == "local"
        assert _git(repository, "rev-parse", "HEAD") == committed_head
        assert _git(repository, "branch", "--show-current") == ""
        assert (repository / "committed.txt").read_text(encoding="utf-8") == "committed\n"
        assert (repository / "dirty.txt").read_text(encoding="utf-8") == "dirty\n"
        assert not Path(worktree.worktree_root).joinpath("dirty.txt").exists()
    finally:
        store.close()


def test_handoff_only_changes_workspace_identity_for_new_runs(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    store, manager, application = _setup(tmp_path)
    try:
        session = application.create(
            SessionCreateRequestDto(workspaceRoot=str(repository))
        ).root
        worktree_session = application.handoff(
            SessionHandoffRequestDto(
                sessionId=str(session["id"]),
                target="worktree",
                operationId="00000000-0000-0000-0000-000000000042",
            )
        ).root
        worktree_root = Path(worktree_session["worktree"]["worktreeRoot"])
        old_run, _old_item = store.create_run(str(session["id"]), "old")
        old_identity = store.workspace_for_run(str(old_run["id"]))
        assert old_identity.path == worktree_root.resolve()
        store.fail_run(str(old_run["id"]), "test_complete")

        local_session = application.handoff(
            SessionHandoffRequestDto(
                sessionId=str(session["id"]),
                target="local",
                operationId="00000000-0000-0000-0000-000000000043",
            )
        ).root
        assert local_session["executionMode"] == "local"
        new_run, _new_item = store.create_run(str(session["id"]), "new")
        new_identity = store.workspace_for_run(str(new_run["id"]))
        assert new_identity.path == repository.resolve()
        assert store.workspace_for_run(str(old_run["id"])) == old_identity
        store.fail_run(str(new_run["id"]), "test_complete")
        assert manager.repository.read_worktree(
            str(worktree_session["worktree"]["worktreeId"])
        ) is not None
    finally:
        store.close()


def test_user_branch_is_released_before_local_acquires_it_and_can_create_another_branch(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    store, manager, application = _setup(tmp_path)
    try:
        session = application.create(
            SessionCreateRequestDto(workspaceRoot=str(repository))
        ).root
        worktree_result = application.handoff(
            SessionHandoffRequestDto(
                sessionId=str(session["id"]),
                target="worktree",
                operationId="00000000-0000-0000-0000-000000000050",
            )
        ).root
        worktree_id = str(worktree_result["worktree"]["worktreeId"])
        branch_result = application.create_branch(
            SessionCreateBranchRequestDto(
                sessionId=str(session["id"]),
                branch="feature/handoff",
                operationId="00000000-0000-0000-0000-000000000051",
            )
        ).root
        assert branch_result["branch"] == "feature/handoff"
        worktree = manager.repository.read_worktree(worktree_id)
        assert worktree is not None
        worktree_root = Path(worktree.worktree_root)
        (worktree_root / "feature.txt").write_text(
            "feature commit\n", encoding="utf-8"
        )
        _git(worktree_root, "add", "feature.txt")
        _git(worktree_root, "commit", "-qm", "feature commit")
        feature_head = _git(worktree_root, "rev-parse", "HEAD")
        local_result = application.handoff(
            SessionHandoffRequestDto(
                sessionId=str(session["id"]),
                target="local",
                operationId="00000000-0000-0000-0000-000000000052",
            )
        ).root
        assert local_result["executionMode"] == "local"
        assert _git(repository, "branch", "--show-current") == "feature/handoff"
        worktree = manager.repository.read_worktree(worktree_id)
        assert worktree is not None
        assert worktree.branch is None
        assert worktree.checkout_branch is None
        assert worktree.branch_ownership.value == "none"
        assert _git(Path(worktree.worktree_root), "branch", "--show-current") == ""
        assert _git(repository, "rev-parse", "feature/handoff") == _git(
            Path(worktree.worktree_root), "rev-parse", "HEAD"
        )
        assert _git(repository, "rev-parse", "feature/handoff") == feature_head

        (repository / "local.txt").write_text("local commit\n", encoding="utf-8")
        _git(repository, "add", "local.txt")
        _git(repository, "commit", "-qm", "local commit")
        latest_head = _git(repository, "rev-parse", "HEAD")

        returned = application.handoff(
            SessionHandoffRequestDto(
                sessionId=str(session["id"]),
                target="worktree",
                operationId="00000000-0000-0000-0000-000000000053",
            )
        ).root
        assert returned["worktree"]["worktreeId"] == worktree_id
        returned_worktree = manager.repository.read_worktree(worktree_id)
        assert returned_worktree is not None
        assert returned_worktree.branch is None
        assert returned_worktree.checkout_branch is None
        assert returned_worktree.branch_ownership.value == "none"
        assert _git(Path(returned_worktree.worktree_root), "branch", "--show-current") == ""
        assert _git(Path(returned_worktree.worktree_root), "rev-parse", "HEAD") == latest_head
        assert _git(repository, "branch", "--show-current") == "feature/handoff"

        next_branch_result = application.create_branch(
            SessionCreateBranchRequestDto(
                sessionId=str(session["id"]),
                branch="feature/next",
                operationId="00000000-0000-0000-0000-000000000054",
            )
        ).root
        assert next_branch_result["branch"] == "feature/next"
        assert _git(
            repository,
            "show-ref",
            "--verify",
            "refs/heads/feature/handoff",
        )
        assert _git(
            Path(returned_worktree.worktree_root),
            "show-ref",
            "--verify",
            "refs/heads/feature/next",
        )
    finally:
        store.close()


def test_local_target_conflict_does_not_modify_either_checkout(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    store, manager, application = _setup(tmp_path)
    try:
        session = application.create(
            SessionCreateRequestDto(workspaceRoot=str(repository))
        ).root
        worktree_result = application.handoff(
            SessionHandoffRequestDto(
                sessionId=str(session["id"]),
                target="worktree",
                operationId="00000000-0000-0000-0000-000000000060",
            )
        ).root
        worktree = manager.repository.read_worktree(
            str(worktree_result["worktree"]["worktreeId"])
        )
        assert worktree is not None
        root = Path(worktree.worktree_root)
        (root / "README.md").write_text("worktree\n", encoding="utf-8")
        (repository / "README.md").write_text("local\n", encoding="utf-8")
        before_worktree = root.joinpath("README.md").read_text(encoding="utf-8")
        before_local = repository.joinpath("README.md").read_text(encoding="utf-8")
        with pytest.raises(ApplicationError) as error:
            application.handoff(
                SessionHandoffRequestDto(
                    sessionId=str(session["id"]),
                    target="local",
                    operationId="00000000-0000-0000-0000-000000000061",
                )
            )
        assert error.value.code == "HANDOFF_LOCAL_CONFLICT"
        assert root.joinpath("README.md").read_text(encoding="utf-8") == before_worktree
        assert repository.joinpath("README.md").read_text(encoding="utf-8") == before_local
    finally:
        store.close()


def test_inactive_worktree_drift_blocks_return_without_creating_a_second_worktree(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    store, manager, application = _setup(tmp_path)
    try:
        session = application.create(
            SessionCreateRequestDto(workspaceRoot=str(repository))
        ).root
        worktree_result = application.handoff(
            SessionHandoffRequestDto(
                sessionId=str(session["id"]),
                target="worktree",
                operationId="00000000-0000-0000-0000-000000000070",
            )
        ).root
        worktree_id = str(worktree_result["worktree"]["worktreeId"])
        application.handoff(
            SessionHandoffRequestDto(
                sessionId=str(session["id"]),
                target="local",
                operationId="00000000-0000-0000-0000-000000000071",
            )
        )
        worktree = manager.repository.read_worktree(worktree_id)
        assert worktree is not None
        (Path(worktree.worktree_root) / "external.txt").write_text(
            "external\n", encoding="utf-8"
        )
        with pytest.raises(ApplicationError) as error:
            application.handoff(
                SessionHandoffRequestDto(
                    sessionId=str(session["id"]),
                    target="worktree",
                    operationId="00000000-0000-0000-0000-000000000072",
                )
            )
        assert error.value.code == "HANDOFF_TARGET_CHANGED"
        assert manager.repository.list_worktrees()[0].id == worktree_id
        assert store.connection.execute(
            "SELECT execution_mode, worktree_id FROM sessions WHERE id = ?",
            (session["id"],),
        ).fetchone()[0] == "local"
    finally:
        store.close()


def test_return_to_deleted_associated_worktree_requires_restore(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    store, manager, application = _setup(tmp_path)
    try:
        session = application.create(
            SessionCreateRequestDto(workspaceRoot=str(repository))
        ).root
        worktree_result = application.handoff(
            SessionHandoffRequestDto(
                sessionId=str(session["id"]),
                target="worktree",
                operationId="00000000-0000-0000-0000-000000000073",
            )
        ).root
        worktree_id = str(worktree_result["worktree"]["worktreeId"])
        application.handoff(
            SessionHandoffRequestDto(
                sessionId=str(session["id"]),
                target="local",
                operationId="00000000-0000-0000-0000-000000000074",
            )
        )
        manager.delete(worktree_id)
        with pytest.raises(ApplicationError) as error:
            application.handoff(
                SessionHandoffRequestDto(
                    sessionId=str(session["id"]),
                    target="worktree",
                    operationId="00000000-0000-0000-0000-000000000075",
                )
            )
        assert error.value.code == "WORKTREE_RESTORE_REQUIRED"
    finally:
        store.close()


def test_deleting_a_local_session_cleans_its_inactive_associated_worktree(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    store, manager, application = _setup(tmp_path)
    try:
        session = application.create(
            SessionCreateRequestDto(workspaceRoot=str(repository))
        ).root
        worktree_result = application.handoff(
            SessionHandoffRequestDto(
                sessionId=str(session["id"]),
                target="worktree",
                operationId="00000000-0000-0000-0000-000000000080",
            )
        ).root
        worktree_id = str(worktree_result["worktree"]["worktreeId"])
        application.create_branch(
            SessionCreateBranchRequestDto(
                sessionId=str(session["id"]),
                branch="feature/delete-preserves-user-branch",
                operationId="00000000-0000-0000-0000-000000000083",
            )
        )
        application.handoff(
            SessionHandoffRequestDto(
                sessionId=str(session["id"]),
                target="local",
                operationId="00000000-0000-0000-0000-000000000081",
            )
        )
        deleted = application.delete(
            SessionDeleteRequestDto(
                sessionId=str(session["id"]),
                operationId="00000000-0000-0000-0000-000000000082",
            )
        ).root
        assert deleted["deletedSessionId"] == session["id"]
        worktree = manager.repository.read_worktree(worktree_id)
        assert worktree is not None
        assert worktree.state.value == "deleted"
        assert not Path(worktree.worktree_root).exists()
        assert _git(
            repository,
            "show-ref",
            "--verify",
            "refs/heads/feature/delete-preserves-user-branch",
        )
        assert store.connection.execute(
            "SELECT id FROM sessions WHERE id = ?", (session["id"],)
        ).fetchone() is None
    finally:
        store.close()


@pytest.mark.parametrize("crash_point", [
    "prepared",
    "git",
    "binding",
    "rebound",
])
def test_local_to_worktree_handoff_recovers_after_each_durable_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    crash_point: str,
) -> None:
    repository = _repository(tmp_path)
    store, _manager, application = _setup(tmp_path)
    operation_id = "00000000-0000-0000-0000-000000000090"
    try:
        session = application.create(
            SessionCreateRequestDto(workspaceRoot=str(repository))
        ).root
        handoffs = store.session_handoff_repository()
        if crash_point in {"prepared", "git", "rebound"}:
            original_update = handoffs.update_state

            def crash_update(*args: object, **kwargs: object) -> object:
                state = args[2] if len(args) > 2 else kwargs.get("state")
                if (
                    crash_point == "prepared"
                    and state is SessionHandoffState.SOURCE_CAPTURED
                ) or (
                    crash_point == "git"
                    and state is SessionHandoffState.TARGET_MATERIALIZED
                ) or (
                    crash_point == "rebound"
                    and state is SessionHandoffState.SESSION_REBOUND
                ):
                    raise SystemExit(f"crash at {crash_point}")
                return original_update(*args, **kwargs)

            monkeypatch.setattr(handoffs, "update_state", crash_update)
        elif crash_point == "binding":
            original_binding = application._repository.update_execution_binding

            def crash_binding(*args: object, **kwargs: object) -> object:
                original_binding(*args, **kwargs)
                raise SystemExit("crash after Session binding")

            monkeypatch.setattr(
                application._repository,
                "update_execution_binding",
                crash_binding,
            )

        with pytest.raises(SystemExit):
            application.handoff(
                SessionHandoffRequestDto(
                    sessionId=str(session["id"]),
                    target="worktree",
                    operationId=operation_id,
                )
            )
        operation = handoffs.read(
            "session/handoff-worktree", operation_id
        )
        assert operation is not None
        assert operation.state is not SessionHandoffState.COMPLETED
    finally:
        store.close()

    restarted, restarted_manager, restarted_application = _setup(tmp_path)
    try:
        restarted_application.recover_handoffs()
        operation = restarted.session_handoff_repository().read(
            "session/handoff-worktree", operation_id
        )
        assert operation is not None
        assert operation.state is SessionHandoffState.COMPLETED
        recovered = restarted_application._repository.read_session(
            str(session["id"])
        )
        assert recovered is not None
        assert recovered.worktree_id == operation.associated_worktree_id
        assert restarted_manager.repository.read_worktree(
            operation.associated_worktree_id
        ) is not None
    finally:
        restarted.close()


def test_worktree_to_local_recovery_accepts_detached_source_after_git_transfer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository(tmp_path)
    store, manager, application = _setup(tmp_path)
    operation_id = "00000000-0000-0000-0000-000000000101"
    try:
        session = application.create(
            SessionCreateRequestDto(workspaceRoot=str(repository))
        ).root
        worktree_result = application.handoff(
            SessionHandoffRequestDto(
                sessionId=str(session["id"]),
                target="worktree",
                operationId="00000000-0000-0000-0000-000000000100",
            )
        ).root
        worktree = manager.repository.read_worktree(
            str(worktree_result["worktree"]["worktreeId"])
        )
        assert worktree is not None
        application.create_branch(
            SessionCreateBranchRequestDto(
                sessionId=str(session["id"]),
                branch="feature/recovery",
                operationId="00000000-0000-0000-0000-000000000102",
            )
        )
        (Path(worktree.worktree_root) / "recovered.txt").write_text(
            "recovered\n", encoding="utf-8"
        )
        handoffs = store.session_handoff_repository()
        original_update = handoffs.update_state

        def crash_after_transfer(*args: object, **kwargs: object) -> object:
            state = args[2] if len(args) > 2 else kwargs.get("state")
            if state is SessionHandoffState.TARGET_MATERIALIZED:
                raise SystemExit("crash after Worktree to Local Git transfer")
            return original_update(*args, **kwargs)

        monkeypatch.setattr(handoffs, "update_state", crash_after_transfer)
        with pytest.raises(SystemExit):
            application.handoff(
                SessionHandoffRequestDto(
                    sessionId=str(session["id"]),
                    target="local",
                    operationId=operation_id,
                )
            )
    finally:
        store.close()

    restarted, restarted_manager, restarted_application = _setup(tmp_path)
    try:
        restarted_application.recover_handoffs()
        recovered = restarted_application._repository.read_session(
            str(session["id"])
        )
        assert recovered is not None
        assert recovered.execution_mode.value == "local"
        assert recovered.worktree_id is None
        assert recovered.associated_worktree_id == str(
            worktree_result["worktree"]["worktreeId"]
        )
        assert _git(repository, "branch", "--show-current") == "feature/recovery"
        assert (repository / "recovered.txt").read_text(encoding="utf-8") == (
            "recovered\n"
        )
        source_after = restarted_manager.source_snapshot(
            Path(worktree.worktree_root), include_local_changes=True
        )
        assert source_after.branch is None
        assert not source_after.status.dirty
    finally:
        restarted.close()


def test_user_branch_metadata_release_recovers_after_local_checkout_crash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository(tmp_path)
    store, manager, application = _setup(tmp_path)
    operation_id = "00000000-0000-0000-0000-000000000103"
    try:
        session = application.create(
            SessionCreateRequestDto(workspaceRoot=str(repository))
        ).root
        worktree_result = application.handoff(
            SessionHandoffRequestDto(
                sessionId=str(session["id"]),
                target="worktree",
                operationId="00000000-0000-0000-0000-000000000104",
            )
        ).root
        worktree_id = str(worktree_result["worktree"]["worktreeId"])
        application.create_branch(
            SessionCreateBranchRequestDto(
                sessionId=str(session["id"]),
                branch="feature/recovery-release",
                operationId="00000000-0000-0000-0000-000000000105",
            )
        )
        worktree = manager.repository.read_worktree(worktree_id)
        assert worktree is not None
        expected_head = _git(Path(worktree.worktree_root), "rev-parse", "HEAD")

        def crash_before_metadata_release(*args: object, **kwargs: object) -> object:
            raise SystemExit("crash before user branch metadata release")

        monkeypatch.setattr(
            manager,
            "release_user_branch_after_handoff",
            crash_before_metadata_release,
        )
        with pytest.raises(SystemExit):
            application.handoff(
                SessionHandoffRequestDto(
                    sessionId=str(session["id"]),
                    target="local",
                    operationId=operation_id,
                )
            )
        assert _git(repository, "branch", "--show-current") == (
            "feature/recovery-release"
        )
        assert _git(repository, "rev-parse", "HEAD") == expected_head
    finally:
        store.close()

    restarted, restarted_manager, restarted_application = _setup(tmp_path)
    try:
        restarted_application.recover_handoffs()
        recovered_worktree = restarted_manager.repository.read_worktree(worktree_id)
        assert recovered_worktree is not None
        assert recovered_worktree.branch is None
        assert recovered_worktree.checkout_branch is None
        assert recovered_worktree.branch_ownership.value == "none"
        assert _git(repository, "branch", "--show-current") == (
            "feature/recovery-release"
        )
        assert _git(
            repository,
            "show-ref",
            "--verify",
            "refs/heads/feature/recovery-release",
        )
        recovered_session = restarted_application._repository.read_session(
            str(session["id"])
        )
        assert recovered_session is not None
        assert recovered_session.execution_mode.value == "local"
        assert recovered_session.worktree_id is None
        assert recovered_session.associated_worktree_id == worktree_id
    finally:
        restarted.close()

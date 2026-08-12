from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.application.sessions import SessionApplication
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.domain.worktree import (
    WorktreeLifecycleScope,
    WorktreeLifecycleState,
)
from eidos_runtime.git import DulwichGitBackend, WorktreeManager
from eidos_runtime.git.errors import GitCommandFailedError, WorktreeError
from eidos_runtime.git.materialization import materialize_worktree_include
from eidos_runtime.protocol.methods import (
    SessionCreateBranchRequestDto,
    SessionCreateRequestDto,
    SessionDeleteRequestDto,
)
from git_backend_fakes import FakeGitBackend


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
    (repository / ".gitignore").write_text(
        ".env*\nconfig/local.json\n", encoding="utf-8"
    )
    (repository / "tracked.txt").write_text("base\n", encoding="utf-8")
    (repository / "staged.txt").write_text("staged base\n", encoding="utf-8")
    (repository / "deleted.txt").write_text("delete me\n", encoding="utf-8")
    (repository / "binary.dat").write_bytes(b"binary base\x00\x01\n")
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "initial")
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


def _always_ignored(_relative: str) -> bool:
    return True


def _include_scenario(tmp_path: Path) -> Path:
    repository = _repository(tmp_path)
    (repository / ".worktreeinclude").write_text(
        "tracked.txt\nuntracked.txt\n.env\n", encoding="utf-8"
    )
    (repository / ".env").write_text("TOKEN=local\n", encoding="utf-8")
    (repository / "tracked.txt").write_text("modified\n", encoding="utf-8")
    (repository / "untracked.txt").write_text("untracked\n", encoding="utf-8")
    return repository


def _ignored_override_repository(tmp_path: Path) -> Path:
    repository = _repository(tmp_path)
    (repository / ".gitignore").write_text(
        ".env*\nconfig/local.json\nEIDOS.override.md\nAGENTS.override.md\n",
        encoding="utf-8",
    )
    _git(repository, "add", ".gitignore")
    _git(repository, "commit", "-qm", "ignore local rule overrides")
    return repository


def test_worktree_session_transfers_include_files_and_all_local_changes(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    (repository / ".worktreeinclude").write_text(
        ".env\n.env.*\nconfig/local.json\n", encoding="utf-8"
    )
    (repository / ".env").write_text("TOKEN=local\n", encoding="utf-8")
    (repository / ".env.local").write_text("MODE=test\n", encoding="utf-8")
    (repository / "config").mkdir()
    (repository / "config" / "local.json").write_text(
        '{"local":true}\n', encoding="utf-8"
    )
    (repository / "tracked.txt").write_text("unstaged change\n", encoding="utf-8")
    (repository / "staged.txt").write_text("staged change\n", encoding="utf-8")
    _git(repository, "add", "staged.txt")
    (repository / "deleted.txt").unlink()
    (repository / "binary.dat").write_bytes(b"binary changed\x00\xff\n")
    (repository / "untracked.txt").write_text("new file\n", encoding="utf-8")
    source_status = _git(repository, "status", "--short")
    source_index = Path(_git(repository, "rev-parse", "--git-path", "index"))
    if not source_index.is_absolute():
        source_index = repository / source_index
    source_index_bytes = source_index.read_bytes()

    store, manager, application = _setup(tmp_path)
    try:
        created = application.create(
            SessionCreateRequestDto(
                workspaceRoot=str(repository),
                executionMode="worktree",
                baseRef="main",
                includeLocalChanges=True,
            )
        ).root
        worktree_root = Path(str(created["worktree"]["worktreeRoot"]))

        assert (worktree_root / ".env").read_text(encoding="utf-8") == "TOKEN=local\n"
        assert (worktree_root / ".env.local").read_text(encoding="utf-8") == "MODE=test\n"
        assert (worktree_root / "config/local.json").read_text(encoding="utf-8") == '{"local":true}\n'
        assert (worktree_root / "tracked.txt").read_text(encoding="utf-8") == "unstaged change\n"
        assert (worktree_root / "staged.txt").read_text(encoding="utf-8") == "staged change\n"
        assert not (worktree_root / "deleted.txt").exists()
        assert (worktree_root / "binary.dat").read_bytes() == b"binary changed\x00\xff\n"
        assert (worktree_root / "untracked.txt").read_text(encoding="utf-8") == "new file\n"

        status = manager.status(str(created["worktree"]["worktreeId"]))
        assert status.dirty
        assert status.staged_count == 1
        assert status.unstaged_count >= 3
        assert status.untracked_count >= 1
        assert _git(repository, "status", "--short") == source_status
        assert source_index.read_bytes() == source_index_bytes
    finally:
        store.close()


def test_worktreeinclude_does_not_copy_tracked_clean_file(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    (repository / ".worktreeinclude").write_text("tracked.txt\n", encoding="utf-8")
    backend = DulwichGitBackend()

    copied = materialize_worktree_include(
        repository,
        target,
        is_ignored=lambda relative: backend.is_ignored(repository, relative),
    )

    assert copied == ()
    assert not (target / "tracked.txt").exists()


def test_tracked_modified_and_untracked_nonignored_files_stay_out_without_dirty_transfer(
    tmp_path: Path,
) -> None:
    repository = _include_scenario(tmp_path)
    store, _manager, application = _setup(tmp_path)
    try:
        session = application.create(
            SessionCreateRequestDto(
                workspaceRoot=str(repository),
                executionMode="worktree",
                includeLocalChanges=False,
            )
        ).root
        root = Path(str(session["worktree"]["worktreeRoot"]))

        assert (root / "tracked.txt").read_text(encoding="utf-8") == "base\n"
        assert not (root / "untracked.txt").exists()
        assert (root / ".env").read_text(encoding="utf-8") == "TOKEN=local\n"
    finally:
        store.close()


def test_tracked_modified_and_untracked_nonignored_files_use_dirty_transfer(
    tmp_path: Path,
) -> None:
    repository = _include_scenario(tmp_path)
    store, _manager, application = _setup(tmp_path)
    try:
        session = application.create(
            SessionCreateRequestDto(
                workspaceRoot=str(repository),
                executionMode="worktree",
                includeLocalChanges=True,
            )
        ).root
        root = Path(str(session["worktree"]["worktreeRoot"]))

        assert (root / "tracked.txt").read_text(encoding="utf-8") == "modified\n"
        assert (root / "untracked.txt").read_text(encoding="utf-8") == "untracked\n"
        assert (root / ".env").read_text(encoding="utf-8") == "TOKEN=local\n"
    finally:
        store.close()


def test_managed_worktree_materializes_only_ignored_rule_overrides(
    tmp_path: Path,
) -> None:
    repository = _ignored_override_repository(tmp_path)
    (repository / "EIDOS.override.md").write_text(
        "local Eidos rules\n", encoding="utf-8"
    )
    (repository / "AGENTS.override.md").write_text(
        "local agent rules\n", encoding="utf-8"
    )
    (repository / "nested").mkdir()
    (repository / "nested" / "EIDOS.override.md").write_text(
        "nested rules\n", encoding="utf-8"
    )
    store, _manager, application = _setup(tmp_path)
    try:
        session = application.create(
            SessionCreateRequestDto(
                workspaceRoot=str(repository), executionMode="worktree"
            )
        ).root
        root = Path(str(session["worktree"]["worktreeRoot"]))

        assert (root / "EIDOS.override.md").read_text(encoding="utf-8") == (
            "local Eidos rules\n"
        )
        assert (root / "AGENTS.override.md").read_text(encoding="utf-8") == (
            "local agent rules\n"
        )
        assert (root / "nested" / "EIDOS.override.md").read_text(
            encoding="utf-8"
        ) == "nested rules\n"
    finally:
        store.close()


def test_tracked_rule_override_uses_git_checkout_without_extra_copy(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    (repository / "EIDOS.override.md").write_text(
        "tracked rules\n", encoding="utf-8"
    )
    _git(repository, "add", "EIDOS.override.md")
    _git(repository, "commit", "-qm", "track rule override")
    store, _manager, application = _setup(tmp_path)
    try:
        session = application.create(
            SessionCreateRequestDto(
                workspaceRoot=str(repository), executionMode="worktree"
            )
        ).root
        root = Path(str(session["worktree"]["worktreeRoot"]))

        assert (root / "EIDOS.override.md").read_text(encoding="utf-8") == (
            "tracked rules\n"
        )
    finally:
        store.close()


def test_ignored_rule_override_symlink_escape_is_rejected(
    tmp_path: Path,
) -> None:
    repository = _ignored_override_repository(tmp_path)
    outside = tmp_path / "outside-rules.md"
    outside.write_text("outside\n", encoding="utf-8")
    (repository / "EIDOS.override.md").symlink_to(outside)
    store, manager, application = _setup(tmp_path)
    try:
        with pytest.raises(ApplicationError) as error:
            application.create(
                SessionCreateRequestDto(
                    workspaceRoot=str(repository), executionMode="worktree"
                )
            )

        assert error.value.code == "WORKTREE_INCLUDE_INVALID"
        assert manager.list() == ()
    finally:
        store.close()


def test_local_changes_require_the_selected_base_commit(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    (repository / "tracked.txt").write_text("local change\n", encoding="utf-8")
    other_commit = _git(repository, "rev-parse", "HEAD")
    _git(repository, "switch", "-q", "-c", "other")
    (repository / "other.txt").write_text("other\n", encoding="utf-8")
    _git(repository, "add", "other.txt")
    _git(repository, "commit", "-qm", "other")
    _git(repository, "switch", "-q", "main")

    store, _manager, application = _setup(tmp_path)
    try:
        with pytest.raises(ApplicationError) as error:
            application.create(
                SessionCreateRequestDto(
                    workspaceRoot=str(repository),
                    executionMode="worktree",
                    baseRef="other",
                    includeLocalChanges=True,
                )
            )
        assert error.value.code == "LOCAL_CHANGES_BASE_MISMATCH"
        assert _git(repository, "rev-parse", "HEAD") == other_commit
        assert store.connection.execute("SELECT COUNT(*) FROM worktrees").fetchone()[0] == 0
    finally:
        store.close()


def test_conflicted_source_worktree_is_rejected_before_capture(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    _git(repository, "switch", "-q", "-c", "conflict-source")
    (repository / "tracked.txt").write_text("other branch\n", encoding="utf-8")
    _git(repository, "add", "tracked.txt")
    _git(repository, "commit", "-qm", "other change")
    _git(repository, "switch", "-q", "main")
    (repository / "tracked.txt").write_text("main branch\n", encoding="utf-8")
    _git(repository, "add", "tracked.txt")
    _git(repository, "commit", "-qm", "main change")
    merge = subprocess.run(
        ["git", "merge", "conflict-source"],
        cwd=repository,
        capture_output=True,
        text=True,
    )
    assert merge.returncode != 0

    store, _manager, application = _setup(tmp_path)
    try:
        with pytest.raises(ApplicationError) as error:
            application.create(
                SessionCreateRequestDto(
                    workspaceRoot=str(repository),
                    executionMode="worktree",
                    includeLocalChanges=True,
                )
            )
        assert error.value.code == "WORKTREE_LOCAL_CHANGES_CONFLICT"
    finally:
        store.close()


def test_local_changes_transfer_external_untracked_symlink_with_git_semantics(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("outside\n", encoding="utf-8")
    (repository / "external-link").symlink_to(outside)

    store, manager, application = _setup(tmp_path)
    try:
        created = application.create(
            SessionCreateRequestDto(
                workspaceRoot=str(repository),
                executionMode="worktree",
                includeLocalChanges=True,
            )
        )
        root = Path(str(created.root["worktree"]["worktreeRoot"]))
        assert (root / "external-link").is_symlink()
        assert (root / "external-link").readlink() == outside
        assert len(manager.list()) == 1
    finally:
        store.close()


def test_source_change_after_worktree_add_aborts_and_compensates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository(tmp_path)
    (repository / "tracked.txt").write_text("local change\n", encoding="utf-8")
    store, manager, application = _setup(tmp_path)
    original_add = manager.git.worktree_add

    def add_then_change_source(
        cwd: Path, worktree_root: Path, branch: str | None, base_commit: str
    ) -> None:
        original_add(cwd, worktree_root, branch, base_commit)
        (repository / "tracked.txt").write_text("changed during create\n", encoding="utf-8")

    monkeypatch.setattr(manager.git, "worktree_add", add_then_change_source)
    try:
        with pytest.raises(ApplicationError) as error:
            application.create(
                SessionCreateRequestDto(
                    workspaceRoot=str(repository),
                    executionMode="worktree",
                    includeLocalChanges=True,
                )
            )
        assert error.value.code == "WORKTREE_SOURCE_CHANGED"
        assert store.connection.execute("SELECT COUNT(*) FROM worktrees").fetchone()[0] == 0
        assert manager.list() == ()
    finally:
        store.close()


def test_create_recovery_reapplies_durable_local_change_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repository(tmp_path)
    (repository / "tracked.txt").write_text("local change\n", encoding="utf-8")
    store, manager, application = _setup(tmp_path)
    operation_id = "55555555-5555-4555-8555-555555555555"
    original_apply = manager.git.apply_worktree_changes

    def crash_before_apply(root: Path, changes: object) -> None:
        raise KeyboardInterrupt("simulated runtime crash before apply")

    monkeypatch.setattr(manager.git, "apply_worktree_changes", crash_before_apply)
    try:
        with pytest.raises(KeyboardInterrupt, match="simulated runtime crash"):
            application.create(
                SessionCreateRequestDto(
                    workspaceRoot=str(repository),
                    executionMode="worktree",
                    includeLocalChanges=True,
                    operationId=operation_id,
                )
            )
        unfinished = manager.lifecycle.read(
            WorktreeLifecycleScope.SESSION_CREATE, operation_id
        )
        assert unfinished is not None
        assert unfinished.state is WorktreeLifecycleState.PREPARED
        root = Path(str(unfinished.worktree_root))
        assert root.exists()
        assert (root / "tracked.txt").read_text(encoding="utf-8") == "base\n"

        monkeypatch.setattr(manager.git, "apply_worktree_changes", original_apply)
        manager.recover_lifecycle()

        assert (root / "tracked.txt").read_text(encoding="utf-8") == "local change\n"
        recovered = manager.lifecycle.read(
            WorktreeLifecycleScope.SESSION_CREATE, operation_id
        )
        assert recovered is not None
        assert recovered.state is WorktreeLifecycleState.WORKTREE_CREATED
    finally:
        store.close()


def test_include_pattern_syntax_is_delegated_to_git_ignore_spec(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    (repository / ".worktreeinclude").write_text(
        "*\n!important.env\n/config/**\n!/config/local.example\n",
        encoding="utf-8",
    )
    (repository / "ordinary.env").write_text("ordinary\n", encoding="utf-8")
    (repository / "important.env").write_text("important\n", encoding="utf-8")
    (repository / "config").mkdir()
    (repository / "config" / "remote.example").write_text(
        "remote\n", encoding="utf-8"
    )
    (repository / "config" / "local.example").write_text(
        "local\n", encoding="utf-8"
    )

    target = tmp_path / "target"
    target.mkdir()
    copied = materialize_worktree_include(
        repository, target, is_ignored=_always_ignored
    )

    assert "ordinary.env" in copied
    assert "important.env" not in copied
    assert "config/remote.example" in copied
    assert "config/local.example" not in copied
    assert (target / "ordinary.env").exists()
    assert not (target / "important.env").exists()
    assert (target / "config/remote.example").exists()
    assert not (target / "config/local.example").exists()


def test_detached_worktree_can_create_user_branch_without_changing_head_or_dirty_state(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    store, manager, application = _setup(tmp_path)
    try:
        session = application.create(
            SessionCreateRequestDto(
                workspaceRoot=str(repository), executionMode="worktree"
            )
        ).root
        worktree_id = str(session["worktree"]["worktreeId"])
        root = Path(str(session["worktree"]["worktreeRoot"]))
        (root / "dirty.txt").write_text("keep\n", encoding="utf-8")
        before = manager.status(worktree_id)

        result = application.create_branch(
            SessionCreateBranchRequestDto(
                sessionId=str(session["id"]),
                branch="feature/runtime-fix",
                operationId="11111111-1111-4111-8111-111111111111",
            )
        ).root

        assert result["sessionId"] == session["id"]
        assert result["worktreeId"] == worktree_id
        assert result["branch"] == "feature/runtime-fix"
        assert result["head"] == before.head
        assert _git(root, "branch", "--show-current") == "feature/runtime-fix"
        after = manager.status(worktree_id)
        assert after.head == before.head
        assert after.dirty
        assert manager.repository.read_worktree(worktree_id).branch == "feature/runtime-fix"
        assert _git(repository, "show-ref", "--verify", "refs/heads/feature/runtime-fix")
    finally:
        store.close()


def test_create_branch_uses_current_head_and_preserves_creation_baseline(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    store, manager, application = _setup(tmp_path)
    try:
        session = application.create(
            SessionCreateRequestDto(
                workspaceRoot=str(repository), executionMode="worktree"
            )
        ).root
        worktree_id = str(session["worktree"]["worktreeId"])
        root = Path(str(session["worktree"]["worktreeRoot"]))
        baseline = manager.repository.read_worktree(worktree_id)
        assert baseline is not None
        baseline_commit = baseline.base_commit

        (root / "commit-b.txt").write_text("B\n", encoding="utf-8")
        _git(root, "add", "commit-b.txt")
        _git(root, "commit", "-qm", "commit B")
        (root / "commit-c.txt").write_text("C\n", encoding="utf-8")
        _git(root, "add", "commit-c.txt")
        commit_c = _git(root, "commit", "-qm", "commit C")
        commit_c = _git(root, "rev-parse", "HEAD")

        result = application.create_branch(
            SessionCreateBranchRequestDto(
                sessionId=str(session["id"]), branch="feature/from-current-head"
            )
        ).root
        persisted = manager.repository.read_worktree(worktree_id)

        assert result["head"] == commit_c
        assert _git(root, "branch", "--show-current") == "feature/from-current-head"
        assert _git(root, "rev-parse", "HEAD") == commit_c
        assert persisted is not None
        assert persisted.base_commit == baseline_commit
        assert _git(repository, "rev-parse", "refs/heads/feature/from-current-head") == commit_c
    finally:
        store.close()


def test_branch_attach_head_change_after_git_create_requires_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository(tmp_path)
    store, manager, application = _setup(tmp_path)
    operation_id = "66666666-6666-4666-8666-666666666666"
    try:
        session = application.create(
            SessionCreateRequestDto(
                workspaceRoot=str(repository), executionMode="worktree"
            )
        ).root
        original_create_branch = manager.git.create_branch

        def create_branch_then_advance_head(worktree_root: Path, branch: str) -> None:
            original_create_branch(worktree_root, branch)
            (worktree_root / "external.txt").write_text("external\n", encoding="utf-8")
            _git(worktree_root, "add", "external.txt")
            _git(worktree_root, "commit", "-qm", "external head change")

        monkeypatch.setattr(manager.git, "create_branch", create_branch_then_advance_head)
        with pytest.raises(ApplicationError) as error:
            application.create_branch(
                SessionCreateBranchRequestDto(
                    sessionId=str(session["id"]),
                    branch="feature/external-head",
                    operationId=operation_id,
                )
            )

        assert error.value.code == "WORKTREE_RECOVERY_REQUIRED"
        operation = manager.lifecycle.read(
            WorktreeLifecycleScope.ATTACH_BRANCH, operation_id
        )
        assert operation is not None
        assert operation.expected_head is not None
        assert operation.state is WorktreeLifecycleState.CLEANUP_REQUIRED
        assert operation.error_code == "worktree_branch_recovery_required"
    finally:
        store.close()


def test_branch_attach_head_change_before_persist_requires_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository(tmp_path)
    store, manager, application = _setup(tmp_path)
    operation_id = "67676767-6767-4676-8676-676767676767"
    try:
        session = application.create(
            SessionCreateRequestDto(
                workspaceRoot=str(repository), executionMode="worktree"
            )
        ).root
        original_persist_branch = manager.persist_branch

        def persist_after_external_head_change(
            worktree_id: str,
            branch: str,
            *,
            expected_head: str | None = None,
        ) -> object:
            root = Path(str(session["worktree"]["worktreeRoot"]))
            (root / "external-after-attach.txt").write_text(
                "external\n", encoding="utf-8"
            )
            _git(root, "add", "external-after-attach.txt")
            _git(root, "commit", "-qm", "external head change")
            return original_persist_branch(
                worktree_id,
                branch,
                expected_head=expected_head,
            )

        monkeypatch.setattr(
            manager,
            "persist_branch",
            persist_after_external_head_change,
        )
        with pytest.raises(ApplicationError) as error:
            application.create_branch(
                SessionCreateBranchRequestDto(
                    sessionId=str(session["id"]),
                    branch="feature/external-before-persist",
                    operationId=operation_id,
                )
            )

        assert error.value.code == "WORKTREE_RECOVERY_REQUIRED"
        operation = manager.lifecycle.read(
            WorktreeLifecycleScope.ATTACH_BRANCH, operation_id
        )
        assert operation is not None
        assert operation.state is WorktreeLifecycleState.CLEANUP_REQUIRED
        assert operation.error_code == "worktree_branch_recovery_required"
    finally:
        store.close()


@pytest.mark.parametrize(
    ("execution_mode", "branch", "expected_code"),
    [
        ("local", "feature/local", "WORKTREE_REQUIRED"),
        ("worktree", "bad branch", "BRANCH_INVALID"),
    ],
)
def test_create_branch_rejects_invalid_session_or_branch(
    tmp_path: Path,
    execution_mode: str,
    branch: str,
    expected_code: str,
) -> None:
    repository = _repository(tmp_path)
    store, _manager, application = _setup(tmp_path)
    try:
        session = application.create(
            SessionCreateRequestDto(
                workspaceRoot=str(repository), executionMode=execution_mode
            )
        ).root
        with pytest.raises(ApplicationError) as error:
            application.create_branch(
                SessionCreateBranchRequestDto(
                    sessionId=str(session["id"]), branch=branch
                )
            )
        assert error.value.code == expected_code
    finally:
        store.close()


def test_create_branch_rejects_existing_branch_and_branch_checked_out_elsewhere(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    _git(repository, "branch", "feature/existing")
    store, _manager, application = _setup(tmp_path)
    other_root = tmp_path / "other-worktree"
    try:
        existing_session = application.create(
            SessionCreateRequestDto(
                workspaceRoot=str(repository), executionMode="worktree"
            )
        ).root
        with pytest.raises(ApplicationError) as existing_error:
            application.create_branch(
                SessionCreateBranchRequestDto(
                    sessionId=str(existing_session["id"]),
                    branch="feature/existing",
                )
            )
        assert existing_error.value.code == "BRANCH_ALREADY_EXISTS"

        _git(
            repository,
            "worktree",
            "add",
            "-q",
            "-b",
            "feature/in-use",
            str(other_root),
            "HEAD",
        )
        in_use_session = application.create(
            SessionCreateRequestDto(
                workspaceRoot=str(repository), executionMode="worktree"
            )
        ).root
        with pytest.raises(ApplicationError) as in_use_error:
            application.create_branch(
                SessionCreateBranchRequestDto(
                    sessionId=str(in_use_session["id"]),
                    branch="feature/in-use",
                )
            )
        assert in_use_error.value.code == "WORKTREE_BRANCH_IN_USE"
    finally:
        if other_root.exists():
            _git(repository, "worktree", "remove", "-f", str(other_root))
        store.close()


def test_branch_attach_recovery_adopts_branch_created_before_sqlite_update(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository(tmp_path)
    store, manager, application = _setup(tmp_path)
    try:
        session = application.create(
            SessionCreateRequestDto(
                workspaceRoot=str(repository), executionMode="worktree"
            )
        ).root
        original_create_branch = manager.git.create_branch

        def create_branch_then_crash(root: Path, branch: str) -> None:
            original_create_branch(root, branch)
            raise RuntimeError("simulated crash after Git branch creation")

        monkeypatch.setattr(manager.git, "create_branch", create_branch_then_crash)
        with pytest.raises(RuntimeError, match="simulated crash"):
            application.create_branch(
                SessionCreateBranchRequestDto(
                    sessionId=str(session["id"]), branch="feature/recover"
                )
            )
        unfinished = manager.lifecycle.list_unfinished()
        assert len(unfinished) == 1
        assert unfinished[0].scope is WorktreeLifecycleScope.ATTACH_BRANCH
        assert unfinished[0].state is WorktreeLifecycleState.PREPARED
        assert _git(
            Path(str(session["worktree"]["worktreeRoot"])),
            "branch",
            "--show-current",
        ) == "feature/recover"

        monkeypatch.setattr(manager.git, "create_branch", original_create_branch)
        manager.recover_lifecycle()

        worktree_id = str(session["worktree"]["worktreeId"])
        worktree = manager.repository.read_worktree(worktree_id)
        assert worktree is not None
        assert worktree.branch == "feature/recover"
        assert worktree.branch_ownership.value == "user"
        recovered = manager.lifecycle.read(
            WorktreeLifecycleScope.ATTACH_BRANCH,
            unfinished[0].operation_id,
        )
        assert recovered is not None
        assert recovered.state is WorktreeLifecycleState.COMPLETED
    finally:
        store.close()


def test_branch_attach_state_change_enters_cleanup_required_without_force_switch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository(tmp_path)
    store, manager, application = _setup(tmp_path)
    try:
        session = application.create(
            SessionCreateRequestDto(
                workspaceRoot=str(repository), executionMode="worktree"
            )
        ).root
        root = Path(str(session["worktree"]["worktreeRoot"]))
        original_create_branch = manager.git.create_branch

        def create_branch_then_detach(worktree_root: Path, branch: str) -> None:
            original_create_branch(worktree_root, branch)
            _git(worktree_root, "switch", "--detach", "HEAD")

        monkeypatch.setattr(manager.git, "create_branch", create_branch_then_detach)
        with pytest.raises(ApplicationError) as error:
            application.create_branch(
                SessionCreateBranchRequestDto(
                    sessionId=str(session["id"]),
                    branch="feature/state-change",
                    operationId="33333333-3333-4333-8333-333333333333",
                )
            )
        assert error.value.code == "WORKTREE_RECOVERY_REQUIRED"
        assert _git(root, "branch", "--show-current") == ""
        operation = manager.lifecycle.read(
            WorktreeLifecycleScope.ATTACH_BRANCH,
            "33333333-3333-4333-8333-333333333333",
        )
        assert operation is not None
        assert operation.state is WorktreeLifecycleState.CLEANUP_REQUIRED
        assert operation.error_code == "worktree_branch_recovery_required"
    finally:
        store.close()


def test_session_delete_removes_user_worktree_but_keeps_user_branch(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    store, manager, application = _setup(tmp_path)
    try:
        session = application.create(
            SessionCreateRequestDto(
                workspaceRoot=str(repository), executionMode="worktree"
            )
        ).root
        application.create_branch(
            SessionCreateBranchRequestDto(
                sessionId=str(session["id"]), branch="feature/keep"
            )
        )
        worktree_root = Path(str(session["worktree"]["worktreeRoot"]))
        deleted = application.delete(
            SessionDeleteRequestDto(
                sessionId=str(session["id"]),
                operationId="22222222-2222-4222-8222-222222222222",
            )
        )

        assert deleted.root["deletedSessionId"] == session["id"]
        assert not worktree_root.exists()
        assert _git(repository, "show-ref", "--verify", "refs/heads/feature/keep")
        assert manager.repository.read_worktree(str(session["worktree"]["worktreeId"])) is not None
    finally:
        store.close()


def test_include_glob_nested_empty_binary_missing_and_internal_symlink(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (source / ".worktreeinclude").write_text(
        "config/local/**\nconfig/local-link\nmissing.txt\n", encoding="utf-8"
    )
    (source / "config" / "local").mkdir(parents=True)
    (source / "config" / "local" / "empty.txt").write_bytes(b"")
    (source / "config" / "local" / "binary.dat").write_bytes(b"\x00\xff\x01")
    (source / "config" / "local" / "target.txt").write_text(
        "inside\n", encoding="utf-8"
    )
    (source / "config" / "local-link").symlink_to(
        Path("local") / "target.txt"
    )

    copied = materialize_worktree_include(
        source, target, is_ignored=_always_ignored
    )

    assert "config/local/empty.txt" in copied
    assert (target / "config/local/empty.txt").read_bytes() == b""
    assert (target / "config/local/binary.dat").read_bytes() == b"\x00\xff\x01"
    assert (target / "config/local-link").is_symlink()
    assert (target / "config/local-link").readlink() == Path("local/target.txt")
    assert not (target / "missing.txt").exists()


@pytest.mark.parametrize("pattern", ["../escape", "/tmp/absolute", ".git", ".git/**"])
def test_include_pattern_does_not_bypass_concrete_path_safety(
    tmp_path: Path, pattern: str
) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (source / ".worktreeinclude").write_text(pattern + "\n", encoding="utf-8")

    assert materialize_worktree_include(
        source, target, is_ignored=_always_ignored
    ) == ()


def test_include_rejects_symlink_escape(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    outside = tmp_path / "outside.txt"
    source.mkdir()
    target.mkdir()
    outside.write_text("outside\n", encoding="utf-8")
    (source / ".worktreeinclude").write_text("secret.txt\n", encoding="utf-8")
    (source / "secret.txt").symlink_to(outside)

    with pytest.raises(WorktreeError) as error:
        materialize_worktree_include(
            source, target, is_ignored=_always_ignored
        )
    assert getattr(error.value, "code", None) == "worktree_include_symlink_escape"


@pytest.mark.parametrize("target_relative", ["source/managed", ""])
def test_include_rejects_overlapping_source_and_target(
    tmp_path: Path, target_relative: str
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    target = tmp_path / target_relative if target_relative else tmp_path
    target.mkdir(parents=True, exist_ok=True)
    pattern = "managed/**\n" if target_relative else "source/**\n"
    (source / ".worktreeinclude").write_text(pattern, encoding="utf-8")

    with pytest.raises(WorktreeError) as error:
        materialize_worktree_include(
            source, target, is_ignored=_always_ignored
        )
    assert getattr(error.value, "code", None) == "worktree_include_target_invalid"


def test_two_worktrees_transfer_the_same_source_without_mutating_it(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    (repository / ".worktreeinclude").write_text(".env\n", encoding="utf-8")
    (repository / ".env").write_text("TOKEN=local\n", encoding="utf-8")
    (repository / "tracked.txt").write_text("changed\n", encoding="utf-8")
    source_status = _git(repository, "status", "--short")
    store, _manager, application = _setup(tmp_path)
    try:
        first = application.create(
            SessionCreateRequestDto(
                workspaceRoot=str(repository),
                executionMode="worktree",
                includeLocalChanges=True,
            )
        ).root
        second = application.create(
            SessionCreateRequestDto(
                workspaceRoot=str(repository),
                executionMode="worktree",
                includeLocalChanges=True,
            )
        ).root
        first_root = Path(str(first["worktree"]["worktreeRoot"]))
        second_root = Path(str(second["worktree"]["worktreeRoot"]))
        assert (first_root / ".env").read_text(encoding="utf-8") == "TOKEN=local\n"
        assert (second_root / ".env").read_text(encoding="utf-8") == "TOKEN=local\n"
        assert (first_root / "tracked.txt").read_text(encoding="utf-8") == "changed\n"
        assert (second_root / "tracked.txt").read_text(encoding="utf-8") == "changed\n"
        assert _git(repository, "status", "--short") == source_status
    finally:
        store.close()


def test_local_change_apply_conflict_compensates_the_new_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository(tmp_path)
    (repository / "tracked.txt").write_text("changed\n", encoding="utf-8")
    store = SessionStore(tmp_path / "data")
    store.initialize()
    backend = FakeGitBackend()
    manager = WorktreeManager(
        store.database,
        managed_root=tmp_path / "managed-worktrees",
        git_backend=backend,
    )
    application = SessionApplication(
        store,
        scan_text=lambda value: value,
        worktree_manager=manager,
    )
    try:
        backend.failures["apply_worktree_changes"] = GitCommandFailedError(
            "worktree-apply", returncode=1, stderr="patch failed"
        )
        with pytest.raises(ApplicationError) as error:
            application.create(
                SessionCreateRequestDto(
                    workspaceRoot=str(repository),
                    executionMode="worktree",
                    includeLocalChanges=True,
                )
            )
        assert error.value.code == "WORKTREE_LOCAL_CHANGES_CONFLICT"
        assert store.connection.execute("SELECT COUNT(*) FROM worktrees").fetchone()[0] == 0
        assert not list((tmp_path / "managed-worktrees").glob("*"))
    finally:
        store.close()


def test_materialization_cleanup_failure_is_durable_cleanup_required(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    (repository / "tracked.txt").write_text("changed\n", encoding="utf-8")
    store = SessionStore(tmp_path / "data")
    store.initialize()
    backend = FakeGitBackend()
    manager = WorktreeManager(
        store.database,
        managed_root=tmp_path / "managed-worktrees",
        git_backend=backend,
    )
    application = SessionApplication(
        store,
        scan_text=lambda value: value,
        worktree_manager=manager,
    )
    operation_id = "44444444-4444-4444-8444-444444444444"
    try:
        backend.failures["apply_worktree_changes"] = GitCommandFailedError(
            "worktree-apply", returncode=1, stderr="patch failed"
        )
        backend.failures["clean_worktree_for_compensation"] = GitCommandFailedError(
            "worktree-clean", returncode=1, stderr="clean failed"
        )
        with pytest.raises(ApplicationError) as error:
            application.create(
                SessionCreateRequestDto(
                    workspaceRoot=str(repository),
                    executionMode="worktree",
                    includeLocalChanges=True,
                    operationId=operation_id,
                )
            )
        assert error.value.code == "WORKTREE_RECOVERY_REQUIRED"
        lifecycle = manager.lifecycle.read(
            WorktreeLifecycleScope.SESSION_CREATE, operation_id
        )
        assert lifecycle is not None
        assert lifecycle.state is WorktreeLifecycleState.CLEANUP_REQUIRED
        assert lifecycle.error_code == "worktree_cleanup_required"
    finally:
        store.close()

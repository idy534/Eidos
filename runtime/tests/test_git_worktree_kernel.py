from __future__ import annotations

from pathlib import Path
import subprocess
import time
from datetime import UTC, datetime

import pytest

from eidos_runtime.db.database import Database
from eidos_runtime.db.errors import StorageError
from eidos_runtime.db.schema import SCHEMA_VERSION
from eidos_runtime.git import (
    DiffScope,
    WorktreeError,
    WorktreeManager,
)
from eidos_runtime.git.errors import GitCommandFailedError, GitCommandTimeoutError
from eidos_runtime.git.process import GitCommandResult, GitProcess
from eidos_runtime.domain.worktree import Worktree, WorktreeOwnership, WorktreeState
from eidos_runtime.persistence.worktrees import ProjectWorktreeRepository


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


@pytest.fixture
def database(tmp_path: Path):
    database = Database(tmp_path / "data")
    database.initialize()
    assert database.health() == {"state": "ready"}
    yield database
    database.close()


def test_project_discovery_canonicalizes_nested_repository(tmp_path: Path, database: Database) -> None:
    repository = _repository(tmp_path)
    nested = repository / "nested"
    nested.mkdir()

    manager = WorktreeManager(database, managed_root=tmp_path / "managed")
    worktree = manager.create(nested, base_ref="main")

    project = manager.project(worktree.project_id)
    assert project.repository_root == str(repository.resolve())
    assert project.git_common_dir == str((repository / ".git").resolve())


def test_non_git_directory_and_missing_repository_have_stable_errors(
    tmp_path: Path, database: Database
) -> None:
    manager = WorktreeManager(database, managed_root=tmp_path / "managed")

    with pytest.raises(WorktreeError) as non_git:
        manager.create(tmp_path)
    assert non_git.value.code == "not_a_git_repository"

    with pytest.raises(WorktreeError) as missing:
        manager.create(tmp_path / "does-not-exist")
    assert missing.value.code == "repository_not_found"


def test_managed_root_is_a_runtime_owned_sibling_of_data_directory(
    tmp_path: Path, database: Database
) -> None:
    manager = WorktreeManager(database)

    assert manager.managed_root == (tmp_path / "data-worktrees").resolve()
    with pytest.raises(WorktreeError) as error:
        WorktreeManager(database, managed_root=tmp_path / "data" / "nested")
    assert error.value.code == "managed_worktree_root_overlaps_data"


def test_create_persists_frozen_base_and_keeps_worktrees_isolated(
    tmp_path: Path, database: Database
) -> None:
    repository = _repository(tmp_path)
    manager = WorktreeManager(database, managed_root=tmp_path / "managed")

    first = manager.create(repository, base_ref="main")
    second = manager.create(repository, base_ref="main")

    assert first.base_commit == _git(repository, "rev-parse", "HEAD")
    assert first.branch.startswith("eidos/")
    assert first.worktree_root != second.worktree_root
    assert Path(first.worktree_root).parent == (tmp_path / "managed").resolve()
    assert first.state.value == "active"

    (Path(first.worktree_root) / "only-first.txt").write_text("first\n", encoding="utf-8")
    assert not (Path(second.worktree_root) / "only-first.txt").exists()
    assert manager.validate(first.id).valid
    assert manager.validate(second.id).valid

    rows = database.connection().execute(
        "SELECT COUNT(*), COUNT(DISTINCT repository_root) FROM projects"
    ).fetchone()
    assert tuple(rows) == (1, 1)
    assert database.connection().execute(
        "SELECT COUNT(*) FROM worktrees"
    ).fetchone()[0] == 2

    views = manager.list()
    assert len(views) == 2
    assert all(view.actual_present for view in views)
    assert all(view.worktree.state.value == "active" for view in views)
    assert manager.recover().updated_worktrees == ()


def test_failed_worktree_observation_does_not_mutate_lifecycle_state(
    tmp_path: Path, database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repository(tmp_path)
    manager = WorktreeManager(database, managed_root=tmp_path / "managed")
    worktrees = [manager.create(repository) for _ in range(4)]
    real_worktree_list = manager.git.worktree_list

    def fail_worktree_list(cwd: Path) -> GitCommandResult:
        raise GitCommandFailedError(
            "worktree-list", returncode=2, stderr="temporary observation failure"
        )

    monkeypatch.setattr(manager.git, "worktree_list", fail_worktree_list)
    operations = (
        lambda: manager.validate(worktrees[0].id),
        lambda: manager.list(),
        lambda: manager.recover(),
        lambda: manager.delete(worktrees[3].id),
    )

    for operation in operations:
        with pytest.raises(WorktreeError) as error:
            operation()
        assert error.value.code == "git_observation_failed"

    monkeypatch.setattr(manager.git, "worktree_list", real_worktree_list)
    assert all(
        manager.repository.read_worktree(worktree.id).state is WorktreeState.ACTIVE
        for worktree in worktrees
    )


def test_truncated_worktree_observation_does_not_mutate_lifecycle_state(
    tmp_path: Path, database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repository(tmp_path)
    manager = WorktreeManager(database, managed_root=tmp_path / "managed")
    worktree = manager.create(repository)
    real_worktree_list = manager.git.worktree_list

    def truncated_worktree_list(cwd: Path) -> GitCommandResult:
        output = real_worktree_list(cwd)
        return GitCommandResult(
            stdout=output,
            stderr="",
            returncode=0,
            stdout_truncated=True,
            stderr_truncated=False,
        )

    monkeypatch.setattr(manager.git, "worktree_list", truncated_worktree_list)

    with pytest.raises(WorktreeError) as error:
        manager.validate(worktree.id)

    assert error.value.code == "git_observation_incomplete"
    assert manager.repository.read_worktree(worktree.id).state is WorktreeState.ACTIVE


def test_incomplete_worktree_observation_does_not_mutate_lifecycle_state(
    tmp_path: Path, database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repository(tmp_path)
    manager = WorktreeManager(database, managed_root=tmp_path / "managed")
    worktree = manager.create(repository)

    def incomplete_worktree_list(cwd: Path) -> GitCommandResult:
        return GitCommandResult(
            stdout="worktree /incomplete\x00",
            stderr="",
            returncode=0,
            stdout_truncated=False,
            stderr_truncated=False,
        )

    monkeypatch.setattr(manager.git, "worktree_list", incomplete_worktree_list)

    with pytest.raises(WorktreeError) as error:
        manager.recover()

    assert error.value.code == "git_observation_incomplete"
    assert manager.repository.read_worktree(worktree.id).state is WorktreeState.ACTIVE


def test_timed_out_worktree_observation_does_not_mutate_lifecycle_state(
    tmp_path: Path, database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repository(tmp_path)
    manager = WorktreeManager(database, managed_root=tmp_path / "managed")
    worktree = manager.create(repository)

    def timeout_worktree_list(cwd: Path) -> GitCommandResult:
        raise GitCommandTimeoutError("worktree-list")

    monkeypatch.setattr(manager.git, "worktree_list", timeout_worktree_list)

    with pytest.raises(WorktreeError) as error:
        manager.recover()

    assert error.value.code == "git_command_timeout"
    assert manager.repository.read_worktree(worktree.id).state is WorktreeState.ACTIVE


def test_status_and_diff_distinguish_head_and_frozen_baseline(
    tmp_path: Path, database: Database
) -> None:
    repository = _repository(tmp_path)
    manager = WorktreeManager(database, managed_root=tmp_path / "managed")
    worktree = manager.create(repository, base_ref="main")

    worktree_root = Path(worktree.worktree_root)
    (worktree_root / "README.md").write_text("working\n", encoding="utf-8")
    (worktree_root / "untracked.txt").write_text("new\n", encoding="utf-8")
    _git(worktree_root, "add", "README.md")

    status = manager.status(worktree.id)
    assert status.dirty
    assert status.staged_count == 1
    assert status.unstaged_count == 0
    assert status.untracked_count == 1

    _git(worktree_root, "commit", "-qm", "worktree commit")
    (worktree_root / "commit-a.txt").write_text("commit-a\n", encoding="utf-8")
    _git(worktree_root, "add", "commit-a.txt")
    _git(worktree_root, "commit", "-qm", "commit A")
    (worktree_root / "README.md").write_text("after-commit\n", encoding="utf-8")

    head_diff = manager.diff(worktree.id, scope=DiffScope.HEAD)
    baseline_diff = manager.diff(worktree.id, scope=DiffScope.BASELINE)
    assert head_diff.base_commit == worktree.base_commit
    assert "after-commit" in head_diff.unified_diff
    assert "commit-a.txt" not in head_diff.unified_diff
    assert "worktree commit" not in head_diff.unified_diff
    assert "README.md" in head_diff.changed_files
    assert "commit-a.txt" not in head_diff.changed_files
    assert "after-commit" in baseline_diff.unified_diff
    assert "commit-a.txt" in baseline_diff.unified_diff
    assert "README.md" in baseline_diff.changed_files
    assert "commit-a.txt" in baseline_diff.changed_files
    assert baseline_diff.head == _git(worktree_root, "rev-parse", "HEAD")


def test_head_and_baseline_diff_include_untracked_paths_with_spaces(
    tmp_path: Path, database: Database
) -> None:
    repository = _repository(tmp_path)
    manager = WorktreeManager(database, managed_root=tmp_path / "managed")
    worktree = manager.create(repository)
    root = Path(worktree.worktree_root)
    untracked = root / "untracked file.txt"
    untracked.write_text("untracked content\n", encoding="utf-8")

    head_diff = manager.diff(worktree.id, scope=DiffScope.HEAD)
    baseline_diff = manager.diff(worktree.id, scope=DiffScope.BASELINE)

    for snapshot in (head_diff, baseline_diff):
        assert str(untracked.relative_to(root)) in snapshot.changed_files
        assert "untracked file.txt" in snapshot.unified_diff
        assert "untracked content" in snapshot.unified_diff


def test_status_covers_clean_unstaged_staged_untracked_and_conflict(
    tmp_path: Path, database: Database
) -> None:
    repository = _repository(tmp_path)
    manager = WorktreeManager(database, managed_root=tmp_path / "managed")
    worktree = manager.create(repository)
    root = Path(worktree.worktree_root)

    clean = manager.status(worktree.id)
    assert (clean.dirty, clean.staged_count, clean.unstaged_count, clean.untracked_count) == (
        False,
        0,
        0,
        0,
    )

    (root / "README.md").write_text("unstaged\n", encoding="utf-8")
    unstaged = manager.status(worktree.id)
    assert unstaged.unstaged_count == 1

    _git(root, "add", "README.md")
    staged = manager.status(worktree.id)
    assert staged.staged_count == 1

    (root / "untracked.txt").write_text("untracked\n", encoding="utf-8")
    untracked = manager.status(worktree.id)
    assert untracked.untracked_count == 1

    _git(root, "commit", "-qm", "ours")
    _git(repository, "switch", "-c", "theirs")
    (repository / "README.md").write_text("theirs\n", encoding="utf-8")
    _git(repository, "commit", "-qam", "theirs")
    _git(repository, "switch", "main")
    merge = subprocess.run(
        ["git", "merge", "theirs", "-m", "merge"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert merge.returncode != 0
    conflict = manager.status(worktree.id)
    assert conflict.conflict_count == 1


def test_adopted_worktree_is_not_deleted_or_cleaned_by_managed_lifecycle(
    tmp_path: Path, database: Database
) -> None:
    repository = _repository(tmp_path)
    manager = WorktreeManager(database, managed_root=tmp_path / "managed")
    managed = manager.create(repository)
    adopted_root = tmp_path / "adopted"
    _git(
        repository,
        "worktree",
        "add",
        "-q",
        "-b",
        "adopted/branch",
        str(adopted_root),
        "HEAD",
    )
    discovered = manager.discovery.discover(adopted_root)
    now = datetime.fromtimestamp(int(datetime.now(UTC).timestamp() * 1000) / 1000, tz=UTC)
    adopted = Worktree(
        id="adopted-worktree",
        project_id=managed.project_id,
        worktree_root=str(adopted_root.resolve()),
        git_dir=discovered.git_dir,
        base_ref="main",
        base_commit=_git(repository, "rev-parse", "main"),
        branch="adopted/branch",
        ownership=WorktreeOwnership.ADOPTED,
        state=WorktreeState.ACTIVE,
        created_at=now,
        updated_at=now,
    )
    manager.repository.insert_worktree(adopted)

    with pytest.raises(WorktreeError) as error:
        manager.delete(adopted.id)
    assert error.value.code == "worktree_not_owned"
    manager.cleanup()
    assert adopted_root.exists()


def test_invalid_base_ref_is_typed_and_does_not_persist_worktree(
    tmp_path: Path, database: Database
) -> None:
    repository = _repository(tmp_path)
    manager = WorktreeManager(database, managed_root=tmp_path / "managed")

    with pytest.raises(WorktreeError) as error:
        manager.create(repository, base_ref="does-not-exist")

    assert error.value.code == "base_ref_not_found"
    assert database.connection().execute(
        "SELECT COUNT(*) FROM worktrees"
    ).fetchone()[0] == 0


def test_branch_collision_retries_with_a_new_runtime_id(
    tmp_path: Path, database: Database
) -> None:
    repository = _repository(tmp_path)
    _git(repository, "branch", "eidos/collision123")
    ids = iter(("wt_collision1234", "wt_after-collision"))
    manager = WorktreeManager(
        database,
        managed_root=tmp_path / "managed",
        id_factory=lambda: next(ids),
    )

    worktree = manager.create(repository)

    assert worktree.id == "wt_after-collision"
    assert worktree.branch == "eidos/after-collis"


def test_git_process_timeout_is_typed_and_uses_a_bounded_failure(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "slow-git"
    executable.write_text("#!/bin/sh\nsleep 2\n", encoding="utf-8")
    executable.chmod(0o755)
    process = GitProcess(git_executable=str(executable), timeout_seconds=0.05)

    started = time.monotonic()
    with pytest.raises(GitCommandTimeoutError) as error:
        process.rev_parse_show_toplevel(tmp_path)

    assert error.value.code == "git_command_timeout"
    assert time.monotonic() - started < 1.5


def test_git_process_bounds_stderr_exposed_by_failed_commands(tmp_path: Path) -> None:
    executable = tmp_path / "noisy-git"
    executable.write_text(
        "#!/bin/sh\n/usr/bin/dd if=/dev/zero bs=200000 count=1 1>&2\nexit 1\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    process = GitProcess(git_executable=str(executable), output_limit_bytes=1024)

    with pytest.raises(GitCommandFailedError) as error:
        process.rev_parse_show_toplevel(tmp_path)

    assert len(error.value.stderr.encode("utf-8")) <= 1024


def test_worktree_add_failure_after_filesystem_creation_is_compensated(
    tmp_path: Path, database: Database
) -> None:
    repository = _repository(tmp_path)
    real_git = GitProcess()

    class FailingAddGitProcess(GitProcess):
        def worktree_add(
            self,
            cwd: Path,
            worktree_root: Path,
            branch: str,
            base_commit: str,
        ) -> None:
            real_git.worktree_add(cwd, worktree_root, branch, base_commit)
            raise GitCommandFailedError("worktree-add", returncode=1, stderr="after add")

    manager = WorktreeManager(
        database,
        managed_root=tmp_path / "managed",
        git_process=FailingAddGitProcess(),
    )

    with pytest.raises(WorktreeError) as error:
        manager.create(repository)

    assert error.value.code == "worktree_create_failed"
    assert database.connection().execute(
        "SELECT COUNT(*) FROM worktrees"
    ).fetchone()[0] == 0
    assert not list((tmp_path / "managed").glob("*"))


def test_persistence_failure_after_git_add_is_compensated(
    tmp_path: Path, database: Database
) -> None:
    repository = _repository(tmp_path)

    class FailingRepository(ProjectWorktreeRepository):
        def insert_worktree(self, worktree: Worktree) -> Worktree:
            raise StorageError("injected persistence failure")

    manager = WorktreeManager(
        database,
        managed_root=tmp_path / "managed",
        repository=FailingRepository(database),
        id_factory=lambda: "wt_failedbranch",
    )

    with pytest.raises(WorktreeError) as error:
        manager.create(repository)

    assert error.value.code == "worktree_persistence_failed"
    assert database.connection().execute(
        "SELECT COUNT(*) FROM worktrees"
    ).fetchone()[0] == 0
    assert not list((tmp_path / "managed").glob("*"))
    branch = subprocess.run(
        ["git", "rev-parse", "--verify", "refs/heads/eidos/failedbranch"],
        cwd=repository,
        capture_output=True,
        text=True,
    )
    assert branch.returncode != 0


def test_create_compensation_preserves_runtime_branch_after_branch_advanced(
    tmp_path: Path, database: Database
) -> None:
    repository = _repository(tmp_path)

    class AdvancingRepository(ProjectWorktreeRepository):
        def insert_worktree(self, worktree: Worktree) -> Worktree:
            root = Path(worktree.worktree_root)
            (root / "advanced.txt").write_text("advanced\n", encoding="utf-8")
            _git(root, "add", "advanced.txt")
            _git(root, "commit", "-qm", "advance runtime branch")
            raise StorageError("injected persistence failure")

    manager = WorktreeManager(
        database,
        managed_root=tmp_path / "managed",
        repository=AdvancingRepository(database),
        id_factory=lambda: "wt_advbranch",
    )

    with pytest.raises(WorktreeError) as error:
        manager.create(repository)

    assert error.value.code == "worktree_persistence_failed"
    assert not list((tmp_path / "managed").glob("*"))
    assert _git(repository, "rev-parse", "refs/heads/eidos/advbranch") != ""
    assert _git(repository, "rev-parse", "refs/heads/eidos/advbranch") != _git(
        repository, "rev-parse", "HEAD"
    )


def test_clean_delete_preserves_branch_and_dirty_delete_is_rejected(
    tmp_path: Path, database: Database
) -> None:
    repository = _repository(tmp_path)
    manager = WorktreeManager(database, managed_root=tmp_path / "managed")
    clean = manager.create(repository)

    deleted = manager.delete(clean.id)
    assert deleted.state.value == "deleted"
    assert not Path(clean.worktree_root).exists()
    assert _git(repository, "rev-parse", f"refs/heads/{clean.branch}") == clean.base_commit

    dirty = manager.create(repository)
    (Path(dirty.worktree_root) / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(WorktreeError) as error:
        manager.delete(dirty.id)
    assert error.value.code == "worktree_dirty"
    assert Path(dirty.worktree_root).exists()


def test_deleted_worktree_is_terminal_when_the_original_path_reappears(
    tmp_path: Path, database: Database
) -> None:
    repository = _repository(tmp_path)
    manager = WorktreeManager(database, managed_root=tmp_path / "managed")
    worktree = manager.create(repository)
    deleted = manager.delete(worktree.id)

    _git(
        repository,
        "worktree",
        "add",
        "-q",
        str(Path(deleted.worktree_root)),
        deleted.branch,
    )

    validation = manager.validate(deleted.id)
    assert not validation.valid
    assert validation.code == "worktree_deleted"

    report = manager.recover()
    view = manager.list()[0]
    assert report.updated_worktrees == ()
    assert view.actual_present
    assert view.worktree.state is WorktreeState.DELETED
    assert manager.repository.read_worktree(deleted.id).state is WorktreeState.DELETED


def test_truncated_status_does_not_mutate_lifecycle_state(
    tmp_path: Path, database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = _repository(tmp_path)
    manager = WorktreeManager(database, managed_root=tmp_path / "managed")
    worktree = manager.create(repository)
    manager.repository.update_state(worktree.id, WorktreeState.INVALID)

    def truncated_status(cwd: Path) -> GitCommandResult:
        return GitCommandResult(
            stdout="? untrusted\x00",
            stderr="",
            returncode=0,
            stdout_truncated=True,
            stderr_truncated=False,
        )

    monkeypatch.setattr(manager.git, "status_porcelain_v2", truncated_status)

    with pytest.raises(WorktreeError) as error:
        manager.status(worktree.id)

    assert error.value.code == "git_observation_incomplete"
    assert manager.repository.read_worktree(worktree.id).state is WorktreeState.INVALID


def test_recovery_marks_missing_and_reports_git_orphan_without_deleting_it(
    tmp_path: Path, database: Database
) -> None:
    repository = _repository(tmp_path)
    manager = WorktreeManager(database, managed_root=tmp_path / "managed")
    known = manager.create(repository)
    missing_root = Path(known.worktree_root)
    missing_root.rename(tmp_path / "moved-away")

    orphan_root = tmp_path / "orphan"
    _git(repository, "worktree", "add", "-q", "-b", "orphan/branch", str(orphan_root), "HEAD")

    report = manager.recover()
    recovered = manager.open(known.id, allow_inactive=True)
    assert recovered.state.value == "missing"
    assert report.orphan_candidates
    assert report.orphan_candidates[0].worktree_root == str(orphan_root.resolve())
    assert orphan_root.exists()


def test_cleanup_does_not_mark_missing_record_deleted_while_git_metadata_remains(
    tmp_path: Path, database: Database
) -> None:
    repository = _repository(tmp_path)
    manager = WorktreeManager(database, managed_root=tmp_path / "managed")
    worktree = manager.create(repository)
    root = Path(worktree.worktree_root)
    (Path(worktree.git_dir) / "locked").write_text("keep\n", encoding="utf-8")
    root.rename(tmp_path / "moved-away")

    manager.cleanup()

    assert manager.open(worktree.id, allow_inactive=True).state is WorktreeState.MISSING
    assert _git(repository, "worktree", "list", "--porcelain").find(
        str(root)
    ) >= 0


def test_recovery_marks_a_worktree_with_missing_git_metadata_as_missing(
    tmp_path: Path, database: Database
) -> None:
    repository = _repository(tmp_path)
    manager = WorktreeManager(database, managed_root=tmp_path / "managed")
    known = manager.create(repository)
    root = Path(known.worktree_root)

    _git(repository, "worktree", "remove", "--force", str(root))
    root.mkdir()
    (root / "not-a-git-worktree.txt").write_text("plain\n", encoding="utf-8")

    report = manager.recover()

    assert any(item.id == known.id and item.state.value == "missing" for item in report.updated_worktrees)
    assert manager.open(known.id, allow_inactive=True).state.value == "missing"


def test_validation_reports_repository_mismatch_and_persists_invalid_state(
    tmp_path: Path, database: Database
) -> None:
    repository = _repository(tmp_path)
    manager = WorktreeManager(database, managed_root=tmp_path / "managed")
    worktree = manager.create(repository)
    database.connection().execute(
        "UPDATE projects SET git_common_dir = ? WHERE id = ?",
        (str((tmp_path / "different-common").resolve()), worktree.project_id),
    )
    database.connection().commit()

    validation = manager.validate(worktree.id)

    assert not validation.valid
    assert validation.code == "worktree_repository_mismatch"
    assert validation.worktree.state.value == "invalid"


def test_recovery_reports_replaced_worktree_repository_as_invalid(
    tmp_path: Path, database: Database
) -> None:
    repository = _repository(tmp_path)
    manager = WorktreeManager(database, managed_root=tmp_path / "managed")
    worktree = manager.create(repository)
    root = Path(worktree.worktree_root)

    _git(repository, "worktree", "remove", "--force", str(root))
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "replacement@example.com")
    _git(root, "config", "user.name", "Replacement")
    (root / "replacement.txt").write_text("replacement\n", encoding="utf-8")
    _git(root, "add", "replacement.txt")
    _git(root, "commit", "-qm", "replacement")

    report = manager.recover()

    assert any(
        item.id == worktree.id and item.state is WorktreeState.INVALID
        for item in report.updated_worktrees
    )
    assert manager.open(worktree.id, allow_inactive=True).state is WorktreeState.INVALID


def test_schema_is_current_and_has_project_worktree_tables(database: Database) -> None:
    assert SCHEMA_VERSION == 15
    tables = {
        row[0]
        for row in database.connection().execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert {"projects", "worktrees"} <= tables
    assert database.connection().execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert database.connection().execute("PRAGMA foreign_key_check").fetchall() == []

from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from eidos_runtime.db.database import Database
from eidos_runtime.domain.worktree import WorktreeState
from eidos_runtime.git.errors import (
    GitCommandFailedError,
    GitCommandTimeoutError,
    WorktreeError,
)
from eidos_runtime.git.manager import WorktreeManager
from eidos_runtime.git.models import GitDiffObservation, GitStatusObservation
from eidos_runtime.git.status import DiffScope
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
    (repository / "README.md").write_text("base\n", encoding="utf-8")
    _git(repository, "add", "README.md")
    _git(repository, "commit", "-qm", "initial")
    return repository


@pytest.fixture
def database(tmp_path: Path):
    database = Database(tmp_path / "data")
    database.initialize()
    yield database
    database.close()


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    (
        (GitCommandTimeoutError("discover"), "git_command_timeout"),
        (
            GitCommandFailedError(
                "discover", returncode=2, stderr="temporary failure"
            ),
            "git_observation_failed",
        ),
    ),
)
def test_validate_discovery_observation_failure_keeps_active_state(
    tmp_path: Path,
    database: Database,
    failure: Exception,
    expected_code: str,
) -> None:
    repository = _repository(tmp_path)
    backend = FakeGitBackend()
    manager = WorktreeManager(
        database,
        managed_root=tmp_path / "managed",
        git_backend=backend,
    )
    worktree = manager.create(repository)
    backend.failures["discover"] = failure

    validation = manager.validate(worktree.id)

    assert not validation.valid
    assert validation.code == expected_code
    assert validation.worktree.state is WorktreeState.ACTIVE
    assert manager.repository.read_worktree(worktree.id).state is WorktreeState.ACTIVE


@pytest.mark.parametrize(
    ("operation", "failure", "expected_code"),
    (
        (
            "current_branch",
            GitCommandTimeoutError("current-branch"),
            "git_command_timeout",
        ),
        (
            "current_branch",
            GitCommandFailedError(
                "current-branch", returncode=2, stderr="temporary failure"
            ),
            "git_observation_failed",
        ),
        (
            "head",
            GitCommandFailedError(
                "head", returncode=2, stderr="temporary failure"
            ),
            "git_observation_failed",
        ),
    ),
)
def test_validate_head_observation_failure_keeps_active_state(
    tmp_path: Path,
    database: Database,
    operation: str,
    failure: Exception,
    expected_code: str,
) -> None:
    repository = _repository(tmp_path)
    backend = FakeGitBackend()
    manager = WorktreeManager(
        database,
        managed_root=tmp_path / "managed",
        git_backend=backend,
    )
    worktree = manager.create(repository)
    backend.failures[operation] = failure

    validation = manager.validate(worktree.id)

    assert not validation.valid
    assert validation.code == expected_code
    assert validation.worktree.state is WorktreeState.ACTIVE


def test_typed_backend_failures_do_not_mutate_worktree_lifecycle_state(
    tmp_path: Path,
    database: Database,
) -> None:
    repository = _repository(tmp_path)
    backend = FakeGitBackend()
    manager = WorktreeManager(
        database,
        managed_root=tmp_path / "managed",
        git_backend=backend,
    )
    worktrees = [manager.create(repository) for _ in range(4)]
    backend.failures["worktree_list"] = GitCommandFailedError(
        "worktree-list", returncode=2, stderr="temporary observation failure"
    )

    operations = (
        lambda: manager.validate(worktrees[0].id),
        lambda: manager.list(),
        lambda: manager.delete(worktrees[3].id),
    )
    for operation in operations:
        with pytest.raises(WorktreeError) as error:
            operation()
        assert error.value.code == "git_observation_failed"
    assert manager.recover().updated_worktrees == ()
    assert all(
        manager.repository.read_worktree(worktree.id).state is WorktreeState.ACTIVE
        for worktree in worktrees
    )


def test_typed_status_failure_does_not_mutate_existing_state(
    tmp_path: Path,
    database: Database,
) -> None:
    repository = _repository(tmp_path)
    backend = FakeGitBackend()
    manager = WorktreeManager(
        database,
        managed_root=tmp_path / "managed",
        git_backend=backend,
    )
    worktree = manager.create(repository)
    manager.repository.update_state(worktree.id, WorktreeState.INVALID)
    backend.failures["status"] = GitCommandFailedError(
        "status", returncode=2, stderr="temporary observation failure"
    )

    with pytest.raises(WorktreeError) as error:
        manager.status(worktree.id)

    assert error.value.code == "git_command_failed"
    assert manager.repository.read_worktree(worktree.id).state is WorktreeState.INVALID


def test_typed_diff_failure_maps_to_stable_error(
    tmp_path: Path,
    database: Database,
) -> None:
    repository = _repository(tmp_path)
    backend = FakeGitBackend()
    manager = WorktreeManager(
        database,
        managed_root=tmp_path / "managed",
        git_backend=backend,
    )
    worktree = manager.create(repository)
    backend.failures["diff"] = GitCommandTimeoutError("diff")

    with pytest.raises(WorktreeError) as error:
        manager.diff(worktree.id, scope=DiffScope.HEAD)

    assert error.value.code == "git_command_timeout"


def test_typed_fake_backend_returns_domain_observations() -> None:
    status = GitStatusObservation(
        head="a" * 40,
        branch="main",
        staged_paths=("staged.txt",),
        unstaged_paths=(),
        untracked_paths=(),
        conflict_paths=(),
    )
    diff = GitDiffObservation(
        patch="patch",
        changed_paths=("staged.txt",),
        truncated=False,
        additions=1,
        deletions=0,
        stats_incomplete=False,
    )

    assert status.dirty
    assert diff.changed_paths == ("staged.txt",)

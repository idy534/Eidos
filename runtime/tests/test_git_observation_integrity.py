from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import subprocess

import pytest

from eidos_runtime.db.database import Database
from eidos_runtime.domain.worktree import WorktreeState
from eidos_runtime.git.errors import GitCommandFailedError, GitCommandTimeoutError
from eidos_runtime.git.manager import WorktreeManager
from eidos_runtime.git.process import GitCommandResult, GitProcess
from eidos_runtime.git.status import parse_porcelain_v2_status


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


def _single_line_calls(process: GitProcess, cwd: Path) -> tuple[Callable[[], object], ...]:
    return (
        lambda: process.rev_parse_show_toplevel(cwd),
        lambda: process.resolve_ref(cwd, "HEAD"),
        lambda: process.try_resolve_ref(cwd, "HEAD"),
        lambda: process.symbolic_ref_short(cwd),
    )


@pytest.mark.parametrize(
    ("stdout_truncated", "stderr_truncated"),
    ((True, False), (False, True)),
)
def test_successful_single_line_git_observations_reject_truncation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stdout_truncated: bool,
    stderr_truncated: bool,
) -> None:
    process = GitProcess()

    def execute(
        operation: str,
        cwd: Path,
        args: tuple[str, ...],
        *,
        output_limit_bytes: int,
    ) -> GitCommandResult:
        del operation, cwd, args, output_limit_bytes
        return GitCommandResult(
            stdout="observed-value\n",
            stderr="diagnostic" if stderr_truncated else "",
            returncode=0,
            stdout_truncated=stdout_truncated,
            stderr_truncated=stderr_truncated,
        )

    monkeypatch.setattr(process, "_execute", execute)

    for operation in _single_line_calls(process, tmp_path):
        with pytest.raises(GitCommandFailedError):
            operation()


def test_expected_nonzero_single_line_observations_still_reject_truncation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = GitProcess()

    def execute(
        operation: str,
        cwd: Path,
        args: tuple[str, ...],
        *,
        output_limit_bytes: int,
    ) -> GitCommandResult:
        del operation, cwd, args, output_limit_bytes
        return GitCommandResult(
            stdout="",
            stderr="incomplete",
            returncode=1,
            stdout_truncated=False,
            stderr_truncated=True,
        )

    monkeypatch.setattr(process, "_execute", execute)

    with pytest.raises(GitCommandFailedError):
        process.try_resolve_ref(tmp_path, "HEAD")
    with pytest.raises(GitCommandFailedError):
        process.symbolic_ref_short(tmp_path)


@pytest.mark.parametrize("stdout", ("first\nsecond\n", "", "   \n"))
def test_successful_single_line_git_observations_reject_non_single_line_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stdout: str,
) -> None:
    process = GitProcess()

    def execute(
        operation: str,
        cwd: Path,
        args: tuple[str, ...],
        *,
        output_limit_bytes: int,
    ) -> GitCommandResult:
        del operation, cwd, args, output_limit_bytes
        return GitCommandResult(
            stdout=stdout,
            stderr="",
            returncode=0,
            stdout_truncated=False,
            stderr_truncated=False,
        )

    monkeypatch.setattr(process, "_execute", execute)

    for operation in _single_line_calls(process, tmp_path):
        with pytest.raises(GitCommandFailedError):
            operation()


@pytest.mark.parametrize(
    "output",
    (
        "?",
        "? ",
        "1 M.",
        "1 M. N... 100644 100644 100644 head index",
        "u UU N... 100644 100644 100644 100644 one two three",
        "x unknown",
        "\x00",
    ),
)
def test_porcelain_v2_status_rejects_malformed_and_unknown_records(output: str) -> None:
    with pytest.raises(ValueError):
        parse_porcelain_v2_status(output)


def test_porcelain_v2_status_counts_complete_records() -> None:
    output = (
        "1 M. N... 100644 100644 100644 head index tracked file\x00"
        "? untracked file\x00"
        "u UU N... 100644 100644 100644 100644 one two three conflict file\x00"
    )

    assert parse_porcelain_v2_status(output) == (2, 1, 1, 1)


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    (
        (GitCommandTimeoutError("rev-parse-show-toplevel"), "git_command_timeout"),
        (
            GitCommandFailedError(
                "rev-parse-show-toplevel",
                returncode=2,
                stderr="temporary failure",
            ),
            "git_observation_failed",
        ),
    ),
)
def test_validate_discovery_observation_failure_keeps_active_state(
    tmp_path: Path,
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    expected_code: str,
) -> None:
    repository = _repository(tmp_path)
    manager = WorktreeManager(database, managed_root=tmp_path / "managed")
    worktree = manager.create(repository)

    def fail_discovery(cwd: Path) -> str:
        del cwd
        raise failure

    monkeypatch.setattr(manager.git, "rev_parse_show_toplevel", fail_discovery)

    validation = manager.validate(worktree.id)

    assert not validation.valid
    assert validation.code == expected_code
    assert validation.worktree.state is WorktreeState.ACTIVE
    assert manager.repository.read_worktree(worktree.id).state is WorktreeState.ACTIVE


@pytest.mark.parametrize(
    ("method_name", "failure", "expected_code"),
    (
        (
            "symbolic_ref_short",
            GitCommandTimeoutError("symbolic-ref-short"),
            "git_command_timeout",
        ),
        (
            "symbolic_ref_short",
            GitCommandFailedError(
                "symbolic-ref-short", returncode=2, stderr="temporary failure"
            ),
            "git_observation_failed",
        ),
        (
            "resolve_ref",
            GitCommandFailedError(
                "rev-parse-ref", returncode=2, stderr="temporary failure"
            ),
            "git_observation_failed",
        ),
    ),
)
def test_validate_branch_and_head_observation_failure_keeps_active_state(
    tmp_path: Path,
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
    method_name: str,
    failure: Exception,
    expected_code: str,
) -> None:
    repository = _repository(tmp_path)
    manager = WorktreeManager(database, managed_root=tmp_path / "managed")
    worktree = manager.create(repository)

    def fail_observation(*args: object, **kwargs: object) -> str:
        del args, kwargs
        raise failure

    monkeypatch.setattr(manager.git, method_name, fail_observation)

    validation = manager.validate(worktree.id)

    assert not validation.valid
    assert validation.code == expected_code
    assert validation.worktree.state is WorktreeState.ACTIVE
    assert manager.repository.read_worktree(worktree.id).state is WorktreeState.ACTIVE

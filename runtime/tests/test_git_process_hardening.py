from __future__ import annotations

from pathlib import Path
import subprocess
import time

import pytest

from eidos_runtime.git.backend import DulwichGitBackend
from eidos_runtime.git.errors import GitCommandFailedError, GitCommandTimeoutError
from eidos_runtime.git.native import (
    HardenedGitRunner,
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
    (repository / "README.txt").write_text("base\n", encoding="utf-8")
    _git(repository, "add", "README.txt")
    _git(repository, "commit", "-qm", "initial")
    return repository


def _marker_executable(tmp_path: Path, marker: Path) -> Path:
    executable = tmp_path / f"marker-{marker.name}.sh"
    executable.write_text(
        "#!/bin/sh\n"
        f"printf marker >> '{marker}'\n"
        "cat\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    return executable


def test_read_observation_and_native_worktree_create_disable_helpers(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    marker = tmp_path / "helper-ran"
    executable = _marker_executable(tmp_path, marker)
    (repository / ".gitattributes").write_text(
        "*.txt diff=evil filter=evil.driver\n", encoding="utf-8"
    )
    _git(repository, "add", ".gitattributes")
    _git(repository, "commit", "-qm", "configure helpers")
    _git(repository, "config", "core.hooksPath", ".hooks")
    (repository / ".hooks").mkdir()
    hook = repository / ".hooks" / "post-checkout"
    hook.write_text(
        "#!/bin/sh\n"
        f"printf marker >> '{marker}'\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)
    _git(repository, "config", "core.fsmonitor", str(executable))
    _git(repository, "config", "filter.evil.driver.clean", str(executable))
    _git(repository, "config", "filter.evil.driver.process", str(executable))
    _git(repository, "config", "filter.evil.driver.required", "true")
    _git(repository, "config", "diff.evil.textconv", str(executable))
    _git(repository, "config", "diff.external", str(executable))
    (repository / "README.txt").write_text("changed\n", encoding="utf-8")
    backend = DulwichGitBackend()

    backend.status(repository)
    backend.diff(repository, base_commit=backend.head(repository))
    linked = tmp_path / "linked"
    backend.worktree_add(repository, linked, "eidos/helper-safety", backend.head(repository))

    assert not marker.exists()


def test_handoff_cleanup_preserves_ignored_files_but_compensation_removes_them(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    (repository / ".gitignore").write_text(
        ".env\nnode_modules/\n", encoding="utf-8"
    )
    _git(repository, "add", ".gitignore")
    _git(repository, "commit", "-qm", "ignore environment")
    (repository / ".env").write_text("TOKEN=test\n", encoding="utf-8")
    (repository / "node_modules").mkdir()
    (repository / "node_modules" / "example").write_text(
        "installed\n", encoding="utf-8"
    )
    (repository / "README.txt").write_text("dirty\n", encoding="utf-8")
    (repository / "untracked.txt").write_text("remove\n", encoding="utf-8")

    backend = DulwichGitBackend()
    backend.clean_worktree_after_handoff(repository)

    assert (repository / ".env").exists()
    assert (repository / "node_modules" / "example").exists()
    assert not (repository / "untracked.txt").exists()
    assert (repository / "README.txt").read_text(encoding="utf-8") == "base\n"

    (repository / "untracked.txt").write_text("remove\n", encoding="utf-8")
    backend.clean_worktree_for_compensation(repository)

    assert not (repository / ".env").exists()
    assert not (repository / "node_modules").exists()
    assert not (repository / "untracked.txt").exists()


def test_dotted_and_worktree_specific_filters_are_disabled_for_native_create(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    marker = tmp_path / "filter-ran"
    executable = _marker_executable(tmp_path, marker)
    (repository / ".gitattributes").write_text(
        "*.txt filter=evil.driver\n", encoding="utf-8"
    )
    _git(repository, "add", ".gitattributes")
    _git(repository, "commit", "-qm", "configure dotted filter")
    _git(repository, "config", "extensions.worktreeConfig", "true")
    _git(repository, "config", "filter.evil.driver.clean", str(executable))
    linked = tmp_path / "linked-worktree"
    DulwichGitBackend().worktree_add(
        repository,
        linked,
        "eidos/filter-safety",
        DulwichGitBackend().head(repository),
    )
    _git(linked, "config", "--worktree", "filter.evil.driver.process", str(executable))
    _git(linked, "config", "--worktree", "filter.evil.driver.clean", str(executable))

    second = tmp_path / "second-worktree"
    backend = DulwichGitBackend()
    backend.worktree_add(linked, second, "eidos/filter-safety-2", backend.head(linked))

    assert not marker.exists()


def test_hardened_runner_timeout_is_bounded(tmp_path: Path) -> None:
    executable = tmp_path / "slow-git"
    executable.write_text("#!/bin/sh\nsleep 2\n", encoding="utf-8")
    executable.chmod(0o755)
    runner = HardenedGitRunner(git_executable=str(executable), timeout_seconds=0.05)

    started = time.monotonic()
    with pytest.raises(GitCommandTimeoutError) as error:
        runner.run(
            ("rev-parse", "HEAD"),
            cwd=tmp_path,
            operation="runner-timeout",
        )

    assert error.value.code == "git_command_timeout"
    assert time.monotonic() - started < 1.5


def test_hardened_runner_bounds_failed_stderr(tmp_path: Path) -> None:
    executable = tmp_path / "noisy-git"
    executable.write_text(
        "#!/bin/sh\n/usr/bin/dd if=/dev/zero bs=200000 count=1 1>&2\nexit 1\n",
        encoding="utf-8",
    )
    executable.chmod(0o755)
    runner = HardenedGitRunner(git_executable=str(executable), output_limit_bytes=1024)

    with pytest.raises(GitCommandFailedError) as error:
        runner.run(
            ("rev-parse", "HEAD"),
            cwd=tmp_path,
            operation="runner-noisy",
        )

    assert len(error.value.stderr.encode("utf-8")) <= 1024


def test_hardened_runner_passes_raw_stdin_bytes_without_decode(
    tmp_path: Path,
) -> None:
    executable = tmp_path / "raw-git"
    executable.write_text("#!/bin/sh\ncat\n", encoding="utf-8")
    executable.chmod(0o755)
    runner = HardenedGitRunner(git_executable=str(executable))

    result = runner.run(
        ("apply",),
        cwd=tmp_path,
        operation="runner-raw-stdin",
        stdin=b"binary\x00\xff",
    )

    assert result.stdout == b"binary\x00\xff"

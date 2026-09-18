from __future__ import annotations

import subprocess
from pathlib import Path

from eidos_runtime.git.backend import DulwichGitBackend
from eidos_runtime.git.native import GitCli


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-q", "-b", "main")
    _git(repository, "config", "user.email", "eidos-tests@example.com")
    _git(repository, "config", "user.name", "Eidos Tests")
    (repository / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "initial")
    return repository


def test_small_untracked_count_keeps_full_diff(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    backend = DulwichGitBackend()
    head = backend.head(repository)
    (repository / "a.txt").write_text("a\n", encoding="utf-8")
    (repository / "b.txt").write_text("b\n", encoding="utf-8")

    diff = backend.diff(repository, base_commit=head)

    assert diff.truncated is False
    assert diff.stats_incomplete is False
    assert set(diff.changed_paths) == {"a.txt", "b.txt"}
    assert "a" in diff.patch


def test_over_cap_truncates_without_per_file_storm(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    cli = GitCli(max_untracked_diff_files=2)
    backend = DulwichGitBackend(git_cli=cli)
    head = backend.head(repository)
    for name in ("u1.txt", "u2.txt", "u3.txt", "u4.txt"):
        (repository / name).write_text(f"{name}\n", encoding="utf-8")

    calls: list[tuple[str, ...]] = []
    original_run = cli._runner.run

    def counting_run(args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(tuple(args))
        return original_run(args, **kwargs)

    cli._runner.run = counting_run  # type: ignore[method-assign]
    try:
        diff = backend.diff(repository, base_commit=head)
    finally:
        cli._runner.run = original_run  # type: ignore[method-assign]

    assert diff.truncated is True
    assert diff.stats_incomplete is True
    # Over-cap must not spawn per-file --no-index subprocesses.
    no_index_calls = [argv for argv in calls if "--no-index" in argv]
    assert no_index_calls == []
    # Changed paths stay bounded by the cap plus tracked files.
    assert len(diff.changed_paths) <= 2


def test_path_scoped_untracked_bypasses_cap(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    cli = GitCli(max_untracked_diff_files=1)
    backend = DulwichGitBackend(git_cli=cli)
    head = backend.head(repository)
    (repository / "keep.txt").write_text("keep\n", encoding="utf-8")
    (repository / "other.txt").write_text("other\n", encoding="utf-8")

    diff = backend.diff(repository, base_commit=head, path="keep.txt")

    assert diff.changed_paths == ("keep.txt",)
    assert "keep" in diff.patch

from __future__ import annotations

import subprocess
from pathlib import Path

from eidos_runtime.git.backend import DulwichGitBackend
from eidos_runtime.git.native import GitCli


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )
    return completed.stdout.strip()


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


def test_batched_diff_matches_per_file_result(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    (repository / "tracked.txt").write_text("base\nchanged\n", encoding="utf-8")
    (repository / "new.txt").write_text("hello\nworld\n", encoding="utf-8")
    (repository / "binary.dat").write_bytes(b"\x00\x01\x02")

    batched_backend = DulwichGitBackend(git_cli=GitCli())
    head = batched_backend.head(repository)
    batched = batched_backend.diff(repository, base_commit=head)

    legacy_cli = GitCli()
    legacy_cli._batched_diff_with_temp_index = lambda *a, **k: None  # type: ignore[method-assign]
    legacy_backend = DulwichGitBackend(git_cli=legacy_cli)
    legacy = legacy_backend.diff(repository, base_commit=head)

    assert set(batched.changed_paths) == set(legacy.changed_paths)
    assert set(stat.path for stat in batched.file_stats) == set(
        stat.path for stat in legacy.file_stats
    )
    assert batched.additions == legacy.additions
    assert batched.deletions == legacy.deletions
    assert batched.stats_incomplete == legacy.stats_incomplete
    assert "hello" in batched.patch


def test_batched_diff_uses_constant_subprocesses(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    cli = GitCli()
    backend = DulwichGitBackend(git_cli=cli)
    head = backend.head(repository)
    for index in range(20):
        (repository / f"file-{index:02d}.txt").write_text(
            f"content {index}\n", encoding="utf-8"
        )

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

    assert set(diff.changed_paths) == {f"file-{index:02d}.txt" for index in range(20)}
    no_index_calls = [argv for argv in calls if "--no-index" in argv]
    assert no_index_calls == []
    # 1 ls-files + read-tree + add chunks + 3 diffs stays small.
    assert len(calls) <= 8


def test_batched_diff_leaves_real_index_untouched(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    (repository / "new.txt").write_text("new\n", encoding="utf-8")
    backend = DulwichGitBackend(git_cli=GitCli())
    head = backend.head(repository)

    before_status = _git(repository, "status", "--porcelain=v1", "--", ".")
    before_cached = _git(repository, "diff", "--cached", "--name-only", "--", ".")

    backend.diff(repository, base_commit=head)

    after_status = _git(repository, "status", "--porcelain=v1", "--", ".")
    after_cached = _git(repository, "diff", "--cached", "--name-only", "--", ".")
    assert before_status == after_status
    assert before_cached == after_cached == ""

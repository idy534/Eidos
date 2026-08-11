from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess

import pytest

from eidos_runtime.git.backend import DulwichGitBackend
from eidos_runtime.git.errors import GitCommandFailedError
from eidos_runtime.git.models import GitDiffObservation, GitStatusObservation


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
    (repository / "tracked.txt").write_text("base\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "initial")
    return repository


def _metadata_snapshot(repository: Path) -> dict[str, object]:
    git_dir = Path(_git(repository, "rev-parse", "--git-dir")).resolve()
    ref_files = tuple(
        sorted(
            (
                str(path.relative_to(git_dir)),
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
            for path in git_dir.joinpath("refs").rglob("*")
            if path.is_file()
        )
    )
    objects = tuple(
        sorted(
            str(path.relative_to(git_dir / "objects"))
            for path in (git_dir / "objects").rglob("*")
            if path.is_file() and len(path.name) == 38
        )
    )
    return {
        "head": (git_dir / "HEAD").read_bytes(),
        "index": (git_dir / "index").read_bytes(),
        "refs": ref_files,
        "packed_refs": (
            (git_dir / "packed-refs").read_bytes()
            if (git_dir / "packed-refs").exists()
            else None
        ),
        "config": (git_dir / "config").read_bytes(),
        "objects": objects,
    }


def test_typed_backend_surface_has_no_cli_result_facade(tmp_path: Path) -> None:
    backend = DulwichGitBackend()

    assert not hasattr(backend, "status_porcelain_v2")
    assert not hasattr(backend, "diff_head")
    assert not hasattr(backend, "diff_name_only")
    assert not hasattr(backend, "untracked_files")
    assert not hasattr(backend, "rev_parse_show_toplevel")
    assert not hasattr(backend, "update_ref_delete")

    repository = _repository(tmp_path)
    discovery = backend.discover(repository)
    assert discovery.repository_root == str(repository.resolve())
    assert backend.current_branch(repository) == "main"
    assert backend.head(repository) == _git(repository, "rev-parse", "HEAD")
    assert backend.status(repository) == GitStatusObservation(
        head=backend.head(repository),
        branch="main",
        staged_paths=(),
        unstaged_paths=(),
        untracked_paths=(),
        conflict_paths=(),
    )


def test_try_discover_treats_non_git_workspace_as_missing_capability(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    assert DulwichGitBackend().try_discover(workspace) is None


def test_try_discover_propagates_broken_git_metadata(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    config_path = repository / ".git" / "config"
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "repositoryformatversion = 0", "repositoryformatversion = malformed"
        ),
        encoding="utf-8",
    )

    with pytest.raises(GitCommandFailedError) as error:
        DulwichGitBackend().try_discover(repository)

    assert error.value.operation == "discover"
    assert error.value.returncode == 128


def test_try_discover_rejects_missing_workspace(tmp_path: Path) -> None:
    missing = tmp_path / "missing"

    with pytest.raises(GitCommandFailedError) as error:
        DulwichGitBackend().try_discover(missing)

    assert error.value.operation == "discover"
    assert error.value.returncode is None


def test_linked_worktree_status_isolated_from_main_and_handles_unicode(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    linked = tmp_path / "linked"
    _git(repository, "worktree", "add", "-q", "-b", "linked", str(linked), "HEAD")
    backend = DulwichGitBackend()

    (repository / "main-only.txt").write_text("main\n", encoding="utf-8")
    (linked / "linked-only.txt").write_text("linked\n", encoding="utf-8")
    unicode_path = linked / "文件.txt"
    unicode_path.write_text("unicode\n", encoding="utf-8")

    main_status = backend.status(repository)
    linked_status = backend.status(linked)

    assert main_status.untracked_paths == ("main-only.txt",)
    assert linked_status.untracked_paths == ("linked-only.txt", "文件.txt")
    assert "main-only.txt" not in linked_status.untracked_paths
    assert "main-only.txt" not in backend.diff(linked, base_commit=backend.head(linked)).changed_paths


def test_linked_worktree_status_excludes_main_tracked_changes(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    (repository / "main-only.txt").write_text("base\n", encoding="utf-8")
    (repository / "linked-only.txt").write_text("base\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "add linked isolation fixtures")
    linked = tmp_path / "linked"
    _git(repository, "worktree", "add", "-q", "-b", "linked", str(linked), "HEAD")
    backend = DulwichGitBackend()

    (repository / "main-only.txt").write_text("main change\n", encoding="utf-8")
    (linked / "linked-only.txt").write_text("linked change\n", encoding="utf-8")

    assert backend.status(repository).unstaged_paths == ("main-only.txt",)
    assert backend.status(linked).unstaged_paths == ("linked-only.txt",)
    assert backend.diff(linked, base_commit=backend.head(linked)).changed_paths == (
        "linked-only.txt",
    )


def test_linked_worktree_staged_and_unstaged_paths_are_typed(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    linked = tmp_path / "linked"
    _git(repository, "worktree", "add", "-q", "-b", "linked", str(linked), "HEAD")
    backend = DulwichGitBackend()

    (linked / "tracked.txt").write_text("staged\n", encoding="utf-8")
    _git(linked, "add", "tracked.txt")
    (linked / "tracked.txt").write_text("unstaged after stage\n", encoding="utf-8")
    (linked / "new.txt").write_text("new\n", encoding="utf-8")

    observation = backend.status(linked)

    assert observation.staged_paths == ("tracked.txt",)
    assert observation.unstaged_paths == ("tracked.txt",)
    assert observation.untracked_paths == ("new.txt",)
    assert observation.dirty


def test_linked_worktree_conflict_is_reported_without_main_changes(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    linked = tmp_path / "linked"
    _git(repository, "worktree", "add", "-q", "-b", "linked", str(linked), "HEAD")
    backend = DulwichGitBackend()

    (repository / "tracked.txt").write_text("main change\n", encoding="utf-8")
    _git(repository, "commit", "-qam", "main change")
    (linked / "tracked.txt").write_text("linked change\n", encoding="utf-8")
    _git(linked, "commit", "-qam", "linked change")
    result = subprocess.run(
        ["git", "merge", "main"],
        cwd=linked,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0

    observation = backend.status(linked)
    assert observation.conflict_paths == ("tracked.txt",)
    assert backend.status(repository).dirty is False


def test_diff_is_typed_and_baseline_includes_history_and_workspace(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    backend = DulwichGitBackend()
    baseline = backend.head(repository)

    (repository / "committed.txt").write_text("committed\n", encoding="utf-8")
    _git(repository, "add", "committed.txt")
    _git(repository, "commit", "-qm", "after baseline")
    (repository / "staged.txt").write_text("staged\n", encoding="utf-8")
    _git(repository, "add", "staged.txt")
    (repository / "tracked.txt").write_text("unstaged\n", encoding="utf-8")
    (repository / "untracked.txt").write_text("untracked\n", encoding="utf-8")

    head_diff = backend.diff(repository, base_commit=backend.head(repository))
    baseline_diff = backend.diff(repository, base_commit=baseline)

    assert isinstance(head_diff, GitDiffObservation)
    assert set(head_diff.changed_paths) == {"staged.txt", "tracked.txt", "untracked.txt"}
    assert "committed.txt" not in head_diff.changed_paths
    assert set(baseline_diff.changed_paths) == {
        "committed.txt",
        "staged.txt",
        "tracked.txt",
        "untracked.txt",
    }
    assert "committed" in baseline_diff.patch
    assert "untracked" in baseline_diff.patch


def test_git_observation_does_not_mutate_repository_metadata(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    backend = DulwichGitBackend()
    linked = tmp_path / "linked"
    _git(repository, "worktree", "add", "-q", "-b", "linked", str(linked), "HEAD")
    before = _metadata_snapshot(repository)

    backend.discover(linked)
    backend.try_discover(linked)
    backend.head(linked)
    backend.current_branch(linked)
    backend.resolve_revision(linked, "HEAD")
    backend.branch_commit(linked, "main")
    backend.status(linked)
    backend.diff(linked, base_commit=backend.head(linked))
    backend.worktree_list(repository)

    assert _metadata_snapshot(repository) == before


def test_diff_reports_bounded_typed_truncation(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    (repository / "tracked.txt").write_text("a much longer changed value\n", encoding="utf-8")

    observation = DulwichGitBackend(diff_output_limit_bytes=8).diff(
        repository,
        base_commit=DulwichGitBackend().head(repository),
    )

    assert observation.truncated
    assert len(observation.patch.encode("utf-8")) <= 8
    assert observation.changed_paths == ("tracked.txt",)


def test_worktree_list_is_always_typed(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    linked = tmp_path / "linked"
    _git(repository, "worktree", "add", "-q", "--detach", str(linked), "HEAD")

    entries = DulwichGitBackend().worktree_list(repository)

    assert isinstance(entries, tuple)
    assert any(entry.worktree_root == str(linked.resolve()) and entry.branch is None for entry in entries)


@pytest.mark.parametrize("method_name", ("status_porcelain_v2", "diff_head", "diff_baseline", "diff_untracked"))
def test_backend_does_not_expose_legacy_git_result_methods(
    method_name: str,
) -> None:
    assert not hasattr(DulwichGitBackend, method_name)

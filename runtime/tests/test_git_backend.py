from __future__ import annotations

from pathlib import Path
import subprocess

from eidos_runtime.git.backend import DulwichGitBackend
from eidos_runtime.git.discovery import GitRepositoryDiscoveryService
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
    (repository / "tracked.txt").write_text("base\n", encoding="utf-8")
    (repository / "delete.txt").write_text("delete\n", encoding="utf-8")
    _git(repository, "add", ".")
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


def test_dulwich_backend_contract_covers_structured_discovery_status_and_diff(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    nested = repository / "nested"
    nested.mkdir()
    backend = DulwichGitBackend()
    discovery = GitRepositoryDiscoveryService(backend).discover(nested)
    head = _git(repository, "rev-parse", "HEAD")

    assert discovery.repository_root == str(repository.resolve())
    assert discovery.git_common_dir == str((repository / ".git").resolve())
    assert backend.resolve_ref(repository, "HEAD") == head
    assert backend.symbolic_ref_short(repository) == "main"

    (repository / "tracked.txt").write_text("unstaged\n", encoding="utf-8")
    (repository / "staged.txt").write_text("staged\n", encoding="utf-8")
    _git(repository, "add", "staged.txt")
    (repository / "delete.txt").unlink()
    unicode_path = repository / "nested" / "文件.txt"
    unicode_path.write_text("untracked\n", encoding="utf-8")

    observation = backend.status_observation(repository)
    assert observation.staged_paths == ("staged.txt",)
    assert observation.unstaged_paths == ("delete.txt", "tracked.txt")
    assert observation.untracked_paths == ("nested/文件.txt",)
    assert observation.conflict_paths == ()
    assert parse_porcelain_v2_status(
        backend.status_porcelain_v2(repository).stdout
    ) == (1, 2, 1, 0)

    names = backend.diff_name_only(repository, scope="head").stdout.split("\0")
    assert set(names[:-1]) == {
        "delete.txt",
        "staged.txt",
        "tracked.txt",
    }
    assert backend.untracked_files(repository).stdout == "nested/文件.txt\0"
    diff = backend.diff_head(repository)
    assert "unstaged" in diff.stdout
    assert "staged" in diff.stdout
    assert "nested/文件.txt" not in diff.stdout
    untracked_diff = backend.diff_untracked(repository, "nested/文件.txt")
    assert "nested/文件.txt" in untracked_diff.stdout


def test_dulwich_backend_contract_covers_linked_worktree_add_list_remove_and_prune(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    backend = DulwichGitBackend()
    head = backend.resolve_ref(repository, "HEAD")
    linked = tmp_path / "linked-worktree"

    backend.worktree_add(repository, linked, "eidos/backend-contract", head)
    entries = backend.worktree_list(repository)
    assert any(
        entry.worktree_root == str(linked.resolve())
        and entry.branch == "eidos/backend-contract"
        and entry.head == head
        for entry in entries
    )

    backend.worktree_remove(repository, linked)
    backend.worktree_prune(repository)
    assert all(entry.worktree_root != str(linked.resolve()) for entry in backend.worktree_list(repository))


def test_dulwich_backend_contract_reports_conflicts_and_unicode_paths(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    backend = DulwichGitBackend()
    (repository / "conflict.txt").write_text("base\n", encoding="utf-8")
    _git(repository, "add", "conflict.txt")
    _git(repository, "commit", "-qm", "conflict base")
    _git(repository, "checkout", "-qb", "feature")
    (repository / "conflict.txt").write_text("feature\n", encoding="utf-8")
    _git(repository, "commit", "-qam", "feature conflict")
    _git(repository, "checkout", "-q", "main")
    (repository / "conflict.txt").write_text("main\n", encoding="utf-8")
    _git(repository, "commit", "-qam", "main conflict")
    subprocess.run(
        ["git", "merge", "feature"],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
    )

    observation = backend.status_observation(repository)
    assert observation.conflict_paths == ("conflict.txt",)
    assert parse_porcelain_v2_status(
        backend.status_porcelain_v2(repository).stdout
    ) == (1, 1, 0, 1)


def test_dulwich_backend_never_executes_configured_git_helpers(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    marker = tmp_path / "helper-ran"
    executable = _marker_executable(tmp_path, marker)
    hooks = repository / ".hooks"
    hooks.mkdir()
    hook = hooks / "post-checkout"
    hook.write_text(
        "#!/bin/sh\n"
        f"printf marker >> '{marker}'\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)
    (repository / ".gitattributes").write_text(
        "*.txt diff=evil filter=evil.driver\n", encoding="utf-8"
    )
    _git(repository, "add", ".gitattributes")
    _git(repository, "commit", "-qm", "configure helpers")
    _git(repository, "config", "core.hooksPath", ".hooks")
    _git(repository, "config", "core.fsmonitor", str(executable))
    _git(repository, "config", "filter.evil.driver.clean", str(executable))
    _git(repository, "config", "filter.evil.driver.process", str(executable))
    _git(repository, "config", "filter.evil.driver.required", "true")
    _git(repository, "config", "diff.evil.textconv", str(executable))
    _git(repository, "config", "diff.external", str(executable))
    (repository / "README.txt").write_text("changed\n", encoding="utf-8")
    backend = DulwichGitBackend()

    backend.status_observation(repository)
    backend.diff_head(repository)
    backend.diff_name_only(repository, scope="head")
    head = backend.resolve_ref(repository, "HEAD")
    linked = tmp_path / "linked"
    backend.worktree_add(repository, linked, "eidos/helper-safety", head)

    assert not marker.exists()

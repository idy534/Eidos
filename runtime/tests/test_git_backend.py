from __future__ import annotations

from pathlib import Path
import shutil
import subprocess

import pytest

from eidos_runtime.git.backend import DulwichGitBackend
from eidos_runtime.git.errors import (
    GitConflictError,
    GitIdentityUnavailableError,
    GitNothingStagedError,
)
from eidos_runtime.git.models import GitDiffObservation, GitStatusObservation
from eidos_runtime.git.native import GitCli, HardenedGitRunner


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


def test_eidos_forensics_snapshot_is_excluded_from_this_repository_git_review() -> None:
    gitignore = Path(__file__).resolve().parents[2] / ".gitignore"

    assert ".eidos-forensics/" in gitignore.read_text(encoding="utf-8").splitlines()


def _repository_with_submodule(tmp_path: Path) -> tuple[Path, Path]:
    repository = _repository(tmp_path)
    source = tmp_path / "submodule-source"
    source.mkdir()
    _git(source, "init", "-q", "-b", "main")
    _git(source, "config", "user.email", "eidos-tests@example.com")
    _git(source, "config", "user.name", "Eidos Tests")
    (source / "child.txt").write_text("child A\n", encoding="utf-8")
    _git(source, "add", ".")
    _git(source, "commit", "-qm", "submodule A")
    _git(
        repository,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        "-q",
        str(source),
        "child",
    )
    _git(repository, "commit", "-qm", "add submodule")
    return repository, repository / "child"


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


def test_dulwich_backend_contract_returns_typed_discovery_status_and_diff(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    backend = DulwichGitBackend()
    nested = repository / "nested"
    nested.mkdir()

    discovery = backend.discover(nested)
    head = backend.head(repository)

    assert discovery.repository_root == str(repository.resolve())
    assert discovery.git_common_dir == str((repository / ".git").resolve())
    assert backend.resolve_revision(repository, "HEAD") == head
    assert backend.current_branch(repository) == "main"

    (repository / "tracked.txt").write_text("unstaged\n", encoding="utf-8")
    (repository / "staged.txt").write_text("staged\n", encoding="utf-8")
    _git(repository, "add", "staged.txt")
    (repository / "delete.txt").unlink()
    unicode_path = repository / "nested" / "文件.txt"
    unicode_path.write_text("untracked\n", encoding="utf-8")

    observation = backend.status(repository)
    diff = backend.diff(repository, base_commit=head)

    assert isinstance(observation, GitStatusObservation)
    assert observation.staged_paths == ("staged.txt",)
    assert observation.unstaged_paths == ("delete.txt", "tracked.txt")
    assert observation.untracked_paths == ("nested/文件.txt",)
    assert observation.conflict_paths == ()
    assert observation.dirty
    assert isinstance(diff, GitDiffObservation)
    assert set(diff.changed_paths) == {
        "delete.txt",
        "nested/文件.txt",
        "staged.txt",
        "tracked.txt",
    }
    assert "untracked" in diff.patch


def test_status_exposes_staged_unstaged_untracked_deleted_and_conflict_paths(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    backend = DulwichGitBackend()
    (repository / "tracked.txt").write_text("staged\n", encoding="utf-8")
    _git(repository, "add", "tracked.txt")
    (repository / "tracked.txt").write_text("unstaged too\n", encoding="utf-8")
    (repository / "delete.txt").unlink()
    (repository / "untracked.txt").write_text("new\n", encoding="utf-8")

    status = backend.status(repository)

    assert status.staged_paths == ("tracked.txt",)
    assert status.unstaged_paths == ("delete.txt", "tracked.txt")
    assert status.untracked_paths == ("untracked.txt",)

    _git(repository, "restore", "--staged", "tracked.txt")
    _git(repository, "restore", "tracked.txt", "delete.txt")
    _git(repository, "checkout", "-qb", "conflict")
    (repository / "tracked.txt").write_text("conflict branch\n", encoding="utf-8")
    _git(repository, "commit", "-qam", "conflict branch")
    _git(repository, "checkout", "-q", "main")
    (repository / "tracked.txt").write_text("main branch\n", encoding="utf-8")
    _git(repository, "commit", "-qam", "main branch")
    merge = subprocess.run(
        ["git", "merge", "conflict"],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
    )
    assert merge.returncode != 0
    assert backend.status(repository).conflict_paths == ("tracked.txt",)


def test_diff_can_be_scoped_to_one_tracked_or_untracked_path(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    backend = DulwichGitBackend()
    head = backend.head(repository)
    (repository / "tracked.txt").write_text("tracked change\n", encoding="utf-8")
    (repository / "delete.txt").write_text("other change\n", encoding="utf-8")
    (repository / "new file.txt").write_text("untracked change\n", encoding="utf-8")

    tracked = backend.diff(repository, base_commit=head, path="tracked.txt")
    untracked = backend.diff(repository, base_commit=head, path="new file.txt")

    assert tracked.changed_paths == ("tracked.txt",)
    assert "tracked change" in tracked.patch
    assert "other change" not in tracked.patch
    assert untracked.changed_paths == ("new file.txt",)
    assert "untracked change" in untracked.patch
    assert "a/tracked.txt" not in untracked.patch


def test_diff_stats_remain_complete_when_patch_is_truncated(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    backend = DulwichGitBackend(diff_output_limit_bytes=64)
    head = backend.head(repository)
    (repository / "tracked.txt").write_text(
        "".join(f"tracked {index}\n" for index in range(200)),
        encoding="utf-8",
    )
    (repository / "untracked.txt").write_text("first\nsecond\n", encoding="utf-8")

    diff = backend.diff(repository, base_commit=head)

    assert diff.truncated is True
    assert diff.additions == 202
    assert diff.deletions == 1
    assert diff.stats_incomplete is False
    assert {stat.path for stat in diff.file_stats} == {"tracked.txt", "untracked.txt"}
    assert next(stat for stat in diff.file_stats if stat.path == "tracked.txt").additions == 200


def test_diff_marks_binary_line_stats_incomplete(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    backend = DulwichGitBackend()
    head = backend.head(repository)
    (repository / "binary.dat").write_bytes(b"\x00\x01\x02")

    diff = backend.diff(repository, base_commit=head)

    assert diff.additions == 0
    assert diff.deletions == 0
    assert diff.stats_incomplete is True
    assert len(diff.file_stats) == 1
    assert diff.file_stats[0].path == "binary.dat"
    assert diff.file_stats[0].stats_incomplete is True


def test_stage_and_unstage_use_path_scoped_native_git_semantics(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    backend = DulwichGitBackend()
    (repository / "tracked.txt").write_text("modified\n", encoding="utf-8")
    (repository / "new file.txt").write_text("new\n", encoding="utf-8")
    (repository / "delete.txt").unlink()

    backend.stage(repository, ("tracked.txt", "new file.txt", "delete.txt"))
    staged = backend.status(repository)
    assert staged.staged_paths == ("delete.txt", "new file.txt", "tracked.txt")
    assert staged.unstaged_paths == ()
    assert staged.untracked_paths == ()

    backend.unstage(repository, ("tracked.txt", "new file.txt"))
    unstaged = backend.status(repository)
    assert unstaged.staged_paths == ("delete.txt",)
    assert unstaged.unstaged_paths == ("tracked.txt",)
    assert unstaged.untracked_paths == ("new file.txt",)


def test_stage_and_unstage_support_multiple_paths_and_spaces(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    backend = DulwichGitBackend()
    for relative in ("one.txt", "path with spaces.txt"):
        (repository / relative).write_text(relative, encoding="utf-8")

    backend.stage(repository, ("one.txt", "path with spaces.txt"))
    assert backend.status(repository).staged_paths == (
        "one.txt",
        "path with spaces.txt",
    )

    backend.unstage(repository, ("one.txt", "path with spaces.txt"))
    assert backend.status(repository).untracked_paths == (
        "one.txt",
        "path with spaces.txt",
    )


def test_stage_unstage_and_diff_treat_api_paths_as_literal_filenames(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    backend = DulwichGitBackend()
    paths = (":x", "*.txt", "[a].txt", ":(glob)*")
    for relative in paths:
        (repository / relative).write_text("base\n", encoding="utf-8")
    _git(repository, "add", "--all")
    _git(repository, "commit", "-qm", "add literal path fixtures")
    head = backend.head(repository)
    for relative in paths:
        (repository / relative).write_text(f"changed {relative}\n", encoding="utf-8")

    for relative in paths:
        backend.stage(repository, (relative,))
        assert backend.status(repository).staged_paths == (relative,)
        _git(repository, "restore", "--staged", "--", ".")

        _git(repository, "add", "--all")
        backend.unstage(repository, (relative,))
        status = backend.status(repository)
        assert relative in status.unstaged_paths
        assert relative not in status.staged_paths
        assert set(status.staged_paths) == set(paths) - {relative}
        _git(repository, "restore", "--staged", "--", ".")

        diff = backend.diff(repository, base_commit=head, path=relative)
        assert diff.changed_paths == (relative,)
        assert f"changed {relative}" in diff.patch


def test_stage_preserves_native_repository_clean_filter_semantics(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    (repository / ".gitattributes").write_text(
        "native-filtered.txt filter=demo\n"
        "eidos-filtered.txt filter=demo\n",
        encoding="utf-8",
    )
    _git(repository, "config", "filter.demo.clean", "sed s/foo/bar/g")
    _git(repository, "add", ".gitattributes")
    _git(repository, "commit", "-qm", "configure clean filter")
    (repository / "native-filtered.txt").write_text("foo\n", encoding="utf-8")
    (repository / "eidos-filtered.txt").write_text("foo\n", encoding="utf-8")

    _git(repository, "add", "--", "native-filtered.txt")
    DulwichGitBackend().stage(repository, ("eidos-filtered.txt",))

    assert _git(repository, "show", ":native-filtered.txt") == "bar"
    assert _git(repository, "show", ":eidos-filtered.txt") == "bar"


def test_stage_reads_controlled_global_clean_filter_configuration(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    user_home = tmp_path / "filter-home"
    user_home.mkdir()
    (user_home / ".gitconfig").write_text(
        "[filter \"demo\"]\n\tclean = sed s/foo/bar/g\n",
        encoding="utf-8",
    )
    (repository / ".gitattributes").write_text(
        "global-filtered.txt filter=demo\n", encoding="utf-8"
    )
    _git(repository, "add", ".gitattributes")
    _git(repository, "commit", "-qm", "configure global clean filter")
    (repository / "global-filtered.txt").write_text("foo\n", encoding="utf-8")
    backend = DulwichGitBackend(
        git_cli=GitCli(runner=HardenedGitRunner(user_home=user_home))
    )

    backend.stage(repository, ("global-filtered.txt",))

    assert _git(repository, "show", ":global-filtered.txt") == "bar"


def test_native_unstage_supports_an_unborn_head(tmp_path: Path) -> None:
    repository = tmp_path / "unborn"
    repository.mkdir()
    _git(repository, "init", "-q", "-b", "main")
    (repository / "new.txt").write_text("new\n", encoding="utf-8")
    cli = GitCli()
    cli.stage(repository, ("new.txt",))

    cli.unstage(repository, ("new.txt",))

    assert _git(repository, "ls-files", "--", "new.txt") == ""


def test_commit_only_commits_staged_changes_and_reobserves_head(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    backend = DulwichGitBackend()
    original_head = backend.head(repository)
    (repository / "tracked.txt").write_text("staged\n", encoding="utf-8")
    backend.stage(repository, ("tracked.txt",))
    (repository / "delete.txt").write_text("unstaged\n", encoding="utf-8")

    commit = backend.commit(repository, "commit staged file")

    status = backend.status(repository)
    assert commit != original_head
    assert commit == backend.head(repository)
    assert status.staged_paths == ()
    assert status.unstaged_paths == ("delete.txt",)
    assert _git(repository, "show", "HEAD:tracked.txt") == "staged"
    assert _git(repository, "show", "HEAD:delete.txt") == "delete"


def test_commit_reports_nothing_staged_and_unresolved_conflict(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    backend = DulwichGitBackend()
    with pytest.raises(GitNothingStagedError):
        backend.commit(repository, "empty")

    _git(repository, "checkout", "-qb", "conflict")
    (repository / "tracked.txt").write_text("conflict branch\n", encoding="utf-8")
    _git(repository, "commit", "-qam", "conflict branch")
    _git(repository, "checkout", "-q", "main")
    (repository / "tracked.txt").write_text("main branch\n", encoding="utf-8")
    _git(repository, "commit", "-qam", "main branch")
    subprocess.run(
        ["git", "merge", "conflict"],
        cwd=repository,
        check=False,
        capture_output=True,
    )

    with pytest.raises(GitConflictError):
        backend.commit(repository, "conflicted")


def test_commit_uses_controlled_global_identity_without_global_hooks(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    _git(repository, "config", "--unset", "user.name")
    _git(repository, "config", "--unset", "user.email")
    user_home = tmp_path / "home"
    user_home.mkdir()
    marker = tmp_path / "hook-ran"
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    hook = hooks / "pre-commit"
    hook.write_text(f"#!/bin/sh\ntouch '{marker}'\n", encoding="utf-8")
    hook.chmod(0o755)
    (user_home / ".gitconfig").write_text(
        "[user]\n\tname = Global Eidos User\n\temail = global@example.com\n"
        f"[core]\n\thooksPath = {hooks}\n",
        encoding="utf-8",
    )
    backend = DulwichGitBackend(
        git_cli=GitCli(runner=HardenedGitRunner(user_home=user_home))
    )
    (repository / "tracked.txt").write_text("global identity\n", encoding="utf-8")
    backend.stage(repository, ("tracked.txt",))

    backend.commit(repository, "controlled identity")

    assert _git(repository, "show", "-s", "--format=%an <%ae>") == (
        "Global Eidos User <global@example.com>"
    )
    assert not marker.exists()


def test_commit_rejects_when_repository_and_global_identity_are_unavailable(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    _git(repository, "config", "--unset", "user.name")
    _git(repository, "config", "--unset", "user.email")
    empty_home = tmp_path / "empty-home"
    empty_home.mkdir()
    backend = DulwichGitBackend(
        git_cli=GitCli(runner=HardenedGitRunner(user_home=empty_home))
    )
    (repository / "tracked.txt").write_text("identity missing\n", encoding="utf-8")
    backend.stage(repository, ("tracked.txt",))

    with pytest.raises(GitIdentityUnavailableError):
        backend.commit(repository, "missing identity")


def test_dulwich_backend_linked_worktree_lifecycle_is_typed(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    backend = DulwichGitBackend()
    head = backend.head(repository)
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


def test_dulwich_backend_reports_conflicts_and_detached_worktrees(
    tmp_path: Path,
) -> None:
    repository = _repository(tmp_path)
    backend = DulwichGitBackend()
    linked = tmp_path / "linked"
    _git(repository, "worktree", "add", "-q", "-b", "feature", str(linked), "HEAD")

    (repository / "tracked.txt").write_text("main\n", encoding="utf-8")
    _git(repository, "commit", "-qam", "main change")
    (linked / "tracked.txt").write_text("feature\n", encoding="utf-8")
    _git(linked, "commit", "-qam", "feature change")
    result = subprocess.run(
        ["git", "merge", "feature"],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert backend.status(repository).conflict_paths == ("tracked.txt",)
    _git(repository, "merge", "--abort")

    detached = tmp_path / "detached"
    _git(repository, "worktree", "add", "-q", "--detach", str(detached), "HEAD")
    assert any(
        entry.worktree_root == str(detached.resolve()) and entry.branch is None
        for entry in backend.worktree_list(repository)
    )


def test_diff_treats_clean_symlink_as_unchanged(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    (repository / "target.txt").write_text("target\n", encoding="utf-8")
    (repository / "link.txt").symlink_to("target.txt")
    _git(repository, "add", "target.txt", "link.txt")
    _git(repository, "commit", "-qm", "add symlink")

    observation = DulwichGitBackend().diff(
        repository,
        base_commit=DulwichGitBackend().head(repository),
    )

    assert observation.changed_paths == ()
    assert observation.patch == ""


def test_diff_reports_modified_symlink_target_string(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    (repository / "a.txt").write_text("a\n", encoding="utf-8")
    (repository / "b.txt").write_text("b\n", encoding="utf-8")
    (repository / "link.txt").symlink_to("a.txt")
    _git(repository, "add", "a.txt", "b.txt", "link.txt")
    _git(repository, "commit", "-qm", "add symlink")
    (repository / "link.txt").unlink()
    (repository / "link.txt").symlink_to("b.txt")

    observation = DulwichGitBackend().diff(
        repository,
        base_commit=DulwichGitBackend().head(repository),
    )

    assert observation.changed_paths == ("link.txt",)
    assert "-a.txt" in observation.patch
    assert "+b.txt" in observation.patch


def test_diff_reports_deleted_symlink(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    (repository / "target.txt").write_text("target\n", encoding="utf-8")
    (repository / "link.txt").symlink_to("target.txt")
    _git(repository, "add", "target.txt", "link.txt")
    _git(repository, "commit", "-qm", "add symlink")
    (repository / "link.txt").unlink()

    observation = DulwichGitBackend().diff(
        repository,
        base_commit=DulwichGitBackend().head(repository),
    )

    assert observation.changed_paths == ("link.txt",)
    assert "-target.txt" in observation.patch


def test_diff_treats_clean_submodule_gitlink_as_unchanged(tmp_path: Path) -> None:
    repository, _submodule = _repository_with_submodule(tmp_path)
    backend = DulwichGitBackend()

    observation = backend.diff(repository, base_commit=backend.head(repository))

    assert observation.changed_paths == ()
    assert observation.patch == ""


def test_diff_reports_advanced_submodule_head(tmp_path: Path) -> None:
    repository, submodule = _repository_with_submodule(tmp_path)
    backend = DulwichGitBackend()
    (submodule / "child.txt").write_text("child B\n", encoding="utf-8")
    _git(submodule, "commit", "-qam", "submodule B")

    observation = backend.diff(repository, base_commit=backend.head(repository))

    assert observation.changed_paths == ("child",)
    assert "Subproject commit" in observation.patch


def test_diff_reports_missing_submodule_worktree(tmp_path: Path) -> None:
    repository, submodule = _repository_with_submodule(tmp_path)
    backend = DulwichGitBackend()
    shutil.rmtree(submodule)

    observation = backend.diff(repository, base_commit=backend.head(repository))

    assert observation.changed_paths == ("child",)
    assert "Subproject commit" in observation.patch


def test_dulwich_read_and_native_create_paths_do_not_execute_git_helpers(
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

    backend.status(repository)
    backend.diff(repository, base_commit=backend.head(repository))
    linked = tmp_path / "linked"
    backend.worktree_add(repository, linked, "eidos/helper-safety", backend.head(repository))
    changes = backend.capture_worktree_changes(repository)
    backend.apply_worktree_changes(linked, changes)

    assert not marker.exists()


def test_branch_delete_is_compare_and_delete(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    backend = DulwichGitBackend()
    head = backend.head(repository)
    _git(repository, "branch", "eidos/to-delete", head)

    assert not backend.delete_branch_if_equals(repository, "eidos/to-delete", "0" * 40)
    assert backend.branch_commit(repository, "eidos/to-delete") == head
    assert backend.delete_branch_if_equals(repository, "eidos/to-delete", head)
    assert backend.branch_commit(repository, "eidos/to-delete") is None

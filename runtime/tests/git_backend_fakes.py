from __future__ import annotations

from pathlib import Path

from eidos_runtime.git.backend import DulwichGitBackend, GitBackend
from eidos_runtime.git.models import (
    GitDiffObservation,
    GitRepositoryDiscovery,
    GitStatusObservation,
    GitWorktreeEntry,
    GitWorkingTreePatch,
)


class FakeGitBackend:
    """Typed failure-injecting adapter for WorktreeManager tests."""

    def __init__(
        self,
        delegate: GitBackend | None = None,
        *,
        failures: dict[str, BaseException] | None = None,
    ) -> None:
        self.delegate = delegate or DulwichGitBackend()
        self.failures = failures or {}

    def _fail(self, operation: str) -> None:
        failure = self.failures.get(operation)
        if failure is not None:
            raise failure

    def discover(self, cwd: Path) -> GitRepositoryDiscovery:
        self._fail("discover")
        return self.delegate.discover(cwd)

    def try_discover(self, cwd: Path) -> GitRepositoryDiscovery | None:
        self._fail("try_discover")
        return self.delegate.try_discover(cwd)

    def head(self, cwd: Path) -> str:
        self._fail("head")
        return self.delegate.head(cwd)

    def current_branch(self, cwd: Path) -> str | None:
        self._fail("current_branch")
        return self.delegate.current_branch(cwd)

    def resolve_revision(self, cwd: Path, revision: str) -> str:
        self._fail("resolve_revision")
        return self.delegate.resolve_revision(cwd, revision)

    def branch_commit(self, cwd: Path, branch: str) -> str | None:
        self._fail("branch_commit")
        return self.delegate.branch_commit(cwd, branch)

    def local_branches(self, cwd: Path) -> tuple[str, ...]:
        self._fail("local_branches")
        return self.delegate.local_branches(cwd)

    def status(self, cwd: Path) -> GitStatusObservation:
        self._fail("status")
        return self.delegate.status(cwd)

    def diff(
        self,
        cwd: Path,
        *,
        base_commit: str,
        include_untracked: bool = True,
    ) -> GitDiffObservation:
        self._fail("diff")
        return self.delegate.diff(
            cwd,
            base_commit=base_commit,
            include_untracked=include_untracked,
        )

    def worktree_list(self, cwd: Path) -> tuple[GitWorktreeEntry, ...]:
        self._fail("worktree_list")
        return self.delegate.worktree_list(cwd)

    def worktree_add(
        self, cwd: Path, worktree_root: Path, branch: str | None, base_commit: str
    ) -> None:
        self._fail("worktree_add")
        self.delegate.worktree_add(cwd, worktree_root, branch, base_commit)

    def worktree_remove(self, cwd: Path, worktree_root: Path) -> None:
        self._fail("worktree_remove")
        self.delegate.worktree_remove(cwd, worktree_root)

    def clean_worktree_for_compensation(self, cwd: Path) -> None:
        self._fail("clean_worktree_for_compensation")
        self.delegate.clean_worktree_for_compensation(cwd)

    def worktree_prune(self, cwd: Path) -> None:
        self._fail("worktree_prune")
        self.delegate.worktree_prune(cwd)

    def capture_worktree_changes(self, cwd: Path) -> GitWorkingTreePatch:
        self._fail("capture_worktree_changes")
        return self.delegate.capture_worktree_changes(cwd)

    def apply_worktree_changes(
        self, cwd: Path, changes: GitWorkingTreePatch
    ) -> None:
        self._fail("apply_worktree_changes")
        self.delegate.apply_worktree_changes(cwd, changes)

    def create_branch(self, cwd: Path, branch: str) -> None:
        self._fail("create_branch")
        self.delegate.create_branch(cwd, branch)

    def delete_branch_if_equals(
        self, cwd: Path, branch: str, expected_commit: str
    ) -> bool:
        self._fail("delete_branch_if_equals")
        return self.delegate.delete_branch_if_equals(cwd, branch, expected_commit)

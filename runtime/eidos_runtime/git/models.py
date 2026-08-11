from __future__ import annotations

from eidos_runtime.domain.project import Project
from eidos_runtime.models import EidosFrozenStrictModel


class GitRepositoryDiscovery(EidosFrozenStrictModel):
    repository_root: str
    git_dir: str
    git_common_dir: str


class GitStatusObservation(EidosFrozenStrictModel):
    """Read-only Git status facts for one concrete worktree."""

    head: str
    branch: str | None
    staged_paths: tuple[str, ...]
    unstaged_paths: tuple[str, ...]
    untracked_paths: tuple[str, ...]
    conflict_paths: tuple[str, ...]

    @property
    def dirty(self) -> bool:
        return bool(
            self.staged_paths
            or self.unstaged_paths
            or self.untracked_paths
            or self.conflict_paths
        )


class GitDiffObservation(EidosFrozenStrictModel):
    """Bounded read-only diff facts for one worktree against one commit."""

    patch: str
    changed_paths: tuple[str, ...]
    truncated: bool


class GitWorktreeEntry(EidosFrozenStrictModel):
    worktree_root: str
    head: str | None = None
    branch: str | None = None
    prunable: bool = False


class ProjectResolution(EidosFrozenStrictModel):
    """Resolved filesystem Project plus its optional Git capability."""

    project: Project
    git: GitRepositoryDiscovery | None = None


__all__ = [
    "GitDiffObservation",
    "GitRepositoryDiscovery",
    "GitStatusObservation",
    "GitWorktreeEntry",
    "ProjectResolution",
]

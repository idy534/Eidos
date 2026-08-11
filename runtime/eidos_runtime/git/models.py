from __future__ import annotations

from eidos_runtime.domain.project import Project
from eidos_runtime.models import EidosFrozenStrictModel


class GitRepositoryDiscovery(EidosFrozenStrictModel):
    repository_root: str
    git_dir: str
    git_common_dir: str


class GitWorktreeEntry(EidosFrozenStrictModel):
    worktree_root: str
    head: str | None = None
    branch: str | None = None
    prunable: bool = False


class ProjectResolution(EidosFrozenStrictModel):
    """Resolved filesystem Project plus its optional Git capability."""

    project: Project
    git: GitRepositoryDiscovery | None = None


__all__ = ["GitRepositoryDiscovery", "GitWorktreeEntry", "ProjectResolution"]

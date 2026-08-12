from __future__ import annotations

import hashlib

from pydantic import Field

from eidos_runtime.domain.project import Project
from eidos_runtime.models import EidosFrozenStrictModel


class GitRepositoryDiscovery(EidosFrozenStrictModel):
    repository_root: str
    git_dir: str
    git_common_dir: str


class GitRepositoryContext(EidosFrozenStrictModel):
    git_available: bool
    current_branch: str | None
    head: str | None
    branches: tuple[str, ...]
    dirty: bool = False
    changed_file_count: int = Field(default=0, ge=0)


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


class GitRemote(EidosFrozenStrictModel):
    name: str


class GitUpstream(EidosFrozenStrictModel):
    remote: str
    branch: str


class GitRemoteObservation(EidosFrozenStrictModel):
    branch: str | None
    remotes: tuple[GitRemote, ...]
    upstream: GitUpstream | None = None
    ahead: int | None = Field(default=None, ge=0)
    behind: int | None = Field(default=None, ge=0)


class GitWorkingTreePatch(EidosFrozenStrictModel):
    """Lossless Git patch bytes produced and consumed by the Git CLI."""

    full_patch: bytes
    staged_patch: bytes


class GitSourceSnapshot(EidosFrozenStrictModel):
    discovery: GitRepositoryDiscovery
    head: str
    branch: str | None
    status: GitStatusObservation
    changes: GitWorkingTreePatch | None = None

    @property
    def fingerprint(self) -> str:
        encoded = self.model_dump_json(
            by_alias=False,
            exclude_none=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


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
    "GitRepositoryContext",
    "GitRepositoryDiscovery",
    "GitStatusObservation",
    "GitRemote",
    "GitRemoteObservation",
    "GitUpstream",
    "GitWorktreeEntry",
    "GitWorkingTreePatch",
    "GitSourceSnapshot",
    "ProjectResolution",
]

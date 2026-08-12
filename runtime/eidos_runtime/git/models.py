from __future__ import annotations

from typing import Literal
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


class GitWorktreeStateEntry(EidosFrozenStrictModel):
    """One exact filesystem/index entry captured without a Git CLI patch."""

    path: str
    kind: Literal["file", "symlink", "gitlink"]
    mode: int = Field(ge=0, le=0o177777)
    content_base64: str | None = None
    object_id: str | None = None


class GitWorkingTreeState(EidosFrozenStrictModel):
    """Immutable working-tree or index projection used for exact transfer."""

    base_head: str
    base_paths: tuple[str, ...]
    entries: tuple[GitWorktreeStateEntry, ...]


class GitWorkingTreePatch(EidosFrozenStrictModel):
    """Durable source state plus optional Dulwich-rendered patch text."""

    full_patch: str
    staged_patch: str
    full_state: GitWorkingTreeState | None = None
    staged_state: GitWorkingTreeState | None = None


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
    "GitWorktreeEntry",
    "GitWorktreeStateEntry",
    "GitWorkingTreeState",
    "GitWorkingTreePatch",
    "GitSourceSnapshot",
    "ProjectResolution",
]

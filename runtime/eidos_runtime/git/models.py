from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

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


@dataclass(frozen=True)
class GitWorkingTreePatch:
    """A source working-tree snapshot represented by Git patch semantics."""

    full_patch: str
    staged_patch: str


@dataclass(frozen=True)
class GitSourceSnapshot:
    discovery: GitRepositoryDiscovery
    head: str
    branch: str | None
    status: GitStatusObservation
    changes: GitWorkingTreePatch | None = None

    @property
    def fingerprint(self) -> str:
        value = {
            "repository_root": self.discovery.repository_root,
            "git_dir": self.discovery.git_dir,
            "git_common_dir": self.discovery.git_common_dir,
            "head": self.head,
            "branch": self.branch,
            "staged_paths": self.status.staged_paths,
            "unstaged_paths": self.status.unstaged_paths,
            "untracked_paths": self.status.untracked_paths,
            "conflict_paths": self.status.conflict_paths,
            "full_patch": (
                self.changes.full_patch if self.changes is not None else None
            ),
            "staged_patch": (
                self.changes.staged_patch if self.changes is not None else None
            ),
        }
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
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
    "GitWorkingTreePatch",
    "GitSourceSnapshot",
    "ProjectResolution",
]

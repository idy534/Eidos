from __future__ import annotations

from pathlib import Path

from eidos_runtime.git.backend import GitBackend
from eidos_runtime.git.errors import (
    GitCommandFailedError,
    GitCommandTimeoutError,
    WorktreeError,
)
from eidos_runtime.git.models import GitRepositoryDiscovery


class GitRepositoryDiscoveryService:
    """Resolves a user-selected path into verified Git repository facts."""

    def __init__(self, backend: GitBackend) -> None:
        self.backend = backend

    def discover(self, repository_root: Path | str) -> GitRepositoryDiscovery:
        path = Path(repository_root)
        try:
            canonical_input = path.resolve(strict=True)
        except OSError as error:
            raise WorktreeError("repository_not_found") from error
        if not canonical_input.is_dir():
            raise WorktreeError("repository_not_found")
        try:
            return self.backend.discover(canonical_input)
        except GitCommandTimeoutError:
            raise WorktreeError("git_command_timeout") from None
        except GitCommandFailedError as error:
            raise WorktreeError("not_a_git_repository") from error

    def resolve(self, repository_root: Path | str) -> GitRepositoryDiscovery | None:
        """Resolve optional Git capability without treating it as a failure."""

        path = Path(repository_root)
        try:
            canonical_input = path.resolve(strict=True)
        except OSError as error:
            raise WorktreeError("repository_not_found") from error
        if not canonical_input.is_dir():
            raise WorktreeError("repository_not_found")
        try:
            return self.backend.try_discover(canonical_input)
        except GitCommandTimeoutError:
            raise WorktreeError("git_command_timeout") from None
        except GitCommandFailedError as error:
            raise WorktreeError("not_a_git_repository") from error


__all__ = ["GitRepositoryDiscoveryService"]

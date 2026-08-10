from __future__ import annotations

from pathlib import Path

from eidos_runtime.git.errors import GitCommandFailedError, GitCommandTimeoutError, WorktreeError
from eidos_runtime.git.models import GitRepositoryDiscovery
from eidos_runtime.git.process import GitProcess


class GitRepositoryDiscoveryService:
    """Resolves a user-selected path into verified Git repository facts."""

    def __init__(self, process: GitProcess) -> None:
        self.process = process

    def discover(self, repository_root: Path | str) -> GitRepositoryDiscovery:
        path = Path(repository_root)
        try:
            canonical_input = path.resolve(strict=True)
        except OSError as error:
            raise WorktreeError("repository_not_found") from error
        if not canonical_input.is_dir():
            raise WorktreeError("repository_not_found")
        try:
            root_text = self.process.rev_parse_show_toplevel(canonical_input)
            git_dir_text = self.process.rev_parse_git_dir(canonical_input)
            common_dir_text = self.process.rev_parse_git_common_dir(canonical_input)
        except GitCommandTimeoutError:
            raise WorktreeError("git_command_timeout") from None
        except GitCommandFailedError as error:
            raise WorktreeError("not_a_git_repository") from error

        try:
            resolved_root = Path(root_text).resolve(strict=True)
            resolved_git_dir = _resolve_git_path(git_dir_text, canonical_input)
            resolved_common_dir = _resolve_git_path(common_dir_text, canonical_input)
        except OSError as error:
            raise WorktreeError("not_a_git_repository") from error
        if not resolved_root.is_dir() or not resolved_git_dir.exists() or not resolved_common_dir.exists():
            raise WorktreeError("not_a_git_repository")
        return GitRepositoryDiscovery(
            repository_root=str(resolved_root),
            git_dir=str(resolved_git_dir),
            git_common_dir=str(resolved_common_dir),
        )


def _resolve_git_path(value: str, cwd: Path) -> Path:
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = cwd / candidate
    return candidate.resolve(strict=True)


__all__ = ["GitRepositoryDiscoveryService"]

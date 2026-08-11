from __future__ import annotations

from dataclasses import dataclass
import io
import os
from pathlib import Path
import re
import stat
from collections.abc import Iterable
from typing import Protocol

from dulwich.diff import iter_tree_contents, write_blob_diff
from dulwich.errors import NotGitRepository
from dulwich.ignore import IgnoreFilterManager
from dulwich.index import ConflictedIndexEntry, Index, get_unstaged_changes
from dulwich.objects import Blob
from dulwich.objectspec import parse_commit
from dulwich.porcelain import (
    get_untracked_paths,
    worktree_list,
    worktree_prune,
    worktree_remove,
)
from dulwich.refs import HEADREF, Ref
from dulwich.repo import Repo

from eidos_runtime.git.errors import GitCommandFailedError, GitCommandTimeoutError
from eidos_runtime.git.models import (
    GitDiffObservation,
    GitRepositoryDiscovery,
    GitStatusObservation,
    GitWorktreeEntry,
    GitWorkingTreePatch,
)
from eidos_runtime.git.native import (
    NativeBranchAttacher,
    NativeWorktreeChangeTransfer,
    NativeWorktreeCleaner,
    NativeWorktreeCreator,
)


DEFAULT_GIT_DIFF_BYTES = 512 * 1024
_GITLINK_MODE = 0o160000


class GitBackend(Protocol):
    """Typed Git mechanics used by the Eidos Worktree lifecycle."""

    def discover(self, cwd: Path) -> GitRepositoryDiscovery: ...

    def try_discover(self, cwd: Path) -> GitRepositoryDiscovery | None: ...

    def head(self, cwd: Path) -> str: ...

    def current_branch(self, cwd: Path) -> str | None: ...

    def is_ignored(self, cwd: Path, relative_path: str) -> bool: ...

    def resolve_revision(self, cwd: Path, revision: str) -> str: ...

    def branch_commit(self, cwd: Path, branch: str) -> str | None: ...

    def local_branches(self, cwd: Path) -> tuple[str, ...]: ...

    def status(self, cwd: Path) -> GitStatusObservation: ...

    def diff(
        self,
        cwd: Path,
        *,
        base_commit: str,
        include_untracked: bool = True,
    ) -> GitDiffObservation: ...

    def worktree_list(self, cwd: Path) -> tuple[GitWorktreeEntry, ...]: ...

    def worktree_add(
        self, cwd: Path, worktree_root: Path, branch: str | None, base_commit: str
    ) -> None: ...

    def worktree_remove(self, cwd: Path, worktree_root: Path) -> None: ...

    def clean_worktree_for_compensation(self, cwd: Path) -> None: ...

    def worktree_prune(self, cwd: Path) -> None: ...

    def capture_worktree_changes(self, cwd: Path) -> GitWorkingTreePatch: ...

    def apply_worktree_changes(
        self, cwd: Path, changes: GitWorkingTreePatch
    ) -> None: ...

    def create_branch(self, cwd: Path, branch: str) -> None: ...

    def delete_branch_if_equals(
        self, cwd: Path, branch: str, expected_commit: str
    ) -> bool: ...


class DulwichGitBackend:
    """Deep typed Git implementation backed by Dulwich read APIs."""

    def __init__(
        self,
        *,
        native_worktree_creator: NativeWorktreeCreator | None = None,
        native_change_transfer: NativeWorktreeChangeTransfer | None = None,
        native_branch_attacher: NativeBranchAttacher | None = None,
        native_worktree_cleaner: NativeWorktreeCleaner | None = None,
        diff_output_limit_bytes: int = DEFAULT_GIT_DIFF_BYTES,
    ) -> None:
        if diff_output_limit_bytes < 1:
            raise ValueError("Git diff output limit must be positive")
        self._native_worktree_creator = native_worktree_creator
        self._native_change_transfer = native_change_transfer
        self._native_branch_attacher = native_branch_attacher
        self._native_worktree_cleaner = native_worktree_cleaner
        self._diff_output_limit_bytes = diff_output_limit_bytes

    def discover(self, cwd: Path) -> GitRepositoryDiscovery:
        result = self.try_discover(cwd)
        if result is None:
            raise GitCommandFailedError(
                "discover",
                returncode=128,
                stderr="not a git repository",
            )
        return result

    def try_discover(self, cwd: Path) -> GitRepositoryDiscovery | None:
        if not cwd.is_absolute() or not cwd.is_dir():
            raise GitCommandFailedError("discover", returncode=None)
        try:
            repo = Repo.discover(cwd)
        except NotGitRepository:
            return None
        except OSError as error:
            raise _git_failure("discover", error) from error
        except ValueError as error:
            raise _git_failure("discover", error) from error
        return _discovery_from_repo(repo)

    def head(self, cwd: Path) -> str:
        repo = self._open_repository(cwd, "head")
        try:
            return _object_id(repo.head())
        except (KeyError, OSError, TypeError, ValueError) as error:
            raise _git_failure("head", error) from error

    def current_branch(self, cwd: Path) -> str | None:
        repo = self._open_repository(cwd, "current-branch")
        try:
            value = repo.refs.read_ref(HEADREF)
        except (KeyError, OSError, TypeError, ValueError) as error:
            raise _git_failure("current-branch", error) from error
        if value is None or not value.startswith(b"ref: refs/heads/"):
            return None
        branch = os.fsdecode(value.removeprefix(b"ref: refs/heads/").strip())
        return branch or None

    def is_ignored(self, cwd: Path, relative_path: str) -> bool:
        _validate_relative_path(relative_path)
        repo = self._open_repository(cwd, "is-ignored")
        try:
            index = repo.open_index(config=repo.get_config_stack())
            if os.fsencode(relative_path) in index:
                return False
            return IgnoreFilterManager.from_repo(repo).is_ignored(relative_path) is True
        except (KeyError, OSError, TypeError, ValueError) as error:
            raise _git_failure("is-ignored", error) from error

    def resolve_revision(self, cwd: Path, revision: str) -> str:
        _validate_ref(revision)
        repo = self._open_repository(cwd, "resolve-revision")
        try:
            return _object_id(parse_commit(repo, revision))
        except (KeyError, TypeError, ValueError) as error:
            raise _git_failure("resolve-revision", error) from error

    def branch_commit(self, cwd: Path, branch: str) -> str | None:
        _validate_branch(branch)
        repo = self._open_repository(cwd, "branch-commit")
        try:
            value = repo.refs[Ref(f"refs/heads/{branch}".encode("utf-8"))]
        except KeyError:
            return None
        except (OSError, TypeError, ValueError) as error:
            raise _git_failure("branch-commit", error) from error
        return _object_id(value)

    def local_branches(self, cwd: Path) -> tuple[str, ...]:
        repo = self._open_repository(cwd, "local-branches")
        try:
            refs = repo.refs.as_dict(b"refs/heads/")
        except (KeyError, OSError, TypeError, ValueError) as error:
            raise _git_failure("local-branches", error) from error
        return tuple(sorted(os.fsdecode(name) for name in refs))

    def status(self, cwd: Path) -> GitStatusObservation:
        repo = self._open_repository(cwd, "status")
        try:
            index = repo.open_index(config=repo.get_config_stack())
            head_tree = parse_commit(repo, "HEAD").tree
            index_entries, conflict_paths = _index_entries(index)
            head_entries = _tree_entries(repo, head_tree)
            changed_at_index = _changed_paths(head_entries, index_entries)
            conflict_set = set(conflict_paths)
            unstaged_paths = tuple(
                sorted(
                    _decode_path(path)
                    for path in get_unstaged_changes(
                        index,
                        repo.path,
                        None,
                        False,
                    )
                    if _decode_path(path) not in conflict_set
                )
            )
            untracked_paths = tuple(
                sorted(
                    os.fsdecode(path)
                    for path in get_untracked_paths(
                        repo.path,
                        repo.path,
                        index,
                        exclude_ignored=True,
                        untracked_files="all",
                        repo=repo,
                    )
                )
            )
            head = _object_id(repo.head())
            branch = _current_branch(repo)
        except (KeyError, OSError, TypeError, ValueError) as error:
            raise _git_failure("status", error) from error
        return GitStatusObservation(
            head=head,
            branch=branch,
            staged_paths=tuple(
                path for path in changed_at_index if path not in conflict_set
            ),
            unstaged_paths=unstaged_paths,
            untracked_paths=untracked_paths,
            conflict_paths=conflict_paths,
        )

    def diff(
        self,
        cwd: Path,
        *,
        base_commit: str,
        include_untracked: bool = True,
    ) -> GitDiffObservation:
        _validate_ref(base_commit)
        repo = self._open_repository(cwd, "diff")
        try:
            base_tree = parse_commit(repo, base_commit).tree
            index = repo.open_index(config=repo.get_config_stack())
            index_entries, _conflict_paths = _index_entries(index)
            base_entries = _tree_entries(repo, base_tree)
            tracked_paths = set(base_entries) | set(index_entries)
            untracked_paths = (
                {
                    os.fsencode(path)
                    for path in get_untracked_paths(
                        repo.path,
                        repo.path,
                        index,
                        exclude_ignored=True,
                        untracked_files="all",
                        repo=repo,
                    )
                }
                if include_untracked
                else set()
            )
            target_paths = tracked_paths | untracked_paths
            target_entries = {
                path: _read_worktree_entry(
                    repo.path,
                    path,
                    base_entries.get(path) or index_entries.get(path),
                )
                for path in target_paths
            }
            changed_paths = tuple(
                sorted(
                    _decode_path(path)
                    for path in target_paths
                    if _entries_differ(
                        base_entries.get(path), target_entries.get(path)
                    )
                )
            )
            output = _LimitedBytesIO(self._diff_output_limit_bytes)
            for path in sorted(target_paths):
                old_entry = base_entries.get(path)
                new_entry = target_entries.get(path)
                if not _entries_differ(old_entry, new_entry):
                    continue
                try:
                    _write_entry_diff(output, repo, path, old_entry, new_entry)
                except _OutputLimitReached:
                    break
        except (KeyError, OSError, TypeError, ValueError) as error:
            raise _git_failure("diff", error) from error
        return GitDiffObservation(
            patch=output.getvalue().decode("utf-8", errors="replace"),
            changed_paths=changed_paths,
            truncated=output.truncated,
        )

    def worktree_list(self, cwd: Path) -> tuple[GitWorktreeEntry, ...]:
        repo = self._open_repository(cwd, "worktree-list")
        try:
            entries = worktree_list(repo)
        except (KeyError, OSError, TypeError, ValueError) as error:
            raise _git_failure("worktree-list", error) from error
        return tuple(
            GitWorktreeEntry(
                worktree_root=str(Path(entry.path).resolve(strict=False)),
                head=_optional_object_id(entry.head),
                branch=(
                    os.fsdecode(entry.branch.removeprefix(b"refs/heads/"))
                    if entry.branch is not None
                    else None
                ),
                prunable=bool(entry.prunable),
            )
            for entry in entries
            if entry.path
        )

    def worktree_add(
        self, cwd: Path, worktree_root: Path, branch: str | None, base_commit: str
    ) -> None:
        if self._native_worktree_creator is None:
            self._native_worktree_creator = NativeWorktreeCreator()
        self._native_worktree_creator.create(
            cwd,
            worktree_root,
            branch,
            base_commit,
        )

    def worktree_remove(self, cwd: Path, worktree_root: Path) -> None:
        repo = self._open_repository(cwd, "worktree-remove")
        try:
            worktree_remove(repo, worktree_root, force=False)
        except (KeyError, OSError, TypeError, ValueError) as error:
            raise _git_failure("worktree-remove", error) from error

    def clean_worktree_for_compensation(self, cwd: Path) -> None:
        if self._native_worktree_cleaner is None:
            self._native_worktree_cleaner = NativeWorktreeCleaner()
        self._native_worktree_cleaner.clean(cwd)

    def capture_worktree_changes(self, cwd: Path) -> GitWorkingTreePatch:
        if self._native_change_transfer is None:
            self._native_change_transfer = NativeWorktreeChangeTransfer()
        try:
            full_patch, staged_patch = self._native_change_transfer.capture(cwd)
        except (GitCommandFailedError, GitCommandTimeoutError):
            raise
        return GitWorkingTreePatch(
            full_patch=full_patch,
            staged_patch=staged_patch,
        )

    def apply_worktree_changes(
        self, cwd: Path, changes: GitWorkingTreePatch
    ) -> None:
        if self._native_change_transfer is None:
            self._native_change_transfer = NativeWorktreeChangeTransfer()
        self._native_change_transfer.apply(
            cwd,
            full_patch=changes.full_patch,
            staged_patch=changes.staged_patch,
        )

    def create_branch(self, cwd: Path, branch: str) -> None:
        if self._native_branch_attacher is None:
            self._native_branch_attacher = NativeBranchAttacher()
        self._native_branch_attacher.attach(cwd, branch)

    def worktree_prune(self, cwd: Path) -> None:
        repo = self._open_repository(cwd, "worktree-prune")
        try:
            worktree_prune(repo)
        except (KeyError, OSError, TypeError, ValueError) as error:
            raise _git_failure("worktree-prune", error) from error

    def delete_branch_if_equals(
        self, cwd: Path, branch: str, expected_commit: str
    ) -> bool:
        _validate_branch(branch)
        _validate_ref(expected_commit)
        repo = self._open_repository(cwd, "delete-branch-if-equals")
        try:
            return bool(
                repo.refs.remove_if_equals(
                    Ref(f"refs/heads/{branch}".encode("utf-8")),
                    expected_commit.encode("ascii"),
                )
            )
        except (OSError, TypeError, ValueError) as error:
            raise _git_failure("delete-branch-if-equals", error) from error

    def _open_repository(self, cwd: Path, operation: str) -> Repo:
        if not cwd.is_absolute() or not cwd.is_dir():
            raise GitCommandFailedError(operation, returncode=None)
        try:
            return Repo.discover(cwd)
        except NotGitRepository as error:
            raise GitCommandFailedError(
                operation,
                returncode=128,
                stderr=str(error),
            ) from error
        except (OSError, ValueError) as error:
            raise _git_failure(operation, error) from error


@dataclass(frozen=True)
class _GitFileEntry:
    mode: int
    object_id: bytes
    blob: Blob | None = None


def _discovery_from_repo(repo: Repo) -> GitRepositoryDiscovery:
    try:
        return GitRepositoryDiscovery(
            repository_root=str(Path(repo.path).resolve(strict=True)),
            git_dir=str(Path(repo.controldir()).resolve(strict=True)),
            git_common_dir=str(Path(repo.commondir()).resolve(strict=True)),
        )
    except (OSError, ValueError) as error:
        raise _git_failure("discover", error) from error


def _tree_entries(repo: Repo, tree_id: bytes) -> dict[bytes, _GitFileEntry]:
    return {
        entry.path: _GitFileEntry(mode=entry.mode, object_id=entry.sha)
        for entry in iter_tree_contents(repo.object_store, tree_id)
        if entry.path is not None and entry.mode is not None and entry.sha is not None
    }


def _index_entries(
    index: Index,
) -> tuple[dict[bytes, _GitFileEntry], tuple[str, ...]]:
    entries: dict[bytes, _GitFileEntry] = {}
    conflicts: list[str] = []
    for path, value in index.iteritems():
        if isinstance(value, ConflictedIndexEntry):
            conflicts.append(_decode_path(path))
            value = value.this or value.other or value.ancestor
        if value is None:
            continue
        entries[path] = _GitFileEntry(mode=value.mode, object_id=value.sha)
    return entries, tuple(sorted(set(conflicts)))


def _changed_paths(
    first: dict[bytes, _GitFileEntry],
    second: dict[bytes, _GitFileEntry],
) -> tuple[str, ...]:
    return tuple(
        sorted(
            _decode_path(path)
            for path in set(first) | set(second)
            if _entries_differ(first.get(path), second.get(path))
        )
    )


def _read_worktree_entry(
    root: str,
    path: bytes,
    expected: _GitFileEntry | None = None,
) -> _GitFileEntry | None:
    filesystem_path = Path(root) / os.fsdecode(path)
    try:
        file_stat = filesystem_path.lstat()
    except FileNotFoundError:
        return None
    if expected is not None and expected.mode == _GITLINK_MODE:
        return _read_gitlink_entry(filesystem_path, file_stat)
    if stat.S_ISLNK(file_stat.st_mode):
        blob = Blob.from_string(os.fsencode(os.readlink(filesystem_path)))
        return _GitFileEntry(mode=0o120000, object_id=blob.id, blob=blob)
    if not stat.S_ISREG(file_stat.st_mode):
        return None
    blob = Blob.from_string(filesystem_path.read_bytes())
    mode = 0o100755 if file_stat.st_mode & stat.S_IXUSR else 0o100644
    return _GitFileEntry(mode=mode, object_id=blob.id, blob=blob)


def _read_gitlink_entry(
    filesystem_path: Path,
    file_stat: os.stat_result,
) -> _GitFileEntry | None:
    if not stat.S_ISDIR(file_stat.st_mode):
        return None
    try:
        submodule_repo = Repo.discover(filesystem_path)
    except NotGitRepository:
        return None
    if Path(submodule_repo.path).resolve(strict=True) != filesystem_path.resolve(
        strict=True
    ):
        return None
    return _GitFileEntry(
        mode=_GITLINK_MODE,
        object_id=bytes(submodule_repo.head()),
    )


def _entries_differ(
    first: _GitFileEntry | None,
    second: _GitFileEntry | None,
) -> bool:
    if first is None or second is None:
        return first is not second
    return first.mode != second.mode or first.object_id != second.object_id


def _write_entry_diff(
    output: _LimitedBytesIO,
    repo: Repo,
    path: bytes,
    old_entry: _GitFileEntry | None,
    new_entry: _GitFileEntry | None,
) -> None:
    old_blob = _entry_blob(repo, old_entry)
    new_blob = _entry_blob(repo, new_entry)
    write_blob_diff(
        output,
        (path if old_entry is not None else None, old_entry.mode if old_entry else None, old_blob),
        (path if new_entry is not None else None, new_entry.mode if new_entry else None, new_blob),
    )


def _entry_blob(repo: Repo, entry: _GitFileEntry | None) -> Blob | None:
    if entry is None:
        return None
    if entry.blob is not None:
        return entry.blob
    if entry.mode == _GITLINK_MODE:
        return Blob.from_string(b"Subproject commit " + entry.object_id + b"\n")
    value = repo.object_store[entry.object_id]
    if isinstance(value, Blob):
        return value
    return Blob.from_string(b"Subproject commit " + entry.object_id + b"\n")


class _OutputLimitReached(RuntimeError):
    pass


class _LimitedBytesIO(io.BytesIO):
    def __init__(self, limit: int) -> None:
        super().__init__()
        self.limit = limit
        self.truncated = False

    def write(self, value: bytes) -> int:
        remaining = self.limit - self.tell()
        if len(value) > remaining:
            if remaining > 0:
                super().write(value[:remaining])
            self.truncated = True
            raise _OutputLimitReached
        return super().write(value)

    def writelines(self, lines: Iterable[bytes]) -> None:
        for line in lines:
            self.write(line)


def _current_branch(repo: Repo) -> str | None:
    value = repo.refs.read_ref(HEADREF)
    if value is None or not value.startswith(b"ref: refs/heads/"):
        return None
    branch = os.fsdecode(value.removeprefix(b"ref: refs/heads/").strip())
    return branch or None


def _object_id(value: object) -> str:
    identifier = getattr(value, "id", value)
    raw = bytes(identifier)
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        return raw.hex()
    return text if re.fullmatch(r"[0-9a-fA-F]{40,64}", text) else raw.hex()


def _optional_object_id(value: object | None) -> str | None:
    return None if value is None else _object_id(value)


def _decode_path(value: bytes | str) -> str:
    return os.fsdecode(value)


def _validate_ref(value: str) -> None:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 4096
        or "\x00" in value
        or value.startswith("-")
        or any(character.isspace() for character in value)
    ):
        raise ValueError("Git ref is invalid")


def _validate_branch(value: str) -> None:
    _validate_ref(value)
    if value.startswith("refs/") or value.endswith("/") or ".." in value:
        raise ValueError("Git branch is invalid")


def _validate_relative_path(value: str) -> None:
    path = Path(value)
    if (
        not isinstance(value, str)
        or not value
        or "\x00" in value
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(part == ".git" for part in value.split("/"))
    ):
        raise ValueError("Git relative path is invalid")


def _git_failure(operation: str, error: BaseException) -> GitCommandFailedError:
    if isinstance(error, GitCommandFailedError):
        return error
    return GitCommandFailedError(
        operation,
        returncode=128,
        stderr=str(error),
    )


__all__ = ["DulwichGitBackend", "GitBackend"]

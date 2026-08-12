from __future__ import annotations

import io
import os
from pathlib import Path
import re
from collections.abc import Iterable
from typing import Protocol

from dulwich.config import ConfigFile
from dulwich.diff import diff_working_tree_to_tree, write_blob_diff
from dulwich.errors import NotGitRepository
from dulwich.ignore import IgnoreFilterManager
from dulwich.index import ConflictedIndexEntry, get_unstaged_changes
from dulwich.diff import iter_tree_contents
from dulwich.objects import Blob
from dulwich.objectspec import parse_commit
from dulwich.porcelain import (
    CheckoutError,
    Error,
    clean,
    get_tree_changes,
    reset,
    switch,
    worktree_add,
    worktree_list,
    worktree_prune,
    worktree_remove,
)
from dulwich.refs import HEADREF, Ref
from dulwich.repo import Repo

from eidos_runtime.git.errors import GitCommandFailedError
from eidos_runtime.git.models import (
    GitDiffObservation,
    GitRepositoryDiscovery,
    GitStatusObservation,
    GitWorktreeEntry,
    GitWorkingTreePatch,
)
from eidos_runtime.git.native import GitCliFallback
from eidos_runtime.git.refs import GitRefValidator
from eidos_runtime.git.state import (
    apply_worktree_changes as apply_structured_changes,
    capture_worktree_changes as capture_structured_changes,
    untracked_paths,
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

    def clean_worktree_for_retention(self, cwd: Path) -> None: ...

    def clean_worktree_after_handoff(self, cwd: Path) -> None: ...

    def worktree_prune(self, cwd: Path) -> None: ...

    def snapshot_anchor(self, cwd: Path, snapshot_id: str) -> str | None: ...

    def create_snapshot_anchor(
        self, cwd: Path, snapshot_id: str, head: str
    ) -> None: ...

    def delete_snapshot_anchor_if_equals(
        self, cwd: Path, snapshot_id: str, expected_head: str
    ) -> bool: ...

    def list_snapshot_anchors(self, cwd: Path) -> tuple[tuple[str, str], ...]: ...

    def capture_worktree_changes(self, cwd: Path) -> GitWorkingTreePatch: ...

    def apply_worktree_changes(
        self, cwd: Path, changes: GitWorkingTreePatch
    ) -> None: ...

    def create_branch(self, cwd: Path, branch: str) -> None: ...

    def delete_branch_if_equals(
        self, cwd: Path, branch: str, expected_commit: str
    ) -> bool: ...

    def detach_worktree(self, cwd: Path) -> None: ...

    def switch_branch(self, cwd: Path, branch: str) -> None: ...

    def switch_detached(self, cwd: Path, commit: str) -> None: ...


class DulwichGitBackend:
    """Dulwich adapter for Git mechanics owned by the Worktree domain."""

    def __init__(
        self,
        *,
        git_cli_fallback: GitCliFallback | None = None,
        diff_output_limit_bytes: int = DEFAULT_GIT_DIFF_BYTES,
    ) -> None:
        if diff_output_limit_bytes < 1:
            raise ValueError("Git diff output limit must be positive")
        self._git_cli_fallback = git_cli_fallback or GitCliFallback()
        self._diff_output_limit_bytes = diff_output_limit_bytes

    def discover(self, cwd: Path) -> GitRepositoryDiscovery:
        result = self.try_discover(cwd)
        if result is None:
            raise GitCommandFailedError(
                "discover", returncode=128, stderr="not a git repository"
            )
        return result

    def try_discover(self, cwd: Path) -> GitRepositoryDiscovery | None:
        if not cwd.is_absolute() or not cwd.is_dir():
            raise GitCommandFailedError("discover", returncode=None)
        try:
            repo = Repo.discover(cwd)
        except NotGitRepository:
            return None
        except (OSError, ValueError) as error:
            raise _git_failure("discover", error) from error
        return _discovery_from_repo(repo)

    def head(self, cwd: Path) -> str:
        repo = self._open_repository(cwd, "head")
        try:
            return _object_id(repo.head())
        except _DULWICH_FAILURES as error:
            raise _git_failure("head", error) from error

    def current_branch(self, cwd: Path) -> str | None:
        repo = self._open_repository(cwd, "current-branch")
        try:
            return _current_branch(repo)
        except _DULWICH_FAILURES as error:
            raise _git_failure("current-branch", error) from error

    def is_ignored(self, cwd: Path, relative_path: str) -> bool:
        _validate_relative_path(relative_path)
        repo = self._open_repository(cwd, "is-ignored")
        try:
            index = repo.open_index(config=ConfigFile())
            if os.fsencode(relative_path) in index:
                return False
            return IgnoreFilterManager.from_repo(repo).is_ignored(relative_path) is True
        except _DULWICH_FAILURES as error:
            raise _git_failure("is-ignored", error) from error

    def resolve_revision(self, cwd: Path, revision: str) -> str:
        GitRefValidator.revision(revision)
        repo = self._open_repository(cwd, "resolve-revision")
        try:
            return _object_id(parse_commit(repo, revision))
        except _DULWICH_FAILURES as error:
            raise _git_failure("resolve-revision", error) from error

    def branch_commit(self, cwd: Path, branch: str) -> str | None:
        ref = GitRefValidator.branch(branch)
        repo = self._open_repository(cwd, "branch-commit")
        try:
            value = repo.refs[ref]
        except KeyError:
            return None
        except _DULWICH_FAILURES as error:
            raise _git_failure("branch-commit", error) from error
        return _object_id(value)

    def local_branches(self, cwd: Path) -> tuple[str, ...]:
        repo = self._open_repository(cwd, "local-branches")
        try:
            refs = repo.refs.as_dict(b"refs/heads/")
        except _DULWICH_FAILURES as error:
            raise _git_failure("local-branches", error) from error
        return tuple(sorted(os.fsdecode(name) for name in refs))

    def status(self, cwd: Path) -> GitStatusObservation:
        repo = self._open_repository(cwd, "status")
        try:
            index = repo.open_index(config=ConfigFile())
            tree_changes = get_tree_changes(repo, index=index)
            conflict_paths = _conflict_paths(index)
            conflict_set = set(conflict_paths)
            staged_paths = tuple(
                sorted(
                    {
                        _decode_path(path)
                        for values in tree_changes.values()
                        for path in values
                        if _decode_path(path) not in conflict_set
                    }
                )
            )
            unstaged_paths = tuple(
                sorted(
                    {
                        _decode_path(path)
                        for path in _unstaged_paths(repo, index)
                        if _decode_path(path) not in conflict_set
                    }
                )
            )
            untracked = untracked_paths(repo, index)
            head = _object_id(repo.head())
            branch = _current_branch(repo)
        except _DULWICH_FAILURES as error:
            raise _git_failure("status", error) from error
        return GitStatusObservation(
            head=head,
            branch=branch,
            staged_paths=staged_paths,
            unstaged_paths=unstaged_paths,
            untracked_paths=untracked,
            conflict_paths=conflict_paths,
        )

    def diff(
        self,
        cwd: Path,
        *,
        base_commit: str,
        include_untracked: bool = True,
    ) -> GitDiffObservation:
        GitRefValidator.revision(base_commit)
        repo = self._open_repository(cwd, "diff")
        try:
            base = parse_commit(repo, base_commit)
            status = self.status(cwd)
            changed_paths = set(
                status.staged_paths
                + status.unstaged_paths
                + status.conflict_paths
            )
            if include_untracked:
                changed_paths.update(status.untracked_paths)
            head_commit = parse_commit(repo, "HEAD")
            for change in repo.object_store.tree_changes(base.tree, head_commit.tree):
                (old_path, new_path), _modes, _ids = change
                if old_path is not None:
                    changed_paths.add(_decode_path(old_path))
                if new_path is not None:
                    changed_paths.add(_decode_path(new_path))

            output = _LimitedBytesIO(self._diff_output_limit_bytes)
            try:
                index = repo.open_index(config=ConfigFile())
                gitlink_paths = {
                    entry.path
                    for entry in iter_tree_contents(repo.object_store, base.tree)
                    if entry.path is not None and entry.mode == _GITLINK_MODE
                }
                gitlink_paths.update(
                    os.fsencode(path)
                    for path, value in index.iteritems()
                    if not isinstance(value, ConflictedIndexEntry)
                    and int(getattr(value, "mode", 0)) == _GITLINK_MODE
                )
                tracked_paths = {
                    entry.path
                    for entry in iter_tree_contents(repo.object_store, base.tree)
                    if entry.path is not None
                }
                tracked_paths.update(
                    path
                    for path, _value in index.iteritems()
                )
                tracked_paths.difference_update(gitlink_paths)
                diff_working_tree_to_tree(
                    repo,
                    output,
                    bytes(base.id),
                    paths=tuple(sorted(tracked_paths)) or (b"__eidos_no_gitlink__",),
                    config=ConfigFile(),
                )
                changed_paths.update(
                    _write_gitlink_diffs(repo, base.tree, gitlink_paths, output)
                )
                if include_untracked and not output.truncated:
                    for relative in status.untracked_paths:
                        path = _safe_worktree_path(Path(repo.path), relative)
                        if os.fsencode(relative) in index:
                            continue
                        _write_untracked_diff(output, path, relative)
            except _OutputLimitReached:
                output.truncated = True
        except _DULWICH_FAILURES as error:
            raise _git_failure("diff", error) from error
        return GitDiffObservation(
            patch=output.getvalue().decode("utf-8", errors="replace"),
            changed_paths=tuple(sorted(changed_paths)),
            truncated=output.truncated,
        )

    def worktree_list(self, cwd: Path) -> tuple[GitWorktreeEntry, ...]:
        repo = self._open_repository(cwd, "worktree-list")
        try:
            entries = worktree_list(repo)
        except _DULWICH_FAILURES as error:
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
        GitRefValidator.revision(base_commit)
        if branch is not None:
            GitRefValidator.branch(branch)
        repo = self._open_repository(cwd, "worktree-add")
        created_branch = False
        use_native = _has_external_filter(repo)
        try:
            if use_native:
                self._git_cli_fallback.worktree_add(
                    cwd, worktree_root, branch, base_commit
                )
            else:
                branch_ref = GitRefValidator.branch(branch) if branch is not None else None
                if branch_ref is not None:
                    if branch_ref in repo.refs:
                        raise ValueError(f"branch already exists: {branch}")
                    if not repo.refs.set_if_equals(
                        branch_ref, None, base_commit.encode("ascii")
                    ):
                        raise ValueError(f"branch already exists: {branch}")
                    created_branch = True
                worktree_add(
                    repo,
                    worktree_root,
                    branch=branch,
                    commit=None if branch is not None else base_commit,
                    detach=branch is None,
                    force=False,
                )
        except GitCommandFailedError:
            _remove_created_branch(repo, branch, base_commit, created_branch)
            raise
        except _DULWICH_FAILURES as error:
            _remove_created_branch(repo, branch, base_commit, created_branch)
            raise _git_failure("worktree-add", error) from error

    def worktree_remove(self, cwd: Path, worktree_root: Path) -> None:
        repo = self._open_repository(cwd, "worktree-remove")
        try:
            worktree_remove(repo, worktree_root, force=False)
        except _DULWICH_FAILURES as error:
            raise _git_failure("worktree-remove", error) from error

    def clean_worktree_for_compensation(self, cwd: Path) -> None:
        self._reset_and_clean(cwd, operation="worktree-compensation")
        self._git_cli_fallback.clean_destructive(cwd)

    def clean_worktree_for_retention(self, cwd: Path) -> None:
        self._reset_and_clean(cwd, operation="worktree-retention")
        self._git_cli_fallback.clean_destructive(cwd)

    def clean_worktree_after_handoff(self, cwd: Path) -> None:
        repo = self._open_repository(cwd, "worktree-handoff")
        try:
            reset(repo, "hard")
            clean(repo, target_dir=cwd)
        except _DULWICH_FAILURES as error:
            raise _git_failure("worktree-handoff", error) from error

    def _reset_and_clean(self, cwd: Path, *, operation: str) -> None:
        repo = self._open_repository(cwd, operation)
        try:
            reset(repo, "hard")
        except _DULWICH_FAILURES as error:
            raise _git_failure(f"{operation}-reset", error) from error

    def capture_worktree_changes(self, cwd: Path) -> GitWorkingTreePatch:
        repo = self._open_repository(cwd, "worktree-capture")
        return capture_structured_changes(repo)

    def apply_worktree_changes(
        self, cwd: Path, changes: GitWorkingTreePatch
    ) -> None:
        repo = self._open_repository(cwd, "worktree-apply")
        apply_structured_changes(repo, changes)

    def create_branch(self, cwd: Path, branch: str) -> None:
        GitRefValidator.branch(branch)
        repo = self._open_repository(cwd, "worktree-branch-create")
        try:
            switch(repo, "HEAD", create=branch, force=False, detach=False)
        except _DULWICH_FAILURES as error:
            raise _git_failure("worktree-branch-create", error) from error

    def worktree_prune(self, cwd: Path) -> None:
        repo = self._open_repository(cwd, "worktree-prune")
        try:
            worktree_prune(repo)
        except _DULWICH_FAILURES as error:
            raise _git_failure("worktree-prune", error) from error

    def snapshot_anchor(self, cwd: Path, snapshot_id: str) -> str | None:
        ref = _snapshot_ref(snapshot_id)
        repo = self._open_repository(cwd, "snapshot-anchor-read")
        try:
            return _optional_object_id(repo.refs.read_ref(ref))
        except _DULWICH_FAILURES as error:
            raise _git_failure("snapshot-anchor-read", error) from error

    def create_snapshot_anchor(self, cwd: Path, snapshot_id: str, head: str) -> None:
        GitRefValidator.revision(head)
        ref = _snapshot_ref(snapshot_id)
        repo = self._open_repository(cwd, "snapshot-anchor-create")
        try:
            expected = head.encode("ascii")
            if not repo.refs.set_if_equals(ref, None, expected):
                current = repo.refs.read_ref(ref)
                if current != expected:
                    raise GitCommandFailedError(
                        "snapshot-anchor-create", returncode=None
                    )
        except GitCommandFailedError:
            raise
        except (UnicodeEncodeError, *_DULWICH_FAILURES) as error:
            raise _git_failure("snapshot-anchor-create", error) from error

    def delete_snapshot_anchor_if_equals(
        self, cwd: Path, snapshot_id: str, expected_head: str
    ) -> bool:
        GitRefValidator.revision(expected_head)
        ref = _snapshot_ref(snapshot_id)
        repo = self._open_repository(cwd, "snapshot-anchor-delete")
        try:
            return bool(
                repo.refs.remove_if_equals(ref, expected_head.encode("ascii"))
            )
        except (UnicodeEncodeError, *_DULWICH_FAILURES) as error:
            raise _git_failure("snapshot-anchor-delete", error) from error

    def list_snapshot_anchors(self, cwd: Path) -> tuple[tuple[str, str], ...]:
        repo = self._open_repository(cwd, "snapshot-anchor-list")
        prefix = b"refs/eidos/worktree-snapshots/"
        try:
            refs = repo.refs.as_dict(prefix)
        except _DULWICH_FAILURES as error:
            raise _git_failure("snapshot-anchor-list", error) from error
        anchors: list[tuple[str, str]] = []
        for name, value in refs.items():
            snapshot_id = os.fsdecode(name)
            if not snapshot_id or "/" in snapshot_id:
                continue
            anchors.append((snapshot_id, _object_id(value)))
        return tuple(sorted(anchors))

    def delete_branch_if_equals(
        self, cwd: Path, branch: str, expected_commit: str
    ) -> bool:
        ref = GitRefValidator.branch(branch)
        GitRefValidator.revision(expected_commit)
        repo = self._open_repository(cwd, "delete-branch-if-equals")
        try:
            return bool(
                repo.refs.remove_if_equals(ref, expected_commit.encode("ascii"))
            )
        except (UnicodeEncodeError, *_DULWICH_FAILURES) as error:
            raise _git_failure("delete-branch-if-equals", error) from error

    def detach_worktree(self, cwd: Path) -> None:
        repo = self._open_repository(cwd, "worktree-detach")
        try:
            switch(repo, "HEAD", force=False, detach=True)
        except _DULWICH_FAILURES as error:
            raise _git_failure("worktree-detach", error) from error

    def switch_branch(self, cwd: Path, branch: str) -> None:
        GitRefValidator.branch(branch)
        repo = self._open_repository(cwd, "worktree-switch-branch")
        try:
            switch(repo, branch, force=False, detach=False)
        except _DULWICH_FAILURES as error:
            raise _git_failure("worktree-switch-branch", error) from error

    def switch_detached(self, cwd: Path, commit: str) -> None:
        GitRefValidator.revision(commit)
        repo = self._open_repository(cwd, "worktree-switch-detached")
        try:
            switch(repo, commit, force=False, detach=True)
        except _DULWICH_FAILURES as error:
            raise _git_failure("worktree-switch-detached", error) from error

    def _open_repository(self, cwd: Path, operation: str) -> Repo:
        if not cwd.is_absolute() or not cwd.is_dir():
            raise GitCommandFailedError(operation, returncode=None)
        try:
            return Repo.discover(cwd)
        except NotGitRepository as error:
            raise GitCommandFailedError(
                operation, returncode=128, stderr=str(error)
            ) from error
        except (OSError, ValueError) as error:
            raise _git_failure(operation, error) from error


_DULWICH_FAILURES = (
    KeyError,
    OSError,
    TypeError,
    ValueError,
    Error,
    CheckoutError,
    AssertionError,
)


def _discovery_from_repo(repo: Repo) -> GitRepositoryDiscovery:
    try:
        return GitRepositoryDiscovery(
            repository_root=str(Path(repo.path).resolve(strict=True)),
            git_dir=str(Path(repo.controldir()).resolve(strict=True)),
            git_common_dir=str(Path(repo.commondir()).resolve(strict=True)),
        )
    except (OSError, ValueError) as error:
        raise _git_failure("discover", error) from error


def _current_branch(repo: Repo) -> str | None:
    value = repo.refs.read_ref(HEADREF)
    if value is None or not value.startswith(b"ref: refs/heads/"):
        return None
    branch = os.fsdecode(value.removeprefix(b"ref: refs/heads/").strip())
    return branch or None


def _conflict_paths(index: object) -> tuple[str, ...]:
    return tuple(
        sorted(
            os.fsdecode(path)
            for path, value in index.iteritems()
            if isinstance(value, ConflictedIndexEntry)
        )
    )


def _unstaged_paths(repo: Repo, index: object) -> tuple[bytes, ...]:
    result: list[bytes] = []
    entries = dict(index.iteritems())
    for path in get_unstaged_changes(index, repo.path, None, False):
        value = entries.get(path)
        if isinstance(value, ConflictedIndexEntry):
            result.append(path)
            continue
        if value is not None and int(getattr(value, "mode", 0)) == _GITLINK_MODE:
            nested_head = _nested_worktree_head(Path(repo.path) / os.fsdecode(path))
            if nested_head == bytes(getattr(value, "sha")):
                continue
        result.append(path)
    return tuple(result)


def _nested_worktree_head(path: Path) -> bytes | None:
    if not path.is_dir() or path.is_symlink():
        return None
    try:
        nested = Repo.discover(path)
        if Path(nested.path).resolve(strict=True) != path.resolve(strict=True):
            return None
        return bytes(nested.head())
    except (NotGitRepository, OSError, ValueError):
        return None


def _has_external_filter(repo: Repo) -> bool:
    config = repo.get_config_stack()
    for section in config.sections():
        if len(section) < 2 or section[0].lower() != b"filter":
            continue
        for backend in config.backends:
            for name, value in backend.items(section):
                if name.lower() in {b"clean", b"process", b"smudge"} and value.strip():
                    return True
    return False


def _remove_created_branch(
    repo: Repo,
    branch: str | None,
    base_commit: str,
    created_branch: bool,
) -> None:
    if not created_branch or branch is None:
        return
    try:
        repo.refs.remove_if_equals(
            GitRefValidator.branch(branch), base_commit.encode("ascii")
        )
    except (KeyError, OSError, TypeError, UnicodeEncodeError, ValueError):
        pass


def _write_untracked_diff(output: io.BytesIO, path: Path, relative: str) -> None:
    value = path.lstat()
    if value.st_mode & 0o170000 == 0o120000:
        blob = Blob.from_string(os.fsencode(os.readlink(path)))
        mode = 0o120000
    elif value.st_mode & 0o170000 == 0o100000:
        blob = Blob.from_string(path.read_bytes())
        mode = 0o100755 if value.st_mode & 0o100 else 0o100644
    else:
        return
    write_blob_diff(
        output,
        (None, None, None),
        (os.fsencode(relative), mode, blob),
    )


def _write_gitlink_diffs(
    repo: Repo,
    tree_id: bytes,
    paths: set[bytes],
    output: io.BytesIO,
) -> tuple[str, ...]:
    changed: list[str] = []
    for entry in iter_tree_contents(repo.object_store, tree_id):
        if entry.path is None or entry.path not in paths or entry.sha is None:
            continue
        relative = os.fsdecode(entry.path)
        path = _safe_worktree_path(Path(repo.path), relative)
        current: bytes | None = None
        if path.is_dir() and not path.is_symlink():
            try:
                nested = Repo.discover(path)
                if Path(nested.path).resolve(strict=True) == path.resolve(strict=True):
                    current = bytes(nested.head())
            except (NotGitRepository, OSError, ValueError):
                current = None
        if current == entry.sha:
            continue
        changed.append(relative)
        old_blob = Blob.from_string(b"Subproject commit " + entry.sha + b"\n")
        new_blob = (
            Blob.from_string(b"Subproject commit " + current + b"\n")
            if current is not None
            else None
        )
        write_blob_diff(
            output,
            (entry.path, _GITLINK_MODE, old_blob),
            (entry.path if current is not None else None, _GITLINK_MODE if current is not None else None, new_blob),
        )
    return tuple(changed)


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


def _snapshot_ref(snapshot_id: str) -> Ref:
    if (
        not snapshot_id
        or len(snapshot_id) > 256
        or any(
            character
            not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
            for character in snapshot_id
        )
    ):
        raise ValueError("invalid snapshot id")
    return Ref(f"refs/eidos/worktree-snapshots/{snapshot_id}".encode("utf-8"))


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


def _safe_worktree_path(root: Path, relative: str) -> Path:
    _validate_relative_path(relative)
    result = root / relative
    if not result.parent.resolve(strict=False).is_relative_to(root.resolve(strict=True)):
        raise ValueError("Git relative path escapes repository")
    return result


def _git_failure(operation: str, error: BaseException) -> GitCommandFailedError:
    if isinstance(error, GitCommandFailedError):
        return error
    return GitCommandFailedError(operation, returncode=128, stderr=str(error))


__all__ = ["DulwichGitBackend", "GitBackend"]

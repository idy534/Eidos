from __future__ import annotations

import os
from pathlib import Path
import re
import threading
from typing import Protocol

from dulwich.config import ConfigFile
from dulwich.errors import NotGitRepository
from dulwich.ignore import IgnoreFilterManager
from dulwich.objectspec import parse_commit
from dulwich.porcelain import (
    CheckoutError,
    Error,
    clean,
    reset,
    switch,
    worktree_list,
    worktree_prune,
    worktree_remove,
)
from dulwich.refs import HEADREF, Ref
from dulwich.repo import Repo

from eidos_runtime.git.errors import (
    GitCommandFailedError,
    GitUnsupportedOperationError,
)
from eidos_runtime.git.models import (
    GitDiffObservation,
    GitRepositoryDiscovery,
    GitRemoteObservation,
    GitOperationState,
    GitStatusObservation,
    GitWorktreeEntry,
    GitWorkingTreePatch,
)
from eidos_runtime.git.native import DEFAULT_GIT_PATCH_BYTES, GitCli
from eidos_runtime.git.refs import GitRefValidator


DEFAULT_GIT_DIFF_BYTES = 512 * 1024


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
        path: str | None = None,
    ) -> GitDiffObservation: ...

    def stage(self, cwd: Path, paths: tuple[str, ...]) -> GitStatusObservation: ...

    def unstage(self, cwd: Path, paths: tuple[str, ...]) -> GitStatusObservation: ...

    def commit(self, cwd: Path, message: str) -> str: ...

    def remote_status(self, cwd: Path) -> GitRemoteObservation: ...

    def fetch(
        self, cwd: Path, remote: str, *, cancel: threading.Event
    ) -> GitRemoteObservation: ...

    def merge_upstream_ff_only(self, cwd: Path) -> GitRemoteObservation: ...

    def operation_state(self, cwd: Path) -> GitOperationState: ...

    def merge(self, cwd: Path, target: str) -> None: ...

    def merge_abort(self, cwd: Path) -> None: ...

    def rebase(self, cwd: Path, target: str) -> None: ...

    def rebase_continue(self, cwd: Path) -> None: ...

    def rebase_abort(self, cwd: Path) -> None: ...

    def push(
        self,
        cwd: Path,
        remote: str,
        *,
        destination_branch: str,
        set_upstream: bool,
        cancel: threading.Event,
    ) -> GitRemoteObservation: ...

    def validate_remote_transport(self, cwd: Path, remote: str) -> None: ...

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
        git_cli: GitCli | None = None,
        diff_output_limit_bytes: int = DEFAULT_GIT_DIFF_BYTES,
    ) -> None:
        if diff_output_limit_bytes < 1:
            raise ValueError("Git diff output limit must be positive")
        self._git_cli = git_cli or GitCli()
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
            staged_paths, unstaged_paths, untracked, conflict_paths = (
                _status_from_porcelain(self._git_cli.status_porcelain(cwd))
            )
            head = _object_id(repo.head())
            branch = _current_branch(repo)
        except (GitCommandFailedError, *_DULWICH_FAILURES) as error:
            if isinstance(error, GitCommandFailedError):
                raise
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
        path: str | None = None,
    ) -> GitDiffObservation:
        GitRefValidator.revision(base_commit)
        if path is not None:
            _validate_relative_path(path)
        repo = self._open_repository(cwd, "diff")
        try:
            captured = self._git_cli.diff(
                Path(repo.path),
                base_commit=base_commit,
                include_untracked=include_untracked,
                output_limit_bytes=self._diff_output_limit_bytes,
                path=path,
            )
        except (GitCommandFailedError, *_DULWICH_FAILURES) as error:
            if isinstance(error, GitCommandFailedError):
                raise
            raise _git_failure("diff", error) from error
        return GitDiffObservation(
            patch=captured.patch.decode("utf-8", errors="replace"),
            changed_paths=captured.changed_paths,
            truncated=captured.truncated,
        )

    def stage(self, cwd: Path, paths: tuple[str, ...]) -> GitStatusObservation:
        for path in paths:
            _validate_relative_path(path)
        self._open_repository(cwd, "stage")
        self._git_cli.stage(cwd, paths)
        return self.status(cwd)

    def unstage(self, cwd: Path, paths: tuple[str, ...]) -> GitStatusObservation:
        for path in paths:
            _validate_relative_path(path)
        self._open_repository(cwd, "unstage")
        self._git_cli.unstage(cwd, paths)
        return self.status(cwd)

    def commit(self, cwd: Path, message: str) -> str:
        self._open_repository(cwd, "commit")
        self._git_cli.commit(cwd, message)
        return self.head(cwd)

    def remote_status(self, cwd: Path) -> GitRemoteObservation:
        self._open_repository(cwd, "remote-status")
        return self._git_cli.remote_status(cwd)

    def fetch(
        self, cwd: Path, remote: str, *, cancel: threading.Event
    ) -> GitRemoteObservation:
        self._open_repository(cwd, "fetch")
        before = self.remote_status(cwd)
        if remote not in {item.name for item in before.remotes}:
            raise GitCommandFailedError("remote-not-found", returncode=None)
        self._git_cli.fetch(cwd, remote, cancel=cancel)
        return self.remote_status(cwd)

    def merge_upstream_ff_only(self, cwd: Path) -> GitRemoteObservation:
        self._open_repository(cwd, "pull-ff-only")
        self._git_cli.merge_upstream_ff_only(cwd)
        return self.remote_status(cwd)

    def operation_state(self, cwd: Path) -> GitOperationState:
        self._open_repository(cwd, "operation-state")
        return self._git_cli.operation_state(cwd)

    def merge(self, cwd: Path, target: str) -> None:
        GitRefValidator.revision(target)
        self._open_repository(cwd, "merge")
        self._git_cli.merge(cwd, target)

    def merge_abort(self, cwd: Path) -> None:
        self._open_repository(cwd, "merge-abort")
        self._git_cli.merge_abort(cwd)

    def rebase(self, cwd: Path, target: str) -> None:
        GitRefValidator.revision(target)
        self._open_repository(cwd, "rebase")
        self._git_cli.rebase(cwd, target)

    def rebase_continue(self, cwd: Path) -> None:
        self._open_repository(cwd, "rebase-continue")
        self._git_cli.rebase_continue(cwd)

    def rebase_abort(self, cwd: Path) -> None:
        self._open_repository(cwd, "rebase-abort")
        self._git_cli.rebase_abort(cwd)

    def push(
        self,
        cwd: Path,
        remote: str,
        *,
        destination_branch: str,
        set_upstream: bool,
        cancel: threading.Event,
    ) -> GitRemoteObservation:
        self._open_repository(cwd, "push")
        before = self.remote_status(cwd)
        if remote not in {item.name for item in before.remotes}:
            raise GitCommandFailedError("remote-not-found", returncode=None)
        self._git_cli.push(
            cwd,
            remote,
            destination_branch=destination_branch,
            set_upstream=set_upstream,
            cancel=cancel,
        )
        return self.remote_status(cwd)

    def validate_remote_transport(self, cwd: Path, remote: str) -> None:
        self._open_repository(cwd, "remote-transport")
        self._git_cli.validate_remote_transport(cwd, remote)

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
        try:
            self._git_cli.worktree_add(cwd, worktree_root, branch, base_commit)
        except _DULWICH_FAILURES as error:
            raise _git_failure("worktree-add", error) from error

    def worktree_remove(self, cwd: Path, worktree_root: Path) -> None:
        repo = self._open_repository(cwd, "worktree-remove")
        try:
            worktree_remove(repo, worktree_root, force=False)
        except _DULWICH_FAILURES as error:
            raise _git_failure("worktree-remove", error) from error

    def clean_worktree_for_compensation(self, cwd: Path) -> None:
        self._reset_and_clean(cwd, operation="worktree-compensation")
        self._git_cli.clean_destructive(cwd)

    def clean_worktree_for_retention(self, cwd: Path) -> None:
        self._reset_and_clean(cwd, operation="worktree-retention")
        self._git_cli.clean_destructive(cwd)

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
        self._open_repository(cwd, "worktree-capture")
        try:
            status = self.status(cwd)
            changed_paths = (
                status.staged_paths
                + status.unstaged_paths
                + status.untracked_paths
                + status.conflict_paths
            )
            gitlinks = self._git_cli.gitlink_paths(cwd, changed_paths)
            if gitlinks:
                raise GitUnsupportedOperationError(
                    "worktree-capture",
                    stderr="dirty submodule transfer is unsupported",
                )
            return GitWorkingTreePatch(
                full_patch=self._git_cli.capture_working_tree_patch(
                    cwd, output_limit_bytes=DEFAULT_GIT_PATCH_BYTES
                ),
                staged_patch=self._git_cli.capture_staged_patch(
                    cwd, output_limit_bytes=DEFAULT_GIT_PATCH_BYTES
                ),
            )
        except GitCommandFailedError:
            raise

    def apply_worktree_changes(
        self, cwd: Path, changes: GitWorkingTreePatch
    ) -> None:
        self._open_repository(cwd, "worktree-apply")
        self._git_cli.apply_working_tree_patch(
            cwd,
            full_patch=changes.full_patch,
            staged_patch=changes.staged_patch,
        )

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


def _status_from_porcelain(
    output: bytes,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    staged: set[str] = set()
    unstaged: set[str] = set()
    untracked: set[str] = set()
    conflicts: set[str] = set()
    for record in output.split(b"\0"):
        if not record:
            continue
        if len(record) < 4 or record[2:3] != b" ":
            raise GitCommandFailedError(
                "status",
                returncode=None,
                stderr="Git status output is invalid",
            )
        code = record[:2].decode("ascii")
        path = os.fsdecode(record[3:])
        if not path:
            raise GitCommandFailedError(
                "status",
                returncode=None,
                stderr="Git status path is empty",
            )
        if code == "??":
            untracked.add(path)
            continue
        if "U" in code or code in {"AA", "DD"}:
            conflicts.add(path)
            continue
        if code[0] != " ":
            staged.add(path)
        if code[1] != " ":
            unstaged.add(path)
    return (
        tuple(sorted(staged)),
        tuple(sorted(unstaged)),
        tuple(sorted(untracked)),
        tuple(sorted(conflicts)),
    )


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


def _git_failure(operation: str, error: BaseException) -> GitCommandFailedError:
    if isinstance(error, GitCommandFailedError):
        return error
    return GitCommandFailedError(operation, returncode=128, stderr=str(error))


__all__ = ["DulwichGitBackend", "GitBackend"]

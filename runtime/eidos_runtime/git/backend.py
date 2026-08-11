from __future__ import annotations

from dataclasses import dataclass
import io
import os
from pathlib import Path
import re
from typing import Protocol

from dulwich import porcelain
from dulwich.diff import diff_working_tree_to_tree
from dulwich.index import ConflictedIndexEntry, get_unstaged_changes
from dulwich.objects import Blob
from dulwich.objectspec import parse_commit
from dulwich.patch import write_blob_diff
from dulwich.porcelain import get_tree_changes, get_untracked_paths
from dulwich.refs import HEADREF, Ref
from dulwich.repo import Repo

from eidos_runtime.git.errors import (
    GitCommandFailedError,
)
from eidos_runtime.git.models import GitWorktreeEntry
from eidos_runtime.git.process import (
    DEFAULT_GIT_DIFF_BYTES,
    DEFAULT_GIT_OUTPUT_BYTES,
    GitCommandResult,
    GitProcess,
)


@dataclass(frozen=True)
class GitStatusObservation:
    """Eidos-owned structured status facts returned by a Git backend."""

    staged_paths: tuple[str, ...]
    unstaged_paths: tuple[str, ...]
    untracked_paths: tuple[str, ...]
    conflict_paths: tuple[str, ...]

    @property
    def staged_count(self) -> int:
        return len(self.staged_paths)

    @property
    def unstaged_count(self) -> int:
        return len(self.unstaged_paths)

    @property
    def untracked_count(self) -> int:
        return len(self.untracked_paths)

    @property
    def conflict_count(self) -> int:
        return len(self.conflict_paths)


class GitBackend(Protocol):
    """Mechanics used by the Eidos Worktree lifecycle boundary."""

    def rev_parse_show_toplevel(self, cwd: Path) -> str: ...

    def rev_parse_git_dir(self, cwd: Path) -> str: ...

    def rev_parse_git_common_dir(self, cwd: Path) -> str: ...

    def resolve_ref(self, cwd: Path, ref: str) -> str: ...

    def try_resolve_ref(self, cwd: Path, ref: str) -> str | None: ...

    def symbolic_ref_short(self, cwd: Path) -> str | None: ...

    def worktree_list(
        self, cwd: Path
    ) -> tuple[GitWorktreeEntry, ...] | GitCommandResult: ...

    def worktree_add(
        self, cwd: Path, worktree_root: Path, branch: str, base_commit: str
    ) -> None: ...

    def worktree_remove(self, cwd: Path, worktree_root: Path) -> None: ...

    def worktree_prune(self, cwd: Path) -> None: ...

    def status_porcelain_v2(self, cwd: Path) -> GitCommandResult: ...

    def diff_head(self, cwd: Path) -> GitCommandResult: ...

    def diff_baseline(self, cwd: Path, base_commit: str) -> GitCommandResult: ...

    def diff_name_only(
        self,
        cwd: Path,
        *,
        scope: str,
        base_commit: str | None = None,
    ) -> GitCommandResult: ...

    def untracked_files(self, cwd: Path) -> GitCommandResult: ...

    def diff_untracked(
        self,
        cwd: Path,
        relative_path: str,
        *,
        output_limit_bytes: int = DEFAULT_GIT_DIFF_BYTES,
    ) -> GitCommandResult: ...

    def update_ref_delete(
        self, cwd: Path, branch: str, expected_base_commit: str
    ) -> None: ...

    def branch_exists(self, cwd: Path, branch: str) -> bool: ...


class NativeGitFallback(GitProcess):
    """Hardened native Git fallback for mechanics Dulwich cannot safely run.

    Dulwich's worktree creation calls its blob normalizer while checking out
    files. That normalizer can honor configured clean/process filters. Eidos
    therefore keeps the existing hardened subprocess implementation for this
    one operation until a non-executing Dulwich checkout path is available.
    """


class DulwichGitBackend:
    """Dulwich-backed Git mechanics with Eidos-owned result DTOs."""

    def __init__(
        self,
        *,
        native_fallback: NativeGitFallback | None = None,
        output_limit_bytes: int = DEFAULT_GIT_OUTPUT_BYTES,
    ) -> None:
        if output_limit_bytes < 1:
            raise ValueError("Git backend output limit must be positive")
        self.native_fallback = native_fallback or NativeGitFallback()
        self.output_limit_bytes = output_limit_bytes

    def rev_parse_show_toplevel(self, cwd: Path) -> str:
        repo = self._discover(cwd, "rev-parse-show-toplevel")
        return str(Path(repo.path).resolve(strict=True))

    def rev_parse_git_dir(self, cwd: Path) -> str:
        repo = self._discover(cwd, "rev-parse-git-dir")
        return str(Path(repo.controldir()).resolve(strict=True))

    def rev_parse_git_common_dir(self, cwd: Path) -> str:
        repo = self._discover(cwd, "rev-parse-git-common-dir")
        return str(Path(repo.commondir()).resolve(strict=True))

    def resolve_ref(self, cwd: Path, ref: str) -> str:
        _validate_ref(ref)
        repo = self._discover(cwd, "rev-parse-ref")
        try:
            return _object_id(parse_commit(repo, ref))
        except (KeyError, TypeError, ValueError) as error:
            raise GitCommandFailedError(
                "rev-parse-ref", returncode=128, stderr=str(error)
            ) from error

    def try_resolve_ref(self, cwd: Path, ref: str) -> str | None:
        _validate_ref(ref)
        try:
            return self.resolve_ref(cwd, ref)
        except GitCommandFailedError:
            return None

    def symbolic_ref_short(self, cwd: Path) -> str | None:
        repo = self._discover(cwd, "symbolic-ref-short")
        try:
            value = repo.refs.read_ref(HEADREF)
        except (OSError, KeyError) as error:
            raise GitCommandFailedError(
                "symbolic-ref-short", returncode=128, stderr=str(error)
            ) from error
        if value is None or not value.startswith(b"ref: refs/heads/"):
            return None
        return os.fsdecode(value.removeprefix(b"ref: refs/heads/").strip())

    def worktree_list(
        self, cwd: Path
    ) -> tuple[GitWorktreeEntry, ...]:
        repo = self._discover(cwd, "worktree-list")
        try:
            entries = porcelain.worktree_list(repo)
        except Exception as error:
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
        self, cwd: Path, worktree_root: Path, branch: str, base_commit: str
    ) -> None:
        # See NativeGitFallback. The fallback retains the configured timeout,
        # bounded output, process-group cleanup, and filter hardening.
        self.native_fallback.worktree_add(cwd, worktree_root, branch, base_commit)

    def worktree_remove(self, cwd: Path, worktree_root: Path) -> None:
        repo = self._discover(cwd, "worktree-remove")
        try:
            porcelain.worktree_remove(repo, worktree_root, force=False)
        except Exception as error:
            raise _git_failure("worktree-remove", error) from error

    def worktree_prune(self, cwd: Path) -> None:
        repo = self._discover(cwd, "worktree-prune")
        try:
            porcelain.worktree_prune(repo)
        except Exception as error:
            raise _git_failure("worktree-prune", error) from error

    def status_porcelain_v2(self, cwd: Path) -> GitCommandResult:
        observation = self.status_observation(cwd)
        records: list[str] = []
        conflict_paths = set(observation.conflict_paths)
        for path in observation.conflict_paths:
            records.append(
                f"u UU N... 100644 100644 100644 "
                f"{'0' * 40} {'0' * 40} {'0' * 40} "
                f"{'0' * 40} {path}"
            )
        staged = set(observation.staged_paths)
        unstaged = set(observation.unstaged_paths)
        for path in sorted((staged | unstaged) - conflict_paths):
            index_state = "M" if path in staged else "."
            worktree_state = "M" if path in unstaged else "."
            if path in unstaged and not (Path(cwd) / path).exists():
                worktree_state = "D"
            records.append(
                f"1 {index_state}{worktree_state} N... 100644 100644 100644 "
                f"{'0' * 40} {'0' * 40} {path}"
            )
        records.extend(f"? {path}" for path in observation.untracked_paths)
        return _result("\0".join(records) + ("\0" if records else ""), self.output_limit_bytes)

    def diff_head(self, cwd: Path) -> GitCommandResult:
        return self._diff(cwd, "HEAD", "diff-head")

    def diff_baseline(self, cwd: Path, base_commit: str) -> GitCommandResult:
        _validate_ref(base_commit)
        return self._diff(cwd, base_commit, "diff-baseline")

    def diff_name_only(
        self,
        cwd: Path,
        *,
        scope: str,
        base_commit: str | None = None,
    ) -> GitCommandResult:
        if scope == "head":
            base = None
        elif scope == "baseline" and base_commit is not None:
            _validate_ref(base_commit)
            base = base_commit
        else:
            raise ValueError("Git diff scope is invalid")
        repo = self._discover(cwd, f"diff-{scope}-names")
        names = self._changed_paths(repo, base)
        return _result("".join(f"{path}\0" for path in names), self.output_limit_bytes)

    def untracked_files(self, cwd: Path) -> GitCommandResult:
        observation = self.status_observation(cwd)
        return _result(
            "".join(f"{path}\0" for path in observation.untracked_paths),
            self.output_limit_bytes,
        )

    def diff_untracked(
        self,
        cwd: Path,
        relative_path: str,
        *,
        output_limit_bytes: int = DEFAULT_GIT_DIFF_BYTES,
    ) -> GitCommandResult:
        _validate_relative_path(relative_path)
        root = Path(cwd).resolve(strict=True)
        path = (root / relative_path).resolve(strict=False)
        try:
            path.relative_to(root)
        except ValueError as error:
            raise ValueError("Git path escapes repository") from error
        if not path.is_file():
            return _result("", output_limit_bytes)
        blob = Blob()
        try:
            blob.data = path.read_bytes()
        except OSError as error:
            raise _git_failure("diff-untracked", error) from error
        output = _LimitedBytesIO(output_limit_bytes)
        try:
            write_blob_diff(
                output,
                (None, None, None),
                (os.fsencode(relative_path), 0o100644, blob),
            )
        except _OutputLimitReached:
            pass
        except Exception as error:
            raise _git_failure("diff-untracked", error) from error
        return GitCommandResult(
            stdout=output.getvalue().decode("utf-8", errors="replace"),
            stderr="",
            returncode=1,
            stdout_truncated=output.truncated,
            stderr_truncated=False,
        )

    def update_ref_delete(
        self, cwd: Path, branch: str, expected_base_commit: str
    ) -> None:
        _validate_branch(branch)
        _validate_ref(expected_base_commit)
        repo = self._discover(cwd, "update-ref-delete")
        try:
            deleted = repo.refs.remove_if_equals(
                Ref(f"refs/heads/{branch}".encode("utf-8")),
                expected_base_commit.encode("ascii"),
            )
        except (OSError, ValueError) as error:
            raise _git_failure("update-ref-delete", error) from error
        if not deleted:
            raise GitCommandFailedError(
                "update-ref-delete", returncode=1, stderr="ref changed"
            )

    def branch_exists(self, cwd: Path, branch: str) -> bool:
        _validate_branch(branch)
        return self.try_resolve_ref(cwd, f"refs/heads/{branch}") is not None

    def status_observation(self, cwd: Path) -> GitStatusObservation:
        repo = self._discover(cwd, "status")
        try:
            index = repo.open_index()
            staged_changes = get_tree_changes(repo, index)
            staged_paths = tuple(
                sorted(
                    {
                        _decode_path(path)
                        for paths in staged_changes.values()
                        for path in paths
                    }
                )
            )
            conflict_paths = tuple(
                sorted(
                    _decode_path(path)
                    for path, entry in index.iteritems()
                    if isinstance(entry, ConflictedIndexEntry)
                    or _index_stage(entry) != 0
                )
            )
            conflict_set = set(conflict_paths)
            unstaged_paths = tuple(
                sorted(
                    path
                    for path in (
                        _decode_path(value)
                        for value in get_unstaged_changes(
                            index, repo.path, None, False
                        )
                    )
                    if path not in conflict_set
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
                    )
                )
            )
        except Exception as error:
            raise _git_failure("status", error) from error
        return GitStatusObservation(
            staged_paths=tuple(path for path in staged_paths if path not in conflict_set),
            unstaged_paths=unstaged_paths,
            untracked_paths=untracked_paths,
            conflict_paths=conflict_paths,
        )

    def _diff(self, cwd: Path, base: str, operation: str) -> GitCommandResult:
        repo = self._discover(cwd, operation)
        output = _LimitedBytesIO(DEFAULT_GIT_DIFF_BYTES)
        original_normalizer = repo.get_blob_normalizer
        # Dulwich's normalizer can execute configured clean/process filters.
        # Eidos deliberately supplies no normalizer for read-only diff work.
        repo.get_blob_normalizer = lambda: None  # type: ignore[method-assign]
        try:
            diff_working_tree_to_tree(
                repo,
                output,
                parse_commit(repo, base).id,
            )
        except _OutputLimitReached:
            pass
        except Exception as error:
            raise _git_failure(operation, error) from error
        finally:
            repo.get_blob_normalizer = original_normalizer  # type: ignore[method-assign]
        return GitCommandResult(
            stdout=output.getvalue().decode("utf-8", errors="replace"),
            stderr="",
            returncode=0,
            stdout_truncated=output.truncated,
            stderr_truncated=False,
        )

    def _changed_paths(self, repo: Repo, base: str | None) -> tuple[str, ...]:
        try:
            index = repo.open_index()
            index_tree = index.commit(repo.object_store)
            if base is None:
                base_tree = parse_commit(repo, "HEAD").tree
            else:
                base_tree = parse_commit(repo, base).tree
            paths = {
                _decode_path(new_path or old_path)
                for (old_path, new_path), _modes, _shas in
                repo.object_store.tree_changes(base_tree, index_tree)
                if new_path is not None or old_path is not None
            }
            paths.update(
                _decode_path(path)
                for path in get_unstaged_changes(index, repo.path, None, False)
            )
            return tuple(sorted(paths))
        except Exception as error:
            raise _git_failure("diff-names", error) from error

    @staticmethod
    def _discover(cwd: Path, operation: str) -> Repo:
        try:
            if not cwd.is_absolute() or not cwd.is_dir():
                raise FileNotFoundError(str(cwd))
            return Repo.discover(cwd)
        except Exception as error:
            raise _git_failure(operation, error) from error


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


def _result(stdout: str, limit: int) -> GitCommandResult:
    encoded = stdout.encode("utf-8")
    if len(encoded) <= limit:
        return GitCommandResult(stdout, "", 0, False, False)
    return GitCommandResult(
        encoded[:limit].decode("utf-8", errors="replace"),
        "",
        0,
        True,
        False,
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


def _index_stage(entry: object) -> int:
    value = getattr(entry, "stage", 0)
    value = value() if callable(value) else value
    return int(getattr(value, "value", value))


def _validate_ref(value: str) -> None:
    if (
        not isinstance(value, str)
        or not value
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
    if (
        not value
        or "\x00" in value
        or Path(value).is_absolute()
        or any(part == ".." for part in Path(value).parts)
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


__all__ = [
    "DulwichGitBackend",
    "GitBackend",
    "GitStatusObservation",
    "NativeGitFallback",
]

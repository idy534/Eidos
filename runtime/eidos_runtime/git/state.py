from __future__ import annotations

import base64
from dataclasses import replace
import io
import os
from pathlib import Path
import shutil
import stat
import tempfile
from collections.abc import Iterable

from dulwich.config import ConfigFile
from dulwich.diff import (
    diff_index_to_tree,
    diff_working_tree_to_tree,
    write_blob_diff,
)
from dulwich.ignore import IgnoreFilterManager
from dulwich.patch import PatchApplicationFailure
from dulwich.index import (
    ConflictedIndexEntry,
    Index,
    IndexEntry,
    get_unstaged_changes,
    index_entry_from_stat,
)
from dulwich.objects import Blob
from dulwich.objectspec import parse_commit
from dulwich.porcelain import apply_patch, get_untracked_paths
from dulwich.repo import Repo

from eidos_runtime.git.errors import GitCommandFailedError
from eidos_runtime.git.models import (
    GitWorkingTreePatch,
    GitWorkingTreeState,
    GitWorktreeStateEntry,
)


MAX_CAPTURE_PATCH_BYTES = 64 * 1024 * 1024
_GITLINK_MODE = 0o160000
_SYMLINK_MODE = 0o120000


def capture_worktree_changes(repo: Repo) -> GitWorkingTreePatch:
    """Capture exact working and index state with Dulwich primitives.

    The source filesystem is read only.  New Git objects are not written to
    the source repository during capture.  Patch text is retained for display,
    while the structured states are the lossless transfer authority.
    """

    try:
        head = _object_id(repo.head())
        index = repo.open_index(config=ConfigFile())
        base_paths = tuple(
            sorted(
                os.fsdecode(entry.path)
                for entry in _tree_entries(repo, parse_commit(repo, "HEAD").tree)
                if entry.path is not None
            )
        )
        index_entries = _index_entries(index)
        untracked = untracked_paths(repo, index)
        full_paths = tuple(sorted(set(base_paths) | set(index_entries) | set(untracked)))
        full_entries = tuple(
            entry
            for relative in full_paths
            if (entry := _working_tree_entry(repo, relative, index_entries.get(relative)))
            is not None
        )
        staged_entries = tuple(
            sorted(
                (_index_state_entry(repo, relative, value) for relative, value in index_entries.items()),
                key=lambda item: item.path,
            )
        )
        full_patch = _render_full_patch(repo, head, untracked)
        staged_patch = _render_staged_patch(repo, head)
    except GitCommandFailedError:
        raise
    except (KeyError, OSError, TypeError, ValueError, AssertionError) as error:
        raise GitCommandFailedError(
            "worktree-capture",
            returncode=None,
            stderr=str(error),
        ) from error

    return GitWorkingTreePatch(
        full_patch=full_patch,
        staged_patch=staged_patch,
        full_state=GitWorkingTreeState(
            base_head=head,
            base_paths=base_paths,
            entries=full_entries,
        ),
        staged_state=GitWorkingTreeState(
            base_head=head,
            base_paths=base_paths,
            entries=staged_entries,
        ),
    )


def apply_worktree_changes(repo: Repo, changes: GitWorkingTreePatch) -> None:
    """Apply Dulwich patch or structured state without a second Git engine."""

    if changes.full_state is not None or changes.staged_state is not None:
        if changes.full_state is None or changes.staged_state is None:
            raise GitCommandFailedError(
                "worktree-apply",
                returncode=None,
                stderr="working-tree state is incomplete",
            )
        _apply_state(repo, changes.full_state, changes.staged_state)
        return

    try:
        if changes.full_patch:
            apply_patch(repo, io.BytesIO(changes.full_patch.encode("utf-8")))
        if changes.staged_patch:
            apply_patch(
                repo,
                io.BytesIO(changes.staged_patch.encode("utf-8")),
                cached=True,
            )
    except (KeyError, OSError, TypeError, ValueError, PatchApplicationFailure) as error:
        raise GitCommandFailedError(
            "worktree-apply",
            returncode=None,
            stderr=str(error),
        ) from error


def _apply_state(
    repo: Repo,
    full_state: GitWorkingTreeState,
    staged_state: GitWorkingTreeState,
) -> None:
    if full_state.base_head != staged_state.base_head:
        raise GitCommandFailedError(
            "worktree-apply",
            returncode=None,
            stderr="working-tree state base mismatch",
        )
    try:
        if _object_id(repo.head()) != full_state.base_head:
            raise GitCommandFailedError(
                "worktree-apply",
                returncode=None,
                stderr="target worktree base changed",
            )
        index = repo.open_index(config=ConfigFile())
        if _has_uncommitted_target_changes(repo, index):
            raise GitCommandFailedError(
                "worktree-apply",
                returncode=None,
                stderr="target worktree is not clean",
            )
        desired = {entry.path: entry for entry in full_state.entries}
        existing_tracked = {
            os.fsdecode(path)
            for path, value in index.iteritems()
            if not isinstance(value, ConflictedIndexEntry)
        }
        existing_tracked.update(full_state.base_paths)
        for relative in sorted(existing_tracked - desired.keys()):
            _remove_tracked_entry(repo.path, relative)
        for entry in full_state.entries:
            _materialize_entry(repo.path, entry)
        _write_index(repo, staged_state, full_state)
    except GitCommandFailedError:
        raise
    except ValueError as error:
        operation = (
            "worktree-materialize"
            if "symlink target" in str(error)
            else "worktree-apply"
        )
        raise GitCommandFailedError(
            operation,
            returncode=None,
            stderr=str(error),
        ) from error
    except (KeyError, OSError, TypeError, AssertionError) as error:
        raise GitCommandFailedError(
            "worktree-apply",
            returncode=None,
            stderr=str(error),
        ) from error


def _has_uncommitted_target_changes(repo: Repo, index: Index) -> bool:
    if any(True for _ in get_unstaged_changes(index, repo.path, None, False)):
        return True
    if any(
        value
        for value in (
            untracked_paths(repo, index)
        )
    ):
        return True
    for value in index.iteritems():
        if isinstance(value[1], ConflictedIndexEntry):
            return True
    return False


def _write_index(
    repo: Repo,
    state: GitWorkingTreeState,
    working_state: GitWorkingTreeState,
) -> None:
    index = repo.open_index(config=ConfigFile())
    index.clear()
    working_entries = {entry.path: entry for entry in working_state.entries}
    for entry in state.entries:
        path = _safe_path(repo.path, entry.path)
        if entry.kind == "gitlink":
            if entry.object_id is None:
                raise ValueError("gitlink object id is missing")
            file_stat = path.lstat()
            value = index_entry_from_stat(
                file_stat,
                entry.object_id.encode("ascii"),
                mode=entry.mode,
            )
            if not _state_entries_match(entry, working_entries.get(entry.path)):
                value = _invalidate_index_stat(value)
            index[os.fsencode(entry.path)] = value
            continue
        content = _decode_content(entry)
        blob = Blob.from_string(content)
        repo.object_store.add_object(blob)
        if path.exists() or path.is_symlink():
            file_stat = path.lstat()
            value = index_entry_from_stat(file_stat, blob.id, mode=entry.mode)
            value = replace(value, size=len(content))
        else:
            value = IndexEntry(
                ctime=(0, 0),
                mtime=(0, 0),
                dev=0,
                ino=0,
                mode=entry.mode,
                uid=0,
                gid=0,
                size=0,
                sha=blob.id,
                flags=0,
            )
        if not _state_entries_match(entry, working_entries.get(entry.path)):
            value = _invalidate_index_stat(value)
        index[os.fsencode(entry.path)] = value
    index.write()


def _state_entries_match(
    index_entry: GitWorktreeStateEntry,
    working_entry: GitWorktreeStateEntry | None,
) -> bool:
    if working_entry is None:
        return False
    return (
        index_entry.kind == working_entry.kind
        and index_entry.mode == working_entry.mode
        and index_entry.content_base64 == working_entry.content_base64
        and index_entry.object_id == working_entry.object_id
    )


def _invalidate_index_stat(value: IndexEntry) -> IndexEntry:
    return replace(
        value,
        ctime=(0, 0),
        mtime=(0, 0),
        dev=0,
        ino=0,
        size=0,
    )


def _materialize_entry(root: str, entry: GitWorktreeStateEntry) -> None:
    path = _safe_path(Path(root), entry.path)
    if entry.kind == "gitlink":
        if not path.is_dir() or path.is_symlink():
            raise ValueError(f"gitlink target is not a directory: {entry.path}")
        return
    parent = _ensure_parent(Path(root), entry.path)
    if entry.kind == "symlink":
        target = os.fsdecode(_decode_content(entry))
        _validate_symlink_target(Path(root), path, target)
        temporary = Path(tempfile.mkdtemp(prefix=".eidos-link-", dir=parent))
        temporary_link = temporary / "link"
        try:
            os.symlink(target, temporary_link)
            os.replace(temporary_link, path)
        finally:
            shutil.rmtree(temporary, ignore_errors=True)
        return
    content = _decode_content(entry)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=parent, prefix=".eidos-state-", delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.chmod(temporary_path, stat.S_IMODE(entry.mode))
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink(missing_ok=True)


def _remove_tracked_entry(root: str, relative: str) -> None:
    path = _safe_path(Path(root), relative)
    try:
        value = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISDIR(value.st_mode):
        raise ValueError(f"refusing to remove directory tracked as file: {relative}")
    path.unlink()


def _validate_symlink_target(root: Path, path: Path, target: str) -> None:
    if Path(target).is_absolute():
        raise ValueError("symlink target is absolute")
    resolved = (path.parent / target).resolve(strict=False)
    if not resolved.is_relative_to(root.resolve(strict=True)):
        raise ValueError("symlink target escapes repository")
    if _contains_git_segment(resolved.relative_to(root.resolve(strict=True)).as_posix()):
        raise ValueError("symlink target enters Git metadata")


def _ensure_parent(root: Path, relative: str) -> Path:
    parts = Path(relative).parts
    current = root
    for part in parts[:-1]:
        current = current / part
        if current.exists() and (current.is_symlink() or not current.is_dir()):
            raise ValueError(f"unsafe target parent: {relative}")
        current.mkdir(exist_ok=True)
    return current


def _safe_path(root: Path, relative: str) -> Path:
    root = Path(root)
    path = Path(relative)
    if (
        not relative
        or path.is_absolute()
        or any(part in {"", ".", "..", ".git"} for part in path.parts)
        or "\x00" in relative
    ):
        raise ValueError("working-tree path is unsafe")
    result = root / path
    if not result.parent.resolve(strict=False).is_relative_to(root.resolve(strict=True)):
        raise ValueError("working-tree path escapes repository")
    return result


def _index_entries(index: Index) -> dict[str, object]:
    result: dict[str, object] = {}
    for path, value in index.iteritems():
        if isinstance(value, ConflictedIndexEntry):
            if value.this is None:
                raise GitCommandFailedError(
                    "worktree-capture",
                    returncode=None,
                    stderr=f"cannot capture conflicted index entry: {os.fsdecode(path)}",
                )
            value = value.this
        result[os.fsdecode(path)] = value
    return result


def _index_state_entry(repo: Repo, relative: str, value: object) -> GitWorktreeStateEntry:
    mode = int(getattr(value, "mode"))
    object_id = bytes(getattr(value, "sha"))
    if mode == _GITLINK_MODE:
        return GitWorktreeStateEntry(
            path=relative,
            kind="gitlink",
            mode=mode,
            object_id=_object_id(object_id),
        )
    blob = repo.object_store[object_id]
    if not isinstance(blob, Blob):
        raise ValueError(f"index entry is not a blob: {relative}")
    kind = "symlink" if stat.S_IFMT(mode) == _SYMLINK_MODE else "file"
    return GitWorktreeStateEntry(
        path=relative,
        kind=kind,
        mode=mode,
        content_base64=base64.b64encode(blob.data).decode("ascii"),
        object_id=_object_id(object_id),
    )


def _working_tree_entry(
    repo: Repo,
    relative: str,
    expected: object | None,
) -> GitWorktreeStateEntry | None:
    path = _safe_path(Path(repo.path), relative)
    try:
        value = path.lstat()
    except FileNotFoundError:
        return None
    expected_mode = int(getattr(expected, "mode", 0)) if expected is not None else 0
    if expected_mode == _GITLINK_MODE:
        if not stat.S_ISDIR(value.st_mode) or path.is_symlink():
            return None
        try:
            nested = Repo.discover(path)
            if Path(nested.path).resolve(strict=True) != path.resolve(strict=True):
                return None
            return GitWorktreeStateEntry(
                path=relative,
                kind="gitlink",
                mode=_GITLINK_MODE,
                object_id=_object_id(nested.head()),
            )
        except (NotADirectoryError, OSError, ValueError):
            return None
    if stat.S_ISLNK(value.st_mode):
        return GitWorktreeStateEntry(
            path=relative,
            kind="symlink",
            mode=_SYMLINK_MODE,
            content_base64=base64.b64encode(os.fsencode(os.readlink(path))).decode(
                "ascii"
            ),
        )
    if not stat.S_ISREG(value.st_mode):
        raise ValueError(f"unsupported working-tree entry: {relative}")
    mode = 0o100755 if value.st_mode & stat.S_IXUSR else 0o100644
    return GitWorktreeStateEntry(
        path=relative,
        kind="file",
        mode=mode,
        content_base64=base64.b64encode(path.read_bytes()).decode("ascii"),
    )


def _tree_entries(repo: Repo, tree_id: bytes) -> tuple[object, ...]:
    from dulwich.diff import iter_tree_contents

    return tuple(iter_tree_contents(repo.object_store, tree_id))


def untracked_paths(repo: Repo, index: Index) -> tuple[str, ...]:
    """Use Dulwich discovery and supplement its symlink traversal gap."""

    paths = {
        os.fsdecode(path)
        for path in get_untracked_paths(
            repo.path,
            repo.path,
            index,
            exclude_ignored=True,
            untracked_files="all",
            repo=repo,
        )
        if not _contains_git_segment(os.fsdecode(path))
    }
    ignore_manager = IgnoreFilterManager.from_repo(repo, config=repo.get_config_stack())
    root = Path(repo.path)
    gitlink_directories = {
        os.fsdecode(path)
        for path, value in index.iteritems()
        if not isinstance(value, ConflictedIndexEntry)
        and int(getattr(value, "mode", 0)) == _GITLINK_MODE
    }
    for current, directories, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        symlink_directories = [
            name for name in directories if (current_path / name).is_symlink()
        ]
        directories[:] = [
            name
            for name in directories
            if name not in symlink_directories
            and name != ".git"
            and current_path.joinpath(name).relative_to(root).as_posix()
            not in gitlink_directories
        ]
        for name in [*files, *symlink_directories]:
            candidate = current_path / name
            relative = candidate.relative_to(root).as_posix()
            if _contains_git_segment(relative):
                continue
            if os.fsencode(relative) in index:
                continue
            if ignore_manager.is_ignored(relative) is not True:
                paths.add(relative)
    return tuple(sorted(paths))


def _contains_git_segment(relative: str) -> bool:
    return any(part == ".git" for part in relative.split("/"))


def _render_full_patch(repo: Repo, head: str, untracked: Iterable[str]) -> str:
    output = _BoundedBytesIO(MAX_CAPTURE_PATCH_BYTES)
    try:
        diff_working_tree_to_tree(
            repo,
            output,
            head.encode("ascii"),
            config=ConfigFile(),
        )
        index = repo.open_index(config=ConfigFile())
        for relative in untracked:
            path = _safe_path(Path(repo.path), relative)
            value = path.lstat()
            if stat.S_ISLNK(value.st_mode):
                blob = Blob.from_string(os.fsencode(os.readlink(path)))
                mode = _SYMLINK_MODE
            elif stat.S_ISREG(value.st_mode):
                blob = Blob.from_string(path.read_bytes())
                mode = 0o100755 if value.st_mode & stat.S_IXUSR else 0o100644
            else:
                continue
            if os.fsencode(relative) not in index:
                write_blob_diff(output, (None, None, None), (os.fsencode(relative), mode, blob))
    except _PatchLimitReached as error:
        raise GitCommandFailedError(
            "worktree-capture",
            returncode=None,
            stderr="worktree patch exceeds the size limit",
        ) from error
    return output.getvalue().decode("utf-8", errors="replace")


def _render_staged_patch(repo: Repo, head: str) -> str:
    output = _BoundedBytesIO(MAX_CAPTURE_PATCH_BYTES)
    try:
        diff_index_to_tree(repo, output, head.encode("ascii"), config=ConfigFile())
    except _PatchLimitReached as error:
        raise GitCommandFailedError(
            "worktree-capture",
            returncode=None,
            stderr="worktree patch exceeds the size limit",
        ) from error
    return output.getvalue().decode("utf-8", errors="replace")


class _PatchLimitReached(RuntimeError):
    pass


class _BoundedBytesIO(io.BytesIO):
    def __init__(self, limit: int) -> None:
        super().__init__()
        self.limit = limit

    def write(self, value: bytes) -> int:
        if self.tell() + len(value) > self.limit:
            raise _PatchLimitReached
        return super().write(value)

    def writelines(self, lines: Iterable[bytes]) -> None:
        for line in lines:
            self.write(line)


def _decode_content(entry: GitWorktreeStateEntry) -> bytes:
    if entry.content_base64 is None:
        raise ValueError(f"entry content is missing: {entry.path}")
    return base64.b64decode(entry.content_base64, validate=True)


def _object_id(value: object) -> str:
    raw = bytes(getattr(value, "id", value))
    try:
        return raw.decode("ascii")
    except UnicodeDecodeError:
        return raw.hex()


__all__ = ["apply_worktree_changes", "capture_worktree_changes", "untracked_paths"]

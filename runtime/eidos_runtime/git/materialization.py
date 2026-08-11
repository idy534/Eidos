from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat
import tempfile
from collections.abc import Collection

from pathspec import PathSpec
from pathspec.patterns.gitwildmatch import GitIgnoreSpecPattern

from eidos_runtime.git.errors import WorktreeError


INCLUDE_FILENAME = ".worktreeinclude"
MAX_INCLUDE_SPEC_BYTES = 64 * 1024


def materialize_worktree_include(
    source_root: Path,
    worktree_root: Path,
    *,
    exclude_paths: Collection[str] = (),
) -> tuple[str, ...]:
    """Copy explicitly included local files into one managed Worktree.

    The source file is the only authority.  The target copy is never read as
    an include specification.  Symlinks are copied as symlinks only after
    their targets have been proven to stay inside the source repository.
    """

    source = _canonical_directory(source_root, "worktree_include_source_invalid")
    target = _canonical_directory(worktree_root, "worktree_include_target_invalid")
    if _paths_overlap(source, target):
        raise WorktreeError("worktree_include_target_invalid")
    include_file = source / INCLUDE_FILENAME
    if not include_file.is_file() or include_file.is_symlink():
        return ()
    try:
        if include_file.stat().st_size > MAX_INCLUDE_SPEC_BYTES:
            raise WorktreeError("worktree_include_invalid")
        lines = include_file.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise WorktreeError("worktree_include_invalid") from error

    patterns: list[str] = []
    for line in lines:
        pattern = line.strip()
        if not pattern or pattern.startswith("#"):
            continue
        _validate_pattern(pattern)
        patterns.append(pattern)
    try:
        spec = PathSpec.from_lines(GitIgnoreSpecPattern, patterns)
    except (TypeError, ValueError) as error:
        raise WorktreeError("worktree_include_invalid") from error

    copied: list[str] = []
    for root, directories, files in os.walk(source, topdown=True, followlinks=False):
        root_path = Path(root)
        directories[:] = sorted(
            name for name in directories if name != ".git"
        )
        files = sorted(files)

        symlink_directories = [
            name
            for name in directories
            if (root_path / name).is_symlink()
        ]
        directories[:] = [
            name for name in directories if name not in symlink_directories
        ]
        entries = [*files, *symlink_directories]
        for name in entries:
            source_path = root_path / name
            relative = source_path.relative_to(source).as_posix()
            if (
                _contains_git_segment(relative)
                or relative in exclude_paths
                or not spec.match_file(relative)
            ):
                continue
            _copy_entry(source, target, source_path, relative)
            copied.append(relative)
    return tuple(copied)


def _canonical_directory(path: Path, code: str) -> Path:
    try:
        if path.is_symlink() or not path.is_dir():
            raise WorktreeError(code)
        resolved = path.resolve(strict=True)
    except WorktreeError:
        raise
    except OSError as error:
        raise WorktreeError(code) from error
    return resolved


def _validate_pattern(pattern: str) -> None:
    if (
        pattern.startswith("/")
        or pattern.startswith("!")
        or "\x00" in pattern
        or any(part == ".." for part in pattern.replace("\\", "/").split("/"))
        or _contains_git_segment(pattern.replace("\\", "/"))
    ):
        raise WorktreeError("worktree_include_invalid")


def _contains_git_segment(relative: str) -> bool:
    return any(part == ".git" for part in relative.split("/"))


def _copy_entry(
    source_root: Path,
    target_root: Path,
    source_path: Path,
    relative: str,
) -> None:
    try:
        source_stat = source_path.lstat()
    except OSError as error:
        raise WorktreeError("worktree_include_invalid") from error

    if stat.S_ISLNK(source_stat.st_mode):
        try:
            resolved_target = source_path.resolve(strict=True)
        except OSError as error:
            raise WorktreeError("worktree_include_symlink_escape") from error
        if not _inside(resolved_target, source_root) or _contains_git_segment(
            resolved_target.relative_to(source_root).as_posix()
        ):
            raise WorktreeError("worktree_include_symlink_escape")
        _ensure_target_parent(target_root, relative)
        target_path = target_root / relative
        _replace_symlink(source_path, target_path)
        return

    if not stat.S_ISREG(source_stat.st_mode):
        raise WorktreeError("worktree_include_invalid")
    _ensure_target_parent(target_root, relative)
    target_path = target_root / relative
    try:
        if target_path.exists() and target_path.is_dir():
            raise WorktreeError("worktree_include_invalid")
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=target_path.parent, prefix=".eidos-include-", delete=False
        ) as temporary:
            temporary_path = Path(temporary.name)
            with source_path.open("rb") as source_file:
                shutil.copyfileobj(source_file, temporary, length=1024 * 1024)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.chmod(temporary_path, stat.S_IMODE(source_stat.st_mode))
        os.replace(temporary_path, target_path)
    except WorktreeError:
        raise
    except OSError as error:
        raise WorktreeError("worktree_include_copy_failed") from error
    finally:
        if "temporary_path" in locals() and temporary_path.exists():
            try:
                temporary_path.unlink()
            except OSError:
                pass


def _ensure_target_parent(target_root: Path, relative: str) -> None:
    parent = target_root / Path(relative).parent
    try:
        current = target_root
        for part in Path(relative).parent.parts:
            current = current / part
            if current.exists() and (current.is_symlink() or not current.is_dir()):
                raise WorktreeError("worktree_include_target_invalid")
            current.mkdir(exist_ok=True)
        if not _inside(parent.resolve(strict=True), target_root):
            raise WorktreeError("worktree_include_target_invalid")
    except WorktreeError:
        raise
    except OSError as error:
        raise WorktreeError("worktree_include_target_invalid") from error


def _replace_symlink(source_path: Path, target_path: Path) -> None:
    temporary_path: Path | None = None
    try:
        if target_path.exists() and target_path.is_dir():
            raise WorktreeError("worktree_include_invalid")
        link_target = os.readlink(source_path)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".eidos-include-link-", dir=target_path.parent
        )
        os.close(descriptor)
        temporary_path = Path(temporary_name)
        temporary_path.unlink()
        os.symlink(link_target, temporary_path)
        os.replace(temporary_path, target_path)
    except WorktreeError:
        raise
    except OSError as error:
        raise WorktreeError("worktree_include_copy_failed") from error
    finally:
        if temporary_path is not None and temporary_path.is_symlink():
            try:
                temporary_path.unlink()
            except OSError:
                pass


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _paths_overlap(left: Path, right: Path) -> bool:
    return _inside(left, right) or _inside(right, left)


__all__ = ["INCLUDE_FILENAME", "materialize_worktree_include"]

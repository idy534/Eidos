"""Commit a prepared file effect with a verified, pinned NTFS handle."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat

from eidos_runtime.file_limits import MAX_FILE_BYTES
from eidos_runtime.infrastructure import windows_files


def commit(
    root_fd: int, workspace: Path, source: Path, target: Path, expected: str | None,
    *, delete: bool, candidate: str | None, content: bytes | None,
) -> str:
    dirty = False
    # Publishing a new file renames it into the target directory, which needs
    # write sharing on every pin of that directory held during the rename.
    publish_parent = target.parent if expected is None else None
    try:
        with windows_files.named_directory(root_fd, rename_target=publish_parent) as root:
            if root != workspace or not windows_files.owner_matches(root_fd):
                return "failed"
            if expected is None:
                if delete or source == target or not candidate:
                    return "failed"
                with windows_files.commit_handle(
                    source, write=True, delete_access=True, rename_target=publish_parent,
                ) as fd:
                    if _digest(fd) != candidate:
                        return "conflict"
                    windows_files.publish_open_file(fd, target)
                    dirty = True
                    os.fsync(fd)
                return "committed"
            with windows_files.commit_handle(target, write=not delete, delete_access=delete) as fd:
                if _digest(fd) != expected:
                    return "conflict"
                if delete:
                    windows_files.delete_open_file(fd)
                    dirty = True
                else:
                    if content is None or len(content) > MAX_FILE_BYTES or hashlib.sha256(content).hexdigest() != candidate:
                        return "failed"
                    dirty = True  # A partial write is possible from this point.
                    os.ftruncate(fd, 0)
                    os.lseek(fd, 0, os.SEEK_SET)
                    rest = memoryview(content)
                    while rest:
                        written = os.write(fd, rest)
                        if written <= 0:
                            return "uncertain"
                        rest = rest[written:]
                    os.fsync(fd)
                    if _digest(fd) != candidate:
                        return "uncertain"
            if delete:
                try:
                    target.lstat()
                except FileNotFoundError:
                    return "committed"
                return "uncertain"
            return "committed"
    except (FileExistsError, FileNotFoundError):
        return "uncertain" if dirty else "conflict"
    except OSError:
        return "uncertain" if dirty else "failed"


def _digest(fd: int) -> str:
    before = os.fstat(fd)
    if (
        not stat.S_ISREG(before.st_mode) or before.st_size > MAX_FILE_BYTES
        or before.st_nlink != 1 or not windows_files.owner_matches(fd)
    ):
        raise PermissionError("unsupported NTFS target")
    digest = hashlib.sha256()
    os.lseek(fd, 0, os.SEEK_SET)
    remaining = before.st_size
    while remaining:
        chunk = os.read(fd, min(65536, remaining))
        if not chunk:
            raise OSError("file changed while reading")
        digest.update(chunk)
        remaining -= len(chunk)
    after = os.fstat(fd)
    if (before.st_size, before.st_mtime_ns, before.st_nlink) != (after.st_size, after.st_mtime_ns, after.st_nlink):
        raise OSError("file changed while reading")
    return digest.hexdigest()

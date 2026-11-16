"""Host filesystem operations that preserve the Runtime's path checks."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import errno
import os
from pathlib import Path
import stat
import sys


WINDOWS = sys.platform == "win32"

if WINDOWS:
    from eidos_runtime.infrastructure import windows_files


def valid_relative_parts(parts: tuple[str, ...]) -> bool:
    return not WINDOWS or all(windows_files.valid_name(part) for part in parts)


def open_directory(path: str | Path, *, dir_fd: int | None = None) -> int:
    if WINDOWS:
        return windows_files.open_directory(path, dir_fd=dir_fd)
    return os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dir_fd)


def open_file(
    path: str | Path, flags: int = os.O_RDONLY, mode: int = 0o600, *, dir_fd: int | None = None,
) -> int:
    if WINDOWS:
        return windows_files.open_file(path, flags, dir_fd=dir_fd)
    return os.open(path, flags | os.O_NOFOLLOW | os.O_NONBLOCK, mode, dir_fd=dir_fd)


def descriptor_path(descriptor: int) -> Path:
    if WINDOWS:
        return windows_files.descriptor_path(descriptor)
    import fcntl

    raw = fcntl.fcntl(descriptor, fcntl.F_GETPATH, bytes(1024))
    value = raw.split(bytes(1), 1)[0].decode("utf-8", errors="strict")
    if not value:
        raise OSError("file descriptor has no path")
    return Path(value)


def is_link(metadata: os.stat_result) -> bool:
    return stat.S_ISLNK(metadata.st_mode) or (
        WINDOWS and bool(metadata.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)
    )


def owned_by_user(path: Path | int, metadata: os.stat_result) -> bool:
    if WINDOWS:
        return windows_files.owner_matches(path)
    return metadata.st_uid == os.getuid()


def has_private_access(path: Path | int, metadata: os.stat_result, mode: int | None = None) -> bool:
    if WINDOWS:
        return not is_link(metadata) and windows_files.owner_matches(path, private=True)
    current_mode = stat.S_IMODE(metadata.st_mode)
    return metadata.st_uid == os.getuid() and (
        not current_mode & 0o077 if mode is None else current_mode == mode
    )


def trusted_asset(path: Path, metadata: os.stat_result) -> bool:
    if WINDOWS:
        return not is_link(metadata) and windows_files.owner_matches(path, asset=True)
    return metadata.st_uid == os.getuid() and not stat.S_IMODE(metadata.st_mode) & 0o022


def lock_exclusive(descriptor: int) -> None:
    if WINDOWS:
        import msvcrt

        try:
            msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
        except OSError as error:
            if error.errno in {errno.EACCES, errno.EDEADLK}:
                raise BlockingIOError(errno.EAGAIN, "data directory is locked") from error
            raise
    else:
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)


def set_file_mode(descriptor: int, mode: int) -> None:
    if not WINDOWS:
        os.fchmod(descriptor, mode)


def sync_directory(path: Path | int) -> None:
    if WINDOWS:
        # Windows does not support flushing a directory with the CRT. Durable
        # replacement uses MoveFileExW with WRITE_THROUGH on this platform.
        return
    if isinstance(path, int):
        os.fsync(path)
        return
    descriptor = open_directory(path)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def replace(source: Path, target: Path) -> None:
    if WINDOWS:
        windows_files.replace(source, target)
    else:
        os.replace(source, target)


def lstat_at(name: str, descriptor: int) -> os.stat_result:
    if WINDOWS:
        return windows_files.lstat_at(name, descriptor)
    return os.stat(name, dir_fd=descriptor, follow_symlinks=False)


def mkdir_at(name: str, descriptor: int, mode: int) -> None:
    if WINDOWS:
        windows_files.mkdir_at(name, descriptor, mode)
    else:
        os.mkdir(name, mode=mode, dir_fd=descriptor)


def rmdir_at(name: str, descriptor: int) -> None:
    if WINDOWS:
        windows_files.rmdir_at(name, descriptor)
    else:
        os.rmdir(name, dir_fd=descriptor)


def unlink_at(name: str, descriptor: int) -> None:
    if WINDOWS:
        windows_files.unlink_at(name, descriptor)
    else:
        os.unlink(name, dir_fd=descriptor)


def replace_at(source: str, target: str, descriptor: int) -> None:
    if WINDOWS:
        if not valid_relative_parts((source, target)):
            raise PermissionError("invalid Windows file name")
        with windows_files.named_directory(descriptor) as directory:
            replace(directory / source, directory / target)
    else:
        os.replace(source, target, src_dir_fd=descriptor, dst_dir_fd=descriptor)


@contextmanager
def scan_directory(descriptor: int) -> Iterator[Iterator[os.DirEntry]]:
    if WINDOWS:
        with windows_files.named_directory(descriptor) as directory, os.scandir(directory) as entries:
            yield entries
    else:
        with os.scandir(descriptor) as entries:
            yield entries

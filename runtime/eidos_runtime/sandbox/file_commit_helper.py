from __future__ import annotations

import ctypes
import errno
import hashlib
import os
from pathlib import Path
import stat
import sys


RENAME_EXCL = 0x00000004
EXIT_CONFLICT = 10
EXIT_UNCERTAIN = 11
EXIT_FAILED = 12
MAX_FILE_BYTES = 16 * 1024 * 1024


def main() -> int:
    if len(sys.argv) not in {4, 5, 6}:
        return EXIT_FAILED
    source = None if sys.argv[1] == "-" else Path(sys.argv[1])
    target = Path(sys.argv[2])
    expected = sys.argv[3]
    if (source is not None and not source.is_absolute()) or not target.is_absolute():
        return EXIT_FAILED
    if len(sys.argv) == 5 and sys.argv[4] != "delete":
        return EXIT_FAILED
    if len(sys.argv) == 5:
        return _delete_existing(target, expected)
    candidate_hash = None
    if len(sys.argv) == 6:
        if sys.argv[4] != "write":
            return EXIT_FAILED
        candidate_hash = sys.argv[5]
    if expected == "new":
        if source is None:
            return EXIT_FAILED
        try:
            return 0 if _rename(source, target, RENAME_EXCL) else EXIT_FAILED
        except OSError as error:
            _report_error("create", error)
            return EXIT_CONFLICT if error.errno in {errno.EEXIST, errno.ENOTEMPTY} else EXIT_FAILED
    if len(expected) != 64 or any(character not in "0123456789abcdef" for character in expected):
        return EXIT_FAILED
    return _write_existing(source, target, expected, candidate_hash)


def _open_parent(path: Path) -> int:
    root = os.environ.get("EIDOS_FILE_ROOT")
    inherited_fd = os.environ.get("EIDOS_FILE_ROOT_FD")
    if root is not None and inherited_fd is not None:
        relative = path.relative_to(root)
        if ".." in relative.parts or not relative.parts:
            raise OSError(errno.EINVAL, "invalid relative target")
        descriptor = os.dup(int(inherited_fd))
        parts = relative.parts[:-1]
    elif root is not None or inherited_fd is not None:
        raise OSError(errno.EINVAL, "incomplete file root")
    else:
        # Legacy direct helper callers do not inherit a Runtime directory fd.
        descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        parts = path.parts[1:-1]
    try:
        if root is not None:
            opened = os.fstat(descriptor)
            current = os.stat(root, follow_symlinks=False)
            if not stat.S_ISDIR(opened.st_mode) or opened.st_uid != os.getuid() or (
                opened.st_dev, opened.st_ino
            ) != (current.st_dev, current.st_ino):
                raise OSError(errno.ESTALE, "file root changed")
        for part in parts:
            next_fd = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = next_fd
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _report_error(stage: str, error: OSError) -> None:
    # Keep diagnostics bounded and free of paths, contents and exception text.
    print(f"file_commit stage={stage} errno={error.errno}", file=sys.stderr)


def _delete_existing(target: Path, expected: str) -> int:
    parent_fd = descriptor = -1
    mutation_started = False
    try:
        parent_fd = _open_parent(target)
        descriptor = os.open(
            target.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=parent_fd,
        )
        if hashlib.sha256(_checked_contents(descriptor)).hexdigest() != expected:
            return EXIT_CONFLICT
        before = os.fstat(descriptor)
        current = os.stat(target.name, dir_fd=parent_fd, follow_symlinks=False)
        if (before.st_dev, before.st_ino) != (current.st_dev, current.st_ino):
            return EXIT_CONFLICT
        mutation_started = True
        os.unlink(target.name, dir_fd=parent_fd)
        os.fsync(parent_fd)
        return 0
    except OSError as error:
        _report_error("delete_after_mutation" if mutation_started else "delete_prepare", error)
        return EXIT_UNCERTAIN if mutation_started else EXIT_FAILED
    finally:
        for opened in (descriptor, parent_fd):
            if opened >= 0:
                os.close(opened)


def _checked_contents(descriptor: int) -> bytes:
    before = os.fstat(descriptor)
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_size > MAX_FILE_BYTES
        or before.st_nlink != 1
        or before.st_uid != os.getuid()
        or stat.S_IMODE(before.st_mode) & 0o7000
        or getattr(before, "st_flags", 0)
    ):
        raise OSError("unsupported file")
    chunks = []
    remaining = MAX_FILE_BYTES + 1
    while remaining:
        chunk = os.read(descriptor, min(remaining, 64 * 1024))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    after = os.fstat(descriptor)
    if not remaining or sum(map(len, chunks)) != before.st_size or (
        before.st_size, before.st_mtime_ns, before.st_ctime_ns,
        before.st_nlink, before.st_mode, before.st_uid,
    ) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns,
          after.st_nlink, after.st_mode, after.st_uid):
        raise OSError("file changed during read")
    return b"".join(chunks)


def _write_existing(
    source: Path | None, target: Path, expected: str,
    candidate_hash: str | None = None,
) -> int:
    parent_fd = source_parent_fd = descriptor = candidate_fd = -1
    mutation_started = False
    try:
        if source is None:
            contents = sys.stdin.buffer.read(MAX_FILE_BYTES + 1)
            if len(contents) > MAX_FILE_BYTES:
                return EXIT_FAILED
        else:
            source_parent_fd = _open_parent(source)
            candidate_fd = os.open(
                source.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                dir_fd=source_parent_fd,
            )
            contents = _checked_contents(candidate_fd)
        if candidate_hash is not None and hashlib.sha256(contents).hexdigest() != candidate_hash:
            return EXIT_FAILED
        parent_fd = _open_parent(target)
        descriptor = os.open(
            target.name, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=parent_fd,
        )
        original = _checked_contents(descriptor)
        if hashlib.sha256(original).hexdigest() != expected:
            return EXIT_CONFLICT
        identity = os.fstat(descriptor)
        current = os.stat(target.name, dir_fd=parent_fd, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino):
            return EXIT_CONFLICT
        # No cancellation point after this boundary: finish this bounded file.
        mutation_started = True
        os.ftruncate(descriptor, 0)
        os.lseek(descriptor, 0, os.SEEK_SET)
        offset = 0
        while offset < len(contents):
            written = os.write(descriptor, contents[offset:])
            if written <= 0:
                raise OSError("short write")
            offset += written
        os.fsync(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        if _checked_contents(descriptor) != contents:
            return EXIT_UNCERTAIN
        current = os.stat(target, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (identity.st_dev, identity.st_ino):
            return EXIT_UNCERTAIN
        return 0
    except OSError as error:
        _report_error("write_after_mutation" if mutation_started else "write_prepare", error)
        return EXIT_UNCERTAIN if mutation_started else EXIT_FAILED
    finally:
        for opened in (descriptor, candidate_fd, parent_fd, source_parent_fd):
            if opened >= 0:
                os.close(opened)


def _rename(source: Path, target: Path, flags: int) -> bool:
    libc = ctypes.CDLL(None, use_errno=True)
    renameatx_np = libc.renameatx_np
    renameatx_np.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameatx_np.restype = ctypes.c_int
    source_parent = target_parent = -1
    try:
        source_parent = _open_parent(source)
        target_parent = _open_parent(target)
        result = renameatx_np(
            source_parent, os.fsencode(source.name),
            target_parent, os.fsencode(target.name), flags,
        )
        if result != 0:
            raise OSError(ctypes.get_errno(), "exclusive rename failed")
        return True
    finally:
        for opened in (source_parent, target_parent):
            if opened >= 0:
                try:
                    os.close(opened)
                except OSError as error:
                    # Closing a directory fd does not undo a completed rename.
                    _report_error("create_cleanup", error)


if __name__ == "__main__":
    raise SystemExit(main())

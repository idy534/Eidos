from __future__ import annotations

import ctypes
import hashlib
import os
from pathlib import Path
import stat
import sys


AT_FDCWD = -2
RENAME_SWAP = 0x00000002
RENAME_EXCL = 0x00000004
EXIT_CONFLICT = 10
EXIT_UNCERTAIN = 11
EXIT_FAILED = 12
MAX_FILE_BYTES = 256 * 1024


def main() -> int:
    if len(sys.argv) != 4:
        return EXIT_FAILED
    source = Path(sys.argv[1])
    target = Path(sys.argv[2])
    expected = sys.argv[3]
    if not source.is_absolute() or not target.is_absolute():
        return EXIT_FAILED
    if expected == "new":
        return 0 if _rename(source, target, RENAME_EXCL) else EXIT_CONFLICT
    if len(expected) != 64 or any(character not in "0123456789abcdef" for character in expected):
        return EXIT_FAILED
    if not _rename(source, target, RENAME_SWAP):
        return EXIT_CONFLICT
    try:
        previous_hash = _regular_file_hash(source)
    except OSError:
        return EXIT_UNCERTAIN
    if previous_hash != expected:
        return EXIT_CONFLICT if _rename(source, target, RENAME_SWAP) else EXIT_UNCERTAIN
    try:
        source.unlink()
    except OSError:
        return EXIT_UNCERTAIN
    return 0


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
    result = renameatx_np(
        AT_FDCWD,
        os.fsencode(source),
        AT_FDCWD,
        os.fsencode(target),
        flags,
    )
    return result == 0


def _regular_file_hash(path: Path) -> str:
    flags = os.O_RDONLY | os.O_NONBLOCK
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_FILE_BYTES:
            raise OSError("unsupported prior target")
        digest = hashlib.sha256()
        remaining = MAX_FILE_BYTES + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            digest.update(chunk)
            remaining -= len(chunk)
        if remaining <= 0:
            raise OSError("prior target too large")
        return digest.hexdigest()
    finally:
        os.close(descriptor)


if __name__ == "__main__":
    raise SystemExit(main())

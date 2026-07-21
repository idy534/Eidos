from __future__ import annotations

import os
from pathlib import Path
import sys


def main() -> None:
    if len(sys.argv) < 4:
        raise SystemExit(64)
    pid_path = Path(sys.argv[1])
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(pid_path, flags, 0o600)
    try:
        os.write(descriptor, str(os.getpid()).encode("ascii"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.execvpe(sys.argv[2], sys.argv[2:], os.environ.copy())


if __name__ == "__main__":
    main()

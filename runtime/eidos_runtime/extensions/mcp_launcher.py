from __future__ import annotations

import os
from pathlib import Path
import sys


def main() -> None:
    if len(sys.argv) < 4:
        raise SystemExit(64)
    if sys.platform == "win32":
        # The MCP SDK owns this process. When it exits, the Job handle closes
        # and Windows ends every process started by this server.
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
        from eidos_runtime.infrastructure.windows_processes import JobProcess, process_owner

        with process_owner(int(sys.argv[2]), int(sys.argv[3])) as owner:
            child = JobProcess(
                sys.argv[4:], cwd=Path.cwd(), env=os.environ.copy(),
                stdin=None, stdout=None, stderr=None,
            )
            try:
                raise SystemExit(child.wait_for_owner_or_exit(owner))
            finally:
                child.close_job()
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

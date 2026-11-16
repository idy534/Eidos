"""Wait for the parent to assign a Job Object before running the command."""
import os
import subprocess
import sys
import msvcrt


if __name__ == "__main__":
    if len(sys.argv) < 3:
        raise SystemExit(1)
    handle = int(sys.argv[1])
    os.set_handle_inheritable(handle, False)
    gate = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    try:
        ready = os.read(gate, 1)
    finally:
        os.close(gate)
    if ready != b"\x01":
        raise SystemExit(1)
    try:
        child = subprocess.Popen(sys.argv[2:])
    except OSError as error:
        print(f"Process creation failed (Windows error {error.winerror})", file=sys.stderr)
        raise SystemExit(1) from None
    raise SystemExit(child.wait())

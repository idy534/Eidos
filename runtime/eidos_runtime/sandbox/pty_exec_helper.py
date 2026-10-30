"""Establish a controlling terminal in a fresh interpreter, then exec.

This helper imports only the standard library. The Runtime supplies the slave
and error descriptors explicitly; neither descriptor survives the final exec.
"""
from __future__ import annotations

import os
import sys


def main() -> None:
    slave_fd, ready_fd, start_fd = (int(value) for value in sys.argv[1:4])
    try:
        os.login_tty(slave_fd)
    except OSError as error:
        # Do not send command arguments, environment values or exception text.
        os.write(ready_fd, str(error.errno or 1).encode("ascii"))
        os._exit(127)
    os.write(ready_fd, b"\0")
    os.close(ready_fd)
    # The parent first registers ownership and records execution start.
    authorized = os.read(start_fd, 1) == b"g"
    os.close(start_fd)
    if not authorized:
        os._exit(125)
    try:
        os.execvpe(sys.argv[4], sys.argv[4:], os.environ)
    except OSError:
        os.write(2, b"Shell executable could not be started.\n")
        os._exit(127)


if __name__ == "__main__":
    main()

"""Find the user's everyday Chrome after they enable remote debugging themselves.

Chrome 144+ exposes remote debugging only after the user turns it on at
chrome://inspect/#remote-debugging, and asks the user to allow every
connection. Eidos reads the one DevToolsActivePort file Chrome writes; it
never changes the user's Chrome settings or launches that Chrome.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import socket
import stat

USER_CHROME_DATA = Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
_MAX_PORT_FILE_BYTES = 4096
_BROWSER_PATH = re.compile(r"^/devtools/browser/[A-Za-z0-9-]{1,64}$")


def user_chrome_endpoint(data_directory: Path = USER_CHROME_DATA) -> str | None:
    """Return the browser WebSocket URL when the user's Chrome is listening."""

    try:
        descriptor = os.open(data_directory / "DevToolsActivePort", os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
            return None
        lines = stream.read(_MAX_PORT_FILE_BYTES).decode("ascii", "replace").splitlines()
    if len(lines) < 2 or not lines[0].strip().isdigit() or not _BROWSER_PATH.fullmatch(lines[1].strip()):
        return None
    port = int(lines[0].strip())
    if not 0 < port < 65536 or not _listening(port):
        return None
    return f"ws://127.0.0.1:{port}{lines[1].strip()}"


def _listening(port: int) -> bool:
    # A TCP connect is enough; Chrome's consent prompt appears only for a
    # real DevTools session, which Playwright opens on the first tool call.
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False

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

from eidos_runtime.infrastructure import local_files


def chrome_user_data() -> Path | None:
    if local_files.WINDOWS:
        local = Path(os.environ.get("LOCALAPPDATA", ""))
        return local / "Google/Chrome/User Data" if local.is_absolute() else None
    return Path.home() / "Library/Application Support/Google/Chrome"


_MAX_PORT_FILE_BYTES = 4096
_BROWSER_PATH = re.compile(r"^/devtools/browser/[A-Za-z0-9-]{1,64}$")


def user_chrome_endpoint(data_directory: Path | None = None) -> str | None:
    """Return the browser WebSocket URL when the user's Chrome is listening."""

    data_directory = data_directory or chrome_user_data()
    if data_directory is None:
        return None
    try:
        descriptor = local_files.open_file(data_directory / "DevToolsActivePort")
    except OSError:
        return None
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or not local_files.owned_by_user(descriptor, metadata):
            return None
        raw = stream.read(_MAX_PORT_FILE_BYTES + 1)
    if len(raw) > _MAX_PORT_FILE_BYTES:
        return None
    lines = raw.decode("ascii", "replace").splitlines()
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

"""Reach the user's own Chrome through the official Playwright Extension.

Playwright MCP's extension mode runs a loopback WebSocket relay inside the
sandboxed MCP process and opens the extension's connect page in Chrome. The
sandbox can neither launch Chrome nor read the user's Chrome profile, so the
relay is pointed at a tiny helper that hands the connect-page URL to a broker
outside the sandbox. The broker accepts only that exact page with a loopback
relay address and opens it in the system Chrome.

The extension keeps the tabs of each connection in their own tab group; only
tabs in that group are reported to the relay. See
docs/design/shared-chrome-tab-group-design.md.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
import hmac
import json
import logging
import os
from pathlib import Path
import re
import subprocess
from typing import Literal
from urllib.parse import parse_qs, urlsplit

import anyio
from anyio.abc import ByteStream, Listener

from eidos_runtime.infrastructure import local_files
from eidos_runtime.infrastructure.user_chrome import chrome_user_data
from eidos_runtime.sandbox.host_shell import sanitize_shell_environment

LOGGER = logging.getLogger(__name__)

PLAYWRIGHT_EXTENSION_ID = "mmlmfjhmonkocbjadbfplnigmagldckm"
PLAYWRIGHT_EXTENSION_STORE_URL = (
    f"https://chromewebstore.google.com/detail/playwright-extension/{PLAYWRIGHT_EXTENSION_ID}"
)
CHROME_BUNDLE_ID = "com.google.Chrome"
TOKEN_ENVIRONMENT = "PLAYWRIGHT_MCP_EXTENSION_TOKEN"
BROKER_SOCKET_NAME = "connect-broker.sock"
CONNECT_HELPER_NAME = "open-connect-page"
WINDOWS_CONNECT_HELPER = "bin/open-connect-page.exe"
BROKER_PORT_ENV = "EIDOS_BROWSER_BROKER_PORT"
BROKER_TOKEN_ENV = "EIDOS_BROWSER_BROKER_TOKEN"
LOOPBACK_SHIM_NAME = "loopback-shim.cjs"

# The extension's status page copies "PLAYWRIGHT_MCP_EXTENSION_TOKEN=<token>";
# the token itself is 32 random bytes in URL-safe base64.
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{32,128}$")
_RELAY = re.compile(
    r"^ws://127\.0\.0\.1:\d{1,5}/extension/"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
_CONNECT_PARAMETERS = frozenset({"mcpRelayUrl", "client", "protocolVersion", "token"})
_MAX_CONNECT_URL = 4096
_MAX_PREFERENCES_BYTES = 16 * 1024 * 1024
_MAX_PROFILES = 64
# A misbehaving server cannot flood the user's Chrome with tabs.
MAX_CONNECT_PAGES_PER_CONNECTION = 8

ExtensionPresence = Literal["installed", "missing", "unknown"]


class ExtensionTokenInvalid(ValueError):
    def __init__(self) -> None:
        super().__init__("browser_extension_token_invalid")
        self.code = "browser_extension_token_invalid"


def normalize_extension_token(value: str) -> str:
    """Accept the bare token or the whole line the extension's copy button copies."""

    token = value.strip()
    if token.startswith("export "):
        token = token.removeprefix("export ").strip()
    token = token.removeprefix(TOKEN_ENVIRONMENT + "=").strip().strip("'\"")
    if not _TOKEN.fullmatch(token):
        raise ExtensionTokenInvalid()
    return token


def extension_presence(data_directory: Path | None = None) -> ExtensionPresence:
    """Whether any Chrome profile has the Playwright Extension.

    "unknown" means Chrome's profile files could not be read; the connection
    attempt then decides.
    """

    data_directory = data_directory or chrome_user_data()
    if data_directory is None:
        return "unknown"
    try:
        if not data_directory.is_dir():
            return "missing"
        profiles = [
            entry for entry in sorted(data_directory.iterdir())[:512]
            if entry.is_dir() and (entry.name == "Default" or entry.name.startswith("Profile "))
        ][:_MAX_PROFILES]
    except OSError:
        return "unknown"
    unreadable = False
    for profile in profiles:
        try:
            if (profile / "Extensions" / PLAYWRIGHT_EXTENSION_ID).is_dir():
                return "installed"
            for name in ("Preferences", "Secure Preferences"):
                path = profile / name
                if not path.is_file() or path.stat().st_size > _MAX_PREFERENCES_BYTES:
                    continue
                settings = json.loads(path.read_text("utf-8")).get("extensions", {}).get("settings", {})
                record = settings.get(PLAYWRIGHT_EXTENSION_ID) if isinstance(settings, dict) else None
                if isinstance(record, dict) and record:
                    return "installed"
        except (OSError, ValueError, AttributeError):
            unreadable = True
    return "unknown" if unreadable else "missing"


def connect_url_rejection(url: str) -> str | None:
    """Why a broker request is not the extension's connect page, or None."""

    if len(url) > _MAX_CONNECT_URL:
        return "too_long"
    try:
        parts = urlsplit(url)
        query = parse_qs(parts.query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        return "malformed"
    if (parts.scheme, parts.netloc, parts.path, parts.fragment) != (
        "chrome-extension", PLAYWRIGHT_EXTENSION_ID, "/connect.html", "",
    ):
        return "not_connect_page"
    if not set(query) <= _CONNECT_PARAMETERS or any(len(values) != 1 for values in query.values()):
        return "unexpected_parameters"
    if not _RELAY.fullmatch(query.get("mcpRelayUrl", [""])[0]):
        return "relay_not_loopback"
    return None


async def open_in_chrome(url: str) -> None:
    """Hand the page to the running Chrome, or start it."""

    if local_files.WINDOWS:
        from eidos_runtime.infrastructure.browser_host import find_chrome

        installation = find_chrome()
        if installation is None:
            raise FileNotFoundError("Chrome is unavailable")
        environment = sanitize_shell_environment(os.environ)
        environment.pop(TOKEN_ENVIRONMENT, None)
        # This is the user's Chrome, which continues to run after the broker exits.
        subprocess.Popen(
            [str(installation.executable), url], env=environment,
            creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
        )
        return
    with anyio.fail_after(10):
        await anyio.run_process(["/usr/bin/open", "-b", CHROME_BUNDLE_ID, url], check=False)


class ConnectPageBroker:
    """Outside the sandbox: open only the extension connect page, a bounded number of times."""

    def __init__(self, opener: Callable[[str], Awaitable[None]] = open_in_chrome, *, token: str | None = None) -> None:
        self.opener = opener
        self.opened = 0
        self._token = token.encode("ascii") + b" " if token else b""

    async def serve(self, listener: Listener[ByteStream]) -> None:
        limit = anyio.CapacityLimiter(8)

        async def handle(stream: ByteStream) -> None:
            try:
                limit.acquire_nowait()
            except anyio.WouldBlock:
                await stream.aclose()
                return
            try:
                await self._handle(stream)
            finally:
                limit.release()

        await listener.serve(handle)

    async def _handle(self, stream: ByteStream) -> None:
        async with stream:
            data = b""
            limit = _MAX_CONNECT_URL + len(self._token) + 1
            try:
                with anyio.fail_after(2):
                    while b"\n" not in data and len(data) < limit:
                        chunk = await stream.receive(limit - len(data))
                        if not chunk:
                            break
                        data += chunk
            except (TimeoutError, anyio.EndOfStream, anyio.BrokenResourceError, anyio.ClosedResourceError):
                pass
            if self._token:
                if not data.endswith(b"\n") or not hmac.compare_digest(data[:len(self._token)], self._token):
                    return
                data = data[len(self._token):]
            try:
                url = data.split(b"\n", 1)[0].decode("ascii").strip()
            except UnicodeDecodeError:
                url = ""
            # The URL carries the extension token; log only the reason.
            rejection = connect_url_rejection(url) if url else "empty"
            if rejection is None and self.opened >= MAX_CONNECT_PAGES_PER_CONNECTION:
                rejection = "limit_reached"
            if rejection is not None:
                LOGGER.warning("browser connect page request refused: %s", rejection)
                return
            self.opened += 1
            try:
                await self.opener(url)
            except (OSError, TimeoutError):
                LOGGER.warning("browser connect page could not be opened")


# Runs inside Seatbelt as Playwright's "browser executable". It launches
# nothing; it passes its last argument (the connect page URL) to the broker.
CONNECT_HELPER_SCRIPT = """#!/bin/sh
for last; do :; done
printf '%s\\n' "$last" | /usr/bin/nc -U "$TMPDIR/""" + BROKER_SOCKET_NAME + """"
"""

# Seatbelt blocks mDNSResponder, so even "localhost" (where the relay
# listens) cannot be resolved. Answer only that name; every other lookup keeps
# its normal path, which the sandbox still blocks.
LOOPBACK_SHIM_SCRIPT = """"use strict";
const dns = require("node:dns");
const lookup = dns.lookup;
dns.lookup = function eidosLoopbackLookup(hostname, options, callback) {
  if (typeof options === "function") { callback = options; options = {}; }
  if (typeof options === "number") options = { family: options };
  if (hostname === "localhost") {
    const entry = { address: "127.0.0.1", family: 4 };
    process.nextTick(() => options && options.all ? callback(null, [entry]) : callback(null, entry.address, entry.family));
    return {};
  }
  return lookup.call(this, hostname, options, callback);
};
"""

"""Eidos-owned risk classes for the bundled browser tools.

The table is an allowlist for a pinned Playwright MCP version. Tools that are
not listed, including new upstream tools, are never registered.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Literal

from eidos_runtime.models import EidosFrozenStrictModel


class BrowserToolClass(StrEnum):
    # Observes pages without changing them; runs without approval.
    READ = "read"
    # Changes page state or navigates; covered by the Session browser grant.
    INTERACT = "interact"
    # Sends a local file to a website; approved on every call.
    FILE = "file"


BROWSER_TOOL_CLASSES: dict[str, BrowserToolClass] = {
    "browser_snapshot": BrowserToolClass.READ,
    "browser_take_screenshot": BrowserToolClass.READ,
    "browser_find": BrowserToolClass.READ,
    "browser_console_messages": BrowserToolClass.READ,
    "browser_network_requests": BrowserToolClass.READ,
    "browser_network_request": BrowserToolClass.READ,
    "browser_wait_for": BrowserToolClass.READ,
    "browser_navigate": BrowserToolClass.INTERACT,
    "browser_navigate_back": BrowserToolClass.INTERACT,
    "browser_click": BrowserToolClass.INTERACT,
    "browser_type": BrowserToolClass.INTERACT,
    "browser_fill_form": BrowserToolClass.INTERACT,
    "browser_select_option": BrowserToolClass.INTERACT,
    "browser_press_key": BrowserToolClass.INTERACT,
    "browser_hover": BrowserToolClass.INTERACT,
    "browser_drag": BrowserToolClass.INTERACT,
    "browser_drop": BrowserToolClass.INTERACT,
    "browser_tabs": BrowserToolClass.INTERACT,
    "browser_handle_dialog": BrowserToolClass.INTERACT,
    "browser_resize": BrowserToolClass.INTERACT,
    "browser_close": BrowserToolClass.INTERACT,
    "browser_file_upload": BrowserToolClass.FILE,
}

# Listed for review only; anything outside BROWSER_TOOL_CLASSES is blocked.
# browser_run_code_unsafe runs arbitrary code in the server process and
# browser_evaluate runs arbitrary script inside signed-in pages.
KNOWN_BLOCKED_BROWSER_TOOLS = frozenset({
    "browser_run_code_unsafe", "browser_evaluate", "browser_emulate_media",
})


# Not registered while the tools drive the user's own Chrome: the full request
# and response headers and bodies carry session cookies and bearer tokens.
USER_CHROME_BLOCKED_BROWSER_TOOLS = frozenset({"browser_network_request"})


def browser_tool_class(remote_name: str) -> BrowserToolClass | None:
    return BROWSER_TOOL_CLASSES.get(remote_name)


BrowserGrantRevokeReason = Literal["user", "plugin_changed", "profile_cleared"]
# "default" is the Eidos Chrome profile; "user" is the user's everyday Chrome,
# whether reached through remote debugging or the Playwright Extension.
BrowserProfile = Literal["default", "user"]
# chrome_extension is recommended and the default: it drives the user's Chrome
# through the Playwright Extension once a token is saved, and otherwise falls
# back to the Eidos browser when a Run starts.
BrowserTarget = Literal["eidos", "user_chrome", "chrome_extension"]
DEFAULT_BROWSER_TARGET: BrowserTarget = "chrome_extension"


class BrowserGrant(EidosFrozenStrictModel):
    """Lets one Session operate the shared Eidos Chrome profile until revoked."""

    id: str
    session_id: str
    profile: BrowserProfile = "default"
    access: Literal["interact"] = "interact"
    plugin_id: str
    plugin_version: str
    plugin_hash: str
    source_approval_id: str | None = None
    status: Literal["active", "revoked"]
    created_at: int
    revoked_at: int | None = None
    revoke_reason: BrowserGrantRevokeReason | None = None

"""Eidos-owned risk classes for the bundled browser tools.

The table is an allowlist for a pinned Playwright MCP version. Tools that are
not listed, including new upstream tools, are never registered.
"""
from __future__ import annotations

from enum import StrEnum


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


def browser_tool_class(remote_name: str) -> BrowserToolClass | None:
    return BROWSER_TOOL_CLASSES.get(remote_name)

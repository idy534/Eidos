"""Identify the bundled Eidos browser plugin by its exact shipped content."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import threading
from typing import Literal

from eidos_runtime.infrastructure.browser_runtime import BrowserRuntime

BROWSER_PLUGIN_ID = "eidos-browser"
BROWSER_SERVER_ID = "browser"
BROWSER_PERMISSION_PROFILE = "browser"
BUNDLED_BROWSER_PLUGIN_ROOT = Path(__file__).resolve().parents[1] / "resources" / "plugins" / "browser"
# Launch variables only the bundled browser server may use. They resolve to
# verified managed resources and the Runtime-owned Chrome endpoint.
BROWSER_LAUNCH_VARIABLES = frozenset({
    "RUNTIME_NODE", "BROWSER_MCP_ENTRY", "BROWSER_CDP_ENDPOINT", "BROWSER_OUTPUT_DIR",
    "BROWSER_IMAGE_RESPONSES",
})


@lru_cache(maxsize=1)
def bundled_browser_plugin_hash() -> str | None:
    from eidos_runtime.extensions.plugins import PluginImportError, _content_hash, _read_source

    try:
        return _content_hash(_read_source(BUNDLED_BROWSER_PLUGIN_ROOT))
    except PluginImportError:
        return None


def is_bundled_browser_server(plugin_id: str, plugin_hash: str, server_id: str) -> bool:
    """A copied manifest or a same-named local package never gains browser access."""

    expected = bundled_browser_plugin_hash()
    return (
        expected is not None
        and plugin_id == BROWSER_PLUGIN_ID
        and server_id == BROWSER_SERVER_ID
        and plugin_hash == expected
    )


@dataclass(frozen=True)
class BrowserLaunch:
    """Verified inputs for one browser MCP connection, resolved by the Run owner."""

    runtime: BrowserRuntime
    variables: Mapping[str, str]
    # Starts or confirms the shared Chrome before a browser tool executes.
    ensure_browser: Callable[[threading.Event], str] | None = None
    # Cheap installation check so a missing Chrome fails before any approval.
    chrome_installed: Callable[[], bool] | None = None
    # "default" drives the Eidos profile; "user" is the user's everyday Chrome.
    profile: Literal["default", "user"] = "default"

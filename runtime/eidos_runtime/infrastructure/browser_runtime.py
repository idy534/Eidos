"""Locate the verified Node runtime and Playwright MCP entry from managed resources."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat

from eidos_runtime.infrastructure import local_files
from eidos_runtime.infrastructure.managed_resources import ManagedResourceManager, ResourceError
from eidos_runtime.infrastructure.runtime_dependencies import RuntimeDependencyCatalogError

BROWSER_PACK_ID = "browser"
NODE_PACK_ID = "node"
PLAYWRIGHT_MCP_ENTRY = "@playwright/mcp/cli.js"
CONNECT_HELPER_EXECUTABLE = "browser-connect"


class BrowserRuntimeUnavailable(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class BrowserRuntime:
    node: Path
    entry: Path
    read_roots: tuple[Path, ...]
    connect_helper: Path | None = None


def resolve_browser_runtime(manager: ManagedResourceManager | None) -> BrowserRuntime:
    """Return installed, verified paths without downloading anything.

    Run startup must not wait on the network. Preparation runs through the
    existing managed resource tasks; an unprepared pack reports a stable code.
    """

    if manager is None:
        raise BrowserRuntimeUnavailable("browser_runtime_unavailable")
    try:
        node_pack = manager.pack(NODE_PACK_ID)
        browser_pack = manager.pack(BROWSER_PACK_ID)
        manager.verified_catalog(NODE_PACK_ID)
        manager.verified_catalog(BROWSER_PACK_ID)
    except ResourceError as error:
        raise BrowserRuntimeUnavailable(
            "browser_runtime_unavailable" if error.code == "resource_unknown" else "browser_runtime_not_ready"
        ) from None
    except (OSError, RuntimeDependencyCatalogError):
        raise BrowserRuntimeUnavailable("browser_runtime_not_ready") from None
    executable = next((item for item in node_pack.manifest.executables if item.name == "node"), None)
    modules = browser_pack.manifest.node_modules
    entry = f"{modules}/{PLAYWRIGHT_MCP_ENTRY}" if modules else None
    declared = next((file for file in browser_pack.manifest.files if file.path == entry), None)
    if executable is None or declared is None:
        raise BrowserRuntimeUnavailable("browser_runtime_unavailable")
    node_root = manager.location(NODE_PACK_ID)
    browser_root = manager.location(BROWSER_PACK_ID)
    entry_path = browser_root / declared.path
    # The catalog recheck covers declared executables; the entry script is
    # what Node will run, so its bytes are checked against the release too.
    if _file_sha256(entry_path) != declared.sha256:
        raise BrowserRuntimeUnavailable("browser_runtime_not_ready")
    helper = next((item for item in browser_pack.manifest.executables if item.name == CONNECT_HELPER_EXECUTABLE), None)
    if local_files.WINDOWS and helper is None:
        raise BrowserRuntimeUnavailable("browser_runtime_unavailable")
    if helper is not None and _file_sha256(browser_root / helper.path) != helper.sha256:
        raise BrowserRuntimeUnavailable("browser_runtime_not_ready")
    return BrowserRuntime(
        node=node_root / executable.path,
        entry=entry_path,
        read_roots=(node_root, browser_root),
        connect_helper=browser_root / helper.path if helper is not None else None,
    )


def _file_sha256(path: Path) -> str | None:
    try:
        descriptor = local_files.open_file(path)
    except OSError:
        return None
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or not local_files.owned_by_user(descriptor, metadata) or metadata.st_size > 16 * 1024**2:
            return None
        return hashlib.sha256(stream.read()).hexdigest()

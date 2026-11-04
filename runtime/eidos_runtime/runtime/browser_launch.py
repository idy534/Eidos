"""Resolve the inputs a bundled browser MCP connection starts with."""
from __future__ import annotations

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.extensions.browser_plugin import BrowserLaunch
from eidos_runtime.infrastructure.browser_host import BrowserHost, BrowserUnavailable
from eidos_runtime.infrastructure.browser_runtime import BrowserRuntimeUnavailable, resolve_browser_runtime
from eidos_runtime.infrastructure.runtime_dependencies import RuntimeDependencyCatalog
from eidos_runtime.infrastructure.user_chrome import user_chrome_endpoint
from eidos_runtime.persistence.browser_grants import BrowserGrantRepository

# Tool discovery never connects to a browser; Playwright lists tools offline.
DISCOVERY_ENDPOINT = "http://127.0.0.1:9"


def resolve_browser_launch(
    store: SessionStore,
    host: BrowserHost | None,
    catalog: RuntimeDependencyCatalog | None,
    *,
    discovery: bool = False,
) -> BrowserLaunch:
    """Verified Playwright MCP inputs plus the Chrome endpoint for the current target."""
    from eidos_runtime.infrastructure.managed_resource_catalog import ManagedResourceCatalog

    if host is None:
        raise BrowserRuntimeUnavailable("browser_runtime_unavailable")
    manager = catalog.resources if isinstance(catalog, ManagedResourceCatalog) else None
    runtime = resolve_browser_runtime(manager)
    # Screenshots are always kept for the feed. Whether the model may see them is
    # decided per request from its declared capability (model/tool_content.py).
    images = "allow"
    if discovery:
        return BrowserLaunch(
            runtime=runtime,
            variables={"BROWSER_CDP_ENDPOINT": DISCOVERY_ENDPOINT, "BROWSER_IMAGE_RESPONSES": images},
        )
    if BrowserGrantRepository(store.database).target() == "user_chrome":
        # The user opted in to their everyday Chrome; Eidos never launches it.
        endpoint = user_chrome_endpoint()
        if endpoint is None:
            raise BrowserRuntimeUnavailable("browser_attach_unavailable")

        def attached(_cancel: object) -> str:
            if user_chrome_endpoint() != endpoint:
                raise BrowserUnavailable("browser_attach_unavailable")
            return endpoint

        return BrowserLaunch(
            runtime=runtime,
            variables={"BROWSER_CDP_ENDPOINT": endpoint, "BROWSER_IMAGE_RESPONSES": images},
            ensure_browser=attached, chrome_installed=lambda: True, profile="user",
        )
    try:
        endpoint = host.endpoint()
    except BrowserUnavailable as error:
        raise BrowserRuntimeUnavailable(error.code) from None
    return BrowserLaunch(
        runtime=runtime,
        variables={"BROWSER_CDP_ENDPOINT": endpoint, "BROWSER_IMAGE_RESPONSES": images},
        ensure_browser=host.ensure,
        chrome_installed=lambda: host.status().chrome_installed,
    )

"""Resolve the inputs a bundled browser MCP connection starts with."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.extensions.browser_plugin import BrowserLaunch
from eidos_runtime.infrastructure.browser_host import BrowserHost, BrowserUnavailable
from eidos_runtime.infrastructure.browser_runtime import BrowserRuntimeUnavailable, resolve_browser_runtime
from eidos_runtime.infrastructure.chrome_extension import TOKEN_ENVIRONMENT, ExtensionPresence, extension_presence
from eidos_runtime.infrastructure.runtime_dependencies import RuntimeDependencyCatalog
from eidos_runtime.infrastructure.secret_files import SecretFiles
from eidos_runtime.infrastructure.user_chrome import user_chrome_endpoint
from eidos_runtime.persistence.browser_grants import BrowserGrantRepository

# Tool discovery never connects to a browser; Playwright lists tools offline.
DISCOVERY_ENDPOINT = "http://127.0.0.1:9"
BROWSER_SECRETS_DIRECTORY = "browser-secrets"
MAX_CLIENT_TITLE = 20


def browser_secrets(store: SessionStore) -> SecretFiles | None:
    directory = store.data_directory
    return SecretFiles(Path(directory) / BROWSER_SECRETS_DIRECTORY, max_bytes=512) if directory is not None else None


def extension_token(store: SessionStore) -> str | None:
    """The saved Playwright Extension token, or None when it is not configured."""

    reference = BrowserGrantRepository(store.database).extension_credential()
    secrets = browser_secrets(store)
    if reference is None or secrets is None:
        return None
    try:
        return secrets.read(reference)
    except ValueError:
        return None


def browser_client_name(session_title: str | None) -> str:
    """Shown by the extension as the tab group title: "Playwright · Eidos · <title>"."""

    title = " ".join((session_title or "").split())
    if len(title) > MAX_CLIENT_TITLE:
        title = title[:MAX_CLIENT_TITLE] + "…"
    return f"Eidos · {title}" if title else "Eidos"


def resolve_browser_launch(
    store: SessionStore,
    host: BrowserHost | None,
    catalog: RuntimeDependencyCatalog | None,
    *,
    discovery: bool = False,
    session_title: str | None = None,
    presence: Callable[[], ExtensionPresence] = extension_presence,
) -> BrowserLaunch:
    """Verified Playwright MCP inputs plus the browser the current target selects.

    The target is decided once, when a Run's connection starts; a Run never
    switches browsers midway.
    """
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
    target = BrowserGrantRepository(store.database).target()
    if target == "user_chrome":
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
    if target == "chrome_extension":
        token = extension_token(store)
        # Not configured: fall back to the Eidos browser for this whole Run.
        if token is not None and presence() != "missing":
            return BrowserLaunch(
                runtime=runtime,
                # An empty CDP endpoint lets Playwright's extension settings apply.
                variables={"BROWSER_CDP_ENDPOINT": "", "BROWSER_IMAGE_RESPONSES": images},
                chrome_installed=lambda: host.status().chrome_installed,
                profile="user", mode="extension",
                environment={TOKEN_ENVIRONMENT: token},
                client_name=browser_client_name(session_title),
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

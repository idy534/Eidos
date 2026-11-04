from __future__ import annotations

from typing import Literal

from pydantic import Field

from eidos_runtime.models import EidosFrozenStrictModel


class BrowserStatusRequest(EidosFrozenStrictModel):
    session_id: str | None = Field(default=None, min_length=1, max_length=128)


class BrowserStatus(EidosFrozenStrictModel):
    chrome_installed: bool
    chrome_version: str | None = Field(default=None, max_length=64)
    running: bool
    plugin_installed: bool
    plugin_enabled: bool
    resource: Literal["unavailable", "missing", "preparing", "ready", "failed"]
    target: Literal["eidos", "user_chrome", "chrome_extension"] = "chrome_extension"
    # The browser a Run starting now would use: chrome_extension falls back to
    # the Eidos browser until an extension token is saved.
    effective_target: Literal["eidos", "user_chrome", "chrome_extension"] = "eidos"
    # The user's Chrome has remote debugging turned on at chrome://inspect.
    attach_available: bool = False
    # An extension token is saved; the token itself is never returned.
    extension_configured: bool = False
    # Whether a Chrome profile has the Playwright Extension; "unknown" when
    # Chrome's profile files cannot be read.
    extension_presence: Literal["installed", "missing", "unknown"] = "unknown"
    # Only reported when the request names a Session; follows the effective target.
    granted: bool = False


class BrowserGrantRevokeRequest(EidosFrozenStrictModel):
    session_id: str = Field(min_length=1, max_length=128)


class BrowserGrantRevokeResponse(EidosFrozenStrictModel):
    revoked: bool


class BrowserCloseRequest(EidosFrozenStrictModel):
    pass


class BrowserCloseResponse(EidosFrozenStrictModel):
    closed: bool


class BrowserOpenRequest(EidosFrozenStrictModel):
    pass


class BrowserOpenResponse(EidosFrozenStrictModel):
    opened: bool
    error_code: str | None = Field(default=None, max_length=64)


class BrowserClearRequest(EidosFrozenStrictModel):
    pass


class BrowserClearResponse(EidosFrozenStrictModel):
    cleared: bool
    revoked_grants: int = Field(ge=0)
    error_code: str | None = Field(default=None, max_length=64)


class BrowserTargetRequest(EidosFrozenStrictModel):
    target: Literal["eidos", "user_chrome", "chrome_extension"]


class BrowserTargetResponse(EidosFrozenStrictModel):
    target: Literal["eidos", "user_chrome", "chrome_extension"]
    revoked_grants: int = Field(ge=0)


class BrowserExtensionTokenRequest(EidosFrozenStrictModel):
    # The bare token or the whole "PLAYWRIGHT_MCP_EXTENSION_TOKEN=..." line.
    token: str = Field(min_length=1, max_length=512, repr=False)


class BrowserExtensionTokenClearRequest(EidosFrozenStrictModel):
    pass


class BrowserExtensionTokenResponse(EidosFrozenStrictModel):
    configured: bool
    revoked_grants: int = Field(ge=0)

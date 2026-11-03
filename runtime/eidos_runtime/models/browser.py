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
    target: Literal["eidos", "user_chrome"] = "eidos"
    # The user's Chrome has remote debugging turned on at chrome://inspect.
    attach_available: bool = False
    # Only reported when the request names a Session; follows the current target.
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
    target: Literal["eidos", "user_chrome"]


class BrowserTargetResponse(EidosFrozenStrictModel):
    target: Literal["eidos", "user_chrome"]
    revoked_grants: int = Field(ge=0)

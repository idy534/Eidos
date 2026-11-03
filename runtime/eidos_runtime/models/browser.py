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
    # Only reported when the request names a Session.
    granted: bool = False


class BrowserGrantRevokeRequest(EidosFrozenStrictModel):
    session_id: str = Field(min_length=1, max_length=128)


class BrowserGrantRevokeResponse(EidosFrozenStrictModel):
    revoked: bool


class BrowserCloseRequest(EidosFrozenStrictModel):
    pass


class BrowserCloseResponse(EidosFrozenStrictModel):
    closed: bool

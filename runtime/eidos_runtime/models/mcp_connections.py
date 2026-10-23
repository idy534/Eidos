"""Remote MCP configuration and public connection contracts. Tokens never project."""
from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator

from eidos_runtime.models import EidosFrozenModel


def remote_url(value: str) -> str:
    parsed = urlsplit(value)
    if (len(value) > 2048 or parsed.username is not None or parsed.password is not None
            or parsed.fragment or parsed.query or not parsed.hostname
            or any(ord(char) < 33 for char in value)
            or (parsed.scheme != "https" and not (
                parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "::1", "localhost"}
            ))):
        raise ValueError("mcp_url_invalid")
    try:
        parsed.port
    except ValueError:
        raise ValueError("mcp_url_invalid") from None
    return value


class RemoteMcpAuth(EidosFrozenModel):
    type: Literal["none", "bearer", "oauth", "github-device"] = "none"
    client_id: str | None = Field(default=None, max_length=256)
    client_metadata_url: str | None = Field(default=None, max_length=2048)
    scopes: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("scopes")
    @classmethod
    def valid_scopes(cls, values: list[str]) -> list[str]:
        if any(not value or len(value) > 128 or any(char.isspace() for char in value) for value in values):
            raise ValueError("mcp_auth_invalid")
        return values

    @model_validator(mode="after")
    def valid_client(self) -> "RemoteMcpAuth":
        if self.client_metadata_url:
            remote_url(self.client_metadata_url)
            if not self.client_metadata_url.startswith("https://"):
                raise ValueError("mcp_auth_invalid")
        if self.type not in {"oauth", "github-device"} and (self.client_id or self.client_metadata_url or self.scopes):
            raise ValueError("mcp_auth_invalid")
        return self


class RemoteMcpInfo(EidosFrozenModel):
    url: str
    auth_type: Literal["none", "bearer", "oauth", "github-device"]


class McpConnectionRequest(EidosFrozenModel):
    plugin_id: str = Field(min_length=1, max_length=128)
    server_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    action: Literal["read", "connect", "cancel", "disconnect"] = "read"
    method: Literal["browser", "token"] = "browser"
    token: SecretStr | None = Field(default=None, repr=False)
    operation_id: str | None = Field(default=None, max_length=128)

    @model_validator(mode="after")
    def valid_action(self) -> "McpConnectionRequest":
        if self.action == "connect" and not self.operation_id:
            raise ValueError("operation_id_required")
        if self.token is not None and (self.action != "connect" or self.method != "token"
                                       or not 1 <= len(self.token.get_secret_value().encode()) <= 8192):
            raise ValueError("mcp_credential_invalid")
        if self.action == "connect" and self.method == "token" and self.token is None:
            raise ValueError("mcp_credential_required")
        if self.token and any(ord(char) <= 32 or ord(char) >= 127 for char in self.token.get_secret_value()):
            raise ValueError("mcp_credential_invalid")
        return self


class McpConnectionState(EidosFrozenModel):
    plugin_id: str
    server_id: str
    status: Literal["disconnected", "connecting", "connected", "expired", "error"] = "disconnected"
    auth_type: Literal["none", "bearer", "oauth", "github-device"] = "none"
    browser_available: bool = False
    account_label: str | None = None
    expires_at: int | None = None
    error_code: str | None = None
    authorization_url: str | None = Field(default=None, max_length=8192)
    user_code: str | None = Field(default=None, max_length=128)
    updated_at: int = 0


class StoredMcpConnection(EidosFrozenModel):
    plugin_id: str
    server_id: str
    config_hash: str
    generation: str
    status: Literal["disconnected", "connecting", "connected", "expired", "error"]
    credential_ref: str | None = Field(default=None, repr=False)
    account_label: str | None = None
    expires_at: int | None = None
    error_code: str | None = None
    updated_at: int


class BuiltinPlugin(EidosFrozenModel):
    id: str
    name: str
    description: str
    version: str
    installed: bool
    update_available: bool = False


class BuiltinPluginRequest(EidosFrozenModel):
    action: Literal["list", "install"] = "list"
    plugin_id: str = Field(default="eidos-github", pattern=r"^[a-z][a-z0-9_-]{0,63}$")


class BuiltinPluginsResponse(EidosFrozenModel):
    plugins: list[BuiltinPlugin]

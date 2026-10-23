from __future__ import annotations

from pathlib import PurePosixPath
import re
from collections.abc import Mapping
from typing import Literal

from pydantic import Field, StrictInt, StrictStr, field_validator, model_validator
from semantic_version import Version

from eidos_runtime.protocol.schemas import ClosedModel
from eidos_runtime.models.mcp_connections import RemoteMcpAuth, remote_url


_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_ENV = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


def validate_env_values(values: Mapping[str, str]) -> dict[str, str]:
    if len(values) > 64:
        raise ValueError("invalid_env_values")
    result: dict[str, str] = {}
    total_bytes = 0
    for name, value in values.items():
        if (
            not isinstance(name, str)
            or not _ENV.fullmatch(name)
            or not isinstance(value, str)
            or "\x00" in value
        ):
            raise ValueError("invalid_env_values")
        value_bytes = len(value.encode("utf-8"))
        if value_bytes > 4096:
            raise ValueError("invalid_env_values")
        total_bytes += value_bytes
        result[name] = value
    if total_bytes > 256 * 1024:
        raise ValueError("invalid_env_values")
    return result


def _relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or value.startswith("/")
        or "\\" in value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError("invalid_relative_path")
    return value


class SkillDeclarationV1(ClosedModel):
    root: StrictStr = Field(min_length=1, max_length=256)

    @field_validator("root")
    @classmethod
    def validate_root(cls, value: str) -> str:
        return _relative_path(value)


class McpServerConfigV1(ClosedModel):
    id: StrictStr
    executable: StrictStr = Field(default="", max_length=1024)
    transport: Literal["stdio", "streamable-http"] = Field(default="stdio", exclude_if=lambda value: value == "stdio")
    url: StrictStr | None = Field(default=None, exclude_if=lambda value: value is None)
    auth: RemoteMcpAuth | None = Field(default=None, exclude_if=lambda value: value is None)
    argv: list[StrictStr] = Field(default_factory=list, max_length=64)
    env_names: list[StrictStr] = Field(default_factory=list, alias="envNames", max_length=64)
    env_bindings: dict[StrictStr, StrictStr] = Field(default_factory=dict, alias="envBindings",
                                                  max_length=64, exclude_if=lambda value: not value)
    permission_profile: Literal["connector", "workspace_read"] = Field(
        alias="permissionProfile"
    )
    startup_timeout_seconds: StrictInt = Field(
        default=15, alias="startupTimeoutSeconds", ge=1, le=60
    )
    tool_timeout_seconds: StrictInt = Field(
        default=60, alias="toolTimeoutSeconds", ge=1, le=600
    )
    enabled: bool = False

    @model_validator(mode="after")
    def validate_transport(self) -> "McpServerConfigV1":
        if self.transport == "stdio":
            if not self.executable or self.url is not None or self.auth is not None:
                raise ValueError("mcp_transport_invalid")
        else:
            if not self.url or self.executable or self.argv or self.env_names or self.env_bindings:
                raise ValueError("mcp_transport_invalid")
            remote_url(self.url)
            if self.auth and self.auth.type == "github-device":
                from urllib.parse import urlsplit
                parsed = urlsplit(self.url)
                if parsed.scheme != "https" or parsed.netloc != "api.githubcopilot.com" or not parsed.path.startswith("/mcp/"):
                    raise ValueError("mcp_github_origin_invalid")
        return self

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not _ID.fullmatch(value):
            raise ValueError("invalid_server_id")
        return value

    @field_validator("executable")
    @classmethod
    def validate_executable(cls, value: str) -> str:
        if "\x00" in value or "\n" in value or "\r" in value:
            raise ValueError("invalid_executable")
        return value

    @field_validator("argv")
    @classmethod
    def validate_argv(cls, values: list[str]) -> list[str]:
        if any("\x00" in value or len(value.encode("utf-8")) > 4096 for value in values):
            raise ValueError("invalid_argv")
        return values

    @field_validator("env_names")
    @classmethod
    def validate_env_names(cls, values: list[str]) -> list[str]:
        if len(set(values)) != len(values) or any(not _ENV.fullmatch(value) for value in values):
            raise ValueError("invalid_env_names")
        return values

    @field_validator("env_bindings")
    @classmethod
    def validate_env_bindings(cls, values: dict[str, str]) -> dict[str, str]:
        if any(not _ENV.fullmatch(key) or not _ENV.fullmatch(value) for key, value in values.items()):
            raise ValueError("invalid_env_bindings")
        return values


class PluginManifestV1(ClosedModel):
    schema_version: Literal[1] = Field(alias="schemaVersion")
    id: StrictStr
    name: StrictStr = Field(min_length=1, max_length=128)
    version: StrictStr
    description: StrictStr = Field(max_length=1024)
    skills: list[SkillDeclarationV1] = Field(default_factory=list, max_length=64)
    mcp_servers: list[McpServerConfigV1] = Field(
        default_factory=list, alias="mcpServers", max_length=32
    )

    @field_validator("id")
    @classmethod
    def validate_id(cls, value: str) -> str:
        if not _ID.fullmatch(value):
            raise ValueError("invalid_plugin_id")
        return value

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        try:
            Version(value)
        except ValueError:
            raise ValueError("invalid_plugin_version")
        return value

    @model_validator(mode="after")
    def unique_declarations(self) -> "PluginManifestV1":
        roots = [skill.root for skill in self.skills]
        server_ids = [server.id for server in self.mcp_servers]
        if len(set(roots)) != len(roots) or len(set(server_ids)) != len(server_ids):
            raise ValueError("duplicate_plugin_declaration")
        return self

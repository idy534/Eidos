from __future__ import annotations

from pathlib import PurePosixPath
import re
from typing import Literal

from pydantic import Field, StrictInt, StrictStr, field_validator, model_validator

from eidos_runtime.protocol.schemas import ClosedModel


_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$")
_ENV = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


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
    executable: StrictStr = Field(min_length=1, max_length=1024)
    argv: list[StrictStr] = Field(default_factory=list, max_length=64)
    env_names: list[StrictStr] = Field(default_factory=list, alias="envNames", max_length=64)
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
        if not _VERSION.fullmatch(value):
            raise ValueError("invalid_plugin_version")
        return value

    @model_validator(mode="after")
    def unique_declarations(self) -> "PluginManifestV1":
        roots = [skill.root for skill in self.skills]
        server_ids = [server.id for server in self.mcp_servers]
        if len(set(roots)) != len(roots) or len(set(server_ids)) != len(server_ids):
            raise ValueError("duplicate_plugin_declaration")
        return self

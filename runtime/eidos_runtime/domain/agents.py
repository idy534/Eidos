"""Declarative agent types. Definitions cannot grant execution authority."""

import hashlib
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from eidos_runtime.models import EidosFrozenStrictModel

AgentRole = Literal["default", "explorer", "worker"]
BUILTIN_AGENT_ROLES = ("default", "explorer", "worker")
AgentTypeName = Annotated[
    str, Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
]
MAX_AGENT_FILES = 128
MAX_AGENT_FILE_BYTES = 64 * 1024


class AgentModelSelection(EidosFrozenStrictModel):
    provider_id: str | None = Field(default=None, min_length=1, max_length=64)
    config_id: str | None = Field(default=None, min_length=1, max_length=256)
    reasoning_selection: (
        Literal["none", "thinking", "low", "medium", "high", "xhigh", "max"] | None
    ) = None

    @model_validator(mode="after")
    def paired_provider(self) -> "AgentModelSelection":
        if (self.provider_id is None) != (self.config_id is None):
            raise ValueError("provider_id 和 config_id 必须同时填写。")
        if self.config_id is None and self.reasoning_selection is None:
            raise ValueError("model 至少需要一个模型设置。")
        return self


class AgentDefinition(EidosFrozenStrictModel):
    schema_version: Literal[1]
    name: AgentTypeName
    description: str = Field(min_length=1, max_length=1000)
    enabled: bool = True
    base_role: AgentRole = "default"
    instructions: str = Field(min_length=1, max_length=48 * 1024)
    model: AgentModelSelection | None = None

    @field_validator("name")
    @classmethod
    def reserved_name(cls, value: str) -> str:
        if value in BUILTIN_AGENT_ROLES:
            raise ValueError("名称不能覆盖内置子 Agent。")
        return value

    @field_validator("instructions", "description")
    @classmethod
    def nonempty_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("正文不能为空。")
        if len(value.encode("utf-8")) > 48 * 1024:
            raise ValueError("正文超过 48 KiB。")
        return value


class AgentCatalogItem(EidosFrozenStrictModel):
    name: str = Field(max_length=256)
    description: str = Field(default="", max_length=1000)
    source: str = Field(max_length=4096)
    base_role: AgentRole = "default"
    provider_id: str | None = None
    model_id: str | None = None
    enabled: bool = False
    available: bool = False
    reason: str | None = Field(default=None, max_length=512)
    definition_version: str | None = None


class AgentCatalogResponse(EidosFrozenStrictModel):
    agents: list[AgentCatalogItem] = Field(
        default_factory=list, max_length=MAX_AGENT_FILES
    )
    error: str | None = Field(default=None, max_length=512)


class AgentCatalogSnapshot(EidosFrozenStrictModel):
    definitions: list[AgentDefinition] = Field(
        default_factory=list, max_length=MAX_AGENT_FILES
    )

    @model_validator(mode="after")
    def unique_names(self) -> "AgentCatalogSnapshot":
        names = [definition.name for definition in self.definitions]
        if len(names) != len(set(names)):
            raise ValueError("Agent 快照包含重复名称。")
        return self


def definition_version(definition: AgentDefinition) -> str:
    return hashlib.sha256(definition.model_dump_json().encode()).hexdigest()

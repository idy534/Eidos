from pydantic import Field
from eidos_runtime.models import EidosFrozenStrictModel
from eidos_runtime.domain.agents import AgentDefinition


class AgentReadRequest(EidosFrozenStrictModel):
    session_id: str = Field(min_length=1, max_length=128)


class AgentStopRequest(EidosFrozenStrictModel):
    parent_run_id: str = Field(min_length=1, max_length=128)
    agent_id: str = Field(min_length=1, max_length=128)


class AgentDiscardWorkspaceRequest(AgentStopRequest):
    """Same shape as stop; a distinct type keeps one request model per registered method."""


class AgentMessageFromUserRequest(EidosFrozenStrictModel):
    operation_id: str = Field(min_length=1, max_length=128)
    parent_run_id: str = Field(min_length=1, max_length=128)
    agent_id: str = Field(min_length=1, max_length=128)
    text: str = Field(min_length=1, max_length=64 * 1024)


class AgentChangesRequest(EidosFrozenStrictModel):
    parent_run_id: str = Field(min_length=1, max_length=128)
    agent_id: str = Field(min_length=1, max_length=128)
    path: str | None = Field(default=None, min_length=1, max_length=4096)
    offset_bytes: int = Field(default=0, ge=0, le=2**53 - 1)
    max_bytes: int = Field(default=64 * 1024, ge=4, le=64 * 1024)


class AgentCatalogRequest(EidosFrozenStrictModel):
    pass


class AgentDefinitionReadRequest(EidosFrozenStrictModel):
    source: str = Field(min_length=1, max_length=4096)


class AgentDefinitionSaveRequest(EidosFrozenStrictModel):
    definition: AgentDefinition
    source: str | None = Field(default=None, min_length=1, max_length=4096)
    expected_version: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class AgentDefinitionRemoveRequest(AgentDefinitionReadRequest):
    expected_version: str = Field(pattern=r"^[a-f0-9]{64}$")


class AgentDefinitionDocument(EidosFrozenStrictModel):
    source: str = Field(min_length=1, max_length=4096)
    version: str = Field(pattern=r"^[a-f0-9]{64}$")
    definition: AgentDefinition
    text: str = Field(max_length=65536)

from pydantic import Field
from eidos_runtime.models import EidosFrozenStrictModel
from eidos_runtime.domain.agents import AgentDefinition


class AgentReadRequest(EidosFrozenStrictModel):
    session_id: str = Field(min_length=1, max_length=128)


class AgentStopRequest(EidosFrozenStrictModel):
    parent_run_id: str = Field(min_length=1, max_length=128)
    agent_id: str = Field(min_length=1, max_length=128)


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

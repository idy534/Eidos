from pydantic import Field
from eidos_runtime.models import EidosFrozenStrictModel


class AgentReadRequest(EidosFrozenStrictModel):
    session_id: str = Field(min_length=1, max_length=128)


class AgentStopRequest(EidosFrozenStrictModel):
    parent_run_id: str = Field(min_length=1, max_length=128)
    agent_id: str = Field(min_length=1, max_length=128)

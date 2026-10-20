from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from eidos_runtime.domain.agents import AgentRole, AgentTypeName
from eidos_runtime.domain.run import RunStatus
from eidos_runtime.domain.run_termination import RunTermination
from eidos_runtime.models import EidosFrozenStrictModel, JsonSafeInt

# Delegation is currently one level deep. These bounds cap persisted context
# and UI responses; ordinary user Sessions keep their policy.
MAX_AGENTS = 16
# The scheduler counts executing children per parent Run, never across Runs.
MAX_ACTIVE_AGENTS = 8
MAX_AGENT_MESSAGE_BYTES = 64 * 1024
MAX_AGENT_MESSAGES = 32
MAX_AGENT_RESULT_PAGE_BYTES = 64 * 1024


def _bounded_message(value: str) -> str:
    if not value.strip() or len(value.encode('utf-8')) > MAX_AGENT_MESSAGE_BYTES:
        raise ValueError('agent_message_empty_or_too_large')
    return value


AGENT_TOOLS = frozenset({
    "spawn_agent", "send_message", "followup_task", "wait_agents", "list_agents", "stop_agent",
    "read_agent_result", "read_agent_message",
})
ACTIVE_STATUSES = ("queued", "running", "waiting_approval", "waiting_input", "waiting_agents", "finalizing")


class SpawnAgent(EidosFrozenStrictModel):
    task_name: str = Field(min_length=1, max_length=60, pattern=r"^[a-z][a-z0-9_-]*$")
    message: str = Field(min_length=1, max_length=MAX_AGENT_MESSAGE_BYTES, description="A self-contained assignment, up to 64 KiB UTF-8, with scope and acceptance criteria. The child receives this task, not the whole parent conversation.")
    role: AgentTypeName = Field(default="default", description="Agent type from the frozen catalog: default for general work, explorer for focused investigation, worker for implementation and verification. All roles inherit the parent Run's permissions and ordinary tools; explorer is not a read-only permission mode. Custom types use their configured instructions and model.")
    required_for_completion: bool = Field(default=True, description="Set false only for supplementary work whose result is not needed to finish the task. Runtime cancels optional tasks and awaits their resource cleanup at parent completion.")

    _bounded = field_validator('message')(_bounded_message)


class AgentTarget(EidosFrozenStrictModel):
    agent_id: str = Field(min_length=1, max_length=80)


class AgentMessageRequest(AgentTarget):
    message: str = Field(min_length=1, max_length=MAX_AGENT_MESSAGE_BYTES, description="Message text, up to 64 KiB UTF-8. Send decisions, blockers or essential evidence; the final answer is delivered automatically.")

    _bounded = field_validator('message')(_bounded_message)


class ReadAgentResult(AgentTarget):
    item_id: str | None = Field(default=None, min_length=1, max_length=128)
    offset_bytes: JsonSafeInt = Field(default=0, ge=0)
    max_bytes: int = Field(default=16 * 1024, ge=4, le=MAX_AGENT_RESULT_PAGE_BYTES)
    from_end: bool = False


class ReadAgentMessage(EidosFrozenStrictModel):
    message_id: str = Field(min_length=1, max_length=128)


class AgentTextPage(EidosFrozenStrictModel):
    source_id: str
    content: str = Field(max_length=MAX_AGENT_RESULT_PAGE_BYTES)
    start_byte: JsonSafeInt = Field(ge=0)
    next_offset: JsonSafeInt = Field(ge=0)
    total_bytes: JsonSafeInt = Field(ge=0)
    has_more: bool

    @model_validator(mode='after')
    def consistent_page(self) -> AgentTextPage:
        if (self.start_byte + len(self.content.encode('utf-8')) != self.next_offset
                or not 0 <= self.start_byte <= self.next_offset <= self.total_bytes
                or self.has_more != (self.next_offset < self.total_bytes)
                or len(self.content.encode('utf-8')) > MAX_AGENT_RESULT_PAGE_BYTES):
            raise ValueError('invalid_agent_output_page')
        return self


class WaitAgents(EidosFrozenStrictModel):
    agent_ids: list[str] = Field(default_factory=list, max_length=MAX_AGENTS, description="Wait until all selected agents finish. An empty list selects all children of this Run.")
    timeout_ms: int = Field(default=30000, ge=1000, le=300000)


class AgentSummary(EidosFrozenStrictModel):
    id: str
    task_name: str
    role: AgentTypeName
    base_role: AgentRole | None = None
    model_id: str | None = None
    provider_id: str | None = None
    definition_version: str | None = None
    required_for_completion: bool = True
    parent_run_id: str
    session_id: str
    run_id: str
    status: RunStatus
    task: str
    result: str | None = None
    result_item_id: str | None = None
    error_code: str | None = None
    stop_reason: str | None = None
    termination: RunTermination | None = None
    reconciliation_required: bool = False
    created_at: JsonSafeInt


class AgentMessage(EidosFrozenStrictModel):
    id: str
    sender_session_id: str
    recipient_session_id: str
    content: str
    created_at: JsonSafeInt


class CollaborationState(EidosFrozenStrictModel):
    parent_run_id: str | None = None
    agents: list[AgentSummary] = Field(default_factory=list, max_length=MAX_AGENTS)
    messages: list[AgentMessage] = Field(default_factory=list, max_length=MAX_AGENT_MESSAGES)


class AgentWait(EidosFrozenStrictModel):
    run_id: str
    item_id: str | None = None
    agent_ids: list[str]
    deadline_at: JsonSafeInt
    status: Literal["pending", "ready", "canceled"]


class AgentSuspended(Exception):
    """A persisted collaboration wait owns the continuation, not a Worker."""


class CollaborationRejected(ValueError):
    """A known rejection before the collaboration mutation commits."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from eidos_runtime.domain.run import RunStatus
from eidos_runtime.models import EidosFrozenStrictModel, JsonSafeInt

# Delegation is currently one level deep. These bounds cap persisted context
# and UI responses; ordinary user Sessions keep their policy.
MAX_AGENTS = 16
# The scheduler counts executing children per parent Run, never across Runs.
MAX_ACTIVE_AGENTS = 8
AgentRole = Literal["default", "explorer", "worker"]
AGENT_TOOLS = frozenset({
    "spawn_agent", "send_message", "followup_task", "wait_agents", "list_agents", "stop_agent",
})
ACTIVE_STATUSES = ("queued", "running", "waiting_approval", "waiting_input", "waiting_agents", "finalizing")


class SpawnAgent(EidosFrozenStrictModel):
    task_name: str = Field(min_length=1, max_length=60, pattern=r"^[a-z][a-z0-9_-]*$")
    message: str = Field(min_length=1, max_length=8000, description="A self-contained assignment with scope and acceptance criteria. The child receives this task, not the whole parent conversation.")
    role: AgentRole = Field(default="default", description="Built-in task role: default for general work, explorer for focused investigation, worker for implementation and verification. All roles inherit the parent Run's permissions and ordinary tools; explorer is not a read-only permission mode. Custom roles are not supported.")
    required_for_completion: bool = Field(default=True, description="Set false only for supplementary work whose result is not needed to finish the task. Runtime cancels optional tasks and awaits their resource cleanup at parent completion.")


class AgentTarget(EidosFrozenStrictModel):
    agent_id: str = Field(min_length=1, max_length=80)


class AgentMessageRequest(AgentTarget):
    message: str = Field(min_length=1, max_length=2000)


class WaitAgents(EidosFrozenStrictModel):
    agent_ids: list[str] = Field(default_factory=list, max_length=MAX_AGENTS, description="Wait until all selected agents finish. An empty list selects all children of this Run.")
    timeout_ms: int = Field(default=30000, ge=1000, le=300000)


class AgentSummary(EidosFrozenStrictModel):
    id: str
    task_name: str
    role: AgentRole
    required_for_completion: bool = True
    parent_run_id: str
    session_id: str
    run_id: str
    status: RunStatus
    task: str
    result: str | None = None
    result_item_id: str | None = None
    error_code: str | None = None
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
    messages: list[AgentMessage] = Field(default_factory=list, max_length=16)


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

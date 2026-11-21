"""Agent Team coordination models.

A team is a Session-level coordination layer. Its tasks and messages are
declared coordination state; execution facts stay in Runs, Items and ToolCalls.
"""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from eidos_runtime.domain.agents import AgentTypeName
from eidos_runtime.domain.collaboration import AgentChangedFile
from eidos_runtime.domain.run import RunStatus
from eidos_runtime.models import EidosFrozenStrictModel, JsonSafeInt

MAX_TEAM_MEMBERS = 16
# Counted per team only, never across teams or Sessions (AGENTS.md section 13).
MAX_ACTIVE_MEMBER_RUNS = 8
MAX_TEAM_TASKS = 200
MAX_TASK_DEPENDENCIES = 16
MAX_TEAM_MESSAGE_BYTES = 64 * 1024
MAX_DELIVERED_MESSAGES = 16
MESSAGE_PREVIEW_CHARACTERS = 2000
MAX_PROPOSED_TASKS = 50
PING_PONG_LIMIT = 6
RESERVED_MEMBER_NAMES = frozenset({"lead", "all", "user", "parent", "system", "team"})

# JsonSafeInt carries its own range; Field(ge=0) beside it would be overridden.
Count = Annotated[int, Field(strict=True, ge=0, le=9_007_199_254_740_991)]
MemberName = Annotated[str, Field(min_length=1, max_length=40, pattern=r"^[a-z][a-z0-9_-]*$")]
TaskKey = Annotated[str, Field(min_length=1, max_length=40, pattern=r"^[a-z0-9][a-z0-9_.-]*$")]
TeamStatus = Literal["active", "paused", "dissolving", "dissolved"]
TaskStatus = Literal["open", "in_progress", "in_review", "done", "canceled"]
WorkspaceMode = Literal["shared", "worktree"]
MessageChannel = Literal["board", "direct"]
TaskOperation = Literal["create", "update", "assign", "claim", "submit", "accept", "reject", "release", "cancel", "reopen"]
TaskKind = Literal["work", "gate"]
MAX_CONTEXT_TURNS = 8
MAX_FORKED_CONTEXT_BYTES = 16 * 1024
TERMINAL_TASK_STATUSES = frozenset({"done", "canceled"})


def _bounded_text(value: str) -> str:
    if not value.strip() or len(value.encode("utf-8")) > MAX_TEAM_MESSAGE_BYTES:
        raise ValueError("team_message_empty_or_too_large")
    return value


class ProposedMember(EidosFrozenStrictModel):
    name: MemberName
    role: AgentTypeName = Field(default="worker", description="Built-in or catalog agent type: default, explorer, worker, verifier or a custom type.")
    brief: str = Field(min_length=1, max_length=MAX_TEAM_MESSAGE_BYTES, description="Standing responsibility of this member, its scope and what done means.")
    workspace: WorkspaceMode = Field(default="shared", description="worktree gives the member its own Git Worktree from the current lead state; use it when two or more members write files.")
    context_turns: int = Field(default=0, ge=0, le=MAX_CONTEXT_TURNS, description="Copy this many of your latest conversation turns into the member's standing context when the member needs the discussion behind its brief; 0 keeps only the brief.")

    @field_validator("name")
    @classmethod
    def _not_reserved(cls, value: str) -> str:
        if value in RESERVED_MEMBER_NAMES:
            raise ValueError("team_member_name_invalid")
        return value


class ProposedTask(EidosFrozenStrictModel):
    key: TaskKey
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=8000)
    acceptance: str = Field(default="", max_length=4000)
    owner: MemberName | None = None
    reviewer: str | None = Field(default=None, min_length=1, max_length=40, description="lead or a member name; required when reviewRequired is true and the lead is not the reviewer.")
    depends_on: list[TaskKey] = Field(default_factory=list, max_length=MAX_TASK_DEPENDENCIES)
    review_required: bool = False
    milestone: str | None = Field(default=None, min_length=1, max_length=80)
    paths: list[str] = Field(default_factory=list, max_length=50, description="Files this task expects to change; a declaration, not a lock.")
    kind: TaskKind = Field(default="work", description="gate is a milestone review: it stays blocked until every work task in its milestone is done, and its owner may reopen those tasks.")

    @model_validator(mode="after")
    def _gate_has_milestone(self) -> ProposedTask:
        if self.kind == "gate" and self.milestone is None:
            raise ValueError("team_gate_milestone_required")
        return self


class TeamProposal(EidosFrozenStrictModel):
    objective: str = Field(min_length=1, max_length=4000, description="The team's goal in the user's terms.")
    summary: str = Field(default="", max_length=2000, description="Why this roster and split fit the goal.")
    members: list[ProposedMember] = Field(min_length=1, max_length=MAX_TEAM_MEMBERS)
    tasks: list[ProposedTask] = Field(default_factory=list, max_length=MAX_PROPOSED_TASKS)
    template: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$", description="Team template name from the frozen template catalog, when one was used.")

    @model_validator(mode="after")
    def _consistent(self) -> TeamProposal:
        names = [member.name for member in self.members]
        if len(set(names)) != len(names):
            raise ValueError("team_member_name_exists")
        keys = [task.key for task in self.tasks]
        if len(set(keys)) != len(keys):
            raise ValueError("team_task_key_exists")
        known = set(names)
        for task in self.tasks:
            if task.owner is not None and task.owner not in known:
                raise ValueError("team_member_not_found")
            if task.reviewer is not None and task.reviewer != "lead" and task.reviewer not in known:
                raise ValueError("team_member_not_found")
            if not set(task.depends_on) <= set(keys) or task.key in task.depends_on:
                raise ValueError("team_task_dependency_invalid")
        _reject_cycles({task.key: list(task.depends_on) for task in self.tasks})
        return self


def _reject_cycles(graph: dict[str, list[str]]) -> None:
    state: dict[str, int] = {}

    def visit(node: str) -> None:
        mark = state.get(node, 0)
        if mark == 1:
            raise ValueError("team_task_cycle")
        if mark == 2:
            return
        state[node] = 1
        for target in graph.get(node, []):
            visit(target)
        state[node] = 2

    for node in graph:
        visit(node)


def reject_cycles(graph: dict[str, list[str]]) -> None:
    _reject_cycles(graph)


class TemplateMember(EidosFrozenStrictModel):
    name: MemberName
    role: AgentTypeName = "worker"
    brief: str = Field(min_length=1, max_length=8000)
    workspace: WorkspaceMode | None = None

    @field_validator("name")
    @classmethod
    def _not_reserved(cls, value: str) -> str:
        if value in RESERVED_MEMBER_NAMES:
            raise ValueError("team_member_name_invalid")
        return value


class TeamTemplate(EidosFrozenStrictModel):
    """A reusable roster and policy, frozen into the team when the roster is confirmed.

    Templates shape proposals and defaults only: they never choose models or
    approval modes (the user's roster decision does).
    """

    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")
    description: str = Field(default="", max_length=500)
    guidance: str = Field(default="", max_length=8000)
    members: list[TemplateMember] = Field(min_length=1, max_length=MAX_TEAM_MEMBERS)
    workspace_policy: Literal["shared", "worktree_for_writers"] = "worktree_for_writers"
    review_required: bool = True
    gate_reviewers: list[MemberName] = Field(default_factory=list, max_length=4)

    @model_validator(mode="after")
    def _consistent(self) -> TeamTemplate:
        names = [member.name for member in self.members]
        if len(set(names)) != len(names) or not set(self.gate_reviewers) <= set(names):
            raise ValueError("team_template_invalid")
        return self


class TeamTemplateSummary(EidosFrozenStrictModel):
    name: str
    description: str = ""
    members: list[str] = Field(default_factory=list)
    gate_reviewers: list[str] = Field(default_factory=list)
    workspace_policy: Literal["shared", "worktree_for_writers"] = "worktree_for_writers"
    source: Literal["builtin", "user"] = "builtin"


class RosterDecision(EidosFrozenStrictModel):
    """The user's answer to a proposal. Edits may only remove members or change workspaces."""

    confirmed: bool
    removed_members: list[MemberName] = Field(default_factory=list, max_length=MAX_TEAM_MEMBERS)
    workspaces: dict[str, WorkspaceMode] = Field(default_factory=dict, max_length=MAX_TEAM_MEMBERS)
    approval_mode: Literal["manual", "auto_review", "full_access"] = "manual"
    full_access_confirmation: Literal["full-access-v1"] | None = None
    auto_wake_lead: bool = True

    @model_validator(mode="after")
    def _full_access(self) -> RosterDecision:
        if (self.approval_mode == "full_access") != (self.full_access_confirmation is not None):
            raise ValueError("full access requires explicit confirmation")
        return self


class TeamRunSettings(EidosFrozenStrictModel):
    """Frozen when the roster is confirmed; automatic Runs use these values."""

    model_id: str = Field(min_length=1, max_length=256)
    reasoning_selection: str | None = Field(default=None, max_length=32)
    approval_mode: Literal["manual", "auto_review", "full_access"] = "manual"
    full_access_confirmation: Literal["full-access-v1"] | None = None


class MemberModel(EidosFrozenStrictModel):
    model_id: str = Field(min_length=1, max_length=256)
    reasoning_selection: str | None = Field(default=None, max_length=32)


class TeamRunOrigin(EidosFrozenStrictModel):
    """Why a team admitted this Run without a human input item."""

    origin: Literal["wake", "user_message"]
    member: str | None = None


class EvidenceRef(EidosFrozenStrictModel):
    item_id: str | None = Field(default=None, min_length=1, max_length=128)
    tool_call_id: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def _one(self) -> EvidenceRef:
        if (self.item_id is None) == (self.tool_call_id is None):
            raise ValueError("team_evidence_invalid")
        return self


# Tool requests -----------------------------------------------------------------


class TeamAddMember(ProposedMember):
    pass


class TeamRemoveMember(EidosFrozenStrictModel):
    name: MemberName
    reason: str = Field(min_length=1, max_length=500)
    discard_changes: bool = False


class TeamTaskRequest(EidosFrozenStrictModel):
    op: TaskOperation
    key: TaskKey
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=8000)
    acceptance: str | None = Field(default=None, max_length=4000)
    owner: MemberName | None = None
    reviewer: str | None = Field(default=None, min_length=1, max_length=40)
    depends_on: list[TaskKey] | None = Field(default=None, max_length=MAX_TASK_DEPENDENCIES)
    review_required: bool | None = None
    milestone: str | None = Field(default=None, min_length=1, max_length=80)
    paths: list[str] | None = Field(default=None, max_length=50)
    kind: TaskKind | None = Field(default=None, description="Only for op=create; gate needs a milestone.")
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=20, description="Tool results from your own Runs, or your subagents' Runs, that prove the work. Required to submit a task that needs review.")
    note: str | None = Field(default=None, max_length=2000, description="Progress, the reason for a rejection or release, or what the reviewer should check.")
    version: JsonSafeInt | None = Field(default=None, ge=1, description="Only for op=update: the task version you read.")


class TeamPostRequest(EidosFrozenStrictModel):
    to: list[str] = Field(min_length=1, max_length=MAX_TEAM_MEMBERS + 2, description="Member names, lead, all, or user. all and user are board-only.")
    channel: MessageChannel = Field(default="board", description="board is visible to every member; direct reaches exactly one recipient and is not shown to other members.")
    text: str = Field(min_length=1, max_length=MAX_TEAM_MESSAGE_BYTES)
    task: TaskKey | None = None

    _bounded = field_validator("text")(_bounded_text)


class TeamReadRequest(EidosFrozenStrictModel):
    kind: Literal["messages", "message", "tasks", "task", "member"]
    id: str | None = Field(default=None, min_length=1, max_length=128, description="Message id, task key or member name.")
    cursor: Count = Field(default=0, description="Continue messages after this sequence number.")
    member: MemberName | None = None
    task: TaskKey | None = None
    channel: MessageChannel | None = None


class TeamWaitRequest(EidosFrozenStrictModel):
    members: list[MemberName] = Field(default_factory=list, max_length=MAX_TEAM_MEMBERS)
    tasks: list[TaskKey] = Field(default_factory=list, max_length=MAX_TEAM_TASKS)
    mode: Literal["any", "all"] = "any"
    timeout_ms: int = Field(default=60000, ge=1000, le=300000)


class TeamIntegrateRequest(EidosFrozenStrictModel):
    task: TaskKey | None = None
    member: MemberName | None = None
    paths: list[str] | None = Field(default=None, min_length=1, max_length=2000)

    @model_validator(mode="after")
    def _one(self) -> TeamIntegrateRequest:
        if (self.task is None) == (self.member is None):
            raise ValueError("team_integrate_target_invalid")
        return self


# Views -----------------------------------------------------------------------------


class TeamMemberView(EidosFrozenStrictModel):
    id: str
    name: str
    role: str
    brief: str
    model_id: str
    workspace_mode: WorkspaceMode
    status: Literal["active", "removed"]
    session_id: str
    run_id: str | None = None
    run_status: RunStatus | None = None
    created_at: JsonSafeInt


class TeamTaskView(EidosFrozenStrictModel):
    id: str
    key: str
    title: str
    description: str = ""
    acceptance: str = ""
    owner: str | None = None
    reviewer: str | None = None
    kind: TaskKind = "work"
    status: TaskStatus
    blocked: bool = False
    depends_on: list[str] = Field(default_factory=list)
    review_required: bool = False
    milestone: str | None = None
    paths: list[str] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    delivery_status: str | None = None
    note: str | None = None
    version: JsonSafeInt
    created_by: str
    updated_at: JsonSafeInt


class TeamMessageView(EidosFrozenStrictModel):
    id: str
    seq: JsonSafeInt
    channel: MessageChannel
    author: str
    author_kind: Literal["lead", "member", "user", "system"]
    recipients: list[str] = Field(default_factory=list)
    to_all: bool = False
    task: str | None = None
    content: str
    truncated: bool = False
    created_at: JsonSafeInt


class TeamView(EidosFrozenStrictModel):
    id: str
    lead_session_id: str
    status: TeamStatus
    objective: str
    auto_wake_lead: bool
    run_settings: TeamRunSettings
    template: str | None = None
    members: list[TeamMemberView] = Field(default_factory=list)
    tasks: list[TeamTaskView] = Field(default_factory=list)
    version: JsonSafeInt
    created_at: JsonSafeInt
    updated_at: JsonSafeInt


class TeamProposalView(EidosFrozenStrictModel):
    id: str
    session_id: str
    run_id: str
    proposal: TeamProposal
    decision: Literal["pending", "confirmed", "declined"]
    team_id: str | None = None
    created_at: JsonSafeInt


class TeamMessagePage(EidosFrozenStrictModel):
    messages: list[TeamMessageView] = Field(default_factory=list)
    next_cursor: JsonSafeInt = 0
    has_more: bool = False


class TeamUsage(EidosFrozenStrictModel):
    """Token and time totals of one member, including the subagents it spawned."""

    member: str
    input_tokens: Count = 0
    output_tokens: Count = 0
    cache_read_tokens: Count = 0
    cache_write_tokens: Count = 0
    incomplete: bool = False
    effective_ms: Count = 0


class TeamSessionState(EidosFrozenStrictModel):
    """Desktop view of one Session: its open team and any pending proposal.

    Usage stays out of ``TeamView`` so tool results and the engine-compared
    team context do not change with every model attempt.
    """

    team: TeamView | None = None
    proposal: TeamProposalView | None = None
    usage: list[TeamUsage] = Field(default_factory=list, max_length=MAX_TEAM_MEMBERS * 4)


class TeamMemberChanges(EidosFrozenStrictModel):
    """A Worktree member's changes from its base, without Git object ids."""

    member: str
    status: Literal["ready", "stale", "integrated", "partially_integrated", "discarded"]
    files: list[AgentChangedFile] = Field(default_factory=list, max_length=2000)
    diff: str = ""
    diff_next_offset: Count = 0
    diff_has_more: bool = False


class TeamToolResultData(EidosFrozenStrictModel):
    team: TeamView | None = None
    task: TeamTaskView | None = None
    tasks: list[TeamTaskView] | None = None
    member: TeamMemberView | None = None
    messages: TeamMessagePage | None = None
    message: TeamMessageView | None = None
    proposal: TeamProposalView | None = None
    wait: dict[str, str] | None = None
    member_result: str | None = None


class TeamRejected(ValueError):
    """A known, stable rejection before any team mutation commits."""

"""Desktop requests for Agent Team state. Responses reuse the domain views."""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from eidos_runtime.domain.teams import (
    Count, MessageChannel, RosterDecision, TaskKey, TeamMemberChanges, TeamMessagePage, TeamPostRequest, TeamProposalView,
    TeamRunSettings, TeamSessionState, TeamTaskRequest, TeamView,
)
from eidos_runtime.models import EidosFrozenStrictModel

Id = Field(min_length=1, max_length=128)


class TeamSessionRequest(EidosFrozenStrictModel):
    session_id: str = Id


class TeamMessagesRequest(EidosFrozenStrictModel):
    team_id: str = Id
    after: Count = 0
    limit: int = Field(default=100, ge=1, le=200)
    member: str | None = Field(default=None, min_length=1, max_length=40)
    task: TaskKey | None = None
    channel: MessageChannel | None = None


class TeamConfirmRosterRequest(EidosFrozenStrictModel):
    operation_id: str = Id
    proposal_id: str = Id
    decision: RosterDecision


class TeamUserPostRequest(EidosFrozenStrictModel):
    operation_id: str = Id
    team_id: str = Id
    message: TeamPostRequest


class TeamUserTaskRequest(EidosFrozenStrictModel):
    operation_id: str = Id
    team_id: str = Id
    request: TeamTaskRequest


class TeamStatusRequest(EidosFrozenStrictModel):
    operation_id: str = Id
    team_id: str = Id
    status: Literal["active", "paused", "dissolving"]


class TeamTargetRequest(EidosFrozenStrictModel):
    operation_id: str = Id
    team_id: str = Id


class TeamRemoveMemberRequest(TeamTargetRequest):
    name: str = Field(min_length=1, max_length=40)
    discard_changes: bool = False


class TeamMemberChangesRequest(EidosFrozenStrictModel):
    team_id: str = Id
    name: str = Field(min_length=1, max_length=40)
    path: str | None = Field(default=None, min_length=1, max_length=4096)
    offset_bytes: Count = 0
    max_bytes: int = Field(default=64 * 1024, ge=4, le=64 * 1024)


class TeamSettingsRequest(TeamTargetRequest):
    auto_wake_lead: bool | None = None
    run_settings: TeamRunSettings | None = None


class TeamChangedNotice(EidosFrozenStrictModel):
    session_id: str = Field(min_length=1, max_length=128)


# The single method table for Desktop: Runtime registers handlers by these names
# and the Desktop contract generator derives typed methods and validators from it.
TEAM_METHODS: tuple[tuple[str, type[EidosFrozenStrictModel], type[EidosFrozenStrictModel]], ...] = (
    ("team/read", TeamSessionRequest, TeamSessionState),
    ("team/messages", TeamMessagesRequest, TeamMessagePage),
    ("team/memberChanges", TeamMemberChangesRequest, TeamMemberChanges),
    ("team/confirmRoster", TeamConfirmRosterRequest, TeamProposalView),
    ("team/post", TeamUserPostRequest, TeamView),
    ("team/taskUpdate", TeamUserTaskRequest, TeamView),
    ("team/setStatus", TeamStatusRequest, TeamView),
    ("team/stopMembers", TeamTargetRequest, TeamView),
    ("team/removeMember", TeamRemoveMemberRequest, TeamView),
    ("team/updateSettings", TeamSettingsRequest, TeamView),
)

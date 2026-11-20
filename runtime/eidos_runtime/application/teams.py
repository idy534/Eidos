"""Agent Team use cases above the coordination repository.

Creation runs inside the lead's team_propose ToolCall after the user confirms,
so Worktree side effects sit under that ToolCall's Durable Intent. Desktop
methods change coordination state only; member work always runs as ordinary
Runs admitted through ``runs.start`` with a team wake id.
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING

from eidos_runtime.application.agent_workspaces import AgentWorkspaceError, PreparedAgentWorkspace, WorkspaceOwner
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.domain.agents import BUILTIN_AGENT_ROLES
from eidos_runtime.domain.collaboration import AgentChangedFile
from eidos_runtime.domain.teams import (
    MemberModel, ProposedMember, RosterDecision, TeamMemberChanges, TeamPostRequest, TeamProposalView, TeamRejected,
    TeamRunSettings, TeamSessionState, TeamTaskRequest, TeamView,
)
from eidos_runtime.infrastructure.team_templates import TeamTemplateCatalog
from eidos_runtime.persistence.teams import USER_ACTOR, Actor, PreparedMember, ReadyWake, TeamRepository

if TYPE_CHECKING:
    from eidos_runtime.application.agent_workspaces import AgentWorkspaceService

logger = logging.getLogger("eidos.runtime")


class TeamApplication:
    def __init__(self, store: SessionStore, *, workspaces: Callable[[], AgentWorkspaceService | None],
                 cancel: Callable[[str], object], publish: Callable[[], object], schedule: Callable[[], object]) -> None:
        self.store = store
        self.repository = TeamRepository(store.database)
        self._workspaces = workspaces
        self._cancel = cancel
        self._publish = publish
        self._schedule = schedule

    # Creation ---------------------------------------------------------------------

    def create_from_proposal(self, lead_run_id: str, proposal: TeamProposalView, decision: RosterDecision) -> TeamView:
        if proposal.team_id is not None:
            return self.repository.team(proposal.team_id)
        removed = set(decision.removed_members)
        members = [member.model_copy(update={"workspace": decision.workspaces.get(member.name, member.workspace)})
                   for member in proposal.proposal.members if member.name not in removed]
        tasks = [task.model_copy(update={"owner": None if task.owner in removed else task.owner,
                                         "reviewer": None if task.reviewer in removed else task.reviewer})
                 for task in proposal.proposal.tasks]
        profile = self.store.read_model_profile(lead_run_id)
        settings = TeamRunSettings(model_id=profile.model_id, reasoning_selection=profile.reasoning_selection,
                                   approval_mode=decision.approval_mode, full_access_confirmation=decision.full_access_confirmation)
        prepared: list[tuple[PreparedMember, PreparedAgentWorkspace | None]] = []
        try:
            for member in members:
                prepared.append(self._prepare(proposal.session_id, lead_run_id, member, settings, f"team:{proposal.id}:{member.name}"))
            team = self.repository.create_team(proposal.id, lead_run_id, settings, decision.auto_wake_lead,
                                               [value for value, _ in prepared], tasks,
                                               template_json=self._template_json(proposal.proposal.template))
        except Exception:
            for _member, workspace in prepared:
                if workspace is not None and self._workspaces() is not None:
                    self._workspaces().abandon(workspace)  # type: ignore[union-attr]
            raise
        self._finish_workspaces(team, prepared)
        self._publish()
        self._schedule()
        return team

    def add_member(self, team_id: str, lead_run_id: str, member: ProposedMember) -> TeamView:
        team = self.repository.team(team_id)
        seed = f"team:{team_id}:{member.name}:{uuid.uuid4().hex}"
        value, workspace = self._prepare(team.lead_session_id, lead_run_id, member, team.run_settings, seed)
        try:
            self.repository.add_member(team_id, value)
        except Exception:
            if workspace is not None and self._workspaces() is not None:
                self._workspaces().abandon(workspace)  # type: ignore[union-attr]
            raise
        team = self.repository.team(team_id)
        self._finish_workspaces(team, [(value, workspace)])
        self._publish()
        self._schedule()
        return team

    def _prepare(self, lead_session_id: str, lead_run_id: str, member: ProposedMember, settings: TeamRunSettings,
                 seed: str) -> tuple[PreparedMember, PreparedAgentWorkspace | None]:
        definition_json = None
        model = MemberModel(model_id=settings.model_id, reasoning_selection=settings.reasoning_selection)
        if member.role not in BUILTIN_AGENT_ROLES:
            from eidos_runtime.persistence.agent_catalog import AgentSnapshotRepository
            definition = next((value for value in AgentSnapshotRepository(self.store.database).read(lead_run_id).definitions
                               if value.name == member.role), None)
            if definition is None:
                raise TeamRejected("agent_type_unavailable")
            definition_json = definition.model_dump_json()
            if definition.model is not None:
                from eidos_runtime.application.agent_catalog import AgentCatalog
                directory = self.store.database.data_directory
                if directory is None:
                    raise TeamRejected("agent_model_unavailable")
                try:
                    profile = AgentCatalog(directory).resolve_model(definition, self.store.read_model_profile(lead_run_id))
                except Exception as error:
                    raise TeamRejected("agent_model_unavailable") from error
                model = MemberModel(model_id=profile.model_id, reasoning_selection=profile.reasoning_selection)
        workspace = None
        if member.workspace == "worktree":
            workspaces = self._workspaces()
            if workspaces is None:
                raise TeamRejected("agent_worktree_unavailable")
            try:
                workspace = workspaces.prepare(lead_session_id, seed)
            except AgentWorkspaceError as error:
                raise TeamRejected(error.code) from error
            session_id = workspace.session_id
        else:
            session_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "eidos:team-member-session:" + seed))
        forked = self.repository.lead_turns(lead_session_id, member.context_turns) if member.context_turns else None
        return (PreparedMember(member, session_id, model, definition_json,
                               workspace.worktree_id if workspace else None, workspace.base_tree if workspace else None,
                               forked), workspace)

    def _finish_workspaces(self, team: TeamView, prepared: list[tuple[PreparedMember, PreparedAgentWorkspace | None]]) -> None:
        workspaces = self._workspaces()
        if workspaces is None:
            return
        ids = {member.name: member.id for member in team.members}
        for value, workspace in prepared:
            if workspace is not None and value.member.name in ids:
                workspaces.pin_base(workspace, ids[value.member.name])
                workspaces.complete(workspace)

    def _template_json(self, template: str | None) -> str | None:
        """Freeze the template into the team row; later catalog edits do not reach a running team."""
        if template is None:
            return None
        return TeamTemplateCatalog(self.store.database.data_directory).get(template).model_dump_json()

    # Members and deliveries ------------------------------------------------------------

    def member_owner(self, team_id: str, name: str) -> WorkspaceOwner:
        row = self.repository.member_row(team_id, name, active_only=False)
        if row["worktree_id"] is None:
            raise TeamRejected("agent_workspace_shared")
        active = bool(self.repository.active_runs(team_id, member_id=row["id"]))
        return WorkspaceOwner("team_member", row["id"], row["worktree_id"], row["base_tree"], active)

    def remove_member(self, team_id: str, name: str, reason: str, actor: Actor, *, discard_changes: bool) -> TeamView:
        row = self.repository.member_row(team_id, name)
        workspaces = self._workspaces()
        if row["worktree_id"] is not None and workspaces is not None and not discard_changes:
            if self.repository.active_runs(team_id, member_id=row["id"]):
                raise TeamRejected("agent_still_running")
            try:
                delivery = workspaces.capture_owner(self.member_owner(team_id, name))
            except AgentWorkspaceError:
                delivery = None
            if delivery is not None and delivery.entries and delivery.status not in {"integrated"}:
                raise TeamRejected("member_has_unintegrated_changes")
        self.repository.remove_member(team_id, name, reason, actor)
        for run_id in self.repository.active_runs(team_id, member_id=row["id"]):
            self._cancel(run_id)
        if row["worktree_id"] is not None and workspaces is not None:
            try:
                workspaces.discard_owner(WorkspaceOwner("team_member", row["id"], row["worktree_id"], row["base_tree"], False))
            except AgentWorkspaceError:
                logger.warning("Removed member Worktree cleanup deferred member_id=%s", row["id"])
        self._publish()
        self._schedule()
        return self.repository.team(team_id)

    # Desktop -----------------------------------------------------------------------

    def session_state(self, session_id: str) -> TeamSessionState:
        team = self.repository.team_for_session(session_id)
        return TeamSessionState(team=team, proposal=self.repository.pending_proposal(session_id),
                                usage=self.repository.usage(team.id) if team else [])

    def member_changes(self, team_id: str, name: str, *, path: str | None, offset: int, max_bytes: int) -> TeamMemberChanges:
        """Read-only view of a Worktree member's delivery; integration stays with the lead's tool."""
        workspaces = self._workspaces()
        if workspaces is None:
            raise TeamRejected("agent_worktree_unavailable")
        try:
            delivery = workspaces.capture_owner(self.member_owner(team_id, name))
            if path is not None and path not in {entry.path for entry in delivery.entries}:
                raise TeamRejected("agent_change_path_unknown")
            diff, next_offset, has_more = workspaces.diff_page(delivery, path=path, offset=offset, max_bytes=max_bytes)
        except AgentWorkspaceError as error:
            raise TeamRejected(error.code) from error
        return TeamMemberChanges(member=name, status=delivery.status, files=[
            AgentChangedFile(path=entry.path, kind=entry.kind, additions=entry.additions, deletions=entry.deletions,
                             binary=entry.binary) for entry in delivery.entries],
            diff=diff, diff_next_offset=next_offset, diff_has_more=has_more)

    def decide(self, proposal_id: str, operation_id: str, decision: RosterDecision) -> TeamProposalView:
        view = self.repository.decide(proposal_id, operation_id, decision)
        self._publish()
        self._schedule()
        return view

    def post(self, team_id: str, operation_id: str, request: TeamPostRequest) -> TeamView:
        self.repository.post(team_id, USER_ACTOR, request, operation_id=operation_id)
        self._publish()
        self.wake_ready_soon()
        return self.repository.team(team_id)

    def task_update(self, team_id: str, request: TeamTaskRequest) -> TeamView:
        if request.op not in {"accept", "reject", "cancel", "assign", "release", "create", "update", "reopen"}:
            raise TeamRejected("team_task_forbidden")
        self.repository.task_op(team_id, USER_ACTOR, request)
        self._publish()
        self.wake_ready_soon()
        return self.repository.team(team_id)

    def set_status(self, team_id: str, status: str) -> TeamView:
        if status == "dissolving":
            return self.dissolve(team_id)
        team = self.repository.set_status(team_id, status)
        self._publish()
        if status == "active":
            self.wake_ready_soon()
        return team

    def stop_members(self, team_id: str) -> TeamView:
        for run_id in self.repository.active_runs(team_id):
            self._cancel(run_id)
        self._publish()
        return self.repository.team(team_id)

    def update_settings(self, team_id: str, *, auto_wake_lead: bool | None = None,
                        run_settings: TeamRunSettings | None = None) -> TeamView:
        team = self.repository.update_settings(team_id, auto_wake_lead=auto_wake_lead, run_settings=run_settings)
        self._publish()
        return team

    def dissolve(self, team_id: str) -> TeamView:
        self.repository.set_status(team_id, "dissolving")
        for run_id in self.repository.active_runs(team_id):
            self._cancel(run_id)
        self._publish()
        self.finish_dissolving()
        return self.repository.team(team_id)

    def finish_dissolving(self) -> None:
        """Remove member Worktrees once a dissolving team's Runs are quiet."""
        with self.store.database.lock:
            teams = [row[0] for row in self.store.database.connection().execute(
                "SELECT id FROM teams WHERE status='dissolving'")]
        for team_id in teams:
            if self.repository.active_runs(team_id):
                continue
            workspaces = self._workspaces()
            if workspaces is not None:
                with self.store.database.lock:
                    rows = self.store.database.connection().execute(
                        "SELECT id,worktree_id,base_tree FROM team_members WHERE team_id=? AND worktree_id IS NOT NULL", (team_id,)).fetchall()
                for row in rows:
                    try:
                        workspaces.discard_owner(WorkspaceOwner("team_member", row["id"], row["worktree_id"], row["base_tree"], False))
                    except AgentWorkspaceError:
                        logger.warning("Dissolved team Worktree cleanup deferred member_id=%s", row["id"])
            self.repository.set_status(team_id, "dissolved")
            self._publish()

    # Wakes ---------------------------------------------------------------------------

    start_wake: Callable[[ReadyWake], None] | None = None

    def wake_ready_soon(self) -> None:
        self._schedule()
        if self.start_wake is None:
            return
        for wake in self.repository.ready_wakes():
            self.start_wake(wake)

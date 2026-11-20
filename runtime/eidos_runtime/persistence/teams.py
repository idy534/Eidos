"""SQLite authority for Agent Team coordination.

Every notification reaches its target as a message. A message addressed by
name to an idle target writes one pending wake intent in the same transaction;
``all`` never wakes anyone. Wakes are therefore edge-triggered: they come from
a message write or a Run's terminal transition, never from re-evaluating state.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from eidos_runtime.db.database import Database, now_ms
from eidos_runtime.db.events import append_event
from eidos_runtime.domain.collaboration import ACTIVE_STATUSES
from eidos_runtime.domain.teams import (
    MAX_ACTIVE_MEMBER_RUNS, MAX_DELIVERED_MESSAGES, MAX_TEAM_MEMBERS, MAX_TEAM_MESSAGE_BYTES, MAX_TEAM_TASKS,
    MAX_FORKED_CONTEXT_BYTES, MESSAGE_PREVIEW_CHARACTERS, PING_PONG_LIMIT, RESERVED_MEMBER_NAMES,
    EvidenceRef, MemberModel, ProposedMember, ProposedTask, RosterDecision, TeamMemberView, TeamMessagePage,
    TeamMessageView, TeamPostRequest, TeamProposal, TeamProposalView, TeamRejected, TeamRunSettings,
    TeamRunOrigin, TeamTaskRequest, TeamTaskView, TeamUsage, TeamView, reject_cycles,
)
from eidos_runtime.db.transitions import transition_run
from eidos_runtime.domain.run import RunStatus as DomainRunStatus
from eidos_runtime.runtime.state_machine import EventType, RunStatus

LEAD = "lead"
USER = "user"
_ACTIVE_SQL = "(" + ",".join(f"'{status}'" for status in ACTIVE_STATUSES) + ")"


@dataclass(frozen=True)
class Actor:
    kind: Literal["lead", "member", "user", "system"]
    name: str
    member_id: str | None = None

    @property
    def key(self) -> str:
        return self.member_id if self.member_id is not None else self.kind


USER_ACTOR = Actor("user", USER)
SYSTEM_ACTOR = Actor("system", "system")


@dataclass(frozen=True)
class RunTeamRole:
    role: Literal["lead", "member", "lead_pending"]
    team_id: str | None
    session_id: str
    member_id: str | None = None
    member_name: str | None = None

    @property
    def actor(self) -> Actor:
        if self.role == "member":
            return Actor("member", self.member_name or "", self.member_id)
        return Actor("lead", LEAD)

    @property
    def recipient(self) -> str:
        return self.member_id if self.role == "member" and self.member_id else LEAD


@dataclass(frozen=True)
class PreparedMember:
    """A member whose Session binding was prepared before the team commits."""

    member: ProposedMember
    session_id: str
    model: MemberModel
    definition_json: str | None = None
    worktree_id: str | None = None
    base_tree: str | None = None
    forked_context: str | None = None


@dataclass(frozen=True)
class PendingProposalCall:
    item_id: str


@dataclass(frozen=True)
class ReadyWake:
    wake_id: str
    team_id: str
    target: str
    session_id: str
    model_id: str
    reasoning_selection: str | None
    approval_mode: str
    full_access_confirmation: str | None


@dataclass(frozen=True)
class RunTeamContext:
    role: RunTeamRole
    team: TeamView | None
    member: TeamMemberView | None
    messages: tuple[TeamMessageView, ...]
    proposal: TeamProposalView | None = None
    # Frozen at member creation and at roster confirmation: stable instruction text.
    forked_context: str | None = None
    template_guidance: str | None = None


class TeamRepository:
    def __init__(self, database: Database) -> None:
        self.database = database

    # Roles and reads ------------------------------------------------------------

    def role_for_run(self, run_id: str) -> RunTeamRole | None:
        with self.database.lock:
            return self._role(self.database.connection(), run_id)

    @staticmethod
    def _role(connection: sqlite3.Connection, run_id: str) -> RunTeamRole | None:
        run = connection.execute("SELECT session_id FROM runs WHERE id=?", (run_id,)).fetchone()
        if run is None:
            return None
        session_id = run["session_id"]
        member = connection.execute(
            "SELECT m.id,m.name,m.team_id FROM team_members m JOIN teams t ON t.id=m.team_id "
            "WHERE m.session_id=? AND m.status='active' AND t.status IN ('active','paused')", (session_id,)).fetchone()
        if member is not None:
            return RunTeamRole("member", member["team_id"], session_id, member["id"], member["name"])
        team = connection.execute(
            "SELECT id FROM teams WHERE lead_session_id=? AND status IN ('active','paused')", (session_id,)).fetchone()
        if team is not None:
            return RunTeamRole("lead", team["id"], session_id)
        if connection.execute("SELECT 1 FROM team_runs WHERE run_id=? AND origin='lead'", (run_id,)).fetchone():
            return RunTeamRole("lead_pending", None, session_id)
        return None

    def mark_lead_mode(self, connection: sqlite3.Connection, run_id: str) -> None:
        connection.execute(
            "INSERT OR IGNORE INTO team_runs (run_id,team_id,member_id,wake_id,origin) VALUES (?,NULL,NULL,NULL,'lead')",
            (run_id,))

    def team_for_session(self, session_id: str) -> TeamView | None:
        with self.database.lock:
            connection = self.database.connection()
            row = self._open_team(connection, session_id)
            return self._team_view(connection, row) if row else None

    def team(self, team_id: str) -> TeamView:
        with self.database.lock:
            connection = self.database.connection()
            return self._team_view(connection, self._team_row(connection, team_id))

    def pending_proposal(self, session_id: str) -> TeamProposalView | None:
        with self.database.lock:
            row = self.database.connection().execute(
                "SELECT * FROM team_proposals WHERE session_id=? AND decision='pending' ORDER BY created_at DESC LIMIT 1",
                (session_id,)).fetchone()
            return self._proposal_view(row) if row else None

    def context_for_run(self, run_id: str) -> RunTeamContext | None:
        with self.database.lock:
            connection = self.database.connection()
            role = self._role(connection, run_id)
            if role is None:
                return None
            if role.team_id is None:
                proposal = connection.execute(
                    "SELECT * FROM team_proposals WHERE run_id=? ORDER BY created_at DESC LIMIT 1", (run_id,)).fetchone()
                return RunTeamContext(role, None, None, (), self._proposal_view(proposal) if proposal else None)
            row = self._team_row(connection, role.team_id)
            team = self._team_view(connection, row)
            member = next((value for value in team.members if value.id == role.member_id), None)
            messages = self._undelivered(connection, role.team_id, role.recipient, MAX_DELIVERED_MESSAGES)
            forked = connection.execute("SELECT forked_context FROM team_members WHERE id=?", (role.member_id,)).fetchone() \
                if role.member_id else None
            guidance = self._template(row).get("guidance")
            return RunTeamContext(role, team, member, messages, forked_context=forked[0] if forked else None,
                                  template_guidance=guidance if isinstance(guidance, str) and guidance else None)

    def has_undelivered_for_run(self, run_id: str) -> bool:
        with self.database.lock:
            connection = self.database.connection()
            role = self._role(connection, run_id)
            return bool(role and role.team_id and self._has_undelivered(connection, role.team_id, role.recipient))

    @classmethod
    def has_undelivered_in_connection(cls, connection: sqlite3.Connection, run_id: str) -> bool:
        role = cls._role(connection, run_id)
        return bool(role and role.team_id and cls._has_undelivered(connection, role.team_id, role.recipient))

    def member_session(self, team_id: str, name: str) -> str:
        with self.database.lock:
            row = self._member(self.database.connection(), team_id, name)
            return str(row["session_id"])

    def member_row(self, team_id: str, name: str, *, active_only: bool = True) -> sqlite3.Row:
        with self.database.lock:
            return self._member(self.database.connection(), team_id, name, active_only=active_only)

    def active_runs(self, team_id: str, *, member_id: str | None = None) -> tuple[str, ...]:
        with self.database.lock:
            return tuple(row[0] for row in self.database.connection().execute(
                "SELECT r.id FROM runs r JOIN team_members m ON m.session_id=r.session_id "
                f"WHERE m.team_id=? AND (? IS NULL OR m.id=?) AND r.status IN {_ACTIVE_SQL}",
                (team_id, member_id, member_id)))

    def lead_turns(self, session_id: str, turns: int) -> str | None:
        """The lead's latest turns as bounded text, frozen into a member at creation.

        Only completed user inputs and final answers are copied; tool output,
        approvals and reasoning stay in the lead Session.
        """
        with self.database.lock:
            rows = self.database.connection().execute(
                "SELECT kind,substr(content,1,?) FROM items WHERE session_id=? AND status='completed' AND incomplete=0 "
                "AND kind IN ('user_message','assistant_message') AND content IS NOT NULL AND content!='' "
                "ORDER BY creation_seq DESC LIMIT ?",
                (MESSAGE_PREVIEW_CHARACTERS, session_id, turns * 2)).fetchall()
        parts: list[str] = []
        size = 0
        for kind, content in rows:
            text = f"{'User' if kind == 'user_message' else 'Lead'}: {content}"
            size += len(text.encode("utf-8"))
            if size > MAX_FORKED_CONTEXT_BYTES:
                break
            parts.append(text)
        return "\n\n".join(reversed(parts)) or None

    def usage(self, team_id: str) -> list[TeamUsage]:
        """Per-member totals over the member Session and every subagent below it.

        One attempt without reported usage marks that member incomplete; missing
        values are never counted as zero (same rule as goal and agent usage).
        """
        with self.database.lock:
            connection = self.database.connection()
            members = connection.execute(
                "SELECT name,session_id FROM team_members WHERE team_id=? ORDER BY created_at,name", (team_id,)).fetchall()
            values = []
            for member in members:
                tree = ("WITH RECURSIVE tree(id) AS (SELECT ? UNION SELECT d.child_session_id FROM agent_delegations d "
                        "JOIN runs p ON p.id=d.parent_run_id JOIN tree t ON t.id=p.session_id) ")
                tokens = connection.execute(
                    tree + "SELECT coalesce(sum(json_extract(a.usage_json,'$.input_tokens')),0),"
                    "coalesce(sum(json_extract(a.usage_json,'$.output_tokens')),0),"
                    "coalesce(sum(json_extract(a.usage_json,'$.cache_read_tokens')),0),"
                    "coalesce(sum(json_extract(a.usage_json,'$.cache_write_tokens')),0),"
                    "coalesce(max(a.usage_json IS NULL OR json_extract(a.usage_json,'$.input_tokens') IS NULL "
                    "OR json_extract(a.usage_json,'$.output_tokens') IS NULL),0) "
                    "FROM model_attempts a JOIN steps s ON s.id=a.step_id JOIN runs r ON r.id=s.run_id "
                    "WHERE r.session_id IN (SELECT id FROM tree)", (member["session_id"],)).fetchone()
                effective = connection.execute(
                    tree + "SELECT coalesce(sum(total_effective_ms),0) FROM runs WHERE session_id IN (SELECT id FROM tree)",
                    (member["session_id"],)).fetchone()[0]
                values.append(TeamUsage(member=member["name"], input_tokens=tokens[0], output_tokens=tokens[1],
                                        cache_read_tokens=tokens[2], cache_write_tokens=tokens[3],
                                        incomplete=bool(tokens[4]), effective_ms=effective))
            return values

    def task_row(self, team_id: str, key: str) -> sqlite3.Row:
        with self.database.lock:
            return self._task(self.database.connection(), team_id, key)

    # Proposals ------------------------------------------------------------------

    def propose(self, run_id: str, item_id: str, proposal: TeamProposal, *, suspend: bool = True) -> TeamProposalView:
        with self.database.transaction() as connection:
            existing = connection.execute("SELECT * FROM team_proposals WHERE item_id=?", (item_id,)).fetchone()
            if existing is not None:
                if TeamProposal.model_validate_json(existing["proposal_json"]) != proposal:
                    raise TeamRejected("team_operation_conflict")
                return self._proposal_view(existing)
            run = connection.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if run is None or run["status"] != "running" or run["cancel_requested_at"] is not None:
                raise TeamRejected("team_proposal_inactive")
            if self._open_team(connection, run["session_id"]) is not None:
                raise TeamRejected("team_already_active")
            connection.execute(
                "UPDATE team_proposals SET decision='declined',decided_at=? WHERE session_id=? AND decision='pending'",
                (now_ms(), run["session_id"]))
            proposal_id = str(uuid.uuid4())
            connection.execute(
                "INSERT INTO team_proposals (id,session_id,run_id,item_id,proposal_json,decision,created_at) VALUES (?,?,?,?,?,'pending',?)",
                (proposal_id, run["session_id"], run_id, item_id, proposal.model_dump_json(), now_ms()))
            if suspend:
                transition_run(connection, run_id, frozenset({RunStatus.RUNNING}), RunStatus.WAITING_INPUT, "team_roster_requested")
            self._event(connection, run["session_id"], None, "proposal")
            return self._proposal_view(connection.execute("SELECT * FROM team_proposals WHERE id=?", (proposal_id,)).fetchone())

    def origins_for_runs(self, run_ids: list[str]) -> dict[str, TeamRunOrigin]:
        if not run_ids:
            return {}
        with self.database.lock:
            rows = self.database.connection().execute(
                "SELECT r.run_id,r.origin,m.name FROM team_runs r LEFT JOIN team_members m ON m.id=r.member_id "
                "WHERE r.origin IN ('wake','user_message') AND r.run_id IN (SELECT value FROM json_each(?))",
                (json.dumps(run_ids),)).fetchall()
        return {row["run_id"]: TeamRunOrigin(origin=row["origin"], member=row["name"]) for row in rows}

    def automatic_origin(self, run_id: str) -> str | None:
        """wake or user_message for Runs a team admitted without a human input item."""
        with self.database.lock:
            row = self.database.connection().execute(
                "SELECT origin FROM team_runs WHERE run_id=? AND origin IN ('wake','user_message')", (run_id,)).fetchone()
        return row["origin"] if row else None

    def unfinished_proposal(self, run_id: str) -> PendingProposalCall | None:
        """The team_propose ToolCall this Run must resume, if it is still running."""
        with self.database.lock:
            row = self.database.connection().execute(
                "SELECT p.item_id FROM team_proposals p JOIN tool_calls t ON t.item_id=p.item_id "
                "WHERE p.run_id=? AND t.status='running' ORDER BY p.created_at DESC LIMIT 1", (run_id,)).fetchone()
        return PendingProposalCall(row["item_id"]) if row else None

    def proposal_for_item(self, item_id: str) -> tuple[TeamProposalView, RosterDecision | None] | None:
        with self.database.lock:
            row = self.database.connection().execute("SELECT * FROM team_proposals WHERE item_id=?", (item_id,)).fetchone()
            if row is None:
                return None
            decision = RosterDecision.model_validate_json(row["decision_json"]) if row["decision_json"] else None
            return self._proposal_view(row), decision

    def decide(self, proposal_id: str, operation_id: str, decision: RosterDecision) -> TeamProposalView:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM team_proposals WHERE id=?", (proposal_id,)).fetchone()
            if row is None:
                raise TeamRejected("team_proposal_not_found")
            if row["operation_id"] == operation_id:
                if row["decision_json"] != decision.model_dump_json():
                    raise TeamRejected("team_operation_conflict")
                return self._proposal_view(row)
            if row["decision"] != "pending" or row["decision_json"] is not None:
                raise TeamRejected("team_proposal_inactive")
            run = connection.execute("SELECT status,cancel_requested_at FROM runs WHERE id=?", (row["run_id"],)).fetchone()
            if run is None or run["status"] not in ACTIVE_STATUSES or run["cancel_requested_at"] is not None:
                raise TeamRejected("team_proposal_inactive")
            proposal = TeamProposal.model_validate_json(row["proposal_json"])
            names = {member.name for member in proposal.members}
            if not set(decision.removed_members) <= names or not set(decision.workspaces) <= names:
                raise TeamRejected("team_member_not_found")
            if decision.confirmed and not names - set(decision.removed_members):
                raise TeamRejected("team_member_limit")
            connection.execute(
                "UPDATE team_proposals SET decision_json=?,operation_id=?,decision=CASE WHEN ? THEN decision ELSE 'declined' END,decided_at=? WHERE id=?",
                (decision.model_dump_json(), operation_id, int(decision.confirmed), now_ms(), proposal_id))
            # The waiting lead Run resumes its team_propose ToolCall, which executes the decision.
            if connection.execute("SELECT status FROM runs WHERE id=?", (row["run_id"],)).fetchone()["status"] == "waiting_input":
                transition_run(connection, row["run_id"], frozenset({RunStatus.WAITING_INPUT}), RunStatus.QUEUED, "team_roster_decided")
            self._event(connection, row["session_id"], None, "proposal")
            return self._proposal_view(connection.execute("SELECT * FROM team_proposals WHERE id=?", (proposal_id,)).fetchone())

    # Creation ---------------------------------------------------------------------

    def create_team(self, proposal_id: str, lead_run_id: str, settings: TeamRunSettings, auto_wake_lead: bool,
                    members: list[PreparedMember], tasks: list[ProposedTask], *, template_json: str | None = None) -> TeamView:
        with self.database.transaction() as connection:
            proposal = connection.execute("SELECT * FROM team_proposals WHERE id=?", (proposal_id,)).fetchone()
            if proposal is None:
                raise TeamRejected("team_proposal_not_found")
            if proposal["team_id"] is not None:
                return self._team_view(connection, self._team_row(connection, proposal["team_id"]))
            lead_session = proposal["session_id"]
            if self._open_team(connection, lead_session) is not None:
                raise TeamRejected("team_already_active")
            objective = TeamProposal.model_validate_json(proposal["proposal_json"]).objective
            team_id = str(uuid.uuid4())
            now = now_ms()
            connection.execute(
                "INSERT INTO teams (id,lead_session_id,status,objective,run_settings_json,auto_wake_lead,template_json,created_at,updated_at) "
                "VALUES (?,?,'active',?,?,?,?,?,?)",
                (team_id, lead_session, objective, settings.model_dump_json(), int(auto_wake_lead), template_json, now, now))
            for prepared in members:
                self._insert_member(connection, team_id, lead_session, prepared)
            names = {row["name"]: row["id"] for row in connection.execute("SELECT id,name FROM team_members WHERE team_id=?", (team_id,))}
            for task in tasks:
                self._insert_task(connection, team_id, task, created_by=LEAD, names=names)
            for task in tasks:
                for dependency in task.depends_on:
                    connection.execute(
                        "INSERT INTO team_task_deps SELECT a.id,b.id FROM team_tasks a,team_tasks b WHERE a.team_id=? AND a.key=? AND b.team_id=? AND b.key=?",
                        (team_id, task.key, team_id, dependency))
            for milestone in dict.fromkeys(task.milestone for task in tasks if task.milestone):
                self._ensure_gates(connection, team_id, milestone)
            for task in tasks:
                self._advance_assigned(connection, team_id, task.key)
            connection.execute("UPDATE team_proposals SET decision='confirmed',team_id=? WHERE id=?", (team_id, proposal_id))
            connection.execute("UPDATE team_runs SET team_id=? WHERE run_id=? AND team_id IS NULL", (team_id, lead_run_id))
            team = self._team_row(connection, team_id)
            for member_id in names.values():
                self._system(connection, team, "You joined the team. Read your brief, the task board and the roster, then start.",
                             [member_id])
            self._event(connection, lead_session, team_id, "team")
            return self._team_view(connection, team)

    def add_member(self, team_id: str, prepared: PreparedMember) -> TeamMemberView:
        with self.database.transaction() as connection:
            team = self._team_row(connection, team_id, writable=True)
            existing = connection.execute("SELECT * FROM team_members WHERE team_id=? AND name=?", (team_id, prepared.member.name)).fetchone()
            if existing is not None:
                if existing["session_id"] == prepared.session_id:
                    return self._member_view(connection, existing)
                raise TeamRejected("team_member_name_exists")
            self._insert_member(connection, team_id, team["lead_session_id"], prepared)
            row = connection.execute("SELECT * FROM team_members WHERE team_id=? AND name=?", (team_id, prepared.member.name)).fetchone()
            self._system(connection, team, f"{prepared.member.name} joined the team as {prepared.member.role}.", [row["id"]], to_all_also=True)
            self._event(connection, team["lead_session_id"], team_id, "member")
            return self._member_view(connection, row)

    def _insert_member(self, connection: sqlite3.Connection, team_id: str, lead_session: str, prepared: PreparedMember) -> None:
        count = connection.execute("SELECT COUNT(*) FROM team_members WHERE team_id=? AND status='active'", (team_id,)).fetchone()[0]
        if count >= MAX_TEAM_MEMBERS:
            raise TeamRejected("team_member_limit")
        if prepared.member.name in RESERVED_MEMBER_NAMES:
            raise TeamRejected("team_member_name_invalid")
        now = now_ms()
        worktree_id = prepared.worktree_id
        connection.execute(
            "INSERT INTO sessions (id,workspace_root,workspace_dev,workspace_inode,workspace_uid,title,created_at,updated_at,"
            "worktree_id,execution_mode,associated_worktree_id,project_id,workspace_roots_json,internal_kind) "
            "SELECT ?,workspace_root,workspace_dev,workspace_inode,workspace_uid,?,?,?,coalesce(?,worktree_id),"
            "CASE WHEN ? IS NULL THEN execution_mode ELSE 'worktree' END,?,project_id,workspace_roots_json,'team_member' "
            "FROM sessions WHERE id=?",
            (prepared.session_id, prepared.member.name, now, now, worktree_id, worktree_id, worktree_id, lead_session))
        connection.execute(
            "INSERT INTO team_members (id,team_id,name,role,brief,definition_json,model_json,session_id,workspace_mode,worktree_id,base_tree,status,forked_context,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,'active',?,?)",
            (str(uuid.uuid4()), team_id, prepared.member.name, prepared.member.role, prepared.member.brief,
             prepared.definition_json, prepared.model.model_dump_json(), prepared.session_id,
             prepared.member.workspace, worktree_id, prepared.base_tree, prepared.forked_context, now))

    def remove_member(self, team_id: str, name: str, reason: str, actor: Actor) -> sqlite3.Row:
        with self.database.transaction() as connection:
            team = self._team_row(connection, team_id)
            member = self._member(connection, team_id, name, active_only=False)
            if member["status"] == "removed":
                return member
            now = now_ms()
            connection.execute("UPDATE team_members SET status='removed',removed_at=? WHERE id=?", (now, member["id"]))
            connection.execute(
                "UPDATE team_tasks SET status='open',owner_member_id=NULL,version=version+1,updated_at=? "
                "WHERE team_id=? AND owner_member_id=? AND status IN ('in_progress','in_review')", (now, team_id, member["id"]))
            connection.execute(
                "UPDATE team_tasks SET reviewer=NULL,version=version+1,updated_at=? WHERE team_id=? AND reviewer=?",
                (now, team_id, member["id"]))
            connection.execute("UPDATE team_wakes SET status='obsolete',updated_at=? WHERE team_id=? AND target=? AND status='pending'",
                               (now, team_id, member["id"]))
            self._system(connection, team, f"{name} left the team ({actor.name}): {reason}", [LEAD] if actor.kind != "lead" else [])
            self._event(connection, team["lead_session_id"], team_id, "member")
            return connection.execute("SELECT * FROM team_members WHERE id=?", (member["id"],)).fetchone()

    # Tasks -----------------------------------------------------------------------

    def task_op(self, team_id: str, actor: Actor, request: TeamTaskRequest, *,
                evidence_session_ids: frozenset[str] = frozenset(), delivery_id: str | None = None) -> TeamTaskView:
        with self.database.transaction() as connection:
            team = self._team_row(connection, team_id, writable=True)
            names = self._names(connection, team_id)
            if request.op == "create":
                if actor.kind == "member" and request.owner not in (None, actor.name):
                    raise TeamRejected("team_task_forbidden")
                if connection.execute("SELECT COUNT(*) FROM team_tasks WHERE team_id=?", (team_id,)).fetchone()[0] >= MAX_TEAM_TASKS:
                    raise TeamRejected("team_task_limit")
                if connection.execute("SELECT 1 FROM team_tasks WHERE team_id=? AND key=?", (team_id, request.key)).fetchone():
                    raise TeamRejected("team_task_key_exists")
                review_default = self._template(team).get("review_required", False) if request.kind != "gate" else False
                try:
                    task = ProposedTask(key=request.key, title=request.title or request.key, description=request.description or "",
                        acceptance=request.acceptance or "", owner=request.owner, reviewer=request.reviewer,
                        depends_on=request.depends_on or [],
                        review_required=review_default if request.review_required is None else request.review_required,
                        milestone=request.milestone, paths=request.paths or [], kind=request.kind or "work")
                except ValueError as error:
                    raise TeamRejected("team_gate_milestone_required") from error
                self._insert_task(connection, team_id, task, created_by=actor.name, names={value: key for key, value in names.items()})
                self._set_dependencies(connection, team_id, request.key, task.depends_on)
                if task.milestone and task.kind == "work":
                    self._ensure_gates(connection, team_id, task.milestone)
                self._advance_assigned(connection, team_id, request.key, actor=actor)
                row = self._task(connection, team_id, request.key)
            else:
                row = self._task(connection, team_id, request.key)
                row = self._transition(connection, team, row, actor, request, names, evidence_session_ids, delivery_id)
            self._event(connection, team["lead_session_id"], team_id, "task")
            return self._task_view(connection, row, names)

    def _transition(self, connection: sqlite3.Connection, team: sqlite3.Row, row: sqlite3.Row, actor: Actor,
                    request: TeamTaskRequest, names: dict[str, str], evidence_sessions: frozenset[str],
                    delivery_id: str | None) -> sqlite3.Row:
        team_id = team["id"]
        manager = actor.kind in {"lead", "user"}
        owner = row["owner_member_id"]
        now = now_ms()
        key = row["key"]

        def update(sql: str, values: tuple[object, ...], expected: str | tuple[str, ...]) -> None:
            states = (expected,) if isinstance(expected, str) else expected
            changed = connection.execute(
                f"UPDATE team_tasks SET {sql},version=version+1,updated_at=? WHERE id=? AND status IN ({','.join('?' * len(states))})",
                (*values, now, row["id"], *states))
            if changed.rowcount != 1:
                raise TeamRejected("team_task_state_conflict")

        if request.op == "update":
            if not manager and owner != actor.member_id:
                raise TeamRejected("team_task_forbidden")
            if request.version != row["version"]:
                raise TeamRejected("team_task_version_conflict")
            fields: dict[str, object] = {}
            for field, column in (("title", "title"), ("description", "description"), ("acceptance", "acceptance"),
                                  ("milestone", "milestone"), ("note", "note")):
                value = getattr(request, field)
                if value is not None:
                    fields[column] = value
            if request.paths is not None:
                fields["paths_json"] = json.dumps(request.paths)
            if manager and request.review_required is not None:
                fields["review_required"] = int(request.review_required)
            if manager and request.reviewer is not None:
                fields["reviewer"] = self._reviewer_key(request.reviewer, names)
            if fields:
                assignments = ",".join(f"{column}=?" for column in fields)
                update(assignments, tuple(fields.values()), ("open", "in_progress", "in_review", "done", "canceled"))
            if manager and request.depends_on is not None:
                self._set_dependencies(connection, team_id, key, request.depends_on)
        elif request.op == "assign":
            if not manager:
                raise TeamRejected("team_task_forbidden")
            member_id = self._member_id(request.owner, names)
            update("owner_member_id=?", (member_id,), ("open", "in_progress"))
            connection.execute("UPDATE team_tasks SET status='open' WHERE id=? AND status='in_progress'", (row["id"],))
            self._advance_assigned(connection, team_id, key, actor=actor)
        elif request.op == "claim":
            if actor.kind != "member":
                raise TeamRejected("team_task_forbidden")
            if self._blocked(connection, row["id"]):
                raise TeamRejected("team_task_blocked")
            changed = connection.execute(
                "UPDATE team_tasks SET status='in_progress',owner_member_id=?,version=version+1,updated_at=? "
                "WHERE id=? AND status='open' AND (owner_member_id IS NULL OR owner_member_id=?)",
                (actor.member_id, now, row["id"], actor.member_id))
            if changed.rowcount != 1:
                raise TeamRejected("team_task_state_conflict")
        elif request.op == "submit":
            if owner != actor.member_id and not manager:
                raise TeamRejected("team_task_forbidden")
            evidence = list(request.evidence)
            if row["review_required"] and not evidence and delivery_id is None:
                raise TeamRejected("team_evidence_required")
            self._validate_evidence(connection, evidence, evidence_sessions)
            target = "in_review" if row["review_required"] else "done"
            update("status=?,evidence_json=?,delivery_id=coalesce(?,delivery_id),note=coalesce(?,note)",
                   (target, json.dumps([value.model_dump(mode="json") for value in evidence]), delivery_id, request.note), "in_progress")
            if target == "in_review":
                reviewer = row["reviewer"] or LEAD
                self._system(connection, team, f"Task {key} is ready for your review: {row['title']}.", [reviewer], task_id=row["id"], actor=actor)
            else:
                self._after_terminal(connection, team, row, actor)
        elif request.op in {"accept", "reject"}:
            reviewer = row["reviewer"] or LEAD
            allowed = actor.kind == "user" or actor.key == reviewer or (actor.kind == "lead" and reviewer == LEAD)
            if not allowed:
                raise TeamRejected("team_task_forbidden")
            if request.op == "accept":
                update("status='done',note=coalesce(?,note)", (request.note,), "in_review")
                self._after_terminal(connection, team, row, actor)
            else:
                if not request.note:
                    raise TeamRejected("team_task_reason_required")
                update("status='in_progress',note=?", (request.note,), "in_review")
                if owner is not None:
                    self._system(connection, team, f"Task {key} was sent back: {request.note}", [owner], task_id=row["id"], actor=actor)
        elif request.op == "release":
            if not manager and owner != actor.member_id:
                raise TeamRejected("team_task_forbidden")
            update("status='open',owner_member_id=NULL,note=coalesce(?,note)", (request.note,), ("in_progress", "in_review"))
            if actor.kind == "member":
                self._system(connection, team, f"{actor.name} released task {key}: {request.note or 'no reason given'}", [LEAD], task_id=row["id"], actor=actor)
        elif request.op == "reopen":
            self._reopen(connection, team, row, actor, request.note)
        elif request.op == "cancel":
            if not manager:
                raise TeamRejected("team_task_forbidden")
            update("status='canceled',note=coalesce(?,note)", (request.note,), ("open", "in_progress", "in_review"))
            if owner is not None:
                self._system(connection, team, f"Task {key} was canceled.", [owner], task_id=row["id"], actor=actor)
            self._after_terminal(connection, team, row, actor)
        else:
            raise TeamRejected("team_task_forbidden")
        return connection.execute("SELECT * FROM team_tasks WHERE id=?", (row["id"],)).fetchone()

    def _after_terminal(self, connection: sqlite3.Connection, team: sqlite3.Row, row: sqlite3.Row, actor: Actor) -> None:
        team_id = team["id"]
        for dependent in connection.execute(
                "SELECT t.key FROM team_tasks t JOIN team_task_deps d ON d.task_id=t.id WHERE d.depends_on_task_id=? AND t.status='open'",
                (row["id"],)).fetchall():
            self._advance_assigned(connection, team_id, dependent["key"], actor=actor)
        if row["milestone"] is not None and row["kind"] == "work":
            # The last work task of a milestone opens its gates.
            for gate in connection.execute(
                    "SELECT key FROM team_tasks WHERE team_id=? AND kind='gate' AND milestone=? AND status='open'",
                    (team_id, row["milestone"])).fetchall():
                self._advance_assigned(connection, team_id, gate["key"], actor=actor)
        remaining = connection.execute(
            "SELECT COUNT(*) FROM team_tasks WHERE team_id=? AND status NOT IN ('done','canceled')", (team_id,)).fetchone()[0]
        if remaining == 0:
            self._system(connection, team, "Every task on the board is done or canceled.", [LEAD], actor=actor)

    def _reopen(self, connection: sqlite3.Connection, team: sqlite3.Row, row: sqlite3.Row, actor: Actor, note: str | None) -> None:
        """A gate owner (or the lead or user) sends finished work in its milestone back to its owner.

        The milestone's gates return to open and stay blocked until the work is
        done again, so a failed gate always holds the next milestone back.
        """
        if not note:
            raise TeamRejected("team_task_reason_required")
        if row["kind"] != "work" or row["status"] != "done" or row["milestone"] is None:
            raise TeamRejected("team_task_state_conflict")
        gates = connection.execute(
            "SELECT * FROM team_tasks WHERE team_id=? AND kind='gate' AND milestone=?", (team["id"], row["milestone"])).fetchall()
        owns_gate = actor.kind == "member" and any(
            gate["owner_member_id"] == actor.member_id and gate["status"] == "in_progress" for gate in gates)
        if actor.kind not in {"lead", "user"} and not owns_gate:
            raise TeamRejected("team_task_forbidden")
        now = now_ms()
        target = "in_progress" if row["owner_member_id"] else "open"
        changed = connection.execute(
            "UPDATE team_tasks SET status=?,note=?,version=version+1,updated_at=? WHERE id=? AND status='done'",
            (target, note, now, row["id"]))
        if changed.rowcount != 1:
            raise TeamRejected("team_task_state_conflict")
        connection.execute(
            "UPDATE team_tasks SET status='open',version=version+1,updated_at=? "
            "WHERE team_id=? AND kind='gate' AND milestone=? AND status IN ('in_progress','in_review','done')",
            (now, team["id"], row["milestone"]))
        if row["owner_member_id"] is not None:
            self._system(connection, team, f"Task {row['key']} was reopened by {actor.name}: {note}", [row["owner_member_id"]],
                         task_id=row["id"], actor=actor)
        if actor.kind == "member":
            self._system(connection, team, f"{actor.name} reopened task {row['key']} at its milestone gate: {note}", [LEAD],
                         task_id=row["id"], actor=actor)

    def _ensure_gates(self, connection: sqlite3.Connection, team_id: str, milestone: str) -> None:
        """Create the template's gate tasks for a milestone that has none yet."""
        team = self._team_row(connection, team_id)
        reviewers = self._template(team).get("gate_reviewers") or []
        if not reviewers or connection.execute(
                "SELECT 1 FROM team_tasks WHERE team_id=? AND kind='gate' AND milestone=?", (team_id, milestone)).fetchone():
            return
        members = {row["name"]: row["id"] for row in connection.execute(
            "SELECT id,name FROM team_members WHERE team_id=? AND status='active'", (team_id,))}
        index = 1 + connection.execute(
            "SELECT COUNT(DISTINCT milestone) FROM team_tasks WHERE team_id=? AND kind='gate'", (team_id,)).fetchone()[0]
        now = now_ms()
        for reviewer in reviewers:
            if reviewer not in members:
                continue
            if connection.execute("SELECT COUNT(*) FROM team_tasks WHERE team_id=?", (team_id,)).fetchone()[0] >= MAX_TEAM_TASKS:
                return
            key = f"gate.{index}.{reviewer}"
            if connection.execute("SELECT 1 FROM team_tasks WHERE team_id=? AND key=?", (team_id, key)).fetchone():
                continue
            connection.execute(
                "INSERT INTO team_tasks (id,team_id,key,kind,title,description,owner_member_id,status,review_required,milestone,created_by,created_at,updated_at) "
                "VALUES (?,?,?,'gate',?,?,?,'open',0,?,?,?,?)",
                (str(uuid.uuid4()), team_id, key, f"Gate: {milestone}"[:200],
                 "Review every work task of this milestone. Submit to pass the gate, or reopen the tasks that fall short with a reason.",
                 members[reviewer], milestone, SYSTEM_ACTOR.name, now, now))

    @staticmethod
    def _template(team: sqlite3.Row) -> dict[str, object]:
        return json.loads(team["template_json"]) if team["template_json"] else {}

    def _advance_assigned(self, connection: sqlite3.Connection, team_id: str, key: str, *, actor: Actor | None = None) -> None:
        """An owned open task starts once its dependencies are done; its owner is told."""
        row = self._task(connection, team_id, key)
        if row["status"] != "open" or row["owner_member_id"] is None or self._blocked(connection, row["id"]):
            return
        connection.execute("UPDATE team_tasks SET status='in_progress',version=version+1,updated_at=? WHERE id=?", (now_ms(), row["id"]))
        team = self._team_row(connection, team_id)
        self._system(connection, team, f"Task {key} is assigned to you and ready: {row['title']}.", [row["owner_member_id"]],
                     task_id=row["id"], actor=actor)

    def _insert_task(self, connection: sqlite3.Connection, team_id: str, task: ProposedTask, *, created_by: str,
                     names: dict[str, str]) -> None:
        now = now_ms()
        connection.execute(
            "INSERT INTO team_tasks (id,team_id,key,kind,title,description,acceptance,owner_member_id,reviewer,status,review_required,milestone,paths_json,created_by,created_at,updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,'open',?,?,?,?,?,?)",
            (str(uuid.uuid4()), team_id, task.key, task.kind, task.title, task.description, task.acceptance,
             names.get(task.owner) if task.owner else None,
             self._reviewer_key(task.reviewer, {value: key for key, value in names.items()}) if task.reviewer else None,
             int(task.review_required), task.milestone, json.dumps(task.paths), created_by, now, now))
        if task.owner and task.owner not in names:
            raise TeamRejected("team_member_not_found")

    def _set_dependencies(self, connection: sqlite3.Connection, team_id: str, key: str, depends_on: list[str]) -> None:
        row = self._task(connection, team_id, key)
        targets = {}
        for dependency in depends_on:
            target = connection.execute("SELECT id FROM team_tasks WHERE team_id=? AND key=?", (team_id, dependency)).fetchone()
            if target is None or dependency == key:
                raise TeamRejected("team_task_dependency_invalid")
            targets[dependency] = target["id"]
        connection.execute("DELETE FROM team_task_deps WHERE task_id=?", (row["id"],))
        connection.executemany("INSERT INTO team_task_deps VALUES (?,?)", ((row["id"], value) for value in targets.values()))
        graph: dict[str, list[str]] = {}
        for edge in connection.execute(
                "SELECT a.key AS task,b.key AS dependency FROM team_task_deps d JOIN team_tasks a ON a.id=d.task_id "
                "JOIN team_tasks b ON b.id=d.depends_on_task_id WHERE a.team_id=?", (team_id,)):
            graph.setdefault(edge["task"], []).append(edge["dependency"])
        try:
            reject_cycles(graph)
        except ValueError:
            raise TeamRejected("team_task_cycle") from None

    def _validate_evidence(self, connection: sqlite3.Connection, evidence: list[EvidenceRef], sessions: frozenset[str]) -> None:
        for value in evidence:
            if value.item_id is not None:
                row = connection.execute("SELECT session_id,kind FROM items WHERE id=?", (value.item_id,)).fetchone()
            else:
                row = connection.execute(
                    "SELECT i.session_id,i.kind FROM tool_calls t JOIN items i ON i.id=t.item_id WHERE t.id=? OR t.provider_call_id=?",
                    (value.tool_call_id, value.tool_call_id)).fetchone()
            if row is None or row["session_id"] not in sessions or row["kind"] not in {"tool_call", "command_execution", "file_change"}:
                raise TeamRejected("team_evidence_invalid")

    def evidence_sessions(self, session_id: str) -> frozenset[str]:
        """The submitter's own Session and the Sessions of its subagents."""
        with self.database.lock:
            rows = self.database.connection().execute(
                "SELECT d.child_session_id FROM agent_delegations d JOIN runs r ON r.id=d.parent_run_id WHERE r.session_id=?",
                (session_id,)).fetchall()
        return frozenset({session_id, *(row[0] for row in rows)})

    # Messages --------------------------------------------------------------------

    def post(self, team_id: str, actor: Actor, request: TeamPostRequest, *, source_item_id: str | None = None,
             operation_id: str | None = None) -> TeamMessageView:
        with self.database.transaction() as connection:
            for column, value in (("source_item_id", source_item_id), ("operation_id", operation_id)):
                if value is None:
                    continue
                existing = connection.execute(f"SELECT * FROM team_messages WHERE {column}=?", (value,)).fetchone()
                if existing is not None:
                    if existing["content"] != request.text:
                        raise TeamRejected("team_operation_conflict")
                    return self._message_view(existing, self._names(connection, team_id), preview=False,
                                              task_keys=self._task_keys(connection, team_id))
            team = self._team_row(connection, team_id, writable=actor.kind != "user")
            if team["status"] not in {"active", "paused"}:
                raise TeamRejected("team_not_active")
            names = self._names(connection, team_id)
            by_name = {name: member_id for member_id, name in names.items()}
            recipients: list[str] = []
            to_all = False
            to_user = False
            for token in dict.fromkeys(request.to):
                if token == "all":
                    to_all = True
                elif token == USER:
                    to_user = True
                elif token == LEAD:
                    recipients.append(LEAD)
                elif token in by_name:
                    recipients.append(by_name[token])
                else:
                    raise TeamRejected("team_recipient_not_found")
            if request.channel == "direct" and (to_all or to_user or len(recipients) != 1):
                raise TeamRejected("team_direct_single_recipient")
            if actor.key in recipients:
                raise TeamRejected("team_recipient_not_found")
            task_id = self._task(connection, team_id, request.task)["id"] if request.task else None
            message_id = self._insert_message(connection, team, actor, request.channel, recipients, to_all, to_user,
                                              task_id, request.text, source_item_id=source_item_id, operation_id=operation_id)
            self._detect_ping_pong(connection, team, actor, recipients)
            self._event(connection, team["lead_session_id"], team_id, "message")
            row = connection.execute("SELECT * FROM team_messages WHERE id=?", (message_id,)).fetchone()
            return self._message_view(row, names, preview=False, task_keys=self._task_keys(connection, team_id))

    def _insert_message(self, connection: sqlite3.Connection, team: sqlite3.Row, actor: Actor, channel: str,
                        recipients: list[str], to_all: bool, to_user: bool, task_id: str | None, text: str, *,
                        source_item_id: str | None = None, operation_id: str | None = None, wake: bool = True) -> str:
        if not text.strip() or len(text.encode("utf-8")) > MAX_TEAM_MESSAGE_BYTES:
            raise TeamRejected("team_message_empty_or_too_large")
        team_id = team["id"]
        seq = connection.execute("SELECT coalesce(max(seq),0)+1 FROM team_messages WHERE team_id=?", (team_id,)).fetchone()[0]
        names = self._names(connection, team_id)
        display = [LEAD if value == LEAD else names.get(value, value) for value in recipients]
        if to_all:
            display.append("all")
        if to_user:
            display.append(USER)
        message_id = str(uuid.uuid4())
        connection.execute(
            "INSERT INTO team_messages (id,team_id,seq,channel,author_kind,author_member_id,to_all,recipients_json,task_id,content,source_item_id,operation_id,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (message_id, team_id, seq, channel, actor.kind, actor.member_id, int(to_all), json.dumps(display), task_id, text,
             source_item_id, operation_id, now_ms()))
        delivered_to = set(recipients)
        if to_all:
            delivered_to |= {LEAD, *names}
        delivered_to.discard(actor.key)
        connection.executemany("INSERT OR IGNORE INTO team_message_deliveries (message_id,recipient) VALUES (?,?)",
                               ((message_id, value) for value in sorted(delivered_to)))
        if wake:
            for target in recipients:
                if target != actor.key:
                    self._wake(connection, team, target, "message", user_message=actor.kind == "user")
        return message_id

    def _system(self, connection: sqlite3.Connection, team: sqlite3.Row, text: str, recipients: list[str], *,
                task_id: str | None = None, actor: Actor | None = None, to_all_also: bool = False) -> None:
        targets = [value for value in recipients if actor is None or value != actor.key]
        if not targets and not to_all_also:
            return
        self._insert_message(connection, team, SYSTEM_ACTOR, "board", targets, to_all_also, False, task_id, text)

    def _detect_ping_pong(self, connection: sqlite3.Connection, team: sqlite3.Row, actor: Actor, recipients: list[str]) -> None:
        if len(recipients) != 1 or actor.kind not in {"lead", "member"}:
            return
        window = PING_PONG_LIMIT * 2
        rows = connection.execute(
            "SELECT author_kind,author_member_id,recipients_json,created_at FROM team_messages WHERE team_id=? AND author_kind!='system' "
            "ORDER BY seq DESC LIMIT ?", (team["id"], window)).fetchall()
        if len(rows) < window:
            return
        parties = {actor.key, recipients[0]}
        for row in rows:
            author = row["author_member_id"] or row["author_kind"]
            if author not in parties:
                return
        oldest = rows[-1]["created_at"]
        if connection.execute("SELECT 1 FROM team_tasks WHERE team_id=? AND updated_at>=?", (team["id"], oldest)).fetchone():
            return
        last_system = connection.execute(
            "SELECT content FROM team_messages WHERE team_id=? AND author_kind='system' ORDER BY seq DESC LIMIT 1", (team["id"],)).fetchone()
        notice = "Two participants have woken each other repeatedly without any task progress. Decide whether to change the plan."
        if last_system is not None and last_system["content"] == notice:
            return
        self._insert_message(connection, team, SYSTEM_ACTOR, "board", [LEAD] if LEAD not in parties else [], False, True, None,
                             notice, wake=False)

    def messages_page(self, team_id: str, viewer: Actor, *, after: int = 0, limit: int = 50,
                      member: str | None = None, task: str | None = None, channel: str | None = None) -> TeamMessagePage:
        with self.database.lock:
            connection = self.database.connection()
            self._team_row(connection, team_id)  # an unknown team is an error, not an empty board
            names = self._names(connection, team_id)
            by_name = {name: member_id for member_id, name in names.items()}
            clauses = ["team_id=?", "seq>?"]
            values: list[object] = [team_id, after]
            if channel is not None:
                clauses.append("channel=?")
                values.append(channel)
            if task is not None:
                clauses.append("task_id=(SELECT id FROM team_tasks WHERE team_id=? AND key=?)")
                values.extend([team_id, task])
            if member is not None:
                member_id = by_name.get(member)
                clauses.append("(author_member_id=? OR EXISTS (SELECT 1 FROM team_message_deliveries d WHERE d.message_id=team_messages.id AND d.recipient=?))")
                values.extend([member_id, member_id])
            if viewer.kind == "member":
                # Members read the board and only their own direct messages.
                clauses.append("(channel='board' OR author_member_id=? OR EXISTS (SELECT 1 FROM team_message_deliveries d WHERE d.message_id=team_messages.id AND d.recipient=?))")
                values.extend([viewer.member_id, viewer.member_id])
            rows = connection.execute(
                f"SELECT * FROM team_messages WHERE {' AND '.join(clauses)} ORDER BY seq LIMIT ?", (*values, limit + 1)).fetchall()
            page = rows[:limit]
            keys = self._task_keys(connection, team_id)
            return TeamMessagePage(messages=[self._message_view(row, names, preview=viewer.kind != "user", task_keys=keys) for row in page],
                                   next_cursor=page[-1]["seq"] if page else after, has_more=len(rows) > limit)

    def message(self, team_id: str, viewer: Actor, message_id: str) -> TeamMessageView:
        with self.database.lock:
            connection = self.database.connection()
            row = connection.execute("SELECT * FROM team_messages WHERE team_id=? AND id=?", (team_id, message_id)).fetchone()
            if row is None:
                raise TeamRejected("team_message_not_found")
            if viewer.kind == "member" and row["channel"] == "direct" and row["author_member_id"] != viewer.member_id and not connection.execute(
                    "SELECT 1 FROM team_message_deliveries WHERE message_id=? AND recipient=?", (message_id, viewer.member_id)).fetchone():
                raise TeamRejected("team_message_not_found")
            return self._message_view(row, self._names(connection, team_id), preview=False, task_keys=self._task_keys(connection, team_id))

    def record_model_delivery(self, run_id: str, model_attempt_id: str) -> None:
        from eidos_runtime.persistence.context_snapshots import ContextSnapshotRepository

        snapshot = ContextSnapshotRepository(self.database).read_for_model_attempt(model_attempt_id)
        if snapshot is None:
            return
        ids = {value for section in snapshot.model_context for value in (section.get("teamMessageIds") or [])
               if isinstance(value, str)}
        if not ids:
            return
        with self.database.transaction() as connection:
            role = self._role(connection, run_id)
            if role is None or role.team_id is None:
                return
            attempt = connection.execute(
                "SELECT 1 FROM model_attempts a JOIN steps s ON s.id=a.step_id JOIN runs r ON r.id=s.run_id "
                "WHERE a.id=? AND s.run_id=? AND a.status='completed' AND r.cancel_requested_at IS NULL",
                (model_attempt_id, run_id)).fetchone()
            if attempt is None:
                return
            connection.executemany(
                "UPDATE team_message_deliveries SET model_attempt_id=?,delivered_at=? WHERE message_id=? AND recipient=? AND delivered_at IS NULL",
                ((model_attempt_id, now_ms(), message_id, role.recipient) for message_id in ids))

    def _undelivered(self, connection: sqlite3.Connection, team_id: str, recipient: str, limit: int) -> tuple[TeamMessageView, ...]:
        names = self._names(connection, team_id)
        rows = connection.execute(
            "SELECT m.* FROM team_messages m JOIN team_message_deliveries d ON d.message_id=m.id "
            "WHERE m.team_id=? AND d.recipient=? AND d.delivered_at IS NULL ORDER BY m.seq LIMIT ?",
            (team_id, recipient, limit)).fetchall()
        keys = self._task_keys(connection, team_id)
        return tuple(self._message_view(row, names, preview=True, task_keys=keys) for row in rows)

    @staticmethod
    def _has_undelivered(connection: sqlite3.Connection, team_id: str, recipient: str) -> bool:
        return connection.execute(
            "SELECT 1 FROM team_message_deliveries d JOIN team_messages m ON m.id=d.message_id "
            "WHERE m.team_id=? AND d.recipient=? AND d.delivered_at IS NULL LIMIT 1", (team_id, recipient)).fetchone() is not None

    # Wakes -------------------------------------------------------------------------

    def _wake(self, connection: sqlite3.Connection, team: sqlite3.Row, target: str, reason: str, *, user_message: bool = False) -> None:
        if team["status"] not in {"active", "paused"}:
            return
        if target == LEAD:
            if not team["auto_wake_lead"] and not user_message:
                return
            session_id = team["lead_session_id"]
        else:
            member = connection.execute("SELECT session_id FROM team_members WHERE id=? AND status='active'", (target,)).fetchone()
            if member is None:
                return
            session_id = member["session_id"]
        if connection.execute(f"SELECT 1 FROM runs WHERE session_id=? AND status IN {_ACTIVE_SQL} LIMIT 1", (session_id,)).fetchone():
            # A working target reads the message at its next context build;
            # its terminal transition wakes it again if any message remains.
            return
        now = now_ms()
        existing = connection.execute(
            "SELECT * FROM team_wakes WHERE team_id=? AND target=? AND status='pending'", (team["id"], target)).fetchone()
        if existing is None:
            connection.execute(
                "INSERT INTO team_wakes (id,team_id,target,reasons_json,user_message,status,created_at,updated_at) VALUES (?,?,?,?,?,'pending',?,?)",
                (str(uuid.uuid4()), team["id"], target, json.dumps([reason]), int(user_message), now, now))
            return
        reasons = json.loads(existing["reasons_json"])
        if reason not in reasons:
            reasons = (reasons + [reason])[-20:]
        connection.execute("UPDATE team_wakes SET reasons_json=?,user_message=max(user_message,?),updated_at=? WHERE id=?",
                           (json.dumps(reasons), int(user_message), now, existing["id"]))

    def working(self, lead_session_id: str) -> bool:
        """True while the Session's active team has running members or a pending wake.

        Goal continuation waits then: the team's own wakes bring the lead back
        when members report, so the goal does not re-run the lead in between.
        """
        with self.database.lock:
            return self.database.connection().execute(
                "SELECT 1 FROM teams t WHERE t.lead_session_id=? AND t.status='active' AND ("
                f"EXISTS (SELECT 1 FROM team_members m JOIN runs r ON r.session_id=m.session_id WHERE m.team_id=t.id AND r.status IN {_ACTIVE_SQL}) "
                "OR EXISTS (SELECT 1 FROM team_wakes w WHERE w.team_id=t.id AND w.status='pending'))",
                (lead_session_id,)).fetchone() is not None

    def ready_wakes(self) -> tuple[ReadyWake, ...]:
        with self.database.lock:
            connection = self.database.connection()
            rows = connection.execute(
                "SELECT w.*,t.status AS team_status,t.lead_session_id,t.run_settings_json FROM team_wakes w JOIN teams t ON t.id=w.team_id "
                "WHERE w.status='pending' AND (t.status='active' OR (t.status='paused' AND w.user_message=1)) ORDER BY w.created_at LIMIT 256"
            ).fetchall()
            ready = []
            for row in rows:
                settings = TeamRunSettings.model_validate_json(row["run_settings_json"])
                if row["target"] == LEAD:
                    session_id, model = row["lead_session_id"], MemberModel(model_id=settings.model_id, reasoning_selection=settings.reasoning_selection)
                else:
                    member = connection.execute("SELECT session_id,model_json FROM team_members WHERE id=? AND status='active'", (row["target"],)).fetchone()
                    if member is None:
                        continue
                    session_id, model = member["session_id"], MemberModel.model_validate_json(member["model_json"])
                if connection.execute(f"SELECT 1 FROM runs WHERE session_id=? AND status IN {_ACTIVE_SQL} LIMIT 1", (session_id,)).fetchone():
                    continue
                ready.append(ReadyWake(row["id"], row["team_id"], row["target"], session_id, model.model_id, model.reasoning_selection,
                                       settings.approval_mode, settings.full_access_confirmation))
            return tuple(ready)

    def accept_wake(self, wake_id: str, write_run: Callable[[sqlite3.Connection], tuple[dict[str, object], dict[str, object]]]
                    ) -> tuple[dict[str, object], dict[str, object]]:
        busy = False
        with self.database.transaction() as connection:
            wake = connection.execute(
                "SELECT w.*,t.status AS team_status,t.lead_session_id FROM team_wakes w JOIN teams t ON t.id=w.team_id WHERE w.id=?",
                (wake_id,)).fetchone()
            if wake is None or wake["status"] != "pending":
                raise TeamRejected("team_wake_inactive")
            if not (wake["team_status"] == "active" or (wake["team_status"] == "paused" and wake["user_message"])):
                raise TeamRejected("team_wake_inactive")
            session_id = wake["lead_session_id"] if wake["target"] == LEAD else connection.execute(
                "SELECT session_id FROM team_members WHERE id=?", (wake["target"],)).fetchone()["session_id"]
            if connection.execute(f"SELECT 1 FROM runs WHERE session_id=? AND status IN {_ACTIVE_SQL} LIMIT 1", (session_id,)).fetchone():
                # The Session's own queued or running work reads the messages.
                connection.execute("UPDATE team_wakes SET status='obsolete',updated_at=? WHERE id=?", (now_ms(), wake_id))
                busy = True
            else:
                run, item = write_run(connection)
                connection.execute("UPDATE team_wakes SET status='accepted',run_id=?,updated_at=? WHERE id=?", (run["id"], now_ms(), wake_id))
                connection.execute(
                    "INSERT INTO team_runs (run_id,team_id,member_id,wake_id,origin) VALUES (?,?,?,?,?)",
                    (run["id"], wake["team_id"], None if wake["target"] == LEAD else wake["target"], wake_id,
                     "user_message" if wake["user_message"] else "wake"))
                self._event(connection, wake["lead_session_id"], wake["team_id"], "status")
        if busy:
            raise TeamRejected("team_session_busy")
        return run, item

    def obsolete_wake(self, wake_id: str) -> None:
        with self.database.transaction() as connection:
            connection.execute("UPDATE team_wakes SET status='obsolete',updated_at=? WHERE id=? AND status='pending'", (now_ms(), wake_id))

    @classmethod
    def settle_run_in_connection(cls, connection: sqlite3.Connection, run_id: str, status: str) -> None:
        cls.__new__(cls).settle_run(connection, run_id, status)

    def settle_run(self, connection: sqlite3.Connection, run_id: str, status: str) -> None:
        """Called inside a Run's terminal transition; writes team events in the same transaction."""
        role = self._role(connection, run_id)
        if role is None or role.team_id is None:
            return
        team = self._team_row(connection, role.team_id)
        if team["status"] not in {"active", "paused"}:
            self._event(connection, team["lead_session_id"], team["id"], "status")
            return
        if role.role == "member" and role.member_id is not None:
            if status in {"failed", "interrupted"}:
                self._system(connection, team, f"{role.member_name} stopped with status {status}. Check its last result.", [LEAD])
            elif status == "succeeded" and not connection.execute(
                    "SELECT 1 FROM team_tasks WHERE team_id=? AND owner_member_id=? AND status='in_progress'",
                    (team["id"], role.member_id)).fetchone():
                self._system(connection, team, f"{role.member_name} finished its work and has no task in progress.", [LEAD])
        # Messages that arrived after this Run's last context build still wake it.
        if self._has_undelivered(connection, team["id"], role.recipient):
            self._wake(connection, team, role.recipient, "message")
        self._event(connection, team["lead_session_id"], team["id"], "status")

    # Lifecycle ---------------------------------------------------------------------

    def set_status(self, team_id: str, status: str) -> TeamView:
        allowed = {"active": {"paused", "dissolving"}, "paused": {"active", "dissolving"}, "dissolving": {"dissolved"}}
        with self.database.transaction() as connection:
            team = self._team_row(connection, team_id)
            if team["status"] == status:
                return self._team_view(connection, team)
            if status not in allowed.get(team["status"], set()):
                raise TeamRejected("team_not_active")
            connection.execute("UPDATE teams SET status=?,version=version+1,updated_at=? WHERE id=?", (status, now_ms(), team_id))
            if status in {"dissolving", "dissolved"}:
                connection.execute("UPDATE team_wakes SET status='obsolete',updated_at=? WHERE team_id=? AND status='pending'",
                                   (now_ms(), team_id))
            self._event(connection, team["lead_session_id"], team_id, "status")
            return self._team_view(connection, self._team_row(connection, team_id))

    def update_settings(self, team_id: str, *, auto_wake_lead: bool | None = None,
                        run_settings: TeamRunSettings | None = None) -> TeamView:
        with self.database.transaction() as connection:
            team = self._team_row(connection, team_id)
            if auto_wake_lead is not None:
                connection.execute("UPDATE teams SET auto_wake_lead=?,version=version+1,updated_at=? WHERE id=?",
                                   (int(auto_wake_lead), now_ms(), team_id))
            if run_settings is not None:
                connection.execute("UPDATE teams SET run_settings_json=?,version=version+1,updated_at=? WHERE id=?",
                                   (run_settings.model_dump_json(), now_ms(), team_id))
            self._event(connection, team["lead_session_id"], team_id, "team")
            return self._team_view(connection, self._team_row(connection, team_id))

    @staticmethod
    def scheduling_clause() -> str:
        """SQL that must hold for a queued Run in a member Session to start."""
        return (
            "NOT EXISTS (SELECT 1 FROM team_members tm JOIN teams tt ON tt.id=tm.team_id "
            "WHERE tm.session_id=queued.session_id AND ("
            "tt.status NOT IN ('active','paused') OR tm.status!='active' "
            "OR (tt.status='paused' AND NOT EXISTS (SELECT 1 FROM team_runs tr WHERE tr.run_id=queued.id AND tr.origin='user_message')) "
            "OR (SELECT COUNT(*) FROM team_members peers JOIN runs busy ON busy.session_id=peers.session_id "
            f"WHERE peers.team_id=tt.id AND busy.status IN ('running','finalizing')) >= {MAX_ACTIVE_MEMBER_RUNS}))"
        )

    # Helpers -----------------------------------------------------------------------

    @staticmethod
    def _open_team(connection: sqlite3.Connection, session_id: str) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT * FROM teams WHERE lead_session_id=? AND status!='dissolved' ORDER BY created_at DESC LIMIT 1", (session_id,)).fetchone()

    @staticmethod
    def _team_row(connection: sqlite3.Connection, team_id: str, *, writable: bool = False) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM teams WHERE id=?", (team_id,)).fetchone()
        if row is None:
            raise TeamRejected("team_not_found")
        if writable and row["status"] not in {"active", "paused"}:
            raise TeamRejected("team_not_active")
        return row

    @staticmethod
    def _member(connection: sqlite3.Connection, team_id: str, name: str, *, active_only: bool = True) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM team_members WHERE team_id=? AND name=?", (team_id, name)).fetchone()
        if row is None or (active_only and row["status"] != "active"):
            raise TeamRejected("team_member_not_found")
        return row

    @staticmethod
    def _task(connection: sqlite3.Connection, team_id: str, key: str | None) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM team_tasks WHERE team_id=? AND key=?", (team_id, key)).fetchone()
        if row is None:
            raise TeamRejected("team_task_not_found")
        return row

    @staticmethod
    def _names(connection: sqlite3.Connection, team_id: str) -> dict[str, str]:
        return {row["id"]: row["name"] for row in connection.execute(
            "SELECT id,name FROM team_members WHERE team_id=? AND status='active'", (team_id,))}

    @staticmethod
    def _member_id(name: str | None, names: dict[str, str]) -> str:
        member_id = next((key for key, value in names.items() if value == name), None)
        if member_id is None:
            raise TeamRejected("team_member_not_found")
        return member_id

    def _reviewer_key(self, reviewer: str | None, names: dict[str, str]) -> str | None:
        if reviewer is None or reviewer == LEAD:
            return LEAD if reviewer == LEAD else None
        return self._member_id(reviewer, names)

    @staticmethod
    def _blocked(connection: sqlite3.Connection, task_id: str) -> bool:
        if connection.execute(
                "SELECT 1 FROM team_task_deps d JOIN team_tasks t ON t.id=d.depends_on_task_id WHERE d.task_id=? AND t.status!='done' LIMIT 1",
                (task_id,)).fetchone() is not None:
            return True
        # A gate waits for every work task of its milestone, without listing them as dependencies.
        return connection.execute(
            "SELECT 1 FROM team_tasks g JOIN team_tasks w ON w.team_id=g.team_id AND w.milestone=g.milestone AND w.kind='work' "
            "WHERE g.id=? AND g.kind='gate' AND w.status NOT IN ('done','canceled') LIMIT 1", (task_id,)).fetchone() is not None

    @staticmethod
    def _event(connection: sqlite3.Connection, session_id: str, team_id: str | None, kind: str) -> None:
        append_event(connection, EventType.TEAM_CHANGED, now_ms(),
                     {"sessionId": session_id, "teamId": team_id, "kind": kind}, session_id=session_id)

    def _team_view(self, connection: sqlite3.Connection, row: sqlite3.Row) -> TeamView:
        names = self._names(connection, row["id"])
        members = [self._member_view(connection, value) for value in connection.execute(
            "SELECT * FROM team_members WHERE team_id=? ORDER BY created_at,name", (row["id"],))]
        tasks = [self._task_view(connection, value, names) for value in connection.execute(
            "SELECT * FROM team_tasks WHERE team_id=? ORDER BY created_at,key", (row["id"],))]
        template = json.loads(row["template_json"]).get("name") if row["template_json"] else None
        return TeamView(id=row["id"], lead_session_id=row["lead_session_id"], status=row["status"], objective=row["objective"],
            auto_wake_lead=bool(row["auto_wake_lead"]), run_settings=TeamRunSettings.model_validate_json(row["run_settings_json"]),
            template=template, members=members, tasks=tasks, version=row["version"], created_at=row["created_at"], updated_at=row["updated_at"])

    @staticmethod
    def _member_view(connection: sqlite3.Connection, row: sqlite3.Row) -> TeamMemberView:
        run = connection.execute("SELECT id,status FROM runs WHERE session_id=? ORDER BY creation_seq DESC LIMIT 1",
                                 (row["session_id"],)).fetchone()
        model = MemberModel.model_validate_json(row["model_json"])
        return TeamMemberView(id=row["id"], name=row["name"], role=row["role"], brief=row["brief"], model_id=model.model_id,
            workspace_mode=row["workspace_mode"], status=row["status"], session_id=row["session_id"],
            run_id=run["id"] if run else None, run_status=DomainRunStatus(run["status"]) if run else None, created_at=row["created_at"])

    @staticmethod
    def _task_view(connection: sqlite3.Connection, row: sqlite3.Row, names: dict[str, str]) -> TeamTaskView:
        deps = [value[0] for value in connection.execute(
            "SELECT t.key FROM team_task_deps d JOIN team_tasks t ON t.id=d.depends_on_task_id WHERE d.task_id=? ORDER BY t.key", (row["id"],))]
        blocked = TeamRepository._blocked(connection, row["id"]) if row["status"] == "open" else False
        delivery = connection.execute("SELECT status FROM workspace_deliveries WHERE id=?", (row["delivery_id"],)).fetchone() if row["delivery_id"] else None
        reviewer = row["reviewer"]
        return TeamTaskView(id=row["id"], key=row["key"], kind=row["kind"], title=row["title"], description=row["description"], acceptance=row["acceptance"],
            owner=names.get(row["owner_member_id"]) if row["owner_member_id"] else None,
            reviewer=LEAD if reviewer == LEAD else names.get(reviewer) if reviewer else None,
            status=row["status"], blocked=blocked, depends_on=deps, review_required=bool(row["review_required"]),
            milestone=row["milestone"], paths=json.loads(row["paths_json"]),
            evidence=[EvidenceRef.model_validate(value) for value in json.loads(row["evidence_json"])],
            delivery_status=delivery["status"] if delivery else None, note=row["note"], version=row["version"],
            created_by=row["created_by"], updated_at=row["updated_at"])

    @staticmethod
    def _task_keys(connection: sqlite3.Connection, team_id: str) -> dict[str, str]:
        return {row["id"]: row["key"] for row in connection.execute("SELECT id,key FROM team_tasks WHERE team_id=?", (team_id,))}

    @staticmethod
    def _message_view(row: sqlite3.Row, names: dict[str, str], *, preview: bool, task_keys: dict[str, str] | None = None) -> TeamMessageView:
        author = (names.get(row["author_member_id"], "former member") if row["author_kind"] == "member" else row["author_kind"])
        content = row["content"]
        truncated = preview and len(content) > MESSAGE_PREVIEW_CHARACTERS
        return TeamMessageView(id=row["id"], seq=row["seq"], channel=row["channel"], author=author, author_kind=row["author_kind"],
            recipients=json.loads(row["recipients_json"]), to_all=bool(row["to_all"]),
            task=None if row["task_id"] is None else (task_keys or {}).get(row["task_id"], row["task_id"]),
            content=content[:MESSAGE_PREVIEW_CHARACTERS] if truncated else content, truncated=truncated, created_at=row["created_at"])

    @staticmethod
    def _proposal_view(row: sqlite3.Row) -> TeamProposalView:
        return TeamProposalView(id=row["id"], session_id=row["session_id"], run_id=row["run_id"],
            proposal=TeamProposal.model_validate_json(row["proposal_json"]),
            decision="declined" if row["decision"] == "declined" else row["decision"], team_id=row["team_id"], created_at=row["created_at"])

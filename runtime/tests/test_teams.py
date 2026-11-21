from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.domain.teams import (
    EvidenceRef, MemberModel, RosterDecision, TeamPostRequest, TeamProposal, TeamRejected, TeamRunSettings, TeamTaskRequest,
)
from eidos_runtime.persistence.teams import LEAD, USER_ACTOR, Actor, PreparedMember, TeamRepository


class Fixture:
    def __init__(self, tmp_path: Path, *, members: int = 2, proposal: TeamProposal | None = None) -> None:
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        self.store = SessionStore(tmp_path / "data")
        self.store.initialize()
        self.session = self.store.create_session(str(workspace))
        self.run, _ = self.store.create_run(self.session["id"], "build the feature as a team")
        self.repo = TeamRepository(self.store.database)
        names = ["alice", "bob", "carol", "dave"][:members]
        self.proposal = proposal or TeamProposal(
            objective="Ship the parser",
            members=[{"name": name, "brief": f"{name} brief", "role": "verifier" if name == "bob" else "worker"} for name in names],
            tasks=[
                {"key": "t1", "title": "Build", "owner": "alice", "review_required": True, "reviewer": "bob"},
                {"key": "t2", "title": "Docs", "depends_on": ["t1"]},
            ],
        )
        item = self.store.create_tool_item(self.run["id"], 1, 0, "propose", "team_propose", "{}")
        self.proposal_view = self.repo.propose(self.run["id"], item["id"], self.proposal)

    def confirm(self) -> None:
        self.repo.decide(self.proposal_view.id, "decision-1", RosterDecision(confirmed=True))
        settings = TeamRunSettings(model_id="model-a")
        prepared = [PreparedMember(member, str(uuid.uuid4()), MemberModel(model_id="model-a")) for member in self.proposal.members]
        self.team = self.repo.create_team(self.proposal_view.id, self.run["id"], settings, True, prepared, self.proposal.tasks)
        self.ids = {member.name: member.id for member in self.team.members}
        self.sessions = {member.name: member.session_id for member in self.team.members}

    def finish_lead(self) -> None:
        claimed = self.store.claim_next_run_committed()
        assert claimed is not None and claimed.value["id"] == self.run["id"]
        self.store.fail_run(self.run["id"], "lead_done")

    def actor(self, name: str) -> Actor:
        return Actor("member", name, self.ids[name])

    def task(self, key: str):
        return next(task for task in self.repo.team(self.team.id).tasks if task.key == key)

    def wakes(self) -> dict[str, str]:
        return {row["target"]: row["status"] for row in self.store.connection.execute(
            "SELECT target,status FROM team_wakes WHERE team_id=? AND status='pending'", (self.team.id,))}

    def close(self) -> None:
        self.store.close()


@pytest.fixture
def team(tmp_path):
    fixture = Fixture(tmp_path)
    try:
        yield fixture
    finally:
        fixture.close()


def test_proposal_suspends_lead_and_confirmation_creates_internal_member_sessions(team):
    assert team.store.read_run(team.run["id"])["status"] == "waiting_input"
    team.confirm()
    assert team.store.read_run(team.run["id"])["status"] == "queued"
    kinds = {row[0] for row in team.store.connection.execute(
        "SELECT internal_kind FROM sessions WHERE id IN (?,?)", tuple(team.sessions.values()))}
    assert kinds == {"team_member"}
    assert all(item["id"] not in team.sessions.values() for item in team.store.list_sessions()["items"])
    assert team.task("t1").status == "in_progress" and team.task("t1").owner == "alice"
    assert team.task("t2").status == "open" and team.task("t2").blocked
    assert set(team.wakes()) == {team.ids["alice"], team.ids["bob"]}
    assert team.repo.team_for_session(team.session["id"]).id == team.team.id
    with pytest.raises(TeamRejected, match="team_operation_conflict"):
        team.repo.decide(team.proposal_view.id, "decision-1", RosterDecision(confirmed=False))


def test_review_flow_requires_own_evidence_and_unblocks_dependents(team):
    team.confirm()
    alice = team.actor("alice")
    with pytest.raises(TeamRejected, match="team_evidence_required"):
        team.repo.task_op(team.team.id, alice, TeamTaskRequest(op="submit", key="t1"))
    bob_run, _ = team.store.create_run(team.sessions["bob"], "review")
    lead_item = team.store.create_tool_item(bob_run["id"], 1, 0, "bob-call", "run_shell", "{}")
    sessions = team.repo.evidence_sessions(team.sessions["alice"])
    with pytest.raises(TeamRejected, match="team_evidence_invalid"):
        team.repo.task_op(team.team.id, alice, TeamTaskRequest(op="submit", key="t1", evidence=[EvidenceRef(item_id=lead_item["id"])]),
                          evidence_session_ids=sessions)
    team.store.fail_run(bob_run["id"], "fixture_done")
    own_run, _ = team.store.create_run(team.sessions["alice"], "work")
    own_item = team.store.create_tool_item(own_run["id"], 1, 0, "own-call", "run_shell", "{}")
    team.store.fail_run(own_run["id"], "fixture_done")
    submitted = team.repo.task_op(team.team.id, alice, TeamTaskRequest(op="submit", key="t1", evidence=[EvidenceRef(item_id=own_item["id"])]),
                                  evidence_session_ids=sessions)
    assert submitted.status == "in_review"
    with pytest.raises(TeamRejected, match="team_task_forbidden"):
        team.repo.task_op(team.team.id, alice, TeamTaskRequest(op="accept", key="t1"))
    with pytest.raises(TeamRejected, match="team_task_reason_required"):
        team.repo.task_op(team.team.id, team.actor("bob"), TeamTaskRequest(op="reject", key="t1"))
    assert team.repo.task_op(team.team.id, team.actor("bob"), TeamTaskRequest(op="accept", key="t1")).status == "done"
    assert team.task("t2").status == "open" and not team.task("t2").blocked
    claimed = team.repo.task_op(team.team.id, team.actor("bob"), TeamTaskRequest(op="claim", key="t2"))
    assert claimed.status == "in_progress" and claimed.owner == "bob"
    with pytest.raises(TeamRejected, match="team_task_state_conflict"):
        team.repo.task_op(team.team.id, alice, TeamTaskRequest(op="claim", key="t2"))
    team.repo.task_op(team.team.id, team.actor("bob"), TeamTaskRequest(op="submit", key="t2"))
    lead_inbox = [message.content for message in team.repo.context_for_run(team.run["id"]).messages]
    assert any("Every task on the board is done" in content for content in lead_inbox)


def test_task_dependencies_reject_cycles_and_update_needs_current_version(team):
    team.confirm()
    lead = Actor("lead", LEAD)
    team.repo.task_op(team.team.id, lead, TeamTaskRequest(op="create", key="t3", title="Tests", depends_on=["t2"]))
    with pytest.raises(TeamRejected, match="team_task_cycle"):
        team.repo.task_op(team.team.id, lead, TeamTaskRequest(op="update", key="t1", depends_on=["t3"], version=team.task("t1").version))
    with pytest.raises(TeamRejected, match="team_task_version_conflict"):
        team.repo.task_op(team.team.id, lead, TeamTaskRequest(op="update", key="t3", title="Renamed", version=99))
    assert team.repo.task_op(team.team.id, lead, TeamTaskRequest(op="update", key="t3", title="Renamed", version=team.task("t3").version)).title == "Renamed"


def test_messages_reach_named_recipients_and_direct_messages_stay_private(team):
    team.confirm()
    with team.store.database.transaction() as connection:
        connection.execute("UPDATE team_wakes SET status='obsolete'")
    alice, bob = team.actor("alice"), team.actor("bob")
    team.repo.post(team.team.id, alice, TeamPostRequest(to=["bob"], channel="direct", text="Only for bob"), source_item_id="m1")
    assert team.repo.post(team.team.id, alice, TeamPostRequest(to=["bob"], channel="direct", text="Only for bob"), source_item_id="m1")
    team.repo.post(team.team.id, alice, TeamPostRequest(to=["all"], text="Status for everyone"), source_item_id="m2")
    with pytest.raises(TeamRejected, match="team_direct_single_recipient"):
        team.repo.post(team.team.id, alice, TeamPostRequest(to=["all"], channel="direct", text="x"))
    with pytest.raises(TeamRejected, match="team_recipient_not_found"):
        team.repo.post(team.team.id, alice, TeamPostRequest(to=["alice"], text="self"))
    assert set(team.wakes()) == {team.ids["bob"]}
    bob_page = team.repo.messages_page(team.team.id, bob)
    assert "Only for bob" in [message.content for message in bob_page.messages]
    carol_view = Actor("member", "carol", "not-a-member")
    assert "Only for bob" not in [message.content for message in team.repo.messages_page(team.team.id, carol_view).messages]
    assert "Only for bob" in [message.content for message in team.repo.messages_page(team.team.id, USER_ACTOR).messages]
    lead_messages = [message.content for message in team.repo.context_for_run(team.run["id"]).messages]
    assert "Status for everyone" in lead_messages and "Only for bob" not in lead_messages


def _succeed(store: SessionStore, run_id: str) -> None:
    from eidos_runtime.db.transitions import transition_run
    from eidos_runtime.runtime.state_machine import RunStatus

    with store.database.transaction() as connection:
        transition_run(connection, run_id, frozenset({RunStatus.RUNNING}), RunStatus.SUCCEEDED, "done")


def test_member_terminal_run_notifies_lead_and_rewakes_for_unread_messages_only_after_success(team):
    team.confirm()
    team.finish_lead()
    with team.store.database.transaction() as connection:
        connection.execute("UPDATE team_wakes SET status='obsolete'")
    bob_run, _ = team.store.create_run(team.sessions["bob"], "review")
    team.repo.post(team.team.id, team.actor("alice"), TeamPostRequest(to=["bob"], text="Please check"), source_item_id="m1")
    assert team.ids["bob"] not in team.wakes()
    assert team.store.read_run(bob_run["id"])["status"] == "running"
    # A failed (or canceled) Run stays stopped: it reports to the lead but does not restart itself.
    team.store.fail_run(bob_run["id"], "done")
    wakes = team.wakes()
    assert team.ids["bob"] not in wakes and LEAD in wakes
    retry, _ = team.store.create_run(team.sessions["bob"], "review again")
    team.repo.post(team.team.id, team.actor("alice"), TeamPostRequest(to=["bob"], text="One more"), source_item_id="m2")
    _succeed(team.store, retry["id"])
    assert team.ids["bob"] in team.wakes()


def test_stopped_lead_declines_its_unanswered_proposal(team):
    assert team.repo.pending_proposal(team.session["id"]).id == team.proposal_view.id
    team.store.cancel_run(team.run["id"])
    assert team.repo.pending_proposal(team.session["id"]) is None
    row = team.store.connection.execute("SELECT decision FROM team_proposals WHERE id=?", (team.proposal_view.id,)).fetchone()
    assert row[0] == "declined"


def test_decided_or_abandoned_proposal_no_longer_waits_for_the_user(team):
    team.repo.decide(team.proposal_view.id, "decision-1", RosterDecision(confirmed=True))
    assert team.store.read_run(team.run["id"])["status"] == "queued"
    assert team.repo.pending_proposal(team.session["id"]) is None
    team.repo.abandon_proposal(team.proposal_view.id)
    row = team.store.connection.execute("SELECT decision FROM team_proposals WHERE id=?", (team.proposal_view.id,)).fetchone()
    assert row[0] == "declined"


def test_team_section_fits_its_budget_and_delivers_what_it_shows(team):
    import json

    from eidos_runtime.context.team_context import TEAM_SECTION_BYTES, _PREFIX, team_section

    team.confirm()
    alice = team.actor("alice")
    for index in range(16):
        team.repo.post(team.team.id, alice, TeamPostRequest(to=["lead"], text="界" * 3000), source_item_id=f"long-{index}")
    context = team.repo.context_for_run(team.run["id"])
    assert len(context.messages) == 16 and context.messages[0].truncated
    section = team_section(context)  # must return: messages alone exceed the budget while open tasks remain
    assert len(section["content"].encode("utf-8")) <= len(_PREFIX.encode("utf-8")) + TEAM_SECTION_BYTES
    payload = json.loads(section["content"][len(_PREFIX):])
    ids = section["teamMessageIds"]
    # The oldest messages that fit are delivered, truncated previews included; the rest wait for the next request.
    assert 0 < len(ids) < 16 and ids == [message.id for message in context.messages[:len(ids)]]
    assert payload["omittedMessages"] == 16 - len(ids) and payload["tasks"] == []


def test_removing_a_member_releases_its_blocked_tasks_too(team):
    team.confirm()
    lead = Actor("lead", LEAD)
    team.repo.task_op(team.team.id, lead, TeamTaskRequest(op="create", key="t3", title="Follow-up", owner="alice", depends_on=["t2"]))
    assert team.task("t3").status == "open" and team.task("t3").blocked
    team.repo.remove_member(team.team.id, "alice", "reassigned", USER_ACTOR)
    owners = dict(team.store.connection.execute(
        "SELECT key,owner_member_id FROM team_tasks WHERE team_id=? AND key IN ('t1','t3')", (team.team.id,)).fetchall())
    assert owners == {"t1": None, "t3": None}
    lead_inbox = [message.content for message in team.repo.context_for_run(team.run["id"]).messages]
    assert any("unassigned: t1, t3" in content for content in lead_inbox)


def test_canceled_dependency_unblocks_its_dependents(team):
    team.confirm()
    team.repo.task_op(team.team.id, Actor("lead", LEAD), TeamTaskRequest(op="cancel", key="t1"))
    assert team.task("t2").status == "open" and not team.task("t2").blocked
    assert team.repo.task_op(team.team.id, team.actor("bob"), TeamTaskRequest(op="claim", key="t2")).status == "in_progress"


def test_evidence_lookup_by_provider_call_id_stays_in_the_submitters_sessions(team):
    team.confirm()
    bob_run, _ = team.store.create_run(team.sessions["bob"], "other work")
    team.store.create_tool_item(bob_run["id"], 1, 0, "call_0", "run_shell", "{}")
    team.store.fail_run(bob_run["id"], "fixture_done")
    own_run, _ = team.store.create_run(team.sessions["alice"], "work")
    team.store.create_tool_item(own_run["id"], 1, 0, "call_0", "run_shell", "{}")
    team.store.fail_run(own_run["id"], "fixture_done")
    submitted = team.repo.task_op(team.team.id, team.actor("alice"),
                                  TeamTaskRequest(op="submit", key="t1", evidence=[EvidenceRef(tool_call_id="call_0")]),
                                  evidence_session_ids=team.repo.evidence_sessions(team.sessions["alice"]))
    assert submitted.status == "in_review"


def test_wake_acceptance_starts_one_run_and_paused_teams_only_accept_user_messages(team):
    team.confirm()
    team.finish_lead()
    ready = {wake.target: wake for wake in team.repo.ready_wakes()}
    alice = ready[team.ids["alice"]]
    from eidos_runtime.db.repositories.runs import RunRepository

    run, _ = team.repo.accept_wake(alice.wake_id, lambda connection: RunRepository(team.store.database).create_run(
        alice.session_id, "", queued=True, internal_input=True, transaction_connection=connection))
    assert team.store.connection.execute("SELECT origin FROM team_runs WHERE run_id=?", (run["id"],)).fetchone()[0] == "wake"
    with pytest.raises(TeamRejected, match="team_wake_inactive"):
        team.repo.accept_wake(alice.wake_id, lambda connection: (_ for _ in ()).throw(AssertionError("no second run")))
    team.repo.set_status(team.team.id, "paused")
    assert team.ids["bob"] not in {wake.target for wake in team.repo.ready_wakes()}
    team.repo.post(team.team.id, USER_ACTOR, TeamPostRequest(to=["bob"], text="Please start"), operation_id="u1")
    assert team.ids["bob"] in {wake.target for wake in team.repo.ready_wakes()}


def test_removing_a_member_releases_its_tasks(team):
    team.confirm()
    team.repo.remove_member(team.team.id, "alice", "reassigned", Actor("lead", LEAD))
    task = team.task("t1")
    assert task.status == "open" and task.owner is None
    with pytest.raises(TeamRejected, match="team_member_not_found"):
        team.repo.task_op(team.team.id, Actor("lead", LEAD), TeamTaskRequest(op="assign", key="t1", owner="alice"))


def test_team_mode_end_to_end_through_the_engine(tmp_path):
    import threading

    from eidos_runtime.application.collaboration import CollaborationApplication
    from eidos_runtime.application.teams import TeamApplication
    from eidos_runtime.db.repositories.runs import RunRepository
    from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel
    from eidos_runtime.runtime.engine import RuntimeEngine

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    try:
        session = store.create_session(str(workspace))
        lead, _ = store.enqueue_run(session["id"], "Build the parser with a team")
        repo = TeamRepository(store.database)
        with store.database.transaction() as connection:
            repo.mark_lead_mode(connection, lead["id"])
        assert store.claim_next_run_committed().value["id"] == lead["id"]
        app = CollaborationApplication(store, lambda: None, lambda _run: None, lambda: None)
        teams = TeamApplication(store, workspaces=lambda: None, cancel=lambda _run: None, publish=lambda: None, schedule=lambda: None)
        app.teams = teams
        started: list[str] = []
        teams.start_wake = lambda wake: started.append(wake.target)
        proposal = {"objective": "Build the parser", "members": [{"name": "alice", "brief": "Implement the parser", "role": "worker"}],
                    "tasks": [{"key": "t1", "title": "Implement", "owner": "alice"}]}
        proposing = ScriptedModel([ModelResponse(tool_calls=(ModelToolCall("propose", "team_propose", proposal),))])
        RuntimeEngine(store, proposing, lambda _event: None, collaboration=app).run(lead["id"], threading.Event())
        names = {tool.name for tool in proposing.tool_definitions_history[0]}
        assert "team_propose" in names and "team_task" not in names
        assert store.read_run(lead["id"])["status"] == "waiting_input"

        pending = repo.pending_proposal(session["id"])
        teams.decide(pending.id, "confirm-1", RosterDecision(confirmed=True))
        assert store.claim_next_run_committed().value["id"] == lead["id"]
        coordinating = ScriptedModel([ModelResponse(text="The team is working.")])
        RuntimeEngine(store, coordinating, lambda _event: None, collaboration=app).run(lead["id"], threading.Event())
        assert store.read_run(lead["id"])["status"] == "succeeded"
        team = repo.team_for_session(session["id"])
        assert team is not None and [task.status for task in team.tasks] == ["in_progress"]
        # Creation starts the members' wakes at once instead of waiting for the next idle edge.
        assert started and set(started) == {team.members[0].id}
        lead_tools = {tool.name for tool in coordinating.tool_definitions_history[-1]}
        assert {"team_task", "team_post", "team_add_member"} <= lead_tools and "team_propose" not in lead_tools

        wake = next(value for value in repo.ready_wakes() if value.target != LEAD)
        member_run, _ = repo.accept_wake(wake.wake_id, lambda connection: RunRepository(store.database).create_run(
            wake.session_id, "", queued=True, internal_input=True, transaction_connection=connection))
        assert store.claim_next_run_committed().value["id"] == member_run["id"]
        working = ScriptedModel([
            ModelResponse(tool_calls=(
                ModelToolCall("submit", "team_task", {"op": "submit", "key": "t1", "note": "Parser implemented"}),
                ModelToolCall("report", "team_post", {"to": ["lead"], "text": "t1 is done."}),
            )),
            ModelResponse(text="Finished my task."),
        ])
        RuntimeEngine(store, working, lambda _event: None, collaboration=app).run(member_run["id"], threading.Event())
        assert store.read_run(member_run["id"])["status"] == "succeeded"
        member_tools = {tool.name for tool in working.tool_definitions_history[0]}
        assert {"team_task", "team_post", "team_read", "spawn_agent"} <= member_tools
        assert not member_tools & {"team_add_member", "team_propose", "request_user_input", "request_permissions"}
        assert any("You are alice" in str(instruction) for instruction in working.instructions_history[:1])
        assert "team-state" in str(working.contexts[0])
        assert repo.team(team.id).tasks[0].status == "done"
        assert LEAD in {value.target for value in repo.ready_wakes()}
        lead_inbox = [message.content for message in repo.context_for_run(lead["id"]).messages]
        assert "t1 is done." in lead_inbox
        # The member read its joining and assignment messages in that Run.
        assert not repo.has_undelivered_for_run(member_run["id"])
    finally:
        store.close()


def test_keep_worker_proposal_wait_pauses_the_tool_deadline(team):
    import threading
    import time
    from types import SimpleNamespace

    from eidos_runtime.model.client import ModelToolCall
    from eidos_runtime.runtime.cancellation import DeadlineCancellation
    from eidos_runtime.tools.team import team_entries

    view = team.proposal_view
    polls = {"count": 0}

    class Repository:
        def proposal_for_item(self, _item_id):
            polls["count"] += 1
            if polls["count"] == 1:
                return None
            if polls["count"] < 5:
                return view, None
            return view, RosterDecision(confirmed=False)

        def propose(self, _run_id, _item_id, _proposal, *, suspend):
            assert suspend is False

    application = SimpleNamespace(repository=Repository())
    runtime = team_entries(application, ("team_propose",))[0].runtime
    context = SimpleNamespace(inline_control_wait=True, shell_process_manager=None,
                              events=SimpleNamespace(deliver_pending=lambda: None))
    # The deadline has passed before the user answers; the paused deadline must not cancel the wait.
    cancel = DeadlineCancellation(threading.Event(), time.monotonic() + 0.05, time.monotonic)
    call = ModelToolCall("propose", "team_propose", team.proposal.model_dump(mode="json"))
    outcome = runtime._propose(context, team.run["id"], {"id": "item-1"}, call, cancel)
    assert outcome.result["code"] == "team_proposal_declined"
    assert cancel.paused_at is None


def test_team_integrate_by_task_uses_only_the_submitted_delivery():
    from types import SimpleNamespace

    from eidos_runtime.application.agent_workspaces import AgentWorkspaceError
    from eidos_runtime.persistence.teams import RunTeamRole
    from eidos_runtime.tools.agent_integration import TeamIntegrationAdapter

    deliveries = {"d-stale": SimpleNamespace(status="stale"), "d-gone": SimpleNamespace(status="discarded")}
    tasks = {"a": {"delivery_id": "d-stale", "owner_member_id": "m-1"}, "b": {"delivery_id": None, "owner_member_id": "m-1"},
             "c": {"delivery_id": "d-gone", "owner_member_id": "m-1"}}

    class Repository:
        def role_for_run(self, _run_id):
            return RunTeamRole("lead", "team-1", "lead-session")

        def task_row(self, _team_id, key):
            return tasks[key]

    class Workspaces:
        def delivery(self, delivery_id):
            return deliveries.get(delivery_id)

        def capture_owner(self, _owner):
            raise AssertionError("a task never integrates the member's current files")

    adapter = TeamIntegrationAdapter.__new__(TeamIntegrationAdapter)
    adapter.teams = SimpleNamespace(repository=Repository())
    adapter.workspaces = Workspaces()
    adapter.run_id = "lead-run"
    delivery, paths = adapter._delivery({"task": "a"})
    assert delivery is deliveries["d-stale"] and paths is None
    for key, code in (("b", "team_task_no_delivery"), ("c", "team_task_delivery_unavailable")):
        with pytest.raises(AgentWorkspaceError, match=code):
            adapter._delivery({"task": key})


def test_removed_worktree_member_is_discarded_only_after_its_run_stops(tmp_path):
    from types import SimpleNamespace

    from eidos_runtime.application.teams import TeamApplication

    fixture = Fixture(tmp_path)
    try:
        fixture.confirm()
        team = fixture.team
        alice = next(member for member in team.members if member.name == "alice")
        # Session Worktree ids need a real project; the member row is all removal reads.
        fixture.store.connection.execute(
            "UPDATE team_members SET worktree_id='wt-alice',base_tree='tree-0' WHERE id=?", (alice.id,))
        fixture.store.connection.commit()
        alice_run, _ = fixture.store.create_run(alice.session_id, "long command")
        discarded: list[str] = []
        canceled: list[str] = []
        workspaces = SimpleNamespace(discard_owner=lambda owner: discarded.append(owner.id))
        app = TeamApplication(fixture.store, workspaces=lambda: workspaces, cancel=canceled.append,
                              publish=lambda: None, schedule=lambda: None)
        app.repository.removed_member_worktrees = lambda: tuple(  # the worktrees table needs a real project
            {"id": row["id"], "team_id": row["team_id"], "worktree_id": row["worktree_id"], "base_tree": row["base_tree"]}
            for row in fixture.store.connection.execute(
                "SELECT * FROM team_members WHERE status='removed' AND worktree_id IS NOT NULL"))
        app.remove_member(team.id, "alice", "done", USER_ACTOR, discard_changes=True)
        assert canceled == [alice_run["id"]] and discarded == []
        fixture.store.cancel_run(alice_run["id"])
        app.finish_removals()
        assert discarded == [alice.id]
    finally:
        fixture.close()


def test_team_protocol_fixture_matches_python_models():
    import json
    from pathlib import Path

    from pydantic import ValidationError

    from eidos_runtime.domain.teams import TeamRunOrigin
    from eidos_runtime.protocol.planning import RunPlanningOptions
    from eidos_runtime.protocol.teams import TEAM_METHODS, TeamChangedNotice

    fixture = json.loads((Path(__file__).resolve().parents[2] / "protocol/fixtures/teams-v1.json").read_text())
    models = {method: (request, response) for method, request, response in TEAM_METHODS}
    assert set(fixture["requests"]) == set(models)
    for method, cases in fixture["requests"].items():
        for value in cases["valid"]:
            models[method][0].model_validate_json(json.dumps(value))
        for value in cases["invalid"]:
            with pytest.raises(ValidationError):
                models[method][0].model_validate_json(json.dumps(value))
    for method, cases in fixture["responses"].items():
        for value in cases["valid"]:
            models[method][1].model_validate_json(json.dumps(value))
        for value in cases["invalid"]:
            with pytest.raises(ValidationError):
                models[method][1].model_validate_json(json.dumps(value))
    TeamRunOrigin.model_validate_json(json.dumps(fixture["origin"]))
    TeamChangedNotice.model_validate_json(json.dumps(fixture["notice"]))
    for value in fixture["validRunOptions"]:
        RunPlanningOptions.model_validate_json(json.dumps(value))
    for value in fixture["invalidRunOptions"]:
        with pytest.raises(ValidationError):
            RunPlanningOptions.model_validate_json(json.dumps(value))


def test_roster_full_access_needs_confirmation():
    from pydantic import ValidationError

    from eidos_runtime.domain.teams import RosterDecision

    with pytest.raises(ValidationError):
        RosterDecision.model_validate({"confirmed": True, "approval_mode": "full_access"})
    RosterDecision.model_validate({"confirmed": True, "approval_mode": "full_access", "full_access_confirmation": "full-access-v1"})


def _milestone_team(tmp_path):
    """A team frozen from the built-in milestones template: two builders, critic and challenger gates."""
    from eidos_runtime.infrastructure.team_templates import TeamTemplateCatalog

    template = TeamTemplateCatalog(None).get("milestones")
    fixture = Fixture(tmp_path, proposal=TeamProposal(
        objective="Ship in milestones", template="milestones",
        members=[{"name": member.name, "role": member.role, "brief": member.brief} for member in template.members],
        tasks=[
            {"key": "m1.api", "title": "API", "owner": "builder-a", "milestone": "M1"},
            {"key": "m1.ui", "title": "UI", "owner": "builder-b", "milestone": "M1"},
        ],
    ))
    fixture.repo.decide(fixture.proposal_view.id, "decision-m", RosterDecision(confirmed=True))
    prepared = [PreparedMember(member, str(uuid.uuid4()), MemberModel(model_id="model-a")) for member in fixture.proposal.members]
    fixture.team = fixture.repo.create_team(fixture.proposal_view.id, fixture.run["id"], TeamRunSettings(model_id="model-a"), True,
                                            prepared, fixture.proposal.tasks, template_json=template.model_dump_json())
    fixture.ids = {member.name: member.id for member in fixture.team.members}
    return fixture


def test_template_gates_hold_a_milestone_until_reviewers_pass_it(tmp_path):
    team = _milestone_team(tmp_path)
    try:
        lead = Actor("lead", LEAD)
        gates = {task.key: task for task in team.repo.team(team.team.id).tasks if task.kind == "gate"}
        assert set(gates) == {"gate.1.critic", "gate.1.challenger"}
        assert all(gate.blocked and gate.status == "open" and gate.milestone == "M1" for gate in gates.values())
        # The template makes review the default for new work tasks, and a new milestone gets its own gates.
        created = team.repo.task_op(team.team.id, lead, TeamTaskRequest(op="create", key="m2.docs", title="Docs",
                                                                       milestone="M2", depends_on=["gate.1.critic"]))
        assert created.review_required and created.blocked
        assert {"gate.2.critic", "gate.2.challenger"} <= {task.key for task in team.repo.team(team.team.id).tasks}
        for key, owner in (("m1.api", "builder-a"), ("m1.ui", "builder-b")):
            # Proposal tasks keep the lead's review choice; these finish on submit.
            team.repo.task_op(team.team.id, Actor("member", owner, team.ids[owner]), TeamTaskRequest(op="submit", key=key))
        assert team.task("gate.1.critic").status == "in_progress" and team.task("gate.1.critic").owner == "critic"
        critic = Actor("member", "critic", team.ids["critic"])
        with pytest.raises(TeamRejected, match="team_task_reason_required"):
            team.repo.task_op(team.team.id, critic, TeamTaskRequest(op="reopen", key="m1.api"))
        with pytest.raises(TeamRejected, match="team_task_forbidden"):
            team.repo.task_op(team.team.id, Actor("member", "builder-b", team.ids["builder-b"]),
                              TeamTaskRequest(op="reopen", key="m1.api", note="no"))
        team.repo.task_op(team.team.id, critic, TeamTaskRequest(op="reopen", key="m1.api", note="missing error handling"))
        assert team.task("m1.api").status == "in_progress"
        assert team.task("gate.1.critic").status == "open" and team.task("gate.1.critic").blocked
        assert team.task("gate.1.challenger").status == "open"
        team.repo.task_op(team.team.id, Actor("member", "builder-a", team.ids["builder-a"]), TeamTaskRequest(op="submit", key="m1.api"))
        for reviewer in ("critic", "challenger"):
            assert team.task(f"gate.1.{reviewer}").status == "in_progress"
            team.repo.task_op(team.team.id, Actor("member", reviewer, team.ids[reviewer]),
                              TeamTaskRequest(op="submit", key=f"gate.1.{reviewer}"))
        assert team.task("gate.1.critic").status == "done"
        assert not team.task("m2.docs").blocked
        # Work added to a passed milestone holds its gates again until it is done.
        with pytest.raises(TeamRejected, match="team_task_forbidden"):
            team.repo.task_op(team.team.id, Actor("member", "builder-a", team.ids["builder-a"]),
                              TeamTaskRequest(op="update", key="m1.api", milestone="M3", version=team.task("m1.api").version))
        team.repo.task_op(team.team.id, lead, TeamTaskRequest(op="create", key="m1.fix", title="Fix", milestone="M1"))
        assert team.task("gate.1.critic").status == "open" and team.task("gate.1.critic").blocked
        assert team.task("gate.1.challenger").status == "open"
        critic_inbox = [message.content for message in team.repo.messages_page(team.team.id, critic).messages]
        assert any("Gate gate.1.critic waits again" in content for content in critic_inbox)
        # Moving that work out releases the milestone's gates to their owners again.
        team.repo.task_op(team.team.id, lead, TeamTaskRequest(op="update", key="m1.fix", milestone="M2",
                                                              version=team.task("m1.fix").version))
        assert team.task("gate.1.critic").status == "in_progress"
    finally:
        team.close()


def test_gate_tasks_need_a_milestone(team):
    team.confirm()
    with pytest.raises(TeamRejected, match="team_gate_milestone_required"):
        team.repo.task_op(team.team.id, Actor("lead", LEAD), TeamTaskRequest(op="create", key="g", kind="gate"))


def test_template_catalog_reads_user_files_without_overriding_builtins(tmp_path):
    from eidos_runtime.infrastructure.team_templates import TeamTemplateCatalog

    directory = tmp_path / "teams"
    directory.mkdir()
    (directory / "pair.toml").write_text(
        'name = "pair"\ndescription = "Two writers"\ngate_reviewers = ["checker"]\n'
        '[[members]]\nname = "writer"\nbrief = "Write"\n[[members]]\nname = "checker"\nrole = "verifier"\nbrief = "Check"\n')
    (directory / "feature.toml").write_text('name = "feature"\n[[members]]\nname = "solo"\nbrief = "Override"\n')
    (directory / "broken.toml").write_text("name = ")
    catalog = TeamTemplateCatalog(tmp_path)
    summaries = {value.name: value for value in catalog.summaries()}
    assert summaries["pair"].source == "user" and summaries["pair"].gate_reviewers == ["checker"]
    assert summaries["feature"].source == "builtin" and "solo" not in summaries["feature"].members
    with pytest.raises(TeamRejected, match="team_template_not_found"):
        catalog.get("missing")


def test_member_context_fork_is_bounded_and_frozen(team):
    store = team.store
    store.connection.execute(
        "UPDATE items SET status='completed', content=? WHERE run_id=? AND kind='user_message'", ("x" * 5000, team.run["id"]))
    store.connection.commit()
    text = team.repo.lead_turns(team.session["id"], 2)
    assert text is not None and text.startswith("User: ") and len(text) <= 2000 + len("User: ")


def test_goal_continuation_waits_while_team_members_work(team):
    team.confirm()
    assert team.repo.working(team.session["id"])  # members have pending start wakes
    team.store.connection.execute("UPDATE team_wakes SET status='obsolete' WHERE team_id=?", (team.team.id,))
    team.store.connection.commit()
    assert not team.repo.working(team.session["id"])


def test_team_usage_counts_member_sessions(team):
    team.confirm()
    usage = {value.member: value for value in team.repo.usage(team.team.id)}
    assert set(usage) == {"alice", "bob"} and usage["alice"].input_tokens == 0 and not usage["alice"].incomplete


def test_team_hook_events_follow_stop_and_deny_semantics():
    from eidos_runtime.domain.hooks import SUPPORTED_HOOK_EVENTS
    from eidos_runtime.extensions.codex_hooks import parse_output

    assert {"TeamMemberIdle", "TeamTaskCompleted"} <= SUPPORTED_HOOK_EVENTS
    assert parse_output("TeamTaskCompleted", "", "tests are missing", 2).action == "deny"
    assert parse_output("TeamMemberIdle", "", "keep going", 2).action == "continue"


def test_team_rejections_map_to_stable_desktop_codes():
    from eidos_runtime.protocol.server import _TEAM_ERROR_CODES

    assert _TEAM_ERROR_CODES["member_has_unintegrated_changes"] == "TEAM_MEMBER_HAS_CHANGES"
    assert _TEAM_ERROR_CODES["team_operation_conflict"] == "OPERATION_ID_REUSED"


def test_lead_pending_instruction_lists_templates_with_their_policies(team):
    from eidos_runtime.context.team_context import team_instruction
    from eidos_runtime.infrastructure.team_templates import TeamTemplateCatalog

    from eidos_runtime.persistence.teams import RunTeamContext, RunTeamRole

    context = RunTeamContext(RunTeamRole("lead_pending", None, team.session["id"]), None, None, ())
    text = team_instruction(context, None, tuple(TeamTemplateCatalog(None).summaries()))
    assert "- milestones:" in text and "Gates by: critic, challenger." in text
    assert "Everyone shares the workspace." in text and "kind=gate" in text


def test_members_use_the_leads_active_task_grants(team):
    team.confirm()
    claimed = team.store.claim_next_run_committed()
    assert claimed is not None and claimed.value["id"] == team.run["id"]
    step = team.store.increment_model_step(team.run["id"])
    item = team.store.create_tool_item(team.run["id"], step, 0, "grant", "request_permissions", "{}")
    team.store.begin_approval_committed(item["id"], "", None, request={
        "kind": "permission_request", "grantScope": "run", "permissions": {"network": {"enabled": True}},
    })
    team.store.resolve_approval_committed(item["id"], "approve", None)
    member_run, _ = team.store.create_run(team.sessions["alice"], "work on t1")
    # One approval in the Lead covers its members while the Lead Run is active.
    assert team.store.run_permission_grants(member_run["id"]).network.enabled is True
    team.store.fail_run(team.run["id"], "lead_done")
    assert team.store.run_permission_grants(member_run["id"]).network is None


def test_members_do_not_inherit_the_leads_high_risk_grants(team):
    team.confirm()
    team.store.claim_next_run_committed()
    step = team.store.increment_model_step(team.run["id"])
    item = team.store.create_tool_item(team.run["id"], step, 0, "grant", "request_permissions", "{}")
    team.store.begin_approval_committed(item["id"], "", None, request={
        "kind": "permission_request", "grantScope": "run", "requiresHumanConfirmation": True,
        "riskWarning": "protected", "permissions": {"network": {"enabled": True}},
    })
    team.store.resolve_approval_committed(item["id"], "approve", None)
    member_run, _ = team.store.create_run(team.sessions["alice"], "work on t1")
    assert team.store.run_permission_grants(team.run["id"]).network.enabled is True
    assert team.store.run_permission_grants(member_run["id"]).network is None

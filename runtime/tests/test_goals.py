from __future__ import annotations

import json
import sqlite3
import threading
from uuid import uuid4

import pytest

from eidos_runtime.context.builder import ContextBuilder
from eidos_runtime.db.goal_migration import migrate_goals
from eidos_runtime.db.repositories.runs import RunRepository
from eidos_runtime.db.schema import V50_SCHEMA_SQL
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.db.transitions import transition_run
from eidos_runtime.domain.goal import GoalError, GoalDeclaration, SessionGoal
from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel, ModelUsage
from eidos_runtime.model.context_projection import recovery_context
from eidos_runtime.persistence.goals import GoalRepository
from eidos_runtime.protocol.goals import GoalRequest
from eidos_runtime.protocol.methods import RunStartRequestDto
from eidos_runtime.runtime.engine import RuntimeEngine
from eidos_runtime.runtime.state_machine import RunStatus


@pytest.fixture
def goals(tmp_path):
    store = SessionStore(tmp_path / "data")
    store.initialize()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "hello.txt").write_text("hello\n")
    session = store.create_session(str(workspace))["id"]
    repository = GoalRepository(store.database)
    yield store, session, repository
    store.close()


def seed_goal(goals, **overrides):
    """Seed a durable continuation; creation through run/start is tested separately."""
    from eidos_runtime.domain.automation import AutomationModel
    from eidos_runtime.persistence.goals import _intend
    from eidos_runtime.db.database import now_ms

    store, session, repository = goals
    goal = SessionGoal(id=str(uuid4()), session_id=session,
        objective=overrides.get("objective", "检查 hello.txt 并证明任务完成。"), status="active",
        revision=1, objective_revision=1, activation=1,
        model=AutomationModel(model_id="deepseek-v4.1-flash"), approval_mode="manual", created_at=now_ms(), updated_at=now_ms())
    with store.database.transaction() as connection:
        connection.execute("INSERT INTO session_goals(id,session_id,status,revision,goal_json) VALUES(?,?,?,?,?)",
            (goal.id, session, goal.status, goal.revision, goal.model_dump_json()))
        _intend(connection, goal, "continuation")
    return goal


def admit(repository, request, write_run):
    """Repository-level admission; run/start replay is covered through RPC."""
    return repository.admit_input(request, write_run, operation_request=request.model_dump(mode="json", by_alias=True))


def start_goal_input(goals, **overrides):
    store, session, repository = goals
    current = repository.view(session).goal
    request = RunStartRequestDto(sessionId=session, userInput="新的目标需要额外验证。", modelId="deepseek-v4.1-flash",
        goalMode=True, goalRevision=current.revision if current else None, operationId=str(uuid4()), **overrides)
    return admit(repository, request, lambda connection: RunRepository(store.database).create_run(
        session, request.user_input, queued=True, model_id=request.model_id, approval_mode=request.approval_mode,
        transaction_connection=connection))


def change(goals, action, **overrides):
    _store, session, repository = goals
    goal = repository.view(session).goal
    return repository.execute(GoalRequest.model_validate({"action": action, "sessionId": session,
        "expectedRevision": goal.revision, "operationId": str(uuid4()), **overrides}))


def accept(goals):
    store, session, repository = goals
    intent, _ = repository.pending()[0]
    return repository.accept(intent, lambda connection: RunRepository(store.database).create_run(
        session, "", internal_input=True, transaction_connection=connection))[0]


def finish(goals, run_id, status="succeeded"):
    store, _session, _repository = goals
    with store.database.transaction() as connection:
        before = RunStatus.RUNNING
        if status == "stopped":
            transition_run(connection, run_id, frozenset({before}), RunStatus.FINALIZING, "no_progress")
            before = RunStatus.FINALIZING
        transition_run(connection, run_id, frozenset({before}), RunStatus(status), None)


def test_goal_commands_keep_history_and_reject_stale_revision(goals):
    store, session, repository = goals
    original = seed_goal(goals)
    assert original.status == "active" and len(repository.pending()) == 1
    paused = change(goals, "pause").goal
    assert paused.status_reason == "user_paused" and repository.pending() == ()
    with pytest.raises(GoalError, match="目标已变化"):
        repository.execute(GoalRequest(action="clear", sessionId=session, expectedRevision=original.revision))
    resumed = change(goals, "resume").goal
    assert resumed.activation == 2 and resumed.audit_runs == 0
    cleared = change(goals, "clear").goal
    assert cleared.status == "cleared" and repository.pending() == ()
    replacement = seed_goal(goals)
    assert replacement.id != original.id
    assert store.database.connection().execute("SELECT count(*) FROM session_goals").fetchone()[0] == 2


def test_goal_run_creation_is_atomic_idempotent_and_keeps_the_real_user_message(goals):
    store, session, repository = goals
    request = RunStartRequestDto(sessionId=session, userInput="完成全部目标。", modelId="deepseek-v4.1-flash",
        goalMode=True, operationId=str(uuid4()))
    def create(connection):
        return RunRepository(store.database).create_run(session, request.user_input, queued=True, transaction_connection=connection)
    first = admit(repository, request, create)
    assert admit(repository, request, create) == first
    assert repository.view(session).goal.objective == request.user_input
    assert first[0]["goalOrigin"]["trigger"] == "user_input"
    assert first[1]["content"] == request.user_input
    assert repository.pending() == ()
    assert store.database.connection().execute("SELECT count(*) FROM session_goals").fetchone()[0] == 1
    assert store.database.connection().execute("SELECT count(*) FROM runs").fetchone()[0] == 1


def test_goal_replacement_uses_the_selected_run_mode_and_rejects_stale_versions(goals):
    store, session, repository = goals
    original = seed_goal(goals)
    created, _item = start_goal_input(goals, approvalMode="full_access", fullAccessConfirmation="full-access-v1")
    goal = repository.view(session).goal
    assert goal.id != original.id and goal.approval_mode == "full_access"
    assert goal.full_access_confirmation == "full-access-v1" and created["approvalMode"] == "full_access"
    request = RunStartRequestDto(sessionId=session, userInput="覆盖新目标。", modelId="deepseek-v4.1-flash", goalMode=True, goalRevision=999)
    with pytest.raises(GoalError, match="目标已变化"):
        admit(repository, request, lambda _connection: pytest.fail("must reject before creating a Run"))
    assert repository.view(session).goal.id == goal.id


@pytest.mark.parametrize("extra", [{"workMode": "plan"}, {"planId": "plan", "planRevision": 1},
    {"approvalMode": "full_access"}, {"userInput": " "}])
def test_goal_run_rejects_conflicting_plan_modes_or_missing_confirmation(goals, extra):
    _store, session, _repository = goals
    with pytest.raises(ValueError):
        RunStartRequestDto.model_validate({"sessionId": session, "userInput": "完整目标。", "modelId": "model", "goalMode": True, **extra})


def test_goal_admission_is_atomic_and_has_no_user_message(goals):
    store, session, repository = goals
    seed_goal(goals)
    intent, _ = repository.pending()[0]
    run = accept(goals)
    assert run["userInput"] == ""
    assert store.database.connection().execute("SELECT count(*) FROM items WHERE run_id=? AND kind='user_message'", (run["id"],)).fetchone()[0] == 0
    with pytest.raises(GoalError, match="目标已变化"):
        repository.accept(intent, lambda _connection: pytest.fail("must not create a second Run"))
    assert repository.for_run(run["id"]).goal.audit_runs == 1


def test_user_queue_wins_and_pending_intent_survives(goals):
    store, session, repository = goals
    seed_goal(goals)
    store.enqueue_run(session, "先回答这个问题。")
    assert repository.pending() == ()
    assert store.database.connection().execute("SELECT status FROM goal_continuations").fetchone()[0] == "pending"


@pytest.mark.parametrize(("status", "expected"), [("failed", "blocked"), ("stopped", "blocked"), ("canceled", "paused"), ("interrupted", "paused")])
def test_run_terminal_state_settles_goal_in_same_transaction(goals, status, expected):
    _store, session, repository = goals
    seed_goal(goals)
    run = accept(goals)
    finish(goals, run["id"], status)
    assert repository.view(session).goal.status == expected
    assert repository.pending() == ()


def test_three_runs_without_progress_block_goal(goals):
    _store, session, repository = goals
    seed_goal(goals)
    for _index in range(3):
        run = accept(goals)
        finish(goals, run["id"])
    assert repository.view(session).goal.status_reason == "idle_runs"
    assert repository.pending() == ()


def test_context_preserves_complete_objective_in_recovery(goals):
    store, _session, _repository = goals
    objective = "目标正文。" * 700
    seed_goal(goals, objective=objective)
    run = accept(goals)
    built = ContextBuilder(store).build(run["id"])
    assert "goal-policy" in {layer.id for layer in built.instructions.layers}
    assert built.goal_sample is not None
    recovered = recovery_context(built.model_context, 512)
    assert any(objective in item.get("content", "") for item in recovered)


def test_real_engine_confirms_completion_after_tool_result(goals):
    store, session, repository = goals
    seed_goal(goals)
    run = accept(goals)
    model = ScriptedModel([ModelResponse(tool_calls=(ModelToolCall("done", "update_goal", {
        "status": "complete", "summary": "已检查目标。", "evidence": [{"requirement": "检查文件", "proof": "测试提供的已确认事实。"}],
    }),)), ModelResponse(text="目标已完成。")])
    RuntimeEngine(store, model, lambda _message: None).run(run["id"], threading.Event())
    assert store.read_run(run["id"])["status"] == "succeeded"
    assert repository.view(session).goal.status == "complete"
    assert repository.view(session).usage_incomplete
    sources = store.database.connection().execute("SELECT eligible FROM memory_source_items WHERE run_id=?", (run["id"],)).fetchall()
    assert sources and all(row[0] == 0 for row in sources)
    assert "memory_edit" not in {tool.name for tool in model.tool_definitions_history[0]}
    assert repository.pending() == ()


def test_goal_terminal_activity_stays_with_its_run_when_user_input_is_queued(goals):
    store, session, repository = goals
    seed_goal(goals)
    run = accept(goals)
    queued, _item = store.enqueue_run(session, "接下来处理我的输入。")
    finish(goals, run["id"], "canceled")
    activity = repository.view(session).activities[-1]
    assert activity.status == "paused"
    assert activity.run_id == run["id"] and activity.run_id != queued["id"]


def test_restart_pauses_goal_between_runs(goals):
    store, session, repository = goals
    seed_goal(goals)
    run = accept(goals)
    finish(goals, run["id"])
    from eidos_runtime.persistence.goals import recover_goals
    with store.database.transaction() as connection:
        recover_goals(connection)
    assert repository.view(session).goal.status == "paused"
    assert repository.view(session).goal.status_reason == "runtime_restarted"
    assert repository.pending() == ()


def test_v50_migration_and_failure_rollback():
    connection = sqlite3.connect(":memory:")
    connection.executescript(V50_SCHEMA_SQL + "PRAGMA user_version=50;")
    migrate_goals(connection)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 51
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    connection.close()
    connection = sqlite3.connect(":memory:")
    connection.executescript(V50_SCHEMA_SQL + "CREATE TABLE goal_runs(test); PRAGMA user_version=50;")
    with pytest.raises(sqlite3.Error):
        migrate_goals(connection)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 50
    assert connection.execute("SELECT 1 FROM sqlite_master WHERE name='session_goals'").fetchone() is None
    connection.close()


@pytest.mark.parametrize("boundary", ["admission", "terminal"])
def test_goal_event_failure_rolls_back_the_whole_run_boundary(goals, monkeypatch, boundary):
    import eidos_runtime.persistence.goals as persistence

    store, session, repository = goals
    seed_goal(goals)
    run = accept(goals) if boundary == "terminal" else None
    connection = store.database.connection()
    before_events = connection.execute("SELECT count(*) FROM events").fetchone()[0]

    def fail_event(*_args, **_kwargs):
        raise sqlite3.OperationalError("injected goal event failure")

    monkeypatch.setattr(persistence, "append_event", fail_event)
    with pytest.raises(sqlite3.OperationalError, match="injected goal event failure"):
        if run:
            finish(goals, run["id"], "canceled")
        else:
            accept(goals)
    assert repository.view(session).goal.status == "active"
    assert connection.execute("SELECT count(*) FROM events").fetchone()[0] == before_events
    if run:
        assert store.read_run(run["id"])["status"] == "running"
        assert repository.pending() == ()
    else:
        assert connection.execute("SELECT count(*) FROM runs").fetchone()[0] == 0
        assert len(repository.pending()) == 1


def declaration_call():
    return ModelResponse(tool_calls=(ModelToolCall(str(uuid4()), "update_goal", {
        "status": "complete", "summary": "全部目标已核实。", "evidence": [{"requirement": "检查文件", "proof": "已读取 hello.txt。"}],
    }),))


def test_old_model_declaration_cannot_settle_edited_objective(goals):
    store, session, repository = goals
    seed_goal(goals)
    run = accept(goals)

    class EditingModel(ScriptedModel):
        def complete(self, *args, **kwargs):
            if self._index == 0:
                start_goal_input(goals)
            return super().complete(*args, **kwargs)

    model = EditingModel([declaration_call(), ModelResponse(text="需要继续检查新目标。")])
    RuntimeEngine(store, model, lambda _message: None).run(run["id"], threading.Event())
    results = store.database.connection().execute("SELECT result_json FROM tool_calls").fetchall()
    assert any(json.loads(row[0])["code"] == "goal_not_active" for row in results)
    assert repository.view(session).goal.status == "active"
    assert repository.view(session).goal.objective == "新的目标需要额外验证。"


def test_late_cancel_does_not_pause_a_new_activation(goals):
    _store, session, repository = goals
    seed_goal(goals)
    run = accept(goals)
    repository.bind_sample(run["id"], repository.view(session).goal.sample())
    change(goals, "pause")
    resumed = change(goals, "resume").goal
    finish(goals, run["id"], "canceled")
    assert repository.view(session).goal.status == "active"
    assert repository.view(session).goal.activation == resumed.activation
    assert len(repository.pending()) == 1


@pytest.mark.parametrize("action", ["pause", "clear", "replace"])
def test_changed_goal_rejects_prepared_continuation(goals, action):
    _store, _session, repository = goals
    seed_goal(goals)
    intent, _ = repository.pending()[0]
    start_goal_input(goals) if action == "replace" else change(goals, action)
    with pytest.raises(GoalError):
        repository.accept(intent, lambda _connection: pytest.fail("stale preparation must not create a Run"))


def test_repeated_read_across_runs_does_not_reset_goal_guard(goals):
    store, session, repository = goals
    seed_goal(goals)
    for _index in range(4):
        run = accept(goals)
        model = ScriptedModel([ModelResponse(tool_calls=(ModelToolCall(str(uuid4()), "read_file", {"path": "hello.txt"}),)), ModelResponse(text="还需要继续。")])
        RuntimeEngine(store, model, lambda _message: None).run(run["id"], threading.Event())
    assert repository.view(session).goal.status_reason == "idle_runs"


def test_candidate_and_tool_result_roll_back_together(goals, monkeypatch):
    store, session, repository = goals
    seed_goal(goals)
    run = accept(goals)
    repository.bind_sample(run["id"], repository.view(session).goal.sample())
    declaration = GoalDeclaration(status="complete", summary="完成。", evidence=[{"requirement": "检查", "proof": "已检查。"}])
    item = store.create_tool_item(run["id"], 1, 0, "done", "update_goal", declaration.model_dump_json())
    def fail(*_args, **_kwargs):
        raise sqlite3.OperationalError("injected event failure")
    monkeypatch.setattr("eidos_runtime.db.repositories.execution.append_event", fail)
    from eidos_runtime.runtime.errors import tool_result
    from eidos_runtime.tools.goal import GoalData
    result = tool_result("update_goal", "success", "goal_declaration_recorded", "记录。", {"pending": True}, data_model=GoalData)
    with pytest.raises(sqlite3.Error):
        store.complete_tool_item_committed(item["id"], json.dumps(result))
    row = store.database.connection().execute("SELECT candidate_json FROM goal_runs WHERE run_id=?", (run["id"],)).fetchone()
    assert row[0] is None
    assert store.database.connection().execute("SELECT result_json FROM tool_calls WHERE item_id=?", (item["id"],)).fetchone()[0] is None


def test_stop_hook_continuation_invalidates_completion_candidate(tmp_path):
    from test_codex_hooks import _fixture, _command
    script = "import json,sys,os,pathlib\ne=json.load(sys.stdin)\np=pathlib.Path(os.environ['PLUGIN_DATA'])/'once'\nif not p.exists():\n p.write_text('done')\n print(json.dumps({'decision':'block','reason':'Inspect another requirement.'}))\nelse:\n print('{}')"
    store, _catalog, _plugin, run, _ = _fixture(tmp_path, {"Stop": _command()}, script)
    try:
        session = store.read_run(run["id"])["sessionId"]
        repository = GoalRepository(store.database)
        seed_goal((store, session, repository), objective="通过最后的全部检查。")
        model = ScriptedModel([declaration_call(), ModelResponse(text="第一次完成声明。"), ModelResponse(text="还有需求需要检查。")])
        RuntimeEngine(store, model, lambda _message: None).run(run["id"], threading.Event())
        assert repository.view(session).goal.status == "active"
        assert store.read_run(run["id"])["status"] == "succeeded"
    finally:
        store.close()


def test_shared_goal_fixtures_match_python_contracts():
    from pathlib import Path
    from eidos_runtime.domain.goal import GoalRunOrigin, GoalView
    from eidos_runtime.protocol.goals import GoalChangedNotice
    from pydantic import ValidationError
    fixture = json.loads((Path(__file__).resolve().parents[2] / "protocol/fixtures/goals-v1.json").read_text())
    for value in fixture["validRequests"]:
        GoalRequest.model_validate_json(json.dumps(value))
    for value in fixture["invalidRequests"]:
        with pytest.raises(ValidationError):
            GoalRequest.model_validate_json(json.dumps(value))
    for value in fixture["validResponses"]:
        GoalView.model_validate_json(json.dumps(value))
    for value in fixture["invalidResponses"]:
        with pytest.raises(ValidationError):
            GoalView.model_validate_json(json.dumps(value))
    GoalRunOrigin.model_validate_json(json.dumps(fixture["origin"]))
    GoalChangedNotice.model_validate_json(json.dumps(fixture["notice"]))
    from eidos_runtime.protocol.planning import RunPlanningOptions
    for value in fixture["validRunOptions"]:
        RunPlanningOptions.model_validate_json(json.dumps(value))
    for value in fixture["invalidRunOptions"]:
        with pytest.raises(ValidationError):
            RunPlanningOptions.model_validate_json(json.dumps(value))


def test_goal_evidence_and_blocked_minimum_have_stable_errors(goals):
    store, session, repository = goals
    seed_goal(goals)
    run = accept(goals)
    repository.bind_sample(run["id"], repository.view(session).goal.sample())
    with pytest.raises(GoalError) as missing:
        repository.declare(run["id"], "unused", GoalDeclaration(status="complete", summary="完成。"))
    assert missing.value.code == "goal_evidence_required"
    with pytest.raises(GoalError) as early:
        repository.declare(run["id"], "unused", GoalDeclaration(status="blocked", summary="需要用户处理。"))
    assert early.value.code == "goal_blocked_too_early"
    assert store.read_run(run["id"])["status"] == "running"


def test_new_goal_session_is_not_limited_by_another_goal_waiting_approval(goals):
    store, session, repository = goals
    seed_goal(goals)
    first = accept(goals)
    with store.database.transaction() as connection:
        transition_run(connection, first["id"], frozenset({RunStatus.RUNNING}), RunStatus.WAITING_APPROVAL, None)
    other = store.create_session(str(store.workspace_for_session(session).path))["id"]
    seed_goal((store, other, repository))
    pending = repository.pending()
    assert len(pending) == 1 and pending[0][1].session_id == other
    second = repository.accept(pending[0][0], lambda connection: RunRepository(store.database).create_run(
        other, "", internal_input=True, transaction_connection=connection))[0]
    assert store.read_run(second["id"])["status"] == "running"


def test_goal_usage_includes_child_runs(goals):
    from eidos_runtime.domain.collaboration import SpawnAgent
    from eidos_runtime.persistence.collaboration import CollaborationRepository
    store, session, repository = goals
    seed_goal(goals)
    run = accept(goals)
    item = store.create_tool_item(run["id"], 1, 0, "spawn", "spawn_agent", "{}")
    child = CollaborationRepository(store.database).spawn(run["id"], item["id"], SpawnAgent(task_name="inspect", message="核对文件。", role="explorer"))
    store.claim_next_run_committed()
    # The child's ordinary Engine records provider usage in model_attempts.
    model = ScriptedModel([ModelResponse(text="已核对。", usage=ModelUsage(input_tokens=11, output_tokens=3))])
    RuntimeEngine(store, model, lambda _message: None).run(child.run_id, threading.Event())
    assert repository.view(session).tokens_used == 14


def test_restart_cancels_an_admitted_automatic_run_before_dispatch(goals):
    from eidos_runtime.persistence.goals import recover_goals
    from eidos_runtime.db.recovery import recover_runtime_facts
    store, session, repository = goals
    seed_goal(goals)
    intent, _ = repository.pending()[0]
    run = repository.accept(intent, lambda connection: RunRepository(store.database).create_run(
        session, "", queued=True, internal_input=True, transaction_connection=connection))[0]
    with store.database.transaction() as connection:
        recover_goals(connection)
        recover_runtime_facts(connection)
    assert repository.view(session).goal.status == "paused"
    assert store.read_run(run["id"])["status"] == "canceled"


def test_checkpoint_rewind_pauses_goal_and_fork_requires_a_new_goal(goals):
    from eidos_runtime.application.checkpoints import CheckpointApplication
    from eidos_runtime.protocol.methods import CheckpointCreateRequestDto, CheckpointForkRequestDto, CheckpointRewindRequestDto

    store, session, repository = goals
    seed_goal(goals)
    run, _item = store.enqueue_run(session, "检查当前工作区。")
    identity = store.workspace_for_run(run["id"])
    store.long_task_repository().initialize(run_id=run["id"], workspace_path=str(identity.path),
        workspace_device=identity.device, workspace_inode=identity.inode, workspace_owner=identity.owner,
        rule_snapshot_id="rule", inventory_snapshot_id="inventory", index_snapshot_id="index", permission_snapshot_hash="permission")
    application = CheckpointApplication(store, store.checkpoint_repository())
    created = application.create(CheckpointCreateRequestDto(runId=run["id"]))
    application.rewind(CheckpointRewindRequestDto(checkpointId=created.checkpoint.id))
    assert repository.view(session).goal.status_reason == "workspace_rewound"
    assert repository.pending() == ()
    forked = application.fork(CheckpointForkRequestDto(checkpointId=created.checkpoint.id))
    assert repository.view(forked.run.session_id).goal is None


@pytest.mark.parametrize("boundary", ["direct", "startup"])
def test_v51_goal_budgets_are_removed_without_automatic_resume(goals, boundary):
    from eidos_runtime.db.goal_migration import GOAL_V51_SCHEMA_SQL, migrate_goal_composer
    from schema_fixtures import remove_goal_schema

    store, session, repository = goals
    goal = seed_goal(goals)
    data = store.data_directory
    connection = store.database.connection()
    remove_goal_schema(connection)
    connection.executescript(GOAL_V51_SCHEMA_SQL)
    legacy = goal.model_dump(mode="json")
    legacy.update(status="budget_limited", status_reason="token_budget", token_budget=100)
    connection.execute("INSERT INTO session_goals(id,session_id,status,revision,goal_json) VALUES(?,?,?,?,?)",
        (goal.id, session, "budget_limited", 1, json.dumps(legacy)))
    connection.execute("PRAGMA user_version=51")
    connection.commit()
    replacement = None
    try:
        if boundary == "startup":
            store.close()
            replacement = SessionStore(data)
            replacement.initialize()
            assert replacement.health_state == "ready"
            repository = GoalRepository(replacement.database)
            connection = replacement.database.connection()
        else:
            migrate_goal_composer(connection)
        current = repository.view(session).goal
        assert current.status == "paused" and current.status_reason == "legacy_budget_removed"
        assert current.id == goal.id and current.objective == goal.objective
        assert "token_budget" not in json.loads(connection.execute("SELECT goal_json FROM session_goals").fetchone()[0])
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 52
        assert repository.pending() == ()
    finally:
        if replacement:
            replacement.close()


def test_v51_budget_migration_failure_rolls_back_its_json_and_version(goals):
    from eidos_runtime.db.goal_migration import migrate_goal_composer

    store, _session, _repository = goals
    seed_goal(goals)
    connection = store.database.connection()
    connection.execute("ALTER TABLE goal_runs DROP COLUMN reference_ids_json")
    connection.execute("PRAGMA user_version=51")
    connection.executescript("CREATE TRIGGER refuse_goal_migration BEFORE UPDATE ON session_goals BEGIN SELECT RAISE(ABORT,'injected'); END;")
    before = connection.execute("SELECT goal_json FROM session_goals").fetchone()[0]
    with pytest.raises(sqlite3.Error, match="injected"):
        migrate_goal_composer(connection)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 51
    assert connection.execute("SELECT goal_json FROM session_goals").fetchone()[0] == before


def test_new_goal_input_and_replaced_goal_roll_back_together(goals, monkeypatch):
    import eidos_runtime.persistence.goals as persistence
    store, session, repository = goals
    original = seed_goal(goals)
    def fail(*_args, **_kwargs):
        raise sqlite3.OperationalError("injected new goal commit")
    monkeypatch.setattr(persistence, "append_event", fail)
    with pytest.raises(sqlite3.Error, match="injected new goal commit"):
        start_goal_input(goals)
    assert repository.view(session).goal.id == original.id and repository.view(session).goal.status == "active"
    assert store.database.connection().execute("SELECT count(*) FROM runs").fetchone()[0] == 0
    assert store.database.connection().execute("SELECT count(*) FROM session_goals").fetchone()[0] == 1


def test_resume_uses_the_composer_model_and_confirmed_access_mode(goals):
    _store, session, repository = goals
    seed_goal(goals)
    change(goals, "pause")
    updated = change(goals, "resume", model={"modelId": "another-model"},
        approvalMode="full_access", fullAccessConfirmation="full-access-v1")
    assert updated.goal.model.model_id == "another-model" and updated.goal.approval_mode == "full_access"
    assert repository.view(session).goal.full_access_confirmation == "full-access-v1"


def test_automatic_goal_run_reads_its_frozen_references_without_a_user_message(goals):
    from types import SimpleNamespace
    from eidos_runtime.application.input_context import InputContextApplication
    from eidos_runtime.persistence.input_context import InputContextRepository
    from eidos_runtime.protocol.input_context import InputPrepareRequest

    store, session, repository = goals
    application = InputContextApplication(store, SimpleNamespace(), lambda text: text)
    reference = application.prepare(InputPrepareRequest(sessionId=session, kind="file",
        source=str(store.workspace_for_session(session).path / "hello.txt"))).reference
    request = RunStartRequestDto(sessionId=session, userInput="检查文件并完成全部目标。", modelId="deepseek-v4.1-flash",
        goalMode=True, references=[reference.id])
    first, _ = admit(repository, request, lambda connection: RunRepository(store.database).create_run(
        session, request.user_input, references=[reference], transaction_connection=connection))
    finish(goals, first["id"])
    automatic = accept(goals)
    snapshots = InputContextRepository(store.database).for_run(automatic["id"])
    assert [snapshot.reference.id for snapshot in snapshots] == [reference.id]
    assert snapshots[0].text == "hello\n"
    assert store.database.connection().execute("SELECT count(*) FROM items WHERE run_id=? AND kind='user_message'", (automatic["id"],)).fetchone()[0] == 0


def test_editing_a_queued_first_goal_updates_the_objective_and_sample_version(goals):
    from eidos_runtime.persistence.queued_runs import QueuedRunRepository
    store, session, repository = goals
    run, _item = start_goal_input(goals)
    original = repository.view(session).goal
    QueuedRunRepository(store.database).change(session, run["id"], run["userInput"], "更新后的完整目标。", operation_id=str(uuid4()))
    changed = repository.view(session).goal
    assert changed.objective == "更新后的完整目标。" and changed.objective_revision == original.objective_revision + 1
    store.claim_next_run_committed()
    finish(goals, run["id"], "canceled")
    assert repository.view(session).goal.status == "paused"
    assert repository.pending() == ()


def test_deleting_a_queued_first_goal_clears_its_target_and_preserves_an_event(goals):
    from eidos_runtime.persistence.queued_runs import QueuedRunRepository
    store, session, repository = goals
    run, _item = start_goal_input(goals)
    QueuedRunRepository(store.database).change(session, run["id"], run["userInput"], None, operation_id=str(uuid4()))
    assert repository.view(session).goal.status == "cleared" and repository.pending() == ()
    assert store.database.connection().execute("SELECT count(*) FROM runs").fetchone()[0] == 0
    event = store.database.connection().execute("SELECT run_id,payload_json FROM events WHERE event_type='goal.changed' ORDER BY id DESC LIMIT 1").fetchone()
    assert event[0] is None and json.loads(event[1])["status"] == "cleared"


def test_steering_a_queued_goal_keeps_a_durable_continuation_after_the_active_run(goals):
    from eidos_runtime.persistence.queued_runs import QueuedRunRepository
    store, session, repository = goals
    active, _ = store.create_run(session, "先完成当前任务。")
    queued, _ = start_goal_input(goals)
    QueuedRunRepository(store.database).change(session, queued["id"], queued["userInput"], None,
        operation_id=str(uuid4()), target_run_id=active["id"])
    assert repository.view(session).goal.status == "active" and repository.pending() == ()
    assert store.has_pending_input(active["id"])
    finish(goals, active["id"])
    assert len(repository.pending()) == 1
    assert repository.view(session).goal.audit_runs == 0


@pytest.mark.parametrize("resumed", [False, True])
def test_steered_goal_cancel_respects_the_latest_activation(goals, resumed):
    from eidos_runtime.persistence.queued_runs import QueuedRunRepository
    store, session, repository = goals
    active, _ = store.create_run(session, "当前任务。")
    queued, _ = start_goal_input(goals)
    QueuedRunRepository(store.database).change(session, queued["id"], queued["userInput"], None,
        operation_id=str(uuid4()), target_run_id=active["id"])
    if resumed:
        change(goals, "pause")
        change(goals, "resume")
    finish(goals, active["id"], "canceled")
    assert repository.view(session).goal.status == ("active" if resumed else "paused")
    assert bool(repository.pending()) == resumed


def test_goal_feedback_cannot_overwrite_the_objective_or_finish_before_queued_input(goals):
    store, session, repository = goals
    original = seed_goal(goals)
    running = accept(goals)
    repository.bind_sample(running["id"], original.sample())
    request = RunStartRequestDto(sessionId=session, userInput="还需要检查另一个分支。", modelId="deepseek-v4.1-flash",
        goalRevision=repository.view(session).goal.revision)
    queued, _ = admit(repository, request, lambda connection: RunRepository(store.database).create_run(
        session, request.user_input, queued=True, transaction_connection=connection))
    repository.bind_sample(running["id"], repository.view(session).goal.sample())
    repository.declare(running["id"], "candidate", GoalDeclaration(status="complete", summary="旧一轮完成声明。",
        evidence=[{"requirement": "全部要求", "proof": "旧轮次中的证据。"}]))
    finish(goals, running["id"])
    assert repository.view(session).goal.status == "active"
    assert repository.view(session).goal.objective == original.objective
    store.claim_next_run_committed()
    assert repository.for_run(queued["id"]).goal.id == original.id


def test_goal_feedback_rejects_a_target_that_finished_before_admission(goals):
    store, session, repository = goals
    seed_goal(goals)
    revision = repository.view(session).goal.revision
    change(goals, "pause")
    request = RunStartRequestDto(sessionId=session, userInput="补充要求。", modelId="deepseek-v4.1-flash", goalRevision=revision)
    with pytest.raises(GoalError, match="目标已变化"):
        admit(repository, request, lambda _connection: pytest.fail("stale goal input must not create a Run"))
    assert store.database.connection().execute("SELECT count(*) FROM runs").fetchone()[0] == 0


def test_plan_admission_pauses_goal_in_the_same_transaction(goals):
    store, session, repository = goals
    seed_goal(goals)
    store.enqueue_run(session, "先制定计划。", work_mode="plan")
    assert repository.view(session).goal.status_reason == "plan_selected"
    assert repository.pending() == ()


def test_goal_feedback_freezes_the_composer_model_and_access_for_future_runs(goals):
    store, session, repository = goals
    original = seed_goal(goals)
    request = RunStartRequestDto(sessionId=session, userInput="按当前设置继续。", modelId="another-model",
        goalRevision=original.revision, approvalMode="full_access", fullAccessConfirmation="full-access-v1")
    admit(repository, request, lambda connection: RunRepository(store.database).create_run(
        session, request.user_input, queued=True, approval_mode="full_access", transaction_connection=connection))
    updated = repository.view(session).goal
    assert updated.objective == original.objective and updated.id == original.id
    assert updated.model.model_id == "another-model" and updated.approval_mode == "full_access"
    assert updated.activation == original.activation + 1


def test_late_cancel_does_not_pause_newer_goal_feedback(goals):
    store, session, repository = goals
    original = seed_goal(goals)
    running = accept(goals)
    repository.bind_sample(running["id"], original.sample())
    request = RunStartRequestDto(sessionId=session, userInput="继续核对全部目标。", modelId="deepseek-v4.1-flash",
        goalRevision=repository.view(session).goal.revision)
    admit(repository, request, lambda connection: RunRepository(store.database).create_run(
        session, request.user_input, queued=True, transaction_connection=connection))
    finish(goals, running["id"], "canceled")
    assert repository.view(session).goal.status == "active"
    assert repository.view(session).goal.activation == original.activation + 1


def test_goal_run_start_replays_the_same_operation_through_rpc(tmp_path):
    import io
    from eidos_runtime.protocol.server import RuntimeServer
    data = tmp_path / "rpc-data"
    data.mkdir(mode=0o700)
    output = io.StringIO()
    server = RuntimeServer(output, data, ScriptedModel([ModelResponse(text="仍在推进。")] * 8))

    def rpc(request_id, method, params):
        server.handle({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        return next(json.loads(line) for line in output.getvalue().splitlines() if json.loads(line).get("id") == request_id)

    try:
        assert "result" in rpc("client-1", "initialize", {"client": {"name": "test", "version": "1"}, "protocolVersion": 1})
        workspace = tmp_path / "rpc-workspace"
        workspace.mkdir()
        session = server.store.create_session(str(workspace))["id"]
        params = {"sessionId": session, "userInput": "完成全部目标。", "modelId": "deepseek-v4-pro",
            "goalMode": True, "operationId": str(uuid4())}
        first = rpc("client-2", "run/start", params)
        replayed = rpc("client-3", "run/start", params)
        assert replayed["result"]["id"] == first["result"]["id"], replayed
        assert server.store.database.connection().execute("SELECT count(*) FROM session_goals").fetchone()[0] == 1
    finally:
        server.close()


def test_failed_goal_run_blocks_even_when_plain_user_input_is_queued(goals):
    store, session, repository = goals
    seed_goal(goals)
    running = accept(goals)
    store.enqueue_run(session, "补充一句。")
    finish(goals, running["id"], "failed")
    assert repository.view(session).goal.status == "blocked"
    assert repository.view(session).goal.status_reason == "run_failed"
    assert repository.pending() == ()


@pytest.mark.parametrize("status", ["failed", "stopped"])
def test_older_activation_failure_does_not_block_goal_feedback(goals, status):
    store, session, repository = goals
    original = seed_goal(goals)
    running = accept(goals)
    request = RunStartRequestDto(sessionId=session, userInput="还要核对错误分支。", modelId="deepseek-v4.1-flash",
        goalRevision=repository.view(session).goal.revision)
    admit(repository, request, lambda connection: RunRepository(store.database).create_run(
        session, request.user_input, queued=True, transaction_connection=connection))
    # The active Run builds its next Step after the feedback was admitted.
    repository.bind_sample(running["id"], repository.view(session).goal.sample())
    finish(goals, running["id"], status)
    assert repository.view(session).goal.status == "active"
    assert repository.view(session).goal.activation == original.activation + 1

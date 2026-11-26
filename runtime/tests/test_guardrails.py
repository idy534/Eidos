from __future__ import annotations

from datetime import datetime, timedelta, timezone
import io
import json
import sqlite3
import threading
from uuid import uuid4

import pytest

from eidos_runtime.application.automations import AutomationApplication
from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.application.guardrails import GuardrailsApplication
from eidos_runtime.db.guardrails_migration import migrate_guardrails
from eidos_runtime.db.repositories.runs import RunRepository
from eidos_runtime.db.schema import SCHEMA_VERSION, V56_SCHEMA_SQL
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.domain.automation import AutomationRequest
from eidos_runtime.domain.goal import SessionGoal
from eidos_runtime.domain.guardrails import BackgroundGuardrailPolicy, GuardrailConfigConflict
from eidos_runtime.model.client import (
    CreditsUsage,
    ModelResponse,
    ModelToolCall,
    ModelUsage,
    ScriptedModel,
)
from eidos_runtime.persistence.goals import GoalRepository, _intend
from eidos_runtime.persistence.guardrails import GuardrailSettingsRepository
from eidos_runtime.protocol.goals import GoalRequest
from eidos_runtime.protocol.guardrails import GuardrailsSaveRequest
from eidos_runtime.protocol.server import RuntimeServer
from eidos_runtime.runtime.engine import RuntimeEngine
from eidos_runtime.runtime.state_machine import EventType
from schema_fixtures import remove_guardrails_schema


NOW = datetime(2026, 10, 15, 1, 7, tzinfo=timezone.utc)


def _setup_store(tmp_path):
    store = SessionStore(tmp_path / "data")
    store.initialize()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "hello.txt").write_text("hello\n", encoding="utf-8")
    session = store.create_session(str(workspace))["id"]
    return store, session


def _seed_goal(store: SessionStore, session_id: str) -> SessionGoal:
    from eidos_runtime.db.database import now_ms
    from eidos_runtime.domain.automation import AutomationModel

    goal = SessionGoal(
        id=str(uuid4()),
        session_id=session_id,
        objective="检查 hello.txt 并证明任务完成。",
        status="active",
        revision=1,
        objective_revision=1,
        activation=1,
        model=AutomationModel(model_id="deepseek-v4.1-flash"),
        approval_mode="manual",
        created_at=now_ms(),
        updated_at=now_ms(),
    )
    with store.database.transaction() as connection:
        connection.execute(
            "INSERT INTO session_goals(id,session_id,status,revision,goal_json) VALUES(?,?,?,?,?)",
            (goal.id, session_id, goal.status, goal.revision, goal.model_dump_json()),
        )
        _intend(connection, goal, "continuation")
    return goal


def _accept_goal_run(store: SessionStore, session_id: str, repository: GoalRepository) -> dict[str, object]:
    intent, _ = repository.pending()[0]
    return repository.accept(
        intent,
        lambda connection: RunRepository(store.database).create_run(
            session_id,
            "",
            internal_input=True,
            transaction_connection=connection,
        ),
    )[0]


def test_guardrail_repository_application_and_rpc_round_trip(tmp_path):
    output = io.StringIO()
    data = tmp_path / "rpc-data"
    data.mkdir(mode=0o700)
    server = RuntimeServer(output, data)
    try:
        server.handle({
            "jsonrpc": "2.0",
            "id": "client-init",
            "method": "initialize",
            "params": {"client": {"name": "test", "version": "1"}, "protocolVersion": 1},
        })
        assert server.initialized, output.getvalue()
        repo = GuardrailSettingsRepository(server.store.database)
        app = GuardrailsApplication(repo)

        default_settings = repo.read()
        assert default_settings.revision == 0
        assert default_settings.policy.max_tokens_per_cycle is None
        assert default_settings.policy.max_cost_credits_per_cycle is None
        assert default_settings.policy.max_idle_rounds == 3

        saved = repo.save(
            BackgroundGuardrailPolicy(
                max_tokens_per_cycle=5000,
                max_cost_credits_per_cycle=12.5,
                max_idle_rounds=4,
            ),
            expected_revision=0,
        )
        assert saved.revision == 1
        assert repo.read() == saved
        event = server.store.connection.execute(
            "SELECT event_type, payload_json FROM events WHERE event_type=? ORDER BY id DESC LIMIT 1",
            (EventType.GUARDRAILS_CHANGED.value,),
        ).fetchone()
        assert event is not None
        assert json.loads(event["payload_json"]) == {"id": "guardrails"}

        with pytest.raises(GuardrailConfigConflict):
            repo.save(BackgroundGuardrailPolicy(), expected_revision=0)
        with pytest.raises(ApplicationError, match="后台护栏设置已经改变") as exc_info:
            app.save(
                GuardrailsSaveRequest(
                    expectedRevision=0,
                    policy=BackgroundGuardrailPolicy(),
                )
            )
        assert exc_info.value.code == "GUARDRAIL_CONFIG_CONFLICT"

        server.handle({"jsonrpc": "2.0", "id": "client-read-1", "method": "guardrails/read", "params": {}})
        server.handle({
            "jsonrpc": "2.0",
            "id": "client-save-2",
            "method": "guardrails/save",
            "params": {
                "expectedRevision": 1,
                "policy": {
                    "maxTokensPerCycle": 2000,
                    "maxCostCreditsPerCycle": 5.0,
                    "maxIdleRounds": None,
                },
            },
        })
        messages = [json.loads(line) for line in output.getvalue().splitlines() if line.strip()]
        read_resp = next(m for m in messages if m.get("id") == "client-read-1")
        save_resp = next(m for m in messages if m.get("id") == "client-save-2")
        assert read_resp["result"] == {
            "revision": 1,
            "policy": {
                "maxTokensPerCycle": 5000,
                "maxCostCreditsPerCycle": 12.5,
                "maxIdleRounds": 4,
            },
        }
        assert save_resp["result"] == {
            "revision": 2,
            "policy": {
                "maxTokensPerCycle": 2000,
                "maxCostCreditsPerCycle": 5.0,
            },
        }
        assert repo.read().policy.max_idle_rounds is None
    finally:
        server.close()


def test_guardrails_schema_migration_and_rollback(tmp_path):
    store = SessionStore(tmp_path / "migrate-data")
    store.initialize()
    assert store.connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    with store.connection:
        remove_guardrails_schema(store.connection)
        store.connection.execute("PRAGMA user_version = 56")
    store.close()

    reopened = SessionStore(tmp_path / "migrate-data")
    reopened.initialize()
    try:
        assert reopened.connection.execute("PRAGMA user_version").fetchone()[0] == 57
        tables = {
            row[0]
            for row in reopened.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert {"background_guardrail_settings", "automation_guardrail_state"} <= tables
    finally:
        reopened.close()

    broken = sqlite3.connect(":memory:")
    try:
        broken.executescript(
            V56_SCHEMA_SQL + "CREATE TABLE background_guardrail_settings(test); PRAGMA user_version=56;"
        )
        with pytest.raises(sqlite3.Error):
            migrate_guardrails(broken)
        assert broken.execute("PRAGMA user_version").fetchone()[0] == 56
        assert (
            broken.execute(
                "SELECT 1 FROM sqlite_master WHERE name='automation_guardrail_state'"
            ).fetchone()
            is None
        )
    finally:
        broken.close()


def test_goal_token_guardrail_pauses_resets_on_resume_and_re_pauses(tmp_path):
    store, session = _setup_store(tmp_path)
    try:
        guardrails = GuardrailSettingsRepository(store.database)
        guardrails.save(
            BackgroundGuardrailPolicy(max_tokens_per_cycle=1000, max_idle_rounds=None),
            expected_revision=0,
        )
        goals = GoalRepository(store.database)
        _seed_goal(store, session)

        # Cycle 1: 600 + 500 = 1100 >= 1000 -> pauses
        run1 = _accept_goal_run(store, session, goals)
        RuntimeEngine(
            store,
            ScriptedModel([
                ModelResponse(text="第一轮推进", usage=ModelUsage(input_tokens=600, output_tokens=500)),
            ]),
            lambda _msg: None,
        ).run(run1["id"], threading.Event())
        paused = goals.view(session).goal
        assert paused is not None
        assert paused.status == "paused"
        assert paused.status_reason == "guardrail_tokens_exceeded"
        assert paused.activation == 1

        # Resume starts Cycle 2 with counters recalculated from 0
        resumed = goals.execute(
            GoalRequest(
                action="resume",
                sessionId=session,
                expectedRevision=paused.revision,
                operationId=str(uuid4()),
            )
        ).goal
        assert resumed is not None
        assert resumed.status == "active"
        assert resumed.activation == 2
        assert resumed.consecutive_idle_runs == 0

        # Cycle 2 Run 1: 400 tokens < 1000 -> remains active
        run2 = _accept_goal_run(store, session, goals)
        RuntimeEngine(
            store,
            ScriptedModel([
                ModelResponse(text="恢复后第一轮", usage=ModelUsage(input_tokens=250, output_tokens=150)),
            ]),
            lambda _msg: None,
        ).run(run2["id"], threading.Event())
        after_run2 = goals.view(session).goal
        assert after_run2 is not None
        assert after_run2.status == "active"

        # Cycle 2 Run 2: cumulative 400 + 700 = 1100 >= 1000 -> pauses again
        run3 = _accept_goal_run(store, session, goals)
        RuntimeEngine(
            store,
            ScriptedModel([
                ModelResponse(text="恢复后第二轮", usage=ModelUsage(input_tokens=500, output_tokens=200)),
            ]),
            lambda _msg: None,
        ).run(run3["id"], threading.Event())
        re_paused = goals.view(session).goal
        assert re_paused is not None
        assert re_paused.status == "paused"
        assert re_paused.status_reason == "guardrail_tokens_exceeded"
        assert re_paused.activation == 2
    finally:
        store.close()


def test_goal_cost_and_idle_guardrails_enforce_and_reset_on_resume(tmp_path):
    store, session = _setup_store(tmp_path)
    try:
        guardrails = GuardrailSettingsRepository(store.database)
        guardrails.save(
            BackgroundGuardrailPolicy(
                max_cost_credits_per_cycle=5.0,
                max_idle_rounds=2,
            ),
            expected_revision=0,
        )
        goals = GoalRepository(store.database)
        _seed_goal(store, session)

        # Idle 1 (with distinct read still no new write/test progress -> idle=1), cost 3.0 < 5.0 -> active
        run1 = _accept_goal_run(store, session, goals)
        RuntimeEngine(
            store,
            ScriptedModel([
                ModelResponse(
                    tool_calls=(ModelToolCall(str(uuid4()), "read_file", {"path": "hello.txt"}),),
                    usage=ModelUsage(credits_usage=CreditsUsage(amount=1.0, source="request")),
                ),
                ModelResponse(
                    text="首次读取完成。",
                    usage=ModelUsage(credits_usage=CreditsUsage(amount=2.0, source="request")),
                ),
            ]),
            lambda _msg: None,
        ).run(run1["id"], threading.Event())
        mid = goals.view(session).goal
        assert mid is not None and mid.status == "active"
        assert mid.consecutive_idle_runs == 0  # first read of hello.txt produced a new fingerprint

        # Next two idle runs without fresh progress hit max_idle_rounds=2
        for _ in range(2):
            r = _accept_goal_run(store, session, goals)
            RuntimeEngine(
                store,
                ScriptedModel([ModelResponse(text="继续观察。")]),
                lambda _msg: None,
            ).run(r["id"], threading.Event())
        idle_paused = goals.view(session).goal
        assert idle_paused is not None
        assert idle_paused.status == "paused"
        assert idle_paused.status_reason == "idle_runs"
        assert idle_paused.consecutive_idle_runs == 2

        # Resume resets both idle count and cost budget tracking for activation=2
        resumed = goals.execute(
            GoalRequest(
                action="resume",
                sessionId=session,
                expectedRevision=idle_paused.revision,
                operationId=str(uuid4()),
            )
        ).goal
        assert resumed is not None and resumed.consecutive_idle_runs == 0
        assert resumed.activation == 2

        # Previous cycle had 3.0 credits; in Cycle 2, 4.0 credits stays active (4.0 < 5.0)
        run4 = _accept_goal_run(store, session, goals)
        RuntimeEngine(
            store,
            ScriptedModel([
                ModelResponse(
                    text="成本内推进",
                    usage=ModelUsage(credits_usage=CreditsUsage(amount=4.0, source="request")),
                ),
            ]),
            lambda _msg: None,
        ).run(run4["id"], threading.Event())
        assert goals.view(session).goal.status == "active"

        # Exceed cost in Cycle 2: 4.0 + 1.5 = 5.5 >= 5.0 -> guardrail_cost_exceeded
        run5 = _accept_goal_run(store, session, goals)
        RuntimeEngine(
            store,
            ScriptedModel([
                ModelResponse(
                    text="超成本",
                    usage=ModelUsage(credits_usage=CreditsUsage(amount=1.5, source="request")),
                ),
            ]),
            lambda _msg: None,
        ).run(run5["id"], threading.Event())
        cost_paused = goals.view(session).goal
        assert cost_paused is not None
        assert cost_paused.status == "paused"
        assert cost_paused.status_reason == "guardrail_cost_exceeded"

        # Disabling max_idle_rounds allows multiple idle runs after resume
        guardrails.save(BackgroundGuardrailPolicy(max_idle_rounds=None), expected_revision=1)
        goals.execute(
            GoalRequest(
                action="resume",
                sessionId=session,
                expectedRevision=cost_paused.revision,
                operationId=str(uuid4()),
            )
        )
        for _ in range(4):
            r = _accept_goal_run(store, session, goals)
            RuntimeEngine(
                store,
                ScriptedModel([ModelResponse(text="不限空转。")]),
                lambda _msg: None,
            ).run(r["id"], threading.Event())
        assert goals.view(session).goal.status == "active"
    finally:
        store.close()


def _run_automation_occurrence(
    store: SessionStore,
    session_id: str,
    repo,
    task_id: str,
    scheduled_for: datetime,
    model_responses: list[ModelResponse],
) -> None:
    occurrence = repo.claim(task_id, scheduled_for.isoformat())
    assert occurrence is not None and occurrence.status == "preparing"
    run, _ = repo.accept(
        occurrence.id,
        lambda connection, _origin: RunRepository(store.database).create_run(
            session_id,
            "scheduled",
            queued=False,
            transaction_connection=connection,
        ),
        session_id,
    )
    RuntimeEngine(store, ScriptedModel(model_responses), lambda _msg: None).run(
        run["id"], threading.Event()
    )


def test_automation_guardrails_pause_reset_on_resume_and_re_pause(tmp_path):
    store, session = _setup_store(tmp_path)
    try:
        guardrails = GuardrailSettingsRepository(store.database)
        guardrails.save(
            BackgroundGuardrailPolicy(max_tokens_per_cycle=1000, max_idle_rounds=None),
            expected_revision=0,
        )
        app = AutomationApplication(
            store,
            resolve_model=lambda value: value,
            publish=lambda: None,
            launch=lambda _task: None,
            clock=lambda: NOW,
        )
        task = app.execute(
            AutomationRequest.model_validate({
                "mode": "create",
                "kind": "cron",
                "name": "定时检查",
                "prompt": "检查部署",
                "schedule": {"expression": "*/10 * * * *", "timezone": "Asia/Shanghai"},
                "sessionId": session,
                "model": {"modelId": "deepseek-v4.1-flash"},
            }),
            context_session_id=session,
            operation_id=str(uuid4()),
        ).automation
        assert task is not None

        # Cycle 1: exceeds 1000 tokens -> pauses with guardrail_tokens_exceeded
        _run_automation_occurrence(
            store,
            session,
            app.repository,
            task.id,
            NOW,
            [ModelResponse(text="首次执行", usage=ModelUsage(input_tokens=700, output_tokens=400))],
        )
        paused = app.repository.read(task.id)
        assert paused.status == "PAUSED"
        assert paused.pause_reason == "guardrail_tokens_exceeded"
        before_resume_max_seq = int(
            store.connection.execute("SELECT coalesce(max(creation_seq),0) FROM runs").fetchone()[0]
        )

        # Resume -> increments guardrail cycle and sets cycle_after_run_seq past prior runs
        resumed = app.execute(
            AutomationRequest.model_validate({
                "mode": "update",
                "id": task.id,
                "expectedRevision": paused.revision,
                "changes": {"status": "ACTIVE"},
            }),
            context_session_id=session,
            operation_id=str(uuid4()),
        ).automation
        assert resumed is not None and resumed.status == "ACTIVE"
        state = store.connection.execute(
            "SELECT cycle, consecutive_idle_runs, cycle_after_run_seq FROM automation_guardrail_state WHERE automation_id=?",
            (task.id,),
        ).fetchone()
        assert state["cycle"] == 2
        assert state["consecutive_idle_runs"] == 0
        assert state["cycle_after_run_seq"] >= before_resume_max_seq

        # Cycle 2 Run 1: 400 tokens -> stays ACTIVE
        _run_automation_occurrence(
            store,
            session,
            app.repository,
            task.id,
            NOW + timedelta(minutes=10),
            [ModelResponse(text="恢复首轮", usage=ModelUsage(input_tokens=250, output_tokens=150))],
        )
        assert app.repository.read(task.id).status == "ACTIVE"

        # Cycle 2 Run 2: cumulative 400 + 700 = 1100 >= 1000 -> pauses AGAIN
        _run_automation_occurrence(
            store,
            session,
            app.repository,
            task.id,
            NOW + timedelta(minutes=20),
            [ModelResponse(text="恢复二轮", usage=ModelUsage(input_tokens=500, output_tokens=200))],
        )
        re_paused = app.repository.read(task.id)
        assert re_paused.status == "PAUSED"
        assert re_paused.pause_reason == "guardrail_tokens_exceeded"

        # Switch to cost + idle guardrails, resume for Cycle 3
        guardrails.save(
            BackgroundGuardrailPolicy(
                max_tokens_per_cycle=None,
                max_cost_credits_per_cycle=4.0,
                max_idle_rounds=2,
            ),
            expected_revision=1,
        )
        app.execute(
            AutomationRequest.model_validate({
                "mode": "update",
                "id": task.id,
                "expectedRevision": re_paused.revision,
                "changes": {"status": "ACTIVE"},
            }),
            context_session_id=session,
            operation_id=str(uuid4()),
        )

        # Two unsuccessful Runs without exceeding cost -> guardrail_idle_runs
        _run_automation_occurrence(
            store,
            session,
            app.repository,
            task.id,
            NOW + timedelta(minutes=30),
            [ModelResponse(finish_reason="error", usage=ModelUsage(credits_usage=CreditsUsage(amount=1.0, source="request")))],
        )
        assert app.repository.read(task.id).status == "ACTIVE"
        _run_automation_occurrence(
            store,
            session,
            app.repository,
            task.id,
            NOW + timedelta(minutes=40),
            [ModelResponse(finish_reason="error", usage=ModelUsage(credits_usage=CreditsUsage(amount=1.0, source="request")))],
        )
        idle_paused = app.repository.read(task.id)
        assert idle_paused.status == "PAUSED"
        assert idle_paused.pause_reason == "guardrail_idle_runs"

        # Resume Cycle 4 and test cost breach (3.0 stays active -> +1.5 breaches 4.0 -> guardrail_cost_exceeded)
        guardrails.save(
            BackgroundGuardrailPolicy(
                max_cost_credits_per_cycle=4.0,
                max_idle_rounds=None,
            ),
            expected_revision=2,
        )
        resumed4 = app.execute(
            AutomationRequest.model_validate({
                "mode": "update",
                "id": task.id,
                "expectedRevision": idle_paused.revision,
                "changes": {"status": "ACTIVE"},
            }),
            context_session_id=session,
            operation_id=str(uuid4()),
        ).automation
        assert resumed4 is not None and resumed4.status == "ACTIVE"

        _run_automation_occurrence(
            store,
            session,
            app.repository,
            task.id,
            NOW + timedelta(minutes=50),
            [ModelResponse(text="成本1", usage=ModelUsage(credits_usage=CreditsUsage(amount=3.0, source="request")))],
        )
        assert app.repository.read(task.id).status == "ACTIVE"
        _run_automation_occurrence(
            store,
            session,
            app.repository,
            task.id,
            NOW + timedelta(minutes=60),
            [ModelResponse(text="成本2", usage=ModelUsage(credits_usage=CreditsUsage(amount=1.5, source="request")))],
        )
        cost_paused = app.repository.read(task.id)
        assert cost_paused.status == "PAUSED"
        assert cost_paused.pause_reason == "guardrail_cost_exceeded"
    finally:
        store.close()


def _create_cron_automation(store: SessionStore, session: str) -> tuple[AutomationApplication, str]:
    app = AutomationApplication(
        store,
        resolve_model=lambda value: value,
        publish=lambda: None,
        launch=lambda _task: None,
        clock=lambda: NOW,
    )
    task = app.execute(
        AutomationRequest.model_validate({
            "mode": "create",
            "kind": "cron",
            "name": "每日汇总",
            "prompt": "汇总今天的变化",
            "schedule": {"expression": "*/10 * * * *", "timezone": "Asia/Shanghai"},
            "sessionId": session,
            "model": {"modelId": "deepseek-v4.1-flash"},
        }),
        context_session_id=session,
        operation_id=str(uuid4()),
    ).automation
    assert task is not None
    return app, task.id


def test_successful_scheduled_runs_without_tools_never_pause_by_default(tmp_path):
    store, session = _setup_store(tmp_path)
    try:
        # No saved settings: the default idle limit (3) applies.
        assert GuardrailSettingsRepository(store.database).read().policy.max_idle_rounds == 3
        app, task_id = _create_cron_automation(store, session)
        for index in range(5):
            _run_automation_occurrence(
                store, session, app.repository, task_id,
                NOW + timedelta(minutes=10 * index),
                [ModelResponse(text="今天没有新的变化。")],
            )
        assert app.repository.read(task_id).status == "ACTIVE"
    finally:
        store.close()


def test_automation_saved_before_guardrails_starts_its_cycle_at_the_next_run(tmp_path):
    store, session = _setup_store(tmp_path)
    try:
        app, task_id = _create_cron_automation(store, session)
        _run_automation_occurrence(
            store, session, app.repository, task_id, NOW,
            [ModelResponse(text="历史执行", usage=ModelUsage(input_tokens=600, output_tokens=300))],
        )
        # A task saved before schema v57 has no guardrail cycle yet.
        with store.database.transaction() as connection:
            connection.execute("DELETE FROM automation_guardrail_state WHERE automation_id=?", (task_id,))
        GuardrailSettingsRepository(store.database).save(
            BackgroundGuardrailPolicy(max_tokens_per_cycle=1000, max_idle_rounds=None),
            expected_revision=0,
        )

        _run_automation_occurrence(
            store, session, app.repository, task_id, NOW + timedelta(minutes=10),
            [ModelResponse(text="升级后首轮", usage=ModelUsage(input_tokens=150, output_tokens=50))],
        )

        # 900 historical tokens + 200 new would breach 1000; only the new Run counts.
        assert app.repository.read(task_id).status == "ACTIVE"
    finally:
        store.close()

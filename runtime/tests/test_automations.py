from __future__ import annotations

from datetime import datetime, timedelta, timezone
import io
import json
import sqlite3
import threading
import time
from uuid import uuid4, uuid5, NAMESPACE_URL

import pytest
from pydantic import ValidationError

from eidos_runtime.application.automations import AutomationApplication
from eidos_runtime.db.automation_migration import migrate_automations
from eidos_runtime.db.repositories.runs import RunRepository
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.domain.automation import AutomationError, AutomationRequest, Schedule
from eidos_runtime.infrastructure.automation_time import future_times, normalize
from eidos_runtime.model.client import ModelToolCall, ScriptedModel, ModelResponse
from eidos_runtime.protocol.server import RuntimeServer
from eidos_runtime.runtime.automation_scheduler import AutomationScheduler
from eidos_runtime.runtime.events import RuntimeEvents
from eidos_runtime.runtime.tool_dispatcher import ToolDispatcher
from eidos_runtime.tools.automation import automation_entry
from eidos_runtime.tools.registry import ToolRegistry

NOW = datetime(2026, 10, 15, 1, 7, tzinfo=timezone.utc)


@pytest.fixture
def automation(tmp_path):
    store = SessionStore(tmp_path / "data")
    store.initialize()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = store.create_session(str(workspace))["id"]
    launched = []
    application = AutomationApplication(store, resolve_model=lambda value: value,
        publish=lambda: None, launch=launched.append, clock=lambda: NOW)
    store.automations = application
    yield store, session, application, launched
    store.close()


def create(fixture, **overrides):
    _store, session, application, _launched = fixture
    command = {"mode": "create", "kind": "cron", "name": "检查部署", "prompt": "检查状态",
        "schedule": {"expression": "*/10 * * * *", "timezone": "Asia/Shanghai"},
        "model": {"modelId": "test"}, **overrides}
    result = application.execute(AutomationRequest.model_validate(command), context_session_id=session, operation_id=str(uuid4()))
    assert result.automation
    return result.automation


@pytest.mark.parametrize("expression", ["*/5 * * * *", "*/15 * * * *", "0 */2 * * *", "0,10 * * * *", "0 9 1 * 1", "0 9 * 2 *", "0 9 * * 7", "* * * * *", "0 0 9 * * *", "0 9 L * *"])
def test_reject_unsupported_cron(expression):
    with pytest.raises(AutomationError):
        normalize("cron", Schedule(expression=expression))


@pytest.mark.parametrize("expression", ["*/10 * * * *", "*/30 * * * *", "0 * * * *", "0 9 * * *", "0 9 * * 1-5", "0 9 31 * *"])
def test_preview_is_future_clock_aligned_and_bounded(expression):
    schedule = normalize("cron", Schedule(expression=expression, timezone="Asia/Shanghai"))
    result = future_times("cron", schedule, NOW)
    assert 1 <= len(result) <= 5
    assert all(datetime.fromisoformat(value) > NOW for value in result)
    if expression == "*/10 * * * *":
        assert result[0] == "2026-10-15T01:10:00+00:00"


def test_nonexistent_dst_time_advances_to_next_valid_day():
    schedule = normalize("cron", Schedule(expression="30 2 * * *", timezone="America/New_York"))
    values = future_times("cron", schedule, datetime(2026, 3, 8, 5, tzinfo=timezone.utc), 1)
    assert values == ["2026-03-09T06:30:00+00:00"]


def test_receipt_identity_preserves_explicit_projectless_target():
    command = {"mode": "update", "id": str(uuid4()), "expectedRevision": 1,
        "changes": {"target": {"sessionId": str(uuid4()), "projectId": None}}}
    explicit = AutomationRequest.model_validate(command).receipt_request()
    assert explicit["changes"]["target"]["projectId"] is None
    del command["changes"]["target"]["projectId"]
    assert AutomationRequest.model_validate(command).receipt_request() != explicit


@pytest.mark.parametrize("command", [
    {"mode": "view", "id": "ping"}, {"mode": "view", "approvalMode": "auto_review"},
    {"mode": "create", "name": "x"}, {"mode": "delete", "id": str(uuid4()), "expectedRevision": True},
    {"mode": "update", "id": str(uuid4()), "expectedRevision": 1, "changes": {}},
    {"mode": "update", "id": str(uuid4()), "expectedRevision": 1, "changes": {"approvalMode": None}},
    {"mode": "preview", "kind": "at", "schedule": {"runAt": "2026-10-16", "timezone": None}},
])
def test_strict_mode_contract(command):
    with pytest.raises(ValidationError):
        AutomationRequest.model_validate(command)


def test_default_approval_session_presence_and_projectless(automation):
    _store, session, _app, _ = automation
    independent = create(automation)
    assert independent.target.kind == "new_session" and independent.target.session_id is None
    assert independent.target.project_id is not None
    assert independent.approval_mode == "auto_review"
    bound = create(automation, sessionId=session)
    assert bound.target.session_id == session
    projectless = create(automation, projectId=None)
    assert projectless.target.project_id is None
    with pytest.raises(AutomationError):
        create(automation, sessionId=session, projectId=None)
    with pytest.raises(ValidationError):
        create(automation, approvalMode="full_access")


def test_update_preserves_target_and_approval_and_checks_revision(automation):
    _store, session, app, _ = automation
    task = create(automation, sessionId=session, approvalMode="manual")
    request = AutomationRequest.model_validate({"mode": "update", "id": task.id, "expectedRevision": 1, "changes": {"name": "新名称"}})
    operation_id = str(uuid4())
    result = app.execute(request, context_session_id=session, operation_id=operation_id)
    assert result.automation.target == task.target
    assert result.automation.approval_mode == "manual"
    assert result.automation.revision == 2
    assert app.execute(request, context_session_id=session, operation_id=operation_id).replayed
    with pytest.raises(AutomationError, match="任务已被修改"):
        app.execute(request, context_session_id=session, operation_id=str(uuid4()))
    changed = app.execute(AutomationRequest.model_validate({"mode": "update", "id": task.id, "expectedRevision": 2,
        "changes": {"approvalMode": "auto_review", "target": {"projectId": None}}}), context_session_id=session, operation_id=str(uuid4())).automation
    assert changed.approval_mode == "auto_review" and changed.target.kind == "new_session"


def test_replay_cancel_delete_and_outbox(automation):
    store, session, app, _ = automation
    task = create(automation)
    notices = []
    RuntimeEvents(notices.append, store=store).deliver_pending()
    assert any(value["method"] == "automation/changed" for value in notices)
    cancel = threading.Event()
    cancel.set()
    request = AutomationRequest(mode="delete", id=task.id, expectedRevision=1)
    with pytest.raises(AutomationError):
        app.execute(request, context_session_id=session, operation_id=str(uuid4()), cancel=cancel)
    assert app.repository.read(task.id).deleted_at is None
    deleted = app.execute(request, context_session_id=session, operation_id=str(uuid4())).automation
    assert deleted.deleted_at is not None
    assert store.read_session(session) is not None
    assert app.execute(AutomationRequest(mode="view", scope="all"), context_session_id=session).items == []


def test_claim_admission_is_atomic_busy_and_version_guarded(automation):
    store, session, app, _ = automation
    task = create(automation, sessionId=session)
    repo = app.repository
    occurrence = repo.claim(task.id, NOW.isoformat())
    assert occurrence.status == "preparing"
    assert repo.claim(task.id, NOW.isoformat()) is None
    assert repo.claim(task.id, (NOW + timedelta(minutes=10)).isoformat()).status == "skipped"
    def write(connection, _origin):
        return RunRepository(store.database).create_run(session, "scheduled", queued=True, approval_mode="auto_review", transaction_connection=connection)
    run, item = repo.accept(occurrence.id, write, session)
    assert repo.occurrence(occurrence.id).run_id == run["id"]
    assert repo.origin(run["id"]).automation_id == task.id
    assert store.connection.execute("SELECT eligible FROM memory_source_items WHERE item_id=?", (item["id"],)).fetchone()[0] == 0
    assert repo.claim(task.id, (NOW + timedelta(minutes=20)).isoformat()).reason == "会话忙碌"
    # User messages in the same Session retain memory eligibility.
    _user_run, user_item = store.enqueue_run(session, "real user")
    assert store.connection.execute("SELECT eligible FROM memory_source_items WHERE item_id=?", (user_item["id"],)).fetchone()[0] == 1
    assert store.connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_admission_rolls_back_run_and_occurrence_on_failure(automation):
    store, session, app, _ = automation
    task = create(automation, sessionId=session)
    occurrence = app.repository.claim(task.id, NOW.isoformat())
    def fail(connection, _origin):
        RunRepository(store.database).create_run(session, "scheduled", queued=True, transaction_connection=connection)
        raise RuntimeError("commit failed")
    with pytest.raises(RuntimeError):
        app.repository.accept(occurrence.id, fail, session)
    assert store.connection.execute("SELECT count(*) FROM runs").fetchone()[0] == 0
    assert app.repository.occurrence(occurrence.id).status == "preparing"
    app.execute(AutomationRequest.model_validate({"mode": "update", "id": task.id, "expectedRevision": 1, "changes": {"status": "PAUSED"}}), context_session_id=session, operation_id=str(uuid4()))
    with pytest.raises(AutomationError):
        app.repository.accept(occurrence.id, fail, session)


def test_once_stops_on_submission_and_unknown_effects_pause_only_own_plan(automation):
    store, session, app, _ = automation
    once = create(automation, kind="at", schedule={"runAt": "2026-10-16T09:00:00+08:00"}, sessionId=session)
    occurrence = app.repository.claim(once.id, "2026-10-16T01:00:00+00:00")
    run, _ = app.repository.accept(occurrence.id, lambda connection, _origin: RunRepository(store.database).create_run(session, "once", queued=True, transaction_connection=connection), session)
    assert app.repository.read(once.id).pause_reason == "one_shot_fired"
    store.cancel_run(run["id"])
    store.connection.execute("UPDATE runs SET reconciliation_required=1 WHERE id=?", (run["id"],))
    with pytest.raises(AutomationError, match="需要核验"):
        app.execute(AutomationRequest.model_validate({"mode": "update", "id": once.id, "expectedRevision": 2, "changes": {"status": "ACTIVE", "schedule": {"runAt": "2026-10-17T09:00:00+08:00"}}}), context_session_id=session, operation_id=str(uuid4()))
    other = create(automation)
    assert app.repository.read(other.id).status == "ACTIVE"


def test_scheduler_future_only_reset_and_exact_claim(automation):
    _store, _session, app, launched = automation
    task = create(automation)
    scheduler = AutomationScheduler(app.repository, launched.append, lambda: True, lambda: None)
    scheduler.tick(NOW)
    scheduler.tick(NOW + timedelta(minutes=3))  # A sleep gap resets future time.
    assert launched == []
    # Poll continuously through the next scheduled minute.
    start = NOW + timedelta(minutes=12, seconds=40)
    scheduler.refresh()
    scheduler.tick(start)
    for seconds in (5, 10, 15, 20, 25):
        scheduler.tick(start + timedelta(seconds=seconds))
    assert len(launched) == 1
    assert app.repository.occurrence(launched[0]).automation_id == task.id
    scheduler.tick(start + timedelta(seconds=26))
    assert len(launched) == 1


def test_scheduler_pauses_expired_once_without_catchup(automation):
    _store, _session, app, launched = automation
    task = create(automation, kind="at", schedule={"runAt": (NOW + timedelta(seconds=10)).isoformat()})
    scheduler = AutomationScheduler(app.repository, launched.append, lambda: True, lambda: None)
    scheduler.tick(NOW + timedelta(seconds=20))
    assert launched == []
    assert app.repository.history(task.id)[0] == []
    saved = app.repository.read(task.id)
    assert saved.status == "PAUSED" and saved.pause_reason == "执行时间已过去"
    scheduler.tick(NOW + timedelta(seconds=25))
    assert app.repository.read(task.id).revision == saved.revision


def test_deferred_single_tool_and_mode_effect(automation):
    _store, _session, app, _ = automation
    entry = automation_entry(app)
    registry = ToolRegistry((entry,))
    assert registry.model_definitions() == ()
    assert len(registry.model_definitions(("automation",))) == 1
    dispatcher = ToolDispatcher(registry)
    assert dispatcher.plan(ModelToolCall("read", "automation", {"mode": "view"})).side_effect == "none"
    assert dispatcher.plan(ModelToolCall("invalid", "automation", {"mode": "view", "status": "bad"})).side_effect == "eidos_state"
    assert dispatcher.plan(ModelToolCall("delete", "automation", {"mode": "delete", "id": str(uuid4()), "expectedRevision": 1})).side_effect == "eidos_state"


def test_migration_rolls_back_and_upgrade_preserves_data(automation):
    store, session, _app, _ = automation
    # Rebuild only new feature objects to simulate a v27 database.
    connection = store.connection
    connection.executescript("DROP TRIGGER automation_memory_insert; DROP TRIGGER automation_origin_memory; DROP TABLE automation_run_origins; DROP TABLE scheduled_occurrences; DROP TABLE scheduled_tasks; PRAGMA user_version=27;")
    migrate_automations(connection)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 28
    assert store.read_session(session)
    with pytest.raises(sqlite3.OperationalError):
        migrate_automations(connection)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 28
    assert not connection.in_transaction


def test_partial_migration_failure_rolls_back_ddl_and_version():
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript("CREATE TABLE automation_run_origins(id TEXT); PRAGMA user_version=27;")
        with pytest.raises(sqlite3.OperationalError):
            migrate_automations(connection)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 27
        assert connection.execute("SELECT name FROM sqlite_master WHERE name IN ('scheduled_tasks','scheduled_occurrences')").fetchall() == []
    finally:
        connection.close()


def test_registered_protocol_and_shutdown_service(tmp_path):
    output = io.StringIO()
    server = RuntimeServer(output, tmp_path / "data", model=ScriptedModel([ModelResponse(text="done")]))
    try:
        server.handle({"jsonrpc": "2.0", "id": "client-init", "method": "initialize", "params": {"protocolVersion": 1, "client": {"name": "test", "version": "1"}}})
        assert server.initialized, output.getvalue()
        server.handle({"jsonrpc": "2.0", "id": "client-list", "method": "schedule/execute", "params": {"command": {"mode": "view", "scope": "all"}}})
        values = [json.loads(line) for line in output.getvalue().splitlines()]
        result = next(value for value in values if value.get("id") == "client-list")
        assert result["result"]["items"] == []
    finally:
        server.close()


def test_real_loop_search_then_create_and_query(automation):
    store, session, app, _ = automation
    from eidos_runtime.runtime.loop import RuntimeEngine
    run, _ = store.create_run(session, "每十分钟检查状态，新建会话")
    responses = [
        ModelResponse(tool_calls=(ModelToolCall("find", "tool_search", {"query": "automation", "limit": 1}),)),
        ModelResponse(tool_calls=(ModelToolCall("create", "automation", {"mode": "create", "kind": "cron", "name": "状态检查", "prompt": "检查状态",
            "schedule": {"expression": "*/10 * * * *", "timezone": "Asia/Shanghai"}}),)),
        ModelResponse(tool_calls=(ModelToolCall("view", "automation", {"mode": "view"}),)),
        ModelResponse(text="任务已创建。"),
    ]
    RuntimeEngine(store, ScriptedModel(responses), lambda _notice: None).run(run["id"], threading.Event())
    assert store.read_run(run["id"])["status"] == "succeeded"
    tasks = app.repository.list()
    assert len(tasks) == 1, [tuple(row) for row in store.connection.execute("SELECT tool_name,result_json FROM tool_calls")]
    assert tasks[0][1].approval_mode == "auto_review"
    assert store.connection.execute("SELECT count(*) FROM durable_intents").fetchone()[0] == 1


@pytest.mark.parametrize("existing", [True, False])
def test_manual_trigger_launches_real_run_with_origin_and_default_review(tmp_path, existing):
    from eidos_runtime.model.config import DEFAULT_MODEL_ID
    output = io.StringIO()
    server = RuntimeServer(output, tmp_path / "data", model=ScriptedModel([ModelResponse(text="已完成检查。")]))
    try:
        server.handle({"jsonrpc": "2.0", "id": "client-init", "method": "initialize", "params": {"protocolVersion": 1, "client": {"name": "test", "version": "1"}}})
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        session = server.store.create_session(str(workspace))["id"]
        app = server._automation_application()
        task = app.execute(AutomationRequest.model_validate({"mode": "create", "kind": "cron", "name": "状态检查", "prompt": "检查当前状态。",
            "schedule": {"expression": "*/10 * * * *"}, "model": {"modelId": DEFAULT_MODEL_ID},
            **({"sessionId": session} if existing else {})}), context_session_id=session, operation_id=str(uuid4())).automation
        result = app.run_now(task.id, str(uuid4()))
        until = time.monotonic() + 10
        record = app.repository.occurrence(result.occurrence_id)
        while record.status == "preparing" and time.monotonic() < until:
            time.sleep(.01)
            record = app.repository.occurrence(record.id)
        assert record.status == "submitted", record
        assert (record.session_id == session) is existing
        run = server.store.read_run(record.run_id)
        assert run["approvalMode"] == "auto_review"
        assert app.repository.origin(run["id"]).automation_id == task.id
        while server.store.read_run(run["id"])["status"] in {"queued", "running", "finalizing"} and time.monotonic() < until:
            time.sleep(.01)
        assert server.store.read_run(run["id"])["status"] == "succeeded"
        assert server.store.approval_user_evidence(run["id"]) == ()
    finally:
        server.close()


@pytest.mark.parametrize("committed", [True, False])
def test_restart_recovers_known_automation_receipt_without_replaying(automation, committed):
    store, session, app, _ = automation
    run, _ = store.create_run(session, "创建状态检查")
    entry = automation_entry(app)
    request = AutomationRequest.model_validate({"mode": "create", "kind": "cron", "name": "检查", "prompt": "检查状态", "schedule": {"expression": "*/10 * * * *"}})
    item = store.create_tool_item_committed(run["id"], 0, 0, "create", "automation", request.model_dump_json(exclude_unset=True, by_alias=True), provenance=entry.provenance.model_dump(mode="json", by_alias=True)).value
    store.begin_durable_intent(item["id"], preconditions={}, approval_required=False)
    if committed:
        app.execute(request, context_session_id=session, run_id=run["id"], operation_id=str(uuid5(NAMESPACE_URL, "automation/tool/" + item["id"])))
    directory = store.database.data_directory
    store.close()
    reopened = SessionStore(directory)
    try:
        reopened.initialize()
        row = reopened.connection.execute("SELECT result_json FROM tool_calls WHERE item_id=?", (item["id"],)).fetchone()
        result = json.loads(row[0])
        assert result["reconciliationRequired"] is False
        assert result["sideEffectsMayExist"] is committed
        assert reopened.connection.execute("SELECT count(*) FROM scheduled_tasks").fetchone()[0] == int(committed)
        assert not reopened.read_run(run["id"]).get("reconciliationRequired", False)
    finally:
        reopened.close()


def test_regenerate_lineage_and_origin_are_atomic_and_creation_is_deduplicated(automation):
    store, session, app, _ = automation
    run, _ = store.create_run(session, "创建检查任务")
    request = AutomationRequest.model_validate({"mode": "create", "kind": "cron", "name": "检查", "prompt": "检查状态", "schedule": {"expression": "*/10 * * * *"}})
    first = app.execute(request, context_session_id=session, run_id=run["id"], operation_id=str(uuid4()))
    store.cancel_run(run["id"])
    replacement, _ = RunRepository(store.database).create_run(session, "创建检查任务", queued=True, regenerate_source_run_id=run["id"])
    repeated = app.execute(request, context_session_id=session, run_id=replacement["id"], operation_id=str(uuid4()))
    assert repeated.replayed and repeated.automation.id == first.automation.id
    assert len(app.repository.list()) == 1
    store.cancel_run(replacement["id"])
    task = create(automation, sessionId=session)
    # A generated Run inherits automatic restrictions before its input is inserted.
    with store.lock, store.connection as connection:
        from eidos_runtime.domain.automation import AutomationOrigin
        origin = AutomationOrigin(automation_id=task.id, revision=1, occurrence_id="o", scheduled_for=NOW.isoformat())
        connection.execute("INSERT INTO automation_run_origins(run_id,origin_json) VALUES(?,?)", (replacement["id"], origin.model_dump_json(by_alias=True)))
    regenerated, item = RunRepository(store.database).create_run(session, "automatic", queued=True, regenerate_source_run_id=replacement["id"])
    assert app.repository.origin(regenerated["id"]) == origin
    assert store.connection.execute("SELECT eligible FROM memory_source_items WHERE item_id=?", (item["id"],)).fetchone()[0] == 0
    with pytest.raises(AutomationError):
        app.execute(request, context_session_id=session, run_id=regenerated["id"], operation_id=str(uuid4()))


def test_regenerated_automatic_runs_block_overlap_and_require_verification(automation):
    store, session, app, _ = automation
    task = create(automation)
    occurrence = app.repository.claim(task.id, NOW.isoformat())
    run, _ = app.repository.accept(occurrence.id, lambda connection, _origin:
        RunRepository(store.database).create_run(session, "scheduled", queued=True, transaction_connection=connection), session)
    store.cancel_run(run["id"])
    replacement, _ = RunRepository(store.database).create_run(session, "scheduled", queued=True, regenerate_source_run_id=run["id"])
    skipped = app.repository.claim(task.id, (NOW + timedelta(minutes=10)).isoformat())
    assert skipped.status == "skipped" and skipped.reason == "上次执行未结束"
    store.cancel_run(replacement["id"])
    with store.lock, store.connection as connection:
        connection.execute("UPDATE runs SET reconciliation_required=1 WHERE id=?", (replacement["id"],))
    assert app.repository.claim(task.id, (NOW + timedelta(minutes=20)).isoformat()) is None
    assert app.repository.read(task.id).status == "PAUSED"


def test_atomic_regeneration_rechecks_current_source_before_creating_run(automation):
    from eidos_runtime.db.errors import InvalidRunStateError
    store, session, _app, _ = automation
    first, _ = store.create_run(session, "first")
    with pytest.raises(InvalidRunStateError, match="not terminal"):
        RunRepository(store.database).create_run(session, "first", queued=True, regenerate_source_run_id=first["id"])
    store.cancel_run(first["id"])
    newer, _ = store.create_run(session, "newer")
    store.cancel_run(newer["id"])
    with pytest.raises(InvalidRunStateError, match="latest visible"):
        RunRepository(store.database).create_run(session, "first", queued=True, regenerate_source_run_id=first["id"])
    assert len(store.read_session_snapshot(session)["runs"]) == 2


def test_late_cancel_preserves_saved_task_receipt(automation):
    store, session, app, _ = automation
    from eidos_runtime.runtime.fault_injection import injected_faults
    from eidos_runtime.runtime.loop import RuntimeEngine
    cancel = threading.Event()
    class LateCancel:
        calls = 0
        def hit(self, point):
            if point == "tool_late_result":
                self.calls += 1
                if self.calls == 2:
                    cancel.set()
    run, _ = store.create_run(session, "创建定时任务")
    responses = [ModelResponse(tool_calls=(ModelToolCall("search", "tool_search", {"query": "automation", "limit": 1}),)),
        ModelResponse(tool_calls=(ModelToolCall("create", "automation", {"mode": "create", "kind": "cron", "name": "检查", "prompt": "检查", "schedule": {"expression": "*/10 * * * *"}}),))]
    with injected_faults(LateCancel()):
        RuntimeEngine(store, ScriptedModel(responses), lambda _message: None).run(run["id"], cancel)
    assert len(app.repository.list()) == 1
    row = store.connection.execute("SELECT result_json FROM tool_calls WHERE tool_name='automation'").fetchone()
    result = json.loads(row[0])
    assert result["sideEffectsMayExist"] and not result["reconciliationRequired"]
    assert result["data"]["response"]["automation"]["id"] == app.repository.list()[0][1].id


def test_automation_protocol_fixture_matches_python_models():
    from pathlib import Path
    from eidos_runtime.protocol.automations import AutomationExecuteRequest
    from eidos_runtime.domain.automation import AutomationResponse
    fixture = json.loads((Path(__file__).parents[2] / "protocol/fixtures/automations-v1.json").read_text())
    for request in fixture["validRequests"]:
        AutomationExecuteRequest.model_validate(request)
    for request in fixture["invalidRequests"]:
        with pytest.raises(ValidationError):
            AutomationExecuteRequest.model_validate(request)
    for response in fixture["validResponses"]:
        AutomationResponse.model_validate(response)
    for response in fixture["invalidResponses"]:
        with pytest.raises(ValidationError):
            AutomationResponse.model_validate(response)

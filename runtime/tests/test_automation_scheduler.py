from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import io
import time
from uuid import uuid4

import anyio
import pytest

from eidos_runtime.application.automations import AutomationApplication
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.domain.automation import AutomationRequest
from eidos_runtime.model.client import ScriptedModel
from eidos_runtime.model.config import DEFAULT_MODEL_ID
from eidos_runtime.protocol.server import RuntimeServer
from eidos_runtime.runtime.automation_scheduler import AutomationScheduler

pytestmark = pytest.mark.integration
NOW = datetime(2026, 10, 26, 1, 57, tzinfo=timezone.utc)


@dataclass
class Clock:
    now: datetime = NOW
    steady: float = 0.0

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)
        self.steady += seconds


@pytest.fixture
def timer(tmp_path):
    store = SessionStore(tmp_path / "data")
    store.initialize()
    clock = Clock()
    launched = []
    app = AutomationApplication(store, resolve_model=lambda model: model, publish=lambda: scheduler.wake(),
        launch=launched.append, clock=lambda: clock.now)
    scheduler = AutomationScheduler(app.repository, launched.append, lambda: True, lambda: None,
        clock=lambda: clock.now, monotonic=lambda: clock.steady)
    yield app, scheduler, clock, launched
    store.close()


def create(app, **values):
    result = app.execute(AutomationRequest.model_validate({"mode": "create", "kind": "cron", "name": "状态检查",
        "prompt": "检查状态", "model": {"modelId": "test"}, "projectId": None,
        "schedule": {"expression": "*/10 * * * *", "timezone": "Asia/Shanghai"}, **values}),
        context_session_id=None, operation_id=str(uuid4()))
    return result.automation


async def eventually(predicate) -> None:
    with anyio.fail_after(2):
        while not predicate():
            await anyio.sleep(.005)


def test_wait_is_capped_at_five_minutes_and_tracks_the_earliest_task(timer):
    app, scheduler, clock, _ = timer
    scheduler.tick(clock.now)
    assert scheduler.next_delay(clock.now) == 300
    create(app, kind="at", schedule={"runAt": (clock.now + timedelta(seconds=420)).isoformat()})
    scheduler.tick(clock.now)
    assert scheduler.next_delay(clock.now) == 300
    create(app, kind="at", schedule={"runAt": (clock.now + timedelta(seconds=75)).isoformat()})
    scheduler.tick(clock.now)
    assert scheduler.next_delay(clock.now) == 75


@pytest.mark.parametrize("kind", ["cron", "at"])
def test_planned_long_wait_keeps_due_tasks_and_claims_once(timer, kind):
    app, scheduler, clock, launched = timer
    task = create(app, **({"kind": "at", "schedule": {"runAt": (clock.now + timedelta(minutes=3)).isoformat()}} if kind == "at" else {}))
    scheduler.tick(clock.now)
    assert scheduler.next_delay(clock.now) == 180
    clock.advance(180)
    scheduler.tick(clock.now, expected_wait=180)
    assert len(launched) == 1
    assert app.repository.occurrence(launched[0]).automation_id == task.id
    scheduler.tick(clock.now)
    assert len(launched) == 1


def test_five_minute_check_preserves_a_later_cached_deadline(timer):
    app, scheduler, clock, launched = timer
    create(app, kind="at", schedule={"runAt": (clock.now + timedelta(seconds=420)).isoformat()})
    scheduler.tick(clock.now)
    clock.advance(300)
    scheduler.tick(clock.now, expected_wait=300)
    assert launched == [] and scheduler.next_delay(clock.now) == 120
    clock.advance(120)
    scheduler.tick(clock.now, expected_wait=120)
    assert len(launched) == 1


@pytest.mark.parametrize("change", ["oversleep", "clock_forward", "resume"])
def test_unexpected_sleep_or_clock_change_does_not_catch_up(timer, change):
    app, scheduler, clock, launched = timer
    task = create(app, kind="at", schedule={"runAt": (clock.now + timedelta(seconds=30)).isoformat()})
    scheduler.tick(clock.now)
    if change == "oversleep":
        clock.advance(90)
    elif change == "clock_forward":
        clock.now += timedelta(seconds=90)
    else:
        clock.advance(31)
        scheduler.refresh()
    scheduler.tick(clock.now, expected_wait=30)
    assert launched == []
    assert app.repository.read(task.id).pause_reason == "执行时间已过去"


def test_clock_rollback_recomputes_future_times(timer, monkeypatch):
    import eidos_runtime.runtime.automation_scheduler as module
    app, scheduler, clock, _ = timer
    task = create(app)
    scheduler.tick(clock.now)
    original = module.future_times
    clocks = []

    def future(kind, schedule, now, count):
        clocks.append(now)
        return original(kind, schedule, now, count)

    monkeypatch.setattr(module, "future_times", future)
    clock.now -= timedelta(minutes=2)
    clock.steady += 1
    scheduler.tick(clock.now, expected_wait=180)
    assert clocks == [clock.now]
    assert scheduler._next[task.id][1] > clock.now


def test_plan_changes_do_not_reset_an_unrelated_due_task(timer):
    app, scheduler, clock, launched = timer
    due = create(app, kind="at", schedule={"runAt": (clock.now + timedelta(seconds=30)).isoformat()})
    scheduler.tick(clock.now)
    clock.advance(30)
    create(app)
    scheduler.tick(clock.now, expected_wait=30)
    assert app.repository.occurrence(launched[0]).automation_id == due.id


def test_early_wake_does_not_expire_a_one_time_task_still_preparing(timer):
    app, scheduler, clock, launched = timer
    task = create(app, kind="at", schedule={"runAt": (clock.now + timedelta(seconds=30)).isoformat()})
    scheduler.tick(clock.now)
    clock.advance(30)
    scheduler.tick(clock.now, expected_wait=30)
    create(app)  # A simultaneous edit wakes the timer before Run admission.
    scheduler.tick(clock.now)
    assert app.repository.read(task.id).status == "ACTIVE"
    assert app.repository.read(task.id).revision == task.revision
    assert app.repository.occurrence(launched[0]).status == "preparing"
    app.repository.finish(launched[0], "准备失败")
    scheduler.tick(clock.now)
    assert app.repository.read(task.id).status == "PAUSED"


def test_disabled_service_discards_overdue_cache_instead_of_spinning(timer):
    app, scheduler, clock, _ = timer
    create(app)
    scheduler.tick(clock.now)
    scheduler.enabled = lambda: False
    clock.advance(600)
    scheduler.tick(clock.now, expected_wait=180)
    assert scheduler.next_delay(clock.now) == 300


@pytest.mark.parametrize("from_worker", [False, True])
def test_plan_commit_interrupts_idle_wait_and_cancel_is_prompt(timer, from_worker):
    app, scheduler, clock, _ = timer

    async def check():
        with anyio.fail_after(3):
            async with anyio.create_task_group() as group:
                group.start_soon(scheduler.run)
                await eventually(lambda: scheduler._last is not None)
                assert scheduler.next_delay(clock.now) == 300
                task = await anyio.to_thread.run_sync(lambda: create(app)) if from_worker else create(app)
                await eventually(lambda: task.id in scheduler._next)
                task = app.execute(AutomationRequest.model_validate({"mode": "update", "id": task.id,
                    "expectedRevision": 1, "changes": {"status": "PAUSED"}}),
                    context_session_id=None, operation_id=str(uuid4())).automation
                await eventually(lambda: task.id not in scheduler._next)
                task = app.execute(AutomationRequest.model_validate({"mode": "update", "id": task.id,
                    "expectedRevision": 2, "changes": {"status": "ACTIVE"}}),
                    context_session_id=None, operation_id=str(uuid4())).automation
                await eventually(lambda: task.id in scheduler._next)
                task = app.execute(AutomationRequest.model_validate({"mode": "update", "id": task.id,
                    "expectedRevision": 3, "changes": {"kind": "at", "schedule": {
                        "runAt": (clock.now + timedelta(seconds=30)).isoformat()}}}),
                    context_session_id=None, operation_id=str(uuid4())).automation
                await eventually(lambda: scheduler._next.get(task.id, (0, None))[0] == 4)
                assert scheduler.next_delay(clock.now) == 30
                app.execute(AutomationRequest(mode="delete", id=task.id, expectedRevision=4),
                    context_session_id=None, operation_id=str(uuid4()))
                await eventually(lambda: task.id not in scheduler._next)
                group.cancel_scope.cancel()
        assert scheduler._wake is None

    anyio.run(check)
    scheduler.wake()  # Late committed events must be safe after shutdown.
    scheduler.refresh()


def test_timer_wakes_at_the_deadline_without_an_external_notification(timer):
    app, scheduler, _, launched = timer
    scheduler.clock = lambda: datetime.now(timezone.utc)
    scheduler.monotonic = time.monotonic
    app.clock = scheduler.clock
    due = app.clock() + timedelta(seconds=1)
    task = create(app, kind="at", schedule={"runAt": due.isoformat()})

    async def check():
        async with anyio.create_task_group() as group:
            group.start_soon(scheduler.run)
            await eventually(lambda: task.id in scheduler._next)
            assert 0 < scheduler.next_delay(app.clock()) <= 1
            await eventually(lambda: bool(launched))
            assert app.repository.occurrence(launched[0]).scheduled_for == due.isoformat()
            group.cancel_scope.cancel()

    anyio.run(check)


def test_change_during_database_scan_is_not_lost(timer, monkeypatch):
    app, scheduler, _, _ = timer
    original = app.repository.list
    inserted = []

    def read(after=0, limit=20):
        rows = original(after, limit)
        if not inserted:
            inserted.append(create(app))  # Committed after this scan's snapshot.
        return rows

    monkeypatch.setattr(app.repository, "list", read)

    async def check():
        async with anyio.create_task_group() as group:
            group.start_soon(scheduler.run)
            await eventually(lambda: inserted and inserted[0].id in scheduler._next)
            group.cancel_scope.cancel()

    anyio.run(check)


def test_activation_interrupts_a_disabled_startup_wait(timer):
    _, scheduler, _, _ = timer
    active = False
    scheduler.enabled = lambda: active

    async def check():
        nonlocal active
        async with anyio.create_task_group() as group:
            group.start_soon(scheduler.run)
            await eventually(lambda: scheduler._reset.is_set())
            active = True
            scheduler.wake()
            await eventually(lambda: scheduler._last is not None)
            group.cancel_scope.cancel()

    anyio.run(check)


def test_committed_runtime_changes_wake_once_even_if_output_fails(tmp_path, monkeypatch):
    server = RuntimeServer(io.StringIO(), tmp_path / "data", model=ScriptedModel([]))
    try:
        server.handle({"jsonrpc": "2.0", "id": "client-init", "method": "initialize", "params": {
            "protocolVersion": 1, "client": {"name": "test", "version": "1"}}})
        assert server.initialized, server.output.getvalue()
        scheduler = server.automation_scheduler
        app = server._automation_application()
        wakes = []
        original = scheduler.wake

        def wake():
            wakes.append(app.repository.list()[0][1].status)
            original()

        monkeypatch.setattr(scheduler, "wake", wake)
        task = create(app, model={"modelId": DEFAULT_MODEL_ID})
        assert wakes == ["ACTIVE"]
        server.output.close()
        app.execute(AutomationRequest.model_validate({"mode": "update", "id": task.id, "expectedRevision": 1,
            "changes": {"status": "PAUSED"}}), context_session_id=None, operation_id=str(uuid4()))
        assert wakes == ["ACTIVE", "PAUSED"]
        assert app.repository.read(task.id).status == "PAUSED"
        server.supervisor.events.deliver_pending()
        assert wakes == ["ACTIVE", "PAUSED"]
    finally:
        server.output = io.StringIO()
        server.close()

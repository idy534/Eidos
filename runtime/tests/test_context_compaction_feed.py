from __future__ import annotations

import json
from pathlib import Path
import threading

import pytest

from eidos_runtime.context.compactor import ContextCompactor
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.domain.context_management import ContextCompactionActivity, ContextCompactionRecord
from eidos_runtime.persistence.context_management import ContextManagementRepository
from eidos_runtime.model.client import ModelResponse, ModelToolCall
from eidos_runtime.protocol.methods import SessionReadResponseDto
from eidos_runtime.runtime.engine import RuntimeEngine
from eidos_runtime.runtime.event_projector import EventProjector


@pytest.fixture
def history(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    session = store.create_session(str(workspace))
    run, user = store.create_run(session["id"], "检查上下文。")
    try:
        yield store, session, run, user
    finally:
        store.close()


@pytest.mark.parametrize("status,reason", [("succeeded", None), ("failed", "no_compactable_history"), ("canceled", "canceled")])
def test_compaction_activity_uses_persisted_events_and_survives_reopen(history, status, reason):
    store, session, run, _ = history
    management = ContextManagementRepository(store.database)
    record = ContextCompactionRecord(request_id="request-one", stage="started", status="running", trigger="window_pressure")
    management.record(run["id"], record)
    active = store.read_session_snapshot(session["id"])["contextCompactions"]
    assert len(active) == 1
    assert active[0]["status"] == "running"
    management.record(run["id"], record.model_copy(update={"stage": "finished", "status": status, "reason": reason}))
    snapshot = store.read_session_snapshot(session["id"])
    parsed = SessionReadResponseDto.model_validate(snapshot)
    assert len(parsed.context_compactions) == 1
    activity = parsed.context_compactions[0]
    assert activity.status == status
    assert activity.started_at == active[0]["startedAt"]
    assert activity.reason == reason
    directory = store.database.data_directory
    store.close()
    reopened = SessionStore(directory)
    reopened.initialize()
    try:
        restored = reopened.read_session_snapshot(session["id"])["contextCompactions"]
        assert len(restored) == 1
        assert restored[0]["startedAt"] == activity.started_at
        assert restored[0]["status"] == status
    finally:
        reopened.close()


def test_compaction_projector_emits_lifecycle_without_losing_run_or_usage_notifications(history):
    store, session, run, _ = history
    management = ContextManagementRepository(store.database)
    record = ContextCompactionRecord(request_id="projection", stage="started", status="running", trigger="manual")
    management.record(run["id"], record)
    events = store.list_events(session["id"], after_event_id=0)["items"]
    event = next(event for event in events if event["payload"].get("reason") == "context_compaction")
    notifications = EventProjector().project(event, run=store.read_run(run["id"]))
    assert [item["method"] for item in notifications] == ["context/compactionUpdated", "run/updated"]
    activity = ContextCompactionActivity.model_validate(notifications[0]["params"]["activity"])
    assert activity.started_at == event["occurredAt"]
    committed = {**event, "eventType": "context.compacted", "payload": {
        "summaryId": "summary", "sourceItemCount": 2, "phase": "mid_turn",
        "compaction": record.model_copy(update={"stage": "finished", "status": "succeeded"}).to_wire_dict(),
    }}
    completed = EventProjector().project(committed)
    assert [item["method"] for item in completed] == ["context/compactionUpdated", "context/compacted"]


def test_snapshot_uses_atomic_commit_if_final_metrics_event_was_not_written(history, monkeypatch):
    store, session, run, _ = history
    store.fail_run(run["id"], "fixture")
    current, _ = store.create_run(session["id"], "继续任务。")
    original = ContextManagementRepository.record

    def interrupted(self, run_id, record):
        if record.stage == "finished":
            raise RuntimeError("metrics publication interrupted")
        return original(self, run_id, record)

    monkeypatch.setattr(ContextManagementRepository, "record", interrupted)
    with pytest.raises(RuntimeError, match="publication interrupted"):
        ContextCompactor(store).compact(current["id"], "pre_turn")
    activities = store.read_session_snapshot(session["id"])["contextCompactions"]
    assert len(activities) == 1
    assert activities[0]["status"] == "succeeded"
    assert store.compaction_count(current["id"]) == 1


def test_pagination_scopes_activity_by_start_time_and_ignores_other_sessions(history):
    store, session, run, user = history
    management = ContextManagementRepository(store.database)
    record = ContextCompactionRecord(request_id="paged", stage="started", status="running", trigger="manual")
    management.record(run["id"], record)
    started = store.read_session_snapshot(session["id"])["contextCompactions"][0]["startedAt"]
    later = store.create_tool_item(run["id"], 1, 0, "call", "read_file", "{}")
    store.complete_tool_item(later["id"], '{"outcome":"success"}')
    with store.database.transaction() as connection:
        connection.execute("UPDATE items SET created_at=? WHERE id=?", (started + 10, later["id"]))
    management.record(run["id"], record.model_copy(update={"stage": "finished", "status": "succeeded"}))
    recent = store.read_session_snapshot(session["id"], item_limit=1)
    assert not recent.get("contextCompactions")
    older = store.read_session_snapshot(session["id"], item_limit=1, before_item_id=later["id"])
    assert older["items"][0]["id"] == user["id"]
    assert older["contextCompactions"][0]["status"] == "succeeded"
    other = store.create_session(session["workspaceRoot"])
    store.create_run(other["id"], "其他会话")
    assert not store.read_session_snapshot(other["id"]).get("contextCompactions")
    assert management.activities(session["id"], (run["id"],), through_event_id=10**9, since=0, byte_budget=1) == ()


def test_compaction_activity_fixture_is_closed():
    fixture = json.loads((Path(__file__).resolve().parents[2] / "protocol/fixtures/context-management-v1.json").read_text())
    ContextCompactionActivity.model_validate(fixture["activity"])
    with pytest.raises(ValueError):
        ContextCompactionActivity.model_validate({**fixture["activity"], "rawHistory": "forbidden"})
    with pytest.raises(ValueError):
        ContextCompactionActivity.model_validate({**fixture["activity"], "eventId": -1})


def test_running_compaction_is_published_before_the_summary_model_is_called(history):
    store, session, run, _ = history
    store.fail_run(run["id"], "fixture")
    current, _ = store.create_run(session["id"], "继续")
    ContextManagementRepository(store.database).request(current["id"])
    notifications = []

    class Model:
        profile_snapshot = store.read_model_profile(current["id"])
        checked = False

        def complete(self, context, cancel, on_text_delta, **kwargs):
            if kwargs.get("required_tool") == "submit_context_summary":
                self.checked = True
                assert any(value["method"] == "context/compactionUpdated"
                           and value["params"]["activity"]["status"] == "running" for value in notifications)
                return ModelResponse(tool_calls=(ModelToolCall("summary", "submit_context_summary", {"notes": []}),))
            return ModelResponse(text="完成。")

    model = Model()
    RuntimeEngine(store, model, notifications.append).run(current["id"], threading.Event())
    assert model.checked
    assert any(value["method"] == "context/compactionUpdated"
               and value["params"]["activity"]["status"] == "succeeded" for value in notifications)

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

from eidos_runtime.db.database import now_ms
from eidos_runtime.db.recovery import _recover_completed_shell_cleanup
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.db.transitions import transition_run, transition_segments
from eidos_runtime.runtime.state_machine import RunStatus, SegmentStatus


@pytest.fixture
def legacy_completion(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data = tmp_path / "data"
    store = SessionStore(data)
    store.initialize()
    session = store.create_session(str(workspace))
    run, _ = store.create_run(session["id"], "create the requested document")
    step = store.increment_model_step(run["id"])
    tool = store.create_tool_item(run["id"], step, 0, "unknown", "apply_patch", "{}")
    store.begin_durable_intent(tool["id"], preconditions={}, approval_required=False)
    store.complete_tool_item(tool["id"], json.dumps({
        "outcome": "error", "code": "outcome_unknown", "data": {},
        "sideEffectsMayExist": True, "reconciliationRequired": True,
    }), item_status="failed", tool_status="failed")
    assistant = store.create_assistant_item(run["id"], step)
    store.append_item_content(assistant["id"], "The requested document is complete.")
    store.complete_current_step(run["id"], "completed")
    store.complete_assistant_item_committed(assistant["id"])
    with store.database.transaction() as connection:
        transition_segments(connection, run["id"], frozenset({SegmentStatus.RUNNING}),
                            SegmentStatus.FAILED, now_ms(), "active_shell_stopped")
        transition_run(connection, run["id"], frozenset({RunStatus.RUNNING}),
                       RunStatus.INTERRUPTED, "active_shell_stopped")
    try:
        yield SimpleNamespace(store=store, data=data, session=session, run=run, assistant=assistant)
    finally:
        store.close()


def test_restart_corrects_cleanup_only_completion_and_keeps_unknown_results(legacy_completion):
    e = legacy_completion
    before = e.store.read_run(e.run["id"])
    tool_before = e.store.connection.execute("SELECT result_json FROM tool_calls").fetchone()[0]
    e.store.close()
    restarted = SessionStore(e.data)
    restarted.initialize()
    try:
        assert restarted.health() == {"state": "ready"}
        completed = restarted.read_run(e.run["id"])
        assert completed["status"] == "succeeded"
        assert "errorCode" not in completed
        assert "stopReason" not in completed
        assert "termination" not in completed
        assert completed["completedAt"] == before["completedAt"]
        assert completed["updatedAt"] == before["updatedAt"]
        assert completed["reconciliationRequired"]
        assert completed["sideEffectsMayExist"]
        assert restarted.read_session(e.session["id"])["taskStatus"] == "completed"
        assert restarted.connection.execute("SELECT result_json FROM tool_calls").fetchone()[0] == tool_before
        assert restarted.connection.execute("SELECT status FROM execution_segments").fetchone()[0] == "completed"
        events = restarted.connection.execute("""
            SELECT id, event_type FROM events
            WHERE json_extract(payload_json, '$.reason') = 'shell_cleanup_completion_recovered'
        """).fetchall()
        assert [event["event_type"] for event in events] == ["segment.status_changed", "run.status_changed"]
        for event in events:
            assert restarted.connection.execute(
                "SELECT 1 FROM event_outbox WHERE event_id = ?", (event["id"],),
            ).fetchone() is not None
        count = restarted.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        with restarted.database.transaction() as connection:
            _recover_completed_shell_cleanup(connection, now_ms())
        assert restarted.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == count
    finally:
        restarted.close()


@pytest.mark.parametrize("change", [
    "UPDATE runs SET status = 'failed', error_code = 'MODEL_INVALID_REQUEST'",
    "UPDATE runs SET stop_reason = 'runtime_shutdown'",
    "UPDATE runs SET cancel_requested_at = completed_at",
    "UPDATE items SET incomplete = 1 WHERE kind = 'assistant_message'",
    "UPDATE items SET content = '' WHERE kind = 'assistant_message'",
    "UPDATE items SET status = 'in_progress' WHERE kind = 'assistant_message'",
    "DELETE FROM items WHERE kind = 'assistant_message'",
])
def test_completion_recovery_preserves_errors_cancellation_and_unfinished_work(legacy_completion, change):
    e = legacy_completion
    with e.store.database.transaction() as connection:
        connection.execute(change)
    before = e.store.read_run(e.run["id"])
    with e.store.database.transaction() as connection:
        _recover_completed_shell_cleanup(connection, now_ms())
    assert e.store.read_run(e.run["id"]) == before
    assert e.store.read_session(e.session["id"])["taskStatus"] == "failed"


def test_completion_recovery_rolls_back_status_and_events_together(legacy_completion, monkeypatch):
    e = legacy_completion
    before = e.store.read_run(e.run["id"])
    count = e.store.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    calls = 0

    def fail_event(_name):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise sqlite3.OperationalError("injected event failure")

    with monkeypatch.context() as patch:
        patch.setattr("eidos_runtime.db.events.hit_fault", fail_event)
        with pytest.raises(sqlite3.OperationalError):
            with e.store.database.transaction() as connection:
                _recover_completed_shell_cleanup(connection, now_ms())
    assert e.store.read_run(e.run["id"]) == before
    assert e.store.connection.execute("SELECT status FROM execution_segments").fetchone()[0] == "failed"
    assert e.store.connection.execute("SELECT COUNT(*) FROM events").fetchone()[0] == count

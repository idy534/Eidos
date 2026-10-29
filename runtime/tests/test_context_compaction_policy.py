from __future__ import annotations

from pathlib import Path
import threading

import pytest

from eidos_runtime.context.builder import ContextBuilder
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.model.client import ModelResponse, ScriptedModel
from eidos_runtime.model.config import default_profile_snapshot
from eidos_runtime.runtime.engine import RuntimeEngine


@pytest.fixture
def history(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    session = store.create_session(str(workspace))
    try:
        yield store, session
    finally:
        store.close()


def seed_history(store, session, count):
    previous, _ = store.create_run(session["id"], "Inspect the project")
    for index in range(count):
        item = store.create_assistant_item(previous["id"], index + 1)
        store.append_item_content(item["id"], f"Observed entry {index}\n" + "x" * (160_000 // count))
        store.complete_assistant_item_committed(item["id"])
    store.fail_run(previous["id"], "fixture")


def measured_build(monkeypatch, store, input_tokens):
    build = ContextBuilder.build

    def with_measurement(builder, run_id, **options):
        built = build(builder, run_id, **options)
        # Model-provider measurements are controlled independently of the
        # amount of local evidence. A committed summary starts a smaller window.
        tokens = 20_000 if store.compaction_count(run_id) else input_tokens
        usage = built.budget.context_usage.model_copy(update={
            "active_tokens": tokens,
            "percent_used": round(tokens / built.budget.context_usage.context_window_tokens * 100, 1),
            "source": "provider",
        })
        budget = built.budget.model_copy(update={
            "projected_input_tokens": tokens,
            "context_usage": usage,
            "measurement": "provider",
            "fits": tokens <= built.budget.usable_input_budget,
        })
        return built.model_copy(update={"budget": budget})

    monkeypatch.setattr(ContextBuilder, "build", with_measurement)


@pytest.mark.parametrize(("window", "input_tokens", "history_count", "expected"), [
    (1_048_576, 151_036, 40, 0),  # Regression: 14.4% and more than 32 old Items.
    (1_048_576, 891_289, 8, 0),  # One token below the unrounded 85% boundary.
    (1_048_576, 891_290, 8, 1),
    (100_000, 84_999, 40, 0),
    (100_000, 85_000, 8, 1),  # Exact boundary; fewer than 32 Items must suffice.
    (100_000, 85_001, 8, 1),
])
def test_automatic_compaction_uses_frozen_window_percentage(
    history, monkeypatch, window, input_tokens, history_count, expected,
):
    store, session = history
    seed_history(store, session, history_count)
    profile = default_profile_snapshot("deepseek-flash").model_copy(update={
        "context_window_tokens": window, "max_output_tokens": 8_192,
    })
    run, _ = store.create_run(session["id"], "Continue the task", model_profile=profile)
    measured_build(monkeypatch, store, input_tokens)
    model = ScriptedModel([ModelResponse(text="Done")])

    RuntimeEngine(store, model, lambda _: None).run(run["id"], threading.Event())

    assert store.read_run(run["id"])["status"] == "succeeded"
    assert store.compaction_count(run["id"]) == expected
    assert len(model.contexts) == 1
    events = store.list_events(session["id"], after_event_id=0, limit=500)["items"]
    records = [event["payload"]["compaction"] for event in events
               if event.get("runId") == run["id"] and event["eventType"] == "context.compacted"]
    assert len(records) == expected
    if records:
        assert records[0]["trigger"] == "window_pressure"
        assert records[0]["beforeTokens"] == input_tokens


def test_output_reserve_still_protects_a_smaller_input_budget(history, monkeypatch):
    store, session = history
    seed_history(store, session, 8)
    profile = default_profile_snapshot("deepseek-flash").model_copy(update={
        "context_window_tokens": 100_000, "max_output_tokens": 30_000,
    })
    run, _ = store.create_run(session["id"], "Continue the task", model_profile=profile)
    measured_build(monkeypatch, store, 70_000)

    RuntimeEngine(store, ScriptedModel([ModelResponse(text="Done")]), lambda _: None).run(
        run["id"], threading.Event(),
    )

    assert store.read_run(run["id"])["status"] == "succeeded"
    assert store.compaction_count(run["id"]) == 1


def test_threshold_without_compactable_history_keeps_the_fitting_request(history, monkeypatch):
    store, session = history
    profile = default_profile_snapshot("deepseek-flash").model_copy(update={
        "context_window_tokens": 100_000, "max_output_tokens": 8_192,
    })
    run, _ = store.create_run(session["id"], "Continue the task", model_profile=profile)
    measured_build(monkeypatch, store, 85_000)
    model = ScriptedModel([ModelResponse(text="Done")])

    RuntimeEngine(store, model, lambda _: None).run(run["id"], threading.Event())

    assert store.read_run(run["id"])["status"] == "succeeded"
    assert store.compaction_count(run["id"]) == 0
    assert len(model.contexts) == 1
    activity, = store.read_session_snapshot(session["id"])["contextCompactions"]
    assert activity["status"] == "failed"
    assert activity["reason"] == "no_compactable_history"


def test_cancel_at_automatic_compaction_start_preserves_original_history(history, monkeypatch):
    store, session = history
    seed_history(store, session, 8)
    profile = default_profile_snapshot("deepseek-flash").model_copy(update={
        "context_window_tokens": 100_000, "max_output_tokens": 8_192,
    })
    run, _ = store.create_run(session["id"], "Continue the task", model_profile=profile)
    measured_build(monkeypatch, store, 85_000)
    cancel = threading.Event()
    model = ScriptedModel([ModelResponse(text="Unreachable")])
    before = store.context_projection_facts(run["id"])

    def cancel_when_started(message):
        if message.get("method") == "context/compactionUpdated":
            if message["params"]["activity"]["status"] == "running":
                cancel.set()

    RuntimeEngine(store, model, cancel_when_started).run(run["id"], cancel)

    assert store.read_run(run["id"])["status"] == "canceled"
    assert store.compaction_count(run["id"]) == 0
    assert not model.contexts
    assert {item.item_id for item in store.context_projection_facts(run["id"]).items} == {
        item.item_id for item in before.items
    }
    activity, = store.read_session_snapshot(session["id"])["contextCompactions"]
    assert activity["status"] == "canceled"

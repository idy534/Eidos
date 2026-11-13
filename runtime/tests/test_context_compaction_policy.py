from __future__ import annotations

from pathlib import Path
import threading

import pytest

from eidos_runtime.context.builder import ContextBuilder
from eidos_runtime.context.policy import context_thresholds
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
        # About 60,000 reference tokens in total: more than the largest working
        # set (32,768), so older entries remain eligible for any window.
        store.append_item_content(item["id"], f"Observed entry {index}\n" + "x" * (480_000 // count))
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


@pytest.mark.parametrize(("usable", "working_set", "clearing", "target", "compaction"), [
    (27_648, 3_456, 20_736, 15_552, 24_192),  # 32K window: three quarters before clearing.
    (89_808, 11_226, 65_536, 49_152, 78_582),  # 100K window: the clearing floor applies.
    (120_258, 15_032, 65_536, 49_152, 105_226),  # 128K window keeps the former clearing point.
    (224_133, 28_016, 112_066, 84_049, 196_117),  # 256K window: half of the budget.
    (1_032_192, 32_768, 262_144, 196_608, 999_424),  # 1M window: ceilings apply.
])
def test_thresholds_follow_the_usable_input_budget(usable, working_set, clearing, target, compaction):
    thresholds = context_thresholds(usable)

    assert thresholds.working_set_tokens == working_set
    assert thresholds.clearing_trigger_tokens == clearing
    assert thresholds.clearing_target_tokens == target
    assert thresholds.compaction_trigger_tokens == compaction


def test_thresholds_grow_with_the_budget_and_keep_their_order():
    previous = context_thresholds(1)
    for usable in (*range(1, 4_096, 97), *range(4_096, 2_000_000, 7_919)):
        current = context_thresholds(usable)
        assert current.clearing_target_tokens <= current.clearing_trigger_tokens
        assert current.clearing_trigger_tokens <= current.compaction_trigger_tokens <= usable
        # Reversible clearing always starts before a summary is requested.
        if usable > 4_096:
            assert current.clearing_trigger_tokens < current.compaction_trigger_tokens
        for name in ("working_set_tokens", "clearing_trigger_tokens",
                     "clearing_target_tokens", "compaction_trigger_tokens"):
            assert getattr(current, name) >= getattr(previous, name)
        previous = current


@pytest.mark.parametrize(("window", "output", "input_tokens", "history_count", "expected"), [
    (1_048_576, 8_192, 151_036, 40, 0),  # Regression: 14.4% and more than 32 old Items.
    (1_048_576, 8_192, 999_423, 8, 0),  # One token below one working set of free budget.
    (1_048_576, 8_192, 999_424, 8, 1),
    (100_000, 8_192, 78_581, 40, 0),
    (100_000, 8_192, 78_582, 8, 1),  # Exact boundary; fewer than 32 Items must suffice.
    (100_000, 8_192, 78_583, 8, 1),
    # A large output reserve leaves 191,365 usable tokens. The boundary follows
    # that budget; 85% of the window (222,822) could never be reached while fitting.
    (262_144, 65_536, 167_444, 8, 0),
    (262_144, 65_536, 167_445, 8, 1),
])
def test_automatic_compaction_leaves_one_working_set_of_input_budget(
    history, monkeypatch, window, output, input_tokens, history_count, expected,
):
    store, session = history
    seed_history(store, session, history_count)
    profile = default_profile_snapshot("deepseek-flash").model_copy(update={
        "context_window_tokens": window, "max_output_tokens": output,
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


def test_summary_that_stays_above_the_trigger_is_not_repeated_at_once(history, monkeypatch):
    store, session = history
    seed_history(store, session, 8)
    profile = default_profile_snapshot("deepseek-flash").model_copy(update={
        "context_window_tokens": 100_000, "max_output_tokens": 8_192,
    })
    run, _ = store.create_run(session["id"], "Continue the task", model_profile=profile)
    build = ContextBuilder.build

    def with_measurement(builder, run_id, **options):
        built = build(builder, run_id, **options)
        # The committed summary only brings the request to 79,000 tokens, still
        # above the 78,582 trigger. Another attempt needs 11,226 more tokens.
        tokens = 79_000 if store.compaction_count(run_id) else 80_000
        budget = built.budget.model_copy(update={
            "projected_input_tokens": tokens, "measurement": "provider", "fits": True,
        })
        return built.model_copy(update={"budget": budget})

    monkeypatch.setattr(ContextBuilder, "build", with_measurement)
    model = ScriptedModel([ModelResponse(text="Done")])

    RuntimeEngine(store, model, lambda _: None).run(run["id"], threading.Event())

    assert store.read_run(run["id"])["status"] == "succeeded"
    assert store.compaction_count(run["id"]) == 1
    assert len(model.contexts) == 1
    activity, = store.read_session_snapshot(session["id"])["contextCompactions"]
    assert activity["status"] == "succeeded"


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


def _spy_compaction(monkeypatch):
    from eidos_runtime.context.compactor import ContextCompactor
    calls = []
    original = ContextCompactor.compact

    def recording(compactor, run_id, phase, **options):
        calls.append(options)
        return original(compactor, run_id, phase, **options)

    monkeypatch.setattr(ContextCompactor, "compact", recording)
    return calls


def _previous_items(store, session):
    rows = store.connection.execute(
        "SELECT id FROM items WHERE session_id=? AND kind='assistant_message' ORDER BY creation_seq",
        (session["id"],),
    ).fetchall()
    return [row[0] for row in rows]


def test_over_budget_compaction_keeps_the_newest_responses_verbatim(history, monkeypatch):
    store, session = history
    seed_history(store, session, 8)
    profile = default_profile_snapshot("deepseek-flash").model_copy(update={
        "context_window_tokens": 100_000, "max_output_tokens": 8_192,
    })
    run, _ = store.create_run(session["id"], "Continue the task", model_profile=profile)
    measured_build(monkeypatch, store, 95_000)
    calls = _spy_compaction(monkeypatch)

    RuntimeEngine(store, ScriptedModel([ModelResponse(text="Done")]), lambda _: None).run(
        run["id"], threading.Event(),
    )

    assert store.read_run(run["id"])["status"] == "succeeded"
    working_set = context_thresholds(89_808).working_set_tokens
    assert [call.get("protect_recent_tokens") for call in calls] == [working_set // 2]
    summary = store.latest_compact_summary(run["id"])
    entries = _previous_items(store, session)
    assert entries[-1] not in summary.source_item_ids
    assert entries[0] in summary.source_item_ids


def test_requested_compaction_keeps_one_working_set(history, monkeypatch):
    from eidos_runtime.context.compactor import ContextCompactor
    store, session = history
    seed_history(store, session, 8)
    run, _ = store.create_run(session["id"], "Continue the task")
    entries = _previous_items(store, session)

    summary = ContextCompactor(store).compact(run["id"], "pre_turn", trigger="manual", protect_recent_tokens=1)
    assert entries[-1] not in summary.source_item_ids
    assert set(entries[:-1]) <= set(summary.source_item_ids)
    # With only the protected response left, the next round summarizes it.
    summary = ContextCompactor(store).compact(run["id"], "pre_turn", trigger="manual", protect_recent_tokens=1)
    assert entries[-1] in summary.source_item_ids

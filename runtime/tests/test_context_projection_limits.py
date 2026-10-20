from __future__ import annotations

from pathlib import Path
import threading

import pytest

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


def assistant(store, run, step, content):
    item = store.create_assistant_item(run["id"], step)
    store.append_item_content(item["id"], content)
    store.complete_assistant_item(item["id"])
    return item["id"]


def test_online_history_larger_than_a_summary_batch_does_not_force_compaction(history):
    store, session = history
    previous, _ = store.create_run(session["id"], "Inspect the project")
    ids = {assistant(store, previous, index + 1, f"Finding {index}: " + "x" * 4_000)
           for index in range(240)}
    store.fail_run(previous["id"], "fixture")
    profile = default_profile_snapshot("deepseek-flash").model_copy(update={
        "context_window_tokens": 1_000_000, "max_output_tokens": 128_000,
    })
    run, _ = store.create_run(session["id"], "Continue", model_profile=profile)

    facts = store.context_projection_facts(run["id"])

    assert ids <= {item.item_id for item in facts.items}
    assert not facts.candidate_overflow
    assert facts.projection_omitted_count == 0
    model = ScriptedModel([ModelResponse(text="Done")])
    RuntimeEngine(store, model, lambda _: None).run(run["id"], threading.Event())
    assert store.read_run(run["id"])["status"] == "succeeded"
    assert store.compaction_count(run["id"]) == 0
    assert len(model.contexts) == 1
    candidates = store.compaction_candidate_facts(run["id"])
    assert candidates.candidate_overflow
    # Reading either projection never deletes durable history.
    assert store.connection.execute(
        "SELECT count(*) FROM items WHERE run_id=? AND kind='assistant_message'", (previous["id"],)
    ).fetchone()[0] == 240
    before_restart = store.context_projection_facts(run["id"])
    directory = store.database.data_directory
    store.close()
    reopened = SessionStore(directory)
    reopened.initialize()
    try:
        assert reopened.context_projection_facts(run["id"]) == before_restart
        assert reopened.compaction_count(run["id"]) == 0
    finally:
        reopened.close()


def test_json_expansion_cannot_crowd_out_protected_recent_facts(history, monkeypatch):
    monkeypatch.setattr("eidos_runtime.db.repositories.context.CONTEXT_PROJECTION_MAX_BYTES", 60_000)
    monkeypatch.setattr("eidos_runtime.db.repositories.context.CONTEXT_PROJECTION_HARD_MAX_BYTES", 60_000)
    store, session = history
    previous, _ = store.create_run(session["id"], "Inspect")
    oversized = assistant(store, previous, 1, "\\" * 25_000)
    store.fail_run(previous["id"], "fixture")
    run, user = store.create_run(session["id"], "Continue")
    recent = assistant(store, run, 1, "Recent evidence " + "x" * 16_000)

    facts = store.context_projection_facts(run["id"])

    selected = {item.item_id for item in facts.items}
    assert {user["id"], recent} <= selected
    assert oversized in selected
    assert "Content truncated" in next(item.content for item in facts.items if item.item_id == oversized)
    assert facts.projection_omitted_count >= 0
    assert 2 + sum(len(item.model_dump_json(exclude_none=True).encode()) + 1 for item in facts.items) <= 60_000
    assert [item.item_id for item in facts.items][-2:] == [user["id"], recent]
    assert len(store.read_item(oversized)["content"]) == 25_000


def test_protected_facts_still_obey_the_actual_hard_serialization_limit(history, monkeypatch):
    monkeypatch.setattr("eidos_runtime.db.repositories.context.CONTEXT_PROJECTION_MAX_BYTES", 60_000)
    monkeypatch.setattr("eidos_runtime.db.repositories.context.CONTEXT_PROJECTION_HARD_MAX_BYTES", 60_000)
    store, session = history
    run, _ = store.create_run(session["id"], "Continue")
    assistant(store, run, 1, "\\" * 31_000)

    facts = store.context_projection_facts(run["id"])
    assert 2 + sum(len(item.model_dump_json(exclude_none=True).encode()) + 1 for item in facts.items) <= 60_000
    assert any("Content truncated" in (item.content or "") for item in facts.items)


def test_online_projection_still_limits_history_item_count(history, monkeypatch):
    monkeypatch.setattr("eidos_runtime.db.repositories.context.CONTEXT_PROJECTION_MAX_ITEMS", 4)
    monkeypatch.setattr("eidos_runtime.db.repositories.context.CONTEXT_PROJECTION_HARD_MAX_ITEMS", 8)
    store, session = history
    previous, _ = store.create_run(session["id"], "Inspect")
    for index in range(12):
        assistant(store, previous, index + 1, f"Finding {index}")
    store.fail_run(previous["id"], "fixture")
    run, user = store.create_run(session["id"], "Continue")

    facts = store.context_projection_facts(run["id"])

    assert len(facts.items) == 8
    assert user["id"] in {item.item_id for item in facts.items}
    assert facts.candidate_overflow
    assert facts.projection_omitted_count == 6

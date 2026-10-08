from __future__ import annotations

import json
from pathlib import Path
import threading

import pytest

from eidos_runtime.db.database import now_ms
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.memory.contracts import (
    MemoryBackfillRequest,
    MemorySettings,
    MemorySettingsRequest,
    MemoryReadRequest,
)
from eidos_runtime.memory.jobs import MemoryJobs
from eidos_runtime.model.client import ModelResponse, ScriptedModel
from eidos_runtime.model.config import ModelConfigStore
from eidos_runtime.model.pydantic_ai_client import ModelClientLease


pytestmark = pytest.mark.integration


class FakeGateway:
    def __init__(self, responses):
        normalized = []
        for response in responses:
            try:
                payload = json.loads(response.text)
            except ValueError:
                normalized.append(response)
                continue
            if "changes" in payload:
                for proposal in payload["changes"]:
                    proposal.setdefault("grounded", True)
                    proposal.setdefault("atomic", True)
                response = response.model_copy(update={"text": json.dumps(payload)})
            normalized.append(response)
        self.model = ScriptedModel(normalized)
        self.leases = []

    def acquire_lease(self, config, **kwargs):
        assert kwargs["retry_policy"].max_attempts == 1
        assert kwargs["max_output_tokens"] == 4096
        lease = ModelClientLease(self.model, lambda: None)
        self.leases.append(lease)
        return lease


@pytest.fixture
def setup(tmp_path: Path):
    (tmp_path / "workspace").mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    session = store.create_session(str(tmp_path / "workspace"))
    configs = ModelConfigStore(store.data_directory)
    configs.initialize()
    configs.create(
        provider_id="deepseek", model_id="deepseek-v4-flash", api_key="fixture"
    )
    store.database.memory.settings(
        MemorySettingsRequest(
            session_id=session["id"],
            scope="current",
            settings=MemorySettings(generate_enabled=True, debounce_seconds=0),
        )
    )
    yield store, session, configs
    store.close()


def extraction(item_id, quote="默认中文回答", **changes):
    candidate = {
        "content": quote,
        "title": "回答语言",
        "kind": "preference",
        "source_item_ids": [item_id],
        "source_quotes": {item_id: quote},
        "evidence_class": "explicit_user",
        "requires_confirmation": False,
    }
    candidate.update(changes)
    return ModelResponse(
        text=json.dumps({"candidates": [candidate]}, ensure_ascii=False)
    )


def final_run(store, session, text="默认中文回答", **kwargs):
    run, item = store.create_run(session["id"], text, **kwargs)
    store.fail_run(run["id"], "fixture_finished")
    return run, item


def jobs(setup, responses):
    store, _session, configs = setup
    gateway = FakeGateway(responses)
    value = MemoryJobs(store.database.memory, configs, gateway, lambda: False)
    return value, gateway


def test_two_stage_learning_publishes_once_and_closes_leases(setup):
    store, session, _ = setup
    _run, item = final_run(store, session)
    worker, gateway = jobs(
        setup,
        [
            extraction(item["id"]),
            ModelResponse(text='{"changes":[{"action":"create","candidate_index":0}]}'),
        ],
    )
    worker.backfill(MemoryBackfillRequest(session_id=session["id"], operation_id="selected-history"))
    task = worker.claim()
    assert task
    worker.process(task, threading.Event())
    state = store.database.memory.read(MemoryReadRequest(session_id=session["id"]))
    assert state.entries[0].content == "默认中文回答"
    assert state.entries[0].status == "active"
    assert state.jobs[0].state == "succeeded"
    consolidated_input = json.loads(gateway.model.contexts[1][0]["content"])
    assert consolidated_input["original_sources"][0]["content"] == "默认中文回答"
    assert len(gateway.model.contexts) == 2 and all(
        lease.closed for lease in gateway.leases
    )
    worker.enqueue_ready()
    assert worker.claim() is None
    assert len(gateway.model.contexts) == 2
    assert (
        store.connection.execute("SELECT count(*) FROM memory_generations").fetchone()[
            0
        ]
        == 1
    )


@pytest.mark.parametrize(("grounded", "atomic", "code"), [(False, True, "memory_claim_unsupported"), (True, False, "memory_claim_not_atomic")])
def test_historical_consolidation_rejects_unsupported_or_compound_final_facts(setup, grounded, atomic, code):
    store, session, _ = setup
    _, item = final_run(store, session)
    worker, _ = jobs(setup, [extraction(item["id"]), ModelResponse(text=json.dumps({"changes": [{"action": "create", "candidate_index": 0, "content": "默认中文回答，用户名是旧资料里的名字", "grounded": grounded, "atomic": atomic}]}))])
    worker.backfill(MemoryBackfillRequest(session_id=session["id"], operation_id="review-history"))
    worker.process(worker.claim(), threading.Event())
    state = store.database.memory.read(MemoryReadRequest(session_id=session["id"]))
    assert state.entries == []
    assert state.jobs[0].error_code == code


@pytest.mark.parametrize(("requires_confirmation", "status"), [(False, "active"), (True, "candidate")])
def test_plan_memory_uses_claim_confirmation_instead_of_run_mode(setup, requires_confirmation, status):
    store, session, _ = setup
    _run, item = final_run(store, session, work_mode="plan")
    worker, _ = jobs(
        setup,
        [
            extraction(item["id"], requires_confirmation=requires_confirmation),
            ModelResponse(text='{"changes":[{"action":"create","candidate_index":0}]}'),
        ],
    )
    worker.backfill(MemoryBackfillRequest(session_id=session["id"], operation_id="selected-history"))
    worker.process(worker.claim(), threading.Event())
    state = store.database.memory.read(MemoryReadRequest(session_id=session["id"]))
    assert state.entries[0].status == status
    assert bool(store.database.memory.read(
        MemoryReadRequest(session_id=session["id"]), for_use=True
    ).entries) == (status == "active")


def test_legacy_extraction_without_confirmation_stays_pending(setup):
    store, session, _ = setup
    _, item = final_run(store, session)
    response = extraction(item["id"])
    payload = json.loads(response.text)
    del payload["candidates"][0]["requires_confirmation"]
    worker, _ = jobs(setup, [ModelResponse(text=json.dumps(payload)),
        ModelResponse(text='{"changes":[{"action":"create","candidate_index":0}]}')])
    worker.backfill(MemoryBackfillRequest(session_id=session["id"], operation_id="legacy-history"))
    worker.process(worker.claim(), threading.Event())
    assert store.database.memory.read(MemoryReadRequest(session_id=session["id"])).entries[0].status == "candidate"


def test_unconfirmed_history_cannot_replace_an_existing_fact(setup):
    from eidos_runtime.memory.contracts import MemoryGetRequest, MemoryWriteRequest

    store, session, _ = setup
    _, original = final_run(store, session, "我习惯先写文档，再实现代码")
    entry = store.database.memory.record(MemoryWriteRequest(session_id=session["id"], operation_id="known-workflow",
        mode="automatic", content=original["content"], source_item_ids=[original["id"]]))
    _, proposal = final_run(store, session, "以后也许改为先写代码，还没有决定")
    worker, _ = jobs(setup, [extraction(proposal["id"], quote=proposal["content"], requires_confirmation=True),
        ModelResponse(text=json.dumps({"changes":[{"action":"revise", "candidate_index":0,
            "target_entry_id":entry.entry_id, "expected_revision":entry.revision}]}))])
    worker.backfill(MemoryBackfillRequest(session_id=session["id"], operation_id="pending-change"))
    worker.process(worker.claim(), threading.Event())
    state = store.database.memory.read(MemoryReadRequest(session_id=session["id"]))
    assert state.jobs[0].error_code == "memory_candidate_target_invalid"
    current = store.database.memory.get(MemoryGetRequest(session_id=session["id"], entry_id=entry.entry_id)).entry
    assert current.status == "active" and current.revision == entry.revision
    assert current.content == original["content"]


@pytest.mark.parametrize(
    "candidate_changes",
    [
        {
            "source_item_ids": ["invented"],
            "source_quotes": {"invented": "默认中文回答"},
        },
        {"source_quotes": {"unused": "x"}},
        {"source_quotes": {}},
        {"source_quotes": {"placeholder": "invented quote"}},
    ],
)
def test_invalid_evidence_never_publishes(setup, candidate_changes):
    store, session, _ = setup
    _run, item = final_run(store, session)
    worker, _ = jobs(setup, [extraction(item["id"], **candidate_changes)])
    worker.backfill(MemoryBackfillRequest(session_id=session["id"], operation_id="selected-history"))
    worker.process(worker.claim(), threading.Event())
    state = store.database.memory.read(MemoryReadRequest(session_id=session["id"]))
    assert state.entries == [] and state.jobs[0].state == "failed"


def test_long_source_windows_cover_middle_without_silent_truncation(setup):
    store, session, _ = setup
    final_run(store, session, "a" * 6000 + "MIDDLE" + "b" * 6000)
    worker, gateway = jobs(setup, [ModelResponse(text='{"candidates":[]}')] * 3)
    worker.backfill(MemoryBackfillRequest(session_id=session["id"], operation_id="long-history"))
    offsets = []
    for _ in range(3):
        worker.enqueue_ready()
        task = worker.claim()
        assert task is not None
        offsets.append(task["start_offset"])
        worker.process(task, threading.Event())
    assert offsets == [0, 6000, 12000]
    assert "MIDDLE" in gateway.model.contexts[1][0]["content"]
    worker.enqueue_ready()
    assert worker.claim() is None


def test_expired_worker_cannot_publish_after_reclaim(setup):
    store, session, _ = setup
    final_run(store, session)
    worker, gateway = jobs(setup, [ModelResponse(text='{"candidates":[]}')])
    worker.backfill(MemoryBackfillRequest(session_id=session["id"], operation_id="selected-history"))
    old = worker.claim()
    with store.database.transaction() as connection:
        connection.execute("UPDATE memory_jobs SET lease_until=?", (now_ms() - 1,))
    current = worker.claim()
    assert current["lease_token"] != old["lease_token"]
    worker.process(old, threading.Event())
    assert not gateway.model.contexts
    assert (
        store.connection.execute("SELECT state FROM memory_jobs").fetchone()[0]
        == "running"
    )


def test_missing_model_and_daily_budget_have_real_states(setup):
    store, session, _ = setup
    final_run(store, session)
    store.database.memory.settings(
        MemorySettingsRequest(
            session_id=session["id"],
            scope="current",
            settings=MemorySettings(
                generate_enabled=True, debounce_seconds=0, daily_call_limit=0
            ),
        )
    )
    worker, gateway = jobs(setup, [])
    worker.backfill(MemoryBackfillRequest(session_id=session["id"], operation_id="selected-history"))
    worker.process(worker.claim(), threading.Event())
    assert not gateway.model.contexts
    assert (
        store.connection.execute("SELECT state FROM memory_jobs").fetchone()[0]
        == "paused_budget"
    )


def test_bad_json_gets_only_one_format_repair(setup):
    store, session, _ = setup
    final_run(store, session)
    worker, gateway = jobs(setup, [ModelResponse(text="invalid")] * 3)
    worker.backfill(MemoryBackfillRequest(session_id=session["id"], operation_id="selected-history"))
    worker.process(worker.claim(), threading.Event())
    assert len(gateway.model.contexts) == 2
    assert (
        store.connection.execute("SELECT state FROM memory_jobs").fetchone()[0]
        == "failed"
    )
    assert all(lease.closed for lease in gateway.leases)


def test_forget_during_learning_blocks_late_result(setup):
    store, session, _ = setup
    _run, item = final_run(store, session)
    worker, gateway = jobs(
        setup,
        [
            extraction(item["id"]),
            ModelResponse(text='{"changes":[{"action":"create","candidate_index":0}]}'),
        ],
    )
    worker.backfill(MemoryBackfillRequest(session_id=session["id"], operation_id="selected-history"))
    task = worker.claim()
    with store.database.transaction() as connection:
        connection.execute(
            "UPDATE memory_scopes SET privacy_epoch=privacy_epoch+1 WHERE id=?",
            (task["scope_id"],),
        )
    worker.process(task, threading.Event())
    assert not gateway.model.contexts
    assert not store.database.memory.read(
        MemoryReadRequest(session_id=session["id"])
    ).entries


def test_enabling_learning_does_not_backfill_without_explicit_control(setup):
    store, session, _ = setup
    final_run(store, session)
    store.database.memory.settings(
        MemorySettingsRequest(
            session_id=session["id"],
            scope="current",
            settings=MemorySettings(generate_enabled=False),
        )
    )
    worker, _ = jobs(setup, [ModelResponse(text='{"candidates":[]}')])
    worker.enqueue_ready()
    store.database.memory.settings(
        MemorySettingsRequest(
            session_id=session["id"],
            scope="current",
            settings=MemorySettings(generate_enabled=True, debounce_seconds=0),
        )
    )
    worker.enqueue_ready()
    assert worker.claim() is None
    worker.backfill(
        MemoryBackfillRequest(session_id=session["id"], operation_id="selected-history")
    )
    assert worker.claim() is not None


def test_backfill_operation_replay_does_not_relearn_completed_source(setup):
    store, session, _ = setup
    final_run(store, session)
    worker, gateway = jobs(setup, [ModelResponse(text='{"candidates":[]}')])
    request = MemoryBackfillRequest(
        session_id=session["id"], operation_id="backfill-once"
    )
    worker.backfill(request)
    worker.process(worker.claim(), threading.Event())
    worker.backfill(request)
    worker.enqueue_ready()
    assert worker.claim() is None
    assert len(gateway.model.contexts) == 1


def test_disabling_learning_revokes_selected_history_consent(setup):
    store, session, _ = setup
    final_run(store, session)
    worker, _ = jobs(setup, [])
    worker.backfill(MemoryBackfillRequest(session_id=session["id"], operation_id="old-consent"))
    service = store.database.memory
    service.settings(MemorySettingsRequest(session_id=session["id"], scope="current", settings=MemorySettings(generate_enabled=False)))
    assert store.database.connection().execute("SELECT backfill_enabled FROM memory_sources WHERE session_id=?", (session["id"],)).fetchone()[0] == 0
    final_run(store, session, text="Never learn this disabled-gap preference")
    service.settings(MemorySettingsRequest(session_id=session["id"], scope="current", settings=MemorySettings(generate_enabled=True, debounce_seconds=0)))
    worker.enqueue_ready()
    assert worker.claim() is None


def test_ordinary_runs_never_enqueue_background_learning(setup):
    store, session, _ = setup
    worker, gateway = jobs(setup, [])
    final_run(store, session, "我的名字是 Eddy")
    worker.enqueue_ready()
    worker.recover()
    worker.enqueue_ready()
    assert worker.claim() is None
    assert not gateway.model.contexts
    assert store.connection.execute("SELECT count(*) FROM memory_jobs").fetchone()[0] == 0


def test_selected_history_is_bounded_and_works_with_automatic_memory_off(setup):
    store, session, _ = setup
    service = store.database.memory
    service.settings(MemorySettingsRequest(session_id=session["id"], scope="current", settings=MemorySettings()))
    final_run(store, session, "a" * 6000 + "MIDDLE" + "b" * 6000)
    worker, gateway = jobs(setup, [ModelResponse(text='{"candidates":[]}')] * 3)
    request = MemoryBackfillRequest(session_id=session["id"], operation_id="bounded-history")
    worker.backfill(request)
    worker.process(worker.claim(), threading.Event())
    upper = store.connection.execute("SELECT backfill_until FROM memory_sources").fetchone()[0]
    final_run(store, session, "FUTURE_SECRET_MUST_NOT_BE_SENT")
    for _ in range(2):
        worker.enqueue_ready()
        task = worker.claim()
        assert task["target_frontier"] <= upper
        worker.process(task, threading.Event())
    assert len(gateway.model.contexts) == 3
    assert all("FUTURE_SECRET_MUST_NOT_BE_SENT" not in str(context) for context in gateway.model.contexts)
    assert store.connection.execute("SELECT backfill_enabled FROM memory_sources").fetchone()[0] == 0
    worker.backfill(request)
    worker.enqueue_ready()
    assert worker.claim() is None

from __future__ import annotations

import json
from pathlib import Path
import threading

import pytest

from eidos_runtime.application.context import ContextApplication
from eidos_runtime.context.budget import estimate_context_budget
from eidos_runtime.context.builder import ContextBuilder
from eidos_runtime.context.compactor import ContextCompactor, ContextCompactionError
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.domain.context_management import ContextCompactionRecord, ContextDetails
from eidos_runtime.model.client import ModelResponse, ModelToolCall, ModelUsage, ScriptedModel
from eidos_runtime.model.config import ModelConfig, MODEL_CATALOG
from eidos_runtime.persistence.context_management import ContextManagementRepository
from eidos_runtime.persistence.context_snapshots import ContextSnapshotRepository
from eidos_runtime.protocol.methods import ContextCompactRequestDto, ContextDetailsRequestDto
from eidos_runtime.runtime.engine import RuntimeEngine
from eidos_runtime.runtime.resolution import canonical_sha256


@pytest.fixture
def history(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    session = store.create_session(str(workspace))
    previous, user = store.create_run(session["id"], "必须保留 notes.py。方案还未采纳。不要提交。")
    tool = store.create_tool_item(previous["id"], 1, 0, "read-notes", "read_file", "{}")
    raw = json.dumps({"outcome": "success", "code": "ok", "summary": "Read notes.py",
                      "data": {"path": "notes.py", "content": "def inspect_context(): pass"}}, ensure_ascii=False)
    store.complete_tool_item(tool["id"], raw, model_result_json=raw)
    store.fail_run(previous["id"], "fixture")
    run, _ = store.create_run(session["id"], "继续检查，测试还没有运行。")
    try:
        yield store, session, run, user, tool
    finally:
        store.close()


class SummaryModel:
    def __init__(self, profile, change=None):
        self.profile_snapshot = profile
        self.change = change
        self.calls = 0

    def complete(self, context, cancel, on_text_delta, **kwargs):
        self.calls += 1
        assert [tool.name for tool in kwargs["tool_definitions"]] == ["submit_context_summary"]
        payload = json.loads(context[0]["content"])
        source = next(item for item in payload["sources"] if item["tool"] == "read_file")
        args = {"notes": [{"kind": "fact", "text": "def inspect_context(): pass", "evidence": [
            {"item_id": source["itemId"], "quote": "def inspect_context(): pass"}]}]}
        if self.change:
            self.change(args, cancel)
        return ModelResponse(tool_calls=(ModelToolCall("summary-output", "submit_context_summary", args),),
                             usage=ModelUsage(input_tokens=1200, output_tokens=200))


def test_model_summary_is_grounded_and_keeps_runtime_facts_and_original_history(history):
    store, _, run, user, tool = history
    model = SummaryModel(store.read_model_profile(run["id"]))
    raw = store.read_item(tool["id"])["toolCall"]["resultJson"]
    compactor = ContextCompactor(store, model)
    summary = compactor.compact(run["id"], "pre_turn", trigger="manual", before_tokens=6000,
                                measure=lambda: ContextBuilder(store).build(run["id"]).budget.projected_input_tokens)
    assert model.calls == 1
    assert summary.continuity_notes[0].kind == "fact"
    assert summary.continuity_notes[0].evidence[0].quote == "def inspect_context(): pass"
    assert "Model note (fact;" in str(ContextBuilder(store).build(run["id"]).model_context)
    assert user["id"] in summary.source_item_ids
    assert "必须保留 notes.py。方案还未采纳。不要提交。" in summary.constraints
    assert all(value.startswith("Tool completed:") for value in summary.completed_actions)
    assert not summary.pending_approvals
    assert store.read_item(tool["id"])["toolCall"]["resultJson"] == raw
    details = ContextManagementRepository(store.database).details(run["id"], model.profile_snapshot)
    assert details.compaction.strategy == "model"
    assert details.compaction.input_tokens == 1200
    assert details.compaction.after_tokens is not None
    assert details.compaction.source_item_count == len(summary.source_item_ids)


def test_report_task_keeps_material_constraints_and_grounded_summary(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    try:
        session = store.create_session(str(workspace))
        previous, _ = store.create_run(session["id"], "撰写中文报告。必须保留金额单位。不要发布。")
        source = store.create_tool_item(previous["id"], 1, 0, "report-data", "read_file", "{}")
        quote = "本季度收入为 120 万元人民币。"
        result = json.dumps({"outcome": "success", "code": "ok", "summary": "Read report data",
                             "data": {"path": "brief.txt", "content": quote}}, ensure_ascii=False)
        store.complete_tool_item(source["id"], result, model_result_json=result)
        store.fail_run(previous["id"], "fixture")
        run, _ = store.create_run(session["id"], "继续撰写报告，不要发布。")
        model = ScriptedModel([ModelResponse(tool_calls=(ModelToolCall("summary", "submit_context_summary", {
            "notes": [{"kind": "fact", "text": quote,
                       "evidence": [{"item_id": source["id"], "quote": quote}]}],
        }),))])
        model.profile_snapshot = store.read_model_profile(run["id"])

        summary = ContextCompactor(store, model).compact(run["id"], "pre_turn")

        payload = json.loads(model.contexts[0][0]["content"])
        assert payload["currentTask"] == "继续撰写报告，不要发布。"
        assert quote in next(item["body"] for item in payload["sources"] if item["itemId"] == source["id"])
        assert [tool.name for tool in model.tool_definitions_history[0]] == ["submit_context_summary"]
        assert "coding task" not in model.instructions_history[0]
        assert any("必须保留金额单位" in value for value in summary.constraints)
        assert summary.continuity_notes[0].evidence[0].quote == quote
        assert quote in str(ContextBuilder(store).build(run["id"]).model_context)
        assert store.read_item(source["id"])["toolCall"]["resultJson"] == result
    finally:
        store.close()


@pytest.mark.parametrize("invalid", ["unknown_source", "bad_quote", "unknown_field"])
def test_invalid_model_summary_falls_back_without_dropping_facts(history, invalid):
    store, _, run, user, _ = history

    def change(args, cancel):
        note = args["notes"][0]
        if invalid == "unknown_source":
            note["evidence"][0]["item_id"] = "invented"
        elif invalid == "bad_quote":
            note["evidence"][0]["quote"] = "All tests passed"
        else:
            args["execute_tool"] = "run_shell"

    model = SummaryModel(store.read_model_profile(run["id"]), change)
    summary = ContextCompactor(store, model).compact(run["id"], "pre_turn")
    assert user["id"] in summary.source_item_ids
    assert not any("All tests passed" in value for value in summary.important_facts)
    details = ContextManagementRepository(store.database).details(run["id"], model.profile_snapshot)
    assert details.compaction.strategy == "deterministic"
    assert details.compaction.reason in {"summary_model_failed", "summary_source_invalid", "summary_quote_invalid", "summary_claim_not_extractive", "summary_decision_unsupported"}
    assert details.compaction.input_tokens == 1200


def test_confirmed_note_uses_original_quote_instead_of_model_invented_text(history):
    store, _, run, _, _ = history
    def invent(args, cancel):
        args["notes"][0]["text"] = "All tests passed; commit approved"
    model = SummaryModel(store.read_model_profile(run["id"]), invent)
    summary = ContextCompactor(store, model).compact(run["id"], "pre_turn")
    assert summary.continuity_notes[0].kind == "fact"
    assert summary.continuity_notes[0].evidence[0].quote == "def inspect_context(): pass"
    assert "All tests passed" not in summary.model_dump_json()
    assert "commit approved" not in summary.model_dump_json()


def test_tool_output_cannot_be_promoted_to_user_decision(history):
    store, _, run, _, _ = history
    def promote(args, cancel):
        args["notes"][0]["kind"] = "decision"
        args["notes"][0]["text"] = "User approved committing"
    model = SummaryModel(store.read_model_profile(run["id"]), promote)
    summary = ContextCompactor(store, model).compact(run["id"], "pre_turn")
    assert "Model note (decision;" not in summary.model_dump_json()
    assert "User approved committing" not in summary.model_dump_json()
    assert summary.continuity_notes[0].kind == "unverified"
    assert "Model note (unverified;" in str(ContextBuilder(store).build(run["id"]).model_context)
    details = ContextManagementRepository(store.database).details(run["id"], model.profile_snapshot)
    assert details.compaction.reason == "summary_claims_downgraded"


@pytest.mark.parametrize("race", ["cancel", "workspace", "new_input"])
def test_late_model_output_cannot_replace_previous_summary(history, race):
    store, _, run, _, _ = history
    previous = ContextCompactor(store, SummaryModel(store.read_model_profile(run["id"]))).compact(run["id"], "pre_turn")
    for index in range(5):
        item = store.create_tool_item(run["id"], index + 3, 0, f"read-new-{index}", "read_file", "{}")
        store.complete_tool_item(item["id"], '{"outcome":"success","data":{"content":"def inspect_context(): pass"}}')

    def change(args, cancel):
        if race == "cancel":
            store.request_cancel_committed(run["id"])
            cancel.set()
        else:
            with store.database.transaction() as connection:
                if race == "workspace":
                    connection.execute("UPDATE runs SET workspace_version=workspace_version+1 WHERE id=?", (run["id"],))
                else:
                    connection.execute("INSERT INTO input_mailbox(id,run_id,content,status,created_at) VALUES('new-input',?,'用户更正','pending',1)", (run["id"],))

    model = SummaryModel(store.read_model_profile(run["id"]), change)
    with pytest.raises(ContextCompactionError):
        ContextCompactor(store, model).compact(run["id"], "mid_turn", cancel=threading.Event())
    assert model.calls == 1
    assert store.latest_compact_summary(run["id"]) == previous
    assert store.compaction_count(run["id"]) == 1
    details = ContextManagementRepository(store.database).details(run["id"], model.profile_snapshot)
    assert details.compaction.status == ("canceled" if race == "cancel" else "failed")


def test_manual_request_is_durable_deduplicated_and_used_by_next_run(history):
    store, _, run, _, _ = history
    store.fail_run(run["id"], "fixture")
    management = ContextManagementRepository(store.database)
    first = management.request(run["id"])
    assert management.request(run["id"]) == first
    directory = store.database.data_directory
    store.close()
    restarted = SessionStore(directory)
    restarted.initialize()
    try:
        old = restarted.read_run(run["id"])
        next_run, _ = restarted.create_run(old["sessionId"], "继续")
        assert ContextManagementRepository(restarted.database).pending(next_run["id"]) == first
        model = ScriptedModel([ModelResponse(text="已检查。")])
        RuntimeEngine(restarted, model, lambda _: None).run(next_run["id"], threading.Event())
        assert restarted.compaction_count(next_run["id"]) == 1
        assert ContextManagementRepository(restarted.database).pending(next_run["id"]) is None
        assert any("Compact summary:" in str(item.get("content")) for item in model.contexts[0])
        assert restarted.read_run(next_run["id"])["status"] == "succeeded"
    finally:
        restarted.close()


def test_provider_usage_is_primary_and_tokenizer_only_estimates_changes():
    params = dict(context_window_tokens=20000, request_max_output_tokens=1000, message_count=1, tool_call_count=0, tool_result_count=0)
    raw = estimate_context_budget("hello", **params)
    measured = estimate_context_budget("hello", provider_usage=ModelUsage(input_tokens=5555, cache_read_tokens=5000), **params)
    assert measured.projected_input_tokens == measured.context_usage.active_tokens == 5555
    assert measured.measurement == "provider"
    anchored = estimate_context_budget("hello", provider_usage=ModelUsage(input_tokens=5555),
                                       provider_calibration_estimate=raw.estimated_input_tokens, **params)
    assert anchored.projected_input_tokens == 5555
    assert anchored.measurement == "provider_calibrated"
    assert raw.measurement == "tokenizer_estimate"


def test_calibration_rejects_model_or_request_configuration_switch(history):
    store, _, run, _, _ = history
    RuntimeEngine(store, ScriptedModel([ModelResponse(text="done", usage=ModelUsage(input_tokens=5000))]), lambda _: None).run(run["id"], threading.Event())
    repo = ContextSnapshotRepository(store.database)
    profile = store.read_model_profile(run["id"])
    assert repo.latest_token_calibration(run["id"], profile_hash=canonical_sha256(profile.model_dump(mode="json")))
    changed = profile.model_copy(update={"wire_api": "openai_responses"})
    assert repo.latest_token_calibration(run["id"], profile_hash=canonical_sha256(changed.model_dump(mode="json"))) is None
    assert repo.latest_token_calibration(run["id"], profile_hash="f" * 64) is None


def test_connection_configuration_scopes_cross_run_calibration_without_credentials(history):
    store, session, current, _, _ = history
    store.fail_run(current["id"], "fixture")
    config = ModelConfig(id="deepseek-flash", name="Fixture", vendor="DeepSeek", url="https://api.deepseek.com/chat/completions",
                         apiKey="fixture-key-only", supportsToolCall=True, supportsReasoning=False)
    spec = MODEL_CATALOG.profile(config.id, config=config)
    profile = spec.snapshot(config)
    changed = spec.snapshot(config.model_copy(update={"url": "https://other.example/chat/completions"}))
    rotated = spec.snapshot(config.model_copy(update={"api_key": "rotated-fixture-key", "name": "Renamed"}))
    assert profile.connection_fingerprint != changed.connection_fingerprint
    assert profile.connection_fingerprint == rotated.connection_fingerprint
    assert "fixture-key" not in profile.model_dump_json()
    assert "https://" not in profile.model_dump_json()
    measured, _ = store.create_run(session["id"], "measure", model_id=profile.model_id, model_profile=profile)
    RuntimeEngine(store, ScriptedModel([ModelResponse(text="done", usage=ModelUsage(input_tokens=7000))]), lambda _: None).run(measured["id"], threading.Event())
    next_run, _ = store.create_run(session["id"], "continue", model_id=profile.model_id, model_profile=profile)
    assert ContextBuilder(store).build(next_run["id"]).budget.measurement == "provider_calibrated"
    store.fail_run(next_run["id"], "fixture")
    switched, _ = store.create_run(session["id"], "continue", model_id=changed.model_id, model_profile=changed)
    assert ContextBuilder(store).build(switched["id"]).budget.measurement == "tokenizer_estimate"


def test_new_context_contract_fixture_and_unknown_fields():
    root = Path(__file__).resolve().parents[2]
    fixture = json.loads((root / "protocol/fixtures/context-management-v1.json").read_text())
    ContextDetailsRequestDto.model_validate(fixture["detailsRequest"])
    ContextCompactRequestDto.model_validate(fixture["compactRequest"])
    ContextDetails.model_validate_json(json.dumps(fixture["details"]))
    ContextCompactionRecord.model_validate_json(json.dumps(fixture["compaction"]))
    with pytest.raises(ValueError):
        ContextDetails.model_validate_json(json.dumps({**fixture["details"], "rawHistory": "not allowed"}))


def test_application_reads_only_metadata_and_queues_compaction(history):
    store, _, run, _, _ = history
    application = ContextApplication(management=ContextManagementRepository(store.database))
    record = application.request_compaction(run["id"])
    assert application.details(run["id"]).manual_pending
    assert record.status == "queued"


def test_interrupted_summary_is_not_replayed_and_user_can_request_again(history):
    store, _, run, _, _ = history
    management = ContextManagementRepository(store.database)
    request = management.request(run["id"])
    management.record(run["id"], request.model_copy(update={"stage": "started", "status": "running"}))
    store.fail_run(run["id"], "runtime_restart")
    directory = store.database.data_directory
    store.close()
    restarted = SessionStore(directory)
    restarted.initialize()
    try:
        repository = ContextManagementRepository(restarted.database)
        details = repository.details(run["id"], restarted.read_model_profile(run["id"]))
        assert details.compaction.status == "failed"
        assert details.compaction.reason == "interrupted"
        assert not details.manual_pending
        assert restarted.latest_compact_summary(run["id"]) is None
        assert repository.request(run["id"]).request_id != request.request_id
    finally:
        restarted.close()


def test_committed_summary_stays_successful_if_post_build_metrics_are_lost(history, monkeypatch):
    store, _, run, _, _ = history
    original = ContextManagementRepository.record
    def lose_metrics(self, run_id, record):
        if record.stage == "finished":
            raise RuntimeError("synthetic metrics interruption")
        original(self, run_id, record)
    monkeypatch.setattr(ContextManagementRepository, "record", lose_metrics)
    with pytest.raises(RuntimeError, match="metrics interruption"):
        ContextCompactor(store).compact(run["id"], "pre_turn")
    assert store.compaction_count(run["id"]) == 1
    assert store.latest_compact_summary(run["id"]) is not None
    store.fail_run(run["id"], "synthetic_shutdown")
    details = ContextManagementRepository(store.database).details(run["id"], store.read_model_profile(run["id"]))
    assert details.compaction.status == "succeeded"
    assert details.compaction.after_tokens is None



def _handoff(source_id: str, **extra):
    return {"current_work": "Checking how notes.py is inspected", "approach": "Read the module first",
            "progress": ["notes.py defines inspect_context"], "open_questions": ["Tests have not run"],
            "next_steps": ["Run the notes tests"], "source_item_ids": [source_id], **extra}


def test_model_handoff_replaces_next_actions_and_is_persisted_and_rendered(history):
    store, _, run, _, tool = history

    def change(args, cancel):
        args["handoff"] = _handoff(tool["id"])

    model = SummaryModel(store.read_model_profile(run["id"]), change)
    summary = ContextCompactor(store, model).compact(run["id"], "pre_turn")

    assert summary.handoff is not None
    assert summary.handoff.current_work == "Checking how notes.py is inspected"
    assert summary.handoff.source_item_ids == (tool["id"],)
    assert summary.next_actions == ("Model next step, unverified: Run the notes tests",)
    assert store.latest_compact_summary(run["id"]) == summary
    rendered = str(ContextBuilder(store).build(run["id"]).model_context)
    assert "Working handoff written by the summarizer" in rendered
    assert "Checking how notes.py is inspected" in rendered
    assert "search_history" in rendered


def test_handoff_is_not_kept_once_a_later_round_cannot_write_one(history):
    store, _, run, _, tool = history

    def change(args, cancel):
        args["handoff"] = _handoff(tool["id"])

    profile = store.read_model_profile(run["id"])
    ContextCompactor(store, SummaryModel(profile, change)).compact(run["id"], "pre_turn")
    for step in range(1, 13):
        item = store.create_tool_item(run["id"], step, 0, f"later-{step}", "read_file", "{}")
        raw = json.dumps({"outcome": "success", "data": {"path": f"later_{step}.py", "content": "later"}})
        store.complete_tool_item(item["id"], raw, model_result_json=raw)

    summary = ContextCompactor(store).compact(run["id"], "mid_turn")

    assert summary.handoff is None
    assert "Working handoff" not in str(ContextBuilder(store).build(run["id"]).model_context)


@pytest.mark.parametrize("invalid", ["note", "handoff"])
def test_one_unsupported_part_does_not_discard_the_grounded_rest(history, invalid):
    store, _, run, _, tool = history

    def change(args, cancel):
        if invalid == "note":
            args["notes"].append({"kind": "fact", "text": "All tests passed", "evidence": [
                {"item_id": tool["id"], "quote": "All tests passed"}]})
        else:
            args["handoff"] = _handoff("invented-item")

    model = SummaryModel(store.read_model_profile(run["id"]), change)
    summary = ContextCompactor(store, model).compact(run["id"], "pre_turn")

    assert [note.evidence[0].quote for note in summary.continuity_notes] == ["def inspect_context(): pass"]
    assert summary.handoff is None
    assert "All tests passed" not in summary.model_dump_json()
    details = ContextManagementRepository(store.database).details(run["id"], model.profile_snapshot)
    assert details.compaction.strategy == "model"
    assert details.compaction.reason == "summary_notes_dropped"


def test_handoff_alone_is_an_accepted_summary(history):
    store, _, run, _, tool = history

    def change(args, cancel):
        args["notes"] = []
        args["handoff"] = _handoff(tool["id"])

    model = SummaryModel(store.read_model_profile(run["id"]), change)
    summary = ContextCompactor(store, model).compact(run["id"], "pre_turn")
    assert summary.handoff is not None and not summary.continuity_notes
    details = ContextManagementRepository(store.database).details(run["id"], model.profile_snapshot)
    assert details.compaction.strategy == "model"


def test_next_actions_follow_the_newest_round_and_decisions_have_one_field(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    try:
        session = store.create_session(str(workspace))
        first, _ = store.create_run(session["id"], "调查缓存。")
        plan = store.create_assistant_item(first["id"], 1)
        store.append_item_content(plan["id"], "Next, inspect cache.py.")
        store.complete_assistant_item_committed(plan["id"])
        decision = store.create_assistant_item(first["id"], 2)
        store.append_item_content(decision["id"], "我决定采用 LRU 策略。")
        store.complete_assistant_item_committed(decision["id"])
        store.fail_run(first["id"], "fixture")
        second, _ = store.create_run(session["id"], "继续。")
        summary = ContextCompactor(store).compact(second["id"], "pre_turn")
        assert any("inspect cache.py" in value for value in summary.next_actions)
        assert any("决定采用 LRU" in value for value in summary.important_decisions)
        assert not any("决定采用 LRU" in value for value in summary.important_facts)

        for step in range(1, 13):
            later = store.create_assistant_item(second["id"], step)
            store.append_item_content(later["id"], f"Step {step}. Next, verify store.py.")
            store.complete_assistant_item_committed(later["id"])
        summary = ContextCompactor(store).compact(second["id"], "mid_turn")

        assert not any("inspect cache.py" in value for value in summary.next_actions)
        assert 0 < len(summary.next_actions) <= 4
        assert all("verify store.py" in value for value in summary.next_actions)
        assert any("决定采用 LRU" in value for value in summary.important_decisions)
    finally:
        store.close()


def test_model_sees_most_of_a_long_round_and_not_the_bodies_of_retained_notes(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    try:
        session = store.create_session(str(workspace))
        previous, _ = store.create_run(session["id"], "检查模块。")
        anchor = store.create_tool_item(previous["id"], 1, 0, "anchor", "read_file", "{}")
        anchor_raw = json.dumps({"outcome": "success", "data": {"path": "anchor.py", "content": "ANCHOR = 1\n" + "a" * 9000}})
        store.complete_tool_item(anchor["id"], anchor_raw, model_result_json=anchor_raw)
        store.fail_run(previous["id"], "fixture")
        run, _ = store.create_run(session["id"], "继续。")
        model = ScriptedModel([
            ModelResponse(tool_calls=(ModelToolCall("first", "submit_context_summary", {"notes": [
                {"kind": "fact", "text": "ANCHOR = 1", "evidence": [{"item_id": anchor["id"], "quote": "ANCHOR = 1"}]},
            ]}),)),
            ModelResponse(tool_calls=(ModelToolCall("second", "submit_context_summary", {"notes": [
                {"kind": "fact", "text": "VALUE_39 = 39", "evidence": [{"item_id": "placeholder", "quote": "VALUE_39 = 39"}]},
            ]}),)),
        ])
        model.profile_snapshot = store.read_model_profile(run["id"])
        ContextCompactor(store, model).compact(run["id"], "pre_turn")
        ids = []
        for step in range(1, 41):
            item = store.create_tool_item(run["id"], step, 0, f"module-{step}", "read_file", "{}")
            raw = json.dumps({"outcome": "success", "data": {"path": f"m{step}.py", "content": f"VALUE_{step} = {step}\n" + "x" * 10_000}})
            store.complete_tool_item(item["id"], raw, model_result_json=raw)
            ids.append(item["id"])

        ContextCompactor(store, model).compact(run["id"], "mid_turn")

        payload_text = model.contexts[1][0]["content"]
        payload = json.loads(payload_text)
        assert len(payload_text.encode()) <= 96 * 1024
        cited = {source["itemId"] for source in payload["sources"]}
        assert anchor["id"] not in cited
        assert "ANCHOR = 1" in json.dumps(payload["previousSummary"])
        assert len(cited & set(ids)) >= 20
    finally:
        store.close()

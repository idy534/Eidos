from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from eidos_runtime.context.builder import ContextBuilder
from eidos_runtime.context.compactor import ContextCompactor, ContextCompactionError, _fit_summary, _merge
from eidos_runtime.context.facts import CompactNoteEvidence
from eidos_runtime.context.verified_compaction import CompactionVerificationError, VerifiedCompactSummary
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.model.client import ModelResponse, ModelToolCall
from eidos_runtime.model.config import default_profile_snapshot
from eidos_runtime.model.context_projection import project_context, recovery_context
from eidos_runtime.persistence.errors import PersistenceCorruptionError
from eidos_runtime.tools.read_tool_output import read_tool_output_entry


CRITICAL = "CACHE_TTL_SECONDS = 300"


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


def read_record(store, run_id, step, content, *, name="read_file", data=None):
    item = store.create_tool_item(run_id, step, 0, f"call-{step}", name, "{}")
    raw = json.dumps({"outcome": "success", "summary": "Read source",
                      "data": data or {"path": f"source_{step}.py", "content": content}}, ensure_ascii=False)
    store.complete_tool_item(item["id"], raw, model_result_json=raw)
    return item, raw


class EvidenceModel:
    def __init__(self, profile, *, quote=CRITICAL, kind="fact", fail=False):
        self.profile_snapshot = profile
        self.quote = quote
        self.kind = kind
        self.fail = fail
        self.payloads = []

    def complete(self, context, cancel, on_text_delta, **options):
        payload = json.loads(context[0]["content"])
        self.payloads.append(payload)
        if self.fail:
            raise ValueError("scripted failure")
        source = next((s for s in payload["sources"] if self.quote in s["body"]), None) if len(self.payloads) == 1 else None
        quote = self.quote if source else "observed"
        source = source or next(s for s in reversed(payload["sources"]) if quote in s["body"])
        return ModelResponse(tool_calls=(ModelToolCall("summary", "submit_context_summary", {
            "notes": [{"kind": self.kind, "text": quote,
                       "evidence": [{"item_id": source["itemId"], "quote": quote}]}],
        }),))


def seed(history):
    store, session = history
    profile = default_profile_snapshot("deepseek-flash").model_copy(update={
        "context_window_tokens": 100_000, "max_output_tokens": 8_192,
    })
    previous, _ = store.create_run(session["id"], "检查登录配置。不要提交代码。", model_profile=profile)
    for index in range(6):
        read_record(store, previous["id"], index + 1, f"def old_{index}(): pass\n# observed")
    critical, raw = read_record(store, previous["id"], 7, CRITICAL)
    store.fail_run(previous["id"], "fixture")
    run, _ = store.create_run(session["id"], "继续检查登录配置。", model_profile=profile)
    return store, run, critical, raw, profile


def test_twenty_compactions_preserve_accepted_evidence_and_originals(history):
    store, run, critical, raw, profile = seed(history)
    model = EvidenceModel(profile)
    for cycle in range(20):
        if cycle:
            for index in range(40):
                step = (cycle - 1) * 40 + index + 1
                read_record(store, run["id"], step, f"def new_{step}(): pass\n# observed")
        summary = ContextCompactor(store, model).compact(run["id"], "mid_turn")
        built = ContextBuilder(store).build(run["id"])
        assert CRITICAL in summary.model_dump_json(), f"fact lost at cycle {cycle + 1}"
        assert CRITICAL in json.dumps(built.model_context), f"input lost at cycle {cycle + 1}"
        assert critical["id"] in summary.source_item_ids
        assert store.read_item(critical["id"])["toolCall"]["resultJson"] == raw
        assert store.verified_compaction_repository().latest(run["id"]).verification_result == "verified"
        assert store.verified_compaction_repository().latest(run["id"]).compaction_version == 2
    assert store.compaction_count(run["id"]) == 20
    assert CRITICAL in json.dumps(model.payloads[-1]), "retained note was not re-anchored"


def test_failed_model_retains_evidence_and_verifier_rejects_omission(history):
    store, run, _, _, profile = seed(history)
    previous = ContextCompactor(store, EvidenceModel(profile)).compact(run["id"], "pre_turn")
    for index in range(5):
        read_record(store, run["id"], index + 1, "observed")
    summary = ContextCompactor(store, EvidenceModel(profile, fail=True)).compact(run["id"], "mid_turn")
    assert summary.continuity_notes == previous.continuity_notes
    repository = store.verified_compaction_repository()
    outbox_count = store.pending_outbox_count()
    with pytest.raises(CompactionVerificationError, match="continuity"):
        repository.verify_and_persist(
            run_id=run["id"], summary=summary.model_copy(update={"continuity_notes": ()}),
            input_range=(0, 10_000),
        )
    assert store.latest_compact_summary(run["id"]) == summary
    assert store.pending_outbox_count() == outbox_count


@pytest.mark.parametrize(("name", "data", "expected"), [
    ("read_file", {"path": "testing_examples.md", "content": "示例：17 passed。这不是本次运行结果。"}, "unverified"),
    ("run_shell", {"stdout": "17 passed", "exitCode": 0, "termination": "exit", "executionStatus": "exited"}, "verification"),
    ("run_shell", {"stdout": "17 passed", "exitCode": 1}, "unverified"),
    ("run_shell", {"stdout": "17 passed", "exitCode": 0, "outputComplete": False}, "unverified"),
])
def test_verification_requires_completed_execution_evidence(history, name, data, expected):
    store, session = history
    previous, _ = store.create_run(session["id"], "检查验证记录。")
    read_record(store, previous["id"], 1, "", name=name, data=data)
    store.fail_run(previous["id"], "fixture")
    run, _ = store.create_run(session["id"], "继续。")
    model = EvidenceModel(store.read_model_profile(run["id"]), quote="17 passed", kind="verification")
    summary = ContextCompactor(store, model).compact(run["id"], "pre_turn")
    assert summary.continuity_notes[0].kind == expected
    assert "17 passed" in json.dumps(ContextBuilder(store).build(run["id"]).model_context)


def test_summary_sources_include_long_result_tail(history):
    store, session = history
    previous, _ = store.create_run(session["id"], "检查日志最后的边界条件。")
    content = "start\n" + "unrelated log line\n" * 700 + CRITICAL
    read_record(store, previous["id"], 1, content)
    store.fail_run(previous["id"], "fixture")
    run, _ = store.create_run(session["id"], "继续。")
    model = EvidenceModel(store.read_model_profile(run["id"]))
    summary = ContextCompactor(store, model).compact(run["id"], "pre_turn")
    assert any(CRITICAL in source["body"] for source in model.payloads[0]["sources"])
    assert summary.continuity_notes[0].evidence[0].quote == CRITICAL


def test_evidence_survives_budget_fitting_recovery_and_restart(history):
    store, run, _, _, profile = seed(history)
    summary = ContextCompactor(store, EvidenceModel(profile)).compact(run["id"], "pre_turn")
    with pytest.raises(ContextCompactionError, match="critical summary facts"):
        _fit_summary(summary, max_tokens=20)
    evidence = next(item for item in ContextBuilder(store).build(run["id"]).model_context
                    if item.get("sectionId") == "continuity-evidence")
    messages = tuple({"type": "user", "content": f"other {index}"} for index in range(30))
    context = (*messages[:6], evidence, *messages[6:])
    projected = project_context(context, text_bytes=128)
    recovered = recovery_context(projected, 1_024)
    assert evidence in recovered
    serialized = store.verified_compaction_repository().latest(run["id"]).model_dump_json()
    assert VerifiedCompactSummary.model_validate_json(serialized).summary == summary
    directory = store.database.data_directory
    store.close()
    restarted = SessionStore(directory)
    restarted.initialize()
    try:
        assert restarted.latest_compact_summary(run["id"]).continuity_notes == summary.continuity_notes
    finally:
        restarted.close()


def test_new_evidence_exceeding_budget_keeps_last_summary_and_raw_sources(history):
    store, run, _, _, profile = seed(history)
    previous = ContextCompactor(store, EvidenceModel(profile)).compact(run["id"], "pre_turn")
    quotes = tuple("龘" * 799 + suffix for suffix in ("甲", "乙", "丙"))
    added, raw = read_record(store, run["id"], 1, "\n".join(quotes))
    for step in range(2, 5):
        read_record(store, run["id"], step, "observed")

    class LargeEvidenceModel(EvidenceModel):
        def complete(self, context, cancel, on_text_delta, **options):
            payload = json.loads(context[0]["content"])
            source = next(s for s in payload["sources"] if quotes[0] in s["body"])
            return ModelResponse(tool_calls=(ModelToolCall("summary", "submit_context_summary", {
                "notes": [{"kind": "fact", "text": "quoted evidence",
                           "evidence": [{"item_id": source["itemId"], "quote": quote}]} for quote in quotes],
            }),))

    with pytest.raises(ContextCompactionError, match="critical summary facts"):
        ContextCompactor(store, LargeEvidenceModel(profile)).compact(run["id"], "mid_turn")
    assert store.latest_compact_summary(run["id"]) == previous
    assert store.compaction_count(run["id"]) == 1
    assert store.read_item(added["id"])["toolCall"]["resultJson"] == raw


@pytest.mark.parametrize("change", ["quote", "kind", "source_kind"])
def test_persistence_rechecks_evidence_instead_of_trusting_the_model_adapter(history, change):
    store, run, _, _, profile = seed(history)
    summary = ContextCompactor(store, EvidenceModel(profile)).compact(run["id"], "pre_turn")
    note = summary.continuity_notes[0]
    if change == "quote":
        forged = note.model_copy(update={"evidence": (CompactNoteEvidence(
            item_id=note.evidence[0].item_id, quote="invented quote",
        ),)})
    else:
        forged = note.model_copy(update={change: "verification" if change == "kind" else "execution"})
    candidate = summary.model_copy(update={"continuity_notes": (*summary.continuity_notes, forged)})
    with pytest.raises(CompactionVerificationError, match="continuity evidence"):
        store.verified_compaction_repository().verify_and_persist(
            run_id=run["id"], summary=candidate, input_range=(0, 10_000),
        )
    assert store.latest_compact_summary(run["id"]) == summary
    assert store.compaction_count(run["id"]) == 1


def test_old_verified_summary_hash_and_legacy_notes_remain_compatible(history):
    store, run, _, _, _ = seed(history)
    summary = ContextCompactor(store).compact(run["id"], "pre_turn")
    assert "continuity_notes" not in summary.model_dump(mode="json")
    verified = store.verified_compaction_repository().latest(run["id"])
    assert VerifiedCompactSummary.model_validate_json(verified.model_dump_json()).summary_hash == verified.summary_hash
    legacy = "Model note (fact; sources=legacy; verify against originals): " + CRITICAL
    protected = summary.model_copy(update={"important_facts": ("workspace state: version=0", legacy)})
    merged = _merge(protected.important_facts, tuple(f"observation {i}" for i in range(40)))
    fitted = _fit_summary(protected.model_copy(update={"important_facts": merged}), max_tokens=4_096)
    assert legacy in fitted.important_facts
    store.verified_compaction_repository().verify_and_persist(
        run_id=run["id"], summary=fitted, input_range=(0, 10_000),
    )
    assert "Legacy summary report, unverified:" in str(ContextBuilder(store).build(run["id"]).model_context)


def test_historical_user_request_keeps_tail_and_original_readback(history):
    store, session = history
    request = "背景说明。" * 180 + "这次的范围限于 src/auth.py。"
    previous, user = store.create_run(session["id"], request)
    store.fail_run(previous["id"], "fixture")
    run, _ = store.create_run(session["id"], "继续调查。")
    for cycle in range(2):
        if cycle:
            for index in range(40):
                read_record(store, run["id"], index + 1, "observed")
        summary = ContextCompactor(store).compact(run["id"], "mid_turn")
        assert "这次的范围限于 src/auth.py。" in "\n".join(summary.important_facts)
        assert "read_tool_output" in "\n".join(summary.important_facts)
        assert "src/auth.py" in str(ContextBuilder(store).build(run["id"]).model_context)
        assert user["id"] in summary.source_item_ids
    result = read_tool_output_entry(store, run["id"]).adapter.execute({
        "callId": user["id"], "stream": "context", "maxBytes": 4_096,
    }, threading.Event())
    assert result["outcome"] == "success"
    assert result["data"]["content"] == request


def test_retained_evidence_can_be_read_back_without_repeating_the_tool(history):
    store, run, _, raw, profile = seed(history)
    summary = ContextCompactor(store, EvidenceModel(profile)).compact(run["id"], "pre_turn")
    source = summary.continuity_notes[0].evidence[0]
    result = read_tool_output_entry(store, run["id"]).adapter.execute({
        "callId": source.call_id, "itemId": source.item_id, "stream": "result",
    }, threading.Event())
    assert result["outcome"] == "success"
    assert json.loads(result["data"]["content"]) == json.loads(raw)


@pytest.mark.parametrize("version", [2, True])
def test_unknown_or_invalid_continuity_format_fails_closed(history, version):
    store, run, _, _, profile = seed(history)
    ContextCompactor(store, EvidenceModel(profile)).compact(run["id"], "pre_turn")
    with store.database.transaction() as connection:
        connection.execute(
            "UPDATE compact_summaries SET summary_metadata_json = json_set(summary_metadata_json, '$.continuity_version', json(?)) WHERE run_id=?",
            (json.dumps(version), run["id"]),
        )
    with pytest.raises(PersistenceCorruptionError):
        store.latest_compact_summary(run["id"])


def test_same_file_versions_remain_distinct_summary_evidence(history):
    store, session = history
    previous, _ = store.create_run(session["id"], "比较配置变化前后。")
    quotes = (CRITICAL, "CACHE_TTL_SECONDS = 120")
    for step, quote in enumerate(quotes, 1):
        read_record(store, previous["id"], step, "", data={"path": "config.py", "content": quote})
    store.fail_run(previous["id"], "fixture")
    run, _ = store.create_run(session["id"], "继续比较。")

    class VersionsModel(EvidenceModel):
        def complete(self, context, cancel, on_text_delta, **options):
            sources = json.loads(context[0]["content"])["sources"]
            notes = [{"kind": "fact", "text": quote, "evidence": [{
                "item_id": next(source["itemId"] for source in sources if quote in source["body"]), "quote": quote,
            }]} for quote in quotes]
            return ModelResponse(tool_calls=(ModelToolCall("summary", "submit_context_summary", {"notes": notes}),))

    summary = ContextCompactor(store, VersionsModel(store.read_model_profile(run["id"]))).compact(run["id"], "pre_turn")
    assert {note.evidence[0].quote for note in summary.continuity_notes} == set(quotes)
    assert len({note.evidence[0].item_id for note in summary.continuity_notes}) == 2


def test_generated_preview_marker_cannot_become_a_fact(history):
    store, session = history
    previous, _ = store.create_run(session["id"], "检查长资料。")
    read_record(store, previous["id"], 1, "long document content\n" * 800)
    store.fail_run(previous["id"], "fixture")
    run, _ = store.create_run(session["id"], "继续。")
    marker = "[Content truncated for this request."

    class MarkerModel(EvidenceModel):
        def complete(self, context, cancel, on_text_delta, **options):
            source = next(s for s in json.loads(context[0]["content"])["sources"] if marker in s["body"])
            return ModelResponse(tool_calls=(ModelToolCall("summary", "submit_context_summary", {"notes": [{
                "kind": "fact", "text": marker, "evidence": [{"item_id": source["itemId"], "quote": marker}],
            }]}),))

    summary = ContextCompactor(store, MarkerModel(store.read_model_profile(run["id"]))).compact(run["id"], "pre_turn")
    assert not summary.continuity_notes
    assert marker not in summary.model_dump_json()

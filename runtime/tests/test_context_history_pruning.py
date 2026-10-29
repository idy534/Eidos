from __future__ import annotations

import json
from pathlib import Path
import threading
from unittest.mock import patch

import pytest

from eidos_runtime.context.builder import ContextBuilder
from eidos_runtime.model.context_projection import model_result_json
from eidos_runtime.context.budget import estimate_model_request_budget
from eidos_runtime.context.compactor import (
    ContextCompactor,
    ContextCompactionError,
    _fit_summary,
)
from eidos_runtime.context.facts import CompactSummary, ContextFacts, ContextItemFact
from eidos_runtime.context.tokens import _encoding, estimate_text_tokens
from eidos_runtime.context.tool_history import clear_old_tool_results
from eidos_runtime.db.errors import InvalidRunStateError
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.model.client import (
    ModelResponse,
    ModelToolCall,
    ModelUsage,
    ScriptedModel,
)
from eidos_runtime.persistence.context_snapshots import ContextSnapshotRepository
from eidos_runtime.runtime.engine import RuntimeEngine
from eidos_runtime.tools.read_tool_output import read_tool_output_entry


@pytest.fixture
def history_store(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    session = store.create_session(str(workspace))
    try:
        yield store, session
    finally:
        store.close()


def _result(content: str, **extra: object) -> str:
    return json.dumps(
        {
            "outcome": "success",
            "code": "ok",
            "summary": "Read file",
            "sideEffectsMayExist": False,
            "reconciliationRequired": False,
            "data": {"path": "notes.py", "content": content},
            **extra,
        },
        ensure_ascii=False,
    )


def _tools(count: int = 12) -> tuple[tuple[dict[str, object], ...], ContextFacts]:
    facts = tuple(
        ContextItemFact(
            item_id=f"item-{n}",
            run_id="run",
            kind="tool_call",
            status="completed",
            provider_call_id=f"call-{n}",
            tool_name="read_file",
            model_step_index=n,
            model_result_json=_result(f"line {n}\n" * 2_000),
        )
        for n in range(count)
    )
    context = tuple(
        value
        for item in facts
        for value in (
            {
                "type": "tool_call",
                "callId": item.provider_call_id,
                "name": item.tool_name,
                "arguments": "{}",
                "responseId": f"response-{item.model_step_index}",
            },
            {
                "type": "tool_result",
                "callId": item.provider_call_id,
                "name": item.tool_name,
                "result": item.model_result_json,
            },
        )
    )
    return context, ContextFacts(run_id="run", session_id="session", items=facts)


def test_old_outputs_clear_before_summary_and_keep_pairing_and_recent_results():
    context, facts = _tools()
    cleared = clear_old_tool_results(context, facts, target_reduction=1)
    assert len(cleared) == len(context)
    assert cleared[0] == context[0]
    marker = json.loads(cleared[1]["result"])
    assert marker["contextCleared"] is True
    assert marker["readBack"] == {
        "tool": "read_tool_output",
        "callId": "call-0",
        "itemId": "item-0",
        "stream": "result",
    }
    assert "originalResultSha256" not in marker
    assert cleared[2:] == context[2:]
    assert clear_old_tool_results(context, facts, target_reduction=1) == cleared
    assert "contextCleared" not in context[1]["result"]


@pytest.mark.parametrize(
    "changed",
    [
        "running",
        "failed",
        "approval",
        "memory",
        "skill",
        "image",
        "duplicate",
        "legacy_barrier",
        "unknown",
        "live_shell",
    ],
)
def test_clearing_keeps_nonrecoverable_or_unresolved_evidence(changed):
    context, facts = _tools()
    items = list(facts.items)
    context = list(context)
    if changed in {"running", "failed"}:
        items[0] = items[0].model_copy(update={"status": changed})
    elif changed in {"memory", "skill"}:
        items[0] = items[0].model_copy(
            update={"tool_name": "memory_read" if changed == "memory" else "skill_read"}
        )
    elif changed == "approval":
        context[1] = {
            **context[1],
            "result": _result("x" * 10_000, reconciliationRequired=True),
        }
    elif changed == "image":
        context[1] = {**context[1], "toolContent": {"items": [{"kind": "binary"}]}}
    elif changed in {"legacy_barrier", "unknown", "live_shell"}:
        payload = json.loads(context[1]["result"])
        key, value = {
            "legacy_barrier": ("reconciliationRequired", True),
            "unknown": ("workspaceChangeState", "unknown"),
            "live_shell": ("executionStatus", "running"),
        }[changed]
        payload["data"][key] = value
        context[1] = {**context[1], "result": json.dumps(payload)}
    else:
        items[1] = items[1].model_copy(
            update={"provider_call_id": items[0].provider_call_id}
        )
    original = tuple(context)
    cleared = clear_old_tool_results(
        original, facts.model_copy(update={"items": tuple(items)}), target_reduction=1
    )
    assert cleared[1] == original[1]


def test_recent_multi_tool_response_is_retained_as_one_group():
    context, facts = _tools()
    items = list(facts.items)
    items[0] = items[0].model_copy(
        update={"model_step_index": items[-1].model_step_index}
    )
    cleared = clear_old_tool_results(
        context, facts.model_copy(update={"items": tuple(items)}), target_reduction=1
    )
    assert cleared[1] == context[1]
    assert json.loads(cleared[3]["result"])["contextCleared"] is True


def test_replayed_reasoning_is_counted_once_per_response():
    from eidos_runtime.model.replay import ModelReasoningPart, ModelResponseReplay

    context, _ = _tools(2)
    replay = ModelResponseReplay(
        reasoning_parts=(ModelReasoningPart(content="reasoning " * 1_000),),
        tool_call_ids=("call-0", "call-1"),
    ).model_dump(mode="json")
    duplicated = tuple(
        {**item, "responseId": "one-response", "responseReplay": replay}
        if item["type"] == "tool_call"
        else item
        for item in context
    )
    single = tuple(
        {key: value for key, value in item.items() if key != "responseReplay"}
        if index == 2
        else item
        for index, item in enumerate(duplicated)
    )
    options = dict(
        instructions="rules",
        tool_definitions=(),
        context_window_tokens=128_000,
        request_max_output_tokens=8_192,
    )
    assert (
        estimate_model_request_budget(duplicated, **options).estimated_input_tokens
        == estimate_model_request_budget(single, **options).estimated_input_tokens
    )


def test_builder_clears_old_output_without_deleting_and_can_page_it_back(history_store):
    store, session = history_store
    run, _ = store.create_run(session["id"], "检查结果，先不要改代码。")
    profile = store.read_model_profile(run["id"]).model_copy(
        update={"context_window_tokens": 16_384, "max_output_tokens": 1_024}
    )
    raw = _result("\n".join(f"field_{n} = unresolved_{n}" for n in range(6_000)))
    old = store.create_tool_item(run["id"], 1, 0, "old-read", "read_file", "{}")
    store.complete_tool_item(old["id"], raw, model_result_json=raw)
    for n in range(8):
        item = store.create_tool_item(
            run["id"], n + 2, 0, f"recent-{n}", "read_file", "{}"
        )
        store.complete_tool_item(item["id"], _result(f"recent {n}"))
    with patch.object(store, "read_model_profile", return_value=profile):
        built = ContextBuilder(store).build(run["id"])
        assert built.budget.fits
        again = ContextBuilder(store).build(run["id"])
        assert again.model_context == built.model_context
    marker_item = next(
        item
        for item in built.model_context
        if item.get("callId") == "old-read" and item["type"] == "tool_result"
    )
    marker = json.loads(marker_item["result"])
    assert marker["contextCleared"] is True
    assert (
        store.connection.execute(
            "SELECT model_result_json FROM tool_calls WHERE item_id=?", (old["id"],)
        ).fetchone()[0]
        == raw
    )
    baseline = tuple(
        {**item, "result": raw} if item is marker_item else item
        for item in built.model_context
    )
    original_budget = estimate_model_request_budget(
        baseline,
        instructions=built.instructions.system_text,
        tool_definitions=(),
        context_window_tokens=profile.context_window_tokens,
        request_max_output_tokens=profile.max_output_tokens,
    )
    assert (
        built.budget.projected_input_tokens
        < original_budget.projected_input_tokens // 2
    )
    entry = read_tool_output_entry(store, run["id"])
    parts = []
    offset = 0
    while True:
        result = entry.adapter.execute(
            {
                **{k: v for k, v in marker["readBack"].items() if k != "tool"},
                "offsetBytes": offset,
            },
            threading.Event(),
        )
        assert result["outcome"] == "success"
        page = result["data"]
        parts.append(page["content"])
        offset = page["nextOffset"]
        if not page["hasMoreAfter"]:
            break
    assert "".join(parts) == model_result_json(raw)
    assert store.compaction_count(run["id"]) == 0


def test_soft_compaction_only_commits_eligible_sources_and_preserves_recent_changes(
    history_store,
):
    store, session = history_store
    run, goal = store.create_run(session["id"], "必须保留原始文件，不要执行写入。")
    old_ids = []
    for n in range(40):
        item = store.create_tool_item(
            run["id"],
            n + 1,
            0,
            f"call-{n}",
            "read_file" if n < 30 else "apply_patch",
            "{}",
        )
        raw = (
            _result(f"fact {n}")
            if n < 30
            else json.dumps(
                {
                    "outcome": "success",
                    "data": {"path": "recent.py", "workspaceChanged": True},
                }
            )
        )
        store.complete_tool_item(item["id"], raw, workspace_changed=n >= 30)
        old_ids.append(item["id"])
    summary = ContextCompactor(store).compact(
        run["id"], "mid_turn", keep_recent_items=16
    )
    assert set(old_ids[-16:]).isdisjoint(summary.source_item_ids)
    assert goal["id"] not in summary.source_item_ids
    verified = store.verified_compaction_repository().latest(run["id"])
    derived = store.verified_compaction_repository().load_facts(
        run["id"], required_item_ids=summary.source_item_ids
    )
    assert set(verified.source_tool_call_ids) == set(derived.available_tool_call_ids)
    assert verified.source_tool_call_ids
    assert "recent.py" not in summary.workspace_changes
    assert store.compaction_count(run["id"]) == 1
    assert any("Tool completed:" in value for value in summary.completed_actions)


def test_engine_uses_cleared_snapshot_before_requesting_summary(history_store):
    store, session = history_store
    old, _ = store.create_run(session["id"], "inspect")
    content = "\n".join(f"field_{n} = unresolved_{n}" for n in range(6_000))
    item = store.create_tool_item(old["id"], 1, 0, "large-read", "read_file", "{}")
    raw = _result(content)
    store.complete_tool_item(item["id"], raw, model_result_json=raw)
    for n in range(8):
        recent = store.create_tool_item(
            old["id"], n + 2, 0, f"recent-{n}", "read_file", "{}"
        )
        store.complete_tool_item(recent["id"], _result("recent evidence"))
    store.fail_run(old["id"], "fixture")
    current, _ = store.create_run(
        session["id"], "Continue reviewing the existing evidence"
    )
    model = ScriptedModel([ModelResponse(text="done")])
    with patch.object(
        ContextCompactor,
        "compact",
        side_effect=AssertionError("clearing should suffice"),
    ):
        RuntimeEngine(store, model, lambda _: None).run(
            current["id"], threading.Event()
        )
    assert store.read_run(current["id"])["status"] == "succeeded"
    assert store.compaction_count(current["id"]) == 0
    snapshot = store.context_snapshot_repository().read_latest_for_run(current["id"])
    assert "cleared-tool-result:large-read" in snapshot.plan.omissions
    assert snapshot.model_context == model.contexts[0]
    assert "contextCleared" in str(snapshot.model_context)


def test_compaction_does_not_turn_assistant_plans_into_completed_work(history_store):
    store, session = history_store
    previous, _ = store.create_run(
        session["id"], "Do not modify code. Must preserve SQLite."
    )
    item = store.create_assistant_item(previous["id"], 1)
    store.append_item_content(item["id"], "I will fix every test. Next, run the suite.")
    store.complete_assistant_item_committed(item["id"])
    store.fail_run(previous["id"], "fixture")
    current, _ = store.create_run(session["id"], "只分析，等我确认。")
    summary = ContextCompactor(store).compact(current["id"], "pre_turn")
    assert summary.completed_actions == ()
    assert summary.important_decisions == ()
    assert "Do not modify code." in " ".join(summary.constraints)
    assert any(
        "Assistant report, unverified" in value and "I will fix" in value
        for value in summary.important_facts
    )
    assert summary.next_actions


def test_critical_constraints_are_not_discarded_to_make_summary_fit():
    summary = CompactSummary(
        task_goal="goal",
        constraints=tuple(f"必须保留用户约束 {n}" for n in range(40)),
        completed_actions=(),
        workspace_changes=(),
        important_facts=(),
        unresolved_problems=(),
        next_actions=(),
        source_item_ids=(),
    )
    with pytest.raises(ContextCompactionError, match="critical summary facts"):
        _fit_summary(summary, max_tokens=50)
    assert len(summary.constraints) == 40


def test_repeated_compaction_keeps_constraints_and_cumulative_source_lineage(
    history_store,
):
    store, session = history_store
    old, _ = store.create_run(session["id"], "必须保留用户文件。不要提交。")
    store.fail_run(old["id"], "fixture")
    run, _ = store.create_run(session["id"], "继续调查。")
    covered = set()
    for cycle in range(3):
        for n in range(40):
            item = store.create_tool_item(
                run["id"], cycle * 40 + n + 1, 0, f"read-{cycle}-{n}", "read_file", "{}"
            )
            store.complete_tool_item(item["id"], _result(f"evidence {cycle}-{n}"))
        summary = ContextCompactor(store).compact(
            run["id"], "mid_turn", keep_recent_items=16
        )
        assert covered <= set(summary.source_item_ids)
        covered = set(summary.source_item_ids)
        assert "必须保留用户文件。不要提交。" in str(
            ContextBuilder(store).build(run["id"]).model_context
        )
    assert store.compaction_count(run["id"]) == 3


def test_cancel_request_prevents_late_summary_commit(history_store):
    store, session = history_store
    run, _ = store.create_run(session["id"], "goal")
    for n in range(5):
        item = store.create_tool_item(run["id"], n, 0, f"read-{n}", "read_file", "{}")
        store.complete_tool_item(item["id"], _result("evidence"))
    before = store.pending_outbox_count()
    store.request_cancel_committed(run["id"])
    with pytest.raises(InvalidRunStateError):
        ContextCompactor(store).compact(run["id"], "mid_turn")
    assert store.compaction_count(run["id"]) == 0
    assert store.latest_compact_summary(run["id"]) is None
    assert store.pending_outbox_count() == before + 1


def test_token_estimator_uses_bundled_vocabulary_without_network(monkeypatch):
    import requests

    _encoding.cache_clear()
    monkeypatch.setattr(
        requests,
        "get",
        lambda *args, **kwargs: pytest.fail("vocabulary must be offline"),
    )
    english = "Read the source and preserve all user constraints.\n" * 100
    assert 100 < estimate_text_tokens(english) < len(english) // 2
    assert estimate_text_tokens("中文约束、代码与😀") > 0
    assert estimate_text_tokens("<|endoftext|>") > 0


def test_calibration_is_bound_to_completed_snapshot_and_survives_rebuild(history_store):
    store, session = history_store
    run, _ = store.create_run(session["id"], "List files and report")
    model = ScriptedModel(
        [
            ModelResponse(
                tool_calls=(ModelToolCall("list", "list_files", {}),),
                usage=ModelUsage(input_tokens=2_000),
            ),
            ModelResponse(text="done", usage=ModelUsage(input_tokens=3_000)),
        ]
    )
    RuntimeEngine(store, model, lambda _: None).run(run["id"], threading.Event())
    calibration = ContextSnapshotRepository(store.database).latest_token_calibration(
        run["id"]
    )
    latest = store.context_snapshot_repository().read_latest_for_run(run["id"])
    assert calibration[0].input_tokens == 3_000
    assert calibration[1] == latest.plan.token_budget.estimated_input_tokens
    first = ContextBuilder(store).build(run["id"])
    second = ContextBuilder(store).build(run["id"])
    assert first.budget.projected_input_tokens == second.budget.projected_input_tokens
    directory = store.database.data_directory
    store.close()
    restarted = SessionStore(directory)
    try:
        restarted.initialize()
        assert (
            ContextBuilder(restarted).build(run["id"]).budget.projected_input_tokens
            == first.budget.projected_input_tokens
        )
    finally:
        restarted.close()


def test_result_pages_reject_other_sessions_and_revocable_memory(history_store):
    store, session = history_store
    run, _ = store.create_run(session["id"], "read")
    item = store.create_tool_item(run["id"], 1, 0, "memory-call", "memory_read", "{}")
    store.complete_tool_item(item["id"], _result("private memory"))
    entry = read_tool_output_entry(store, run["id"])
    assert (
        entry.adapter.execute(
            {"callId": "memory-call", "itemId": item["id"], "stream": "result"},
            threading.Event(),
        )["code"]
        == "tool_output_not_available"
    )
    other = store.create_session(session["workspaceRoot"])
    other_run, _ = store.create_run(other["id"], "other")
    source = store.create_tool_item(
        other_run["id"], 1, 0, "other-call", "read_file", "{}"
    )
    store.complete_tool_item(source["id"], _result("other session"))
    assert (
        entry.adapter.execute(
            {"callId": "other-call", "itemId": source["id"], "stream": "result"},
            threading.Event(),
        )["code"]
        == "tool_output_not_available"
    )


def test_result_pages_reopen_and_keep_utf8_offsets(history_store):
    store, session = history_store
    run, _ = store.create_run(session["id"], "read")
    item = store.create_tool_item(run["id"], 1, 0, "read", "read_file", "{}")
    raw = _result("甲乙😀")
    store.complete_tool_item(item["id"], raw, model_result_json=raw)
    directory = store.database.data_directory
    store.close()
    restarted = SessionStore(directory)
    restarted.initialize()
    entry = read_tool_output_entry(restarted, run["id"])
    start = raw.encode().index("甲".encode())
    request = {
        "callId": "read",
        "itemId": item["id"],
        "stream": "result",
        "offsetBytes": start,
        "maxBytes": 4,
    }
    page = entry.adapter.execute(request, threading.Event())
    assert page["data"]["content"] == "甲"
    assert page["data"]["nextOffset"] == start + 3
    assert (
        entry.adapter.execute({**request, "offsetBytes": start + 1}, threading.Event())[
            "code"
        ]
        == "invalid_output_offset"
    )
    restarted.close()

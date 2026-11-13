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
from eidos_runtime.context.facts import CompactHandoff, CompactSummary, ContextFacts, ContextItemFact
from eidos_runtime.context.policy import context_thresholds
from eidos_runtime.context.tokens import _encoding, estimate_text_tokens
from eidos_runtime.context.tool_history import clear_old_tool_results, retained_content_ids
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
    cleared = clear_old_tool_results(context, facts, target_reduction=1, batch_bytes=1)
    assert len(cleared) == len(context)
    assert cleared[0] == context[0]
    marker = json.loads(cleared[1]["result"])
    assert marker["contextCleared"] is True
    # The envelope carries callId; the stub adds only what resolves it.
    assert cleared[1]["callId"] == "call-0"
    assert marker["itemId"] == "item-0"
    assert not {"readBack", "notice", "sideEffectsMayExist", "reconciliationRequired"} & marker.keys()
    assert "originalResultSha256" not in marker
    assert cleared[2:] == context[2:]
    assert clear_old_tool_results(context, facts, target_reduction=1, batch_bytes=1) == cleared
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
        original, facts.model_copy(update={"items": tuple(items)}), target_reduction=1,
        batch_bytes=1,
    )
    assert cleared[1] == original[1]


def test_recent_multi_tool_response_is_retained_as_one_group():
    context, facts = _tools()
    items = list(facts.items)
    items[0] = items[0].model_copy(
        update={"model_step_index": items[-1].model_step_index}
    )
    cleared = clear_old_tool_results(
        context, facts.model_copy(update={"items": tuple(items)}), target_reduction=1,
        batch_bytes=1,
    )
    assert cleared[1] == context[1]
    assert json.loads(cleared[3]["result"])["contextCleared"] is True


def _cleared_calls(context) -> set[str]:
    return {
        str(item["callId"]) for item in context
        if item["type"] == "tool_result" and "contextCleared" in item["result"]
    }


def test_clearing_releases_whole_batches_and_keeps_the_prefix_between_them():
    context, facts = _tools(16)
    result_bytes = len(facts.items[0].model_result_json.encode("utf-8"))
    batch = result_bytes * 3

    def clear(count: int):
        window = facts.model_copy(update={"items": facts.items[:count]})
        return clear_old_tool_results(
            context[:count * 2], window, target_reduction=10**9, batch_bytes=batch,
        )

    # Eight recent results stay protected. Batches hold three results each.
    assert _cleared_calls(clear(10)) == set()
    assert _cleared_calls(clear(11)) == {"call-0", "call-1", "call-2"}
    # New responses inside the next batch do not clear one more old result.
    assert clear(12)[:22] == clear(11)
    assert clear(13)[:22] == clear(11)
    assert _cleared_calls(clear(14)) == {f"call-{n}" for n in range(6)}


def test_clearing_finishes_the_batch_that_meets_the_target():
    context, facts = _tools()
    result_bytes = len(facts.items[0].model_result_json.encode("utf-8"))
    cleared = clear_old_tool_results(context, facts, target_reduction=1, batch_bytes=result_bytes * 2)
    assert _cleared_calls(cleared) == {"call-0", "call-1"}


def test_media_window_releases_half_the_budget_at_once():
    sizes = tuple((f"image-{n}", 30) for n in range(8))
    assert retained_content_ids(sizes[:3], 100) == {"image-0", "image-1", "image-2"}
    # The fourth image overflows the window; the oldest images leave together.
    assert retained_content_ids(sizes[:4], 100) == {"image-3"}
    assert retained_content_ids(sizes[:5], 100) == {"image-3", "image-4"}
    assert retained_content_ids(sizes[:6], 100) == {"image-3", "image-4", "image-5"}
    assert retained_content_ids(sizes[:7], 100) == {"image-6"}


def test_media_window_skips_oversized_content_and_keeps_the_newest_item():
    assert retained_content_ids((("old", 10), ("huge", 200), ("new", 95)), 100) == {"new"}
    assert retained_content_ids((("old", 60), ("new", 60)), 100) == {"new"}
    assert retained_content_ids((("one", 1),), 0) == set()


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
                "callId": marker_item["callId"], "itemId": marker["itemId"], "stream": "result",
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


def test_larger_window_keeps_old_output_that_a_smaller_window_clears(history_store):
    store, session = history_store
    run, _ = store.create_run(session["id"], "检查结果，先不要改代码。")
    raw = _result("\n".join(f"field_{n} = unresolved_{n}" for n in range(9_000)))
    old = store.create_tool_item(run["id"], 1, 0, "old-read", "read_file", "{}")
    store.complete_tool_item(old["id"], raw, model_result_json=raw)
    for n in range(8):
        item = store.create_tool_item(run["id"], n + 2, 0, f"recent-{n}", "read_file", "{}")
        store.complete_tool_item(item["id"], _result(f"recent {n}"))
    base = store.read_model_profile(run["id"])

    def old_read(window: int) -> tuple[str, int]:
        profile = base.model_copy(
            update={"context_window_tokens": window, "max_output_tokens": 8_192}
        )
        with patch.object(store, "read_model_profile", return_value=profile):
            built = ContextBuilder(store).build(run["id"])
        result = next(
            item["result"] for item in built.model_context
            if item.get("callId") == "old-read" and item["type"] == "tool_result"
        )
        return result, built.budget.projected_input_tokens

    # About 98,000 tokens. A 128K window clears above 65,536; a 256K window
    # has 248,709 usable tokens and clears above half of them (124,354).
    cleared, cleared_tokens = old_read(131_072)
    kept, kept_tokens = old_read(262_144)

    assert json.loads(cleared)["contextCleared"] is True
    assert cleared_tokens < 49_152
    assert kept == model_result_json(raw)
    assert 65_536 < kept_tokens < 124_354
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
    # The old read alone exceeds this window's 56,033 usable tokens and its
    # 42,024-token clearing trigger. A larger window would keep it in place.
    profile = store.read_model_profile(old["id"]).model_copy(
        update={"context_window_tokens": 65_536, "max_output_tokens": 8_192}
    )
    current, _ = store.create_run(
        session["id"], "Continue reviewing the existing evidence",
        model_profile=profile,
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


def _writes(custom: bool) -> tuple[tuple[dict[str, object], ...], ContextFacts]:
    patch_text = "*** Begin Patch\n*** Update File: big.py\n" + "+line\n" * 2_000 + "*** End Patch"
    arguments = patch_text if custom else json.dumps({"path": "big.py", "patch": patch_text})
    result = json.dumps({"outcome": "success", "code": "ok", "summary": "Patch applied",
                         "sideEffectsMayExist": False, "reconciliationRequired": False,
                         "data": {"paths": ["big.py"], "workspaceChanged": True}})
    facts = tuple(
        ContextItemFact(item_id=f"item-{n}", run_id="run", kind="tool_call", status="completed",
                        provider_call_id=f"call-{n}", tool_name="apply_patch", model_step_index=n,
                        payload_kind="custom" if custom else "function",
                        arguments_json=json.dumps({"input": arguments}) if custom else arguments,
                        model_result_json=result)
        for n in range(12)
    )
    field = "input" if custom else "arguments"
    context = tuple(value for item in facts for value in (
        {"type": "tool_call", "callId": item.provider_call_id, "name": "apply_patch", field: arguments,
         **({"payloadKind": "custom"} if custom else {})},
        {"type": "tool_result", "callId": item.provider_call_id, "name": "apply_patch", "result": result,
         **({"payloadKind": "custom"} if custom else {})},
    ))
    return context, ContextFacts(run_id="run", session_id="session", items=facts)


@pytest.mark.parametrize("custom", [False, True])
def test_old_successful_write_arguments_clear_and_recent_ones_stay(custom):
    context, facts = _writes(custom)
    cleared = clear_old_tool_results(context, facts, target_reduction=1, batch_bytes=1)
    field = "input" if custom else "arguments"
    stub = cleared[0][field]
    assert "argumentsCleared" in stub and "item-0" in stub
    if not custom:
        assert json.loads(stub)["path"] == "big.py"
    # Results stay paired with their call; small write results keep their bodies.
    assert cleared[1] == context[1]
    assert cleared[-2] == context[-2]


def test_write_arguments_with_unresolved_effects_stay():
    context, facts = _writes(False)
    unknown = json.dumps({"outcome": "success", "data": {"workspaceChangeState": "unknown"}})
    items = (facts.items[0].model_copy(update={"model_result_json": unknown}), *facts.items[1:])
    cleared = clear_old_tool_results(
        context, facts.model_copy(update={"items": items}), target_reduction=1, batch_bytes=1,
    )
    assert cleared[0] == context[0]


def test_cleared_write_arguments_page_back_from_the_saved_call(history_store):
    store, session = history_store
    run, _ = store.create_run(session["id"], "write")
    arguments = json.dumps({"path": "big.py", "patch": "+line\n" * 2_000})
    item = store.create_tool_item(run["id"], 1, 0, "write-call", "apply_patch", arguments)
    store.complete_tool_item(item["id"], json.dumps({"outcome": "success", "data": {"paths": ["big.py"]}}))
    memory = store.create_tool_item(run["id"], 2, 0, "memory-call", "memory_edit", '{"body":"private"}')
    store.complete_tool_item(memory["id"], json.dumps({"outcome": "success", "data": {}}))
    entry = read_tool_output_entry(store, run["id"])

    parts, offset = [], 0
    while True:
        page = entry.adapter.execute({"callId": "write-call", "itemId": item["id"], "stream": "arguments",
                                      "offsetBytes": offset}, threading.Event())["data"]
        parts.append(page["content"])
        offset = page["nextOffset"]
        if not page["hasMoreAfter"]:
            break
    assert "".join(parts) == arguments
    assert entry.adapter.execute({"callId": "memory-call", "stream": "arguments"},
                                 threading.Event())["code"] == "tool_output_not_available"


def test_clearing_batches_grow_with_the_window():
    small = context_thresholds(27_648)
    mid = context_thresholds(164_000)
    large = context_thresholds(1_032_192)
    assert small.clearing_batch_bytes == 64 * 1024
    assert mid.clearing_batch_bytes == (mid.clearing_trigger_tokens - mid.clearing_target_tokens) * 4
    assert large.clearing_batch_bytes == 262_144
    assert small.clearing_batch_bytes <= mid.clearing_batch_bytes <= large.clearing_batch_bytes


def test_summary_drops_its_handoff_before_failing_on_budget():
    handoff = CompactHandoff(current_work="w" * 600, progress=("p" * 300,) * 6, source_item_ids=("item",))
    summary = CompactSummary(
        task_goal="goal", constraints=("必须保留用户约束",), completed_actions=(), workspace_changes=(),
        important_facts=(), unresolved_problems=(), next_actions=(), source_item_ids=(), handoff=handoff,
    )
    assert _fit_summary(summary, max_tokens=4_096).handoff == handoff
    without = estimate_text_tokens(summary.model_copy(update={"handoff": None}).model_dump_json(exclude={"source_item_ids"}))
    fitted = _fit_summary(summary, max_tokens=without + 8)
    assert fitted.handoff is None and fitted.constraints == summary.constraints
    with pytest.raises(ContextCompactionError, match="critical summary facts"):
        _fit_summary(summary, max_tokens=without - 8)

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from eidos_runtime.context.compactor import ContextCompactor
from eidos_runtime.context.builder import ContextBuilder
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.persistence import history_search
from eidos_runtime.tools.contracts import result_model
from eidos_runtime.tools.read_tool_output import read_tool_output_entry
from eidos_runtime.tools.search_history import SearchHistoryResultData, search_history_entry


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


def _tool(store, run_id, step, name, data, *, arguments="{}", call=None):
    item = store.create_tool_item(run_id, step, 0, call or f"call-{step}", name, arguments)
    raw = json.dumps({"outcome": "success", "code": "ok", "summary": "done", "data": data}, ensure_ascii=False)
    store.complete_tool_item(item["id"], raw, model_result_json=raw)
    return item


def _search(store, run_id, query, **extra):
    result = search_history_entry(store.database, run_id).adapter.execute({"query": query, **extra}, threading.Event())
    # Runtime validates tool results from JSON, as canonical_tool_result does.
    result_model(SearchHistoryResultData).model_validate_json(json.dumps(result))
    return result["data"]


def test_compacted_history_is_found_and_paged_back_without_replay(history):
    store, session = history
    previous, user = store.create_run(session["id"], "排查缓存过期，先别改代码。")
    reply = store.create_assistant_item(previous["id"], 1)
    store.append_item_content(reply["id"], "The TTL lives in settings.py.")
    store.complete_assistant_item_committed(reply["id"])
    read = _tool(store, previous["id"], 2, "read_file",
                 {"path": "settings.py", "content": "CACHE_TTL_SECONDS = 300\n# 缓存过期时间"})
    shell = _tool(store, previous["id"], 3, "run_shell", {"exitCode": 1, "stdout": "", "stderr": "boom"},
                  arguments=json.dumps({"command": "pytest tests/test_cache.py"}))
    store.fail_run(previous["id"], "fixture")
    run, _ = store.create_run(session["id"], "继续。")
    summary = ContextCompactor(store).compact(run["id"], "pre_turn")
    assert read["id"] in summary.source_item_ids
    assert "CACHE_TTL_SECONDS" not in json.dumps(ContextBuilder(store).build(run["id"]).model_context)

    found = _search(store, run["id"], "cache_ttl_seconds")
    assert [(m["itemId"], m["kind"], m["toolName"], m["stream"]) for m in found["matches"]] == [
        (read["id"], "tool", "read_file", "result")]
    assert "CACHE_TTL_SECONDS = 300" in found["matches"][0]["snippet"]
    assert found["complete"] is True
    page = read_tool_output_entry(store, run["id"]).adapter.execute({
        "callId": found["matches"][0]["readCallId"], "itemId": read["id"], "stream": "result",
    }, threading.Event())
    assert "CACHE_TTL_SECONDS = 300" in page["data"]["content"]

    assert [m["itemId"] for m in _search(store, run["id"], "test_cache.py")["matches"]] == [shell["id"]]
    assert {m["itemId"] for m in _search(store, run["id"], "缓存过期")["matches"]} == {user["id"], read["id"]}
    message = _search(store, run["id"], "settings.py")["matches"]
    assert [m["itemId"] for m in message] == [read["id"], reply["id"]]
    assert message[1]["kind"] == "assistant" and message[1]["stream"] == "context"
    page = read_tool_output_entry(store, run["id"]).adapter.execute({
        "callId": message[1]["readCallId"], "stream": "context",
    }, threading.Event())
    assert page["data"]["content"] == "The TTL lives in settings.py."


def test_memory_other_sessions_and_repeated_output_are_not_searched(history):
    store, session = history
    run, _ = store.create_run(session["id"], "read")
    _tool(store, run["id"], 1, "memory_read", {"body": "private token-ish note"})
    _tool(store, run["id"], 2, "memory_edit", {}, arguments=json.dumps({"body": "private token-ish note"}))
    _tool(store, run["id"], 3, "read_tool_output", {"content": "private token-ish note"})
    _tool(store, run["id"], 4, "search_history", {"matches": [{"snippet": "private token-ish note"}]})
    other = store.create_session(session["workspaceRoot"])
    other_run, _ = store.create_run(other["id"], "other")
    _tool(store, other_run["id"], 1, "read_file", {"content": "private token-ish note"})

    assert _search(store, run["id"], "token-ish")["matches"] == []


def test_limit_and_scan_bound_report_an_incomplete_answer(history, monkeypatch):
    store, session = history
    run, _ = store.create_run(session["id"], "read")
    items = [_tool(store, run["id"], step, "read_file", {"content": f"needle {step}"}) for step in range(1, 6)]

    found = _search(store, run["id"], "needle", limit=2)
    assert [m["itemId"] for m in found["matches"]] == [items[4]["id"], items[3]["id"]]
    assert found["complete"] is False

    monkeypatch.setattr(history_search, "TOOL_SCAN_CALLS", 3)
    found = _search(store, run["id"], "needle 1")
    assert found["matches"] == []
    assert found["complete"] is False


def test_invalid_query_is_rejected_without_reading_history(history):
    store, session = history
    run, _ = store.create_run(session["id"], "read")
    result = search_history_entry(store.database, run["id"]).adapter.execute({"query": "x"}, threading.Event())
    assert result["outcome"] == "error" and result["code"] == "invalid_arguments"

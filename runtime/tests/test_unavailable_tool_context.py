from __future__ import annotations

import json

import pytest

from eidos_runtime.context.builder import ContextBuilder, unavailable_history_tools
from eidos_runtime.db.storage import SessionStore


@pytest.fixture
def store(tmp_path):
    value = SessionStore(tmp_path / "data")
    value.initialize()
    yield value
    value.close()


def _history_with_browser_call(store: SessionStore, workspace) -> str:
    session = store.create_session(str(workspace))
    run, _ = store.create_run(session["id"], "Open the browser")
    item = store.create_tool_item(
        run["id"], 1, 0, "call-1", "mcp__browser__browser_navigate", '{"url":"https://example.com"}'
    )
    result = {
        "schemaVersion": 1, "toolContractVersion": 1, "toolName": "mcp__browser__browser_navigate",
        "outcome": "success", "code": "ok", "summary": "Opened", "data": {},
        "sideEffectsMayExist": False, "reconciliationRequired": False,
    }
    store.complete_tool_item(item["id"], json.dumps(result), model_result_json=json.dumps(result))
    return str(run["id"])


def _section(built) -> list[str]:
    return [
        str(item["content"]) for item in built.model_context
        if item.get("sectionId") == "unavailable-tools"
    ]


def test_history_tools_missing_from_this_step_are_named_once(store, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    run_id = _history_with_browser_call(store, workspace)

    built = ContextBuilder(store).build(run_id, callable_tool_names=frozenset({"read_file"}))

    sections = _section(built)
    assert len(sections) == 1
    assert '["mcp__browser__browser_navigate"]' in sections[0]
    assert "installed or enabled" in sections[0]
    # The historical call itself stays in the projected history.
    assert any(
        item.get("type") == "tool_call" and item.get("name") == "mcp__browser__browser_navigate"
        for item in built.model_context
    )


def test_callable_or_unknown_tool_sets_add_no_section(store, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    run_id = _history_with_browser_call(store, workspace)

    callable_now = ContextBuilder(store).build(
        run_id, callable_tool_names=frozenset({"mcp__browser__browser_navigate"}),
    )
    unknown = ContextBuilder(store).build(run_id)

    assert _section(callable_now) == []
    assert _section(unknown) == []


def test_unavailable_history_tools_are_bounded_and_sorted():
    context = [
        {"type": "tool_call", "name": f"tool_{index:02d}"} for index in range(30, 0, -1)
    ] + [
        {"type": "tool_call", "name": "x" * 129},
        {"type": "tool_call", "name": "kept"},
        {"type": "tool_result", "name": "result_only"},
    ]

    names = unavailable_history_tools(context, frozenset({"kept"}))

    assert names == [f"tool_{index:02d}" for index in range(1, 21)]

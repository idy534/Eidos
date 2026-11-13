from __future__ import annotations

import json

from eidos_runtime.context.facts import ContextFacts, ContextItemFact
from eidos_runtime.context.tool_history import (
    BROWSER_PREVIEW_CHARS,
    clear_old_tool_results,
    omit_old_browser_images,
)


def _mcp_result(text: str) -> str:
    return json.dumps({
        "toolContractVersion": 1, "schemaVersion": 1, "toolName": "mcp", "outcome": "success", "code": "ok",
        "summary": "MCP tool completed", "sideEffectsMayExist": True, "reconciliationRequired": False,
        "data": {"text": text, "isError": False},
    })


def _history(names: list[str]) -> tuple[tuple[dict[str, object], ...], ContextFacts]:
    facts = tuple(
        ContextItemFact(
            item_id=f"item-{n}", run_id="run", kind="tool_call", status="completed",
            provider_call_id=f"call-{n}", tool_name=name, model_step_index=n,
            model_result_json=_mcp_result(f"### Page state\n- Page URL: https://example.com/{n}\n" + "- row\n" * 2_000),
        )
        for n, name in enumerate(names)
    )
    context = tuple(
        value for item in facts for value in (
            {"type": "tool_call", "callId": item.provider_call_id, "name": item.tool_name, "arguments": "{}",
             "responseId": f"response-{item.model_step_index}"},
            {"type": "tool_result", "callId": item.provider_call_id, "name": item.tool_name, "result": item.model_result_json},
        )
    )
    return context, ContextFacts(run_id="run", session_id="session", items=facts)


def test_old_browser_pages_clear_to_a_preview_with_read_back():
    names = ["mcp__browser__browser_snapshot"] * 12
    context, facts = _history(names)
    cleared = clear_old_tool_results(context, facts, target_reduction=1, batch_bytes=1)
    marker = json.loads(cleared[1]["result"])
    assert marker["contextCleared"] is True
    assert cleared[1]["callId"] == "call-0" and marker["itemId"] == "item-0"
    assert marker["data"]["preview"].startswith("### Page state\n- Page URL: https://example.com/0")
    assert len(marker["data"]["preview"]) == BROWSER_PREVIEW_CHARS
    assert cleared[-1] == context[-1]


def test_other_mcp_results_clear_without_a_browser_preview():
    # Every saved MCP result pages back through read_tool_output(stream='result').
    context, facts = _history(["mcp__github__get_issue"] * 12)
    cleared = clear_old_tool_results(context, facts, target_reduction=10**9, batch_bytes=1)
    marker = json.loads(cleared[1]["result"])
    assert marker["contextCleared"] is True and "preview" not in marker["data"]
    assert cleared[-1] == context[-1]


def test_only_the_newest_browser_screenshots_stay_in_the_request():
    image = {"schemaVersion": 1, "items": [{"kind": "binary", "mime": "image/png", "data": "AA=="}]}
    context = tuple(
        {"type": "tool_result", "callId": f"call-{n}", "name": "mcp__browser__browser_take_screenshot",
         "result": "{}", "toolContent": image}
        for n in range(4)
    ) + ({"type": "tool_result", "callId": "view", "name": "view_image", "result": "{}", "toolContent": image},)
    projected = omit_old_browser_images(context)
    assert [item.get("toolContent") is not None for item in projected] == [False, False, True, True, True]
    assert projected[0]["toolContentOmitted"] is True
    assert projected[4] == context[4]
    assert omit_old_browser_images(projected) == projected

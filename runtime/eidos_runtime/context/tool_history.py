"""Clear old successful tool bodies in the model projection, never in SQLite."""

from __future__ import annotations

import json

from eidos_runtime.context.facts import ContextFacts
from eidos_runtime.context.tokens import estimate_text_tokens
from eidos_runtime.domain.tool_output import RECOVERABLE_RESULT_TOOLS
from eidos_runtime.model.client import ModelContextItem
from eidos_runtime.context.working_set import recent_item_ids


KEEP_RECENT_TOOL_RESULTS = 8
MIN_RESULT_BYTES = 4 * 1024
# Browser pages are re-read with a fresh snapshot, so old page bodies and
# screenshots are the first context to give back.
BROWSER_TOOL_PREFIX = "mcp__browser__browser_"
KEEP_BROWSER_IMAGES = 2
BROWSER_PREVIEW_CHARS = 512


def is_browser_tool(name: object) -> bool:
    return isinstance(name, str) and name.startswith(BROWSER_TOOL_PREFIX)


def omit_old_browser_images(
    context: tuple[ModelContextItem, ...], *, keep: int = KEEP_BROWSER_IMAGES,
) -> tuple[ModelContextItem, ...]:
    """Keep only the newest browser screenshots in the model request."""

    result = list(context)
    seen = 0
    for index in range(len(result) - 1, -1, -1):
        projected = result[index]
        if projected.get("type") != "tool_result" or not is_browser_tool(projected.get("name")):
            continue
        if projected.get("toolContent") is None:
            continue
        seen += 1
        if seen > keep:
            omitted = {key: value for key, value in projected.items() if key != "toolContent"}
            result[index] = {**omitted, "toolContentOmitted": True}
    return tuple(result)


def clear_old_tool_results(
    context: tuple[ModelContextItem, ...],
    facts: ContextFacts,
    *,
    target_reduction: int,
    recent_token_budget: int | None = None,
) -> tuple[ModelContextItem, ...]:
    if target_reduction <= 0:
        return context
    tools = [item for item in facts.items if item.provider_call_id is not None]
    protected = (recent_item_ids(tuple(tools), recent_token_budget) if recent_token_budget is not None
                 else {item.item_id for item in tools[-KEEP_RECENT_TOOL_RESULTS:]})
    # Preserve an entire assistant response and all its tool returns. This also
    # protects signed/provider reasoning attached to a multi-tool response.
    groups = {
        (item.run_id, item.model_step_index)
        for item in tools
        if item.item_id in protected and item.model_step_index is not None
    }
    protected.update(
        item.item_id
        for item in tools
        if (
            item.model_step_index is not None
            and (item.run_id, item.model_step_index) in groups
        )
    )
    candidates = {
        item.provider_call_id: item for item in tools if item.item_id not in protected
    }
    # Provider call IDs may be reused across Runs. Never create an ambiguous
    # reference, even if a request encoder accepts the duplicate history.
    call_counts: dict[str, int] = {}
    for item in tools:
        call_counts[item.provider_call_id] = (
            call_counts.get(item.provider_call_id, 0) + 1
        )
    result = list(context)
    saved = 0
    for index, projected in enumerate(context):
        if projected.get("type") != "tool_result":
            continue
        call_id = str(projected.get("callId", ""))
        item = candidates.get(call_id)
        raw = projected.get("result")
        if (
            item is None
            or call_counts[call_id] != 1
            or item.status != "completed"
            or (item.tool_name not in RECOVERABLE_RESULT_TOOLS and not is_browser_tool(item.tool_name))
            or not isinstance(raw, str)
            or len(raw.encode("utf-8")) < MIN_RESULT_BYTES
            or projected.get("toolContent") is not None
        ):
            continue
        payload = json.loads(raw)
        if (
            not isinstance(payload, dict)
            or payload.get("outcome") != "success"
            or payload.get("reconciliationRequired") is True
            or (
                payload.get("reconciliationRequired") is not False
                and payload.get("sideEffectsMayExist") is True
            )
        ):
            continue
        data = payload.get("data")
        if (
            not isinstance(data, dict)
            or data.get("executionStatus") == "running"
            or data.get("outputComplete") is False
            or data.get("reconciliationRequired") is True
            or data.get("workspaceChangeState") == "unknown"
        ):
            continue
        metadata = {
            key: data[key]
            for key in (
                "path",
                "sizeBytes",
                "startLine",
                "endLine",
                "totalLines",
                "exitCode",
                "termination",
                "executionStatus",
                "workspaceChanged",
                "workspaceChangeState",
                "truncated",
                "omittedBytes",
                "outputComplete",
            )
            if key in data
        }
        if is_browser_tool(item.tool_name) and isinstance(data.get("text"), str):
            metadata["preview"] = data["text"][:BROWSER_PREVIEW_CHARS]
        cleared = json.dumps(
            {
                **{
                    key: payload[key]
                    for key in (
                        "outcome",
                        "code",
                        "summary",
                        "sideEffectsMayExist",
                        "reconciliationRequired",
                    )
                    if key in payload
                },
                "data": metadata,
                "contextCleared": True,
                "originalResultBytes": len(raw.encode("utf-8")),
                "readBack": {
                    "tool": "read_tool_output",
                    "callId": call_id,
                    "itemId": item.item_id,
                    "stream": "result",
                },
                "notice": "Older tool body omitted from active context. Read the saved result when needed; do not repeat an operation to recover its output. Historical file content does not prove the current file version.",
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        reduction = estimate_text_tokens(raw) - estimate_text_tokens(cleared)
        if reduction <= 0:
            continue
        result[index] = {**projected, "result": cleared}
        saved += reduction
        if saved >= target_reduction:
            break
    return tuple(result)

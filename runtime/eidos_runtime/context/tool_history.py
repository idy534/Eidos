"""Clear old successful tool bodies in the model projection, never in SQLite."""

from __future__ import annotations

import json

from eidos_runtime.context.facts import ContextFacts, ContextItemFact
from eidos_runtime.context.tokens import estimate_text_tokens
from eidos_runtime.domain.tool_output import RECOVERABLE_RESULT_TOOLS
from eidos_runtime.model.client import ModelContextItem
from eidos_runtime.context.working_set import recent_item_ids


KEEP_RECENT_TOOL_RESULTS = 8
MIN_RESULT_BYTES = 4 * 1024
# Old tool bodies are released in whole batches of raw history. A Provider
# prompt cache only reuses an unchanged prefix, so clearing one more result on
# every request would invalidate everything after it each time.
CLEARING_BATCH_BYTES = 64 * 1024
# Browser pages are re-read with a fresh snapshot, so old page bodies and
# screenshots are the first context to give back.
BROWSER_TOOL_PREFIX = "mcp__browser__browser_"
KEEP_BROWSER_IMAGES = 2
BROWSER_PREVIEW_CHARS = 512
MCP_TOOL_PREFIX = "mcp__"
_CLEARABLE_RESULT_TOOLS = frozenset({
    *RECOVERABLE_RESULT_TOOLS, "write_stdin", "control_shell", "apply_patch",
    "web_search", "read_tool_output", "search_history",
})
_KEPT_RESULT_TOOLS = frozenset({"skill_read", "skill_read_resource", "read_agent_message", "view_image"})
# Write calls whose old arguments may leave the request once they succeeded.
WRITE_ARGUMENT_TOOLS = frozenset({"apply_patch", "write_file"})
_RESULT_METADATA_KEYS = (
    "path", "sizeBytes", "startLine", "endLine", "totalLines", "exitCode", "termination",
    "executionStatus", "workspaceChanged", "workspaceChangeState", "truncated", "omittedBytes",
    "outputComplete",
)


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


def retained_content_ids(sizes: tuple[tuple[str, int], ...], budget: int) -> set[str]:
    """Select saved media for one request with a stable eviction boundary.

    ``sizes`` lists item IDs and stored sizes from oldest to newest. Content
    larger than the whole budget is never sent. When the retained window
    overflows, the oldest content is released until the window uses at most
    half of the budget (the newest item always stays). Later media then extends
    the same request prefix until the next overflow, instead of displacing one
    old image on every request.
    """

    low_water = budget // 2
    window: list[tuple[str, int]] = []
    start = 0
    used = 0
    for item_id, size in sizes:
        if size > budget:
            continue
        window.append((item_id, size))
        used += size
        if used > budget:
            while start < len(window) - 1 and used > low_water:
                used -= window[start][1]
                start += 1
    return {item_id for item_id, _ in window[start:]}


def is_clearable_result_tool(name: object) -> bool:
    """Whether an old successful result may leave the request for a stub.

    Every such result is paged back with read_tool_output(stream='result'), so
    clearing never needs an operation to run again. Memory results stay, since
    their revocation applies to history; Skill text, agent messages and images
    carry activation, delivery or media state of their own.
    """
    if not isinstance(name, str) or name.startswith("memory_") or name in _KEPT_RESULT_TOOLS:
        return False
    return name in _CLEARABLE_RESULT_TOOLS or name.startswith(MCP_TOOL_PREFIX)


def clear_old_tool_results(
    context: tuple[ModelContextItem, ...],
    facts: ContextFacts,
    *,
    target_reduction: int,
    recent_token_budget: int | None = None,
    batch_bytes: int = CLEARING_BATCH_BYTES,
) -> tuple[ModelContextItem, ...]:
    if target_reduction <= 0:
        return context
    if batch_bytes <= 0:
        raise ValueError("clearing batch must be positive")
    tools = [item for item in facts.items if item.provider_call_id is not None]
    recent = (recent_item_ids(tuple(tools), recent_token_budget) if recent_token_budget is not None
              else {item.item_id for item in tools[-KEEP_RECENT_TOOL_RESULTS:]})
    protected = set(recent)
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
    # Batch numbers come from the raw size of earlier tool facts, which never
    # changes, so a fact keeps its batch while new responses arrive. Only whole
    # batches older than the recent window are eligible: the cleared boundary
    # moves one batch at a time instead of on every request.
    batches: dict[str, int] = {}
    position = 0
    for item in tools:
        batches[item.item_id] = position // batch_bytes
        position += len((item.arguments_json or "").encode("utf-8"))
        position += len((item.model_result_json or item.result_json or "").encode("utf-8"))
    first_recent_batch = min(
        (batches[item.item_id] for item in tools if item.item_id in recent), default=None,
    )
    candidates = {
        item.provider_call_id: item
        for item in tools
        if item.item_id not in protected
        and (first_recent_batch is None or batches[item.item_id] < first_recent_batch)
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
    # Once the target is met, finish the current batch so the next request
    # does not clear the rest of it one result at a time.
    final_batch: int | None = None
    for index, projected in enumerate(context):
        kind = projected.get("type")
        if kind not in {"tool_call", "tool_result"}:
            continue
        call_id = str(projected.get("callId", ""))
        item = candidates.get(call_id)
        if item is not None and final_batch is not None and batches[item.item_id] > final_batch:
            break
        if item is None or call_counts[call_id] != 1 or item.status != "completed":
            continue
        replacement = (_cleared_arguments(projected, item) if kind == "tool_call"
                       else _cleared_result(projected, item))
        if replacement is None:
            continue
        field, before, after = replacement
        reduction = estimate_text_tokens(before) - estimate_text_tokens(after)
        if reduction <= 0:
            continue
        result[index] = {**projected, field: after}
        saved += reduction
        if saved >= target_reduction and final_batch is None:
            final_batch = batches[item.item_id]
    return tuple(result)


def _settled_success(payload: object) -> bool:
    """A success without an unresolved effect; only these leave the request."""
    if (
        not isinstance(payload, dict)
        or payload.get("outcome") != "success"
        or payload.get("reconciliationRequired") is True
        or (
            payload.get("reconciliationRequired") is not False
            and payload.get("sideEffectsMayExist") is True
        )
    ):
        return False
    data = payload.get("data")
    return (
        isinstance(data, dict)
        and data.get("executionStatus") != "running"
        and data.get("outputComplete") is not False
        and data.get("reconciliationRequired") is not True
        and data.get("workspaceChangeState") != "unknown"
    )


def _cleared_result(
    projected: ModelContextItem, item: ContextItemFact,
) -> tuple[str, str, str] | None:
    raw = projected.get("result")
    if (
        not is_clearable_result_tool(item.tool_name)
        or not isinstance(raw, str)
        or len(raw.encode("utf-8")) < MIN_RESULT_BYTES
        or projected.get("toolContent") is not None
    ):
        return None
    payload = json.loads(raw)
    if not _settled_success(payload):
        return None
    data = payload["data"]
    metadata = {key: data[key] for key in _RESULT_METADATA_KEYS if key in data and data[key] is not None}
    if is_browser_tool(item.tool_name) and isinstance(data.get("text"), str):
        metadata["preview"] = data["text"][:BROWSER_PREVIEW_CHARS]
    # The envelope already carries callId; itemId resolves a callId reused by
    # another Run. read_tool_output's description explains the marker once,
    # instead of every stub repeating it.
    cleared = json.dumps(
        {
            **{key: payload[key] for key in ("outcome", "code", "summary") if key in payload},
            **{key: True for key in ("sideEffectsMayExist", "reconciliationRequired") if payload.get(key) is True},
            "data": metadata,
            "contextCleared": True,
            "itemId": item.item_id,
            "originalResultBytes": len(raw.encode("utf-8")),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return "result", raw, cleared


def _cleared_arguments(
    projected: ModelContextItem, item: ContextItemFact,
) -> tuple[str, str, str] | None:
    """Old write calls carry whole files or patches; the workspace holds the result."""
    if item.tool_name not in WRITE_ARGUMENT_TOOLS:
        return None
    try:
        payload = json.loads(item.model_result_json or item.result_json or "{}")
    except json.JSONDecodeError:
        return None
    if not _settled_success(payload):
        return None
    custom = projected.get("payloadKind") == "custom"
    field = "input" if custom else "arguments"
    raw = projected.get(field)
    if not isinstance(raw, str) or len(raw.encode("utf-8")) < MIN_RESULT_BYTES:
        return None
    size = len(raw.encode("utf-8"))
    if custom:
        return field, raw, (
            f"[argumentsCleared: {size} bytes of input omitted from active context; itemId={item.item_id}]"
        )
    try:
        arguments = json.loads(raw)
    except json.JSONDecodeError:
        arguments = None
    path = arguments.get("path") if isinstance(arguments, dict) else None
    return field, raw, json.dumps(
        {
            "argumentsCleared": True,
            **({"path": path} if isinstance(path, str) else {}),
            "itemId": item.item_id,
            "originalArgumentsBytes": size,
        },
        ensure_ascii=False, separators=(",", ":"), sort_keys=True,
    )

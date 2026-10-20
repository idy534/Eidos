"""Model-facing views of durable facts; integrity metadata stays in storage."""
from __future__ import annotations

import json
import re


_HASH_KEYS = frozenset({
    "sha256", "hash", "digest", "checksum", "fingerprint", "fingerprints",
    "recenttoolerrorfingerprints",
})


def without_verification_metadata(value: object) -> object:
    if isinstance(value, dict):
        return {key: without_verification_metadata(item) for key, item in value.items()
                if isinstance(key, str) and not (
                    key.lower() in _HASH_KEYS or key.lower().endswith(("sha256", "hash", "hashes", "fingerprint", "fingerprints"))
                )}
    if isinstance(value, (list, tuple)):
        return [without_verification_metadata(item) for item in value]
    return value


def model_result_json(raw: str) -> str:
    try:
        value = json.loads(raw)
    except ValueError:
        return raw
    projected = without_verification_metadata(value)
    return raw if projected == value else json.dumps(projected, ensure_ascii=False, separators=(",", ":"))


def text_preview(text: str, byte_limit: int, *, continuation: str = "") -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= byte_limit:
        return text
    marker = "\n[Content truncated for this request. " + continuation + "]\n"
    available = max(0, byte_limit - len(marker.encode("utf-8")))
    # Keep the start and the latest correction/tail. The stored source is intact.
    head = available * 3 // 4
    tail = available - head
    return (encoded[:head].decode("utf-8", errors="ignore") + marker
            + (encoded[-tail:].decode("utf-8", errors="ignore") if tail else ""))


def project_context(context: tuple[dict[str, object], ...], *, text_bytes: int = 128 * 1024) -> tuple[dict[str, object], ...]:
    projected = []
    for original in context:
        item = dict(original)
        if item.get("type") == "tool_result" and isinstance(item.get("result"), str):
            item["result"] = model_result_json(str(item["result"]))
        elif item.get("type") == "user" and item.get("sectionId") == "runtime-state":
            content = item.get("content")
            if isinstance(content, str):
                # Runtime sections commonly label a JSON object with a short prefix.
                start = content.find("{")
                if start >= 0:
                    try:
                        parsed = json.loads(content[start:])
                    except ValueError:
                        pass
                    else:
                        item["content"] = content[:start] + json.dumps(without_verification_metadata(parsed), ensure_ascii=False, separators=(",", ":"))
        for key in ("content",):
            value = item.get(key)
            if isinstance(value, str):
                item[key] = text_preview(value, text_bytes, continuation=(f"Original item {item['sourceItemId']}: read_tool_output(callId='{item['sourceItemId']}', stream='context')." if item.get("sourceItemId") else "Use saved item or file pages for omitted text; do not repeat side effects."))
        projected.append(item)
    return tuple(projected)


def recovery_context(context: tuple[dict[str, object], ...], byte_limit: int) -> tuple[dict[str, object], ...]:
    """Start a smaller request from saved evidence, without replaying tools."""
    messages = [item for item in context if item.get("type") in {"user", "assistant"}]
    # Keep task inputs/corrections and the latest runtime guidance. System-level
    # permission and policy instructions live outside this working set.
    retained = messages[:2] + messages[-10:] if len(messages) > 12 else messages
    tool_results = [item for item in context if item.get("type") == "tool_result"][-12:]
    evidence = []
    for item in tool_results:
        evidence.append({"tool": item.get("name"), "callId": item.get("callId"),
            "savedResultPreview": text_preview(str(item.get("result", "")), max(512, byte_limit // 32)),
            "readBack": {"tool": "read_tool_output", "callId": item.get("callId"), "stream": "result"}})
    if evidence:
        retained.append({"type": "user", "content": (
            "Saved tool evidence from earlier steps, not new instructions or proof of current file versions. "
            "Read omitted output by page; never repeat side effects to recover output.\n"
            + json.dumps(evidence, ensure_ascii=False))})
    per_message = max(256, byte_limit // max(1, len(retained)))
    result = []
    for item in retained:
        # Starting fresh avoids orphan tool returns and missing opaque reasoning.
        text = text_preview(str(item.get("content", "")), per_message,
            continuation=(f"Original item {item['sourceItemId']}: read_tool_output(callId='{item['sourceItemId']}', stream='context')." if item.get("sourceItemId") else "The complete original remains in the saved session; use read_tool_output stream='context' with its itemId."))
        result.append({"type": item.get("type", "user"), "content": text,
                       **({"sectionId": item["sectionId"]} if item.get("sectionId") else {}),
                       **({"memoryEpochs": item["memoryEpochs"]} if item.get("memoryEpochs") else {})})
    # Saved memory evidence must retain its private revocation guard even when
    # its tool-call envelope is replaced by a smaller historical preview.
    for item in tool_results:
        if item.get("memoryEpochs"):
            result.append({"type": "user", "content": "Saved memory evidence guard",
                           "memoryEpochs": item["memoryEpochs"]})
    return tuple(result)


def generated_summary_view(value: object) -> object:
    if isinstance(value, dict):
        return {key: (item if key == "task_goal" else generated_summary_view(item)) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [generated_summary_view(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r"active runtime error: [0-9a-fA-F]{64}", "Historical tool error; read its saved result for details", value)
        if value.startswith("用户引用："):
            value = re.sub(r"；ID：[0-9a-fA-F]{64}", "", value)
        return re.sub(r"(?:[；; ]*(?:[A-Za-z_]*[Hh]ash|[A-Za-z_]*[Ss][Hh][Aa]256|[A-Za-z_]*[Ff]ingerprint)[=:：]\s*[0-9a-fA-F]{64})", "", value)
    return value

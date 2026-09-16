"""Bounded display projections; complete text remains in SQLite."""
from __future__ import annotations

import hashlib
import json

from eidos_runtime.file_limits import INLINE_TOOL_TEXT_BYTES
from eidos_runtime.sandbox.sensitive import SensitiveScanError, default_scanner


_DISPLAY_ARGUMENT_FIELDS = {
    "run_shell": ("command", "cwd"),
    "write_stdin": ("chars",),
    "search_text": ("query",),
}


def project_tool_text(item: dict[str, object]) -> dict[str, object]:
    call = item.get("toolCall")
    if not isinstance(call, dict):
        return item
    projected = dict(call)
    arguments = projected.get("argumentsJson")
    if isinstance(arguments, str):
        arguments = _redact_display_arguments(
            str(projected.get("toolName", "")), arguments
        )
        projected["argumentsJson"] = arguments
    if isinstance(arguments, str) and len(arguments.encode("utf-8")) > INLINE_TOOL_TEXT_BYTES:
        projected.pop("argumentsJson", None)
    for field, prefix in (("changeDiff", "changeDiff"), ("resultJson", "result")):
        content = projected.get(field)
        if not isinstance(content, str):
            continue
        encoded = content.encode("utf-8")
        if len(encoded) <= INLINE_TOOL_TEXT_BYTES:
            continue
        projected[prefix + "Bytes"] = len(encoded)
        projected[prefix + "Hash"] = hashlib.sha256(encoded).hexdigest()
        if field == "changeDiff":
            projected[field] = ""
        else:
            result = json.loads(content)
            projected[field] = json.dumps({
                "outcome": result.get("outcome"), "code": result.get("code"),
                "summary": str(result.get("summary", ""))[:1024],
                "sideEffectsMayExist": result.get("sideEffectsMayExist", False),
                "reconciliationRequired": result.get("reconciliationRequired", False),
                "data": {"truncated": True},
            }, ensure_ascii=False)
    return {**item, "toolCall": projected}


def _redact_display_arguments(tool_name: str, value: str) -> str:
    fields = _DISPLAY_ARGUMENT_FIELDS.get(tool_name)
    if fields is None:
        return value
    try:
        arguments = json.loads(value)
    except json.JSONDecodeError:
        return value
    if not isinstance(arguments, dict):
        return value
    changed = False
    scanner = default_scanner()
    for field in fields:
        text = arguments.get(field)
        if not isinstance(text, str):
            continue
        try:
            redacted = scanner.redact_for_presentation(text).text
        except SensitiveScanError:
            redacted = "[REDACTED:sensitive_scan_failed]"
        if redacted != text:
            arguments[field] = redacted
            changed = True
    if not changed:
        return value
    return json.dumps(arguments, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def project_approval_diff(description: dict[str, object]) -> dict[str, object]:
    content = description.get("diff")
    if description.get("kind") != "file_change" or not isinstance(content, str):
        return description
    # The typed approval UI reads the paths from the complete Diff. Keeping the
    # redundant path list in every notification can overflow the control frame.
    description = {key: value for key, value in description.items() if key != "paths"}
    encoded = content.encode("utf-8")
    if len(encoded) <= INLINE_TOOL_TEXT_BYTES:
        return description
    return {
        **description, "diff": "", "diffBytes": len(encoded),
        "diffHash": hashlib.sha256(encoded).hexdigest(),
    }

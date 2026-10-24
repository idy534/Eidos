"""Codex Hook argument templates and MCP-result adaptation."""
from __future__ import annotations

import json
import re

from pydantic import JsonValue, TypeAdapter


_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_.]*)\}")


class HookMcpUnavailable(ValueError):
    """No already connected, authorized service can accept this invocation."""


def expand_input(template: dict[str, JsonValue], event: dict[str, JsonValue]) -> dict[str, JsonValue]:
    def lookup(path: str) -> JsonValue:
        value: JsonValue = event
        for key in path.split("."):
            if isinstance(value, dict) and key in value:
                value = value[key]
            elif isinstance(value, list) and key.isdigit() and int(key) < len(value):
                value = value[int(key)]
            else:
                raise ValueError("hook_mcp_input_missing")
        return value

    def expand(value: JsonValue, depth: int = 0) -> JsonValue:
        if depth > 32:
            raise ValueError("hook_mcp_input_limit")
        if isinstance(value, dict):
            return {key: expand(item, depth + 1) for key, item in value.items()}
        if isinstance(value, list):
            return [expand(item, depth + 1) for item in value]
        if not isinstance(value, str):
            return value
        match = _PLACEHOLDER.fullmatch(value)
        if match:
            return lookup(match[1])
        return _PLACEHOLDER.sub(lambda match: str(found) if isinstance(found := lookup(match[1]), str) else json.dumps(found, ensure_ascii=False, separators=(",", ":")), value)

    result = TypeAdapter(dict[str, JsonValue]).validate_python(expand(template))
    if len(json.dumps(result, ensure_ascii=False).encode()) > 1024 * 1024:
        raise ValueError("hook_mcp_input_limit")
    return result


def hook_output(result: dict[str, JsonValue]) -> dict[str, JsonValue]:
    data = result.get("data")
    data = data if isinstance(data, dict) else {}
    structured = data.get("structuredContent")
    content = data.get("content")
    text = str(data["text"]) if isinstance(data.get("text"), str) else "\n".join(str(block["text"]) for block in content if isinstance(block, dict) and isinstance(block.get("text"), str)) if isinstance(content, list) else ""
    success = result.get("outcome") == "success" and not data.get("isError")
    return {"data": {"termination": "mcp_outcome_unknown" if result.get("reconciliationRequired") is True else "exit",
        "exitCode": 0 if success else 1, "stdout": json.dumps(structured, ensure_ascii=False) if structured is not None else text,
        "stderr": "" if success else str(result.get("code") or "hook_mcp_call_failed"), "truncated": False}}

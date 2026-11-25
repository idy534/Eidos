from __future__ import annotations

import copy
import json

import httpx2 as httpx

ANTHROPIC_CACHE_CONTROL = {"type": "ephemeral"}


def apply_anthropic_cache_control(payload: dict[str, object]) -> dict[str, object]:
    """Attach ephemeral prompt-cache breakpoints to system, tools, and recent user/assistant turns."""
    updated = copy.deepcopy(payload)
    raw_messages = updated.get("messages")
    if isinstance(raw_messages, list):
        for message in raw_messages:
            if not isinstance(message, dict) or message.get("role") != "system":
                continue
            content = message.get("content")
            if isinstance(content, str) and content:
                message["content"] = [
                    {
                        "type": "text",
                        "text": content,
                        "cache_control": dict(ANTHROPIC_CACHE_CONTROL),
                    }
                ]
            elif isinstance(content, list) and content:
                last_block = content[-1]
                if isinstance(last_block, dict) and "cache_control" not in last_block:
                    last_block["cache_control"] = dict(ANTHROPIC_CACHE_CONTROL)
            break

        for message in reversed(raw_messages):
            if not isinstance(message, dict) or message.get("role") == "system":
                continue
            content = message.get("content")
            if isinstance(content, str) and content:
                message["content"] = [
                    {
                        "type": "text",
                        "text": content,
                        "cache_control": dict(ANTHROPIC_CACHE_CONTROL),
                    }
                ]
                break
            if isinstance(content, list) and content:
                last_block = content[-1]
                if isinstance(last_block, dict) and "cache_control" not in last_block:
                    last_block["cache_control"] = dict(ANTHROPIC_CACHE_CONTROL)
                break

    raw_tools = updated.get("tools")
    if isinstance(raw_tools, list) and raw_tools:
        last_tool = raw_tools[-1]
        if isinstance(last_tool, dict) and "cache_control" not in last_tool:
            last_tool["cache_control"] = dict(ANTHROPIC_CACHE_CONTROL)

    return updated


async def inject_anthropic_prompt_cache(request: httpx.Request) -> None:
    """HTTPX request hook that enables Anthropic prompt caching without breaking streaming/retry."""
    if request.method != "POST":
        return
    content_type = request.headers.get("content-type", "")
    if "application/json" not in content_type.lower():
        return
    try:
        raw_body = request.content
        if not raw_body:
            return
        parsed = json.loads(raw_body.decode("utf-8"))
    except (RuntimeError, UnicodeDecodeError, ValueError):
        return
    if not isinstance(parsed, dict):
        return
    updated = apply_anthropic_cache_control(parsed)
    encoded = json.dumps(updated, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request._content = encoded
    request.stream = httpx.ByteStream(encoded)
    request.headers["content-length"] = str(len(encoded))
    request.headers.setdefault("anthropic-beta", "prompt-caching-2024-07-31")

from __future__ import annotations

from dataclasses import dataclass
import http.client
import json
import socket
import threading
import time
from typing import Callable

from eidos_runtime.model import ModelContextItem, ModelResponse, ModelToolCall
from eidos_runtime.tools import model_tool_definitions


HOST = "api.deepseek.com"
PATH = "/chat/completions"
MODEL = "deepseek-v4-flash"
REQUEST_DEADLINE_SECONDS = 120.0
MAX_SSE_LINE_BYTES = 1024 * 1024
MAX_SSE_EVENTS = 4_096
MAX_TOOL_NAME_BYTES = 256
SYSTEM_PROMPT = """You are Eidos, a local coding agent. Work only through the provided tools.
Use relative workspace paths. Inspect relevant files before answering. Never invent tool results.
When the task is complete, give a concise final answer in the user's language."""
TITLE_PROMPT = """Create a concise task title from the user query below.
Use the query's language, capture its intent, and return only the title with no quotes or punctuation wrapper.
Keep it under 60 characters.

User query:
"""


class ModelProviderError(RuntimeError):
    pass


@dataclass
class _ToolAccumulator:
    call_id: str = ""
    name: str = ""
    arguments: str = ""


class DeepSeekChatModel:
    def __init__(self, api_key: str) -> None:
        self.api_key = api_key

    def generate_title(self, user_input: str, cancel: threading.Event) -> str:
        return self.complete(
            ({"type": "user", "content": TITLE_PROMPT + user_input},),
            cancel,
            lambda _delta: None,
            allow_tools=False,
        ).text

    def complete(
        self,
        context: tuple[ModelContextItem, ...],
        cancel: threading.Event,
        on_text_delta: Callable[[str], None],
        allow_tools: bool = True,
    ) -> ModelResponse:
        if cancel.is_set():
            return ModelResponse()
        deadline = time.monotonic() + REQUEST_DEADLINE_SECONDS
        request: dict[str, object] = {
                "model": MODEL,
                "messages": _messages_from_context(context),
                "stream": True,
                "thinking": {"type": "disabled"},
        }
        if allow_tools:
            request["tools"] = _tool_definitions()
            request["tool_choice"] = "auto"
        payload = json.dumps(
            request,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        connection = http.client.HTTPSConnection(HOST, timeout=10.0)
        watcher_done = threading.Event()

        def cancel_watcher() -> None:
            while not watcher_done.wait(0.1):
                if cancel.is_set():
                    if connection.sock is not None:
                        try:
                            connection.sock.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
                    connection.close()
                    return

        watcher = threading.Thread(target=cancel_watcher, daemon=True)
        watcher.start()
        try:
            connection.request(
                "POST",
                PATH,
                body=payload,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream",
                    "User-Agent": "Eidos/0.2.0",
                },
            )
            response = connection.getresponse()
            if response.status != 200:
                raise ModelProviderError(f"provider_http_{response.status}")
            return _read_stream(
                response,
                cancel,
                on_text_delta,
                deadline=deadline,
                set_read_timeout=(
                    connection.sock.settimeout if connection.sock is not None else None
                ),
            )
        except ModelProviderError:
            raise
        except (OSError, http.client.HTTPException, socket.timeout):
            if cancel.is_set():
                return ModelResponse()
            raise ModelProviderError("provider_unavailable") from None
        finally:
            watcher_done.set()
            connection.close()


def _read_stream(
    response: http.client.HTTPResponse,
    cancel: threading.Event,
    on_text_delta: Callable[[str], None],
    *,
    deadline: float | None = None,
    set_read_timeout: Callable[[float], None] | None = None,
) -> ModelResponse:
    if deadline is None:
        deadline = time.monotonic() + REQUEST_DEADLINE_SECONDS
    text_parts: list[str] = []
    tool_calls: dict[int, _ToolAccumulator] = {}
    finish_reason: str | None = None
    event_count = 0
    while True:
        if cancel.is_set():
            return ModelResponse()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ModelProviderError("provider_timeout")
        if set_read_timeout is not None:
            set_read_timeout(remaining)
        try:
            line = response.readline(MAX_SSE_LINE_BYTES + 1)
        except socket.timeout:
            continue
        if not line:
            break
        if len(line) > MAX_SSE_LINE_BYTES:
            raise ModelProviderError("provider_response_too_large")
        if not line.startswith(b"data:"):
            continue
        data = line[5:].strip()
        if data == b"[DONE]":
            break
        if not data:
            continue
        event_count += 1
        if event_count > MAX_SSE_EVENTS:
            raise ModelProviderError("provider_response_too_large")
        try:
            event = json.loads(data.decode("utf-8"))
            choice = event["choices"][0]
            delta = choice.get("delta", {})
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError):
            raise ModelProviderError("provider_protocol_error") from None
        content = delta.get("content")
        if isinstance(content, str) and content:
            text_parts.append(content)
            on_text_delta(content)
        # reasoning_content is intentionally neither exposed nor persisted.
        raw_tool_calls = delta.get("tool_calls", [])
        if raw_tool_calls is None:
            raw_tool_calls = []
        if not isinstance(raw_tool_calls, list):
            raise ModelProviderError("provider_protocol_error")
        for raw_call in raw_tool_calls:
            _merge_tool_delta(tool_calls, raw_call)
        reason = choice.get("finish_reason")
        if reason is not None:
            if not isinstance(reason, str):
                raise ModelProviderError("provider_protocol_error")
            finish_reason = reason

    if finish_reason not in {"stop", "tool_calls"}:
        raise ModelProviderError("provider_incomplete_response")
    calls: list[ModelToolCall] = []
    for index in sorted(tool_calls):
        accumulated = tool_calls[index]
        try:
            arguments = json.loads(accumulated.arguments)
        except json.JSONDecodeError:
            arguments = None
        if not isinstance(arguments, dict):
            raise ModelProviderError("provider_invalid_tool_arguments")
        calls.append(
            ModelToolCall(
                provider_call_id=accumulated.call_id,
                name=accumulated.name,
                arguments=arguments,
            )
        )
    if finish_reason == "tool_calls" and not calls:
        raise ModelProviderError("provider_protocol_error")
    return ModelResponse(text="".join(text_parts), tool_calls=tuple(calls))


def _merge_tool_delta(
    accumulators: dict[int, _ToolAccumulator], raw_call: object
) -> None:
    if not isinstance(raw_call, dict) or not isinstance(raw_call.get("index"), int):
        raise ModelProviderError("provider_protocol_error")
    index = raw_call["index"]
    if index < 0 or index > 15:
        raise ModelProviderError("provider_protocol_error")
    accumulator = accumulators.setdefault(index, _ToolAccumulator())
    call_id = raw_call.get("id")
    if call_id is not None:
        if not isinstance(call_id, str):
            raise ModelProviderError("provider_protocol_error")
        accumulator.call_id += call_id
    function = raw_call.get("function")
    if function is not None:
        if not isinstance(function, dict):
            raise ModelProviderError("provider_protocol_error")
        name = function.get("name")
        arguments = function.get("arguments")
        if name is not None:
            if not isinstance(name, str):
                raise ModelProviderError("provider_protocol_error")
            accumulator.name += name
        if arguments is not None:
            if not isinstance(arguments, str):
                raise ModelProviderError("provider_protocol_error")
            accumulator.arguments += arguments
    if (
        len(accumulator.call_id) > 256
        or len(accumulator.name.encode("utf-8")) > MAX_TOOL_NAME_BYTES
        or len(accumulator.arguments) > 64 * 1024
    ):
        raise ModelProviderError("provider_response_too_large")


def _messages_from_context(
    context: tuple[ModelContextItem, ...],
) -> list[dict[str, object]]:
    messages: list[dict[str, object]] = [
        {"role": "system", "content": SYSTEM_PROMPT}
    ]
    for item in context:
        item_type = item.get("type")
        if item_type in {"user", "assistant"}:
            content = item.get("content")
            if isinstance(content, str):
                messages.append({"role": item_type, "content": content})
        elif item_type == "tool_call":
            call_id = item.get("callId")
            name = item.get("name")
            arguments = item.get("arguments")
            if all(isinstance(value, str) for value in (call_id, name, arguments)):
                messages.append(
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": call_id,
                                "type": "function",
                                "function": {"name": name, "arguments": arguments},
                            }
                        ],
                    }
                )
        elif item_type == "tool_result":
            call_id = item.get("callId")
            result = item.get("result")
            if isinstance(call_id, str) and isinstance(result, str):
                messages.append(
                    {"role": "tool", "tool_call_id": call_id, "content": result}
                )
        elif item_type == "protocol_error":
            code = item.get("code")
            if isinstance(code, str):
                messages.append(
                    {
                        "role": "user",
                        "content": f"Your previous response was invalid ({code}). Try again using the provided tool schemas.",
                    }
                )
    return messages


def _tool_definitions() -> list[dict[str, object]]:
    return model_tool_definitions()

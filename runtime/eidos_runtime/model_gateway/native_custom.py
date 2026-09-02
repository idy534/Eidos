from __future__ import annotations

import asyncio
from contextlib import nullcontext, suppress
import inspect
import json
import threading
from typing import Callable

import anyio
from openai import APIConnectionError, APIStatusError, APITimeoutError

from eidos_runtime.model.client import (
    CustomToolDefinition,
    CustomToolPayload,
    FunctionToolDefinition,
    FunctionToolPayload,
    MAX_CUSTOM_TOOL_INPUT_BYTES,
    MAX_FUNCTION_ARGUMENT_BYTES,
    MAX_TOOL_CALL_ID_BYTES,
    MAX_TOOL_NAME_BYTES,
    ModelContextItem,
    ModelProfileSnapshot,
    ModelRequestError,
    ModelRequestFailure,
    ModelResponse,
    ModelToolCall,
    ModelToolDefinitionLike,
    ModelUsage,
)
from eidos_runtime.model.config import ModelProfileSpec
from eidos_runtime.model.pydantic_ai_client import (
    _cancelled_failure,
    _with_retry_diagnostics,
    map_model_error,
)
from eidos_runtime.model.response_phase import resolve_chat_completion_phase
from eidos_runtime.model_gateway.retry_transport import (
    RetryBackoffCanceled,
    RetryTracker,
    RetryTransportClient,
)
from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel


def encode_responses_tool_definition(
    definition: ModelToolDefinitionLike,
) -> dict[str, object]:
    if isinstance(definition, CustomToolDefinition):
        value: dict[str, object] = {
            "type": "custom",
            "name": definition.name,
            "description": definition.description,
        }
        if definition.format is not None:
            value["format"] = definition.format.model_dump(
                mode="json", exclude_none=True
            )
        return value
    if isinstance(definition, FunctionToolDefinition):
        return {
            "type": "function",
            "name": definition.name,
            "description": definition.description,
            "parameters": definition.parameters_json_schema,
        }
    raise ValueError("unsupported_model_tool_definition")


def encode_responses_context(
    context: tuple[ModelContextItem, ...],
) -> list[dict[str, object]]:
    encoded: list[dict[str, object]] = []
    for item in context:
        item_type = item.get("type", item.get("role"))
        if item_type in {"user", "assistant"}:
            content = item.get("content")
            if isinstance(content, str):
                encoded.append({
                    "type": "message",
                    "role": item_type,
                    "content": content,
                })
        elif item_type == "tool_call":
            call_id = item.get("callId")
            name = item.get("name")
            payload_kind = item.get("payloadKind", "function")
            if not all(isinstance(value, str) for value in (call_id, name)):
                continue
            if payload_kind == "custom":
                raw_input = item.get("input")
                if isinstance(raw_input, str):
                    encoded.append({
                        "type": "custom_tool_call",
                        "call_id": call_id,
                        "name": name,
                        "input": raw_input,
                    })
            else:
                arguments = item.get("arguments")
                if isinstance(arguments, str):
                    encoded.append({
                        "type": "function_call",
                        "call_id": call_id,
                        "name": name,
                        "arguments": arguments,
                    })
        elif item_type == "tool_result":
            call_id = item.get("callId")
            result = item.get("result")
            if not isinstance(call_id, str) or not isinstance(result, str):
                continue
            if item.get("payloadKind", "function") == "custom":
                encoded.append({
                    "type": "custom_tool_call_output",
                    "call_id": call_id,
                    "output": result,
                })
            else:
                name = item.get("name")
                if isinstance(name, str):
                    encoded.append({
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": result,
                    })
        elif item_type in {"protocol_error", "tool_error", "finalization"}:
            text = _context_feedback(item_type, item)
            if text:
                encoded.append({
                    "type": "message",
                    "role": "user",
                    "content": text,
                })
        else:
            raise ValueError(f"unsupported model context item type: {item_type!r}")
    return encoded


def map_responses_response(
    response: object,
    *,
    custom_inputs: dict[str, str] | None = None,
    retry_tracker: RetryTracker | None = None,
) -> ModelResponse:
    if _field(response, "status") != "completed":
        raise _protocol_error(_field(response, "provider_name"))
    custom_inputs = custom_inputs or {}
    calls: list[ModelToolCall] = []
    output = _field(response, "output", ())
    if not isinstance(output, (list, tuple)):
        output = ()
    for index, item in enumerate(output):
        item_type = _field(item, "type")
        if item_type == "custom_tool_call":
            raw_input = _field(item, "input")
            item_id = _field(item, "id")
            if (
                isinstance(item_id, str)
                and item_id in custom_inputs
                and (not isinstance(raw_input, str) or not raw_input)
            ):
                raw_input = custom_inputs[item_id]
            call_id = _field(item, "call_id")
            name = _field(item, "name")
            if (
                not isinstance(call_id, str)
                or not isinstance(name, str)
                or not isinstance(raw_input, str)
                or not _valid_call_identity(call_id, name)
                or not _valid_utf8_size(raw_input, MAX_CUSTOM_TOOL_INPUT_BYTES)
            ):
                raise _protocol_error(_field(response, "provider_name"))
            calls.append(ModelToolCall(
                call_id, name, CustomToolPayload(input=raw_input)
            ))
        elif item_type == "function_call":
            arguments = _field(item, "arguments")
            call_id = _field(item, "call_id")
            name = _field(item, "name")
            if not isinstance(arguments, str):
                raise _protocol_error(_field(response, "provider_name"))
            try:
                parsed = json.loads(arguments)
            except (TypeError, ValueError):
                raise _protocol_error(_field(response, "provider_name")) from None
            encoded = json.dumps(
                parsed,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            if (
                not isinstance(parsed, dict)
                or not isinstance(call_id, str)
                or not isinstance(name, str)
                or not _valid_call_identity(call_id, name)
                or len(encoded) > MAX_FUNCTION_ARGUMENT_BYTES
            ):
                raise _protocol_error(_field(response, "provider_name"))
            calls.append(ModelToolCall(
                call_id, name, FunctionToolPayload(arguments=parsed)
            ))
    text = _response_text(response)
    finish_reason = "tool_call" if calls else "stop"
    usage = _responses_usage(_field(response, "usage"))
    return ModelResponse(
        text=text,
        tool_calls=tuple(calls),
        phase=resolve_chat_completion_phase(
            text=text,
            has_tool_calls=bool(calls),
            finish_reason=finish_reason,
        ),
        usage=usage,
        provider_name=_string_or_none(_field(response, "provider_name")),
        resolved_model_name=_string_or_none(_field(response, "model")),
        finish_reason=finish_reason,
        provider_response_id=_string_or_none(_field(response, "id")),
        response_state="complete",
        transport_attempt_count=(
            retry_tracker.transport_attempt_count if retry_tracker else 0
        ),
        transport_retry_count=(
            retry_tracker.transport_retry_count if retry_tracker else 0
        ),
        last_retry_reason=(retry_tracker.last_retry_reason if retry_tracker else None),
        last_backoff_seconds=(
            retry_tracker.last_backoff_seconds if retry_tracker else None
        ),
        retry_after_applied=(retry_tracker.retry_after_applied if retry_tracker else False),
    )


class OpenAIResponsesModelClient:
    """ModelClient for the Responses wire, including native Custom tools."""

    def __init__(
        self,
        profile_spec: ModelProfileSpec,
        *,
        openai_client: object,
        retry_transport: RetryTransportClient | None,
        profile_snapshot: ModelProfileSnapshot,
        async_kernel: RuntimeAsyncKernel,
        parallel_tool_calls: bool | None = True,
        reasoning_effort: str | None = None,
    ) -> None:
        if profile_snapshot.wire_api != "openai_responses":
            raise ValueError("responses_client_requires_responses_profile")
        self._profile_spec = profile_spec
        self._openai_client = openai_client
        self._retry_transport = retry_transport
        self._profile_snapshot = profile_snapshot
        self._async_kernel = async_kernel
        self._parallel_tool_calls = parallel_tool_calls
        self._reasoning_effort = reasoning_effort
        self._lock = threading.RLock()
        self._closed = False

    @property
    def profile_snapshot(self) -> ModelProfileSnapshot:
        return self._profile_snapshot

    def generate_title(self, user_input: str, cancel: threading.Event) -> str:
        from eidos_runtime.model.prompts import TITLE_PROMPT, TITLE_SYSTEM_INSTRUCTIONS

        return self.complete(
            ({"type": "user", "content": TITLE_PROMPT + user_input},),
            cancel,
            lambda _delta: None,
            instructions=TITLE_SYSTEM_INSTRUCTIONS,
            allow_tools=False,
        ).text

    def complete(
        self,
        context: tuple[ModelContextItem, ...],
        cancel: threading.Event,
        on_text_delta: Callable[[str], None],
        *,
        instructions: str,
        allow_tools: bool = True,
        tool_definitions: tuple[ModelToolDefinitionLike, ...] = (),
    ) -> ModelResponse:
        with self._lock:
            if self._closed:
                raise RuntimeError("model client is closed")
        if cancel.is_set():
            raise ModelRequestError(_cancelled_failure())
        tracker = RetryTracker()
        try:
            return self._async_kernel.call(
                self._complete,
                context,
                cancel,
                on_text_delta,
                instructions,
                allow_tools,
                tool_definitions,
                tracker,
            )
        except RetryBackoffCanceled:
            raise ModelRequestError(
                _with_retry_diagnostics(_cancelled_failure(), tracker)
            ) from None
        except ModelRequestError:
            raise
        except (
            APITimeoutError,
            APIConnectionError,
            APIStatusError,
            OSError,
        ) as error:
            failure = _cancelled_failure() if cancel.is_set() else map_model_error(error)
            if failure.provider_name is None:
                failure = failure.model_copy(update={
                    "provider_name": self._profile_spec.provider_id
                })
            raise ModelRequestError(
                _with_retry_diagnostics(failure, tracker)
            ) from None
        except (ValueError, AssertionError):
            raise ModelRequestError(ModelRequestFailure(
                code="protocol_error",
                retryable=False,
                provider_name=self._profile_spec.provider_id,
            )) from None

    async def _complete(
        self,
        context: tuple[ModelContextItem, ...],
        cancel: threading.Event,
        on_text_delta: Callable[[str], None],
        instructions: str,
        allow_tools: bool,
        tool_definitions: tuple[ModelToolDefinitionLike, ...],
        retry_tracker: RetryTracker,
    ) -> ModelResponse:
        request_kwargs: dict[str, object] = {
            "model": self._profile_spec.model_id,
            "input": encode_responses_context(context),
            "instructions": instructions,
            "max_output_tokens": self._profile_spec.max_output_tokens,
            "stream": True,
            "store": False,
        }
        if allow_tools:
            for definition in tool_definitions:
                if isinstance(definition, CustomToolDefinition):
                    if not self._profile_snapshot.supports_custom_tools:
                        raise ValueError("custom_tools_not_supported")
                    if (
                        definition.format is not None
                        and definition.format.type == "grammar"
                        and not self._profile_snapshot.supports_tool_grammar
                    ):
                        raise ValueError("tool_grammar_not_supported")
            request_kwargs["tools"] = [
                encode_responses_tool_definition(definition)
                for definition in tool_definitions
            ]
        else:
            request_kwargs["tools"] = []
        if self._parallel_tool_calls is not None:
            request_kwargs["parallel_tool_calls"] = self._parallel_tool_calls
        if self._reasoning_effort is not None:
            request_kwargs["reasoning"] = {"effort": self._reasoning_effort}
        retry_scope = (
            self._retry_transport.request_scope(cancel, retry_tracker)
            if self._retry_transport is not None else nullcontext()
        )
        with retry_scope:
            response = getattr(self._openai_client, "responses").create(
                **request_kwargs
            )
            if inspect.isawaitable(response):
                response = await response
            custom_inputs: dict[str, str] = {}
            if hasattr(response, "__aiter__"):
                response, custom_inputs = await self._consume_stream(
                    response, cancel, on_text_delta
                )
        if cancel.is_set():
            raise ModelRequestError(_cancelled_failure())
        return map_responses_response(
            response, custom_inputs=custom_inputs, retry_tracker=retry_tracker
        )

    async def _consume_stream(
        self,
        stream: object,
        cancel: threading.Event,
        on_text_delta: Callable[[str], None],
    ) -> tuple[object, dict[str, str]]:
        custom_inputs: dict[str, str] = {}
        function_arguments: dict[str, str] = {}
        final_response: object | None = None
        iterator = stream.__aiter__()  # type: ignore[attr-defined]
        stream_closed = False

        async def close_stream(*, suppress_errors: bool) -> None:
            nonlocal stream_closed
            if stream_closed:
                return
            stream_closed = True
            try:
                await _close_stream(stream)
            except asyncio.CancelledError:
                if not suppress_errors:
                    raise
            except Exception:
                if not suppress_errors:
                    raise

        async def wait_for_cancel() -> None:
            while not cancel.is_set():
                await asyncio.sleep(0.005)

        async def next_event() -> object:
            if cancel.is_set():
                raise ModelRequestError(_cancelled_failure())
            next_task = asyncio.create_task(iterator.__anext__())
            cancel_task = asyncio.create_task(wait_for_cancel())
            try:
                done, _pending = await asyncio.wait(
                    (next_task, cancel_task),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if cancel_task in done and cancel.is_set():
                    close_task = asyncio.create_task(close_stream(suppress_errors=True))
                    await asyncio.sleep(0)
                    next_task.cancel()
                    with suppress(asyncio.CancelledError, StopAsyncIteration):
                        await next_task
                    await close_task
                    raise ModelRequestError(_cancelled_failure())
                cancel_task.cancel()
                with suppress(asyncio.CancelledError):
                    await cancel_task
                return next_task.result()
            finally:
                if not cancel_task.done():
                    cancel_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await cancel_task
                if not next_task.done():
                    next_task.cancel()
                    with suppress(asyncio.CancelledError, StopAsyncIteration):
                        await next_task

        try:
            while True:
                try:
                    event = await next_event()
                except StopAsyncIteration:
                    raise _protocol_error(None) from None
                event_type = _field(event, "type")
                if event_type == "response.output_text.delta":
                    delta = _field(event, "delta")
                    if isinstance(delta, str) and delta:
                        await anyio.to_thread.run_sync(on_text_delta, delta)
                elif event_type == "response.custom_tool_call_input.delta":
                    item_id = _field(event, "item_id")
                    delta = _field(event, "delta")
                    if isinstance(item_id, str) and isinstance(delta, str):
                        value = custom_inputs.get(item_id, "") + delta
                        if not _valid_utf8_size(value, MAX_CUSTOM_TOOL_INPUT_BYTES):
                            raise ValueError("custom_tool_input_too_large")
                        custom_inputs[item_id] = value
                elif event_type == "response.custom_tool_call_input.done":
                    item_id = _field(event, "item_id")
                    raw_input = _field(event, "input")
                    if isinstance(item_id, str) and isinstance(raw_input, str):
                        if not _valid_utf8_size(raw_input, MAX_CUSTOM_TOOL_INPUT_BYTES):
                            raise ValueError("custom_tool_input_too_large")
                        custom_inputs[item_id] = raw_input
                elif event_type == "response.function_call_arguments.delta":
                    item_id = _field(event, "item_id")
                    delta = _field(event, "delta")
                    if isinstance(item_id, str) and isinstance(delta, str):
                        function_arguments[item_id] = (
                            function_arguments.get(item_id, "") + delta
                        )
                elif event_type == "response.function_call_arguments.done":
                    item_id = _field(event, "item_id")
                    arguments = _field(event, "arguments")
                    if isinstance(item_id, str) and isinstance(arguments, str):
                        function_arguments[item_id] = arguments
                elif event_type == "response.output_item.done":
                    pass
                elif event_type == "response.completed":
                    final_response = _field(event, "response")
                    if final_response is None:
                        raise _protocol_error(None)
                    break
                elif event_type in {
                    "response.failed", "response.incomplete", "error"
                }:
                    raise _protocol_error(_field(event, "provider_name"))
        finally:
            await close_stream(suppress_errors=cancel.is_set())

        if final_response is None:
            raise _protocol_error(None)
        output = _field(final_response, "output", ())
        if isinstance(output, (list, tuple)):
            for item in output:
                item_id = _field(item, "id")
                if not isinstance(item_id, str):
                    continue
                if _field(item, "type") == "custom_tool_call":
                    raw_input = custom_inputs.get(item_id)
                    if raw_input is not None:
                        _set_field(item, "input", raw_input)
                elif _field(item, "type") == "function_call":
                    arguments = function_arguments.get(item_id)
                    if arguments is not None:
                        _set_field(item, "arguments", arguments)
        return final_response, custom_inputs

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        close = getattr(self._openai_client, "close", None)
        if callable(close):
            result = self._async_kernel.call(_await_if_needed, close)
            del result


async def _await_if_needed(function: Callable[[], object]) -> None:
    result = function()
    if inspect.isawaitable(result):
        await result


async def _close_stream(stream: object) -> None:
    close = getattr(stream, "aclose", None) or getattr(stream, "close", None)
    if callable(close):
        result = close()
        if inspect.isawaitable(result):
            await result


def _context_feedback(item_type: object, item: ModelContextItem) -> str:
    if item_type == "protocol_error":
        code = item.get("code")
        return f"Your previous response was invalid ({code}). Correct the tool call."
    if item_type == "tool_error":
        code = item.get("code")
        return f"Runtime tool error data: {json.dumps({'code': code}, ensure_ascii=False)}"
    reason = item.get("stopReason")
    return f"Runtime finalization state data: {json.dumps({'stopReason': reason}, ensure_ascii=False)}"


def _field(value: object, name: str, default: object = None) -> object:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _set_field(value: object, name: str, field_value: object) -> None:
    if isinstance(value, dict):
        value[name] = field_value
    else:
        try:
            setattr(value, name, field_value)
        except (AttributeError, TypeError):
            return


def _valid_call_identity(call_id: str, name: str) -> bool:
    try:
        return bool(call_id and name) and (
            len(call_id.encode("utf-8")) <= MAX_TOOL_CALL_ID_BYTES
            and len(name.encode("utf-8")) <= MAX_TOOL_NAME_BYTES
        )
    except UnicodeEncodeError:
        return False


def _valid_utf8_size(value: str, maximum: int) -> bool:
    try:
        return len(value.encode("utf-8")) <= maximum
    except UnicodeEncodeError:
        return False


def _protocol_error(provider_name: object) -> ModelRequestError:
    return ModelRequestError(ModelRequestFailure(
        code="protocol_error",
        retryable=False,
        provider_name=_string_or_none(provider_name),
    ))


def _response_text(response: object) -> str:
    output_text = _field(response, "output_text")
    if isinstance(output_text, str):
        return output_text
    parts: list[str] = []
    output = _field(response, "output", ())
    if isinstance(output, (list, tuple)):
        for item in output:
            if _field(item, "type") != "message":
                continue
            content = _field(item, "content", ())
            if not isinstance(content, (list, tuple)):
                continue
            for part in content:
                if _field(part, "type") == "output_text":
                    text = _field(part, "text")
                    if isinstance(text, str):
                        parts.append(text)
    return "".join(parts)


def _responses_usage(value: object) -> ModelUsage | None:
    if value is None:
        return None
    input_tokens = _nonnegative_int(_field(value, "input_tokens"))
    output_tokens = _nonnegative_int(_field(value, "output_tokens"))
    details: dict[str, int] = {}
    output_details = _field(value, "output_tokens_details")
    reasoning = _nonnegative_int(_field(output_details, "reasoning_tokens"))
    if reasoning is not None:
        details["reasoning_tokens"] = reasoning
    input_details = _field(value, "input_tokens_details")
    cached = _nonnegative_int(_field(input_details, "cached_tokens"))
    return ModelUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cached,
        details=details,
    )


def _nonnegative_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) else None


__all__ = [
    "OpenAIResponsesModelClient",
    "encode_responses_context",
    "encode_responses_tool_definition",
    "map_responses_response",
]

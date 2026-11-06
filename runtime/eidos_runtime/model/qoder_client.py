"""Qoder transport at the existing ModelClient seam; Eidos keeps its loop."""
from __future__ import annotations

from collections.abc import Callable
import json
import threading
import uuid

from jsonschema import Draft202012Validator
from referencing import Registry

from eidos_runtime.infrastructure.qoder_model_driver import QoderModelDriver, failure
from eidos_runtime.model.client import (
    CreditsUsage, FunctionToolPayload, MAX_FUNCTION_ARGUMENT_BYTES, MAX_TOOL_CALL_ID_BYTES, ModelContextItem,
    ModelProfileSnapshot, ModelResponse, ModelToolCall, ModelToolDefinitionLike, ModelUsage,
)
from eidos_runtime.model.prompts import TITLE_PROMPT, TITLE_SYSTEM_INSTRUCTIONS
from eidos_runtime.model.qoder_context import encode_qoder_context, qoder_images
from eidos_runtime.model.qoder_protocol import MAX_TEXT_BYTES, WorkerRequest, WorkerResult
from eidos_runtime.model.request_scope import run_model_request
from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel


class QoderModelClient:
    def __init__(self, profile: ModelProfileSnapshot, driver: QoderModelDriver,
                 async_kernel: RuntimeAsyncKernel) -> None:
        self._profile = profile
        self._driver = driver
        self._kernel = async_kernel

    @property
    def profile_snapshot(self) -> ModelProfileSnapshot:
        return self._profile

    def generate_title(self, user_input: str, cancel: threading.Event) -> str:
        return self.complete(({"type": "user", "content": TITLE_PROMPT.format(task=user_input),
                               "isCurrentInput": True},), cancel, lambda _text: None,
                             instructions=TITLE_SYSTEM_INSTRUCTIONS, allow_tools=False,
                             max_output_tokens=min(256, self._profile.max_output_tokens)).text

    def complete(self, context: tuple[ModelContextItem, ...], cancel: threading.Event,
                 on_text_delta: Callable[[str], None], *, instructions: str,
                 allow_tools: bool = True, tool_definitions: tuple[ModelToolDefinitionLike, ...] = (),
                 max_output_tokens: int | None = None, required_tool: str | None = None) -> ModelResponse:
        if cancel.is_set():
            raise failure("sampling_canceled", "qoder_canceled")
        limit = self._profile.max_output_tokens if max_output_tokens is None else max_output_tokens
        if not 1 <= limit <= self._profile.max_output_tokens:
            raise ValueError("model_output_limit_invalid")
        if required_tool is not None and (not allow_tools or not self._profile.supports_tools or not any(t.name == required_tool for t in tool_definitions)):
            raise ValueError("model_output_tool_invalid")
        definitions = tool_definitions if allow_tools and self._profile.supports_tools else ()
        try:
            system, prompt, tools, aliases = encode_qoder_context(context, instructions, definitions, required_tool=required_tool)
        except ValueError:
            raise failure("invalid_request", "qoder_context_unsupported") from None
        images = qoder_images(context, supports_images=self._profile.supports_images)
        if images and (not self._profile.supports_images or any(i["mime"] not in {"image/png", "image/jpeg"} for i in images)):
            raise failure("invalid_request", "qoder_images_unsupported")
        request = WorkerRequest(request_id=str(uuid.uuid4()), operation="sample",
            model_id=self._profile.remote_model_id or self._profile.model_id,
            prompt=prompt, instructions=system, tools=tools, max_output_tokens=limit,
            context_window_tokens=self._profile.context_window_tokens, reasoning_effort=self._profile.reasoning_selection, images=images)
        text: list[str] = []
        size = 0

        def emit(delta: str) -> None:
            nonlocal size
            size += len(delta.encode())
            if size > MAX_TEXT_BYTES:
                raise failure("protocol_error", "qoder_output_too_large", progress=True)
            if cancel.is_set():
                raise failure("sampling_canceled", "qoder_canceled", progress=bool(text))
            text.append(delta)
            on_text_delta(delta)

        event = self._kernel.call(run_model_request, self._driver.request,
                                  request, cancel, emit, self._profile.request_timeout_seconds)
        if cancel.is_set():
            raise failure("sampling_canceled", "qoder_canceled", progress=bool(text))
        result = event.payload
        if not isinstance(result, WorkerResult):
            raise failure("protocol_error", "qoder_terminal_event_invalid", progress=bool(text))
        usage = ModelUsage(input_tokens=result.input_tokens, output_tokens=result.output_tokens,
            credits_usage=CreditsUsage(amount=result.credits, source="request") if result.credits is not None else None)
        calls = []
        seen = set()
        definitions_by_name = {t.name: t for t in definitions}
        for call in result.calls:
            name = aliases.get(call.name.removeprefix("mcp__eidos__"))
            if (name is None or call.id in seen or len(call.id.encode()) > MAX_TOOL_CALL_ID_BYTES
                    or not call.id.strip() or len(json.dumps(call.arguments, ensure_ascii=False).encode()) > MAX_FUNCTION_ARGUMENT_BYTES):
                raise failure("protocol_error", "qoder_invalid_tool_batch", progress=bool(text), usage=usage)
            seen.add(call.id)
            if required_tool is not None:
                validator = Draft202012Validator(definitions_by_name[name].parameters_json_schema,
                    registry=Registry())
                try:
                    validator.validate(call.arguments)
                except Exception:
                    raise failure("protocol_error", "qoder_required_tool_invalid", progress=bool(text), usage=usage) from None
            calls.append(ModelToolCall(provider_call_id=call.id, name=name,
                payload=FunctionToolPayload(arguments=call.arguments)))
        if (bool(calls) != (result.finish_reason == "tool_use")
                or required_tool is not None and (len(calls) != 1 or calls[0].name != required_tool)):
            raise failure("protocol_error", "qoder_required_tool_missing", progress=bool(text), usage=usage)
        return ModelResponse(text="".join(text), tool_calls=tuple(calls), wire_api="qoder_sdk",
            end_turn=not calls, provider_name="qoder", resolved_model_name=request.model_id,
            finish_reason="tool_call" if calls else "stop", provider_response_id=result.response_id,
            response_state="complete", usage=usage)

    def close(self) -> None:
        self._kernel.call(self._driver.close)

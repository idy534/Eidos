from __future__ import annotations

import asyncio
from concurrent.futures import Future
from contextlib import suppress
from dataclasses import replace
import hashlib
import json
import threading
from typing import Any, Coroutine

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI
from pydantic_ai.direct import model_request_stream
from pydantic_ai.exceptions import (
    IncompleteToolCall,
    ModelAPIError,
    ModelHTTPError,
    UnexpectedModelBehavior,
)
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest as PAIModelRequest,
    ModelResponse as PAIModelResponse,
    PartDeltaEvent,
    PartStartEvent,
    TextPart,
    TextPartDelta,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import Model, ModelRequestParameters, StreamedResponse
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.deepseek import DeepSeekProvider
from pydantic_ai.settings import ModelSettings
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.usage import RequestUsage

from eidos_runtime.model.client import (
    ModelContextItem,
    ModelProfileSnapshot,
    ModelRequestError,
    ModelRequestFailure,
    ModelResponse,
    ModelToolCall,
    ModelToolDefinition,
    ModelUsage,
)
from eidos_runtime.model.config import MODEL_CATALOG, PROVIDER, ModelProfileSpec
from eidos_runtime.model.prompts import SYSTEM_PROMPT, TITLE_PROMPT


MAX_TOOL_CALL_ID_BYTES = 256
MAX_TOOL_NAME_BYTES = 256
MAX_TOOL_ARGUMENT_BYTES = 64 * 1024


class _AsyncLoop:
    def __init__(self, name: str) -> None:
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        self.thread = threading.Thread(
            target=self._run,
            name=name,
            daemon=True,
        )
        self.thread.start()
        self.ready.wait()

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.ready.set()
        self.loop.run_forever()
        self.loop.close()

    def run(self, coroutine: Coroutine[Any, Any, Any]) -> Any:
        if not self.thread.is_alive():
            coroutine.close()
            raise RuntimeError("model client is closed")
        future: Future[Any] = asyncio.run_coroutine_threadsafe(coroutine, self.loop)
        return future.result()

    def close(self) -> None:
        if not self.thread.is_alive():
            return
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(timeout=5.0)
        if self.thread.is_alive():
            raise RuntimeError("model event loop did not stop")


class PydanticAIModelClient:
    """Eidos ModelClient backed only by Pydantic AI's public Direct Model API."""

    def __init__(
        self,
        model: Model,
        profile_spec: ModelProfileSpec,
        *,
        openai_client: AsyncOpenAI | None = None,
    ) -> None:
        self._model = model
        self._profile_spec = profile_spec
        self._openai_client = openai_client
        self._loop = _AsyncLoop(f"eidos-model-{profile_spec.provider_id}-{profile_spec.model_id}")
        self._closed = False
        self._profile_snapshot = profile_spec.snapshot(dict(model.profile))

    @classmethod
    def deepseek(cls, api_key: str, model_id: str) -> PydanticAIModelClient:
        profile = MODEL_CATALOG.profile(model_id)
        timeout = httpx.Timeout(
            connect=10.0,
            read=profile.request_timeout_seconds,
            write=30.0,
            pool=10.0,
        )
        client = AsyncOpenAI(
            api_key=api_key,
            base_url="https://api.deepseek.com",
            max_retries=0,
            timeout=timeout,
        )
        model = OpenAIChatModel(
            model_id,
            provider=DeepSeekProvider(openai_client=client),
        )
        return cls(model, profile, openai_client=client)

    @property
    def profile_snapshot(self) -> ModelProfileSnapshot:
        return self._profile_snapshot

    @property
    def sdk_max_retries(self) -> int | None:
        return self._openai_client.max_retries if self._openai_client is not None else None

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
        on_text_delta,
        allow_tools: bool = True,
        tool_definitions: tuple[ModelToolDefinition, ...] = (),
    ) -> ModelResponse:
        if cancel.is_set():
            raise ModelRequestError(_cancelled_failure())
        try:
            return self._loop.run(self._complete(
                context,
                cancel,
                on_text_delta,
                allow_tools,
                tool_definitions,
            ))
        except ModelRequestError:
            raise
        except (
            ModelHTTPError,
            ModelAPIError,
            UnexpectedModelBehavior,
            IncompleteToolCall,
            httpx.TimeoutException,
            httpx.NetworkError,
            APITimeoutError,
            APIConnectionError,
            APIStatusError,
        ) as error:
            failure = _cancelled_failure() if cancel.is_set() else map_model_error(error)
            raise ModelRequestError(failure) from None
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
        on_text_delta,
        allow_tools: bool,
        tool_definitions: tuple[ModelToolDefinition, ...],
    ) -> ModelResponse:
        settings = ModelSettings(
            max_tokens=self._profile_spec.max_output_tokens,
            timeout=self._profile_spec.request_timeout_seconds,
            parallel_tool_calls=True,
            thinking=False,
            extra_body={"thinking": {"type": "disabled"}},
        )
        parameters = ModelRequestParameters(
            function_tools=(
                [encode_tool_definition(tool) for tool in tool_definitions]
                if allow_tools else []
            ),
            allow_text_output=True,
            thinking=False,
        )
        async with model_request_stream(
            self._model,
            encode_context(context),
            model_settings=settings,
            model_request_parameters=parameters,
            instrument=False,
        ) as stream:
            cancel_task = asyncio.create_task(_cancel_when_requested(cancel, stream))
            try:
                async for event in stream:
                    if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
                        if event.part.content:
                            on_text_delta(event.part.content)
                    elif isinstance(event, PartDeltaEvent) and isinstance(
                        event.delta, TextPartDelta
                    ):
                        if event.delta.content_delta:
                            on_text_delta(event.delta.content_delta)
                response = stream.get()
            finally:
                cancel_task.cancel()
                with suppress(asyncio.CancelledError):
                    await cancel_task
        if cancel.is_set():
            raise ModelRequestError(_cancelled_failure())
        return map_model_response(response)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._openai_client is not None:
                self._loop.run(self._openai_client.close())
        finally:
            self._loop.close()


class ModelClientFactory:
    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._clients: dict[tuple[str, str], PydanticAIModelClient] = {}
        self._lock = threading.RLock()

    def client_for(self, model_id: str) -> PydanticAIModelClient:
        key = (PROVIDER, model_id)
        with self._lock:
            client = self._clients.get(key)
            if client is None:
                client = PydanticAIModelClient.deepseek(self._api_key, model_id)
                self._clients[key] = client
            return client

    def close(self) -> None:
        with self._lock:
            clients = tuple(self._clients.values())
            self._clients.clear()
        for client in clients:
            client.close()


def encode_context(context: tuple[ModelContextItem, ...]) -> list[ModelMessage]:
    messages: list[ModelMessage] = []
    for item in context:
        item_type = item.get("type")
        if item_type == "user":
            content = item.get("content")
            if isinstance(content, str):
                messages.append(PAIModelRequest([UserPromptPart(content)]))
        elif item_type == "assistant":
            content = item.get("content")
            if isinstance(content, str):
                messages.append(PAIModelResponse([TextPart(content)]))
        elif item_type == "tool_call":
            call_id = item.get("callId")
            name = item.get("name")
            arguments = item.get("arguments")
            if all(isinstance(value, str) for value in (call_id, name, arguments)):
                messages.append(PAIModelResponse([
                    ToolCallPart(name, arguments, call_id)
                ]))
        elif item_type == "tool_result":
            call_id = item.get("callId")
            name = item.get("name")
            result = item.get("result")
            if all(isinstance(value, str) for value in (call_id, name, result)):
                messages.append(PAIModelRequest([
                    ToolReturnPart(name, result, call_id)
                ]))
        elif item_type == "protocol_error":
            code = item.get("code")
            if isinstance(code, str):
                messages.append(PAIModelRequest([UserPromptPart(
                    f"Your previous response was invalid ({code}). "
                    "Try again using the provided tool schemas."
                )]))

    for index, message in enumerate(messages):
        if isinstance(message, PAIModelRequest):
            messages[index] = replace(message, instructions=SYSTEM_PROMPT)
            break
    else:
        messages.insert(0, PAIModelRequest([], instructions=SYSTEM_PROMPT))
    return messages


def encode_tool_definition(definition: ModelToolDefinition) -> ToolDefinition:
    return ToolDefinition(
        name=definition.name,
        description=definition.description,
        parameters_json_schema=definition.parameters_json_schema,
    )


def map_model_response(response: PAIModelResponse) -> ModelResponse:
    calls: list[ModelToolCall] = []
    for index, call in enumerate(response.tool_calls):
        try:
            arguments = call.args_as_dict(raise_if_invalid=True)
        except (ValueError, AssertionError):
            raise ModelRequestError(ModelRequestFailure(
                code="protocol_error",
                retryable=False,
                provider_name=response.provider_name,
            )) from None
        encoded = json.dumps(
            arguments,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        call_id = call.tool_call_id or _stable_call_id(index, call.tool_name, encoded)
        if (
            not call_id
            or len(call_id.encode("utf-8")) > MAX_TOOL_CALL_ID_BYTES
            or not call.tool_name
            or len(call.tool_name.encode("utf-8")) > MAX_TOOL_NAME_BYTES
            or len(encoded) > MAX_TOOL_ARGUMENT_BYTES
        ):
            raise ModelRequestError(ModelRequestFailure(
                code="protocol_error",
                retryable=False,
                provider_name=response.provider_name,
            ))
        calls.append(ModelToolCall(call_id, call.tool_name, arguments))
    return ModelResponse(
        text=response.text or "",
        tool_calls=tuple(calls),
        usage=_map_usage(response.usage),
        provider_name=response.provider_name,
        resolved_model_name=response.model_name,
        finish_reason=response.finish_reason or ("tool_call" if calls else "unknown"),
        provider_response_id=response.provider_response_id,
        response_state=response.state,
    )


def _map_usage(usage: RequestUsage) -> ModelUsage:
    return ModelUsage(
        input_tokens=usage.input_tokens or None,
        output_tokens=usage.output_tokens or None,
        cache_read_tokens=usage.cache_read_tokens or None,
        cache_write_tokens=usage.cache_write_tokens or None,
        details={
            key: value
            for key, value in usage.details.items()
            if isinstance(key, str) and isinstance(value, int) and value >= 0
        },
    )


def map_model_error(error: BaseException) -> ModelRequestFailure:
    if isinstance(error, APITimeoutError | httpx.TimeoutException):
        return ModelRequestFailure(code="provider_timeout", retryable=True)
    if isinstance(error, APIConnectionError | httpx.NetworkError):
        return ModelRequestFailure(code="provider_unavailable", retryable=True)
    if isinstance(error, APIStatusError):
        return _http_failure(error.status_code)
    if isinstance(error, ModelHTTPError):
        context_exceeded = error.status_code == 413 or (
            error.status_code == 400 and _looks_like_context_error(error.body)
        )
        return _http_failure(error.status_code, context_exceeded=context_exceeded)
    if isinstance(error, (IncompleteToolCall, UnexpectedModelBehavior)):
        return ModelRequestFailure(code="protocol_error", retryable=False)
    if isinstance(error, ModelAPIError):
        return ModelRequestFailure(code="invalid_request", retryable=False)
    return ModelRequestFailure(code="protocol_error", retryable=False)


def _http_failure(
    status_code: int,
    *,
    context_exceeded: bool = False,
) -> ModelRequestFailure:
    if context_exceeded or status_code == 413:
        code, retryable = "context_exceeded", False
    elif status_code in {401, 403}:
        code, retryable = "authentication_failed", False
    elif status_code == 429:
        code, retryable = "rate_limited", True
    elif status_code in {408, 425, 500, 502, 503, 504}:
        code, retryable = "provider_unavailable", True
    elif status_code in {400, 404, 422}:
        code, retryable = "invalid_request", False
    else:
        code, retryable = "invalid_request", False
    return ModelRequestFailure(
        code=code,
        retryable=retryable,
        status_code=status_code,
    )


def _looks_like_context_error(body: object) -> bool:
    text = str(body).lower()
    return any(marker in text for marker in (
        "context length",
        "context_length",
        "maximum context",
        "too many tokens",
    ))


def _stable_call_id(index: int, name: str, arguments: bytes) -> str:
    digest = hashlib.sha256(
        str(index).encode("ascii") + b"\0" + name.encode("utf-8") + b"\0" + arguments
    ).hexdigest()
    return f"pyd_ai_{digest[:32]}"


async def _cancel_when_requested(
    cancel: threading.Event,
    stream: StreamedResponse,
) -> None:
    while not cancel.is_set():
        await asyncio.sleep(0.025)
    await stream.cancel()


def _cancelled_failure() -> ModelRequestFailure:
    return ModelRequestFailure(code="sampling_canceled", retryable=False)

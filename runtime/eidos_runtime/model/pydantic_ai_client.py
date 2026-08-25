from __future__ import annotations

import asyncio
from contextlib import nullcontext, suppress
from dataclasses import replace
import hashlib
import inspect
import json
import threading
from typing import Any, Callable

import anyio
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
    BinaryContent,
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
    ModelClient,
    ModelContextItem,
    ModelProfileSnapshot,
    ModelRequestError,
    ModelRequestFailure,
    ModelResponse,
    ModelToolCall,
    ModelToolDefinition,
    ModelUsage,
)
from eidos_runtime.model.config import (
    MODEL_CATALOG,
    ModelProfileSpec,
)
from eidos_runtime.model.prompts import TITLE_PROMPT, TITLE_SYSTEM_INSTRUCTIONS
from eidos_runtime.model.response_phase import resolve_chat_completion_phase
from eidos_runtime.model_gateway.retry_transport import (
    RetryBackoffCanceled,
    RetryTracker,
    RetryTransportClient,
)
from eidos_runtime.runtime.resource_registry import (
    ResourceRegistry,
    RuntimeResource,
    RuntimeResourceKind,
)
from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel
from eidos_runtime.runtime.fault_injection import hit_fault
from eidos_runtime.tools.view_image import (
    ViewImageAuthority,
    ViewImageError,
    read_authorized_image,
    resolve_view_image_authority,
)


MAX_TOOL_CALL_ID_BYTES = 256
MAX_TOOL_NAME_BYTES = 256
MAX_TOOL_ARGUMENT_BYTES = 64 * 1024
USAGE_DETAIL_KEYS = frozenset({
    "accepted_prediction_tokens",
    "audio_tokens",
    "reasoning_tokens",
    "rejected_prediction_tokens",
})


class PydanticAIModelClient:
    """Eidos ModelClient backed only by Pydantic AI's public Direct Model API."""

    def __init__(
        self,
        model: Model,
        profile_spec: ModelProfileSpec,
        *,
        openai_client: AsyncOpenAI | None = None,
        provider_client: Any | None = None,
        retry_transport: RetryTransportClient | None = None,
        profile_snapshot: ModelProfileSnapshot | None = None,
        settings_extra_body: dict[str, object] | None = None,
        parallel_tool_calls: bool | None = True,
        reasoning_effort: str | None = None,
        image_authority: ViewImageAuthority | None = None,
        async_kernel: RuntimeAsyncKernel,
    ) -> None:
        self._model = model
        self._profile_spec = profile_spec
        self._openai_client = openai_client
        self._provider_client = provider_client or openai_client
        self._retry_transport = retry_transport
        self._settings_extra_body = (
            settings_extra_body
            if settings_extra_body is not None
            else (
                {"thinking": {"type": "disabled"}}
                if profile_spec.provider_id == "deepseek"
                else None
            )
        )
        self._parallel_tool_calls = parallel_tool_calls
        self._reasoning_effort = reasoning_effort
        self._image_authority = image_authority
        self._async_kernel = async_kernel
        self._closed = False
        self._lock = threading.RLock()
        self._profile_snapshot = (
            profile_snapshot or profile_spec.snapshot(dict(model.profile))
        )

    def set_image_authority_provider(
        self, authority: ViewImageAuthority | None
    ) -> None:
        """Bind the current Run's dynamic image-root authority."""

        with self._lock:
            self._image_authority = authority

    @classmethod
    def deepseek(
        cls,
        api_key: str,
        model_id: str,
        *,
        async_kernel: RuntimeAsyncKernel,
    ) -> PydanticAIModelClient:
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
        return cls(
            model,
            profile,
            openai_client=client,
            settings_extra_body={"thinking": {"type": "disabled"}},
            async_kernel=async_kernel,
        )

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
            instructions=TITLE_SYSTEM_INSTRUCTIONS,
            allow_tools=False,
        ).text

    def complete(
        self,
        context: tuple[ModelContextItem, ...],
        cancel: threading.Event,
        on_text_delta,
        *,
        instructions: str,
        allow_tools: bool = True,
        tool_definitions: tuple[ModelToolDefinition, ...] = (),
    ) -> ModelResponse:
        with self._lock:
            if self._closed:
                raise RuntimeError("model client is closed")
        if cancel.is_set():
            raise ModelRequestError(_cancelled_failure())
        retry_tracker = RetryTracker()
        try:
            return self._async_kernel.call(
                self._complete,
                context,
                cancel,
                on_text_delta,
                instructions,
                allow_tools,
                tool_definitions,
                retry_tracker,
            )
        except RetryBackoffCanceled:
            raise ModelRequestError(
                _with_retry_diagnostics(_cancelled_failure(), retry_tracker)
            ) from None
        except ModelRequestError:
            raise
        except (
            ModelHTTPError,
            ModelAPIError,
            UnexpectedModelBehavior,
            IncompleteToolCall,
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.HTTPStatusError,
            APITimeoutError,
            APIConnectionError,
            APIStatusError,
        ) as error:
            failure = _cancelled_failure() if cancel.is_set() else map_model_error(error)
            if failure.provider_name is None:
                failure = failure.model_copy(update={
                    "provider_name": self._profile_spec.provider_id
                })
            raise ModelRequestError(
                _with_retry_diagnostics(failure, retry_tracker)
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
        on_text_delta,
        instructions: str,
        allow_tools: bool,
        tool_definitions: tuple[ModelToolDefinition, ...],
        retry_tracker: RetryTracker,
    ) -> ModelResponse:
        hit_fault("model_stream_block")
        settings_values: dict[str, object] = {
            "max_tokens": self._profile_spec.max_output_tokens,
            "timeout": self._profile_spec.request_timeout_seconds,
        }
        if self._parallel_tool_calls is not None:
            settings_values["parallel_tool_calls"] = self._parallel_tool_calls
        if self._settings_extra_body is not None:
            settings_values["extra_body"] = self._settings_extra_body
        if self._reasoning_effort is not None:
            settings_values["reasoning_effort"] = self._reasoning_effort
        settings = ModelSettings(**settings_values)
        parameters = ModelRequestParameters(
            function_tools=(
                [encode_tool_definition(tool) for tool in tool_definitions]
                if allow_tools else []
            ),
            allow_text_output=True,
        )
        retry_scope = (
            self._retry_transport.request_scope(cancel, retry_tracker)
            if self._retry_transport is not None
            else nullcontext()
        )
        with retry_scope:
            async with model_request_stream(
                self._model,
                _attach_instructions(
                    encode_context(
                        context,
                        supports_images=self._profile_snapshot.supports_images,
                        image_authority=self._image_authority,
                    ),
                    instructions,
                ),
                model_settings=settings,
                model_request_parameters=parameters,
                instrument=False,
            ) as stream:
                cancel_task = asyncio.create_task(_cancel_when_requested(cancel, stream))
                try:
                    async for event in stream:
                        if isinstance(event, PartStartEvent) and isinstance(event.part, TextPart):
                            if event.part.content:
                                await anyio.to_thread.run_sync(
                                    on_text_delta, event.part.content
                                )
                        elif isinstance(event, PartDeltaEvent) and isinstance(
                            event.delta, TextPartDelta
                        ):
                            if event.delta.content_delta:
                                await anyio.to_thread.run_sync(
                                    on_text_delta, event.delta.content_delta
                                )
                    response = stream.get()
                finally:
                    cancel_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await cancel_task
        if cancel.is_set():
            raise ModelRequestError(_cancelled_failure())
        return map_model_response(response, retry_tracker=retry_tracker)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            provider_client = self._provider_client
        if provider_client is not None:
            self._async_kernel.call(_close_provider_client, provider_client)
        with self._lock:
            self._closed = True


class ModelClientLease:
    def __init__(
        self,
        client: ModelClient,
        release: Callable[[], None] | None = None,
        *,
        resource_registry: ResourceRegistry | None = None,
        owner_id: str = "model",
    ) -> None:
        self.client = client
        self._release = release
        self._lock = threading.Lock()
        self._closed = False
        self._resource: RuntimeResource | None = (
            resource_registry.register(
                RuntimeResourceKind.MODEL_LEASE,
                owner_id=owner_id,
            )
            if resource_registry is not None
            else None
        )
        if self._resource is not None:
            self._resource.start()

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            release = self._release
            self._release = None
        if release is not None:
            release()
        if self._resource is not None:
            self._resource.close()


async def _close_provider_client(provider_client: Any) -> None:
    close = getattr(provider_client, "close", None)
    if not callable(close):
        return
    result = close()
    if inspect.isawaitable(result):
        await result


def encode_context(
    context: tuple[ModelContextItem, ...],
    *,
    supports_images: bool = False,
    image_authority: ViewImageAuthority | None = None,
) -> list[ModelMessage]:
    messages: list[ModelMessage] = []
    for item in context:
        item_type = item.get("type", item.get("role"))
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
                content: object = result
                if name == "view_image" and supports_images:
                    content = _encode_view_image_result(
                        result,
                        image_authority=image_authority,
                    )
                messages.append(PAIModelRequest([
                    ToolReturnPart(name, content, call_id)
                ]))
        elif item_type == "protocol_error":
            code = item.get("code")
            if isinstance(code, str):
                messages.append(PAIModelRequest([UserPromptPart(
                    f"Your previous response was invalid ({code}). "
                    "Try again using the provided tool schemas."
                )]))
        elif item_type == "tool_error":
            code = item.get("code")
            if isinstance(code, str):
                messages.append(PAIModelRequest([UserPromptPart(
                    "Runtime tool error data: " + json.dumps(
                        {"code": code},
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                )]))
        elif item_type == "finalization":
            reason = item.get("stopReason")
            if isinstance(reason, str):
                messages.append(PAIModelRequest([UserPromptPart(
                    "Runtime finalization state data: " + json.dumps(
                        {
                            "stopReason": reason,
                            "toolsAllowed": item.get("toolsAllowed") is True,
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    )
                )]))
        else:
            raise ValueError(f"unsupported model context item type: {item_type!r}")

    return messages


def _encode_view_image_result(
    result: str,
    *,
    image_authority: ViewImageAuthority | None,
) -> str | list[object]:
    if image_authority is None:
        raise ValueError("view_image_projection_failed:missing_authority")
    authority = resolve_view_image_authority(image_authority)
    try:
        decoded = json.loads(result)
    except json.JSONDecodeError as error:
        raise ValueError("view_image_projection_failed:invalid_result") from error
    if not isinstance(decoded, dict) or decoded.get("outcome") != "success":
        return result
    data = decoded.get("data")
    if not isinstance(data, dict):
        raise ValueError("view_image_projection_failed:invalid_metadata")
    path = data.get("path")
    mime = data.get("mime")
    size = data.get("size")
    sha256 = data.get("sha256")
    if (
        not isinstance(path, str)
        or mime not in {"image/png", "image/jpeg"}
        or not isinstance(size, int)
        or isinstance(size, bool)
        or size < 1
        or not isinstance(sha256, str)
        or len(sha256) != 64
        or any(character not in "0123456789abcdef" for character in sha256)
    ):
        raise ValueError("view_image_projection_failed:invalid_metadata")
    try:
        image = read_authorized_image(path, authority)
    except ViewImageError as error:
        raise ValueError(
            f"view_image_projection_failed:{error.code}"
        ) from error
    if image.mime != mime or image.size != size or image.sha256 != sha256:
        raise ValueError("view_image_result_changed")
    return [
        json.dumps(decoded, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
        BinaryContent(data=image.data, media_type=image.mime),
    ]


def _attach_instructions(
    messages: list[ModelMessage],
    instructions: str,
) -> list[ModelMessage]:
    for index, message in enumerate(messages):
        if isinstance(message, PAIModelRequest):
            messages[index] = replace(message, instructions=instructions)
            break
    else:
        messages.insert(0, PAIModelRequest([], instructions=instructions))
    return messages


def encode_tool_definition(definition: ModelToolDefinition) -> ToolDefinition:
    return ToolDefinition(
        name=definition.name,
        description=definition.description,
        parameters_json_schema=definition.parameters_json_schema,
    )


def map_model_response(
    response: PAIModelResponse,
    *,
    retry_tracker: RetryTracker | None = None,
) -> ModelResponse:
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
    text = response.text or ""
    finish_reason = response.finish_reason or ("tool_call" if calls else "unknown")
    phase = resolve_chat_completion_phase(
        text=text,
        has_tool_calls=bool(calls),
        finish_reason=finish_reason,
    )
    return ModelResponse(
        text=text,
        tool_calls=tuple(calls),
        phase=phase,
        usage=_map_usage(response.usage),
        provider_name=response.provider_name,
        resolved_model_name=response.model_name,
        finish_reason=finish_reason,
        provider_response_id=response.provider_response_id,
        response_state=response.state,
        transport_attempt_count=(
            retry_tracker.transport_attempt_count if retry_tracker is not None else 0
        ),
        transport_retry_count=(
            retry_tracker.transport_retry_count if retry_tracker is not None else 0
        ),
        last_retry_reason=(
            retry_tracker.last_retry_reason if retry_tracker is not None else None
        ),
        last_backoff_seconds=(
            retry_tracker.last_backoff_seconds if retry_tracker is not None else None
        ),
        retry_after_applied=(
            retry_tracker.retry_after_applied if retry_tracker is not None else False
        ),
    )


def _map_usage(usage: RequestUsage) -> ModelUsage:
    return ModelUsage(
        input_tokens=usage.input_tokens if usage.input_tokens is not None else None,
        output_tokens=usage.output_tokens if usage.output_tokens is not None else None,
        cache_read_tokens=(
            usage.cache_read_tokens
            if usage.cache_read_tokens is not None else None
        ),
        cache_write_tokens=(
            usage.cache_write_tokens
            if usage.cache_write_tokens is not None else None
        ),
        details={
            key: value
            for key, value in usage.details.items()
            if key in USAGE_DETAIL_KEYS
            and isinstance(value, int)
            and value >= 0
        },
    )


def map_model_error(error: BaseException) -> ModelRequestFailure:
    if isinstance(error, APITimeoutError | httpx.TimeoutException):
        return ModelRequestFailure(code="provider_timeout", retryable=True)
    if isinstance(error, APIConnectionError | httpx.NetworkError):
        return ModelRequestFailure(code="provider_unavailable", retryable=True)
    if isinstance(error, httpx.HTTPStatusError):
        return _http_failure(error.response.status_code)
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


def _with_retry_diagnostics(
    failure: ModelRequestFailure,
    tracker: RetryTracker,
) -> ModelRequestFailure:
    return failure.model_copy(update={
        "transport_attempt_count": tracker.transport_attempt_count,
        "transport_retry_count": tracker.transport_retry_count,
        "last_retry_reason": tracker.last_retry_reason,
        "last_backoff_seconds": tracker.last_backoff_seconds,
        "retry_after_applied": tracker.retry_after_applied,
    })


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
    hit_fault("model_cancel_delay")
    await stream.cancel()


def _cancelled_failure() -> ModelRequestFailure:
    return ModelRequestFailure(code="sampling_canceled", retryable=False)

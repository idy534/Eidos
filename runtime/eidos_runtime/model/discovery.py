"""User-triggered custom-provider listing. This never samples a model."""
from __future__ import annotations

import json
import threading
from typing import Literal, get_args

import anyio
import httpx2 as httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI, Omit
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, ValidationError, ValidatorFunctionWrapHandler, field_validator

from eidos_runtime.model.config import (
    ModelReasoningConfig, ModelReasoningSelection, normalize_model_base_url,
)
from eidos_runtime.models import EidosFrozenStrictModel

MAX_DISCOVERY_BYTES = 1024 * 1024
MAX_DISCOVERY_MODELS = 500


class CustomProviderRequest(EidosFrozenStrictModel):
    model_config = ConfigDict(hide_input_in_errors=True)
    provider: Literal["custom"]
    base_url: str = Field(min_length=1, max_length=2048)
    api_key: str | None = Field(default=None, max_length=512, repr=False)
    config_id: str | None = Field(default=None, min_length=1, max_length=256)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        return normalize_model_base_url(value)

    @field_validator("api_key")
    @classmethod
    def validate_key(cls, value: str | None) -> str | None:
        if value is not None and any(character.isspace() or ord(character) < 32 for character in value):
            raise ValueError("API Key 不能包含空白或控制字符。")
        return value


class DiscoveredModel(EidosFrozenStrictModel):
    id: str = Field(min_length=1, max_length=256)
    name: str | None = Field(default=None, max_length=120)
    context_window_tokens: int | None = None
    max_output_tokens: int | None = None
    supports_images: bool | None = None
    supports_tool_call: bool | None = None
    reasoning: ModelReasoningConfig | None = None
    reasoning_selections: list[ModelReasoningSelection] | None = Field(default=None, max_length=20)


class CustomProviderResult(EidosFrozenStrictModel):
    state: Literal["success", "unavailable", "error", "canceled"]
    message: str
    models: list[DiscoveredModel] = Field(default_factory=list, max_length=MAX_DISCOVERY_MODELS)
    truncated: bool = False


def _known_remote_levels(value: object) -> object:
    if not isinstance(value, list):
        return value
    return [level for level in value if isinstance(level, str) and level in get_args(ModelReasoningSelection)]


class _RemoteEffort(BaseModel):
    supported_levels: list[ModelReasoningSelection] = Field(max_length=20, validation_alias=AliasChoices("supported_levels", "supportedLevels", "selections"))
    default_level: ModelReasoningSelection | None = Field(default=None, validation_alias=AliasChoices("default_level", "defaultLevel", "defaultSelection", "default_selection"))

    @field_validator("supported_levels", mode="before")
    @classmethod
    def known_levels(cls, value: object) -> object:
        return _known_remote_levels(value)

    @field_validator("default_level", mode="before")
    @classmethod
    def known_default(cls, value: object) -> object:
        return value if isinstance(value, str) and value in get_args(ModelReasoningSelection) else None


class _RemoteArchitecture(BaseModel):
    input_modalities: list[str] | None = Field(default=None, max_length=20)


class _RemoteModalities(BaseModel):
    input: list[str] | None = Field(default=None, max_length=20)


class _RemoteProvider(BaseModel):
    max_completion_tokens: int | None = None


class _RemoteModel(BaseModel):
    """Only documented listing metadata is read; all other data is ignored."""
    id: str = Field(strict=True, min_length=1, max_length=256)
    name: str | None = Field(default=None, strict=True, max_length=120, validation_alias=AliasChoices("name", "display_name", "displayName"))
    context_window: int | None = Field(default=None, validation_alias=AliasChoices("context_window", "context_window_tokens", "contextWindowTokens"))
    context_length: int | None = None
    max_output_tokens: int | None = Field(default=None, validation_alias=AliasChoices("max_output_tokens", "maxOutputTokens"))
    input_modalities: list[str] | None = Field(default=None, max_length=20)
    architecture: _RemoteArchitecture | None = None
    modalities: _RemoteModalities | None = None
    top_provider: _RemoteProvider | None = None
    effort: _RemoteEffort | None = None
    reasoning: _RemoteEffort | None = None
    reasoning_selections: list[ModelReasoningSelection] | None = Field(default=None, max_length=20, validation_alias=AliasChoices("reasoning_selections", "reasoningSelections"))
    supported_reasoning_levels: list[ModelReasoningSelection] | None = Field(default=None, max_length=20, validation_alias=AliasChoices("supported_reasoning_levels", "supportedReasoningLevels"))
    supported_parameters: list[str] | None = Field(default=None, max_length=100)

    @field_validator("reasoning_selections", mode="before")
    @classmethod
    def known_selections(cls, value: object) -> object:
        return _known_remote_levels(value)

    @field_validator("supported_reasoning_levels", mode="before")
    @classmethod
    def known_supported_levels(cls, value: object) -> object:
        if isinstance(value, list):
            return _known_remote_levels([level.get("effort") if isinstance(level, dict) else level for level in value])
        return value

    @field_validator("name", "context_window", "context_length", "max_output_tokens", "input_modalities",
                     "architecture", "modalities", "top_provider", "effort", "reasoning", "reasoning_selections", "supported_reasoning_levels", "supported_parameters", mode="wrap")
    @classmethod
    def optional_metadata(cls, value: object, handler: ValidatorFunctionWrapHandler) -> object:
        try:
            return handler(value)
        except ValidationError:
            return None

    def public(self) -> DiscoveredModel:
        modalities = self.input_modalities
        if modalities is None and self.architecture is not None:
            modalities = self.architecture.input_modalities
        if modalities is None and self.modalities is not None:
            modalities = self.modalities.input
        reasoning = None
        levels = tuple(dict.fromkeys(self.reasoning_selections or self.supported_reasoning_levels or ())) or None
        metadata = self.reasoning if self.reasoning and self.reasoning.supported_levels else self.effort
        if metadata and metadata.supported_levels:
            levels = tuple(dict.fromkeys(metadata.supported_levels))
            default = metadata.default_level
            if default is not None and default in levels:
                reasoning = ModelReasoningConfig(default_selection=default, selections=levels)
        output = self.max_output_tokens
        if output is None and self.top_provider:
            output = self.top_provider.max_completion_tokens
        return DiscoveredModel(
            id=self.id, name=self.name,
            context_window_tokens=_token_limit(self.context_window or self.context_length),
            max_output_tokens=_token_limit(output),
            supports_images="image" in modalities if modalities is not None else None,
            supports_tool_call="tools" in self.supported_parameters if self.supported_parameters is not None else None,
            reasoning=reasoning,
            reasoning_selections=list(levels) if levels else None,
        )


def _token_limit(value: int | None) -> int | None:
    return value if value is not None and 0 < value <= 100_000_000 else None


async def discover_custom_models(
    base_url: str, api_key: str, cancel: threading.Event,
    *, transport: httpx.AsyncBaseTransport | None = None,
) -> CustomProviderResult:
    async def watch_cancel(scope: anyio.CancelScope) -> None:
        while not cancel.is_set():
            await anyio.sleep(0.05)
        scope.cancel()

    try:
        with anyio.move_on_after(12) as scope:
            async with anyio.create_task_group() as group:
                group.start_soon(watch_cancel, scope)
                try:
                    result = await _fetch_models(base_url, api_key, transport=transport)
                finally:
                    group.cancel_scope.cancel()
            if not cancel.is_set():
                return result
    except ExceptionGroup as error:
        # TaskGroup preserves network failures in a group. No remote error body is exposed.
        failure = error.exceptions[0]
        return _failure(failure)
    except (APIStatusError, APIConnectionError, APITimeoutError, ValueError) as error:
        return _failure(error)
    return CustomProviderResult(
        state="canceled" if cancel.is_set() else "error",
        message="连接测试已取消。" if cancel.is_set() else "连接超时，请检查接口地址和服务状态。",
    )


def _failure(error: BaseException) -> CustomProviderResult:
    if isinstance(error, APIStatusError):
        if error.status_code in {404, 405}:
            return CustomProviderResult(state="unavailable", message="此接口未提供模型列表，你可以手动填写模型 ID。")
        if error.status_code in {401, 403}:
            message = "接口拒绝了当前凭据，请检查 API Key 或访问权限。"
        elif error.status_code == 429:
            message = "接口请求过于频繁，请稍后重试。"
        else:
            message = "接口暂时无法提供模型列表，请检查服务状态。"
    elif isinstance(error, APITimeoutError):
        message = "连接超时，请检查接口地址和服务状态。"
    elif isinstance(error, APIConnectionError):
        message = "无法连接该地址，请检查地址、证书和服务状态。"
    elif isinstance(error, ValueError):
        message = "接口返回的内容无法识别为模型列表，你可以手动填写模型 ID。"
    else:
        message = "连接测试未完成，请查看 Runtime 诊断。"
    return CustomProviderResult(state="error", message=message)


async def _fetch_models(base_url: str, api_key: str, *, transport: httpx.AsyncBaseTransport | None) -> CustomProviderResult:
    headers = {} if api_key else {"Authorization": Omit()}
    async with httpx.AsyncClient(transport=transport, timeout=10, follow_redirects=False) as http_client:
        async with AsyncOpenAI(
            base_url=base_url, api_key=api_key or "unused-no-auth", admin_api_key="",
            organization="", project="",
            max_retries=0, http_client=http_client,
        ) as client:
            async with client.models.with_streaming_response.list(extra_headers=headers) as response:
                raw = bytearray()
                async for chunk in response.iter_bytes():
                    raw.extend(chunk)
                    if len(raw) > MAX_DISCOVERY_BYTES:
                        raise ValueError("model listing exceeds size limit")
    payload = json.loads(raw)
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("missing model data array")
    models: list[DiscoveredModel] = []
    seen: set[str] = set()
    for row in payload["data"][:MAX_DISCOVERY_MODELS]:
        try:
            remote = _RemoteModel.model_validate(row)
        except ValidationError:
            # Optional metadata is handled per field; invalid model identities are skipped.
            continue
        if remote.id not in seen:
            models.append(remote.public())
            seen.add(remote.id)
    if payload["data"] and not models:
        raise ValueError("no valid model IDs")
    return CustomProviderResult(
        state="success", models=models, truncated=len(payload["data"]) > MAX_DISCOVERY_MODELS,
        message=f"已获取 {len(models)} 个模型" if models else "接口返回了空模型列表，你可以手动填写模型 ID。",
    )

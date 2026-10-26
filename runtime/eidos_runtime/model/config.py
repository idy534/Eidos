from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import stat
import tempfile
import uuid
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field, field_validator, model_validator

from eidos_runtime.model.client import ModelProfileSnapshot
from eidos_runtime.models import EidosFrozenStrictModel


CONFIG_NAME = "models.json"
DEFAULT_CONTEXT_WINDOW_TOKENS = 128_000
DEFAULT_MAX_OUTPUT_TOKENS = 8_192
DEFAULT_REQUEST_TIMEOUT_SECONDS = 120.0


ModelReasoningSelection = Literal[
    "none", "thinking", "low", "medium", "high", "xhigh", "max"
]


class ModelReasoningConfig(EidosFrozenStrictModel):
    default_selection: ModelReasoningSelection | None = None
    selections: tuple[ModelReasoningSelection, ...]

    @field_validator("selections", mode="before")
    @classmethod
    def normalize_selections(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="before")
    @classmethod
    def migrate_old_effort_fields(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        old_default = normalized.pop(
            "defaultEffort", normalized.pop("default_effort", None)
        )
        old_selections = normalized.pop(
            "supportedEfforts", normalized.pop("supported_efforts", None)
        )
        if (
            "defaultSelection" not in normalized
            and "default_selection" not in normalized
        ):
            if old_default is not None:
                normalized["defaultSelection"] = old_default
        if "selections" not in normalized:
            if old_selections is not None:
                normalized["selections"] = old_selections
        return normalized

    @model_validator(mode="after")
    def validate_default_selection(self) -> "ModelReasoningConfig":
        if not self.selections or (self.default_selection is not None and self.default_selection not in self.selections):
            raise ValueError("default reasoning selection must be supported")
        if len(set(self.selections)) != len(self.selections):
            raise ValueError("reasoning selections must be unique")
        return self


WireApi = Literal["chat_completions", "openai_responses"]
ReasoningFormat = Literal["openai", "deepseek", "minimax"]


def normalize_model_base_url(value: str) -> str:
    parsed = urlsplit(value.strip())
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment
            or any(character.isspace() or ord(character) < 32 for character in value)):
        raise ValueError("接口地址必须是有效的 HTTP 或 HTTPS 地址，且不能包含凭据、查询或片段。")
    try:
        parsed.port
    except ValueError:
        raise ValueError("接口地址的端口无效。") from None
    path = parsed.path.rstrip("/")
    for suffix in ("/chat/completions", "/responses"):
        if path.endswith(suffix):
            path = path[:-len(suffix)]
            break
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


class CustomModelSettings(EidosFrozenStrictModel):
    provider_id: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
    provider_name: str = Field(min_length=1, max_length=64)
    base_url: str = Field(min_length=1, max_length=2048)
    wire_api: WireApi = "chat_completions"
    name: str = Field(min_length=1, max_length=120)
    context_window_tokens: int = Field(default=DEFAULT_CONTEXT_WINDOW_TOKENS, gt=0, le=100_000_000)
    max_output_tokens: int = Field(default=DEFAULT_MAX_OUTPUT_TOKENS, gt=0, le=100_000_000)
    supports_tool_call: bool = True
    supports_images: bool = False
    reasoning: ModelReasoningConfig | None = None
    reasoning_format: ReasoningFormat = "openai"
    reasoning_control: Literal["provider_default", "unsupported", "configured"] = "provider_default"
    output_token_field: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        return normalize_model_base_url(value)

    @model_validator(mode="after")
    def validate_limits(self) -> "CustomModelSettings":
        if self.max_output_tokens >= self.context_window_tokens:
            raise ValueError("上下文窗口必须大于最大输出。")
        if self.wire_api == "openai_responses" and self.reasoning_format != "openai":
            raise ValueError("Responses 使用标准 reasoning 参数。")
        if (self.reasoning is not None) != (self.reasoning_control == "configured"):
            raise ValueError("思考控制与可选档位不一致。")
        if self.reasoning is not None:
            if self.reasoning_format == "openai" and "thinking" in self.reasoning.selections:
                raise ValueError("标准 OpenAI 接口需要明确的思考强度。")
            if self.reasoning_format == "minimax" and not set(self.reasoning.selections) <= {"none", "thinking"}:
                raise ValueError("MiniMax 思考参数只支持开关。")
        return self


class CustomModelRuntimeSettings(EidosFrozenStrictModel):
    provider_id: str | None = Field(default=None, min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
    wire_api: WireApi = "chat_completions"
    context_window_tokens: int = Field(default=DEFAULT_CONTEXT_WINDOW_TOKENS, gt=0, le=100_000_000)
    max_output_tokens: int = Field(default=DEFAULT_MAX_OUTPUT_TOKENS, gt=0, le=100_000_000)
    reasoning_format: ReasoningFormat = "openai"
    reasoning_control: Literal["provider_default", "unsupported", "configured"] = "provider_default"
    output_token_field: Literal["max_tokens", "max_completion_tokens"] = "max_tokens"

    @model_validator(mode="after")
    def validate_limits(self) -> "CustomModelRuntimeSettings":
        if self.max_output_tokens >= self.context_window_tokens:
            raise ValueError("上下文窗口必须大于最大输出。")
        return self


class ModelConfig(EidosFrozenStrictModel):
    id: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=120)
    vendor: str = Field(min_length=1, max_length=64)
    url: str = Field(min_length=1, max_length=2048)
    api_key: str = Field(alias="apiKey", max_length=512, repr=False)
    supports_tool_call: bool = Field(alias="supportsToolCall")
    supports_images: bool = Field(default=False, alias="supportsImages")
    supports_reasoning: bool = Field(alias="supportsReasoning")
    reasoning: ModelReasoningConfig | None = None
    model_id: str | None = Field(default=None, min_length=1, max_length=256)
    custom: CustomModelRuntimeSettings | None = None

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: str) -> str:
        key = value.strip()
        if (
            any(character.isspace() or ord(character) < 32 for character in key)
        ):
            raise ValueError("API key is invalid")
        return key

    @model_validator(mode="after")
    def validate_reasoning(self) -> "ModelConfig":
        if not self.api_key and self.custom is None:
            raise ValueError("API key is required for built-in providers")
        if self.custom is not None:
            if self.model_id is None or not self.model_id.strip():
                raise ValueError("custom model requires a remote model ID")
            settings = CustomModelSettings(
                provider_name=self.vendor, base_url=self.url, name=self.name,
                supports_tool_call=self.supports_tool_call, supports_images=self.supports_images,
                reasoning=self.reasoning, **self.custom.model_dump(),
            )
            suffix = "/responses" if self.custom.wire_api == "openai_responses" else "/chat/completions"
            if self.url != settings.base_url + suffix:
                raise ValueError("custom model URL does not match wire API")
        if self.reasoning is not None and not self.supports_reasoning:
            raise ValueError("reasoning selections require reasoning capability")
        return self


class ModelPublicConfig(EidosFrozenStrictModel):
    id: str
    name: str
    vendor: str
    provider: str
    url: str
    supports_tool_call: bool = Field(alias="supportsToolCall")
    supports_images: bool = Field(default=False, alias="supportsImages")
    supports_reasoning: bool = Field(alias="supportsReasoning")
    reasoning: ModelReasoningConfig | None = None
    custom: CustomModelSettings | None = None
    model_id: str | None = None
    context_window_tokens: int | None = None
    max_output_tokens: int | None = None
    api_key: str = Field(default="", alias="apiKey", repr=False)


class ModelProfileSpec(EidosFrozenStrictModel):
    provider_id: str
    model_id: str
    wire_api: Literal["chat_completions", "openai_responses"] = "chat_completions"
    context_window_tokens: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    request_timeout_seconds: float = Field(gt=0)
    supports_images: bool = False
    supports_custom_tools: bool = False
    supports_tool_grammar: bool = False

    def snapshot(
        self,
        config: ModelConfig | dict[str, object],
        *,
        reasoning_selection: ModelReasoningSelection | None = None,
    ) -> ModelProfileSnapshot:
        supports_tools = (
            config.supports_tool_call
            if isinstance(config, ModelConfig)
            else config.get("supports_tools") is True
        )
        supports_reasoning = (
            config.supports_reasoning
            if isinstance(config, ModelConfig)
            else config.get("supports_thinking") is True
        )
        supports_json_schema_output = (
            False
            if isinstance(config, ModelConfig)
            else config.get("supports_json_schema_output") is True
        )
        supports_images = (
            config.supports_images
            if isinstance(config, ModelConfig)
            else config.get(
                "supports_images",
                config.get("supportsImages", self.supports_images),
            ) is True
        )
        return ModelProfileSnapshot(
            provider_id=self.provider_id,
            model_id=config.id if isinstance(config, ModelConfig) and config.custom else self.model_id,
            remote_model_id=self.model_id if isinstance(config, ModelConfig) and config.custom else None,
            wire_api=self.wire_api,
            context_window_tokens=self.context_window_tokens,
            max_output_tokens=self.max_output_tokens,
            request_timeout_seconds=self.request_timeout_seconds,
            supports_tools=supports_tools,
            supports_json_schema_output=supports_json_schema_output,
            supports_reasoning=supports_reasoning,
            reasoning_selection=reasoning_selection,
            supports_images=supports_images,
            supports_custom_tools=self.supports_custom_tools,
            supports_tool_grammar=self.supports_tool_grammar,
            connection_fingerprint=(hashlib.sha256(json.dumps(
                config.model_dump(mode="json", exclude={"api_key", "name"}),
                ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            ).encode()).hexdigest() if isinstance(config, ModelConfig) else None),
        )


class CatalogModel(EidosFrozenStrictModel):
    id: str
    name: str
    url: str
    supports_tool_call: bool
    supports_images: bool = False
    supports_reasoning: bool
    reasoning: ModelReasoningConfig | None = None
    wire_api: Literal["chat_completions", "openai_responses"] = "chat_completions"
    supports_custom_tools: bool = False
    supports_tool_grammar: bool = False
    context_window_tokens: int = DEFAULT_CONTEXT_WINDOW_TOKENS
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS


class CatalogProvider(EidosFrozenStrictModel):
    id: Literal["deepseek", "minimax", "kimi", "volcengine"]
    name: str
    vendor: str
    models: tuple[CatalogModel, ...]


MODEL_PROVIDERS = (
    CatalogProvider(
        id="deepseek",
        name="深度求索 / DeepSeek",
        vendor="DeepSeek",
        models=(
            CatalogModel(
                id="deepseek-flash",
                name="DeepSeek-V4.1 Flash",
                url="https://api.deepseek.com/chat/completions",
                supportsToolCall=True,
                supportsImages=False,
                supportsReasoning=True,
                reasoning=ModelReasoningConfig(
                    defaultSelection="high",
                    selections=("none", "low", "high", "max"),
                ),
                contextWindowTokens=802_816,
            ),
        ),
    ),
    CatalogProvider(
        id="minimax",
        name="MiniMax",
        vendor="MiniMax",
        models=(
            CatalogModel(
                id="MiniMax-M3",
                name="MiniMax M3",
                url="https://api.minimaxi.com/v1/chat/completions",
                supportsToolCall=True,
                supportsImages=True,
                supportsReasoning=True,
                reasoning=ModelReasoningConfig(
                    defaultSelection="thinking",
                    selections=("none", "thinking"),
                ),
            ),
        ),
    ),
    CatalogProvider(
        id="kimi",
        name="月之暗面 / Kimi",
        vendor="Kimi",
        models=(
            CatalogModel(
                id="kimi-k3",
                name="Kimi K3",
                url="https://api.moonshot.cn/v1/chat/completions",
                supportsToolCall=True,
                supportsImages=True,
                supportsReasoning=True,
                reasoning=ModelReasoningConfig(
                    defaultSelection="max",
                    selections=("low", "high", "max"),
                ),
            ),
            CatalogModel(
                id="kimi-k2.7-code-highspeed",
                name="Kimi K2.7 Code",
                url="https://api.moonshot.cn/v1/chat/completions",
                supportsToolCall=True,
                supportsImages=True,
                supportsReasoning=True,
                reasoning=None,
            ),
        ),
    ),
    CatalogProvider(
        id="volcengine",
        name="火山引擎 / Volcengine",
        vendor="Volcengine",
        # Coding Plan uses a separate endpoint; model-specific request fields
        # still need provider verification.
        models=(
            CatalogModel(
                id="deepseek-v4.1-flash",
                name="DeepSeek V4.1 Flash",
                url="https://ark.cn-beijing.volces.com/api/plan/v3/chat/completions",
                supportsToolCall=True,
                supportsImages=True,
                supportsReasoning=True,
                reasoning=ModelReasoningConfig(
                    defaultSelection="high",
                    selections=("none", "low", "medium", "high", "max"),
                ),
                contextWindowTokens=1_048_576,
                maxOutputTokens=131_072,
            ),
            CatalogModel(
                id="deepseek-v4-pro-ga-260813",
                name="DeepSeek V4 Pro GA",
                url="https://ark.cn-beijing.volces.com/api/plan/v3/chat/completions",
                supportsToolCall=True,
                supportsImages=False,
                supportsReasoning=True,
                reasoning=ModelReasoningConfig(
                    defaultSelection="high",
                    selections=("none", "low", "high", "max"),
                ),
                contextWindowTokens=1_048_576,
                maxOutputTokens=131_072,
            ),
            CatalogModel(
                id="deepseek-v4-flash-ga-260731",
                name="DeepSeek V4 Flash GA",
                url="https://ark.cn-beijing.volces.com/api/plan/v3/chat/completions",
                supportsToolCall=True,
                supportsImages=False,
                supportsReasoning=True,
                reasoning=ModelReasoningConfig(
                    defaultSelection="high",
                    selections=("none", "low", "high", "max"),
                ),
                contextWindowTokens=1_048_576,
                maxOutputTokens=393_216,
            ),
            CatalogModel(
                id="glm-5.3",
                name="GLM 5.3",
                url="https://ark.cn-beijing.volces.com/api/plan/v3/chat/completions",
                supportsToolCall=True,
                supportsImages=False,
                supportsReasoning=True,
                contextWindowTokens=1_048_576,
                maxOutputTokens=131_072,
            ),
            CatalogModel(
                id="glm-5.3-flash",
                name="GLM 5.3 Flash",
                url="https://ark.cn-beijing.volces.com/api/plan/v3/chat/completions",
                supportsToolCall=True,
                supportsImages=True,
                supportsReasoning=True,
                reasoning=ModelReasoningConfig(
                    defaultSelection="max",
                    selections=("low", "high", "max"),
                ),
                contextWindowTokens=1_048_576,
                maxOutputTokens=131_072,
            ),
            CatalogModel(
                id="minimax-m3",
                name="MiniMax M3",
                url="https://ark.cn-beijing.volces.com/api/plan/v3/chat/completions",
                supportsToolCall=True,
                supportsImages=True,
                supportsReasoning=False,
                contextWindowTokens=524_288,
                maxOutputTokens=131_072,
            ),
        ),
    ),
)


LEGACY_MODEL_ID_ALIASES = {
    "deepseek-v4-flash": "deepseek-flash",
    "deepseek-v4-pro": "deepseek-flash",
    "glm-5-2-260617": "glm-5.3",
}


class ModelCatalog:
    def __init__(self) -> None:
        self.providers = MODEL_PROVIDERS
        self._models = {
            model.id: (provider, model)
            for provider in self.providers
            for model in provider.models
        }

    def materialize(
        self, provider_id: str, model_id: str, api_key: str
    ) -> ModelConfig:
        provider, model = self.lookup(provider_id, model_id)
        return ModelConfig.model_validate({
            **model.model_dump(mode="python", by_alias=True, exclude={
                "context_window_tokens", "max_output_tokens",
                "wire_api", "supports_custom_tools", "supports_tool_grammar",
            }),
            "vendor": provider.vendor,
            "apiKey": api_key,
        })

    @staticmethod
    def canonical_id(model_id: str) -> str:
        return LEGACY_MODEL_ID_ALIASES.get(model_id, model_id)

    def lookup(
        self, provider_id: str, model_id: str
    ) -> tuple[CatalogProvider, CatalogModel]:
        selected = self._models.get(self.canonical_id(model_id))
        if selected is None or selected[0].id != provider_id:
            raise ModelConfigError("model is unsupported")
        return selected

    def provider_id_for(self, model_id: str, *, vendor: str | None = None) -> str:
        selected = self._models.get(self.canonical_id(model_id))
        if selected is not None:
            return selected[0].id
        if vendor:
            vendor_normalized = vendor.strip().lower()
            for provider in self.providers:
                if (
                    provider.id == vendor_normalized
                    or provider.vendor.lower() == vendor_normalized
                ):
                    return provider.id
            return vendor_normalized
        raise ModelConfigError("model is unsupported")

    def profile(
        self, model_id: str, *, config: ModelConfig | None = None
    ) -> ModelProfileSpec:
        if config is not None and config.custom is not None:
            return ModelProfileSpec(
                provider_id="custom", model_id=config.model_id or config.id,
                wire_api=config.custom.wire_api,
                context_window_tokens=config.custom.context_window_tokens,
                max_output_tokens=config.custom.max_output_tokens,
                request_timeout_seconds=DEFAULT_REQUEST_TIMEOUT_SECONDS,
                supports_images=config.supports_images,
            )
        selected = self._models.get(self.canonical_id(model_id))
        if selected is not None:
            provider, model = selected
            return ModelProfileSpec(
                provider_id=provider.id,
                model_id=model_id,
                context_window_tokens=model.context_window_tokens,
                max_output_tokens=model.max_output_tokens,
                request_timeout_seconds=DEFAULT_REQUEST_TIMEOUT_SECONDS,
                supports_images=(
                    config.supports_images
                    if config is not None
                    else model.supports_images
                ),
                wire_api=model.wire_api,
                supports_custom_tools=model.supports_custom_tools,
                supports_tool_grammar=model.supports_tool_grammar,
            )
        vendor = config.vendor if config is not None else None
        provider_id = self.provider_id_for(model_id, vendor=vendor)
        return ModelProfileSpec(
            provider_id=provider_id,
            model_id=model_id,
            context_window_tokens=DEFAULT_CONTEXT_WINDOW_TOKENS,
            max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
            request_timeout_seconds=DEFAULT_REQUEST_TIMEOUT_SECONDS,
            supports_images=config.supports_images if config is not None else False,
            wire_api="chat_completions",
            supports_custom_tools=False,
            supports_tool_grammar=False,
        )

    def reasoning_config(self, model_id: str) -> ModelReasoningConfig | None:
        selected = self._models.get(self.canonical_id(model_id))
        return selected[1].reasoning if selected is not None else None

    def reasoning_selection(
        self,
        model_id: str,
        requested: ModelReasoningSelection | None,
        *,
        config: ModelConfig | None = None,
    ) -> ModelReasoningSelection | None:
        reasoning = None
        selected = self._models.get(self.canonical_id(model_id))
        if config is not None and config.reasoning is not None:
            reasoning = config.reasoning
        elif selected is not None and (config is None or config.custom is None):
            reasoning = selected[1].reasoning
        if requested is None:
            return reasoning.default_selection if reasoning else None
        if reasoning is None or requested not in reasoning.selections:
            raise ModelReasoningSelectionError("reasoning selection is unsupported")
        return requested

    def public(self) -> dict[str, object]:
        return {
            "providers": [
                {
                    "id": provider.id,
                    "name": provider.name,
                    "models": [
                        model.model_dump(
                            mode="json",
                            by_alias=True,
                            exclude={
                                "context_window_tokens",
                                "max_output_tokens",
                                "wire_api",
                                "supports_custom_tools",
                                "supports_tool_grammar",
                            },
                        )
                        for model in provider.models
                    ],
                }
                for provider in self.providers
            ]
        }


MODEL_CATALOG = ModelCatalog()
SUPPORTED_MODELS = tuple(
    model.id for provider in MODEL_PROVIDERS for model in provider.models
)
DEFAULT_MODEL_ID = "deepseek-flash"


class ModelConfigError(RuntimeError):
    pass


class ModelReasoningSelectionError(ValueError):
    pass


class ModelConfigStore:
    def __init__(self, data_directory: Path | None = None) -> None:
        self.data_directory = data_directory
        self.path: Path | None = None

    def initialize(self) -> None:
        configured_directory = os.environ.get("EIDOS_DATA_DIR")
        directory = self.data_directory
        if directory is None and configured_directory:
            directory = Path(configured_directory).expanduser()
        if directory is None:
            directory = Path.home() / ".eidos"
        directory = directory.resolve()
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = directory / CONFIG_NAME
        if self.path.exists():
            self._validate_file(self.path)
            self._migrate_legacy_configs()
            self.list()

    def _migrate_legacy_configs(self) -> None:
        assert self.path is not None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, list):
                return
            values = [ModelConfig.model_validate(value) for value in payload]
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return

        migrated: list[ModelConfig] = []
        seen: set[str] = set()
        changed = False
        for value in values:
            canonical_id = value.id if value.custom else MODEL_CATALOG.canonical_id(value.id)
            if canonical_id in seen:
                raise ModelConfigError("model configuration migration conflict")
            seen.add(canonical_id)
            if canonical_id != value.id:
                migrated.append(value.model_copy(update={"id": canonical_id}))
                changed = True
            else:
                migrated.append(value)
        if changed:
            self._write(migrated)

    def list(self) -> list[ModelConfig]:
        path = self._path()
        if not path.exists():
            return []
        self._validate_file(path)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, list):
                raise ValueError
            values = [ModelConfig.model_validate(value) for value in payload]
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            raise ModelConfigError("model configuration is invalid") from None
        ids = [value.id for value in values]
        if len(ids) != len(set(ids)):
            raise ModelConfigError("model configuration contains duplicate IDs")
        return values

    def get(self, model_id: str) -> ModelConfig | None:
        model_id = MODEL_CATALOG.canonical_id(model_id)
        return next((model for model in self.list() if model.id == model_id), None)

    def create(
        self, *, provider_id: str, model_id: str, api_key: str,
        custom: CustomModelSettings | None = None,
    ) -> ModelConfig:
        values = self.list()
        if provider_id == "custom":
            if custom is None:
                raise ModelConfigError("自定义模型配置必填。")
            created = self._custom_config(f"custom-{uuid.uuid4()}", model_id, api_key, custom)
            self._write([*values, created])
            return created
        if custom is not None:
            raise ModelConfigError("内置提供商不能使用自定义连接配置。")
        model_id = MODEL_CATALOG.canonical_id(model_id)
        if any(value.id == model_id for value in values):
            raise ModelConfigError("model ID already exists")
        try:
            created = MODEL_CATALOG.materialize(provider_id, model_id, api_key)
        except ValueError as error:
            raise ModelConfigError("API key is invalid") from error
        self._write([*values, created])
        return created

    def update(
        self,
        existing_id: str,
        *,
        provider_id: str,
        model_id: str,
        api_key: str | None,
        custom: CustomModelSettings | None = None,
    ) -> ModelConfig:
        values = self.list()
        existing_id = MODEL_CATALOG.canonical_id(existing_id)
        model_id = model_id if provider_id == "custom" else MODEL_CATALOG.canonical_id(model_id)
        index = next(
            (position for position, value in enumerate(values) if value.id == existing_id),
            None,
        )
        if index is None:
            raise ModelConfigError("model was not found")
        if provider_id == "custom":
            if custom is None:
                raise ModelConfigError("自定义模型配置必填。")
            old = values[index]
            if api_key is None and normalize_model_base_url(old.url) != custom.base_url:
                raise ModelConfigError("修改接口地址后，请重新填写或清除 API Key。")
            replacement = self._custom_config(existing_id, model_id, old.api_key if api_key is None else api_key, custom)
            values[index] = replacement
            self._write(values)
            return replacement
        if custom is not None:
            raise ModelConfigError("内置提供商不能使用自定义连接配置。")
        if model_id != existing_id and any(value.id == model_id for value in values):
            raise ModelConfigError("model ID already exists")
        key = api_key.strip() if api_key is not None else ""
        try:
            replacement = MODEL_CATALOG.materialize(
                provider_id,
                model_id,
                key or values[index].api_key,
            )
        except ValueError as error:
            raise ModelConfigError("API key is invalid") from error
        values[index] = replacement
        self._write(values)
        return replacement

    def delete(self, model_id: str) -> ModelConfig:
        values = self.list()
        model_id = MODEL_CATALOG.canonical_id(model_id)
        deleted = next((value for value in values if value.id == model_id), None)
        if deleted is None:
            raise ModelConfigError("model was not found")
        self._write([value for value in values if value.id != model_id])
        return deleted

    def public_list(self) -> list[ModelPublicConfig]:
        return [public_model_config(value) for value in self.list()]

    @staticmethod
    def _custom_config(config_id: str, model_id: str, api_key: str, custom: CustomModelSettings) -> ModelConfig:
        suffix = "/responses" if custom.wire_api == "openai_responses" else "/chat/completions"
        try:
            return ModelConfig(
                id=config_id, model_id=model_id, name=custom.name, vendor=custom.provider_name,
                url=custom.base_url + suffix, apiKey=api_key,
                supportsToolCall=custom.supports_tool_call, supportsImages=custom.supports_images,
                supportsReasoning=custom.reasoning is not None, reasoning=custom.reasoning,
                custom=CustomModelRuntimeSettings.model_validate(custom.model_dump(include={
                    "provider_id", "wire_api", "context_window_tokens", "max_output_tokens", "reasoning_format", "reasoning_control", "output_token_field",
                })),
            )
        except ValueError:
            raise ModelConfigError("自定义模型配置无效。") from None

    def _write(self, values: list[ModelConfig]) -> None:
        path = self._path()
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".models-",
            suffix=".tmp",
            dir=path.parent,
        )
        temporary_path = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            payload = json.dumps(
                [value.model_dump(mode="json", by_alias=True, exclude={"custom", "model_id"} if value.custom is None else set()) for value in values],
                ensure_ascii=False,
                indent=2,
            ).encode("utf-8")
            offset = 0
            while offset < len(payload):
                written = os.write(descriptor, payload[offset:])
                if written <= 0:
                    raise OSError("model configuration write failed")
                offset += written
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            os.replace(temporary_path, path)
            directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            self._validate_file(path)
        except Exception:
            if descriptor >= 0:
                os.close(descriptor)
            temporary_path.unlink(missing_ok=True)
            raise

    @staticmethod
    def _validate_file(path: Path) -> None:
        if path.is_symlink():
            raise ModelConfigError("model configuration must not be a symlink")
        metadata = path.stat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise ModelConfigError("model configuration owner or mode is invalid")

    def _path(self) -> Path:
        if self.path is None:
            self.initialize()
        assert self.path is not None
        return self.path


def public_model_config(config: ModelConfig) -> ModelPublicConfig:
    reasoning = config.reasoning
    if reasoning is None and config.supports_reasoning and config.custom is None:
        reasoning = MODEL_CATALOG.reasoning_config(config.id)
    profile = MODEL_CATALOG.profile(config.id, config=config)
    custom = None
    if config.custom is not None:
        custom = CustomModelSettings(
            provider_name=config.vendor, base_url=normalize_model_base_url(config.url),
            name=config.name, supports_tool_call=config.supports_tool_call,
            supports_images=config.supports_images, reasoning=config.reasoning,
            **config.custom.model_dump(),
        )
    return ModelPublicConfig(
        **config.model_dump(
            mode="json", by_alias=True, exclude={"api_key", "reasoning", "custom", "model_id"}
        ),
        api_key=config.api_key,
        provider="custom" if custom else MODEL_CATALOG.provider_id_for(config.id, vendor=config.vendor),
        reasoning=reasoning, custom=custom, model_id=config.model_id,
        context_window_tokens=profile.context_window_tokens, max_output_tokens=profile.max_output_tokens,
    )


def model_presets() -> dict[str, object]:
    return MODEL_CATALOG.public()


def default_profile_snapshot(model_id: str) -> ModelProfileSnapshot:
    provider_id = MODEL_CATALOG.provider_id_for(model_id)
    config = MODEL_CATALOG.materialize(provider_id, model_id, "placeholder-key")
    selection = MODEL_CATALOG.reasoning_selection(model_id, None)
    return MODEL_CATALOG.profile(model_id).snapshot(
        config, reasoning_selection=selection
    ).model_copy(update={"completion_check_version": 0})

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Literal

from pydantic import Field, field_validator, model_validator

from eidos_runtime.model.client import ModelProfileSnapshot
from eidos_runtime.models import EidosFrozenStrictModel


CONFIG_NAME = "models.json"
DEFAULT_CONTEXT_WINDOW_TOKENS = 128_000
DEFAULT_MAX_OUTPUT_TOKENS = 8_192
DEFAULT_REQUEST_TIMEOUT_SECONDS = 120.0


class ModelReasoningConfig(EidosFrozenStrictModel):
    default_effort: Literal["high", "max"]
    supported_efforts: tuple[Literal["high", "max"], ...]

    @field_validator("supported_efforts", mode="before")
    @classmethod
    def normalize_supported_efforts(cls, value: object) -> object:
        return tuple(value) if isinstance(value, list) else value

    @model_validator(mode="after")
    def validate_default_effort(self) -> "ModelReasoningConfig":
        if self.default_effort not in self.supported_efforts:
            raise ValueError("default reasoning effort must be supported")
        return self


class ModelConfig(EidosFrozenStrictModel):
    id: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=120)
    vendor: str = Field(min_length=1, max_length=64)
    url: str = Field(min_length=1, max_length=2048)
    api_key: str = Field(alias="apiKey", min_length=1, max_length=512)
    supports_tool_call: bool = Field(alias="supportsToolCall")
    supports_images: bool = Field(alias="supportsImages")
    supports_reasoning: bool = Field(alias="supportsReasoning")
    reasoning: ModelReasoningConfig | None = None

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: str) -> str:
        key = value.strip()
        if (
            not key
            or any(character.isspace() or ord(character) < 32 for character in key)
        ):
            raise ValueError("API key is invalid")
        return key

    @model_validator(mode="after")
    def validate_reasoning(self) -> "ModelConfig":
        if self.supports_reasoning != (self.reasoning is not None):
            raise ValueError("reasoning configuration does not match capability")
        return self


class ModelPublicConfig(EidosFrozenStrictModel):
    id: str
    name: str
    vendor: str
    provider: str
    url: str
    supports_tool_call: bool = Field(alias="supportsToolCall")
    supports_images: bool = Field(alias="supportsImages")
    supports_reasoning: bool = Field(alias="supportsReasoning")
    reasoning: ModelReasoningConfig | None = None


class ModelProfileSpec(EidosFrozenStrictModel):
    provider_id: str
    model_id: str
    wire_api: Literal["chat_completions"] = "chat_completions"
    context_window_tokens: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    request_timeout_seconds: float = Field(gt=0)

    def snapshot(self, config: ModelConfig | dict[str, object]) -> ModelProfileSnapshot:
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
        return ModelProfileSnapshot(
            provider_id=self.provider_id,
            model_id=self.model_id,
            wire_api=self.wire_api,
            context_window_tokens=self.context_window_tokens,
            max_output_tokens=self.max_output_tokens,
            request_timeout_seconds=self.request_timeout_seconds,
            supports_tools=supports_tools,
            supports_json_schema_output=supports_json_schema_output,
            supports_reasoning=supports_reasoning,
        )


class CatalogModel(EidosFrozenStrictModel):
    id: str
    name: str
    url: str
    supports_tool_call: bool
    supports_images: bool
    supports_reasoning: bool
    reasoning: ModelReasoningConfig | None = None
    context_window_tokens: int = DEFAULT_CONTEXT_WINDOW_TOKENS
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS


class CatalogProvider(EidosFrozenStrictModel):
    id: Literal["deepseek", "minimax", "kimi", "volcengine"]
    name: str
    vendor: str
    models: tuple[CatalogModel, ...]


_REASONING = ModelReasoningConfig(
    defaultEffort="high",
    supportedEfforts=("high", "max"),
)
MODEL_PROVIDERS = (
    CatalogProvider(
        id="deepseek",
        name="深度求索 / DeepSeek",
        vendor="DeepSeek",
        models=(
            CatalogModel(
                id="deepseek-v4-pro",
                name="DeepSeek-V4 Pro",
                url="https://api.deepseek.com/chat/completions",
                supportsToolCall=True,
                supportsImages=False,
                supportsReasoning=True,
                reasoning=_REASONING,
                contextWindowTokens=802_816,
            ),
            CatalogModel(
                id="deepseek-v4-flash",
                name="DeepSeek-V4 Flash",
                url="https://api.deepseek.com/chat/completions",
                supportsToolCall=True,
                supportsImages=False,
                supportsReasoning=True,
                reasoning=_REASONING,
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
                supportsImages=False,
                supportsReasoning=True,
                reasoning=_REASONING,
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
                supportsImages=False,
                supportsReasoning=True,
                reasoning=_REASONING,
            ),
            CatalogModel(
                id="kimi-k2.7-code-highspeed",
                name="Kimi K2.7 Code",
                url="https://api.moonshot.cn/v1/chat/completions",
                supportsToolCall=True,
                supportsImages=False,
                supportsReasoning=True,
                reasoning=_REASONING,
            ),
        ),
    ),
    CatalogProvider(
        id="volcengine",
        name="火山引擎 / Volcengine",
        vendor="Volcengine",
        models=(
            CatalogModel(
                id="deepseek-v4-pro-ga-260813",
                name="DeepSeek V4 Pro GA",
                url="https://ark.cn-beijing.volces.com/api/coding/v3/chat/completions",
                supportsToolCall=True,
                supportsImages=False,
                supportsReasoning=False,
                contextWindowTokens=1_048_576,
                maxOutputTokens=131_072,
            ),
            CatalogModel(
                id="deepseek-v4-flash-ga-260731",
                name="DeepSeek V4 Flash GA",
                url="https://ark.cn-beijing.volces.com/api/coding/v3/chat/completions",
                supportsToolCall=True,
                supportsImages=False,
                supportsReasoning=False,
                contextWindowTokens=1_048_576,
                maxOutputTokens=393_216,
            ),
            CatalogModel(
                id="glm-5-2-260617",
                name="GLM 5.2",
                url="https://ark.cn-beijing.volces.com/api/coding/v3/chat/completions",
                supportsToolCall=True,
                supportsImages=False,
                supportsReasoning=False,
                contextWindowTokens=1_048_576,
                maxOutputTokens=131_072,
            ),
            CatalogModel(
                id="doubao-seed-evolving",
                name="Doubao Seed Evolving",
                url="https://ark.cn-beijing.volces.com/api/coding/v3/chat/completions",
                supportsToolCall=True,
                supportsImages=True,
                supportsReasoning=False,
                contextWindowTokens=1_048_576,
                maxOutputTokens=262_144,
            ),
            CatalogModel(
                id="doubao-seed-2-1-pro-260628",
                name="Doubao Seed 2.1 Pro",
                url="https://ark.cn-beijing.volces.com/api/coding/v3/chat/completions",
                supportsToolCall=True,
                supportsImages=True,
                supportsReasoning=False,
                contextWindowTokens=262_144,
                maxOutputTokens=262_144,
            ),
            CatalogModel(
                id="doubao-seed-2-1-turbo-260628",
                name="Doubao Seed 2.1 Turbo",
                url="https://ark.cn-beijing.volces.com/api/coding/v3/chat/completions",
                supportsToolCall=True,
                supportsImages=True,
                supportsReasoning=False,
                contextWindowTokens=262_144,
                maxOutputTokens=262_144,
            ),
            CatalogModel(
                id="doubao-seed-2-0-code-preview-260215",
                name="Doubao Seed 2.0 Code",
                url="https://ark.cn-beijing.volces.com/api/coding/v3/chat/completions",
                supportsToolCall=True,
                supportsImages=True,
                supportsReasoning=False,
                contextWindowTokens=262_144,
                maxOutputTokens=131_072,
            ),
        ),
    ),
)


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
                "context_window_tokens", "max_output_tokens"
            }),
            "vendor": provider.vendor,
            "apiKey": api_key,
        })

    def lookup(
        self, provider_id: str, model_id: str
    ) -> tuple[CatalogProvider, CatalogModel]:
        selected = self._models.get(model_id)
        if selected is None or selected[0].id != provider_id:
            raise ModelConfigError("model is unsupported")
        return selected

    def provider_id_for(self, model_id: str) -> str:
        selected = self._models.get(model_id)
        if selected is None:
            raise ModelConfigError("model is unsupported")
        return selected[0].id

    def profile(self, model_id: str) -> ModelProfileSpec:
        provider, model = self.lookup(self.provider_id_for(model_id), model_id)
        return ModelProfileSpec(
            provider_id=provider.id,
            model_id=model.id,
            context_window_tokens=model.context_window_tokens,
            max_output_tokens=model.max_output_tokens,
            request_timeout_seconds=DEFAULT_REQUEST_TIMEOUT_SECONDS,
        )

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
DEFAULT_MODEL_ID = "deepseek-v4-flash"


class ModelConfigError(RuntimeError):
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
            self.list()

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
        for value in values:
            provider_id = MODEL_CATALOG.provider_id_for(value.id)
            expected = MODEL_CATALOG.materialize(provider_id, value.id, value.api_key)
            if value != expected:
                raise ModelConfigError("model configuration is invalid")
        return values

    def get(self, model_id: str) -> ModelConfig | None:
        return next((model for model in self.list() if model.id == model_id), None)

    def create(
        self, *, provider_id: str, model_id: str, api_key: str
    ) -> ModelConfig:
        values = self.list()
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
    ) -> ModelConfig:
        values = self.list()
        index = next(
            (position for position, value in enumerate(values) if value.id == existing_id),
            None,
        )
        if index is None:
            raise ModelConfigError("model was not found")
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
        deleted = next((value for value in values if value.id == model_id), None)
        if deleted is None:
            raise ModelConfigError("model was not found")
        self._write([value for value in values if value.id != model_id])
        return deleted

    def public_list(self) -> list[ModelPublicConfig]:
        return [public_model_config(value) for value in self.list()]

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
                [value.model_dump(mode="json", by_alias=True) for value in values],
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
    return ModelPublicConfig(
        **config.model_dump(mode="json", by_alias=True, exclude={"api_key"}),
        provider=MODEL_CATALOG.provider_id_for(config.id),
    )


def model_presets() -> dict[str, object]:
    return MODEL_CATALOG.public()


def default_profile_snapshot(model_id: str) -> ModelProfileSnapshot:
    provider_id = MODEL_CATALOG.provider_id_for(model_id)
    config = MODEL_CATALOG.materialize(provider_id, model_id, "placeholder-key")
    return MODEL_CATALOG.profile(model_id).snapshot(config)

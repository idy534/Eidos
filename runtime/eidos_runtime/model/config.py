from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from eidos_runtime.model.client import ModelProfileSnapshot


CONFIG_NAME = "model.json"
PROVIDER = "deepseek"
SUPPORTED_MODELS = ("deepseek-v4-flash", "deepseek-v4-pro")
DEFAULT_MODEL_ID = SUPPORTED_MODELS[0]
MODEL = DEFAULT_MODEL_ID
MODEL_NAMES = {
    "deepseek-v4-flash": "DeepSeek V4 Flash",
    "deepseek-v4-pro": "DeepSeek V4 Pro",
}
DEFAULT_CONTEXT_WINDOW_TOKENS = 802_816
DEFAULT_MAX_OUTPUT_TOKENS = 8_192
DEFAULT_REQUEST_TIMEOUT_SECONDS = 120.0


class ModelProfileSpec(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    provider_id: str
    model_id: str
    wire_api: Literal["chat_completions"] = "chat_completions"
    context_window_tokens: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    request_timeout_seconds: float = Field(gt=0)

    def snapshot(self, resolved_profile: dict[str, object]) -> ModelProfileSnapshot:
        return ModelProfileSnapshot(
            provider_id=self.provider_id,
            model_id=self.model_id,
            context_window_tokens=self.context_window_tokens,
            max_output_tokens=self.max_output_tokens,
            request_timeout_seconds=self.request_timeout_seconds,
            supports_tools=resolved_profile.get("supports_tools") is True,
            supports_json_schema_output=(
                resolved_profile.get("supports_json_schema_output") is True
            ),
            supports_reasoning=resolved_profile.get("supports_thinking") is True,
        )


class ModelCatalog:
    def __init__(self) -> None:
        self._profiles = {
            model_id: ModelProfileSpec(
                provider_id=PROVIDER,
                model_id=model_id,
                context_window_tokens=DEFAULT_CONTEXT_WINDOW_TOKENS,
                max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
                request_timeout_seconds=DEFAULT_REQUEST_TIMEOUT_SECONDS,
            )
            for model_id in SUPPORTED_MODELS
        }

    def profile(self, model_id: str) -> ModelProfileSpec:
        try:
            return self._profiles[model_id]
        except KeyError:
            raise ValueError("model is unsupported") from None

    def public(self, *, configured: bool) -> dict[str, object]:
        return {
            "models": [
                {
                    "id": model_id,
                    "provider": PROVIDER,
                    "displayName": MODEL_NAMES[model_id],
                    "configured": configured,
                    "selectable": configured,
                }
                for model_id in SUPPORTED_MODELS
            ],
            "defaultModelId": DEFAULT_MODEL_ID,
        }


MODEL_CATALOG = ModelCatalog()


def default_profile_snapshot(model_id: str) -> ModelProfileSnapshot:
    return MODEL_CATALOG.profile(model_id).snapshot({
        "supports_tools": True,
        "supports_json_schema_output": False,
        "supports_thinking": True,
    })


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
        self.path = directory.resolve() / CONFIG_NAME
        if self.path.exists():
            self._validate_file(self.path)
            self._read_key()

    def configured(self) -> bool:
        return self._read_key() is not None

    def api_key(self) -> str | None:
        return self._read_key()

    def save_api_key(self, value: str) -> None:
        key = _validate_key(value)
        path = self._path()
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".model-",
            suffix=".tmp",
            dir=path.parent,
        )
        temporary_path = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            payload = json.dumps(
                {"provider": PROVIDER, "model": MODEL, "apiKey": key},
                ensure_ascii=False,
                separators=(",", ":"),
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

    def restore_api_key(self, value: str | None) -> None:
        if value is not None:
            self.save_api_key(value)
            return
        path = self._path()
        path.unlink(missing_ok=True)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def public_status(self) -> dict[str, object]:
        return {
            "provider": PROVIDER,
            "model": MODEL,
            "configured": self.configured(),
        }

    def _read_key(self) -> str | None:
        path = self._path()
        if not path.exists():
            return None
        self._validate_file(path)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise ModelConfigError("model configuration is invalid") from None
        if (
            not isinstance(payload, dict)
            or set(payload) != {"provider", "model", "apiKey"}
            or payload.get("provider") != PROVIDER
            or payload.get("model") != MODEL
        ):
            raise ModelConfigError("model configuration is invalid")
        try:
            return _validate_key(payload.get("apiKey"))
        except ValueError:
            raise ModelConfigError("model configuration is invalid") from None

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
            raise ModelConfigError("model configuration is not initialized")
        return self.path


def model_catalog(*, configured: bool) -> dict[str, object]:
    return MODEL_CATALOG.public(configured=configured)


def _validate_key(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("API key is invalid")
    key = value.strip()
    if (
        not key.startswith("sk-")
        or not 16 <= len(key) <= 256
        or any(character.isspace() or ord(character) < 32 for character in key)
    ):
        raise ValueError("API key is invalid")
    return key

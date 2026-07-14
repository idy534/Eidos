from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import tempfile


CONFIG_NAME = "model.json"
PROVIDER = "deepseek"
MODEL = "deepseek-v4-flash"


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

from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import tempfile
import uuid


SECRET_FILE = "model-secrets.json"


class ModelSecretError(RuntimeError):
    pass


class ModelSecretStore:
    def __init__(self, data_directory: Path | None = None) -> None:
        self.data_directory = data_directory
        self.path: Path | None = None

    def initialize(self) -> None:
        directory = self.data_directory
        if directory is None:
            configured = os.environ.get("EIDOS_DATA_DIR")
            directory = Path(configured).expanduser() if configured else Path.home() / ".eidos"
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.path = directory.resolve() / SECRET_FILE
        if self.path.exists():
            self._validate_file(self.path)
            self._read()

    def save(self, value: str, *, secret_id: str | None = None) -> str:
        secret = _validate_secret(value)
        identifier = secret_id or str(uuid.uuid4())
        if not identifier or any(character.isspace() for character in identifier):
            raise ValueError("secret identifier is invalid")
        payload = self._read()
        payload[identifier] = secret
        self._write(payload)
        return f"local:{identifier}"

    def resolve(self, reference: str) -> str:
        if reference.startswith("env:"):
            value = os.environ.get(reference[4:])
            if value is None:
                raise ModelSecretError("model secret is unavailable")
            return _validate_secret(value)
        if reference.startswith("local:"):
            value = self._read().get(reference[6:])
            if value is None:
                raise ModelSecretError("model secret is unavailable")
            return _validate_secret(value)
        raise ModelSecretError("model secret reference is invalid")

    def delete(self, reference: str) -> None:
        if not reference.startswith("local:"):
            return
        payload = self._read()
        if payload.pop(reference[6:], None) is not None:
            self._write(payload)

    def _read(self) -> dict[str, str]:
        path = self._path()
        if not path.exists():
            return {}
        self._validate_file(path)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            raise ModelSecretError("model secret store is invalid") from None
        if not isinstance(payload, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in payload.items()
        ):
            raise ModelSecretError("model secret store is invalid")
        return payload

    def _write(self, payload: dict[str, str]) -> None:
        path = self._path()
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".model-secrets-",
            suffix=".tmp",
            dir=path.parent,
        )
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            encoded = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
            offset = 0
            while offset < len(encoded):
                written = os.write(descriptor, encoded[offset:])
                if written <= 0:
                    raise OSError("model secret write failed")
                offset += written
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            os.replace(temporary, path)
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            self._validate_file(path)
        except Exception:
            if descriptor >= 0:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)
            raise

    @staticmethod
    def _validate_file(path: Path) -> None:
        if path.is_symlink():
            raise ModelSecretError("model secret store must not be a symlink")
        metadata = path.stat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise ModelSecretError("model secret store owner or mode is invalid")

    def _path(self) -> Path:
        if self.path is None:
            raise ModelSecretError("model secret store is not initialized")
        return self.path


def _validate_secret(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("model secret is invalid")
    secret = value.strip()
    if (
        not 16 <= len(secret) <= 512
        or any(character.isspace() or ord(character) < 32 for character in secret)
    ):
        raise ValueError("model secret is invalid")
    return secret

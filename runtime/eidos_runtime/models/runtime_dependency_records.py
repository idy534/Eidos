from __future__ import annotations

import json
from typing import Literal

from pydantic import Field, field_validator

from eidos_runtime.models.base import EidosFrozenStrictModel
from eidos_runtime.models.types import (
    JSON_SAFE_INTEGER_MAX,
    JSON_SAFE_INTEGER_MIN,
    JsonSafeInt,
)


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
MAX_RUNTIME_DEPENDENCY_SNAPSHOT_BYTES = 512 * 1024
MAX_RUNTIME_DEPENDENCY_DIAGNOSTICS_BYTES = 64 * 1024
MAX_RUNTIME_DEPENDENCY_ID_CHARS = 256
MAX_QUALIFIED_SKILL_ID_CHARS = 256


def _validate_canonical_json(
    value: str,
    *,
    expected: Literal["object", "array"],
    max_bytes: int,
) -> str:
    if len(value.encode("utf-8")) > max_bytes:
        raise ValueError("JSON payload exceeds its bounded size")
    try:
        parsed = json.loads(
            value,
            parse_constant=lambda constant: _reject_json_constant(constant),
        )
        if expected == "object" and not isinstance(parsed, dict):
            raise ValueError("JSON payload must be an object")
        if expected == "array" and not isinstance(parsed, list):
            raise ValueError("JSON payload must be an array")
        _validate_json_safe_integers(parsed)
        canonical = json.dumps(
            parsed,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
    except (
        RecursionError,
        UnicodeEncodeError,
        ValueError,
        TypeError,
        json.JSONDecodeError,
    ) as error:
        raise ValueError("JSON payload must be canonical and JSON-safe") from error
    if value != canonical:
        raise ValueError("JSON payload must use canonical encoding")
    return value


def _reject_json_constant(constant: str) -> None:
    raise ValueError(f"JSON payload contains non-standard constant {constant}")


def _validate_json_safe_integers(value: object) -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, int):
        if not JSON_SAFE_INTEGER_MIN <= value <= JSON_SAFE_INTEGER_MAX:
            raise ValueError("JSON payload contains an unsafe integer")
        return
    if isinstance(value, dict):
        for child in value.values():
            _validate_json_safe_integers(child)
        return
    if isinstance(value, list):
        for child in value:
            _validate_json_safe_integers(child)


class RuntimeDependencySnapshotRecord(EidosFrozenStrictModel):
    """The immutable dependency catalog fact captured once for a Run."""

    schema_version: Literal[1] = 1
    run_id: str = Field(min_length=1, max_length=MAX_RUNTIME_DEPENDENCY_ID_CHARS)
    manifest_hash: str = Field(pattern=_SHA256_PATTERN)
    catalog_hash: str = Field(pattern=_SHA256_PATTERN)
    snapshot_json: str = Field(
        min_length=2,
        max_length=MAX_RUNTIME_DEPENDENCY_SNAPSHOT_BYTES,
    )
    created_at: JsonSafeInt = Field(ge=0)

    _validate_snapshot_json = field_validator("snapshot_json")(
        lambda value: _validate_canonical_json(
            value,
            expected="object",
            max_bytes=MAX_RUNTIME_DEPENDENCY_SNAPSHOT_BYTES,
        )
    )


class RuntimeDependencyBindingRecord(EidosFrozenStrictModel):
    """An immutable binding result that references its Run snapshot by hashes."""

    schema_version: Literal[1] = 1
    run_id: str = Field(min_length=1, max_length=MAX_RUNTIME_DEPENDENCY_ID_CHARS)
    binding_id: str = Field(
        min_length=1,
        max_length=MAX_RUNTIME_DEPENDENCY_ID_CHARS,
    )
    manifest_hash: str = Field(pattern=_SHA256_PATTERN)
    requirements_hash: str = Field(pattern=_SHA256_PATTERN)
    qualified_skill_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_QUALIFIED_SKILL_ID_CHARS,
    )
    status: Literal["ready", "missing", "incompatible", "invalid"]
    diagnostics_json: str = Field(
        default="[]",
        min_length=2,
        max_length=MAX_RUNTIME_DEPENDENCY_DIAGNOSTICS_BYTES,
    )
    created_at: JsonSafeInt = Field(ge=0)

    _validate_diagnostics_json = field_validator("diagnostics_json")(
        lambda value: _validate_canonical_json(
            value,
            expected="array",
            max_bytes=MAX_RUNTIME_DEPENDENCY_DIAGNOSTICS_BYTES,
        )
    )


__all__ = [
    "MAX_QUALIFIED_SKILL_ID_CHARS",
    "MAX_RUNTIME_DEPENDENCY_DIAGNOSTICS_BYTES",
    "MAX_RUNTIME_DEPENDENCY_ID_CHARS",
    "MAX_RUNTIME_DEPENDENCY_SNAPSHOT_BYTES",
    "RuntimeDependencyBindingRecord",
    "RuntimeDependencySnapshotRecord",
]

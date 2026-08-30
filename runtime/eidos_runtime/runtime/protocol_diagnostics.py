from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import Field

from eidos_runtime.models import EidosFrozenStrictModel


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_MAX_DIAGNOSTIC_TOOL_CALLS = 1024
_MAX_ARGUMENT_KEYS = 64
_MAX_ARGUMENT_KEY_CHARS = 256
_MAX_ARGUMENT_BYTES = 1024 * 1024

ArgumentType = Literal[
    "null", "boolean", "integer", "number", "string", "array", "object",
    "invalid",
]


class ProtocolDiagnostic(EidosFrozenStrictModel):
    """Bounded, value-free evidence for a rejected model response."""

    schema_version: Literal[1] = 1
    stage: Literal[
        "response_validation",
        "tool_validation",
        "response_completion",
        "model_transport",
        "sensitive_scan",
    ]
    code: str = Field(min_length=1, max_length=128)
    tool_call_count: int = Field(
        default=0, ge=0, le=_MAX_DIAGNOSTIC_TOOL_CALLS
    )
    tool_calls_truncated: bool = False
    tool_call_index: int | None = Field(
        default=None, ge=0, le=_MAX_DIAGNOSTIC_TOOL_CALLS - 1
    )
    tool_name: str | None = Field(default=None, min_length=1, max_length=128)
    provider_call_id: str | None = Field(
        default=None, min_length=1, max_length=256
    )
    tool_declared: bool | None = None
    tool_set_hash: str | None = Field(
        default=None, min_length=64, max_length=64, pattern=_SHA256_PATTERN
    )
    contract_fingerprint: str | None = Field(
        default=None, min_length=64, max_length=64, pattern=_SHA256_PATTERN
    )
    validation_code: str | None = Field(default=None, max_length=128)
    validation_path: str | None = Field(default=None, max_length=256)
    arguments_sha256: str | None = Field(
        default=None, min_length=64, max_length=64, pattern=_SHA256_PATTERN
    )
    argument_bytes: int | None = Field(
        default=None, ge=0, le=_MAX_ARGUMENT_BYTES
    )
    argument_keys: tuple[str, ...] = Field(
        default=(), max_length=_MAX_ARGUMENT_KEYS
    )
    argument_types: dict[str, ArgumentType] = Field(
        default_factory=dict, max_length=_MAX_ARGUMENT_KEYS
    )
    arguments_truncated: bool = False


def response_text_metrics(text: str) -> tuple[int, str | None]:
    encoded = text.encode("utf-8")
    if not encoded:
        return 0, None
    return len(encoded), hashlib.sha256(encoded).hexdigest()


def argument_summary(
    value: object,
) -> tuple[
    int | None,
    str | None,
    tuple[str, ...],
    dict[str, ArgumentType],
    bool,
]:
    """Return bounded argument shape evidence without retaining argument values."""

    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        return None, None, (), {}, True

    argument_bytes = (
        len(encoded) if len(encoded) <= _MAX_ARGUMENT_BYTES else None
    )
    arguments_sha256 = hashlib.sha256(encoded).hexdigest()
    if not isinstance(value, dict) or not all(
        isinstance(key, str) for key in value
    ):
        return argument_bytes, arguments_sha256, (), {}, True

    ordered_keys = sorted(value)
    visible_keys = tuple(
        _safe_argument_key(key) for key in ordered_keys[:_MAX_ARGUMENT_KEYS]
    )
    argument_types = {
        _safe_argument_key(key): _json_type(value[key])
        for key in ordered_keys[:_MAX_ARGUMENT_KEYS]
    }
    return (
        argument_bytes,
        arguments_sha256,
        visible_keys,
        argument_types,
        argument_bytes is None or len(ordered_keys) > _MAX_ARGUMENT_KEYS
    )


def serialize_protocol_diagnostic(diagnostic: ProtocolDiagnostic) -> str:
    return json.dumps(
        diagnostic.model_dump(mode="json", by_alias=True, exclude_none=True),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _json_type(value: object) -> ArgumentType:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "invalid"


def _safe_argument_key(value: str) -> str:
    if len(value) <= _MAX_ARGUMENT_KEY_CHARS:
        return value
    return "<key:" + hashlib.sha256(value.encode("utf-8")).hexdigest() + ">"


__all__ = [
    "ArgumentType",
    "ProtocolDiagnostic",
    "argument_summary",
    "response_text_metrics",
    "serialize_protocol_diagnostic",
]

from __future__ import annotations

from copy import deepcopy
import json
import math


MAX_SCHEMA_BYTES = 256 * 1024
MAX_VALUE_BYTES = 256 * 1024
MAX_DEPTH = 16
MAX_NODES = 4096
_KEYS = {
    "type", "properties", "required", "additionalProperties", "items",
    "enum", "const", "default", "minimum", "maximum", "minLength",
    "maxLength", "minItems", "maxItems", "description",
}
_TYPES = {"object", "array", "string", "integer", "number", "boolean", "null"}


class JsonSchemaValidationError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class BoundedJsonSchema:
    """Validates Eidos's closed, non-executable JSON Schema subset."""

    def __init__(self, schema: dict[str, object]) -> None:
        self.schema = deepcopy(schema)
        try:
            encoded = _json(self.schema)
        except (TypeError, ValueError):
            raise JsonSchemaValidationError("JSON_SCHEMA_MALFORMED") from None
        if len(encoded.encode("utf-8")) > MAX_SCHEMA_BYTES:
            raise JsonSchemaValidationError("JSON_SCHEMA_TOO_LARGE")
        count = [0]
        self._validate_schema(self.schema, 0, count)

    def validate(
        self, value: object, *, apply_defaults: bool = False
    ) -> object:
        try:
            encoded = _json(value)
        except (TypeError, ValueError):
            raise JsonSchemaValidationError("JSON_VALUE_INVALID") from None
        if len(encoded.encode("utf-8")) > MAX_VALUE_BYTES:
            raise JsonSchemaValidationError("JSON_VALUE_TOO_LARGE")
        candidate = deepcopy(value)
        count = [0]
        return self._validate_value(
            self.schema, candidate, 0, count, apply_defaults
        )

    def _validate_schema(
        self, schema: object, depth: int, count: list[int]
    ) -> None:
        _count(depth, count, "JSON_SCHEMA_LIMIT_EXCEEDED")
        if not isinstance(schema, dict) or not set(schema) <= _KEYS:
            raise JsonSchemaValidationError("JSON_SCHEMA_UNSUPPORTED")
        kind = schema.get("type")
        if kind not in _TYPES:
            raise JsonSchemaValidationError("JSON_SCHEMA_UNSUPPORTED")
        if "description" in schema and not isinstance(schema["description"], str):
            raise JsonSchemaValidationError("JSON_SCHEMA_MALFORMED")
        for key in ("minLength", "maxLength", "minItems", "maxItems"):
            if key in schema and (
                not isinstance(schema[key], int)
                or isinstance(schema[key], bool)
                or schema[key] < 0
            ):
                raise JsonSchemaValidationError("JSON_SCHEMA_MALFORMED")
        for key in ("minimum", "maximum"):
            if key in schema and (
                not isinstance(schema[key], (int, float))
                or isinstance(schema[key], bool)
                or not math.isfinite(schema[key])
            ):
                raise JsonSchemaValidationError("JSON_SCHEMA_MALFORMED")
        for lower, upper in (
            ("minLength", "maxLength"),
            ("minItems", "maxItems"),
            ("minimum", "maximum"),
        ):
            if (
                lower in schema
                and upper in schema
                and schema[lower] > schema[upper]  # type: ignore[operator]
            ):
                raise JsonSchemaValidationError("JSON_SCHEMA_MALFORMED")
        if "enum" in schema and (
            not isinstance(schema["enum"], list) or not schema["enum"]
        ):
            raise JsonSchemaValidationError("JSON_SCHEMA_MALFORMED")
        for keyword in ("enum", "const", "default"):
            if keyword in schema:
                try:
                    _json(schema[keyword])
                except (TypeError, ValueError):
                    raise JsonSchemaValidationError(
                        "JSON_SCHEMA_MALFORMED"
                    ) from None
        if kind == "object":
            properties = schema.get("properties")
            required = schema.get("required", [])
            if (
                not isinstance(properties, dict)
                or schema.get("additionalProperties") is not False
                or not isinstance(required, list)
                or len(set(required)) != len(required)
                or not all(
                    isinstance(key, str) and key in properties
                    for key in required
                )
            ):
                raise JsonSchemaValidationError("JSON_SCHEMA_UNSUPPORTED")
            for key, child in properties.items():
                if not isinstance(key, str):
                    raise JsonSchemaValidationError("JSON_SCHEMA_MALFORMED")
                self._validate_schema(child, depth + 1, count)
        elif kind == "array":
            self._validate_schema(schema.get("items"), depth + 1, count)
        elif any(
            key in schema for key in (
                "properties", "required", "additionalProperties", "items"
            )
        ):
            raise JsonSchemaValidationError("JSON_SCHEMA_MALFORMED")

    def _validate_value(
        self,
        schema: dict[str, object],
        value: object,
        depth: int,
        count: list[int],
        apply_defaults: bool,
    ) -> object:
        _count(depth, count, "JSON_VALUE_LIMIT_EXCEEDED")
        if "enum" in schema and not any(
            _same_json(value, option) for option in schema["enum"]  # type: ignore[union-attr]
        ):
            raise JsonSchemaValidationError("JSON_VALUE_ENUM")
        if "const" in schema and not _same_json(value, schema["const"]):
            raise JsonSchemaValidationError("JSON_VALUE_CONST")
        kind = schema["type"]
        if kind == "null":
            if value is not None:
                raise JsonSchemaValidationError("JSON_VALUE_TYPE")
            return value
        if kind == "boolean":
            if not isinstance(value, bool):
                raise JsonSchemaValidationError("JSON_VALUE_TYPE")
            return value
        if kind == "integer":
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or abs(value) > 9_007_199_254_740_991
            ):
                raise JsonSchemaValidationError("JSON_VALUE_TYPE")
            _number_bounds(schema, value)
            return value
        if kind == "number":
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
            ):
                raise JsonSchemaValidationError("JSON_VALUE_TYPE")
            _number_bounds(schema, value)
            return value
        if kind == "string":
            if not isinstance(value, str):
                raise JsonSchemaValidationError("JSON_VALUE_TYPE")
            if len(value) < int(schema.get("minLength", 0)) or len(value) > int(
                schema.get("maxLength", MAX_VALUE_BYTES)
            ):
                raise JsonSchemaValidationError("JSON_VALUE_LENGTH")
            return value
        if kind == "array":
            if not isinstance(value, list):
                raise JsonSchemaValidationError("JSON_VALUE_TYPE")
            if len(value) < int(schema.get("minItems", 0)) or len(value) > int(
                schema.get("maxItems", MAX_NODES)
            ):
                raise JsonSchemaValidationError("JSON_VALUE_LENGTH")
            return [
                self._validate_value(
                    schema["items"], child, depth + 1, count, apply_defaults
                )
                for child in value
            ]
        if not isinstance(value, dict):
            raise JsonSchemaValidationError("JSON_VALUE_TYPE")
        properties = schema["properties"]
        assert isinstance(properties, dict)
        if not set(value) <= set(properties):
            raise JsonSchemaValidationError("JSON_VALUE_ADDITIONAL_PROPERTY")
        result = dict(value)
        if apply_defaults:
            for key, child in properties.items():
                if key not in result and isinstance(child, dict) and "default" in child:
                    result[key] = deepcopy(child["default"])
        required = schema.get("required", [])
        assert isinstance(required, list)
        if not set(required) <= set(result):
            raise JsonSchemaValidationError("JSON_VALUE_REQUIRED")
        for key, child in tuple(result.items()):
            result[key] = self._validate_value(
                properties[key], child, depth + 1, count, apply_defaults
            )
        return result


def _count(depth: int, count: list[int], code: str) -> None:
    count[0] += 1
    if depth > MAX_DEPTH or count[0] > MAX_NODES:
        raise JsonSchemaValidationError(code)


def validate_bounded_json_value(value: object) -> object:
    try:
        encoded = _json(value)
    except (TypeError, ValueError):
        raise JsonSchemaValidationError("JSON_VALUE_INVALID") from None
    if len(encoded.encode("utf-8")) > MAX_VALUE_BYTES:
        raise JsonSchemaValidationError("JSON_VALUE_TOO_LARGE")
    count = [0]

    def visit(child: object, depth: int) -> None:
        _count(depth, count, "JSON_VALUE_LIMIT_EXCEEDED")
        if child is None or isinstance(child, (str, bool)):
            return
        if isinstance(child, int):
            if abs(child) > 9_007_199_254_740_991:
                raise JsonSchemaValidationError("JSON_VALUE_INVALID")
            return
        if isinstance(child, float):
            if not math.isfinite(child):
                raise JsonSchemaValidationError("JSON_VALUE_INVALID")
            return
        if isinstance(child, list):
            for item in child:
                visit(item, depth + 1)
            return
        if isinstance(child, dict) and all(
            isinstance(key, str) for key in child
        ):
            for item in child.values():
                visit(item, depth + 1)
            return
        raise JsonSchemaValidationError("JSON_VALUE_INVALID")

    visit(value, 0)
    return value


def _number_bounds(schema: dict[str, object], value: int | float) -> None:
    if "minimum" in schema and value < schema["minimum"]:  # type: ignore[operator]
        raise JsonSchemaValidationError("JSON_VALUE_RANGE")
    if "maximum" in schema and value > schema["maximum"]:  # type: ignore[operator]
        raise JsonSchemaValidationError("JSON_VALUE_RANGE")


def _same_json(left: object, right: object) -> bool:
    return _json(left) == _json(right)


def _json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )

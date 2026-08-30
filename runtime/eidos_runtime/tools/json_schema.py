from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
import json
import math

from jsonschema import Draft202012Validator, validators
from jsonschema.exceptions import SchemaError, ValidationError
from referencing import Registry


MAX_SCHEMA_BYTES = 256 * 1024
MAX_VALUE_BYTES = 256 * 1024
MAX_DEPTH = 16
MAX_NODES = 4096
MAX_JSON_SAFE_INTEGER = 9_007_199_254_740_991
_KEYS = {
    "type", "properties", "required", "additionalProperties", "items",
    "enum", "const", "default", "minimum", "maximum", "minLength",
    "maxLength", "minItems", "maxItems", "description",
}
_TYPES = {"object", "array", "string", "integer", "number", "boolean", "null"}
_VALUE_ERROR_CODES = {
    "type": "JSON_VALUE_TYPE",
    "enum": "JSON_VALUE_ENUM",
    "const": "JSON_VALUE_CONST",
    "required": "JSON_VALUE_REQUIRED",
    "additionalProperties": "JSON_VALUE_ADDITIONAL_PROPERTY",
    "minLength": "JSON_VALUE_LENGTH",
    "maxLength": "JSON_VALUE_LENGTH",
    "minItems": "JSON_VALUE_LENGTH",
    "maxItems": "JSON_VALUE_LENGTH",
    "minimum": "JSON_VALUE_RANGE",
    "maximum": "JSON_VALUE_RANGE",
}


class JsonSchemaValidationError(ValueError):
    def __init__(self, code: str, path: str | None = None) -> None:
        self.code = code
        self.path = path
        super().__init__(code)


def _is_safe_integer(_checker: object, value: object) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and abs(value) <= MAX_JSON_SAFE_INTEGER
    )


def _is_finite_number(_checker: object, value: object) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return abs(value) <= MAX_JSON_SAFE_INTEGER
    return isinstance(value, float) and math.isfinite(value)


_TYPE_CHECKER = Draft202012Validator.TYPE_CHECKER.redefine_many({
    "integer": _is_safe_integer,
    "number": _is_finite_number,
})
_VALIDATOR = validators.extend(Draft202012Validator, type_checker=_TYPE_CHECKER)


class BoundedJsonSchema:
    """Validates Eidos's bounded, closed, offline JSON Schema subset."""

    def __init__(self, schema: dict[str, object]) -> None:
        self.schema = deepcopy(schema)
        _validate_schema(self.schema)
        try:
            Draft202012Validator.check_schema(self.schema)
        except SchemaError:
            raise JsonSchemaValidationError("JSON_SCHEMA_MALFORMED") from None
        # This empty Registry has no retriever. Preflight rejects every reference
        # keyword, so validation cannot resolve a network, file, or package URI.
        self._validator = _VALIDATOR(self.schema, registry=Registry())

    def validate(
        self, value: object, *, apply_defaults: bool = False,
    ) -> object:
        _validate_value_boundary(value)
        candidate = deepcopy(value)
        if apply_defaults:
            candidate = _apply_defaults(self.schema, candidate)
            # Defaults are schema metadata, not trusted values. Reapply Eidos's
            # value boundary to their fully expanded result before standard
            # validation, including the JSON-safe integer rule.
            _validate_value_boundary(candidate, enforce_safe_integers=True)
        errors = sorted(
            self._validator.iter_errors(candidate), key=_validation_error_sort_key,
        )
        if errors:
            raise JsonSchemaValidationError(
                _error_code(errors[0]), _validation_path(errors[0].absolute_path)
            )
        return candidate


def validate_bounded_json_value(value: object) -> object:
    """Validate JSON safety and Eidos's generic value size/shape bounds."""
    _validate_value_boundary(value, enforce_safe_integers=True)
    return value


def _validate_schema(schema: object) -> None:
    try:
        encoded = _json(schema)
    except (TypeError, ValueError):
        raise JsonSchemaValidationError("JSON_SCHEMA_MALFORMED") from None
    if len(encoded.encode("utf-8")) > MAX_SCHEMA_BYTES:
        raise JsonSchemaValidationError("JSON_SCHEMA_TOO_LARGE")
    _preflight_schema_node(schema, 0, [0])


def _preflight_schema_node(schema: object, depth: int, count: list[int]) -> None:
    _count(depth, count, "JSON_SCHEMA_LIMIT_EXCEEDED")
    if not isinstance(schema, dict) or not set(schema).issubset(_KEYS):
        raise JsonSchemaValidationError("JSON_SCHEMA_UNSUPPORTED")
    kind = schema.get("type")
    if not isinstance(kind, str) or kind not in _TYPES:
        raise JsonSchemaValidationError("JSON_SCHEMA_UNSUPPORTED")
    if "description" in schema and not isinstance(schema["description"], str):
        raise JsonSchemaValidationError("JSON_SCHEMA_MALFORMED")
    _validate_bounds(schema)
    _validate_literal_keywords(schema)

    structural = {"properties", "required", "additionalProperties", "items"}
    if kind == "object":
        properties = schema.get("properties")
        required = schema.get("required", [])
        if (
            not isinstance(properties, dict)
            or schema.get("additionalProperties") is not False
            or not isinstance(required, list)
            or not all(isinstance(key, str) for key in required)
            or len(set(required)) != len(required)
            or not all(key in properties for key in required)
        ):
            raise JsonSchemaValidationError("JSON_SCHEMA_UNSUPPORTED")
        for key, child in properties.items():
            if not isinstance(key, str):
                raise JsonSchemaValidationError("JSON_SCHEMA_MALFORMED")
            _preflight_schema_node(child, depth + 1, count)
        if structural.intersection(schema) - {
            "properties", "required", "additionalProperties",
        }:
            raise JsonSchemaValidationError("JSON_SCHEMA_MALFORMED")
        return
    if kind == "array":
        if "items" not in schema:
            raise JsonSchemaValidationError("JSON_SCHEMA_UNSUPPORTED")
        _preflight_schema_node(schema["items"], depth + 1, count)
        if structural.intersection(schema) - {"items"}:
            raise JsonSchemaValidationError("JSON_SCHEMA_MALFORMED")
        return
    if structural.intersection(schema):
        raise JsonSchemaValidationError("JSON_SCHEMA_MALFORMED")


def _validate_bounds(schema: dict[object, object]) -> None:
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
        if lower in schema and upper in schema and schema[lower] > schema[upper]:
            raise JsonSchemaValidationError("JSON_SCHEMA_MALFORMED")


def _validate_literal_keywords(schema: dict[object, object]) -> None:
    if "enum" in schema:
        enum = schema["enum"]
        if not isinstance(enum, list) or not enum:
            raise JsonSchemaValidationError("JSON_SCHEMA_MALFORMED")
        _validate_json_tree(enum, 0, [0], "JSON_SCHEMA_MALFORMED", True)
    if "const" in schema:
        _validate_json_tree(
            schema["const"], 0, [0], "JSON_SCHEMA_MALFORMED", True,
        )
    if "default" in schema:
        # Defaults are bounded JSON metadata. Their JSON-safe integer policy is
        # enforced after expansion by validate(..., apply_defaults=True), so
        # the resulting error stays on the value boundary.
        _validate_json_tree(
            schema["default"], 0, [0], "JSON_SCHEMA_MALFORMED", False,
        )


def _validate_value_boundary(
    value: object, *, enforce_safe_integers: bool = False,
) -> None:
    try:
        encoded = _json(value)
    except (TypeError, ValueError):
        raise JsonSchemaValidationError("JSON_VALUE_INVALID") from None
    if len(encoded.encode("utf-8")) > MAX_VALUE_BYTES:
        raise JsonSchemaValidationError("JSON_VALUE_TOO_LARGE")
    _validate_json_tree(
        value,
        0,
        [0],
        "JSON_VALUE_INVALID",
        enforce_safe_integers,
        limit_code="JSON_VALUE_LIMIT_EXCEEDED",
    )


def _validate_json_tree(
    value: object,
    depth: int,
    count: list[int],
    invalid_code: str,
    enforce_safe_integers: bool,
    *,
    limit_code: str = "JSON_SCHEMA_LIMIT_EXCEEDED",
) -> None:
    _count(depth, count, limit_code)
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int):
        if enforce_safe_integers and abs(value) > MAX_JSON_SAFE_INTEGER:
            raise JsonSchemaValidationError(invalid_code)
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise JsonSchemaValidationError(invalid_code)
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_tree(
                item, depth + 1, count, invalid_code, enforce_safe_integers,
                limit_code=limit_code,
            )
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for item in value.values():
            _validate_json_tree(
                item, depth + 1, count, invalid_code, enforce_safe_integers,
                limit_code=limit_code,
            )
        return
    raise JsonSchemaValidationError(invalid_code)


def _apply_defaults(schema: dict[str, object], value: object) -> object:
    kind = schema["type"]
    if kind == "object" and isinstance(value, dict):
        properties = schema["properties"]
        assert isinstance(properties, dict)
        result = dict(value)
        for key, child in properties.items():
            assert isinstance(key, str) and isinstance(child, dict)
            if key not in result and "default" in child:
                result[key] = deepcopy(child["default"])
            elif key in result:
                result[key] = _apply_defaults(child, result[key])
        return result
    if kind == "array" and isinstance(value, list):
        items = schema["items"]
        assert isinstance(items, dict)
        return [_apply_defaults(items, item) for item in value]
    return value


def _validation_error_sort_key(error: ValidationError) -> tuple[tuple[tuple[int, object], ...], tuple[tuple[int, object], ...], str]:
    return (
        _path_sort_key(error.absolute_path),
        _path_sort_key(error.absolute_schema_path),
        error.validator,
    )


def _path_sort_key(path: Iterable[object]) -> tuple[tuple[int, object], ...]:
    return tuple((0, value) if isinstance(value, int) else (1, str(value)) for value in path)


def _error_code(error: ValidationError) -> str:
    return _VALUE_ERROR_CODES.get(error.validator, "JSON_VALUE_INVALID")


def _validation_path(value: Iterable[object]) -> str | None:
    path = ""
    for part in value:
        if isinstance(part, int) and not isinstance(part, bool):
            path += f"[{part}]"
        elif isinstance(part, str) and part:
            path = f"{path}.{part}" if path else part
        else:
            return None
    return path or None


def _count(depth: int, count: list[int], code: str) -> None:
    count[0] += 1
    if depth > MAX_DEPTH or count[0] > MAX_NODES:
        raise JsonSchemaValidationError(code)


def _json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )

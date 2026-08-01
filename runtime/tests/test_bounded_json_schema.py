from __future__ import annotations

from collections.abc import Callable
import math
from pathlib import Path
import sys

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eidos_runtime.tools.json_schema import (  # noqa: E402
    BoundedJsonSchema,
    JsonSchemaValidationError,
    validate_bounded_json_value,
)
from eidos_runtime.tools import json_schema  # noqa: E402


def closed_object_schema() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "name": {"type": "string", "minLength": 1},
            "count": {"type": "integer", "default": 1},
            "settings": {
                "type": "object",
                "properties": {
                    "enabled": {"type": "boolean", "default": True},
                },
                "additionalProperties": False,
            },
        },
        "required": ["name"],
        "additionalProperties": False,
    }


def assert_code(code: str, operation: Callable[[], object]) -> None:
    with pytest.raises(JsonSchemaValidationError) as error:
        operation()
    assert error.value.code == code


@pytest.mark.parametrize(
    ("schema", "code"),
    [
        ({"type": "string", "unknown": True}, "JSON_SCHEMA_UNSUPPORTED"),
        ({"type": "string", "$ref": "#/value"}, "JSON_SCHEMA_UNSUPPORTED"),
        ({"type": "string", "$ref": "https://invalid.example/schema"}, "JSON_SCHEMA_UNSUPPORTED"),
        ({"type": "object", "additionalProperties": False}, "JSON_SCHEMA_UNSUPPORTED"),
        ({"type": "object", "properties": {}, "additionalProperties": True}, "JSON_SCHEMA_UNSUPPORTED"),
        ({"type": "object", "properties": {}, "required": [{"bad": True}], "additionalProperties": False}, "JSON_SCHEMA_UNSUPPORTED"),
        ({"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name", "name"], "additionalProperties": False}, "JSON_SCHEMA_UNSUPPORTED"),
        ({"type": "object", "properties": {}, "required": ["missing"], "additionalProperties": False}, "JSON_SCHEMA_UNSUPPORTED"),
        ({"type": "array"}, "JSON_SCHEMA_UNSUPPORTED"),
        ({"type": "string", "items": {"type": "string"}}, "JSON_SCHEMA_MALFORMED"),
        ({"type": "object", "properties": {}, "additionalProperties": False, "items": {"type": "string"}}, "JSON_SCHEMA_MALFORMED"),
        ({"type": "string", "minLength": True}, "JSON_SCHEMA_MALFORMED"),
        ({"type": "number", "minimum": math.inf}, "JSON_SCHEMA_MALFORMED"),
        ({"type": "string", "minLength": 2, "maxLength": 1}, "JSON_SCHEMA_MALFORMED"),
        ({"type": "string", "enum": []}, "JSON_SCHEMA_MALFORMED"),
        ({"type": "string", "enum": [9_007_199_254_740_992]}, "JSON_SCHEMA_MALFORMED"),
        ({"type": "string", "const": float("nan")}, "JSON_SCHEMA_MALFORMED"),
        ({"type": "string", "default": object()}, "JSON_SCHEMA_MALFORMED"),
    ],
)
def test_schema_policy_rejects_closed_subset_violations(
    schema: dict[str, object], code: str,
) -> None:
    assert_code(code, lambda: BoundedJsonSchema(schema))


@pytest.mark.parametrize(
    ("schema", "value", "code"),
    [
        ({"type": "integer"}, True, "JSON_VALUE_TYPE"),
        ({"type": "number"}, False, "JSON_VALUE_TYPE"),
        ({"type": "integer"}, 9_007_199_254_740_992, "JSON_VALUE_TYPE"),
        ({"type": "number"}, float("nan"), "JSON_VALUE_INVALID"),
        ({"type": "string", "enum": ["safe"]}, "other", "JSON_VALUE_ENUM"),
        ({"type": "string", "const": "safe"}, "other", "JSON_VALUE_CONST"),
        ({"type": "string", "minLength": 2}, "x", "JSON_VALUE_LENGTH"),
        ({"type": "array", "items": {"type": "string"}, "maxItems": 1}, ["first", "second"], "JSON_VALUE_LENGTH"),
        ({"type": "number", "minimum": 2}, 1, "JSON_VALUE_RANGE"),
    ],
)
def test_standard_validation_preserves_stable_error_codes(
    schema: dict[str, object], value: object, code: str,
) -> None:
    validator = BoundedJsonSchema(schema)
    assert_code(code, lambda: validator.validate(value))


def test_object_defaults_are_recursive_without_mutating_or_synthesizing_parents() -> None:
    validator = BoundedJsonSchema(closed_object_schema())
    value = {"name": "Eidos", "settings": {}}

    assert validator.validate(value, apply_defaults=True) == {
        "name": "Eidos",
        "count": 1,
        "settings": {"enabled": True},
    }
    assert value == {"name": "Eidos", "settings": {}}
    assert validator.validate({"name": "Eidos"}, apply_defaults=True) == {
        "name": "Eidos", "count": 1,
    }


def test_nested_arrays_and_objects_use_the_standard_validator() -> None:
    validator = BoundedJsonSchema({
        "type": "array",
        "items": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
            "additionalProperties": False,
        },
    })

    assert validator.validate([{"name": "Eidos"}]) == [{"name": "Eidos"}]
    assert_code("JSON_VALUE_TYPE", lambda: validator.validate([{"name": 1}]))


def test_required_and_closed_object_errors_are_stable() -> None:
    validator = BoundedJsonSchema(closed_object_schema())

    assert_code("JSON_VALUE_REQUIRED", lambda: validator.validate({}))
    assert_code(
        "JSON_VALUE_ADDITIONAL_PROPERTY",
        lambda: validator.validate({"name": "Eidos", "extra": True}),
    )


def test_defaults_are_validated_without_bypassing_required_or_closed_objects() -> None:
    invalid_default = BoundedJsonSchema({
        "type": "object",
        "properties": {"count": {"type": "integer", "default": "wrong"}},
        "additionalProperties": False,
    })
    required = BoundedJsonSchema({
        "type": "object",
        "properties": {
            "name": {"type": "string", "default": "Eidos"},
            "token": {"type": "string"},
        },
        "required": ["token"],
        "additionalProperties": False,
    })

    assert_code("JSON_VALUE_TYPE", lambda: invalid_default.validate({}, apply_defaults=True))
    assert_code("JSON_VALUE_REQUIRED", lambda: required.validate({}, apply_defaults=True))


@pytest.mark.parametrize(
    "value",
    [
        {1: "not-json"},
        {"nested": float("inf")},
        9_007_199_254_740_992,
    ],
)
def test_generic_bounded_json_validation_preserves_json_safety(value: object) -> None:
    assert_code("JSON_VALUE_INVALID", lambda: validate_bounded_json_value(value))


def test_schema_and_value_byte_limits_remain_eidos_owned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(json_schema, "MAX_SCHEMA_BYTES", 8)
    assert_code(
        "JSON_SCHEMA_TOO_LARGE",
        lambda: BoundedJsonSchema({"type": "string"}),
    )

    monkeypatch.setattr(json_schema, "MAX_SCHEMA_BYTES", 256 * 1024)
    monkeypatch.setattr(json_schema, "MAX_VALUE_BYTES", 8)
    validator = BoundedJsonSchema({"type": "string"})
    assert_code("JSON_VALUE_TOO_LARGE", lambda: validator.validate("Eidos!!"))


@pytest.mark.parametrize(
    ("factory", "code"),
    [
        (
            lambda: BoundedJsonSchema({
                "type": "array",
                "items": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            }),
            "JSON_SCHEMA_LIMIT_EXCEEDED",
        ),
        (
            lambda: validate_bounded_json_value([["value"]]),
            "JSON_VALUE_LIMIT_EXCEEDED",
        ),
    ],
)
def test_depth_limit_remains_bounded(
    monkeypatch: pytest.MonkeyPatch, factory: Callable[[], object], code: str,
) -> None:
    monkeypatch.setattr(json_schema, "MAX_DEPTH", 1)
    assert_code(code, factory)


def test_node_limit_remains_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(json_schema, "MAX_NODES", 2)
    assert_code(
        "JSON_VALUE_LIMIT_EXCEEDED",
        lambda: validate_bounded_json_value(["first", "second"]),
    )


def test_first_standard_validation_error_is_deterministic() -> None:
    validator = BoundedJsonSchema({
        "type": "object",
        "properties": {"name": {"type": "string"}},
        "required": ["name"],
        "additionalProperties": False,
    })

    first = []
    for _ in range(3):
        with pytest.raises(JsonSchemaValidationError) as error:
            validator.validate({"extra": True})
        first.append(error.value.code)
    assert first == ["JSON_VALUE_ADDITIONAL_PROPERTY"] * 3


def test_defaults_are_rebounded_after_expansion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validator = BoundedJsonSchema({
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "payload": {"type": "string", "default": "0123456789"},
            },
            "additionalProperties": False,
        },
    })
    value = [{}, {}, {}]
    monkeypatch.setattr(json_schema, "MAX_VALUE_BYTES", 32)

    assert_code(
        "JSON_VALUE_TOO_LARGE",
        lambda: validator.validate(value, apply_defaults=True),
    )
    assert value == [{}, {}, {}]


@pytest.mark.parametrize(
    ("limit_name", "limit", "property_schema", "default", "code"),
    [
        (
            "MAX_NODES", 3,
            {"type": "array", "items": {"type": "string"}},
            ["first", "second"],
            "JSON_VALUE_LIMIT_EXCEEDED",
        ),
        (
            "MAX_DEPTH", 2,
            {
                "type": "array",
                "items": {"type": "array", "items": {"type": "string"}},
            },
            [["default"]],
            "JSON_VALUE_LIMIT_EXCEEDED",
        ),
    ],
)
def test_nested_defaults_are_rebounded_after_expansion(
    monkeypatch: pytest.MonkeyPatch,
    limit_name: str,
    limit: int,
    property_schema: dict[str, object],
    default: object,
    code: str,
) -> None:
    property_schema = {**property_schema, "default": default}
    validator = BoundedJsonSchema({
        "type": "object",
        "properties": {"value": property_schema},
        "additionalProperties": False,
    })
    monkeypatch.setattr(json_schema, limit_name, limit)

    assert_code(code, lambda: validator.validate({}, apply_defaults=True))


def test_unsafe_integer_default_is_rejected_as_an_expanded_value() -> None:
    validator = BoundedJsonSchema({
        "type": "object",
        "properties": {
            "count": {"type": "integer", "default": 9_007_199_254_740_992},
        },
        "additionalProperties": False,
    })

    assert_code("JSON_VALUE_INVALID", lambda: validator.validate({}, apply_defaults=True))

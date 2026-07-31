from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import Field, ValidationError

from eidos_runtime.models import (
    EidosFrozenModel,
    EidosFrozenStrictModel,
    EidosModel,
    EidosStrictModel,
)


class NestedValue(EidosModel):
    model_id: str
    optional_value: str | None = None


class AliasValue(EidosModel):
    model_id: str
    api_url: str
    json_rpc_id: str
    tool_call_id: str
    workspace_id: str
    supports_tools: bool
    explicit_override: str = Field(alias="legacyName")
    nested_value: NestedValue
    created_at: datetime


class DefaultValue(EidosModel):
    name: str = Field(default="", min_length=1)


class StrictValue(EidosStrictModel):
    value: int


class FrozenValue(EidosFrozenModel):
    value: str


class FrozenStrictValue(EidosFrozenStrictModel):
    value: int


def alias_value() -> AliasValue:
    return AliasValue(
        model_id="model-1",
        api_url="https://example.test",
        json_rpc_id="rpc-1",
        tool_call_id="call-1",
        workspace_id="workspace-1",
        supports_tools=True,
        legacyName="legacy",
        nested_value=NestedValue(model_id="nested-1"),
        created_at=datetime(2026, 7, 31, tzinfo=UTC),
    )


def test_model_rejects_unknown_fields_and_validates_defaults() -> None:
    with pytest.raises(ValidationError):
        AliasValue.model_validate({"modelId": "model-1", "unknown": True})
    with pytest.raises(ValidationError):
        DefaultValue()


def test_model_accepts_snake_case_camel_case_and_explicit_aliases() -> None:
    snake_case = alias_value()
    camel_case = AliasValue.model_validate(
        {
            "modelId": "model-1",
            "apiUrl": "https://example.test",
            "jsonRpcId": "rpc-1",
            "toolCallId": "call-1",
            "workspaceId": "workspace-1",
            "supportsTools": True,
            "legacyName": "legacy",
            "nestedValue": {"modelId": "nested-1"},
            "createdAt": "2026-07-31T00:00:00Z",
        }
    )

    assert snake_case.explicit_override == camel_case.explicit_override == "legacy"
    assert camel_case.model_id == "model-1"


def test_internal_and_wire_serialization_are_json_compatible_and_explicit() -> None:
    value = alias_value()

    assert value.to_internal_dict() == {
        "model_id": "model-1",
        "api_url": "https://example.test",
        "json_rpc_id": "rpc-1",
        "tool_call_id": "call-1",
        "workspace_id": "workspace-1",
        "supports_tools": True,
        "explicit_override": "legacy",
        "nested_value": {"model_id": "nested-1", "optional_value": None},
        "created_at": "2026-07-31T00:00:00Z",
    }
    assert value.to_wire_dict() == {
        "modelId": "model-1",
        "apiUrl": "https://example.test",
        "jsonRpcId": "rpc-1",
        "toolCallId": "call-1",
        "workspaceId": "workspace-1",
        "supportsTools": True,
        "legacyName": "legacy",
        "nestedValue": {"modelId": "nested-1", "optionalValue": None},
        "createdAt": "2026-07-31T00:00:00Z",
    }
    assert (
        "optional_value"
        not in value.to_internal_dict(exclude_none=True)["nested_value"]
    )
    assert "optionalValue" not in value.to_wire_dict(exclude_none=True)["nestedValue"]


@pytest.mark.parametrize("invalid", ["1", 1.0, True])
def test_strict_models_reject_implicit_integer_conversions(invalid: object) -> None:
    with pytest.raises(ValidationError):
        StrictValue(value=invalid)
    assert StrictValue(value=1).value == 1


def test_frozen_models_reject_mutation_and_allow_explicit_copy() -> None:
    value = FrozenValue(value="one")
    with pytest.raises(ValidationError):
        value.value = "two"
    assert value.model_copy(update={"value": "two"}) == FrozenValue(value="two")
    assert FrozenStrictValue(value=1).value == 1


def test_model_json_schema_defaults_to_internal_names_and_can_use_wire_aliases() -> (
    None
):
    internal = AliasValue.model_json_schema()
    wire = AliasValue.model_json_schema(by_alias=True)

    assert "model_id" in internal["properties"]
    assert "modelId" in wire["properties"]

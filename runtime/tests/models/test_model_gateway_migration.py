from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from eidos_runtime.model_gateway.capability import (
    TestConnectionResult as ConnectionResult,
)
from eidos_runtime.model_gateway.errors import EidosModelError
from eidos_runtime.model_gateway.events import ModelTextDelta
from eidos_runtime.model_gateway.models import (
    CapabilityProbeSource,
    CapabilitySnapshot,
    CapabilityWarning,
    ModelProfile,
    ReasoningMode,
    RetryPolicy,
    RunModelSnapshot,
    WireAPI,
)
from eidos_runtime.model_gateway.usage import (
    NormalizedCost,
    NormalizedUsage,
    PricingReference,
)
from eidos_runtime.model_gateway.presets import ProviderPreset
from eidos_runtime.model_gateway.retry import RetryDecision, RetryState
from eidos_runtime.models import EidosFrozenStrictModel


NOW = datetime(2026, 7, 31, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[2]


def profile() -> ModelProfile:
    return ModelProfile(
        id="profile-1",
        name="DeepSeek",
        provider="deepseek",
        base_url="https://api.deepseek.com",
        auth_reference="env:DEEPSEEK_API_KEY",
        wire_api=WireAPI.OPENAI_CHAT_COMPLETIONS,
        model_id="deepseek-chat",
        context_window=128_000,
        max_output_tokens=8_192,
        reasoning_mode=ReasoningMode.NONE,
        request_timeout=30.0,
        retry_policy=RetryPolicy(max_attempts=3),
        created_at=NOW,
        updated_at=NOW,
    )


def capability(value: ModelProfile) -> CapabilitySnapshot:
    return CapabilitySnapshot.conservative(
        value,
        probe_source=CapabilityProbeSource.USER_DECLARATION,
        probe_version="test-v1",
        probed_at=NOW,
        verified={"supports_tools": True},
        snapshot_id="capability-1",
    )


@pytest.mark.parametrize(
    "model",
    [
        RetryPolicy(),
        profile(),
        CapabilityWarning(
            code="CAPABILITY_UNVERIFIED",
            message="not verified",
            source=CapabilityProbeSource.CONSERVATIVE_DEFAULT,
        ),
        capability(profile()),
        RunModelSnapshot(
            profile=profile(), capability=capability(profile()), frozen_at=NOW
        ),
        NormalizedUsage(provider_reported=True, estimated=False),
        PricingReference(id="pricing-1", source="test", effective_at="2026-07-31"),
        NormalizedCost(),
        ConnectionResult(
            success=True,
            profile_valid=True,
            endpoint_identity="https://api.example.test",
            probe_duration_ms=0,
        ),
        EidosModelError(
            code="MODEL_INVALID_REQUEST",
            message="invalid request",
            retryable=False,
            provider="test",
            wire_api=WireAPI.OPENAI_CHAT_COMPLETIONS,
            model_id="test-model",
            attempt_id="attempt-1",
        ),
        ModelTextDelta(
            run_id="run-1",
            attempt_id="attempt-1",
            sequence=0,
            timestamp=NOW,
            text="hello",
        ),
        ProviderPreset(
            id="test",
            display_name="Test",
            provider_adapter_id="test",
            default_wire_api=WireAPI.OPENAI_CHAT_COMPLETIONS,
            default_base_url="https://api.example.test",
        ),
        RetryState(attempt_number=1),
        RetryDecision(retry=False, reason="test", backoff_seconds=0),
    ],
)
def test_migrated_model_gateway_values_use_shared_frozen_strict_base(
    model: EidosFrozenStrictModel,
) -> None:
    assert isinstance(model, EidosFrozenStrictModel)
    with pytest.raises(ValidationError):
        model.__setattr__(next(iter(type(model).model_fields)), "changed")


def test_model_profile_keeps_snake_case_input_schema_and_defaults() -> None:
    value = profile()
    schema = ModelProfile.model_json_schema()

    assert value.schema_version == 1
    assert "model_id" in schema["properties"]
    assert schema["properties"]["model_id"]["maxLength"] == 256
    with pytest.raises(ValidationError):
        ModelProfile.model_validate({**value.to_internal_dict(), "unexpected": True})


@pytest.mark.parametrize(
    ("model_type", "field_name"),
    [
        (RetryPolicy, "max_attempts"),
        (ModelProfile, "model_id"),
        (CapabilityWarning, "capability"),
        (CapabilitySnapshot, "profile_id"),
        (RunModelSnapshot, "frozen_at"),
        (NormalizedUsage, "input_tokens"),
        (PricingReference, "effective_at"),
        (NormalizedCost, "pricing_reference"),
    ],
)
def test_migrated_model_schemas_keep_internal_field_names(
    model_type: type[EidosFrozenStrictModel], field_name: str
) -> None:
    assert field_name in model_type.model_json_schema()["properties"]


def test_model_profile_adds_compatible_alias_input_and_wire_serialization() -> None:
    value = profile()
    camel_case = ModelProfile.model_validate(value.model_dump(by_alias=True))

    assert camel_case == value
    assert value.to_internal_dict()["model_id"] == "deepseek-chat"
    assert value.to_wire_dict()["modelId"] == "deepseek-chat"
    assert "contextWindow" in value.to_wire_dict()


def test_snapshot_and_usage_models_keep_defaults_frozen_behavior_and_json_data() -> (
    None
):
    value = profile()
    snapshot = capability(value)
    run_snapshot = RunModelSnapshot(
        profile=value,
        capability=snapshot,
        frozen_at=NOW,
    )
    usage = NormalizedUsage(provider_reported=True, estimated=False)

    assert snapshot.to_internal_dict()["profile_id"] == value.id
    assert run_snapshot.to_wire_dict()["frozenAt"] == "2026-07-31T00:00:00Z"
    assert usage.to_internal_dict()["input_tokens"] is None
    assert "inputTokens" not in usage.to_wire_dict(exclude_none=True)
    assert run_snapshot.model_copy(update={"lease_id": "lease-2"}).lease_id == "lease-2"


def test_migrated_modules_do_not_directly_import_pydantic_base_model() -> None:
    for path in (ROOT / "eidos_runtime/model_gateway").glob("*.py"):
        assert "BaseModel" not in path.read_text(encoding="utf-8")

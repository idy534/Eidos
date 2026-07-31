from __future__ import annotations

from datetime import UTC, datetime
import inspect
from pathlib import Path
import sys


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.model_gateway.capabilities import (  # noqa: E402
    resolve_model_capabilities,
)
from eidos_runtime.model_gateway.models import (  # noqa: E402
    ModelProfile,
    ReasoningMode,
    RetryPolicy,
    WireAPI,
)
from eidos_runtime.model_gateway.presets import ProviderPreset  # noqa: E402


NOW = datetime(2026, 7, 31, tzinfo=UTC)


def profile(**updates: object) -> ModelProfile:
    values: dict[str, object] = {
        "id": "profile-1",
        "name": "Example",
        "provider": "example",
        "base_url": "https://api.example.test",
        "auth_reference": "env:EIDOS_TEST_MODEL_KEY",
        "wire_api": WireAPI.OPENAI_CHAT_COMPLETIONS,
        "model_id": "example-model",
        "reasoning_mode": ReasoningMode.NONE,
        "request_timeout": 30.0,
        "retry_policy": RetryPolicy(max_attempts=3),
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(updates)
    return ModelProfile.model_validate(values)


def preset(**updates: object) -> ProviderPreset:
    values: dict[str, object] = {
        "id": "example",
        "display_name": "Example",
        "default_wire_api": WireAPI.OPENAI_CHAT_COMPLETIONS,
        "default_base_url": "https://api.example.test",
        "capability_hints": {
            "supports_tools": True,
            "supports_parallel_tools": True,
            "supports_images": True,
            "supports_structured_output": True,
            "supports_prompt_cache": True,
        },
        "context_window": 32_000,
        "max_output_tokens": 4_096,
    }
    values.update(updates)
    return ProviderPreset.model_validate(values)


def test_explicit_tools_declaration_overrides_preset() -> None:
    assert resolve_model_capabilities(profile(supports_tools=True), preset()).supports_tools


def test_explicit_false_declaration_cannot_be_overridden_by_preset() -> None:
    resolved = resolve_model_capabilities(
        profile(supports_tools=False, supports_parallel_tools=True), preset()
    )

    assert not resolved.supports_tools
    assert not resolved.supports_parallel_tools


def test_undeclared_capability_uses_static_preset() -> None:
    resolved = resolve_model_capabilities(profile(), preset())

    assert resolved.supports_tools
    assert resolved.supports_structured_output
    assert resolved.sources["supports_tools"].value == "built_in_preset"


def test_unknown_capabilities_have_conservative_defaults_without_guessed_limits() -> None:
    resolved = resolve_model_capabilities(
        profile(), preset(capability_hints={}, context_window=None, max_output_tokens=None)
    )

    assert not resolved.supports_tools
    assert not resolved.supports_parallel_tools
    assert resolved.context_window is None
    assert resolved.max_output_tokens is None


def test_same_input_produces_same_declared_capability_fields() -> None:
    first = resolve_model_capabilities(profile(), preset())
    second = resolve_model_capabilities(profile(), preset())

    assert first.model_dump() == second.model_dump()


def test_resolution_is_pure_and_does_not_depend_on_http_or_credentials() -> None:
    source = inspect.getsource(resolve_model_capabilities)

    assert "httpx" not in source
    assert "ModelSecretStore" not in source
    assert ".resolve(" not in source
    assert "open(" not in source

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import sys
import unittest

from pydantic import ValidationError


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.model_gateway.errors import (  # noqa: E402
    EidosModelAuthenticationError,
    EidosModelContextExceededError,
    EidosModelRateLimitError,
    normalize_http_error,
)
from eidos_runtime.model_gateway.events import (  # noqa: E402
    ModelTextDelta,
    ModelToolCallCompleted,
)
from eidos_runtime.model_gateway.models import (  # noqa: E402
    CapabilityProbeSource,
    CapabilitySnapshot,
    ModelProfile,
    ReasoningMode,
    RetryPolicy,
    WireAPI,
)
from eidos_runtime.model_gateway.presets import PRESETS  # noqa: E402
from eidos_runtime.model_gateway.registry import AdapterRegistry  # noqa: E402
from eidos_runtime.model_gateway.retry import RetryState, retry_decision  # noqa: E402
from eidos_runtime.model_gateway.usage import NormalizedUsage  # noqa: E402


NOW = datetime(2026, 7, 30, tzinfo=UTC)


def profile(**updates: object) -> ModelProfile:
    values: dict[str, object] = {
        "id": "profile-1",
        "name": "DeepSeek",
        "provider": "deepseek",
        "base_url": "https://api.deepseek.com",
        "auth_reference": "env:DEEPSEEK_API_KEY",
        "wire_api": WireAPI.OPENAI_CHAT_COMPLETIONS,
        "model_id": "deepseek-chat",
        "reasoning_mode": ReasoningMode.NONE,
        "request_timeout": 30.0,
        "retry_policy": RetryPolicy(max_attempts=3),
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(updates)
    return ModelProfile.model_validate(values)


class ModelGatewayDomainTests(unittest.TestCase):
    def test_profile_is_frozen_closed_and_rejects_raw_secrets_or_unsafe_urls(self) -> None:
        value = profile()
        with self.assertRaises(ValidationError):
            value.name = "changed"  # type: ignore[misc]
        with self.assertRaises(ValidationError):
            profile(auth_reference="sk-this-is-a-raw-secret")
        for url in ("file:///tmp/provider", "http://127.0.0.1:8000", "ftp://host"):
            with self.subTest(url=url), self.assertRaises(ValidationError):
                profile(base_url=url)

    def test_unknown_capabilities_default_false_and_remain_distinct_from_declarations(self) -> None:
        declared = profile(
            supports_tools=True,
            supports_parallel_tools=None,
            supports_structured_output=True,
        )
        snapshot = CapabilitySnapshot.conservative(
            declared,
            probe_source=CapabilityProbeSource.ACTIVE_PROBE,
            probe_version="r2-v1",
            probed_at=NOW,
            verified={"supports_tools": True},
        )

        self.assertTrue(declared.supports_structured_output)
        self.assertTrue(snapshot.supports_tools)
        self.assertFalse(snapshot.supports_parallel_tools)
        self.assertFalse(snapshot.supports_structured_output)
        self.assertTrue(any(w.code == "CAPABILITY_UNVERIFIED" for w in snapshot.warnings))
        with self.assertRaises(ValidationError):
            snapshot.supports_tools = False  # type: ignore[misc]

    def test_provider_and_wire_are_independent_registry_dimensions(self) -> None:
        registry = AdapterRegistry.default()
        provider = registry.provider("deepseek")
        wire = registry.wire(WireAPI.OPENAI_CHAT_COMPLETIONS)

        self.assertEqual(provider.provider_id, "openai_compatible")
        self.assertEqual(wire.wire_api, WireAPI.OPENAI_CHAT_COMPLETIONS)
        self.assertIsNot(provider, wire)
        self.assertEqual(
            registry.provider("custom").provider_id,
            "openai_compatible",
        )

    def test_required_presets_exist_without_forcing_model_ids(self) -> None:
        self.assertEqual(
            set(PRESETS),
            {
                "openai",
                "deepseek",
                "volcengine_ark",
                "minimax",
                "moonshot",
                "qwen",
                "custom_openai_compatible",
            },
        )
        self.assertEqual(
            tuple(WireAPI),
            (
                WireAPI.OPENAI_RESPONSES,
                WireAPI.OPENAI_CHAT_COMPLETIONS,
            ),
        )
        self.assertTrue(all(preset.model_id is None for preset in PRESETS.values()))

    def test_normalized_models_do_not_accept_native_payloads(self) -> None:
        delta = ModelTextDelta(
            run_id="run-1",
            attempt_id="attempt-1",
            sequence=1,
            timestamp=NOW,
            text="hello",
        )
        usage = NormalizedUsage(
            input_tokens=2,
            output_tokens=1,
            total_tokens=3,
            provider_reported=True,
            estimated=False,
        )
        self.assertEqual(delta.text, "hello")
        self.assertEqual(usage.total_tokens, 3)
        with self.assertRaises(ValidationError):
            ModelToolCallCompleted.model_validate({
                "run_id": "run-1",
                "attempt_id": "attempt-1",
                "sequence": 2,
                "timestamp": NOW,
                "tool_call_id": "call-1",
                "name": "read_file",
                "arguments": {},
                "native_payload": {"secret": "must not escape"},
            })


class ModelGatewayErrorAndRetryTests(unittest.TestCase):
    def test_provider_statuses_normalize_to_typed_safe_errors(self) -> None:
        common = {
            "provider": "openai",
            "wire_api": WireAPI.OPENAI_RESPONSES,
            "model_id": "gpt-5",
            "attempt_id": "attempt-1",
        }
        self.assertIsInstance(normalize_http_error(401, **common), EidosModelAuthenticationError)
        self.assertIsInstance(normalize_http_error(429, **common), EidosModelRateLimitError)
        self.assertIsInstance(
            normalize_http_error(
                400,
                diagnostic="maximum context length exceeded",
                **common,
            ),
            EidosModelContextExceededError,
        )
        error = normalize_http_error(
            500,
            diagnostic="Authorization: Bearer sk-secret-value",
            **common,
        )
        self.assertNotIn("sk-secret-value", error.model_dump_json())

    def test_retry_only_allows_transport_changes_before_unsafe_progress(self) -> None:
        transient = normalize_http_error(
            503,
            provider="deepseek",
            wire_api=WireAPI.OPENAI_CHAT_COMPLETIONS,
            model_id="deepseek-chat",
            attempt_id="attempt-1",
        )
        self.assertTrue(retry_decision(transient, RetryState(attempt_number=1)).retry)
        self.assertFalse(
            retry_decision(
                transient,
                RetryState(attempt_number=1, complete_tool_call_emitted=True),
            ).retry
        )
        self.assertFalse(
            retry_decision(
                normalize_http_error(
                    401,
                    provider="deepseek",
                    wire_api=WireAPI.OPENAI_CHAT_COMPLETIONS,
                    model_id="deepseek-chat",
                    attempt_id="attempt-1",
                ),
                RetryState(attempt_number=1),
            ).retry
        )


if __name__ == "__main__":
    unittest.main()

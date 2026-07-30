from __future__ import annotations

from datetime import UTC, datetime
import os
from pathlib import Path
import tempfile
import unittest


import sys
RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))

from pydantic_ai.models.openai import (  # noqa: E402
    OpenAIChatModel,
    OpenAIResponsesModel,
)

from eidos_runtime.model_gateway.auth import ModelSecretStore  # noqa: E402
from eidos_runtime.model_gateway.gateway import ModelGateway  # noqa: E402
from eidos_runtime.model_gateway.models import (  # noqa: E402
    CapabilityProbeSource,
    CapabilitySnapshot,
    ModelProfile,
    ReasoningMode,
    RetryPolicy,
    RunModelSnapshot,
    WireAPI,
)


NOW = datetime(2026, 7, 30, tzinfo=UTC)


def snapshot(provider: str, wire: WireAPI, base_url: str) -> RunModelSnapshot:
    profile = ModelProfile(
        id=f"profile-{provider}",
        name=provider,
        provider=provider,
        base_url=base_url,
        auth_reference=f"env:{provider.upper()}_API_KEY",
        wire_api=wire,
        model_id="fixture-model",
        context_window=128_000,
        max_output_tokens=4_096,
        reasoning_mode=ReasoningMode.NONE,
        supports_tools=True,
        request_timeout=30.0,
        retry_policy=RetryPolicy(max_attempts=3),
        created_at=NOW,
        updated_at=NOW,
    )
    capability = CapabilitySnapshot.conservative(
        profile,
        snapshot_id=f"capability-{provider}",
        probe_source=CapabilityProbeSource.ACTIVE_PROBE,
        probe_version="r2-v1",
        probed_at=NOW,
        verified={
            "supports_tools": True,
            "context_window": 128_000,
            "max_output_tokens": 4_096,
        },
    )
    return RunModelSnapshot(profile=profile, capability=capability, frozen_at=NOW)


class ModelGatewayLeaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-gateway-lease-")
        self.secrets = ModelSecretStore(Path(self.temporary.name))
        self.secrets.initialize()
        for provider in ("openai", "deepseek"):
            os.environ[f"{provider.upper()}_API_KEY"] = "provider-key-value-123456"

    def tearDown(self) -> None:
        for provider in ("openai", "deepseek"):
            os.environ.pop(f"{provider.upper()}_API_KEY", None)
        self.temporary.cleanup()

    def test_supported_wire_families_build_direct_models_with_stable_lease_metadata(self) -> None:
        cases = (
            (
                snapshot("openai", WireAPI.OPENAI_RESPONSES, "https://api.openai.com/v1"),
                OpenAIResponsesModel,
            ),
            (
                snapshot("deepseek", WireAPI.OPENAI_CHAT_COMPLETIONS, "https://api.deepseek.com"),
                OpenAIChatModel,
            ),
        )
        for frozen, expected_model_type in cases:
            with self.subTest(wire=frozen.profile.wire_api):
                lease = ModelGateway(self.secrets).acquire_lease(frozen)
                try:
                    self.assertIsInstance(lease.client._model, expected_model_type)
                    self.assertEqual(lease.profile_snapshot, frozen.profile)
                    self.assertEqual(lease.capability_snapshot, frozen.capability)
                    self.assertEqual(lease.provider_adapter.provider_id, (
                        "openai_compatible"
                        if frozen.profile.provider == "deepseek"
                        else frozen.profile.provider
                    ))
                    self.assertEqual(lease.wire_adapter.wire_api, frozen.profile.wire_api)
                    self.assertEqual(lease.auth_reference, frozen.profile.auth_reference)
                    self.assertEqual(
                        lease.client.profile_snapshot.retry_max_attempts,
                        frozen.profile.retry_policy.max_attempts,
                    )
                    self.assertNotIn("provider-key-value", repr(lease))
                    self.assertFalse(lease.closed)
                finally:
                    lease.close()
                self.assertTrue(lease.closed)

    def test_gateway_requires_effective_context_limits_before_acquiring(self) -> None:
        frozen = snapshot(
            "deepseek",
            WireAPI.OPENAI_CHAT_COMPLETIONS,
            "https://api.deepseek.com",
        )
        invalid = frozen.model_copy(update={
            "profile": frozen.profile.model_copy(update={"context_window": None}),
            "capability": frozen.capability.model_copy(update={"context_window": None}),
        })
        with self.assertRaisesRegex(ValueError, "context window"):
            ModelGateway(self.secrets).acquire_lease(invalid)


if __name__ == "__main__":
    unittest.main()

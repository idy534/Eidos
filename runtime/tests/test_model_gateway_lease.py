from __future__ import annotations

import asyncio
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
from eidos_runtime.model_gateway.capabilities import resolve_model_capabilities  # noqa: E402
from eidos_runtime.model_gateway.gateway import ModelGateway  # noqa: E402
from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel  # noqa: E402
from eidos_runtime.model_gateway.pydantic_factory import (  # noqa: E402
    build_pydantic_model,
)
from eidos_runtime.model_gateway.models import (  # noqa: E402
    ModelProfile,
    ReasoningMode,
    RetryPolicy,
    RunModelSnapshot,
    WireAPI,
)
from eidos_runtime.model_gateway.presets import PRESETS  # noqa: E402


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
    capability = resolve_model_capabilities(
        profile, PRESETS.get(provider, PRESETS["deepseek"])
    ).model_copy(
        update={"id": f"capability-{provider}"}
    )
    return RunModelSnapshot(profile=profile, capability=capability, frozen_at=NOW)


class ModelGatewayLeaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-gateway-lease-")
        self.secrets = ModelSecretStore(Path(self.temporary.name))
        self.secrets.initialize()
        self.kernel = RuntimeAsyncKernel()
        self.kernel.start()
        for provider in PRESETS:
            os.environ[f"{provider.upper()}_API_KEY"] = "provider-key-value-123456"

    def tearDown(self) -> None:
        for provider in PRESETS:
            os.environ.pop(f"{provider.upper()}_API_KEY", None)
        self.kernel.close()
        self.temporary.cleanup()

    def test_supported_profiles_build_their_pydantic_ai_model_class(self) -> None:
        cases = (
            (
                snapshot("openai", WireAPI.OPENAI_RESPONSES, "https://api.openai.com/v1"),
                OpenAIResponsesModel,
            ),
            (
                snapshot("deepseek", WireAPI.OPENAI_CHAT_COMPLETIONS, "https://api.deepseek.com"),
                OpenAIChatModel,
            ),
            (
                snapshot("moonshot", WireAPI.OPENAI_CHAT_COMPLETIONS, "https://api.moonshot.cn/v1"),
                OpenAIChatModel,
            ),
            (
                snapshot("qwen", WireAPI.OPENAI_CHAT_COMPLETIONS, "https://dashscope.aliyuncs.com/compatible-mode/v1"),
                OpenAIChatModel,
            ),
            (
                snapshot("minimax", WireAPI.OPENAI_CHAT_COMPLETIONS, "https://api.minimax.chat/v1"),
                OpenAIChatModel,
            ),
            (
                snapshot("volcengine_ark", WireAPI.OPENAI_CHAT_COMPLETIONS, "https://ark.cn-beijing.volces.com/api/v3"),
                OpenAIChatModel,
            ),
            (
                snapshot("custom_openai_compatible", WireAPI.OPENAI_CHAT_COMPLETIONS, "https://api.example.test/v1"),
                OpenAIChatModel,
            ),
        )
        for frozen, expected_model_type in cases:
            with self.subTest(provider=frozen.profile.provider, wire=frozen.profile.wire_api):
                built = build_pydantic_model(frozen, "provider-key-value-123456")
                try:
                    self.assertIsInstance(built.model, expected_model_type)
                    self.assertIsNotNone(built.model.profile)
                    self.assertEqual(str(built.provider_client.base_url).rstrip("/"), frozen.profile.base_url)
                    self.assertEqual(built.provider_client.api_key, "provider-key-value-123456")
                    self.assertEqual(built.provider_client.timeout.read, frozen.profile.request_timeout)
                    self.assertEqual(built.provider_client.max_retries, 0)
                finally:
                    asyncio.run(built.provider_client.close())

    def test_lease_preserves_eidos_metadata_without_adapter_objects(self) -> None:
        frozen = snapshot(
            "deepseek",
            WireAPI.OPENAI_CHAT_COMPLETIONS,
            "https://api.deepseek.com",
        )
        lease = ModelGateway(self.secrets, async_kernel=self.kernel).acquire_lease(frozen)
        original_release = lease._release
        close_count = 0

        def release_once() -> None:
            nonlocal close_count
            close_count += 1
            assert original_release is not None
            original_release()

        lease._release = release_once
        try:
            self.assertEqual(lease.profile_snapshot, frozen.profile)
            self.assertEqual(lease.capability_snapshot, frozen.capability)
            self.assertEqual(lease.auth_reference, frozen.profile.auth_reference)
            self.assertFalse(hasattr(lease, "provider" + "_adapter"))
            self.assertFalse(hasattr(lease, "wire" + "_adapter"))
            self.assertEqual(
                lease.client.profile_snapshot.retry_max_attempts,
                frozen.profile.retry_policy.max_attempts,
            )
            self.assertEqual(
                lease.client.profile_snapshot.wire_api,
                "chat_completions",
            )
            self.assertNotIn("provider-key-value", repr(lease))
            self.assertNotIn("provider-key-value", repr(lease.client))
            self.assertNotIn(
                "provider-key-value", lease.profile_snapshot.model_dump_json()
            )
            self.assertNotIn(
                "provider-key-value", lease.capability_snapshot.model_dump_json()
            )
            self.assertFalse(lease.closed)
        finally:
            lease.close()
        self.assertTrue(lease.closed)
        lease.close()
        self.assertTrue(lease.closed)
        self.assertEqual(close_count, 1)

    def test_openai_responses_spec_keeps_the_frozen_wire_api(self) -> None:
        frozen = snapshot(
            "openai",
            WireAPI.OPENAI_RESPONSES,
            "https://api.openai.com/v1",
        )
        lease = ModelGateway(self.secrets, async_kernel=self.kernel).acquire_lease(frozen)
        try:
            self.assertEqual(lease.client.profile_snapshot.wire_api, "openai_responses")
        finally:
            lease.close()

    def test_unknown_provider_fails_before_provider_client_construction(self) -> None:
        frozen = snapshot(
            "unknown",
            WireAPI.OPENAI_CHAT_COMPLETIONS,
            "https://api.example.test/v1",
        )
        with self.assertRaisesRegex(ValueError, "unknown model provider") as raised:
            ModelGateway(self.secrets, async_kernel=self.kernel).acquire_lease(frozen)
        self.assertNotIn("provider-key-value", str(raised.exception))

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
            ModelGateway(self.secrets, async_kernel=self.kernel).acquire_lease(invalid)


if __name__ == "__main__":
    unittest.main()

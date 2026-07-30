from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import sys
import threading
import unittest

import httpx


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.model_gateway.capability import CapabilityProbe  # noqa: E402
from eidos_runtime.model_gateway.models import (  # noqa: E402
    ModelProfile,
    ReasoningMode,
    RetryPolicy,
    WireAPI,
)


NOW = datetime(2026, 7, 30, tzinfo=UTC)


def profile(
    provider: str,
    wire_api: WireAPI,
    base_url: str,
    *,
    supports_tools: bool | None = None,
    supports_structured_output: bool | None = None,
) -> ModelProfile:
    return ModelProfile(
        id=f"profile-{provider}",
        name=provider,
        provider=provider,
        base_url=base_url,
        auth_reference=f"env:{provider.upper()}_API_KEY",
        wire_api=wire_api,
        model_id="fixture-model",
        reasoning_mode=ReasoningMode.NONE,
        supports_tools=supports_tools,
        supports_structured_output=supports_structured_output,
        request_timeout=5.0,
        retry_policy=RetryPolicy(max_attempts=2),
        created_at=NOW,
        updated_at=NOW,
    )


class CapabilityProbeAdapterContractTests(unittest.TestCase):
    def test_supported_wire_families_use_native_endpoint_shape_and_auth(self) -> None:
        cases = (
            (
                profile("openai", WireAPI.OPENAI_RESPONSES, "https://api.openai.com/v1"),
                "/v1/responses",
                "authorization",
                "input",
            ),
            (
                profile("deepseek", WireAPI.OPENAI_CHAT_COMPLETIONS, "https://api.deepseek.com"),
                "/chat/completions",
                "authorization",
                "messages",
            ),
        )
        for value, path, auth_header, body_key in cases:
            with self.subTest(wire=value.wire_api):
                requests: list[httpx.Request] = []

                async def handler(request: httpx.Request) -> httpx.Response:
                    requests.append(request)
                    return httpx.Response(
                        200,
                        headers={"x-request-id": "request-1"},
                        json={"id": "response-1"},
                    )

                snapshot = CapabilityProbe(
                    transport=httpx.MockTransport(handler)
                ).probe(value, "provider-key-value-123456", threading.Event())

                self.assertEqual(len(requests), 1)
                self.assertEqual(requests[0].url.path, path)
                self.assertIn(auth_header, requests[0].headers)
                self.assertIn(body_key, json.loads(requests[0].content))
                self.assertTrue(snapshot.reachable)
                self.assertTrue(snapshot.authenticated)

    def test_claimed_tool_and_structured_output_require_bounded_active_probes(self) -> None:
        requests: list[dict[str, object]] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={"id": f"response-{len(requests)}"})

        snapshot = CapabilityProbe(
            transport=httpx.MockTransport(handler)
        ).probe(
            profile(
                "deepseek",
                WireAPI.OPENAI_CHAT_COMPLETIONS,
                "https://api.deepseek.com",
                supports_tools=True,
                supports_structured_output=True,
            ),
            "provider-key-value-123456",
            threading.Event(),
        )

        self.assertEqual(len(requests), 3)
        self.assertNotIn("tools", requests[0])
        self.assertIn("tools", requests[1])
        self.assertIn("response_format", requests[2])
        self.assertTrue(snapshot.supports_tools)
        self.assertTrue(snapshot.supports_structured_output)

    def test_probe_error_is_normalized_and_redacted(self) -> None:
        async def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                401,
                text='{"error":"Authorization: Bearer sk-super-secret-value"}',
            )

        result = CapabilityProbe(
            transport=httpx.MockTransport(handler)
        ).test_connection(
            profile(
                "openai",
                WireAPI.OPENAI_RESPONSES,
                "https://api.openai.com/v1",
            ),
            "provider-key-value-123456",
            threading.Event(),
        )

        self.assertFalse(result.success)
        assert result.error is not None
        self.assertEqual(result.error.code, "MODEL_AUTHENTICATION_FAILED")
        self.assertNotIn("sk-super-secret-value", result.model_dump_json())


if __name__ == "__main__":
    unittest.main()

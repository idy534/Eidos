from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

import httpx


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.model_gateway.capability import CapabilityProbe  # noqa: E402
from eidos_runtime.protocol.server import RuntimeServer  # noqa: E402
from eidos_runtime.sandbox.sensitive import SensitiveScanner  # noqa: E402


class ModelGatewayProtocolTests(unittest.TestCase):
    def test_profile_crud_probe_and_run_snapshot_use_closed_rpc_contracts(self) -> None:
        with (
            tempfile.TemporaryDirectory(prefix="eidos-gateway-rpc-") as data,
            tempfile.TemporaryDirectory(prefix="eidos-gateway-workspace-") as workspace,
        ):
            output = io.StringIO()
            server = RuntimeServer(output, Path(data))
            server.store.initialize()
            server.model_secrets.initialize()
            server.initialized = True
            server.sensitive = SensitiveScanner()
            server.supervisor.prepare_next = lambda: None  # type: ignore[method-assign]
            server._schedule_title_generation = lambda *_args: None  # type: ignore[method-assign]

            async def probe_handler(_request: httpx.Request) -> httpx.Response:
                return httpx.Response(200, json={"id": "probe-response"})

            server.capability_probe = CapabilityProbe(
                transport=httpx.MockTransport(probe_handler)
            )
            server.handle(request("model_profile/list_presets", {}, "presets"))
            server.handle(request(
                "model_profile/create",
                {
                    "apiKey": "provider-key-value-123456",
                    "profile": {
                        "name": "DeepSeek",
                        "provider": "deepseek",
                        "modelId": "deepseek-chat",
                        "contextWindow": 128000,
                        "maxOutputTokens": 4096,
                        "supportsTools": True,
                        "requestTimeout": 30.0,
                        "retryPolicy": {"maxAttempts": 3},
                    },
                },
                "create",
            ))
            created = messages(output)["create"]["result"]
            profile_id = created["id"]
            self.assertEqual(created["wireApi"], "openai_chat_completions")
            self.assertTrue(created["authReference"].startswith("local:"))

            server.handle(request(
                "model_profile/test_connection",
                {"profileId": profile_id},
                "probe",
            ))
            server.handle(request("model_profile/list", {}, "list"))
            session = server.store.create_session(workspace)
            server.handle(request(
                "run/start",
                {
                    "sessionId": session["id"],
                    "userInput": "inspect",
                    "profileId": profile_id,
                },
                "run",
            ))

            results = messages(output)
            self.assertTrue(results["probe"]["result"]["success"])
            self.assertEqual(
                results["list"]["result"]["profiles"][0]["id"],
                profile_id,
            )
            run = results["run"]["result"]
            frozen = server.store.read_run_model_snapshot(run["id"])
            self.assertEqual(frozen.profile.id, profile_id)
            self.assertEqual(frozen.profile.model_id, "deepseek-chat")
            self.assertEqual(frozen.capability.id, (
                results["probe"]["result"]["capabilitySnapshot"]["id"]
            ))
            self.assertNotIn(
                b"provider-key-value-123456",
                (Path(data) / "eidos.db").read_bytes(),
            )
            server.store.close()


def request(method: str, params: dict[str, object], suffix: str) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": f"client-{suffix}",
        "method": method,
        "params": params,
    }


def messages(output: io.StringIO) -> dict[str, dict[str, object]]:
    return {
        message["id"].removeprefix("client-"): message
        for line in output.getvalue().splitlines()
        if (message := json.loads(line)).get("id")
    }


if __name__ == "__main__":
    unittest.main()

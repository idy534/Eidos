from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.protocol.server import RuntimeServer  # noqa: E402
from eidos_runtime.model_gateway.capabilities import resolve_model_capabilities  # noqa: E402
from eidos_runtime.model_gateway.models import CapabilityProbeSource  # noqa: E402
from eidos_runtime.model_gateway.presets import PRESETS  # noqa: E402
from eidos_runtime.sandbox.sensitive import SensitiveScanner  # noqa: E402


class ModelGatewayProtocolTests(unittest.TestCase):
    def test_profile_crud_without_probe_is_selectable_and_freezes_declared_capability(self) -> None:
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

            server.handle(request("model_profile/list", {}, "list"))
            server.handle(request("model/list", {}, "models"))
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
            self.assertEqual(
                results["list"]["result"]["profiles"][0]["id"],
                profile_id,
            )
            option = next(
                value for value in results["models"]["result"]["models"]
                if value["id"] == profile_id
            )
            self.assertTrue(option["configured"])
            self.assertTrue(option["selectable"])
            run = results["run"]["result"]
            frozen = server.store.read_run_model_snapshot(run["id"])
            self.assertEqual(frozen.profile.id, profile_id)
            self.assertEqual(frozen.profile.model_id, "deepseek-chat")
            self.assertFalse(frozen.capability.reachable)
            self.assertFalse(frozen.capability.authenticated)
            self.assertNotIn(
                b"provider-key-value-123456",
                (Path(data) / "eidos.db").read_bytes(),
            )
            server.store.close()

    def test_removed_test_connection_rpc_returns_method_not_found(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-gateway-rpc-") as data:
            output = io.StringIO()
            server = RuntimeServer(output, Path(data))
            server.store.initialize()
            server.model_secrets.initialize()
            server.initialized = True
            server.handle(request(
                "model_profile/test_connection", {"profileId": "profile-1"}, "removed"
            ))

            self.assertEqual(messages(output)["removed"]["error"]["code"], -32601)
            server.store.close()

    def test_historical_active_probe_snapshot_does_not_control_new_run(self) -> None:
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
            server.handle(request(
                "model_profile/create",
                {"apiKey": "provider-key-value-123456", "profile": {
                    "name": "DeepSeek", "provider": "deepseek", "modelId": "deepseek-chat",
                    "contextWindow": 128000, "maxOutputTokens": 4096,
                }},
                "create",
            ))
            profile_id = messages(output)["create"]["result"]["id"]
            profile = server.store.get_model_profile(profile_id)
            assert profile is not None
            server.store.save_model_capability_snapshot(
                resolve_model_capabilities(profile, PRESETS[profile.provider]).model_copy(
                    update={
                        "id": "legacy-active-probe",
                        "probe_source": CapabilityProbeSource.ACTIVE_PROBE,
                        "probe_version": "r2-v1",
                    }
                )
            )

            server.handle(request("model/list", {}, "models"))
            session = server.store.create_session(workspace)
            server.handle(request("run/start", {
                "sessionId": session["id"], "userInput": "inspect", "profileId": profile_id,
            }, "run"))

            option = next(
                value for value in messages(output)["models"]["result"]["models"]
                if value["id"] == profile_id
            )
            self.assertTrue(option["selectable"])
            self.assertIn("result", messages(output)["run"])
            frozen = server.store.read_run_model_snapshot(messages(output)["run"]["result"]["id"])
            self.assertIsNot(frozen.capability.probe_source, CapabilityProbeSource.ACTIVE_PROBE)
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

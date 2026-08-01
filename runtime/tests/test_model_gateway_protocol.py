from __future__ import annotations

import io
import json
from pathlib import Path
import tempfile
import unittest

from eidos_runtime.protocol.server import RuntimeServer
from eidos_runtime.sandbox.sensitive import SensitiveScanner


class ModelGatewayProtocolTests(unittest.TestCase):
    def test_model_crud_and_turn_level_switch_use_the_closed_rpc_contract(self) -> None:
        with (
            tempfile.TemporaryDirectory(prefix="eidos-model-rpc-") as data,
            tempfile.TemporaryDirectory(prefix="eidos-model-workspace-") as workspace,
        ):
            output = io.StringIO()
            server = RuntimeServer(output, Path(data))
            server.store.initialize()
            server.model_config.initialize()
            server.initialized = True
            server.sensitive = SensitiveScanner()
            server.supervisor.prepare_next = lambda: None  # type: ignore[method-assign]
            server._schedule_title_generation = lambda *_args: None  # type: ignore[method-assign]

            server.handle(request("model/presets", {}, "presets"))
            server.handle(request(
                "model/create",
                {
                    "provider": "deepseek",
                    "modelId": "deepseek-v4-flash",
                    "apiKey": "sk-deepseek-secret-value",
                },
                "create-flash",
            ))
            server.handle(request(
                "model/create",
                {
                    "provider": "minimax",
                    "modelId": "MiniMax-M3",
                    "apiKey": "minimax-secret-value",
                },
                "create-minimax",
            ))
            server.handle(request(
                "model/create",
                {
                    "provider": "kimi",
                    "modelId": "kimi-k2.7-code-highspeed",
                    "apiKey": "kimi-secret-value",
                },
                "create-kimi",
            ))
            server.handle(request("model/list", {}, "list"))

            results = messages(output)
            self.assertEqual(
                [provider["id"] for provider in results["presets"]["result"]["providers"]],
                ["deepseek", "minimax", "kimi"],
            )
            self.assertEqual(
                [model["id"] for model in results["list"]["result"]["models"]],
                [
                    "deepseek-v4-flash",
                    "MiniMax-M3",
                    "kimi-k2.7-code-highspeed",
                ],
            )
            self.assertNotIn(
                "sk-deepseek-secret-value",
                json.dumps(results["list"], ensure_ascii=False),
            )

            session = server.store.create_session(workspace)
            server.handle(request(
                "run/start",
                {"sessionId": session["id"], "userInput": "missing model"},
                "run-missing-model",
            ))
            server.handle(request(
                "run/start",
                {
                    "sessionId": session["id"],
                    "userInput": "legacy selector",
                    "modelId": "deepseek-v4-flash",
                    "profileId": "legacy-profile",
                },
                "run-profile-id",
            ))
            invalid_runs = messages(output)
            self.assertEqual(invalid_runs["run-missing-model"]["error"]["code"], -32602)
            self.assertEqual(invalid_runs["run-profile-id"]["error"]["code"], -32602)
            server.handle(request(
                "run/start",
                {
                    "sessionId": session["id"],
                    "userInput": "first turn",
                    "modelId": "deepseek-v4-flash",
                },
                "run-one",
            ))
            first = messages(output)["run-one"]["result"]
            server.store.cancel_run(first["id"])

            server.handle(request(
                "run/start",
                {
                    "sessionId": session["id"],
                    "userInput": "second turn",
                    "modelId": "MiniMax-M3",
                },
                "run-two",
            ))
            second = messages(output)["run-two"]["result"]
            self.assertEqual(first["modelId"], "deepseek-v4-flash")
            self.assertEqual(second["modelId"], "MiniMax-M3")
            self.assertNotIn("profileId", first)
            self.assertNotIn("profileId", second)

            server.handle(request(
                "model/update",
                {
                    "id": "MiniMax-M3",
                    "provider": "kimi",
                    "modelId": "kimi-k3",
                    "apiKey": "",
                },
                "update",
            ))
            self.assertEqual(messages(output)["update"]["result"]["id"], "kimi-k3")
            self.assertEqual(
                server._frozen_model_configs[second["id"]].id,
                "MiniMax-M3",
            )
            self.assertEqual(
                server._frozen_model_configs[second["id"]].api_key,
                "minimax-secret-value",
            )

            server.handle(request(
                "model/delete", {"id": "kimi-k3"}, "delete"
            ))
            self.assertEqual(
                messages(output)["delete"]["result"], {"deletedModelId": "kimi-k3"}
            )
            server.store.close()

    def test_removed_profile_and_configure_methods_are_not_registered(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-model-rpc-") as data:
            output = io.StringIO()
            server = RuntimeServer(output, Path(data))
            server.store.initialize()
            server.model_config.initialize()
            server.initialized = True

            for index, method in enumerate((
                "model/status",
                "model/configure",
                "model_profile/list",
                "model_profile/create",
                "model_profile/update",
                "model_profile/delete",
                "model_profile/list_presets",
            )):
                server.handle(request(method, {}, f"removed-{index}"))

            results = messages(output)
            self.assertTrue(all(
                results[f"removed-{index}"]["error"]["code"] == -32601
                for index in range(7)
            ))
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

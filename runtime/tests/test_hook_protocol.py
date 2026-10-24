from __future__ import annotations

import io
import json

from eidos_runtime.model.client import ModelResponse, ScriptedModel
from eidos_runtime.protocol.server import RuntimeServer


def test_hook_sources_enter_run_snapshot_and_project_history_through_rpc(tmp_path):
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    configuration = {"hooks": {event: [{"hooks": [{"type": "command", "command": "printf '%s' '{}'"}]}]
        for event in ("SessionStart", "SessionEnd")}}
    configuration["hooks"]["SessionEnd"][0]["hooks"][0]["command"] = 'printf x >> "$PLUGIN_DATA/ended"'
    (data / "hooks.json").write_text(json.dumps(configuration))
    output = io.StringIO()
    server = RuntimeServer(output, data, ScriptedModel([ModelResponse(text="done")]))
    count = 0

    def rpc(method, params):
        nonlocal count
        count += 1
        request_id = f"client-{count}"
        server.handle({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        return next(json.loads(line) for line in output.getvalue().splitlines() if json.loads(line).get("id") == request_id)

    try:
        assert "result" in rpc("initialize", {"client": {"name": "test", "version": "1"}, "protocolVersion": 1})
        invalid = rpc("hook/configuration", {"action": "read", "trusted": True})
        assert invalid["error"]["code"] == -32602
        source = rpc("hook/configuration", {})["result"]["sources"][0]
        assert not source["trusted"]
        trusted = rpc("hook/configuration", {"action": "trust", "sourceId": source["id"], "contentHash": source["contentHash"], "trusted": True})
        assert trusted["result"]["sources"][0]["trusted"]
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        session = server.store.create_session(str(workspace))
        run = rpc("run/start", {"sessionId": session["id"], "userInput": "Check hooks", "modelId": "deepseek-v4-pro", "operationId": "00000000-0000-4000-8000-000000000039"})["result"]
        server.supervisor.wait(10)
        history = rpc("hook/history", {"runId": run["id"]})["result"]["items"]
        assert len(history) == 1 and history[0]["event"] == "SessionStart"
        assert history[0]["status"] == "completed"
        response = rpc("session/read", {"sessionId": session["id"]})
        assert "result" in response, response
        snapshot = response["result"]
        assert snapshot["runs"][0]["hookActivity"][0]["id"] == history[0]["id"]
        assert "command" not in snapshot["runs"][0]["hookActivity"][0]
        deletion = {"sessionId": session["id"], "operationId": "00000000-0000-4000-8000-000000000040"}
        assert "result" in rpc("session/delete", deletion)
        marker = data / "hook-data" / source["id"].replace(":", "-") / "ended"
        assert marker.read_text() == "x"
        assert "result" in rpc("session/delete", deletion)
        assert marker.read_text() == "x"
    finally:
        server.close()

from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eidos_runtime.model.client import ScriptedModel  # noqa: E402
from eidos_runtime.protocol.server import RuntimeServer  # noqa: E402


class PluginProtocolTests(unittest.TestCase):
    def test_import_list_enable_and_remove_use_safe_closed_results(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-plugin-rpc-") as directory:
            root = Path(directory)
            data = root / "data"
            source = root / "plugin"
            data.mkdir(mode=0o700)
            (source / "skills" / "review").mkdir(parents=True)
            (source / "skills" / "review" / "SKILL.md").write_text(
                "---\nname: review\ndescription: Review files.\n---\nInspect first.\n",
                encoding="utf-8",
            )
            (source / "server.py").write_text("# fixture\n", encoding="utf-8")
            (source / "plugin.json").write_text(json.dumps({
                "schemaVersion": 1,
                "id": "demo",
                "name": "Demo",
                "version": "1.0.0",
                "description": "Fixture",
                "skills": [{"root": "skills/review"}],
                "mcpServers": [{
                    "id": "fixture", "executable": "python3", "argv": ["server.py"],
                    "envNames": [], "permissionProfile": "workspace_read",
                    "startupTimeoutSeconds": 5, "toolTimeoutSeconds": 10,
                    "enabled": True,
                }],
            }), encoding="utf-8")
            output = io.StringIO()
            server = RuntimeServer(output, data, ScriptedModel([]))
            server.handle({
                "jsonrpc": "2.0", "id": "client-init", "method": "initialize",
                "params": {"client": {"name": "test", "version": "1"}, "protocolVersion": 1},
            })
            server.handle({
                "jsonrpc": "2.0", "id": "client-import", "method": "plugin/import",
                "params": {
                    "sourcePath": str(source),
                    "operationId": "00000000-0000-4000-8000-000000000001",
                },
            })
            self.assertTrue(server.supervisor.wait_managed_tasks(2))
            server.handle({
                "jsonrpc": "2.0", "id": "client-import-replay", "method": "plugin/import",
                "params": {
                    "sourcePath": str(source),
                    "operationId": "00000000-0000-4000-8000-000000000001",
                },
            })
            server.handle({
                "jsonrpc": "2.0", "id": "client-enable", "method": "plugin/setEnabled",
                "params": {"pluginId": "demo", "enabled": True},
            })
            server.handle({
                "jsonrpc": "2.0", "id": "client-list", "method": "plugin/list",
                "params": {},
            })
            server.handle({
                "jsonrpc": "2.0", "id": "client-skills", "method": "skill/list",
                "params": {},
            })
            server.handle({
                "jsonrpc": "2.0", "id": "client-mcp-list", "method": "mcp/list",
                "params": {},
            })
            server.handle({
                "jsonrpc": "2.0", "id": "client-mcp-enable", "method": "mcp/setEnabled",
                "params": {
                    "pluginId": "demo", "serverId": "fixture", "enabled": True,
                    "consent": True,
                    "operationId": "00000000-0000-4000-8000-000000000002",
                },
            })
            server.handle({
                "jsonrpc": "2.0", "id": "client-mcp-create", "method": "mcp/create",
                "params": {
                    "serverId": "manualfixture", "executable": "python3",
                    "argv": ["server.py"], "env": {"MCP_SECRET": "secret-value"},
                    "envNames": ["PATH"], "cwd": str(source),
                    "permissionProfile": "workspace_read",
                    "operationId": "00000000-0000-4000-8000-000000000003",
                },
            })
            server.handle({
                "jsonrpc": "2.0", "id": "client-mcp-update", "method": "mcp/update",
                "params": {
                    "serverId": "manualfixture", "executable": "python3",
                    "argv": ["server.py", "--updated"], "env": {"NEW_SECRET": "new-value"},
                    "envNames": ["PATH"], "cwd": str(source),
                    "permissionProfile": "connector", "startupTimeoutSeconds": 6,
                    "toolTimeoutSeconds": 12,
                    "operationId": "00000000-0000-4000-8000-000000000004",
                },
            })
            server.handle({
                "jsonrpc": "2.0", "id": "client-mcp-remove", "method": "mcp/remove",
                "params": {
                    "serverId": "manualfixture",
                    "operationId": "00000000-0000-4000-8000-000000000005",
                },
            })
            server.handle({
                "jsonrpc": "2.0", "id": "client-remove", "method": "plugin/remove",
                "params": {"pluginId": "demo"},
            })
            messages = [json.loads(line) for line in output.getvalue().splitlines()]

            imported = next(message["result"] for message in messages if message.get("id") == "client-import")
            listed = next(message["result"] for message in messages if message.get("id") == "client-list")
            removed = next(message["result"] for message in messages if message.get("id") == "client-remove")
            skills = next(message["result"] for message in messages if message.get("id") == "client-skills")
            mcp = next(message["result"] for message in messages if message.get("id") == "client-mcp-enable")
            manual_mcp = next(message["result"] for message in messages if message.get("id") == "client-mcp-create")
            updated_mcp = next(message["result"] for message in messages if message.get("id") == "client-mcp-update")
            removed_mcp = next(message["result"] for message in messages if message.get("id") == "client-mcp-remove")
            self.assertEqual(imported["id"], "demo")
            self.assertEqual(next(message["result"] for message in messages if message.get("id") == "client-import-replay"), imported)
            self.assertTrue(listed["plugins"][0]["enabled"])
            self.assertEqual(skills["skills"][0]["qualifiedId"], "demo:review")
            self.assertTrue(mcp["consented"])
            self.assertEqual(manual_mcp["pluginId"], "manual")
            self.assertFalse(manual_mcp["consented"])
            self.assertEqual(updated_mcp["argv"], ["server.py", "--updated"])
            self.assertEqual(updated_mcp["permissionProfile"], "connector")
            self.assertFalse(updated_mcp["consented"])
            self.assertEqual(removed_mcp, {"serverId": "manualfixture", "removed": True})
            self.assertNotIn("secret-value", json.dumps(messages))
            self.assertEqual(removed["status"], "removed")
            self.assertEqual(
                server.store.connection.execute(
                    "SELECT COUNT(*) FROM events WHERE event_type = 'plugin.imported'"
                ).fetchone()[0],
                1,
            )
            self.assertNotIn(str(data), json.dumps(messages))
            non_manual_messages = [
                message for message in messages
                if message.get("id") not in {"client-mcp-create", "client-mcp-update"}
            ]
            self.assertNotIn(str(source), json.dumps(non_manual_messages))
            server.close()

    def test_plugin_methods_reject_unknown_fields(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-plugin-rpc-") as directory:
            data = Path(directory) / "data"
            data.mkdir(mode=0o700)
            output = io.StringIO()
            server = RuntimeServer(output, data, ScriptedModel([]))
            server.handle({
                "jsonrpc": "2.0", "id": "client-init", "method": "initialize",
                "params": {"client": {"name": "test", "version": "1"}, "protocolVersion": 1},
            })
            server.handle({
                "jsonrpc": "2.0", "id": "client-list", "method": "plugin/list",
                "params": {"unexpected": True},
            })
            message = json.loads(output.getvalue().splitlines()[-1])
            self.assertEqual(message["error"]["code"], -32602)
            server.close()


if __name__ == "__main__":
    unittest.main()

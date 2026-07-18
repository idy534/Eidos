from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eidos_runtime.db.storage import SessionStore  # noqa: E402
from eidos_runtime.extensions.plugins import PluginCatalog  # noqa: E402
from eidos_runtime.extensions.skills import SkillCatalog  # noqa: E402
from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel  # noqa: E402
from eidos_runtime.runtime.loop import RuntimeEngine  # noqa: E402
from eidos_runtime.runtime.loop import ApprovalDecision  # noqa: E402


class PhaseThreeRuntimeTests(unittest.TestCase):
    def test_external_timeout_pauses_for_reconciliation_without_model_retry(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-p3-timeout-") as directory:
            root = Path(directory)
            data, workspace, source = root / "data", root / "workspace", root / "plugin"
            data.mkdir(mode=0o700)
            workspace.mkdir()
            source.mkdir()
            (source / "server.py").write_bytes(
                (Path(__file__).parent / "fixtures" / "mcp_fixture.py").read_bytes()
            )
            (source / "plugin.json").write_text(json.dumps({
                "schemaVersion": 1, "id": "demo", "name": "Demo",
                "version": "1.0.0", "description": "Fixture", "skills": [],
                "mcpServers": [{
                    "id": "fixture", "executable": sys.executable,
                    "argv": ["server.py"], "envNames": [],
                    "permissionProfile": "workspace_read",
                    "startupTimeoutSeconds": 5, "toolTimeoutSeconds": 1,
                    "enabled": True,
                }],
            }), encoding="utf-8")
            store = SessionStore(data)
            store.initialize()
            plugins = PluginCatalog(store)
            plugins.import_directory(source)
            plugins.set_enabled("demo", True)
            plugins.set_mcp_enabled("demo", "fixture", True)
            session = store.create_session(str(workspace))
            run, _ = store.create_run(
                session["id"], "Call slow",
                extension_snapshot=SkillCatalog(plugins).extension_snapshot(),
            )
            model = ScriptedModel([
                ModelResponse(tool_calls=(ModelToolCall(
                    "search", "tool_search", {"query": "slow"}
                ),)),
                ModelResponse(tool_calls=(ModelToolCall(
                    "slow", "mcp__fixture__slow", {}
                ),)),
                ModelResponse(text="must not retry"),
            ])
            RuntimeEngine(
                store, model, lambda _message: None,
                request_approval=lambda _params, _cancel: ApprovalDecision("approve"),
                mcp_sandbox=False,
            ).run(run["id"], threading.Event())

            paused = store.read_run(run["id"])
            self.assertEqual(paused["status"], "waiting_user_input")
            self.assertTrue(paused["sideEffectsMayExist"])
            self.assertEqual(len(model.contexts), 2)
            result = json.loads(store.connection.execute(
                "SELECT result_json FROM tool_calls WHERE tool_name = ?",
                ("mcp__fixture__slow",),
            ).fetchone()[0])
            self.assertEqual(result["code"], "mcp_tool_timeout")
            store.close()

    def test_tool_search_activates_mcp_for_next_step_and_external_call_is_approved(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-p3-vertical-") as directory:
            root = Path(directory)
            data = root / "data"
            workspace = root / "workspace"
            source = root / "plugin"
            data.mkdir(mode=0o700)
            workspace.mkdir()
            source.mkdir()
            fixture = Path(__file__).parent / "fixtures" / "mcp_fixture.py"
            (source / "server.py").write_bytes(fixture.read_bytes())
            (source / "plugin.json").write_text(json.dumps({
                "schemaVersion": 1,
                "id": "demo",
                "name": "Demo",
                "version": "1.0.0",
                "description": "Fixture",
                "skills": [],
                "mcpServers": [{
                    "id": "fixture",
                    "executable": sys.executable,
                    "argv": ["server.py"],
                    "envNames": [],
                    "permissionProfile": "workspace_read",
                    "startupTimeoutSeconds": 5,
                    "toolTimeoutSeconds": 2,
                    "enabled": True,
                }],
            }), encoding="utf-8")
            store = SessionStore(data)
            store.initialize()
            plugins = PluginCatalog(store)
            plugins.import_directory(source)
            plugins.set_enabled("demo", True)
            plugins.set_mcp_enabled("demo", "fixture", True)
            session = store.create_session(str(workspace))
            run, _ = store.create_run(
                session["id"], "Find and call echo",
                extension_snapshot=SkillCatalog(plugins).extension_snapshot(),
            )
            model = ScriptedModel([
                ModelResponse(tool_calls=(ModelToolCall(
                    "search", "tool_search", {"query": "echo"}
                ),)),
                ModelResponse(tool_calls=(ModelToolCall(
                    "echo", "mcp__fixture__echo", {"message": "hello"}
                ),)),
                ModelResponse(text="done"),
            ])
            approvals: list[dict[str, object]] = []

            def approve(params: dict[str, object], _cancel: threading.Event) -> ApprovalDecision:
                approvals.append(params)
                return ApprovalDecision("approve")

            RuntimeEngine(
                store, model, lambda _message: None,
                request_approval=approve, mcp_sandbox=False,
            ).run(run["id"], threading.Event())

            first_names = {
                value["function"]["name"] for value in model.tool_definitions_history[0]
            }
            second_names = {
                value["function"]["name"] for value in model.tool_definitions_history[1]
            }
            self.assertNotIn("mcp__fixture__echo", first_names)
            self.assertIn("mcp__fixture__echo", second_names)
            self.assertEqual(store.read_run(run["id"])["status"], "succeeded")
            self.assertEqual(store.activated_tools(run["id"]), ("mcp__fixture__echo",))
            self.assertEqual(approvals[0]["kind"], "external_tool")
            self.assertEqual(approvals[0]["permissionProfile"], "workspace_read")
            rows = store.connection.execute(
                "SELECT tool_name, provenance_json, result_json FROM tool_calls ORDER BY creation_seq"
            ).fetchall()
            self.assertEqual(rows[1]["tool_name"], "mcp__fixture__echo")
            self.assertEqual(json.loads(rows[1]["provenance_json"])["kind"], "mcp")
            self.assertEqual(json.loads(rows[1]["result_json"])["data"]["text"], "hello")
            store.close()

            restarted = SessionStore(data)
            restarted.initialize()
            self.assertEqual(restarted.read_run(run["id"])["status"], "succeeded")
            self.assertEqual(
                restarted.activated_tools(run["id"]), ("mcp__fixture__echo",)
            )
            persisted = restarted.connection.execute(
                "SELECT provenance_json, result_json FROM tool_calls WHERE tool_name = ?",
                ("mcp__fixture__echo",),
            ).fetchone()
            self.assertEqual(json.loads(persisted["provenance_json"])["serverId"], "fixture")
            self.assertEqual(json.loads(persisted["result_json"])["code"], "ok")
            restarted.close()

    def test_explicit_skill_is_loaded_and_skill_tool_uses_same_run_snapshot(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-p3-skill-runtime-") as directory:
            root = Path(directory)
            data = root / "data"
            workspace = root / "workspace"
            source = root / "plugin"
            data.mkdir(mode=0o700)
            workspace.mkdir()
            (source / "skills" / "review").mkdir(parents=True)
            (source / "skills" / "review" / "SKILL.md").write_text(
                "---\nname: review\ndescription: Review files.\n---\nUse the checklist.\n",
                encoding="utf-8",
            )
            (source / "plugin.json").write_text(json.dumps({
                "schemaVersion": 1,
                "id": "demo",
                "name": "Demo",
                "version": "1.0.0",
                "description": "Fixture",
                "skills": [{"root": "skills/review"}],
                "mcpServers": [],
            }), encoding="utf-8")
            store = SessionStore(data)
            store.initialize()
            plugins = PluginCatalog(store)
            plugins.import_directory(source)
            plugins.set_enabled("demo", True)
            session = store.create_session(str(workspace))
            run, _ = store.create_run(
                session["id"],
                "Use @demo:review",
                extension_snapshot=SkillCatalog(plugins).extension_snapshot(),
            )
            model = ScriptedModel([
                ModelResponse(tool_calls=(ModelToolCall(
                    "call-skill", "skill_read", {"qualifiedId": "demo:review"}
                ),)),
                ModelResponse(text="done"),
            ])

            RuntimeEngine(store, model, lambda _message: None).run(
                run["id"], threading.Event()
            )

            self.assertIn("Use the checklist", json.dumps(model.contexts[0]))
            names = {
                value["function"]["name"]
                for value in model.tool_definitions_history[0]
            }
            self.assertIn("skill_read", names)
            self.assertIn("skill_read_resource", names)
            result = json.loads(store.connection.execute(
                "SELECT result_json FROM tool_calls"
            ).fetchone()[0])
            self.assertEqual(result["data"]["pluginId"], "demo")
            self.assertIn("Use the checklist", result["data"]["content"])
            store.close()

    def test_each_model_step_persists_its_tools_and_tool_call_provenance(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-p3-runtime-") as directory:
            root = Path(directory)
            data = root / "data"
            workspace = root / "workspace"
            data.mkdir(mode=0o700)
            workspace.mkdir()
            (workspace / "a.txt").write_text("hello", encoding="utf-8")
            store = SessionStore(data)
            store.initialize()
            session = store.create_session(str(workspace))
            run, _ = store.create_run(session["id"], "read a.txt")
            model = ScriptedModel([
                ModelResponse(tool_calls=(ModelToolCall(
                    "call-1", "read_file", {"path": "a.txt"}
                ),)),
                ModelResponse(text="done"),
            ])

            RuntimeEngine(store, model, lambda _message: None).run(
                run["id"], threading.Event()
            )

            connection = store.connection
            assert connection is not None
            snapshots = [
                json.loads(row[0]) for row in connection.execute(
                    "SELECT tool_snapshot_json FROM steps ORDER BY creation_seq"
                )
            ]
            provenance = json.loads(connection.execute(
                "SELECT provenance_json FROM tool_calls"
            ).fetchone()[0])
            self.assertEqual(len(snapshots), 2)
            self.assertIn("read_file", snapshots[0]["availableNames"])
            self.assertEqual(snapshots[0]["toolSetHash"], snapshots[1]["toolSetHash"])
            self.assertEqual(provenance["kind"], "builtin")
            self.assertEqual(
                model.tool_definitions_history[0],
                model.tool_definitions_history[1],
            )
            store.close()


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eidos_runtime.db.storage import SessionStore  # noqa: E402
from eidos_runtime.extensions.plugins import PluginCatalog  # noqa: E402
from eidos_runtime.extensions.skills import SkillCatalog  # noqa: E402
from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel  # noqa: E402
from eidos_runtime.runtime.loop import RuntimeEngine  # noqa: E402
from eidos_runtime.runtime.loop import ApprovalDecision  # noqa: E402
from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel  # noqa: E402
from eidos_runtime.runtime.resource_registry import ResourceRegistry  # noqa: E402


class PhaseThreeRuntimeTests(unittest.TestCase):
    def test_skill_create_requires_approval_and_is_available_to_a_new_run(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-p3-skill-create-") as directory:
            root = Path(directory)
            data, workspace = root / "data", root / "workspace"
            data.mkdir(mode=0o700)
            workspace.mkdir()
            store = SessionStore(data)
            store.initialize()
            plugins = PluginCatalog(store)
            skills = SkillCatalog(plugins)
            session = store.create_session(str(workspace))
            run, _ = store.create_run(
                session["id"], "Create a release notes skill",
                extension_snapshot=skills.extension_snapshot(),
            )
            model = ScriptedModel([
                ModelResponse(tool_calls=(ModelToolCall(
                    "create-skill", "skill_create", {
                        "name": "release-notes",
                        "description": "Draft concise release notes.",
                        "instructions": "Summarize user-visible changes and migration steps.",
                    }
                ),)),
                ModelResponse(text="done"),
            ])
            approvals: list[dict[str, object]] = []

            RuntimeEngine(
                store, model, lambda _message: None,
                request_approval=lambda params, _cancel: (
                    approvals.append(params) or ApprovalDecision("approve")
                ),
            ).run(run["id"], threading.Event())

            skill_file = data / "skills" / "release-notes" / "SKILL.md"
            self.assertEqual(store.read_run(run["id"])["status"], "succeeded")
            self.assertEqual(approvals[0]["kind"], "file_change")
            self.assertIn(
                "~/.eidos/skills/release-notes/SKILL.md",
                str(approvals[0]["diff"]),
            )
            self.assertEqual(skill_file.stat().st_mode & 0o777, 0o600)
            self.assertIn(
                "Summarize user-visible changes",
                skill_file.read_text(encoding="utf-8"),
            )
            self.assertIn(
                "user:release-notes",
                {
                    entry["qualifiedId"]
                    for entry in skills.catalog(skills.extension_snapshot())
                },
            )
            store.close()

    def test_skill_create_can_include_text_resources(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-p3-skill-resources-") as directory:
            root = Path(directory)
            data, workspace = root / "data", root / "workspace"
            data.mkdir(mode=0o700)
            workspace.mkdir()
            store = SessionStore(data)
            store.initialize()
            skills = SkillCatalog(PluginCatalog(store))
            session = store.create_session(str(workspace))
            run, _ = store.create_run(
                session["id"], "Create a skill with a script",
                extension_snapshot=skills.extension_snapshot(),
            )
            model = ScriptedModel([
                ModelResponse(tool_calls=(ModelToolCall(
                    "create-skill", "skill_create", {
                        "name": "release-notes",
                        "description": "Draft concise release notes.",
                        "instructions": "Run the bundled helper when deterministic output is needed.",
                        "files": [{
                            "path": "scripts/render.py",
                            "content": "print('release notes')\n",
                        }],
                    }
                ),)),
                ModelResponse(text="done"),
            ])

            RuntimeEngine(
                store, model, lambda _message: None,
                request_approval=lambda _params, _cancel: ApprovalDecision("approve"),
            ).run(run["id"], threading.Event())

            script = data / "skills" / "release-notes" / "scripts" / "render.py"
            self.assertEqual(script.read_text(encoding="utf-8"), "print('release notes')\n")
            self.assertEqual(script.stat().st_mode & 0o777, 0o600)
            store.close()

    def test_skill_install_requires_network_then_state_approval_and_preserves_tree(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-p3-skill-install-") as directory:
            root = Path(directory)
            data, workspace = root / "data", root / "workspace"
            data.mkdir(mode=0o700)
            workspace.mkdir()
            store = SessionStore(data)
            store.initialize()
            skills = SkillCatalog(PluginCatalog(store))
            session = store.create_session(str(workspace))
            run, _ = store.create_run(
                session["id"], "Install grilling",
                extension_snapshot=skills.extension_snapshot(),
            )
            url = "https://github.com/mattpocock/skills/tree/main/skills/productivity/grilling"
            model = ScriptedModel([
                ModelResponse(tool_calls=(ModelToolCall(
                    "install-skill", "skill_install", {"url": url}
                ),)),
                ModelResponse(text="done"),
            ])
            approvals: list[dict[str, object]] = []
            downloaded = {
                "SKILL.md": b"---\nname: grilling\ndescription: Grill a plan.\n---\nRun a grilling session.\n",
                "scripts/check.py": b"print('ok')\n",
            }

            with patch(
                "eidos_runtime.extensions.skills._download_github_skill",
                return_value=("grilling", downloaded),
                create=True,
            ):
                RuntimeEngine(
                    store, model, lambda _message: None,
                    request_approval=lambda params, _cancel: (
                        approvals.append(params) or ApprovalDecision("approve")
                    ),
                ).run(run["id"], threading.Event())

            self.assertEqual(
                [approval["kind"] for approval in approvals],
                ["network_access", "file_change"],
            )
            self.assertEqual(
                store.connection.execute(
                    "SELECT COUNT(*) FROM approvals WHERE item_id = ("
                    "SELECT item_id FROM tool_calls WHERE tool_name = 'skill_install')"
                ).fetchone()[0],
                2,
            )
            self.assertEqual(approvals[0]["hosts"], ["codeload.github.com:443"])
            self.assertIn("scripts/check.py", str(approvals[1]["diff"]))
            self.assertEqual(
                (data / "skills" / "grilling" / "scripts" / "check.py").read_bytes(),
                downloaded["scripts/check.py"],
            )
            next_snapshot = skills.extension_snapshot()
            self.assertIn(
                "user:grilling",
                {entry["qualifiedId"] for entry in skills.catalog(next_snapshot)},
            )
            self.assertNotEqual(
                run["extensionSnapshot"]["skillCatalogHash"],
                next_snapshot["skillCatalogHash"],
            )
            store.close()

    def test_skill_install_rejection_stops_before_download_or_write(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-p3-skill-install-reject-") as directory:
            root = Path(directory)
            data, workspace = root / "data", root / "workspace"
            data.mkdir(mode=0o700)
            workspace.mkdir()
            store = SessionStore(data)
            store.initialize()
            skills = SkillCatalog(PluginCatalog(store))
            session = store.create_session(str(workspace))
            run, _ = store.create_run(
                session["id"], "Install grilling",
                extension_snapshot=skills.extension_snapshot(),
            )
            model = ScriptedModel([
                ModelResponse(tool_calls=(ModelToolCall(
                    "install-skill", "skill_install", {
                        "url": "https://github.com/mattpocock/skills/tree/main/skills/productivity/grilling"
                    }
                ),)),
                ModelResponse(text="done"),
            ])

            with patch(
                "eidos_runtime.extensions.skills._download_github_skill",
                create=True,
            ) as download:
                RuntimeEngine(
                    store, model, lambda _message: None,
                    request_approval=lambda _params, _cancel: ApprovalDecision("reject"),
                ).run(run["id"], threading.Event())

            download.assert_not_called()
            self.assertFalse((data / "skills" / "grilling").exists())
            store.close()

    def test_skill_install_state_rejection_keeps_downloaded_skill_out_of_store(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-p3-skill-write-reject-") as directory:
            root = Path(directory)
            data, workspace = root / "data", root / "workspace"
            data.mkdir(mode=0o700)
            workspace.mkdir()
            store = SessionStore(data)
            store.initialize()
            skills = SkillCatalog(PluginCatalog(store))
            session = store.create_session(str(workspace))
            run, _ = store.create_run(
                session["id"], "Install grilling",
                extension_snapshot=skills.extension_snapshot(),
            )
            model = ScriptedModel([
                ModelResponse(tool_calls=(ModelToolCall(
                    "install-skill", "skill_install", {
                        "url": "https://github.com/mattpocock/skills/tree/main/skills/productivity/grilling"
                    }
                ),)),
                ModelResponse(text="done"),
            ])
            decisions = iter(("approve", "reject"))

            with patch(
                "eidos_runtime.extensions.skills._download_github_skill",
                return_value=("grilling", {
                    "SKILL.md": b"---\nname: grilling\ndescription: Grill a plan.\n---\nBody.\n",
                }),
                create=True,
            ) as download:
                RuntimeEngine(
                    store, model, lambda _message: None,
                    request_approval=lambda _params, _cancel: ApprovalDecision(next(decisions)),
                ).run(run["id"], threading.Event())

            download.assert_called_once()
            self.assertFalse((data / "skills" / "grilling").exists())
            store.close()

    def test_skill_create_rejection_has_no_side_effect(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-p3-skill-reject-") as directory:
            root = Path(directory)
            data, workspace = root / "data", root / "workspace"
            data.mkdir(mode=0o700)
            workspace.mkdir()
            store = SessionStore(data)
            store.initialize()
            plugins = PluginCatalog(store)
            session = store.create_session(str(workspace))
            run, _ = store.create_run(
                session["id"], "Create a rejected skill",
                extension_snapshot=SkillCatalog(plugins).extension_snapshot(),
            )
            model = ScriptedModel([
                ModelResponse(tool_calls=(ModelToolCall(
                    "create-skill", "skill_create", {
                        "name": "rejected-skill",
                        "description": "Must not be written.",
                        "instructions": "Do nothing.",
                    }
                ),)),
                ModelResponse(text="done"),
            ])

            RuntimeEngine(
                store, model, lambda _message: None,
                request_approval=lambda _params, _cancel: ApprovalDecision("reject"),
            ).run(run["id"], threading.Event())

            self.assertEqual(store.read_run(run["id"])["status"], "succeeded")
            self.assertFalse((data / "skills" / "rejected-skill").exists())
            result = json.loads(store.connection.execute(
                "SELECT result_json FROM tool_calls WHERE tool_name = ?",
                ("skill_create",),
            ).fetchone()[0])
            self.assertEqual(result["code"], "user_rejected")
            self.assertFalse(result["sideEffectsMayExist"])
            store.close()

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
            resources = ResourceRegistry()
            kernel = RuntimeAsyncKernel(resource_registry=resources)
            kernel.start()
            try:
                RuntimeEngine(
                    store, model, lambda _message: None,
                    request_approval=(
                        lambda _params, _cancel: ApprovalDecision("approve")
                    ),
                    async_kernel=kernel,
                    mcp_sandbox=False,
                    resource_registry=resources,
                ).run(run["id"], threading.Event())
            finally:
                kernel.close()

            interrupted = store.read_run(run["id"])
            self.assertEqual(interrupted["status"], "interrupted")
            self.assertTrue(interrupted["sideEffectsMayExist"])
            self.assertEqual(len(model.contexts), 2)
            result = json.loads(store.connection.execute(
                "SELECT result_json FROM tool_calls WHERE tool_name = ?",
                ("mcp__fixture__slow",),
            ).fetchone()[0])
            # The MCP adapter and the outer ToolExecutionController share the
            # configured deadline. Whichever boundary observes the deadline
            # first owns the canonical timeout result, and both outcomes are
            # uncertain and must pause the run without retrying the tool.
            self.assertIn(result["code"], {"mcp_tool_timeout", "TOOL_TIMEOUT"})
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

            resources = ResourceRegistry()
            kernel = RuntimeAsyncKernel(resource_registry=resources)
            kernel.start()
            try:
                RuntimeEngine(
                    store, model, lambda _message: None,
                    request_approval=approve,
                    async_kernel=kernel,
                    mcp_sandbox=False,
                    resource_registry=resources,
                ).run(run["id"], threading.Event())
            finally:
                kernel.close()

            first_names = {
                value.name for value in model.tool_definitions_history[0]
            }
            second_names = {
                value.name for value in model.tool_definitions_history[1]
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
            self.assertNotIn("Use the checklist", model.instructions_history[0])
            names = {
                value.name
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

    def test_projectless_run_uses_enabled_plugin_skill_and_mcp(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-p3-projectless-resources-") as directory:
            root = Path(directory)
            data = root / "data"
            chat_root = data / ".data-projectless" / "chat-root"
            source = root / "plugin"
            data.mkdir(mode=0o700)
            chat_root.mkdir(parents=True)
            source.mkdir()
            (source / "skills" / "review").mkdir(parents=True)
            (source / "skills" / "review" / "SKILL.md").write_text(
                "---\nname: review\ndescription: Review files.\n---\n"
                "Use the projectless checklist.\n",
                encoding="utf-8",
            )
            (source / "server.py").write_bytes(
                (Path(__file__).parent / "fixtures" / "mcp_fixture.py").read_bytes()
            )
            (source / "plugin.json").write_text(json.dumps({
                "schemaVersion": 1,
                "id": "demo",
                "name": "Demo",
                "version": "1.0.0",
                "description": "Fixture",
                "skills": [{"root": "skills/review"}],
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
            session = store.typed_runtime_repository().create_session(
                str(chat_root), projectless=True
            ).value
            run, _ = store.create_run(
                session.id,
                "Use @demo:review and call echo",
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
            resources = ResourceRegistry()
            kernel = RuntimeAsyncKernel(resource_registry=resources)
            kernel.start()
            try:
                RuntimeEngine(
                    store, model, lambda _message: None,
                    request_approval=lambda _params, _cancel: (
                        ApprovalDecision("approve")
                    ),
                    async_kernel=kernel,
                    mcp_sandbox=False,
                    resource_registry=resources,
                ).run(run["id"], threading.Event())
            finally:
                kernel.close()

            self.assertEqual(store.read_run(run["id"])["status"], "succeeded")
            self.assertEqual(
                Path(store.workspace_for_run(run["id"]).path), chat_root.resolve()
            )
            self.assertIn(
                "Use the projectless checklist.",
                json.dumps(model.contexts[0]),
            )
            self.assertIn(
                "mcp__fixture__echo",
                {value.name for value in model.tool_definitions_history[1]},
            )
            self.assertIn(
                "mcp__fixture__echo", store.activated_tools(run["id"])
            )
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

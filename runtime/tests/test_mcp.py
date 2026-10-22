from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
import json
import os
from pathlib import Path
from types import SimpleNamespace
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import anyio
import pytest
from mcp import types as mcp_types


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eidos_runtime.db.storage import SessionStore  # noqa: E402
from eidos_runtime.extensions.mcp import (  # noqa: E402
    McpConnection,
    McpManager,
    McpShutdownTimeout,
    McpUnavailable,
    _tool_entry,
)
from eidos_runtime.extensions.plugins import PluginCatalog  # noqa: E402
from eidos_runtime.extensions.skills import SkillCatalog  # noqa: E402
from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel  # noqa: E402
from eidos_runtime.runtime.engine import RuntimeEngine  # noqa: E402
from eidos_runtime.runtime.approval import ApprovalDecision  # noqa: E402
from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel  # noqa: E402
from eidos_runtime.runtime.async_kernel import AsyncTaskState  # noqa: E402
from eidos_runtime.runtime.async_kernel import AsyncKernelClosedError  # noqa: E402
from eidos_runtime.runtime.resource_registry import (  # noqa: E402
    ResourceRegistry,
    RuntimeResourceKind,
)
from eidos_runtime.runtime.tool_dispatcher import ToolDispatcher  # noqa: E402
from eidos_runtime.tools.registry import ToolRegistry  # noqa: E402


@pytest.mark.parametrize('annotations,required', [
    (None, True),
    ({'readOnlyHint': False}, True),
    ({'destructiveHint': False}, True),
    ({'idempotentHint': True}, True),
    ({'readOnlyHint': True}, False),
    ({'readOnlyHint': True, 'openWorldHint': True}, False),
])
def test_mcp_approval_policy_uses_only_explicit_read_only_hint(annotations, required):
    connection = SimpleNamespace(config=SimpleNamespace(tool_timeout_seconds=30))
    tool = mcp_types.Tool.model_validate({
        'name': 'read_data', 'description': 'Read data',
        'inputSchema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
        'annotations': annotations,
    })
    server = {'serverId': 'fixture', 'pluginId': 'demo', 'pluginVersion': '1'}
    entry = _tool_entry(connection, tool, server)
    assert entry.spec.approval_required is required
    assert entry.spec.side_effect == 'external'
    assert entry.spec.batch_policy == 'single'
    changed = _tool_entry(connection, tool.model_copy(update={'annotations':
        mcp_types.ToolAnnotations(read_only_hint=required)}), server)
    assert entry.provenance.content_hash != changed.provenance.content_hash
    if not required:
        legacy = replace(entry, spec=entry.spec.model_copy(update={'approval_required': True}),
            runtime=None, execution_policy=None)
        dispatcher = ToolDispatcher(ToolRegistry((entry,)))
        call = ModelToolCall('pending', entry.spec.name, {})
        assert dispatcher.plan(call, legacy.contract_fingerprint).descriptor is None
        restored = dispatcher.plan_pending_approval(call, legacy.contract_fingerprint)
        assert restored.descriptor is not None and dispatcher.validate_execution(call, restored)
        changed_contract = replace(legacy, spec=legacy.spec.model_copy(update={'description': 'Changed description'}))
        assert dispatcher.plan_pending_approval(call, changed_contract.contract_fingerprint).descriptor is None
        changed_source = replace(legacy, provenance=legacy.provenance.model_copy(update={'content_hash': 'd' * 64}))
        assert dispatcher.plan_pending_approval(call, changed_source.contract_fingerprint).descriptor is None
        unrelated = replace(entry, provenance=entry.provenance.model_copy(update={
            'kind': 'builtin', 'source_id': 'test.other_read_only',
        }))
        old_unrelated = replace(unrelated, execution_policy=unrelated.execution_policy.model_copy(
            update={'approval_required': True},
        ))
        assert ToolDispatcher(ToolRegistry((unrelated,))).plan_pending_approval(
            call, old_unrelated.contract_fingerprint,
        ).descriptor is None


class McpManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-mcp-")
        root = Path(self.temporary.name)
        self.data = root / "data"
        self.workspace = root / "workspace"
        source = root / "plugin"
        self.manual_root = root / "manual-mcp"
        self.data.mkdir(mode=0o700)
        self.workspace.mkdir()
        source.mkdir()
        self.manual_root.mkdir()
        fixture = Path(__file__).parent / "fixtures" / "mcp_fixture.py"
        (source / "server.py").write_bytes(fixture.read_bytes())
        (self.manual_root / "server.py").write_bytes(fixture.read_bytes())
        (source / "plugin.json").write_text(json.dumps({
            "schemaVersion": 1,
            "id": "demo",
            "name": "Demo",
            "version": "1.0.0",
            "description": "MCP fixture",
            "skills": [],
            "mcpServers": [{
                "id": "fixture",
                "executable": sys.executable,
                "argv": ["server.py"],
                "envNames": [],
                "permissionProfile": "workspace_read",
                "startupTimeoutSeconds": 5,
                "toolTimeoutSeconds": 1,
                "enabled": True,
            }],
        }), encoding="utf-8")
        self.store = SessionStore(self.data)
        self.store.initialize()
        self.plugins = PluginCatalog(self.store)
        self.plugins.import_directory(source)
        self.plugins.set_enabled("demo", True)
        self.plugins.set_mcp_enabled("demo", "fixture", True)
        self.resources = ResourceRegistry()
        self.kernel = RuntimeAsyncKernel(resource_registry=self.resources)
        self.kernel.start()
        self.manager = McpManager(
            self.plugins,
            self.plugins.extension_snapshot(),
            self.workspace,
            sandbox=False,
            async_kernel=self.kernel,
            resource_registry=self.resources,
        )

    def tearDown(self) -> None:
        self.manager.close()
        self.kernel.close()
        self.assertEqual(self.resources.active_resources(), ())
        self.store.close()
        self.temporary.cleanup()

    def test_read_only_mcp_skips_approval_and_review_but_commits_intent(self) -> None:
        for mode in ('manual', 'auto_review', 'full_access'):
            with self.subTest(mode=mode):
                session = self.store.create_session(str(self.workspace))
                run, _ = self.store.create_run(
                    session['id'], 'Read structured data', approval_mode=mode,
                    extension_snapshot=SkillCatalog(self.plugins).extension_snapshot(),
                )
                model = ScriptedModel([
                    ModelResponse(tool_calls=(ModelToolCall('find', 'tool_search', {
                        'query': 'mcp__fixture__structured', 'limit': 1,
                    }),)),
                    ModelResponse(tool_calls=(ModelToolCall('read', 'mcp__fixture__structured', {}),)),
                    ModelResponse(text='Done.'),
                ])
                original = McpConnection.call

                def call_after_intent(connection, name, arguments, cancel):
                    self.assertEqual(name, 'structured')
                    row = self.store.connection.execute(
                        'SELECT status FROM durable_intents WHERE run_id=?', (run['id'],),
                    ).fetchone()
                    self.assertEqual(row[0], 'running')
                    return original(connection, name, arguments, cancel)

                with patch.object(McpConnection, 'call', call_after_intent), patch(
                    'eidos_runtime.runtime.approval.review_approval',
                    side_effect=AssertionError('Read-only tools must not call a reviewer'),
                ) as review:
                    RuntimeEngine(
                        self.store, model, lambda _message: None,
                        request_approval=lambda *_args: self.fail('Read-only MCP must not request approval'),
                        async_kernel=self.kernel, resource_registry=self.resources, mcp_sandbox=False,
                    ).run(run['id'], threading.Event())
                    review.assert_not_called()
                self.assertEqual(self.store.read_run(run['id'])['status'], 'succeeded')
                self.assertEqual(self.store.connection.execute(
                    'SELECT count(*) FROM approvals WHERE run_id=?', (run['id'],),
                ).fetchone()[0], 0)
                intent = self.store.connection.execute(
                    'SELECT preconditions_json,status FROM durable_intents WHERE run_id=?', (run['id'],),
                ).fetchone()
                self.assertEqual(intent['status'], 'completed')
                if mode != 'full_access':
                    self.assertEqual(json.loads(intent['preconditions_json'])['authorization'], 'mcp_read_only')

    def test_pending_read_only_mcp_approval_recovers_without_skipping_user_decision(self) -> None:
        class ProcessCrash(BaseException):
            pass

        for decision in ('approve', 'reject'):
            with self.subTest(decision=decision):
                session = self.store.create_session(str(self.workspace))
                run, _ = self.store.create_run(
                    session['id'], 'Read data',
                    extension_snapshot=SkillCatalog(PluginCatalog(self.store)).extension_snapshot(),
                )
                model = ScriptedModel([
                    ModelResponse(tool_calls=(ModelToolCall('find', 'tool_search', {'query': 'mcp__fixture__structured', 'limit': 1}),)),
                    ModelResponse(tool_calls=(ModelToolCall('read', 'mcp__fixture__structured', {}),)),
                ])

                def legacy_entry(connection, tool, server):
                    entry = _tool_entry(connection, tool, server)
                    return replace(entry, spec=entry.spec.model_copy(update={'approval_required': True}),
                        runtime=None, execution_policy=None)

                def crash(*_args):
                    raise ProcessCrash()

                with patch('eidos_runtime.extensions.mcp._tool_entry', legacy_entry):
                    with self.assertRaises(ProcessCrash):
                        RuntimeEngine(
                            self.store, model, lambda _message: None, request_approval=crash,
                            async_kernel=self.kernel, resource_registry=self.resources, mcp_sandbox=False,
                        ).run(run['id'], threading.Event())
                pending = self.store.typed_runtime_repository().read_pending_approval(run['id'])
                self.store.close()
                self.store = SessionStore(self.data)
                self.store.initialize()
                requests = []
                RuntimeEngine(
                    self.store, ScriptedModel([ModelResponse(text='Done.')]), lambda _message: None,
                    request_approval=lambda request, _cancel: requests.append(request) or ApprovalDecision(decision),
                    async_kernel=self.kernel, resource_registry=self.resources, mcp_sandbox=False,
                ).run(run['id'], threading.Event())
                self.assertEqual(len(requests), 1)
                self.assertEqual(self.store.read_run(run['id'])['status'], 'succeeded')
                rows = self.store.connection.execute('SELECT id,decision FROM approvals WHERE run_id=?', (run['id'],)).fetchall()
                self.assertEqual([(row['id'], row['decision']) for row in rows], [(pending.id, decision)])
                self.assertEqual(self.store.connection.execute(
                    'SELECT count(*) FROM durable_intents WHERE run_id=?', (run['id'],),
                ).fetchone()[0], int(decision == 'approve'))

    def test_cancel_wins_over_a_late_read_only_mcp_result(self) -> None:
        session = self.store.create_session(str(self.workspace))
        run, _ = self.store.create_run(
            session['id'], 'Read data', extension_snapshot=SkillCatalog(self.plugins).extension_snapshot(),
        )
        cancel = threading.Event()
        model = ScriptedModel([
            ModelResponse(tool_calls=(ModelToolCall('find', 'tool_search', {'query': 'mcp__fixture__structured', 'limit': 1}),)),
            ModelResponse(tool_calls=(ModelToolCall('read', 'mcp__fixture__structured', {}),)),
        ])
        original = McpConnection.call

        def late_result(connection, name, arguments, controlled_cancel):
            result = original(connection, name, arguments, controlled_cancel)
            cancel.set()
            return result

        with patch.object(McpConnection, 'call', late_result):
            RuntimeEngine(
                self.store, model, lambda _message: None,
                async_kernel=self.kernel, resource_registry=self.resources, mcp_sandbox=False,
            ).run(run['id'], cancel)
        self.assertEqual(self.store.read_run(run['id'])['status'], 'canceled')
        result = self.store.connection.execute(
            "SELECT result_json FROM tool_calls WHERE tool_name='mcp__fixture__structured'",
        ).fetchone()[0]
        self.assertNotEqual(json.loads(result)['outcome'], 'success')
        self.assertEqual(self.store.connection.execute('SELECT count(*) FROM approvals').fetchone()[0], 0)

    def test_inactive_mcp_tool_is_reported_then_model_searches_and_recovers(self) -> None:
        session = self.store.create_session(str(self.workspace))
        run, _ = self.store.create_run(
            session["id"], "Use the echo tool", extension_snapshot=SkillCatalog(self.plugins).extension_snapshot(),
        )
        model = ScriptedModel([
            ModelResponse(tool_calls=(ModelToolCall("inactive", "mcp__fixture__echo", {"message": "before"}),)),
            ModelResponse(tool_calls=(ModelToolCall("search", "tool_search", {"query": "mcp__fixture__echo", "limit": 1}),)),
            ModelResponse(tool_calls=(ModelToolCall("active", "mcp__fixture__echo", {"message": "after"}),)),
            ModelResponse(text="Done."),
        ])
        approvals = []
        sdk_calls = []
        original_call = McpConnection.call

        def record_call(connection, name, arguments, cancel):
            sdk_calls.append((name, arguments))
            return original_call(connection, name, arguments, cancel)

        with patch.object(McpConnection, "call", record_call):
            RuntimeEngine(
                self.store, model, lambda _message: None,
                request_approval=lambda request, _cancel: approvals.append(request) or ApprovalDecision("approve"),
                async_kernel=self.kernel, resource_registry=self.resources, mcp_sandbox=False,
            ).run(run["id"], threading.Event())

        self.assertEqual(self.store.read_run(run["id"])["status"], "succeeded")
        self.assertEqual(sdk_calls, [("echo", {"message": "after"})])
        self.assertEqual(len(approvals), 1)
        self.assertNotIn("mcp__fixture__echo", [definition.name for definition in model.tool_definitions_history[0]])
        self.assertIn("mcp__fixture__echo", [definition.name for definition in model.tool_definitions_history[2]])
        self.assertIn("tool_not_activated", json.dumps(model.contexts[1]))
        feedback = next(item for item in model.contexts[1] if item.get("type") == "tool_result")
        self.assertIn('query="mcp__fixture__echo"', json.loads(feedback["result"])["summary"])
        self.assertFalse(any(item.get("type") == "protocol_error" for context in model.contexts for item in context))
        self.assertTrue(all(attempt["status"] == "completed" for attempt in self.store.read_model_attempts(run["id"])))
        saved = self.store.connection.execute(
            "SELECT id,result_json FROM tool_calls WHERE provider_call_id = 'inactive'",
        ).fetchone()
        result = json.loads(saved["result_json"])
        self.assertEqual(result["code"], "tool_not_activated")
        self.assertFalse(result["sideEffectsMayExist"])
        self.assertFalse(result["reconciliationRequired"])
        self.assertEqual(self.store.connection.execute(
            "SELECT COUNT(*) FROM durable_intents WHERE tool_call_id = ?", (saved["id"],),
        ).fetchone()[0], 0)
        self.assertEqual(self.store.connection.execute(
            "SELECT consecutive_protocol_errors FROM runs WHERE id = ?", (run["id"],),
        ).fetchone()[0], 0)

        self.store.close()
        self.store = SessionStore(self.data)
        self.store.initialize()
        followup, _ = self.store.create_run(session["id"], "Review the previous tool feedback")
        replay = ScriptedModel([ModelResponse(text="The earlier inactive call did not execute.")])
        RuntimeEngine(
            self.store, replay, lambda _message: None,
            async_kernel=self.kernel, resource_registry=self.resources, mcp_sandbox=False,
        ).run(followup["id"], threading.Event())
        self.assertEqual(self.store.read_run(followup["id"])["status"], "succeeded")
        self.assertIn("tool_not_activated", json.dumps(replay.contexts[0]))

    def test_search_and_inactive_tool_in_same_batch_require_a_subsequent_step(self) -> None:
        session = self.store.create_session(str(self.workspace))
        run, _ = self.store.create_run(
            session["id"], "Search then echo", extension_snapshot=SkillCatalog(self.plugins).extension_snapshot(),
        )
        model = ScriptedModel([
            ModelResponse(tool_calls=(
                ModelToolCall("search", "tool_search", {"query": "mcp__fixture__echo", "limit": 1}),
                ModelToolCall("too-early", "mcp__fixture__echo", {"message": "early"}),
            )),
            ModelResponse(tool_calls=(ModelToolCall("active", "mcp__fixture__echo", {"message": "later"}),)),
            ModelResponse(text="Done."),
        ])
        RuntimeEngine(
            self.store, model, lambda _message: None,
            request_approval=lambda _request, _cancel: ApprovalDecision("approve"),
            async_kernel=self.kernel, resource_registry=self.resources, mcp_sandbox=False,
        ).run(run["id"], threading.Event())
        self.assertEqual(self.store.read_run(run["id"])["status"], "succeeded")
        results = self.store.connection.execute(
            "SELECT provider_call_id,result_json FROM tool_calls ORDER BY creation_seq",
        ).fetchall()
        self.assertEqual([(row[0], json.loads(row[1])["code"]) for row in results], [
            ("search", "ok"), ("too-early", "tool_not_activated"), ("active", "ok"),
        ])

    def test_disabled_mcp_tool_is_not_misreported_as_needing_activation(self) -> None:
        self.plugins.set_mcp_enabled("demo", "fixture", False)
        session = self.store.create_session(str(self.workspace))
        run, _ = self.store.create_run(
            session["id"], "Call a disabled tool", extension_snapshot=SkillCatalog(self.plugins).extension_snapshot(),
        )
        model = ScriptedModel([
            ModelResponse(tool_calls=(ModelToolCall("disabled-1", "mcp__fixture__echo", {"message": "x"}),)),
            ModelResponse(tool_calls=(ModelToolCall("disabled-2", "mcp__fixture__echo", {"message": "x"}),)),
        ])
        RuntimeEngine(
            self.store, model, lambda _message: None,
            async_kernel=self.kernel, resource_registry=self.resources, mcp_sandbox=False,
        ).run(run["id"], threading.Event())
        self.assertEqual(self.store.read_run(run["id"])["errorCode"], "MODEL_PROTOCOL_ERROR")
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM tool_calls").fetchone()[0], 0)

    def test_multiple_inactive_calls_do_not_exhaust_protocol_repair(self) -> None:
        session = self.store.create_session(str(self.workspace))
        run, _ = self.store.create_run(
            session["id"], "Inspect two deferred tools", extension_snapshot=SkillCatalog(self.plugins).extension_snapshot(),
        )
        model = ScriptedModel([
            ModelResponse(tool_calls=(ModelToolCall("inactive-echo", "mcp__fixture__echo", {"message": "x"}),)),
            ModelResponse(tool_calls=(ModelToolCall("inactive-fail", "mcp__fixture__fail", {}),)),
            ModelResponse(text="Both tools need activation first."),
        ])
        with patch.object(McpConnection, "call", side_effect=AssertionError("Inactive tools must not execute")) as call:
            RuntimeEngine(
                self.store, model, lambda _message: None,
                async_kernel=self.kernel, resource_registry=self.resources, mcp_sandbox=False,
            ).run(run["id"], threading.Event())
            call.assert_not_called()
        self.assertEqual(self.store.read_run(run["id"])["status"], "succeeded")
        codes = [json.loads(row[0])["code"] for row in self.store.connection.execute("SELECT result_json FROM tool_calls")]
        self.assertEqual(codes, ["tool_not_activated", "tool_not_activated"])
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM approvals").fetchone()[0], 0)
        self.assertEqual(self.store.connection.execute("SELECT COUNT(*) FROM durable_intents").fetchone()[0], 0)

    def test_connection_is_a_service_on_the_shared_kernel(self) -> None:
        self.manager.start()

        connection = self.manager.connections[0]
        active = self.resources.active_resources()

        self.assertIs(connection.async_kernel, self.kernel)
        self.assertFalse(hasattr(connection, "thread"))
        self.assertFalse(hasattr(connection, "commands"))
        self.assertFalse(any(
            thread.name.startswith("eidos-mcp-")
            for thread in threading.enumerate()
        ))
        self.assertEqual(
            [resource.kind for resource in active].count(
                RuntimeResourceKind.MCP_CONNECTION
            ),
            1,
        )
        async_tasks = [
            resource
            for resource in active
            if resource.kind is RuntimeResourceKind.ASYNC_TASK
        ]
        self.assertEqual(len(async_tasks), 1)
        self.assertEqual(async_tasks[0].owner_id, "mcp:fixture")
        self.assertIsNone(connection._service.deadline)
        self.assertIsNone(async_tasks[0].deadline)

    def test_connection_uses_short_private_paths_and_cleans_only_its_tree(self) -> None:
        with patch.dict(os.environ, {"TMPDIR": str(self.data / ("long-" * 30))}):
            entries = self.manager.start()
        environment = next(value for value in entries if value.spec.name.endswith("__environment"))
        result = environment.adapter.execute({"bindSocket": True}, threading.Event())
        self.assertEqual(result["outcome"], "success")
        paths = json.loads(result["data"]["text"])
        connection = self.manager.connections[0]
        root = connection.runtime_root
        self.assertEqual(root.parent, Path("/private/tmp" if sys.platform == "darwin" else "/tmp").resolve())
        self.assertLessEqual(len(os.fsencode(root)), 48)
        self.assertEqual(root.stat().st_mode & 0o777, 0o700)
        self.assertEqual(paths["HOME"], str(root / "home"))
        self.assertEqual(paths["TMPDIR"], str(root / "tmp"))
        self.assertEqual(paths["cwd"], str(self.plugins.installed_root("demo")))
        self.assertLessEqual(len(os.fsencode(paths["socketPath"])), 103)
        self.assertTrue((root / "server.pid").is_file())
        self.manager.close()
        self.assertFalse(root.exists())
        self.assertTrue(self.workspace.is_dir())

    def test_short_directory_allocation_failure_does_not_launch_server(self) -> None:
        with patch("eidos_runtime.extensions.mcp.tempfile.mkdtemp", side_effect=OSError):
            self.assertEqual(self.manager.start(), ())
        server = next(value for value in self.plugins.list_mcp_servers() if value["serverId"] == "fixture")
        self.assertEqual(server["errorCode"], "mcp_temporary_directory_unavailable")
        self._assert_startup_resources_released()

    def test_changed_runtime_directory_is_not_followed_or_deleted(self) -> None:
        connection = self._connection(self.plugins.manifest("demo").mcp_servers[0])
        root = connection.runtime_root
        saved = root.with_name(root.name + "-saved")
        root.rename(saved)
        root.symlink_to(self.workspace, target_is_directory=True)
        try:
            with self.assertRaisesRegex(McpUnavailable, "mcp_temporary_directory_unavailable"):
                connection._prepare_parameters()
            with self.assertRaises(McpShutdownTimeout):
                connection.close()
            self.assertTrue(root.is_symlink())
            self.assertTrue(self.workspace.is_dir())
        finally:
            root.unlink()
            saved.rename(root)
            connection.close()

    def test_explicit_home_and_tmpdir_keep_priority(self) -> None:
        connection = self._connection(self.plugins.manifest("demo").mcp_servers[0])
        connection.env_values = {"HOME": str(self.manual_root), "TMPDIR": str(self.workspace)}
        try:
            parameters = connection._prepare_parameters()
            self.assertEqual(parameters.env["HOME"], str(self.manual_root))
            self.assertEqual(parameters.env["TMPDIR"], str(self.workspace))
        finally:
            connection.close()

    def test_blank_manual_cwd_controls_relative_output_without_changing_home(self) -> None:
        home = self.manual_root.resolve()
        with patch("eidos_runtime.extensions.plugins.Path.home", return_value=home):
            created = self.plugins.create_manual_mcp(
                server_id="manual_fixture", executable=sys.executable,
                argv=[str(self.manual_root.resolve() / "server.py")],
                env={}, env_names=[], cwd=None, permission_profile="connector",
                startup_timeout_seconds=5, tool_timeout_seconds=1,
            )
        expected = home / "Eidos" / "eidos-mcp" / "manual_fixture"
        self.assertEqual(created["cwd"], str(expected))
        self.plugins.set_mcp_enabled("manual", "manual_fixture", True)
        manager = McpManager(
            self.plugins, self.plugins.extension_snapshot(), self.workspace,
            sandbox=False, async_kernel=self.kernel, resource_registry=self.resources,
        )
        try:
            entries = manager.start()
            environment = next(value for value in entries if value.spec.name == "mcp__manual_fixture__environment")
            result = environment.adapter.execute({"writeOutput": True}, threading.Event())
            self.assertEqual(result["outcome"], "success")
            paths = json.loads(result["data"]["text"])
            self.assertEqual(paths["cwd"], str(expected))
            self.assertNotEqual(paths["HOME"], paths["cwd"])
            self.assertEqual((expected / "relative-output.txt").read_text(), "fixture-output")
        finally:
            manager.close()
        self.assertEqual((expected / "relative-output.txt").read_text(), "fixture-output")

    def test_two_connections_share_the_runtime_kernel(self) -> None:
        second = McpManager(
            self.plugins,
            self.plugins.extension_snapshot(),
            self.workspace,
            sandbox=False,
            async_kernel=self.kernel,
            resource_registry=self.resources,
        )
        try:
            self.manager.start()
            second.start()

            self.assertIs(
                self.manager.connections[0].async_kernel,
                second.connections[0].async_kernel,
            )
            self.assertEqual(
                sum(
                    resource.kind is RuntimeResourceKind.ASYNC_TASK
                    for resource in self.resources.active_resources()
                ),
                2,
            )
        finally:
            second.close()

    def test_startup_timeout_is_closed_and_releases_async_task(self) -> None:
        config = self.plugins.manifest("demo").mcp_servers[0].model_copy(
            update={
                "argv": ["server.py", "--startup-delay"],
                "startup_timeout_seconds": 1,
            }
        )
        connection = McpConnection(
            plugin_root=self.plugins.installed_root("demo"),
            workspace_root=self.workspace,
            config=config,
            on_list_changed=lambda: None,
            async_kernel=self.kernel,
            sandbox=False,
            resource_registry=self.resources,
        )

        with self.assertRaisesRegex(McpUnavailable, "mcp_startup_timeout"):
            connection.start()

        self.assertFalse(any(
            resource.kind is RuntimeResourceKind.ASYNC_TASK
            for resource in self.resources.active_resources()
        ))
        self.assertFalse(any(
            resource.kind is RuntimeResourceKind.MCP_CONNECTION
            for resource in self.resources.active_resources()
        ))

    def test_startup_timeout_bounds_transport_context_entry(self) -> None:
        config = self.plugins.manifest("demo").mcp_servers[0].model_copy(
            update={"startup_timeout_seconds": 0.05}
        )
        connection = self._connection(config)

        @asynccontextmanager
        async def stalled_transport(_parameters, *, errlog):
            del errlog
            await anyio.sleep(1)
            yield (object(), object())

        started = time.monotonic()
        with patch("eidos_runtime.extensions.mcp.stdio_client", stalled_transport):
            with self.assertRaisesRegex(McpUnavailable, "mcp_startup_timeout"):
                connection.start()

        self.assertLess(time.monotonic() - started, 0.5)
        self._assert_startup_resources_released()

    def test_startup_timeout_bounds_client_session_context_entry(self) -> None:
        config = self.plugins.manifest("demo").mcp_servers[0].model_copy(
            update={"startup_timeout_seconds": 0.05}
        )
        connection = self._connection(config)

        @asynccontextmanager
        async def transport(_parameters, *, errlog):
            del errlog
            yield (object(), object())

        class StalledSession:
            def __init__(self, *_args, **_kwargs) -> None:
                pass

            async def __aenter__(self):
                await anyio.sleep(1)

            async def __aexit__(self, *_args) -> None:
                return None

        started = time.monotonic()
        with (
            patch("eidos_runtime.extensions.mcp.stdio_client", transport),
            patch("eidos_runtime.extensions.mcp.ClientSession", StalledSession),
        ):
            with self.assertRaisesRegex(McpUnavailable, "mcp_startup_timeout"):
                connection.start()

        self.assertLess(time.monotonic() - started, 0.5)
        self._assert_startup_resources_released()

    def test_forced_service_cancellation_stays_canceled(self) -> None:
        self.manager.start()
        connection = self.manager.connections[0]
        service = connection._service
        assert service is not None

        self.assertTrue(service.cancel())
        self.assertTrue(service.wait(1))

        self.assertEqual(service.state, AsyncTaskState.CANCELED)
        self.assertNotEqual(connection.error_code, "mcp_protocol_error")

    def test_list_changed_callback_failure_keeps_session_available(self) -> None:
        connection = self._connection(
            self.plugins.manifest("demo").mcp_servers[0],
            on_list_changed=lambda: (_ for _ in ()).throw(RuntimeError("storage")),
        )
        try:
            connection.start()
            result = connection.call("echo", {"message": "still-live"}, threading.Event())
        finally:
            connection.close()

        self.assertEqual(result["outcome"], "success")
        self.assertNotEqual(connection.error_code, "mcp_protocol_error")

    def test_blocking_list_changed_callback_does_not_block_kernel(self) -> None:
        callback_entered = threading.Event()
        release_callback = threading.Event()
        config = self.plugins.manifest("demo").mcp_servers[0]
        connection = self._connection(
            config,
            on_list_changed=lambda: (
                callback_entered.set(), release_callback.wait(2)
            ),
        )
        try:
            connection.start()
            self.assertTrue(callback_entered.wait(1))

            completed = threading.Event()

            async def unrelated() -> None:
                completed.set()

            task = self.kernel.start_task(unrelated, owner_id="unrelated")
            self.assertTrue(completed.wait(0.2))
            self.assertTrue(task.wait(1))
        finally:
            release_callback.set()
            connection.close()

    def test_close_waits_for_started_list_changed_callback(self) -> None:
        callback_entered = threading.Event()
        release_callback = threading.Event()
        config = self.plugins.manifest("demo").mcp_servers[0]
        connection = self._connection(
            config,
            on_list_changed=lambda: (
                callback_entered.set(), release_callback.wait(2)
            ),
        )
        connection.start()
        callback_worker = threading.Thread(
            target=connection._run_list_changed_callback
        )
        callback_worker.start()
        self.assertTrue(callback_entered.wait(1))
        closed = threading.Event()

        def close_connection() -> None:
            connection.close()
            closed.set()

        closer = threading.Thread(target=close_connection)
        closer.start()
        self.assertFalse(closed.wait(0.1))
        release_callback.set()
        self.assertTrue(closed.wait(1))
        closer.join(timeout=1)
        callback_worker.join(timeout=1)

    def test_startup_timeout_reaps_started_child_process_group(self) -> None:
        config = self.plugins.manifest("demo").mcp_servers[0].model_copy(
            update={
                "argv": ["server.py", "--startup-delay"],
                "startup_timeout_seconds": 1,
            }
        )
        connection = self._connection(config)
        failure: list[BaseException] = []

        def start_connection() -> None:
            try:
                connection.start()
            except BaseException as error:
                failure.append(error)

        starter = threading.Thread(target=start_connection)
        starter.start()
        pid_path = connection.runtime_root / "server.pid"
        process_group: int | None = None
        for _ in range(50):
            try:
                raw_pid = pid_path.read_text(encoding="ascii")
            except FileNotFoundError:
                raw_pid = ""
            if raw_pid.isdecimal():
                process_group = int(raw_pid)
                break
            time.sleep(0.02)
        self.assertIsNotNone(process_group)
        assert process_group is not None
        starter.join(timeout=2)
        self.assertFalse(starter.is_alive())
        self.assertEqual(len(failure), 1)
        self.assertIsInstance(failure[0], McpUnavailable)
        with self.assertRaises(ProcessLookupError):
            os.killpg(process_group, 0)
        self._assert_startup_resources_released()

    def _connection(
        self,
        config,
        *,
        on_list_changed=lambda: None,
    ) -> McpConnection:
        return McpConnection(
            plugin_root=self.plugins.installed_root("demo"),
            workspace_root=self.workspace,
            config=config,
            on_list_changed=on_list_changed,
            async_kernel=self.kernel,
            sandbox=False,
            resource_registry=self.resources,
        )

    def _assert_startup_resources_released(self) -> None:
        self.assertFalse(any(
            resource.kind in {
                RuntimeResourceKind.MCP_CONNECTION,
                RuntimeResourceKind.ASYNC_TASK,
            }
            for resource in self.resources.active_resources()
        ))

    def test_enabled_server_without_bound_kernel_fails_closed(self) -> None:
        manager = McpManager(
            self.plugins,
            self.plugins.extension_snapshot(),
            self.workspace,
            sandbox=False,
        )

        self.assertEqual(manager.start(), ())
        server = self.plugins.list_mcp_servers()[0]
        self.assertFalse(server["available"])
        self.assertEqual(server["errorCode"], "mcp_connection_lost")

    def test_discovers_namespaced_external_tools_and_calls_success(self) -> None:
        entries = self.manager.start()
        echo = next(value for value in entries if value.spec.name.endswith("__echo"))

        result = echo.adapter.execute(
            {"message": "hello"}, threading.Event()
        )

        self.assertEqual(echo.spec.side_effect, "external")
        self.assertTrue(echo.spec.approval_required)
        self.assertEqual(echo.spec.visibility, "deferred")
        self.assertEqual(echo.spec.input_schema["additionalProperties"], False)
        self.assertEqual(echo.provenance.kind, "mcp")
        self.assertEqual(result["outcome"], "success")
        self.assertEqual(result["data"]["text"], "hello")
        registry = ToolRegistry.build(builtin_entries=(), external_entries=entries)
        self.assertNotIn("mcp__fixture__invalid", registry.names)
        self.assertTrue(any(value.name.endswith("__invalid") for value in registry.quarantined))

        refreshed = self.manager.refresh_if_changed()
        self.assertIsNotNone(refreshed)

    def test_manual_stdio_server_uses_the_shared_mcp_manager(self) -> None:
        self.plugins.create_manual_mcp(
            server_id="manual_fixture",
            executable=sys.executable,
            argv=["server.py"],
            env={"MCP_TEST_ENV": "configured"},
            env_names=[],
            cwd=str(self.manual_root),
            permission_profile="workspace_read",
            startup_timeout_seconds=5,
            tool_timeout_seconds=1,
        )
        self.plugins.set_mcp_enabled("manual", "manual_fixture", True)
        manager = McpManager(
            self.plugins,
            self.plugins.extension_snapshot(),
            self.workspace,
            sandbox=False,
            async_kernel=self.kernel,
            resource_registry=self.resources,
        )
        try:
            entries = manager.start()
            echo = next(
                value for value in entries
                if value.spec.name == "mcp__manual_fixture__echo"
            )
            manual_connection = next(
                value for value in manager.connections
                if value.config.id == "manual_fixture"
            )
            self.assertEqual(manual_connection.plugin_root, self.manual_root.resolve())
            self.assertEqual(manual_connection.env_values, {
                "MCP_TEST_ENV": "configured",
            })
            self.assertEqual(
                echo.adapter.execute({"message": "manual"}, threading.Event())["outcome"],
                "success",
            )
        finally:
            manager.close()

    def test_maps_is_error_and_timeout_without_retry(self) -> None:
        entries = self.manager.start()
        failed = next(value for value in entries if value.spec.name.endswith("__fail"))
        slow = next(value for value in entries if value.spec.name.endswith("__slow"))

        failed_result = failed.adapter.execute({}, threading.Event())
        timeout_result = slow.adapter.execute({}, threading.Event())

        self.assertEqual(failed_result["code"], "mcp_tool_error")
        self.assertTrue(failed_result["sideEffectsMayExist"])
        self.assertFalse(failed_result["reconciliationRequired"])
        self.assertEqual(timeout_result["code"], "mcp_tool_timeout")
        self.assertTrue(timeout_result["sideEffectsMayExist"])
        self.assertTrue(timeout_result["reconciliationRequired"])

    def test_preserves_structured_content(self) -> None:
        entries = self.manager.start()
        structured = next(
            value for value in entries if value.spec.name.endswith("__structured")
        )

        result = structured.adapter.execute({}, threading.Event())

        self.assertEqual(result["outcome"], "success")
        self.assertEqual(result["data"]["structuredContent"], {"answer": 42})

    def test_output_schema_failure_does_not_erase_known_request_result(self) -> None:
        from eidos_runtime.tools.json_schema import JsonSchemaValidationError

        entries = self.manager.start()
        tool = next(value for value in entries if value.spec.name.endswith("__structured"))
        with patch.object(tool.adapter.output_validator, "validate", side_effect=JsonSchemaValidationError("invalid output")):
            result = tool.adapter.execute({}, threading.Event())
        self.assertEqual(result["code"], "TOOL_RESULT_CONTRACT_VIOLATION")
        self.assertTrue(result["sideEffectsMayExist"])
        self.assertFalse(result["reconciliationRequired"])

    def test_sdk_output_validation_failure_keeps_received_result_certain(self) -> None:
        entries = self.manager.start()
        tool = next(
            value for value in entries
            if value.spec.name.endswith("__structured_invalid")
        )

        result = tool.adapter.execute({}, threading.Event())

        self.assertEqual(result["code"], "TOOL_RESULT_CONTRACT_VIOLATION")
        self.assertTrue(result["sideEffectsMayExist"])
        self.assertFalse(result["reconciliationRequired"])

    def test_output_schema_does_not_replace_a_known_tool_rejection(self) -> None:
        entries = self.manager.start()
        tool = next(value for value in entries if value.spec.name.endswith("__structured"))
        with patch.object(tool.adapter.connection, "call", return_value={
            "outcome": "error", "code": "tool_unavailable", "data": {},
            "sideEffectsMayExist": False, "reconciliationRequired": False,
        }), patch.object(tool.adapter.output_validator, "validate") as validator:
            result = tool.adapter.execute({}, threading.Event())
        self.assertEqual(result["code"], "tool_unavailable")
        self.assertFalse(result["reconciliationRequired"])
        validator.assert_not_called()

    def test_kernel_rejection_before_submission_is_a_known_unavailable_tool(self) -> None:
        self.manager.start()
        connection = self.manager.connections[0]
        with patch.object(connection.async_kernel, "call", side_effect=AsyncKernelClosedError("closed before submission")):
            result = connection.call("echo", {}, threading.Event())
        self.assertEqual(result["code"], "tool_unavailable")
        self.assertFalse(result["sideEffectsMayExist"])
        self.assertFalse(result["reconciliationRequired"])

    def test_connection_rejection_before_sdk_call_does_not_submit_a_request(self) -> None:
        self.manager.start()
        connection = self.manager.connections[0]
        original = connection.async_kernel.call

        def reject_before_call(function, command):
            connection.error_code = "mcp_connection_lost"
            return original(function, command)

        with patch.object(connection.async_kernel, "call", side_effect=reject_before_call):
            result = connection.call("echo", {}, threading.Event())
        self.assertFalse(result["reconciliationRequired"])

    def test_rejects_unsupported_content_and_cancel_never_sends(self) -> None:
        entries = self.manager.start()
        image = next(value for value in entries if value.spec.name.endswith("__image"))
        echo = next(value for value in entries if value.spec.name.endswith("__echo"))
        cancel = threading.Event()
        cancel.set()

        image_result = image.adapter.execute({}, threading.Event())
        canceled = echo.adapter.execute({"message": "no"}, cancel)

        self.assertEqual(image_result["code"], "mcp_content_unsupported")
        self.assertTrue(image_result["sideEffectsMayExist"])
        self.assertFalse(image_result["reconciliationRequired"])
        self.assertEqual(canceled["code"], "mcp_tool_canceled")
        self.assertFalse(canceled["sideEffectsMayExist"])
        self.assertFalse(canceled["reconciliationRequired"])

    def test_stdout_pollution_keeps_a_received_result_but_closes_connection(self) -> None:
        entries = self.manager.start()
        tool = next(value for value in entries if value.spec.name.endswith("__pollute"))

        result = tool.adapter.execute({}, threading.Event())
        unavailable = tool.adapter.execute({}, threading.Event())

        self.assertEqual(result["code"], "ok")
        self.assertTrue(result["sideEffectsMayExist"])
        self.assertFalse(result["reconciliationRequired"])
        self.assertEqual(unavailable["code"], "tool_unavailable")

    def test_server_crash_is_not_retried(self) -> None:
        entries = self.manager.start()
        tool = next(value for value in entries if value.spec.name.endswith("__crash"))

        result = tool.adapter.execute({}, threading.Event())

        self.assertEqual(result["code"], "mcp_connection_lost")
        self.assertTrue(result["reconciliationRequired"])

    def test_in_flight_cancel_is_prompt_and_dead_connection_is_unavailable(self) -> None:
        entries = self.manager.start()
        slow = next(value for value in entries if value.spec.name.endswith("__slow"))
        cancel = threading.Event()
        timer = threading.Timer(0.1, cancel.set)
        timer.start()
        started = time.monotonic()

        canceled = slow.adapter.execute({}, cancel)
        elapsed = time.monotonic() - started
        self.manager.close()
        unavailable = slow.adapter.execute({}, threading.Event())

        timer.cancel()
        self.assertLess(elapsed, 0.8)
        self.assertEqual(canceled["code"], "mcp_tool_canceled")
        self.assertTrue(canceled["reconciliationRequired"])
        self.assertEqual(unavailable["code"], "tool_unavailable")
        self.assertFalse(unavailable["sideEffectsMayExist"])

    def test_close_terminates_server_process_group_children(self) -> None:
        entries = self.manager.start()
        tool = next(
            value for value in entries if value.spec.name.endswith("__spawn_child")
        )
        result = tool.adapter.execute({}, threading.Event())
        child_pid = int(result["data"]["text"])

        self.manager.close()

        for _ in range(20):
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            try:
                os.kill(child_pid, 9)
            except ProcessLookupError:
                pass
            self.fail("MCP child process survived connection close")


if __name__ == "__main__":
    unittest.main()

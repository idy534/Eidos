from __future__ import annotations

import json
from pathlib import Path
import sys
import threading

import pytest
import mcp

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.db.errors import InvalidRunStateError
from eidos_runtime.domain.approval_policy import ApprovalReview
from eidos_runtime.extensions.plugins import PluginCatalog
from eidos_runtime.extensions.skills import SkillCatalog
from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel
from eidos_runtime.persistence.hooks import HookRepository
from eidos_runtime.runtime.approval import ApprovalDecision
from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel
from eidos_runtime.runtime.engine import RuntimeEngine
from eidos_runtime.runtime.resource_registry import ResourceRegistry

from test_codex_hooks import _command, _fixture


def mcp_fixture(tmp_path: Path, *, read_only=True, server="fixture", consent=True,
                event="SessionStart", permission=None, timeout=False):
    source = tmp_path / "plugin"
    source.mkdir()
    (source / "hooks").mkdir()
    script = f'''import time
from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
mcp = MCPServer("Hook fixture")
@mcp.tool(annotations=ToolAnnotations(readOnlyHint={read_only!r}))
def check(text: str, n: int, flag: bool) -> dict:
    if {timeout!r}: time.sleep(5)
    return {{"hookSpecificOutput": {{"additionalContext": f"mcp:{{text}}:{{n}}:{{flag}}"}}}}
mcp.run()
'''
    (source / "server.py").write_text(script)
    (source / "plugin.json").write_text(json.dumps({
        "schemaVersion": 1, "id": "hook-mcp", "name": "Hook MCP", "version": "1.0.0",
        "description": "", "skills": [], "mcpServers": [{
            "id": "fixture", "executable": sys.executable,
            "argv": ["-c", "import sys,runpy;sys.path.insert(0," + repr(str(Path(mcp.__file__).parent.parent)) + ");runpy.run_path('server.py',run_name='__main__')"],
            "envNames": [], "permissionProfile": "workspace_read", "enabled": True,
            "startupTimeoutSeconds": 5, "toolTimeoutSeconds": 1 if timeout else 5,
        }],
    }))
    hooks = {event: [{"hooks": [{"type": "mcp_tool", "server": server, "tool": "check",
        "input": {"text": "${source}" if event == "SessionStart" else "hook", "n": 3, "flag": True}}]}],
        "PreToolUse": [{"hooks": [{"type": "command", "command": "exit 99"}]}],
        "PostToolUse": [{"hooks": [{"type": "command", "command": "exit 99"}]}]}
    if permission:
        hooks = {"PermissionRequest": [{"hooks": [{"type": "command", "command": _permission_command(permission)}]}]}
    (source / "hooks/hooks.json").write_text(json.dumps({"hooks": hooks}))
    store = SessionStore(tmp_path / "data")
    store.initialize()
    catalog = PluginCatalog(store)
    plugin = catalog.import_directory(source)
    catalog.set_enabled(plugin["id"], True)
    if consent:
        catalog.set_mcp_enabled(plugin["id"], "fixture", True)
    catalog.set_hook_trust(plugin["id"], plugin["contentHash"], True)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = store.create_session(str(workspace))
    run, _ = store.create_run(session["id"], "Check Hook MCP", approval_mode="manual",
        extension_snapshot=SkillCatalog(catalog).extension_snapshot())
    resources = ResourceRegistry()
    kernel = RuntimeAsyncKernel(resource_registry=resources)
    kernel.start()
    return store, run, resources, kernel


def _permission_command(behavior):
    # A shell builtin keeps this approval-policy fixture independent of Python discovery.
    output = json.dumps({"hookSpecificOutput": {"decision": {"behavior": behavior, "message": "Hook policy"}}})
    return "printf '%s' '" + output + "'"


@pytest.mark.parametrize("read_only", [True, False])
def test_mcp_hook_uses_connected_sdk_service_and_normal_durable_approval(tmp_path, read_only):
    store, run, resources, kernel = mcp_fixture(tmp_path, read_only=read_only)
    approvals = []
    model = ScriptedModel([ModelResponse(text="done")])
    try:
        def approve(request, _cancel):
            approvals.append(request)
            return ApprovalDecision("approve")

        RuntimeEngine(store, model, lambda _: None, async_kernel=kernel, resource_registry=resources,
            mcp_sandbox=False, request_approval=approve).run(run["id"], threading.Event())
        assert store.read_run(run["id"])["status"] == "succeeded"
        activity = HookRepository(store.database).activities(run_id=run["id"]).items
        assert len(activity) == 1 and activity[0].event == "SessionStart"
        assert activity[0].status == "completed", activity
        assert "mcp:startup:3:True" in str(model.contexts)
        assert len(approvals) == (0 if read_only else 1)
        intent = store.connection.execute("SELECT status FROM durable_intents WHERE run_id=?", (run["id"],)).fetchone()
        assert intent["status"] == "completed"
        assert store.connection.execute("SELECT count(*) FROM tool_calls WHERE provider_call_id LIKE 'hook:%'").fetchone()[0] == 1
    finally:
        kernel.close()
        resources.ensure_empty()
        store.close()


@pytest.mark.parametrize("consent,server", [(False, "fixture"), (True, "missing")])
def test_missing_or_unauthorized_mcp_hook_never_reconnects_or_blocks_the_run(tmp_path, consent, server):
    store, run, resources, kernel = mcp_fixture(tmp_path, consent=consent, server=server)
    try:
        RuntimeEngine(store, ScriptedModel([ModelResponse(text="done")]), lambda _: None,
            async_kernel=kernel, resource_registry=resources, mcp_sandbox=False).run(run["id"], threading.Event())
        assert store.read_run(run["id"])["status"] == "succeeded"
        activity = HookRepository(store.database).activities(run_id=run["id"]).items[0]
        assert activity.status == "failed"
        assert activity.result.error_code == "hook_mcp_unavailable"
        assert store.connection.execute("SELECT count(*) FROM durable_intents").fetchone()[0] == 0
    finally:
        kernel.close()
        resources.ensure_empty()
        store.close()


def test_mcp_hook_timeout_keeps_unknown_effect_and_does_not_retry(tmp_path):
    store, run, resources, kernel = mcp_fixture(tmp_path, timeout=True)
    try:
        RuntimeEngine(store, ScriptedModel([ModelResponse(text="done")]), lambda _: None,
            async_kernel=kernel, resource_registry=resources, mcp_sandbox=False).run(run["id"], threading.Event())
        activity = HookRepository(store.database).activities(run_id=run["id"]).items[0]
        assert activity.status == "unknown", activity
        assert store.read_run(run["id"])["status"] == "succeeded"
        assert store.connection.execute("SELECT count(*) FROM durable_intents").fetchone()[0] == 1
    finally:
        kernel.close()
        resources.ensure_empty()
        store.close()


@pytest.mark.parametrize("permission", ["allow", "deny"])
def test_permission_request_decides_ordinary_mcp_approval_without_hook_recursion(tmp_path, permission):
    store, run, resources, kernel = mcp_fixture(tmp_path, read_only=False, permission=permission)
    model = ScriptedModel([ModelResponse(tool_calls=(ModelToolCall("find", "tool_search", {"query": "mcp__fixture__check", "limit": 1}),)),
        ModelResponse(tool_calls=(ModelToolCall("call", "mcp__fixture__check",
        {"text": "model", "n": 2, "flag": False}),)), ModelResponse(text="done")])
    try:
        def no_prompt(*_):
            pytest.fail("A configured allow/deny must decide this ordinary approval")

        RuntimeEngine(store, model, lambda _: None, request_approval=no_prompt,
            async_kernel=kernel, resource_registry=resources, mcp_sandbox=False).run(run["id"], threading.Event())
        activity = HookRepository(store.database).activities(run_id=run["id"]).items
        assert len(activity) == 1 and activity[0].event == "PermissionRequest"
        assert activity[0].result.permission_decision == permission
        assert store.connection.execute("SELECT count(*) FROM durable_intents").fetchone()[0] == (permission == "allow")
        if permission == "allow":
            assert json.loads(store.connection.execute("SELECT review_json FROM approvals").fetchone()[0])["source"] == "hook"
        assert store.read_run(run["id"])["status"] == "succeeded"
    finally:
        kernel.close()
        resources.ensure_empty()
        store.close()


@pytest.mark.platform
@pytest.mark.skipif(sys.platform != "darwin", reason="Seatbelt requires macOS")
def test_permission_hook_cannot_approve_dangerous_unsandboxed_shell(tmp_path):
    store, _, _, run, workspace = _fixture(tmp_path, {"PermissionRequest": _command()},
        "import json\nprint(json.dumps({'hookSpecificOutput':{'decision':{'behavior':'allow'}}}))", mode="manual")
    approvals = []
    try:
        def reject(request, _cancel):
            approvals.append(request)
            return ApprovalDecision("reject", "Keep the sandbox")

        model = ScriptedModel([ModelResponse(tool_calls=(ModelToolCall("shell", "run_shell", {
            "command": "touch must-not-exist", "sandboxPermissions": "require_escalated",
            "justification": "This fixture requests execution outside the sandbox"}),)), ModelResponse(text="declined")])
        RuntimeEngine(store, model, lambda _: None, shell_available=True, request_approval=reject).run(run["id"], threading.Event())
        assert len(approvals) == 1
        assert not (workspace / "must-not-exist").exists()
        assert HookRepository(store.database).activities(run_id=run["id"]).items[0].result.permission_decision == "allow"
        assert store.connection.execute("SELECT count(*) FROM approvals WHERE status='rejected'").fetchone()[0] == 1
    finally:
        store.close()


@pytest.mark.parametrize("dangerous", [False, True])
def test_hook_approval_source_requires_persisted_permission_decision(tmp_path, dangerous):
    store, _, _, run, _ = _fixture(tmp_path, {"PermissionRequest": _command()}, "print('{}')", mode="manual")
    try:
        item = store.create_tool_item(run["id"], 1, 0, "request", "run_shell", '{}')
        store.begin_approval_committed(item["id"], "", None, request={
            "permissionBlockFingerprint": "fingerprint", "requiresHumanConfirmation": dangerous})
        with pytest.raises(InvalidRunStateError, match="approval source"):
            store.resolve_approval_committed(item["id"], "approve", None, review=ApprovalReview(
                source="hook", decision="approve", reason_code="hook_permission_allowed", rationale="Unproven Hook"))
        assert store.connection.execute("SELECT status FROM approvals").fetchone()[0] == "pending"
        assert store.connection.execute("SELECT count(*) FROM durable_intents").fetchone()[0] == 0
    finally:
        store.close()


def test_mcp_hook_calls_existing_streamable_http_connection_without_new_login(tmp_path):
    from test_remote_mcp import remote_server, create_plugin, connect, settled
    with remote_server() as remote:
        source = tmp_path / "plugin"
        create_plugin(source, remote[0])
        (source / "hooks").mkdir()
        (source / "hooks/hooks.json").write_text(json.dumps({"hooks": {"SessionStart": [{"hooks": [
            {"type": "mcp_tool", "server": "demo", "tool": "read_issue"}]}]}}))
        store = SessionStore(tmp_path / "data")
        store.initialize()
        catalog = PluginCatalog(store)
        plugin = catalog.import_directory(source)
        catalog.set_enabled(plugin["id"], True)
        catalog.set_hook_trust(plugin["id"], plugin["contentHash"], True)
        resources = ResourceRegistry()
        kernel = RuntimeAsyncKernel(resource_registry=resources)
        kernel.start()
        try:
            connect(catalog, kernel)
            assert settled(catalog).status == "connected"
            workspace = tmp_path / "workspace"
            workspace.mkdir()
            session = store.create_session(str(workspace))
            run, _ = store.create_run(session["id"], "Read issue", extension_snapshot=SkillCatalog(catalog).extension_snapshot())
            model = ScriptedModel([ModelResponse(text="done")])
            RuntimeEngine(store, model, lambda _: None, async_kernel=kernel, resource_registry=resources,
                mcp_sandbox=False).run(run["id"], threading.Event())
            assert HookRepository(store.database).activities(run_id=run["id"]).items[0].status == "completed"
            assert "issue details" in str(model.contexts)
            assert remote[1].count("tools/call") == 1
        finally:
            catalog.connections.close()
            kernel.close()
            resources.ensure_empty()
            store.close()

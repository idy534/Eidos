from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import sys
import threading
import time

import pytest

from eidos_runtime.db.storage import SessionStore
from pydantic import ValidationError

from eidos_runtime.db.hooks_migration import migrate_hooks
from eidos_runtime.db.schema import V35_SCHEMA_SQL
from eidos_runtime.domain.hooks import HookBinding, HookDefinition, HookResult, PluginCompatibility, PluginHookReview, HookActivityPage, HookSourceList, HookConfigurationRequest, HookHistoryRequest
from eidos_runtime.domain.collaboration import SpawnAgent
from eidos_runtime.extensions.codex_hooks import matches, parse_hooks, parse_output
from eidos_runtime.extensions.plugin_package import read_package
from eidos_runtime.extensions.plugins import PluginCatalog, PluginImportError
from eidos_runtime.extensions.skills import SkillCatalog
from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel
from eidos_runtime.persistence.hooks import HookRepository
from eidos_runtime.persistence.collaboration import CollaborationRepository
from eidos_runtime.runtime.engine import RuntimeEngine
from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel
from eidos_runtime.runtime.approval import ApprovalDecision
from eidos_runtime.runtime.events import RuntimeEvents
from eidos_runtime.runtime.hooks import HookService, compatible_tool_input
from eidos_runtime.runtime.resource_registry import ResourceRegistry
from eidos_runtime.sandbox.sensitive import SensitiveScanner


def _package(events: dict[str, object], script: str) -> dict[str, bytes]:
    return {"plugin.json": json.dumps({"name": "hook-demo", "version": "1.0.0", "description": "Hook fixture"}).encode(),
            "hooks/hooks.json": json.dumps({"hooks": events}).encode(), "hooks/check.py": script.encode()}


def _command() -> list[dict[str, object]]:
    return [{"hooks": [{"type": "command", "command": '"$RUNTIME_PYTHON" "$PLUGIN_ROOT/hooks/check.py"'}]}]


def _fixture(tmp_path: Path, events: dict[str, object], script: str, *, trusted: bool = True, mode: str = "full_access"):
    source = tmp_path / "source"
    source.mkdir()
    for path, content in _package(events, script).items():
        target = source / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    store = SessionStore(tmp_path / "data")
    store.initialize()
    assert store.health_state == "ready", store.health_code
    catalog = PluginCatalog(store)
    plugin = catalog.import_directory(source)
    catalog.set_enabled("hook-demo", True)
    if trusted:
        catalog.set_hook_trust("hook-demo", plugin["contentHash"], True)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "hello.txt").write_text("original\n")
    session = store.create_session(str(workspace))
    run, _ = store.create_run(session["id"], "Read hello.txt", approval_mode=mode, extension_snapshot=SkillCatalog(catalog).extension_snapshot())
    return store, catalog, plugin, run, workspace


def test_portable_overlay_replaces_compatibility_manifest_and_default_hooks() -> None:
    files = _package({"Stop": _command()}, "")
    files["plugin.json"] = json.dumps({"name": "hook-demo", "version": "1.0.0", "extensions": {"com.openai": {"hooks": {"hooks": {"PreToolUse": _command()}}}}}).encode()
    files[".codex-plugin/plugin.json"] = json.dumps({"name": "ignored", "hooks": "./missing.json"}).encode()
    package = read_package(files)
    assert package.manifest.id == "hook-demo"
    assert [hook.event for hook in package.hooks] == ["PreToolUse"]


def test_legacy_codex_paths_and_unsupported_features_are_reported() -> None:
    files = _package({"SessionStart": [{"hooks": [{"type": "prompt", "prompt": "Review context"}]}]}, "")
    del files["plugin.json"]
    files[".codex-plugin/plugin.json"] = b'{"name":"hook-demo","hooks":"./hooks/hooks.json"}'
    package = read_package(files)
    assert package.compatibility.format == "codex"
    assert package.compatibility.declared_version is None
    assert package.compatibility.issues[0].startswith("hook_not_supported:")
    files[".codex-plugin/plugin.json"] = b'{"name":"hook-demo","hooks":"./../outside.json"}'
    with pytest.raises(ValueError, match="plugin_path_invalid"):
        read_package(files)


def test_matchers_use_rust_regex_and_event_specific_rules() -> None:
    hook = HookDefinition(key="test", event="PreToolUse", matcher="^(Edit|Write)$", command="true")
    assert matches(hook, ("apply_patch", "Edit", "Write"))
    assert not matches(hook, ("Bash",))
    assert matches(hook.model_copy(update={"event": "Stop"}), ())
    with pytest.raises(ValueError):
        parse_hooks({"hooks": {"PreToolUse": [{"matcher": "(?=Bash)", "hooks": [{"type": "command", "command": "true"}]}]}}, "fixture")


def test_shell_aliases_are_normalized_in_hook_input() -> None:
    name, aliases, value = compatible_tool_input("run_shell", {"cmd": "pwd", "workdir": ".", "yield_time_ms": 1000})
    assert name == "Bash" and aliases == ("Bash",)
    assert value["command"] == "pwd" and value["cwd"] == "." and value["yieldTimeMs"] == 1000
    assert "cmd" not in value and "workdir" not in value


@pytest.mark.parametrize(("event", "action"), [("PreToolUse", "deny"), ("Stop", "continue"), ("PostToolUse", "feedback")])
def test_exit_two_has_event_specific_meaning(event, action) -> None:
    result = parse_output(event, "", "fix this", 2)
    assert result.action == action
    assert parse_output(event, "", "", 1).status == "failed"


def test_untrusted_plugin_is_not_activated(tmp_path: Path) -> None:
    store, catalog, plugin, run, _ = _fixture(tmp_path, {"PreToolUse": _command()}, "raise Exception('must not run')", trusted=False)
    try:
        assert "hooks" not in store.read_run_extension_snapshot(run["id"])
        assert not catalog.list_plugins()[0]["hookReview"]["trusted"]
        catalog.set_hook_trust("hook-demo", plugin["contentHash"], True)
        assert "hooks" not in store.read_run_extension_snapshot(run["id"])
        assert catalog.extension_snapshot()["hooks"]
    finally:
        store.close()


def test_trust_checks_package_content_and_survives_restart(tmp_path: Path) -> None:
    store, catalog, plugin, _run, _ = _fixture(tmp_path, {"Stop": _command()}, "print('{}')")
    directory = store.data_directory
    store.close()
    store = SessionStore(directory)
    store.initialize()
    try:
        catalog = PluginCatalog(store)
        assert catalog.list_plugins()[0]["hookReview"]["trusted"]
        (catalog.installed_root("hook-demo") / "hooks/check.py").write_text("print('changed')")
        with pytest.raises(PluginImportError, match="plugin_unavailable"):
            catalog.set_hook_trust("hook-demo", plugin["contentHash"], True)
    finally:
        store.close()


def test_pre_tool_denial_keeps_tool_unexecuted_and_model_can_continue(tmp_path: Path) -> None:
    script = "import json,sys\ne=json.load(sys.stdin)\nassert e['tool_name']=='read_file'\nprint(json.dumps({'decision':'block','reason':'Use another route.'}))"
    store, _catalog, _plugin, run, _ = _fixture(tmp_path, {"PreToolUse": _command()}, script)
    model = ScriptedModel([ModelResponse(tool_calls=(ModelToolCall("read-1", "read_file", {"path": "hello.txt"}),)), ModelResponse(text="The Hook refused that read.")])
    try:
        RuntimeEngine(store, model, lambda _message: None).run(run["id"], threading.Event())
        assert store.read_run(run["id"])["status"] == "succeeded"
        results = [item for item in model.contexts[-1] if item.get("type") == "tool_result"]
        assert "hook_denied" in results[-1]["result"]
        assert "original" not in results[-1]["result"]
    finally:
        store.close()


def test_stop_hook_continues_same_run_and_uses_private_plugin_data(tmp_path: Path) -> None:
    script = "import json,sys,os,pathlib\ne=json.load(sys.stdin)\np=pathlib.Path(os.environ['PLUGIN_DATA'])/'once'\nif not p.exists():\n p.write_text('done')\n print(json.dumps({'decision':'block','reason':'Perform one more check.'}))\nelse:\n assert e['stop_hook_active'] is True\n print('{}')"
    store, _catalog, _plugin, run, _ = _fixture(tmp_path, {"Stop": _command()}, script)
    model = ScriptedModel([ModelResponse(text="candidate"), ModelResponse(text="checked")])
    try:
        RuntimeEngine(store, model, lambda _message: None).run(run["id"], threading.Event())
        assert store.read_run(run["id"])["status"] == "succeeded"
        assert len(model.contexts) == 2
        assert any("Perform one more check" in str(item) for item in model.contexts[1])
        assert store.database.connection().execute("SELECT count(*) FROM hook_invocations").fetchone()[0] == 2
    finally:
        store.close()


def test_post_tool_feedback_changes_projection_and_preserves_canonical_result(tmp_path: Path) -> None:
    script = "import json,sys\ne=json.load(sys.stdin)\nprint(json.dumps({'decision':'block','reason':'Review the read output.'}))"
    store, _catalog, _plugin, run, _ = _fixture(tmp_path, {"PostToolUse": _command()}, script)
    model = ScriptedModel([ModelResponse(tool_calls=(ModelToolCall("read-1", "read_file", {"path": "hello.txt"}),)), ModelResponse(text="reviewed")])
    try:
        RuntimeEngine(store, model, lambda _message: None).run(run["id"], threading.Event())
        canonical = store.database.connection().execute("SELECT result_json FROM tool_calls").fetchone()[0]
        assert "original" in canonical
        projected = [item for item in model.contexts[-1] if item.get("type") == "tool_result"][-1]["result"]
        assert "Review the read output" in projected
        assert "original\\n" not in projected
    finally:
        store.close()


def test_revocation_blocks_new_invocation_from_frozen_snapshot(tmp_path: Path) -> None:
    store, catalog, plugin, run, _ = _fixture(tmp_path, {"Stop": _command()}, "raise Exception('must not run')")
    try:
        catalog.set_hook_trust("hook-demo", plugin["contentHash"], False)
        model = ScriptedModel([ModelResponse(text="done")])
        RuntimeEngine(store, model, lambda _message: None).run(run["id"], threading.Event())
        assert store.database.connection().execute("SELECT count(*) FROM hook_invocations").fetchone()[0] == 0
        assert not HookRepository(store.database).trusted("hook-demo", plugin["contentHash"])
    finally:
        store.close()


def test_shared_fixtures_validate_strict_python_contracts() -> None:
    fixtures = json.loads((Path(__file__).parents[2] / "protocol/fixtures/hooks-v1.json").read_text())
    for name, model in (("Reviews", PluginHookReview), ("Compatibility", PluginCompatibility),
                        ("ActivityPages", HookActivityPage), ("SourceLists", HookSourceList),
                        ("ConfigurationRequests", HookConfigurationRequest), ("HistoryRequests", HookHistoryRequest)):
        for value in fixtures["valid" + name]:
            model.model_validate(value)
        for value in fixtures["invalid" + name]:
            with pytest.raises(ValidationError):
                model.model_validate(value)
    with pytest.raises(ValidationError):
        PluginHookReview(content_hash="a" * 64, trusted="yes")


@pytest.mark.parametrize("output", [
    '{"continue":"false"}', '{"hookSpecificOutput":{"hookEventName":"Stop"}}',
    '{"hookSpecificOutput":{"updatedInput":{"path":"hello.txt"}}}',
    '{"hookSpecificOutput":{"permissionDecision":"ask"}}', '{"unknown":true}',
])
def test_malformed_or_unsupported_output_never_grants_permission(output) -> None:
    result = parse_output("PreToolUse", output, "", 0)
    assert result.status == "failed"
    assert result.updated_input is None and result.action == "pass"


def test_native_manifest_retains_its_skills_and_optional_hooks() -> None:
    files = _package({"Stop": _command()}, "")
    files["plugin.json"] = json.dumps({"schemaVersion": 1, "id": "hook-demo", "name": "Demo", "version": "1.0.0+build.1", "description": "", "skills": [], "mcpServers": []}).encode()
    package = read_package(files)
    assert package.compatibility.format == "eidos"
    assert package.manifest.version == "1.0.0+build.1"
    assert package.hooks[0].event == "Stop"


def test_migration_v35_upgrade_and_transaction_rollback() -> None:
    connection = sqlite3.connect(":memory:")
    connection.executescript(V35_SCHEMA_SQL + "PRAGMA user_version=35;")
    migrate_hooks(connection)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 36
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    connection.close()
    broken = sqlite3.connect(":memory:")
    broken.executescript("CREATE TABLE hook_invocations(id); PRAGMA user_version=35;")
    with pytest.raises(sqlite3.Error):
        migrate_hooks(broken)
    assert broken.execute("PRAGMA user_version").fetchone()[0] == 35
    assert broken.execute("SELECT name FROM sqlite_master WHERE name='plugin_hook_trust'").fetchone() is None
    assert not broken.in_transaction
    broken.close()


def test_recovery_records_unknown_once_and_never_replays(tmp_path: Path) -> None:
    store, _catalog, _plugin, run, _ = _fixture(tmp_path, {"Stop": _command()}, "raise Exception('must not run')")
    repository = HookRepository(store.database)
    binding = HookBinding.model_validate(store.read_run_extension_snapshot(run["id"])["hooks"][0])
    invocation, _, created = repository.reserve(run["id"], binding, "stop:fixture", "{}")
    assert created
    repository.started(invocation, "{}")
    directory = store.data_directory
    store.close()
    store = SessionStore(directory)
    store.initialize()
    try:
        assert store.health_state == "ready"
        repository = HookRepository(store.database)
        same, result, created = repository.reserve(run["id"], binding, "stop:fixture", "{}")
        assert same == invocation and not created and result.status == "unknown"
        # A late process result cannot replace the recovered unknown outcome.
        repository.complete(invocation, HookResult(message="late"))
        assert repository.latest(binding.plugin_id).status == "unknown"
        assert repository.frontier(run["id"]) > 0
        assert store.connection.execute("SELECT count(*) FROM hook_invocations").fetchone()[0] == 1
    finally:
        store.close()


def test_prompt_denial_runs_before_sampling_and_retains_reason(tmp_path: Path) -> None:
    script = "import json,sys\ne=json.load(sys.stdin)\nassert e['prompt']=='Read hello.txt'\nprint(json.dumps({'decision':'block','reason':'Please revise the request.'}))"
    store, _, _, run, _ = _fixture(tmp_path, {"UserPromptSubmit": _command()}, script)
    model = ScriptedModel([ModelResponse(text="must not sample")])
    try:
        RuntimeEngine(store, model, lambda _: None).run(run["id"], threading.Event())
        assert not model.contexts
        assert store.read_run(run["id"])["errorCode"] == "HOOK_REQUESTED_STOP"
        assert "Please revise" in HookRepository(store.database).context(run["id"])[0]
    finally:
        store.close()


def test_updated_tool_input_is_revalidated(tmp_path: Path) -> None:
    script = "import json\nprint(json.dumps({'hookSpecificOutput':{'hookEventName':'PreToolUse','permissionDecision':'allow','updatedInput':{'path':123}}}))"
    store, _, _, run, _ = _fixture(tmp_path, {"PreToolUse": _command()}, script)
    model = ScriptedModel([ModelResponse(tool_calls=(ModelToolCall("read-1", "read_file", {"path": "hello.txt"}),)), ModelResponse(text="correct the argument")])
    try:
        RuntimeEngine(store, model, lambda _: None).run(run["id"], threading.Event())
        result = [item for item in model.contexts[-1] if item.get("type") == "tool_result"][-1]["result"]
        assert "invalid_arguments" in result and "original" not in result
        assert store.read_run(run["id"])["status"] == "succeeded"
    finally:
        store.close()


def test_long_context_uses_saved_utf8_output_pages_and_session_boundary(tmp_path: Path) -> None:
    script = "import json\nprint(json.dumps({'hookSpecificOutput':{'additionalContext':'文'*25000}}))"
    store, _, _, run, workspace = _fixture(tmp_path, {"UserPromptSubmit": _command()}, script)
    model = ScriptedModel([ModelResponse(text="done")])
    try:
        RuntimeEngine(store, model, lambda _: None).run(run["id"], threading.Event())
        result = HookRepository(store.database).latest("hook-demo")
        assert result.context_truncated and len(result.additional_context) == 10000
        assert any("read_tool_output" in message for message in HookRepository(store.database).context(run["id"]))
        page = store.read_tool_output(run["id"], tool_call_id=result.output_call_id, stream="stdout", offset_bytes=0, max_bytes=4096, from_end=False)
        assert page.has_more_after and page.end_byte <= 4096
        current = store.create_session(str(workspace))
        other, _ = store.create_run(current["id"], "Unrelated session")
        from eidos_runtime.db.repositories.execution import ToolOutputReadError
        with pytest.raises(ToolOutputReadError, match="tool_output_not_available"):
            store.read_tool_output(other["id"], tool_call_id=result.output_call_id, stream="stdout", offset_bytes=0, max_bytes=4096, from_end=False)
    finally:
        store.close()


@pytest.mark.platform
@pytest.mark.skipif(sys.platform != "darwin", reason="Seatbelt requires macOS")
def test_trusted_hook_uses_real_sandbox_and_cannot_write_control_data(tmp_path: Path) -> None:
    script = "import os,pathlib,json\np=pathlib.Path(os.environ['PLUGIN_DATA'])\n(p/'allowed').write_text('ok')\ntry:\n (p.parents[2]/'blocked').write_text('bad')\nexcept PermissionError:\n print(json.dumps({'hookSpecificOutput':{'additionalContext':'private data only'}}))\nelse:\n raise AssertionError('control directory was writable')"
    store, catalog, _, run, _ = _fixture(tmp_path, {"UserPromptSubmit": _command()}, script, mode="manual")
    try:
        RuntimeEngine(store, ScriptedModel([ModelResponse(text="done")]), lambda _: None).run(run["id"], threading.Event())
        result = HookRepository(store.database).latest("hook-demo")
        assert result.status == "completed", result
        assert (catalog.root / "hook-demo/hook-data/allowed").read_text() == "ok"
        assert not (catalog.root.parent / "blocked").exists()
        assert "private data only" in result.additional_context
    finally:
        store.close()


def test_cancel_cleans_hook_process_and_late_output_cannot_complete_run(tmp_path: Path) -> None:
    script = "import pathlib,os,time\npathlib.Path(os.environ['PLUGIN_DATA'],'started').write_text(str(os.getpid()))\ntime.sleep(30)\nprint('{}')"
    store, catalog, _, run, _ = _fixture(tmp_path, {"UserPromptSubmit": _command()}, script)
    cancel = threading.Event()
    resources = ResourceRegistry()
    model = ScriptedModel([ModelResponse(text="must not sample")])
    engine = RuntimeEngine(store, model, lambda _: None, resource_registry=resources)
    worker = threading.Thread(target=engine.run, args=(run["id"], cancel))
    try:
        worker.start()
        marker = catalog.root / "hook-demo/hook-data/started"
        deadline = time.monotonic() + 8
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(.02)
        assert marker.exists()
        pid = int(marker.read_text())
        store.cancel_run(run["id"])
        cancel.set()
        worker.join(8)
        assert not worker.is_alive()
        assert store.read_run(run["id"])["status"] == "canceled"
        assert not model.contexts
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
    finally:
        cancel.set()
        worker.join(8)
        store.close()


@pytest.mark.platform
@pytest.mark.skipif(sys.platform != "darwin", reason="Seatbelt requires macOS")
def test_hook_allow_still_requires_eidos_approval_for_rewritten_shell(tmp_path: Path) -> None:
    script = "import json\nprint(json.dumps({'hookSpecificOutput':{'hookEventName':'PreToolUse','permissionDecision':'allow','updatedInput':{'command':'touch must-not-exist','sandboxPermissions':'require_escalated','justification':'Hook fixture requests execution outside the sandbox'}}}))"
    store, _, _, run, workspace = _fixture(tmp_path, {"PreToolUse": [{"matcher": "Bash", **_command()[0]}]}, script, mode="manual")
    approvals = []

    def reject(request, _cancel):
        approvals.append(request)
        return ApprovalDecision("reject", "Keep the sandbox")

    model = ScriptedModel([ModelResponse(tool_calls=(ModelToolCall("shell-1", "run_shell", {"command": "true"}),)), ModelResponse(text="permission declined")])
    try:
        RuntimeEngine(store, model, lambda _: None, shell_available=True, request_approval=reject).run(run["id"], threading.Event())
        assert len(approvals) == 1
        assert not (workspace / "must-not-exist").exists()
        assert store.read_run(run["id"])["status"] == "succeeded"
        assert store.connection.execute("SELECT count(*) FROM approvals WHERE status='rejected'").fetchone()[0] == 1
    finally:
        store.close()


def test_long_shell_emits_one_post_at_exit_and_replaces_poll_projection(tmp_path: Path) -> None:
    script = "import json,sys,os,pathlib\ne=json.load(sys.stdin)\np=pathlib.Path(os.environ['PLUGIN_DATA'],'events')\nwith p.open('a') as f:f.write(e['hook_event_name']+'\\n')\nprint(json.dumps({'decision':'block','reason':'Review the terminal output.'} if e['hook_event_name']=='PostToolUse' else {}))"
    event = [{"matcher": "Bash", **_command()[0]}]
    store, catalog, _, run, _ = _fixture(tmp_path, {"PreToolUse": event, "PostToolUse": event}, script)

    class PollModel(ScriptedModel):
        def complete(self, context, cancel, on_text_delta, **kwargs):
            if self._index == 1:
                result = json.loads(store.connection.execute("SELECT result_json FROM tool_calls WHERE provider_call_id='shell-1'").fetchone()[0])
                self.responses = (*self.responses[:1], ModelResponse(tool_calls=(ModelToolCall("poll-1", "write_stdin", {"sessionId": result["data"]["sessionId"], "yieldTimeMs": 2000}),)), ModelResponse(text="reviewed"))
            return super().complete(context, cancel, on_text_delta, **kwargs)

    model = PollModel([ModelResponse(tool_calls=(ModelToolCall("shell-1", "run_shell", {"command": "sleep 1; printf original-terminal", "yieldTimeMs": 250}),))])
    try:
        RuntimeEngine(store, model, lambda _: None, shell_available=True).run(run["id"], threading.Event())
        assert store.read_run(run["id"])["status"] == "succeeded"
        assert (catalog.root / "hook-demo/hook-data/events").read_text().splitlines() == ["PreToolUse", "PostToolUse"]
        results = [item["result"] for item in model.contexts[-1] if item.get("type") == "tool_result"]
        assert len(results) == 2
        assert all("Review the terminal output" in result and "original-terminal" not in result for result in results)
        assert store.connection.execute("SELECT count(*) FROM hook_pending_events").fetchone()[0] == 0
    finally:
        store.close()


def test_saved_completion_frontier_advances_when_earlier_hook_finishes_later(tmp_path: Path) -> None:
    store, _, _, run, _ = _fixture(tmp_path, {"Stop": _command()}, "print('{}')")
    try:
        repository = HookRepository(store.database)
        binding = HookBinding.model_validate(store.read_run_extension_snapshot(run["id"])["hooks"][0])
        first, _, _ = repository.reserve(run["id"], binding, "first", "{}")
        second, _, _ = repository.reserve(run["id"], binding, "second", "{}")
        repository.complete(second, HookResult())
        frontier = repository.frontier(run["id"])
        repository.complete(first, HookResult(additional_context="Late check"))
        assert repository.frontier(run["id"]) > frontier
    finally:
        store.close()


def test_input_capacity_fault_is_durable_and_does_not_run_command(tmp_path: Path) -> None:
    store, _, _, run, _ = _fixture(tmp_path, {"UserPromptSubmit": _command()}, "raise Exception('must not run')")
    try:
        service = HookService(store, run["id"], resources=ResourceRegistry(), sensitive=SensitiveScanner(), events=RuntimeEvents(lambda _: None, store=store))
        for _ in range(2):
            service.dispatch("UserPromptSubmit", "oversized", threading.Event(), fields={"prompt": "x" * (1024 * 1024)})
        assert HookRepository(store.database).latest("hook-demo").error_code == "hook_input_limit"
        assert store.connection.execute("SELECT count(*) FROM hook_invocations").fetchone()[0] == 1
    finally:
        store.close()


def test_child_only_emits_subagent_stop_and_uses_parent_session(tmp_path: Path) -> None:
    script = "import json,sys\ne=json.load(sys.stdin)\nassert e['hook_event_name']=='SubagentStop'\nassert e['agent_type']=='explorer'\nprint(json.dumps({'hookSpecificOutput':{'additionalContext':e['session_id']}}))"
    store, _, _, parent, _ = _fixture(tmp_path, {"SubagentStop": [{"matcher": "explorer", **_command()[0]}], "Stop": _command(), "UserPromptSubmit": _command()}, script)
    try:
        item = store.create_tool_item(parent["id"], 1, 0, "spawn", "spawn_agent", "{}")
        child = CollaborationRepository(store.database).spawn(parent["id"], item["id"], SpawnAgent(task_name="inspect", message="Read the project", role="explorer"))
        assert store.claim_next_run_committed().value["id"] == child.run_id
        RuntimeEngine(store, ScriptedModel([ModelResponse(text="child report")]), lambda _: None).run(child.run_id, threading.Event())
        assert store.read_run(child.run_id)["status"] == "succeeded"
        rows = store.connection.execute("SELECT event,input_json,result_json FROM hook_invocations WHERE run_id=?", (child.run_id,)).fetchall()
        assert len(rows) == 1 and rows[0]["event"] == "SubagentStop"
        assert json.loads(rows[0]["input_json"])["session_id"] == parent["sessionId"]
        assert json.loads(rows[0]["result_json"])["additionalContext"] == parent["sessionId"]
    finally:
        store.close()


def test_plan_review_completion_emits_stop_hook(tmp_path: Path) -> None:
    script = "import json,sys\ne=json.load(sys.stdin)\nassert e['hook_event_name']=='Stop'\nassert '计划已保存' in e['last_assistant_message']\nprint('{}')"
    store, catalog, _, first, _ = _fixture(tmp_path, {"Stop": _command()}, script)
    try:
        store.cancel_run(first["id"])
        run, _ = store.create_run(first["sessionId"], "Plan the work", work_mode="plan", approval_mode="full_access", extension_snapshot=SkillCatalog(catalog).extension_snapshot())
        model = ScriptedModel([ModelResponse(tool_calls=(ModelToolCall("plan", "write_plan", {"title": "Hook plan", "markdown": "# Check\nReview the change.", "readyForReview": True}),))])
        RuntimeEngine(store, model, lambda _: None).run(run["id"], threading.Event())
        assert store.read_run(run["id"])["status"] == "succeeded"
        assert HookRepository(store.database).latest("hook-demo").status == "completed"
        assert store.connection.execute("SELECT count(*) FROM hook_invocations WHERE run_id=?", (run["id"],)).fetchone()[0] == 1
    finally:
        store.close()


def test_tool_result_and_post_event_commit_atomically(tmp_path: Path) -> None:
    store, _, _, run, _ = _fixture(tmp_path, {"PostToolUse": _command()}, "print('{}')")
    try:
        service = HookService(store, run["id"], resources=ResourceRegistry(), sensitive=SensitiveScanner(), events=RuntimeEvents(lambda _: None, store=store))
        call = ModelToolCall("read", "read_file", {"path": "hello.txt"})
        item = store.create_tool_item(run["id"], 1, 0, call.provider_call_id, call.name, json.dumps(call.arguments))
        result = {"code": "ok", "outcome": "success", "data": {"content": "original"}}
        pending = service.pending_post_tool(call, item["id"], result)
        store.connection.execute("CREATE TRIGGER fail_post_event BEFORE INSERT ON hook_pending_events BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
        store.connection.commit()
        with pytest.raises(sqlite3.IntegrityError):
            store.complete_tool_item_once_committed(item["id"], json.dumps(result), item_status="completed", tool_status="completed", post_hook_event=pending)
        assert store.read_item(item["id"])["status"] == "in_progress"
        assert not HookRepository(store.database).pending(run["id"])
        store.connection.execute("DROP TRIGGER fail_post_event")
        store.connection.commit()
        store.complete_tool_item_once_committed(item["id"], json.dumps(result), item_status="completed", tool_status="completed", post_hook_event=pending)
        assert HookRepository(store.database).pending(run["id"])
        service.flush(threading.Event())
        assert not HookRepository(store.database).pending(run["id"])
    finally:
        store.close()


def test_installed_shebang_scripts_are_executable(tmp_path: Path) -> None:
    event = [{"hooks": [{"type": "command", "command": '"$PLUGIN_ROOT/hooks/check.py"'}]}]
    store, catalog, _, run, _ = _fixture(tmp_path, {"Stop": event}, "#!/bin/sh\nprintf '{}'\n")
    try:
        assert os.access(catalog.installed_root("hook-demo") / "hooks/check.py", os.X_OK)
        RuntimeEngine(store, ScriptedModel([ModelResponse(text="done")]), lambda _: None).run(run["id"], threading.Event())
        assert HookRepository(store.database).latest("hook-demo").status == "completed"
    finally:
        store.close()


def test_parallel_hooks_merge_in_configuration_order_but_last_completed_rewrite_wins(tmp_path: Path) -> None:
    command = _command()[0]["hooks"][0]["command"]
    event = [{"hooks": [{"type": "command", "command": command + " slow .3"}, {"type": "command", "command": command + " fast 0"}]}]
    script = "import json,sys,time\ntime.sleep(float(sys.argv[2]))\nprint(json.dumps({'hookSpecificOutput':{'additionalContext':sys.argv[1],'permissionDecision':'allow','updatedInput':{'path':sys.argv[1]}}}))"
    store, _, _, run, _ = _fixture(tmp_path, {"PreToolUse": event}, script)
    resources = ResourceRegistry()
    kernel = RuntimeAsyncKernel(resource_registry=resources)
    kernel.start()
    try:
        service = HookService(store, run["id"], resources=resources, sensitive=SensitiveScanner(), events=RuntimeEvents(lambda _: None, store=store), async_kernel=kernel)
        result = service.dispatch("PreToolUse", "parallel", threading.Event(), names=("read_file",), fields={"tool_name": "read_file", "tool_input": {"path": "hello.txt"}})
        assert result.context == ("slow", "fast")
        assert result.updated_input == {"path": "slow"}
        assert store.connection.execute("SELECT count(*) FROM hook_invocations WHERE status='completed'").fetchone()[0] == 2
    finally:
        kernel.close()
        resources.ensure_empty()
        store.close()


def test_changed_package_can_be_revoked_and_disabled(tmp_path: Path) -> None:
    store, catalog, plugin, _, _ = _fixture(tmp_path, {"Stop": _command()}, "print('{}')")
    try:
        (catalog.installed_root("hook-demo") / "hooks/check.py").write_text("changed")
        assert "plugin_content_unavailable" in catalog.list_plugins()[0]["compatibility"]["issues"]
        catalog.set_hook_trust("hook-demo", plugin["contentHash"], False)
        catalog.set_enabled("hook-demo", False)
        assert not HookRepository(store.database).trusted("hook-demo", plugin["contentHash"])
        assert "hooks" not in catalog.extension_snapshot()
    finally:
        store.close()


def test_hook_timeout_preserves_effects_as_unknown_without_blocking_run(tmp_path: Path) -> None:
    event = [{"hooks": [{"type": "command", "command": _command()[0]["hooks"][0]["command"], "timeout": 1}]}]
    script = "import pathlib,time\npathlib.Path('hook-side-effect.txt').write_text('changed')\ntime.sleep(30)"
    store, _, _, run, workspace = _fixture(tmp_path, {"UserPromptSubmit": event}, script)
    try:
        model = ScriptedModel([ModelResponse(text="Continue with the recorded uncertainty.")])
        RuntimeEngine(store, model, lambda _: None).run(run["id"], threading.Event())
        result = HookRepository(store.database).latest("hook-demo")
        assert result.status == "unknown" and result.error_code == "hook_timeout"
        assert (workspace / "hook-side-effect.txt").read_text() == "changed"
        assert result.workspace_changed
        assert store.read_run(run["id"])["status"] == "succeeded"
        assert "hook_timeout" in str(model.contexts[0])
    finally:
        store.close()

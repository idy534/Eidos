from __future__ import annotations

import json
import sqlite3
import sys
import threading
import time
from unittest.mock import patch

import pytest

from eidos_runtime.db.hook_lifecycle_migration import migrate_hook_lifecycle
from eidos_runtime.db.schema import V38_SCHEMA_SQL
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.domain.hooks import HookBinding, HookConfigurationRequest, HookDefinition, HookResult
from eidos_runtime.extensions.codex_hooks import matches, parse_hooks, parse_output
from eidos_runtime.extensions.hook_sources import HookSourceCatalog
from eidos_runtime.extensions.skills import SkillCatalog
from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel
from eidos_runtime.persistence.collaboration import CollaborationRepository
from eidos_runtime.domain.collaboration import SpawnAgent
from eidos_runtime.persistence.context_management import ContextManagementRepository
from eidos_runtime.persistence.hooks import HookRepository
from eidos_runtime.runtime.engine import RuntimeEngine
from eidos_runtime.runtime.events import RuntimeEvents
from eidos_runtime.runtime.hook_lifecycle import HookLifecycle
from eidos_runtime.runtime.hook_mcp import expand_input, hook_output
from eidos_runtime.runtime.hooks import HookService, compatible_tool_input
from eidos_runtime.runtime.resource_registry import ResourceRegistry
from eidos_runtime.sandbox.sensitive import SensitiveScanner

from test_codex_hooks import _command, _fixture


def wait_for(predicate, timeout=8):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(.02)
    assert predicate()


@pytest.mark.parametrize("event", ["SessionStart", "SessionEnd", "PreCompact", "PostCompact", "PermissionRequest", "SubagentStart", "Interrupt"])
def test_new_events_are_supported_and_reserved_handlers_stay_skipped(event):
    hook = parse_hooks({"hooks": {event: _command()}}, "fixture")[0]
    assert hook.supported
    assert not hook.model_copy(update={"handler_type": "prompt"}).supported
    assert not hook.model_copy(update={"handler_type": "agent"}).supported


def test_agent_alias_and_mcp_templates_preserve_json_types(tmp_path):
    _, aliases, _ = compatible_tool_input("spawn_agent", {"task_name": "read", "message": "Inspect"})
    assert matches(HookDefinition(key="agent", event="PreToolUse", matcher="^Agent$"), aliases)
    assert expand_input({"n": "${tool_input.n}", "nested": ["value ${tool_input.n}", "${tool_input.flag}"]},
        {"tool_input": {"n": 3, "flag": True}}) == {"n": 3, "nested": ["value 3", True]}
    with pytest.raises(ValueError, match="hook_mcp_input_missing"):
        expand_input({"path": "${missing.path}"}, {})
    assert hook_output({"outcome": "success", "data": {"text": '{"decision":"block","reason":"review"}'}})["data"]["stdout"].startswith('{"decision"')
    store, _, _, run, _ = _fixture(tmp_path, {"PostToolUse": [{"matcher": "^Agent$", **_command()[0]}]}, "print('{}')")
    resources = ResourceRegistry()
    try:
        service = HookService(store, run["id"], resources=resources, sensitive=SensitiveScanner(), events=RuntimeEvents(lambda _: None, store=store))
        pending = service.pending_post_tool(ModelToolCall("spawn", "spawn_agent", {"task_name": "read", "message": "Inspect"}),
            "item", {"outcome": "success", "code": "ok", "data": {}})
        assert pending is not None
        with store.database.transaction() as connection:
            HookRepository.record_pending(connection, run["id"], pending)
        service.flush(threading.Event())
        assert HookRepository(store.database).activities(run_id=run["id"]).items[0].event == "PostToolUse"
        resources.ensure_empty()
    finally:
        store.close()


def test_event_specific_output_and_cleanup_timeouts():
    assert parse_output("SessionStart", "session context", "", 0).additional_context == "session context"
    assert parse_output("SubagentStart", '{"continue":false}', "", 0).action == "pass"
    assert parse_output("PreCompact", '{"continue":false}', "", 0).action == "stop"
    assert parse_output("Interrupt", '{"continue":false}', "", 0).action == "pass"
    denied = parse_output("PermissionRequest", '{"hookSpecificOutput":{"decision":{"behavior":"deny","message":"blocked"}}}', "", 0)
    assert denied.permission_decision == "deny" and denied.action == "deny"
    for event in ("SessionEnd", "Interrupt"):
        hook = parse_hooks({"hooks": {event: [{"hooks": [{"type": "command", "command": "true", "async": True, "timeout": 100}]}]}}, "fixture")[0]
        assert hook.timeout_seconds == 3
        assert hook.background == (event == "Interrupt")


def test_session_start_runs_once_per_open_session_and_end_has_no_control(tmp_path):
    script = "import json,sys\ne=json.load(sys.stdin)\nprint(json.dumps({'hookSpecificOutput':{'additionalContext':e.get('source','end')},'continue':True}))"
    store, catalog, _, first, _ = _fixture(tmp_path, {"SessionStart": _command(), "SessionEnd": _command()}, script)
    resources = ResourceRegistry()
    events = RuntimeEvents(lambda _: None, store=store)
    lifecycle = HookLifecycle(store, resources, events, SensitiveScanner())
    try:
        for run in (first,):
            RuntimeEngine(store, ScriptedModel([ModelResponse(text="done")]), lambda _: None, hook_lifecycle=lifecycle).run(run["id"], threading.Event())
        second, _ = store.create_run(first["sessionId"], "again", approval_mode="full_access", extension_snapshot=SkillCatalog(catalog).extension_snapshot())
        RuntimeEngine(store, ScriptedModel([ModelResponse(text="done")]), lambda _: None, hook_lifecycle=lifecycle).run(second["id"], threading.Event())
        rows = store.connection.execute("SELECT event,input_json FROM hook_invocations ORDER BY rowid").fetchall()
        assert [row["event"] for row in rows] == ["SessionStart"]
        assert json.loads(rows[0]["input_json"])["source"] == "startup"
        lifecycle.close()
        assert store.connection.execute("SELECT count(*) FROM hook_invocations WHERE event='SessionEnd'").fetchone()[0] == 1
        assert store.read_run(second["id"])["status"] == "succeeded"
        third, _ = store.create_run(first["sessionId"], "resume", approval_mode="full_access", extension_snapshot=SkillCatalog(catalog).extension_snapshot())
        RuntimeEngine(store, ScriptedModel([ModelResponse(text="done")]), lambda _: None).run(third["id"], threading.Event())
        row = store.connection.execute("SELECT input_json FROM hook_invocations WHERE event='SessionStart' ORDER BY rowid DESC LIMIT 1").fetchone()
        assert json.loads(row[0])["source"] == "resume"
    finally:
        lifecycle.close()
        store.close()


def test_subagent_start_uses_parent_session_and_context(tmp_path):
    script = "import json,sys\ne=json.load(sys.stdin)\nassert e['hook_event_name']=='SubagentStart'\nassert e['agent_type']=='explorer'\nprint(json.dumps({'hookSpecificOutput':{'additionalContext':'child start context'}}))"
    store, _, _, parent, _ = _fixture(tmp_path, {"SubagentStart": [{"matcher": "explorer", **_command()[0]}]}, script)
    try:
        item = store.create_tool_item(parent["id"], 1, 0, "spawn", "spawn_agent", "{}")
        child = CollaborationRepository(store.database).spawn(parent["id"], item["id"], SpawnAgent(task_name="inspect", message="Read", role="explorer"))
        assert store.claim_next_run_committed().value["id"] == child.run_id
        model = ScriptedModel([ModelResponse(text="done")])
        RuntimeEngine(store, model, lambda _: None).run(child.run_id, threading.Event())
        activity = HookRepository(store.database).activities(run_id=child.run_id).items
        assert activity[0].status == "completed"
        row = store.connection.execute("SELECT input_json FROM hook_invocations WHERE run_id=?", (child.run_id,)).fetchone()
        assert json.loads(row[0])["session_id"] == parent["sessionId"]
        assert "child start context" in str(model.contexts)
    finally:
        store.close()


def test_manual_compaction_emits_pre_post_and_compact_session_start(tmp_path):
    script = "import json,sys\ne=json.load(sys.stdin)\nprint(json.dumps({'hookSpecificOutput':{'additionalContext':'after compaction'}}))"
    store, catalog, _, first, _ = _fixture(tmp_path, {"PreCompact": _command(), "PostCompact": _command(), "SessionStart": _command()}, script)
    try:
        store.fail_run(first["id"], "fixture")
        run, _ = store.create_run(first["sessionId"], "compact", approval_mode="full_access", extension_snapshot=SkillCatalog(catalog).extension_snapshot())
        ContextManagementRepository(store.database).request(run["id"])
        class Model:
            profile_snapshot = store.read_model_profile(run["id"])
            def complete(self, context, cancel, on_text_delta, **kwargs):
                if kwargs.get("required_tool") == "submit_context_summary":
                    return ModelResponse(tool_calls=(ModelToolCall("summary", "submit_context_summary", {"notes": []}),))
                return ModelResponse(text="done")
        RuntimeEngine(store, Model(), lambda _: None).run(run["id"], threading.Event())
        rows = store.connection.execute("SELECT event,input_json FROM hook_invocations WHERE run_id=? ORDER BY rowid", (run["id"],)).fetchall()
        assert [row["event"] for row in rows] == ["SessionStart", "PreCompact", "PostCompact", "SessionStart"]
        assert json.loads(rows[-1]["input_json"])["source"] == "compact"
    finally:
        store.close()


def test_interrupt_is_advisory_and_cancellation_wins(tmp_path):
    script = "import json,sys,os,pathlib,time\ne=json.load(sys.stdin)\np=pathlib.Path(os.environ['PLUGIN_DATA'])\nif e['hook_event_name']=='UserPromptSubmit':\n (p/'started').write_text('yes')\n time.sleep(30)\nelse:\n (p/'interrupted').write_text('yes')\nprint('{}')"
    store, catalog, _, run, _ = _fixture(tmp_path, {"UserPromptSubmit": _command(), "Interrupt": _command()}, script)
    cancel = threading.Event()
    worker = threading.Thread(target=RuntimeEngine(store, ScriptedModel([ModelResponse(text="never")]), lambda _: None).run, args=(run["id"], cancel))
    try:
        worker.start()
        data = catalog.root / "hook-demo/hook-data"
        wait_for(lambda: (data / "started").exists())
        cancel.set()
        worker.join(8)
        assert not worker.is_alive()
        assert (data / "interrupted").exists()
        assert store.read_run(run["id"])["status"] == "canceled"
    finally:
        cancel.set()
        worker.join(8)
        store.close()


def test_user_and_project_sources_merge_and_require_exact_review(tmp_path):
    store, _, _, run, workspace = _fixture(tmp_path, {}, "")
    try:
        user = store.data_directory / "hooks.json"
        project = workspace / ".eidos"
        project.mkdir()
        user.write_text(json.dumps({"hooks": {"Stop": _command()}}))
        (project / "hooks.json").write_text(json.dumps({"hooks": {"PreToolUse": _command()}}))
        (project / "config.toml").write_text('[[hooks.Stop]]\n[[hooks.Stop.hooks]]\ntype="command"\ncommand="true"\n')
        catalog = HookSourceCatalog(store)
        sources = catalog.discover(workspace)
        assert [source.scope for source in sources] == ["user", "project"]
        assert len(sources[1].definitions) == 2
        assert not catalog.bindings(workspace)
        for source in sources:
            catalog.repository.set_source_state(source.id, source.content_hash, trusted=True)
        bindings = catalog.bindings(workspace)
        assert len(bindings) == 3
        user.write_text(json.dumps({"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "false"}]}]}}))
        assert len(catalog.bindings(workspace)) == 2
        with pytest.raises(ValueError, match="hook_source_changed"):
            catalog.verify(bindings[0])
        assert store.read_run(run["id"])["status"] == "running"
    finally:
        store.close()


def test_invalid_and_symlink_sources_are_not_executable(tmp_path):
    store, _, _, _, workspace = _fixture(tmp_path, {}, "")
    try:
        source = store.data_directory / "hooks.json"
        source.symlink_to(workspace / "outside")
        catalog = HookSourceCatalog(store)
        assert catalog.discover()[0].error_code == "hook_source_invalid"
        assert not catalog.bindings()
        source.unlink()
        source.write_text('{"hooks": {"NotAnEvent": []}}')
        assert catalog.discover()[0].error_code == "hook_source_invalid"
        assert not catalog.bindings()
    finally:
        store.close()


@pytest.mark.platform
@pytest.mark.skipif(sys.platform != "darwin", reason="Seatbelt requires macOS")
def test_transcript_history_and_progress_are_bounded_and_persisted(tmp_path):
    script = "import json,sys,pathlib\ne=json.load(sys.stdin)\np=pathlib.Path(e['transcript_path'])\nassert p.is_file()\nassert 'Read hello.txt' in p.read_text()\nprint(json.dumps({'systemMessage':'Review warning'}))"
    store, _, _, run, _ = _fixture(tmp_path, {"UserPromptSubmit": _command()}, script, mode="manual")
    try:
        notifications = []
        RuntimeEngine(store, ScriptedModel([ModelResponse(text="done")]), notifications.append).run(run["id"], threading.Event())
        activity = HookRepository(store.database).activities(run_id=run["id"]).items[0]
        assert activity.status == "completed", activity
        assert activity.result.system_message == "Review warning"
        assert store.read_session_snapshot(run["sessionId"])["runs"][0]["hookActivity"]
        assert any(value["method"] == "run/updated" and value["params"]["run"].get("hookActivity") for value in notifications)
        directory = store.data_directory
        store.close()
        store = SessionStore(directory)
        store.initialize()
        assert HookRepository(store.database).activities(run_id=run["id"]).items[0].result.system_message == "Review warning"
    finally:
        store.close()


def test_history_pages_do_not_cross_requested_source_or_session(tmp_path):
    store, _, plugin, run, _ = _fixture(tmp_path, {"Stop": _command()}, "")
    try:
        repository = HookRepository(store.database)
        binding = HookBinding(plugin_id=plugin["id"], plugin_hash=plugin["contentHash"], definition=HookDefinition(key="page", event="Stop", command="true"))
        for index in range(23):
            identifier, _, _ = repository.reserve(run["id"], binding, str(index), "{}")
            repository.complete(identifier, HookResult(system_message=str(index), additional_context="c" * 50000,
                updated_input={"private": "x" * 16384}, workspace_changes=("path" * 256,)))
        first = repository.activities(source_id=plugin["id"], session_id=run["sessionId"])
        assert len(first.items) == 20 and first.next_cursor
        assert first.items[0].result.additional_context == ""
        assert first.items[0].result.updated_input is None and first.items[0].result.workspace_changes == ()
        assert len(first.model_dump_json()) < 30000
        assert repository.latest(plugin["id"]).additional_context == "c" * 50000
        second = repository.activities(source_id=plugin["id"], session_id=run["sessionId"], before=first.next_cursor)
        assert len(second.items) == 3 and second.next_cursor is None
        assert not repository.activities(source_id="other").items
        assert not repository.activities(session_id="other").items
    finally:
        store.close()


def test_v38_upgrade_and_failed_migration_rollback(tmp_path):
    database = tmp_path / "before.sqlite"
    connection = sqlite3.connect(database)
    connection.executescript(V38_SCHEMA_SQL + "PRAGMA user_version=38;")
    with patch("eidos_runtime.db.hook_lifecycle_migration.HOOK_INVOCATION_COLUMNS", {"binding_json": "TEXT", "broken": "NOT SQL ?"}):
        with pytest.raises(sqlite3.Error):
            migrate_hook_lifecycle(connection)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 38
    assert "binding_json" not in {row[1] for row in connection.execute("PRAGMA table_info(hook_invocations)")}
    migrate_hook_lifecycle(connection)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 39
    assert connection.execute("SELECT count(*) FROM hook_sources").fetchone()[0] == 0
    connection.close()


@pytest.mark.parametrize("value", [{"action": "trust"}, {"action": "read", "enabled": False}, {"action": "enable", "sourceId": "a", "contentHash": "a"*64, "trusted": True}])
def test_source_mutations_have_strict_action_contracts(value):
    with pytest.raises(ValueError):
        HookConfigurationRequest.model_validate(value)

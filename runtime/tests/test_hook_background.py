from __future__ import annotations

from pathlib import Path
import threading

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.domain.hooks import HookBinding, HookDefinition
from eidos_runtime.domain.collaboration import SpawnAgent
from eidos_runtime.extensions.skills import SkillCatalog
from eidos_runtime.model.client import ModelResponse, ScriptedModel
from eidos_runtime.persistence.hooks import HookRepository
from eidos_runtime.persistence.collaboration import CollaborationRepository
from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel
from eidos_runtime.runtime.engine import RuntimeEngine
from eidos_runtime.runtime.events import RuntimeEvents
from eidos_runtime.runtime.hook_lifecycle import HookLifecycle
from eidos_runtime.runtime.hooks import HookService
from eidos_runtime.runtime.resource_registry import ResourceRegistry, RuntimeResourceKind
from eidos_runtime.sandbox.sensitive import SensitiveScanner

from test_codex_hooks import _command, _fixture
from test_hook_lifecycle import wait_for


def background_fixture(tmp_path: Path, script: str, count=1):
    handlers = []
    for index in range(count):
        handler = {**_command()[0]["hooks"][0], "async": True}
        handler["command"] += f" {index}"
        handlers.append(handler)
    store, catalog, plugin, run, workspace = _fixture(tmp_path, {"UserPromptSubmit": [{"hooks": handlers}]}, script)
    resources = ResourceRegistry()
    kernel = RuntimeAsyncKernel(resource_registry=resources)
    kernel.start()
    events = RuntimeEvents(lambda _: None, store=store)
    lifecycle = HookLifecycle(store, resources, events, SensitiveScanner(), kernel)
    lifecycle.enter_run(run["id"])
    service = HookService(store, run["id"], resources=resources, events=events, sensitive=SensitiveScanner(),
        async_kernel=kernel, background_submit=lifecycle.submit)
    return store, catalog, plugin, run, workspace, resources, kernel, lifecycle, service


def test_background_cannot_block_rewrite_or_restart_a_finished_turn(tmp_path):
    script = "import os,json,pathlib,time\np=pathlib.Path(os.environ['PLUGIN_DATA'])\n(p/'started').write_text('ready')\nwhile not (p/'release').exists(): time.sleep(.01)\nprint(json.dumps({'decision':'block','reason':'must be ignored','hookSpecificOutput':{'additionalContext':'background context'}}))"
    store, catalog, _, run, _, resources, kernel, lifecycle, _service = background_fixture(tmp_path, script)
    try:
        model = ScriptedModel([ModelResponse(text="done")])
        RuntimeEngine(store, model, lambda _: None, async_kernel=kernel, resource_registry=resources, hook_lifecycle=lifecycle).run(run["id"], threading.Event())
        assert store.read_run(run["id"])["status"] == "succeeded"
        data = catalog.root / "hook-demo/hook-data"
        wait_for(lambda: (data / "started").exists())
        (data / "release").write_text("go")
        repository = HookRepository(store.database)
        wait_for(lambda: repository.activities(run_id=run["id"]).items[0].status == "completed")
        activity = repository.activities(run_id=run["id"]).items[0]
        assert activity.background and activity.result.action == "pass"
        assert activity.result.message == "" and activity.result.updated_input is None
        assert len(model.contexts) == 1 and store.read_run(run["id"])["status"] == "succeeded"
        next_run, _ = store.create_run(run["sessionId"], "next", approval_mode="full_access", extension_snapshot=SkillCatalog(catalog).extension_snapshot())
        assert "background context" in str(repository.context(next_run["id"]))
        store.cancel_run(next_run["id"])
        later, _ = store.create_run(run["sessionId"], "later", approval_mode="full_access", extension_snapshot=SkillCatalog(catalog).extension_snapshot())
        assert "background context" not in str(repository.context(later["id"]))
    finally:
        lifecycle.close()
        kernel.close()
        resources.ensure_empty()
        store.close()


def test_background_has_eight_slots_per_session_and_keeps_queued_invocations(tmp_path):
    script = "import os,sys,pathlib,time\np=pathlib.Path(os.environ['PLUGIN_DATA'])\n(p/('started-'+sys.argv[1])).write_text('ready')\nwhile not (p/'release').exists(): time.sleep(.01)\nprint('{}')"
    store, catalog, _, run, _, resources, kernel, lifecycle, service = background_fixture(tmp_path, script, count=12)
    data = catalog.root / "hook-demo/hook-data"
    try:
        service.dispatch("UserPromptSubmit", "batch", threading.Event(), fields={"prompt": "check"})
        wait_for(lambda: len(list(data.glob("started-*"))) >= 8)
        assert len(list(data.glob("started-*"))) == 8
        assert store.connection.execute("SELECT count(*) FROM hook_invocations").fetchone()[0] == 12
        (data / "release").write_text("go")
        wait_for(lambda: store.connection.execute("SELECT count(*) FROM hook_invocations WHERE result_json IS NOT NULL").fetchone()[0] == 12)
        assert len(list(data.glob("started-*"))) == 12
    finally:
        lifecycle.close()
        kernel.close()
        resources.ensure_empty()
        store.close()


def test_session_end_cancels_background_processes_and_discards_pending_output(tmp_path):
    script = "import os,pathlib,time\np=pathlib.Path(os.environ['PLUGIN_DATA'])\n(p/'started').write_text('yes')\ntime.sleep(30)\nprint('{}')"
    store, catalog, _, run, _, resources, kernel, lifecycle, service = background_fixture(tmp_path, script)
    try:
        service.dispatch("UserPromptSubmit", "cancel", threading.Event())
        wait_for(lambda: (catalog.root / "hook-demo/hook-data/started").exists())
        lifecycle.end_session(run["sessionId"])
        activity = HookRepository(store.database).activities(run_id=run["id"]).items[0]
        assert activity.status == "unknown"
        assert store.connection.execute("SELECT discarded_at FROM hook_invocations").fetchone()[0] is not None
        assert HookRepository(store.database).context(run["id"]) == ()
    finally:
        lifecycle.close()
        kernel.close()
        resources.ensure_empty()
        store.close()


def test_restart_marks_background_unknown_and_never_replays_it(tmp_path):
    store, catalog, plugin, run, _ = _fixture(tmp_path, {"UserPromptSubmit": _command()}, "raise AssertionError('must not execute')")
    try:
        definition = HookDefinition(key="abandoned", event="UserPromptSubmit", command="false", background=True)
        binding = HookBinding(plugin_id=plugin["id"], plugin_hash=plugin["contentHash"], definition=definition)
        repository = HookRepository(store.database)
        identifier, _, _ = repository.reserve(run["id"], binding, "event", "{}")
        repository.started(identifier, "{}")
        directory = store.data_directory
        store.close()
        store = SessionStore(directory)
        store.initialize()
        repository = HookRepository(store.database)
        assert repository.activities(run_id=run["id"]).items[0].status == "unknown"
        _, saved, created = repository.reserve(run["id"], binding, "event", "{}")
        assert not created and saved.status == "unknown"
        assert store.connection.execute("SELECT count(*) FROM hook_invocations").fetchone()[0] == 1
        assert not (catalog.root / "hook-demo/hook-data").exists()
    finally:
        store.close()


def test_parent_session_end_cancels_child_hook_resources_and_removes_transcript(tmp_path):
    script = "import os,pathlib,time\npathlib.Path(os.environ['PLUGIN_DATA'],'started').write_text('yes')\ntime.sleep(30)"
    events = {"SubagentStart": [{"hooks": [{**_command()[0]["hooks"][0], "async": True}]}]}
    store, catalog, _, parent, _ = _fixture(tmp_path, events, script)
    resources = ResourceRegistry()
    kernel = RuntimeAsyncKernel(resource_registry=resources)
    kernel.start()
    lifecycle = HookLifecycle(store, resources, RuntimeEvents(lambda _: None, store=store), SensitiveScanner(), kernel)
    try:
        lifecycle.enter_run(parent["id"])
        item = store.create_tool_item(parent["id"], 1, 0, "spawn", "spawn_agent", "{}")
        child = CollaborationRepository(store.database).spawn(parent["id"], item["id"], SpawnAgent(task_name="inspect", message="Read", role="explorer"))
        assert store.claim_next_run()["id"] == child.run_id
        service = HookService(store, child.run_id, resources=resources, events=lifecycle.events, sensitive=SensitiveScanner(),
            async_kernel=kernel, background_submit=lifecycle.submit)
        service.start(lifecycle.enter_run(child.run_id), lifecycle.key, threading.Event())
        wait_for(lambda: (catalog.root / "hook-demo/hook-data/started").exists())
        transcript = store.data_directory / "hook-transcripts" / child.session_id / "transcript.jsonl"
        assert transcript.exists()
        lifecycle.end_session(parent["sessionId"])
        assert HookRepository(store.database).activities(run_id=child.run_id).items[0].status == "unknown"
        assert not transcript.exists()
        assert all(value.kind is RuntimeResourceKind.ASYNC_KERNEL for value in resources.active_resources())
    finally:
        lifecycle.close()
        kernel.close()
        store.close()


def test_background_feedback_outside_context_budget_stays_pending_for_next_run(tmp_path):
    store, _, plugin, origin, _ = _fixture(tmp_path, {"UserPromptSubmit": _command()}, "print('{}')")
    try:
        definition = HookDefinition(key="large", event="UserPromptSubmit", command="true", background=True)
        binding = HookBinding(plugin_id=plugin["id"], plugin_hash=plugin["contentHash"], definition=definition)
        repository = HookRepository(store.database)
        for index in range(12):
            identifier, _, _ = repository.reserve(origin["id"], binding, str(index), "{}")
            from eidos_runtime.domain.hooks import HookResult
            repository.complete(identifier, HookResult(additional_context=f"message-{index}-" + "x" * 10000, output_call_id="hook:" + identifier))
        store.cancel_run(origin["id"])
        first, _ = store.create_run(origin["sessionId"], "next")
        assert "Truncated Hook feedback" in str(repository.context(first["id"]))
        first_count = store.connection.execute("SELECT count(*) FROM hook_invocations WHERE delivered_run_id=?", (first["id"],)).fetchone()[0]
        assert 0 < first_count < 12
        store.cancel_run(first["id"])
        second, _ = store.create_run(origin["sessionId"], "later")
        assert repository.context(second["id"])
        assert store.connection.execute("SELECT count(*) FROM hook_invocations WHERE delivered_run_id=?", (first["id"],)).fetchone()[0] == first_count
        assert store.connection.execute("SELECT count(*) FROM hook_invocations WHERE delivered_run_id=?", (second["id"],)).fetchone()[0] > 0
    finally:
        store.close()

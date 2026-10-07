from __future__ import annotations

import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace

import pytest

from eidos_runtime.application.collaboration import CollaborationApplication
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.db.errors import RunCompletionDeferred
from eidos_runtime.domain.collaboration import AgentSuspended, CollaborationRejected, WaitAgents
from eidos_runtime.model.client import ModelToolCall
from eidos_runtime.runtime.contracts import LoopAction, LoopDecision, RuntimeCancelled, SampleBoundaryAction
from eidos_runtime.runtime.engine import RuntimeEngine
from eidos_runtime.runtime.events import RuntimeEvents
from eidos_runtime.runtime.run_resources import RunResources
from eidos_runtime.runtime.state_machine import RuntimePhaseTracker
from eidos_runtime.runtime.tool_execution import PreparedToolExecution, VerifiedToolExecutionResult
from eidos_runtime.runtime.tool_runtime import FileChangeToolHandler, ToolCallRuntime
from eidos_runtime.sandbox.permissions import BasePermissionProfile
from eidos_runtime.sandbox.sensitive import default_scanner
from eidos_runtime.sandbox.shell import ShellLaunchSpec


@pytest.fixture
def execution(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    session = store.create_session(str(workspace))
    run, _ = store.create_run(session["id"], "work independently", approval_mode="full_access")
    app = CollaborationApplication(store, lambda: None, store.request_cancel_committed, lambda: None)
    try:
        with RunResources(store, run["id"], run["extensionSnapshot"], collaboration=app) as resources:
            runtime = ToolCallRuntime(
                store, resources.dispatcher, None, RuntimeEvents(lambda _event: None),
                default_scanner(), RuntimePhaseTracker(), shell_available=True,
                base_permissions=BasePermissionProfile.model_validate_json(
                    store.read_run_resolution_snapshot(run["id"]).permission_profile_json),
                resource_registry=resources.resources,
                shell_process_manager=resources.shell_process_manager,
                collaboration=app,
            )
            yield SimpleNamespace(store=store, run=run, app=app, resources=resources,
                                  runtime=runtime, workspace=workspace)
    finally:
        store.close()


def _step(e):
    e.store.complete_current_step(e.run["id"], "completed")
    snapshot = e.resources.dispatcher.snapshot()
    return SimpleNamespace(run_id=e.run["id"], step_index=e.store.increment_model_step(e.run["id"], tool_snapshot=snapshot.as_dict()),
                           tool_snapshot=snapshot)


def _results(e):
    return [(item["toolCall"]["toolName"], json.loads(item["toolCall"].get("resultJson") or "{}").get("code"),
             json.loads(item["toolCall"].get("resultJson") or "{}").get("summary"))
            for item in e.store.read_session_snapshot(e.run["sessionId"])["items"] if item.get("toolCall")]


def _execute(e, calls, cancel):
    step = _step(e)
    batch = e.runtime.validate(step, SimpleNamespace(text="", tool_calls=calls))
    assert batch.status == "ready"
    return e.runtime.execute(step, batch.tool_calls, cancel)


def _uncertain(e, name="apply_patch", conditions=None, *, provenance=None):
    index = _step(e).step_index
    item = e.store.create_tool_item(e.run["id"], index, 0, f"origin-{index}", name, "{}",
        provenance=provenance or e.resources.dispatcher.provenance(name))
    e.store.begin_durable_intent(item["id"], preconditions=conditions or {"paths": ["changed.txt"]}, approval_required=False)
    e.store.complete_tool_item(item["id"], json.dumps({
        "outcome": "error", "code": "outcome_unknown", "data": {},
        "sideEffectsMayExist": True, "reconciliationRequired": True,
    }), item_status="failed", tool_status="failed")
    return item


def _start_shell(e):
    result = e.resources.shell_process_manager.start(ShellLaunchSpec(
        argv=(sys.executable, "-c", "import time; time.sleep(30)"), cwd=e.workspace,
        environment={"PATH": "/usr/bin:/bin"}, sandboxed=False,
    ), yield_time_ms=0)
    assert result["data"]["executionStatus"] == "running"
    return result["data"]["sessionId"]


def _spawn(e, count=1):
    calls = tuple(ModelToolCall(f"spawn-{index}", "spawn_agent", {
        "taskName": f"task-{index}", "message": "Inspect assigned evidence", "role": "explorer",
    }) for index in range(count))
    result = _execute(e, calls, threading.Event())
    assert not result.error_fingerprints
    return e.app.repository.state(e.run["id"]).agents


def test_two_shells_and_batched_collaboration_do_not_block_each_other(execution):
    e = execution
    first = _start_shell(e)
    second = _start_shell(e)
    agents = _spawn(e, 2)
    assert len(agents) == 2
    assert e.resources.shell_process_manager.is_running(first)
    assert e.resources.shell_process_manager.is_running(second)
    # Launch windows are not retained for process lifetime.
    assert e.runtime.controller.run_exclusive_side_effect(threading.Event(), lambda: "committed") == "committed"


def test_cleanup_distinguishes_a_natural_exit_from_stopping_live_work(execution):
    e = execution
    manager = e.resources.shell_process_manager
    shell = manager.start(ShellLaunchSpec(argv=(sys.executable, '-c', 'print("done")'), cwd=e.workspace,
        environment={'PATH': '/usr/bin:/bin'}, sandboxed=False), yield_time_ms=0)
    assert manager._session(shell['data']['sessionId']).done.wait(3)
    assert manager.cleanup(close=False) is False
    _start_shell(e)
    assert manager.cleanup(close=False) is True


@pytest.mark.skipif(sys.platform != "darwin", reason="Secure file commit helper requires macOS descriptor identity")
def test_live_shell_allows_actual_file_commit_and_second_shell(execution):
    e = execution
    result = _execute(e, (
        ModelToolCall("long-shell", "run_shell", {"command": "sleep 30", "yieldTimeMs": 250}),
    ), threading.Event())
    assert not result.error_fingerprints, _results(e)
    assert e.resources.shell_process_manager.has_running()
    result = _execute(e, (
        ModelToolCall("patch", "apply_patch", {"patch": "*** Begin Patch\n*** Add File: independent.txt\n+written\n*** End Patch"}),
        ModelToolCall("short-shell", "run_shell", {"command": "printf ready", "yieldTimeMs": 250}),
    ), threading.Event())
    assert not result.error_fingerprints, _results(e)
    assert (e.workspace / "independent.txt").read_text() == "written\n"
    assert e.resources.shell_process_manager.has_running()


def test_wait_tool_timeout_does_not_suspend_or_kill_its_shell(execution):
    e = execution
    _spawn(e)
    session = _start_shell(e)
    result = _execute(e, (
        ModelToolCall("wait", "wait_agents", {"timeoutMs": 1000}),
    ), threading.Event())
    assert not result.error_fingerprints, _results(e)
    assert e.store.read_run(e.run["id"])["status"] == "running"
    assert e.resources.shell_process_manager.is_running(session)
    assert e.app.repository.wait_record(e.run["id"]) is None


def test_mixed_wait_continues_after_timeout_without_replaying_prior_calls(execution):
    e = execution
    _spawn(e)
    result = _execute(e, (
        ModelToolCall('wait-in-batch', 'wait_agents', {'timeoutMs': 1000}),
        ModelToolCall('after-wait', 'list_agents', {}),
    ), threading.Event())
    assert not result.error_fingerprints
    assert e.store.read_run(e.run['id'])['status'] == 'running'
    results = _results(e)
    assert [name for name, _, _ in results] == ['spawn_agent', 'wait_agents', 'list_agents']
    assert e.app.repository.wait_record(e.run['id']) is None


def test_unknown_shell_allows_readonly_exploration_but_blocks_new_worker(execution):
    e = execution
    _uncertain(e, 'run_shell')
    result = _execute(e, (
        ModelToolCall('readonly-child', 'spawn_agent', {'taskName': 'explore', 'message': 'Inspect evidence', 'role': 'explorer'}),
        ModelToolCall('worker-child', 'spawn_agent', {'taskName': 'execute', 'message': 'Modify files', 'role': 'worker'}),
    ), threading.Event())
    assert result.error_fingerprints
    children = e.app.repository.state(e.run['id']).agents
    assert len(children) == 1 and children[0].role == 'explorer'
    assert e.store.side_effects_blocked(e.run['id'])


def test_deferred_completion_keeps_shell_manager_available_for_continuation(execution, monkeypatch):
    e = execution
    step = _step(e)
    item = e.store.create_assistant_item(e.run["id"], step.step_index)

    def defer(*_args, **_kwargs):
        raise RunCompletionDeferred("pending_input")

    monkeypatch.setattr(e.store, "complete_assistant_and_run_committed", defer)
    engine = RuntimeEngine(e.store, None, lambda _event: None)
    action = engine._settle_sample_boundary(
        e.run["id"], LoopDecision(action=LoopAction.COMPLETE),
        SimpleNamespace(assistant_item=item), SimpleNamespace(status="no_tools"),
        None, e.resources, None, threading.Event(),
    )
    assert action is SampleBoundaryAction.REBUILD_CONTEXT
    assert e.store.read_run(e.run["id"])["status"] == "running"
    assert _start_shell(e)


@pytest.mark.parametrize("name", ["spawn_agent", "send_message", "followup_task", "list_agents", "stop_agent"])
def test_control_tools_accept_mixed_batches(execution, name):
    e = execution
    call = ModelToolCall("first", name, {})
    other = ModelToolCall("second", "list_agents", {})
    # Batch policy is independent of argument validation at execution time.
    result = e.runtime.validate(_step(e), SimpleNamespace(text="", tool_calls=(call, other)))
    assert result.status == "ready"
    result = e.runtime.validate(_step(e), SimpleNamespace(text="", tool_calls=(ModelToolCall("wait", "wait_agents", {}), other)))
    assert result.status == "ready"


def test_unknown_shell_does_not_block_messages_or_stopping_owned_children(execution):
    e = execution
    child = _spawn(e)[0]
    _uncertain(e, "run_shell")
    result = _execute(e, (
        ModelToolCall("message", "send_message", {"agentId": child.id, "message": "Review existing evidence"}),
        ModelToolCall("stop", "stop_agent", {"agentId": child.id}),
    ), threading.Event())
    assert not result.error_fingerprints
    assert e.store.side_effects_blocked(e.run["id"])
    blocked = _execute(e, (
        ModelToolCall("new-shell", "run_shell", {"command": "echo must-not-run"}),
    ), threading.Event())
    assert blocked.error_fingerprints
    assert not e.resources.shell_process_manager.has_running()


def test_wait_keeps_live_shell_owned_and_can_be_canceled(execution):
    e = execution
    _spawn(e)
    session = _start_shell(e)
    cancel = threading.Event()
    observed = threading.Event()
    inspect = e.app.repository.inspect_wait

    def inspect_wait(*args):
        result = inspect(*args)
        observed.set()
        return result

    e.app.repository.inspect_wait = inspect_wait
    errors = []

    def wait():
        try:
            e.app.wait(e.run["id"], None, WaitAgents(), cancel=cancel, keep_worker=True)
        except RuntimeCancelled:
            errors.append("canceled")

    thread = threading.Thread(target=wait)
    thread.start()
    try:
        assert observed.wait(2)
        assert e.store.read_run(e.run["id"])["status"] == "running"
        assert e.app.repository.wait_record(e.run["id"]) is None
        assert e.resources.shell_process_manager.is_running(session)
        # An independent Session can still claim its Run while this owner waits.
        session2 = e.store.create_session(str(e.workspace))
        other, _ = e.store.enqueue_run(session2["id"], "independent")
        claimed = {e.store.claim_next_run()["id"], e.store.claim_next_run()["id"]}
        assert other["id"] in claimed
    finally:
        cancel.set()
        thread.join(2)
    assert not thread.is_alive()
    assert errors == ["canceled"]


def test_resource_owning_wait_returns_completed_children_and_checks_ownership(execution):
    e = execution
    child = _spawn(e)[0]
    assert e.store.claim_next_run()["id"] == child.run_id
    e.store.fail_run(child.run_id, "fixture_finished")
    result = e.app.wait(e.run["id"], None, WaitAgents(), cancel=threading.Event(), keep_worker=True)
    assert result.agents[0].status == "failed"
    with pytest.raises(CollaborationRejected, match="agent_not_owned_by_run"):
        e.app.wait(e.run["id"], None, WaitAgents(agent_ids=["other-parent"]), cancel=threading.Event(), keep_worker=True)


def test_no_shell_wait_still_releases_worker_via_durable_suspension(execution):
    e = execution
    _spawn(e)
    with pytest.raises(AgentSuspended):
        e.app.wait(e.run["id"], None, WaitAgents())
    assert e.store.read_run(e.run["id"])["status"] == "waiting_agents"


def test_workspace_recovery_is_automatic_and_needs_no_model_read(execution):
    e = execution
    _uncertain(e)
    calls = []
    e.runtime.workspace_refresh = lambda _cancel: calls.append("refresh") or SimpleNamespace(complete=True)
    # A control-only batch recovers the verifiable workspace intent up front.
    result = _execute(e, (ModelToolCall("list", "list_agents", {}),), threading.Event())
    assert not result.error_fingerprints
    assert calls == ["refresh"]
    assert not e.store.side_effects_blocked(e.run["id"])


def test_incomplete_recovery_does_not_repeat_full_scan_within_unchanged_step(execution):
    e = execution
    _uncertain(e)
    calls = []
    e.runtime.workspace_refresh = lambda _cancel: calls.append("refresh") or SimpleNamespace(complete=False)
    _execute(e, tuple(ModelToolCall(f"list-{i}", "list_agents", {}) for i in range(3)), threading.Event())
    assert calls == ["refresh"]
    assert e.store.side_effects_blocked(e.run["id"])


@pytest.mark.parametrize("name,conditions", [
    ("run_shell", {"paths": ["changed.txt"]}),
    ("apply_patch", {"mutationScope": "external_file", "path": "/outside/file"}),
    ("apply_patch", {"authorization": "workspace"}),
    ("apply_patch", {"paths": ["../outside"]}),
])
def test_unknown_scope_is_not_treated_as_a_known_file(execution, name, conditions):
    e = execution
    _uncertain(e, name, conditions)
    assert e.store.reconciliation_file_paths(e.run["id"]) is None


@pytest.mark.parametrize("targets,blocked", [
    (["unrelated.txt"], False), (["changed.txt"], True),
    (["changed.txt/child"], True), (["."], True),
    (["unrelated.txt", "changed.txt"], True),
])
def test_file_reconciliation_only_blocks_conflicting_prepared_targets(execution, targets, blocked):
    e = execution
    _uncertain(e)
    calls = []
    executor = SimpleNamespace(workspace=SimpleNamespace(path=e.workspace), _external_writers={},
                               write_permissions=SimpleNamespace(full_access=True))
    runtime = SimpleNamespace(spec=SimpleNamespace(name="apply_patch"), implementation=SimpleNamespace(executor=executor))
    handler = FileChangeToolHandler(SimpleNamespace(store=e.store, execute_workspace_side_effect=lambda **kw: VerifiedToolExecutionResult(result=kw["execute"]())))
    result = handler._execute_file_effect(e.run["id"], {}, PreparedToolExecution(
        approval_description={}, intent_preconditions={"paths": targets}, transition_reason="test",
    ), threading.Event(), runtime, lambda: calls.append("write") or {"outcome": "success"})
    assert (result.result.get("code") == "reconciliation_required") is blocked
    assert calls == ([] if blocked else ["write"])
    assert e.store.side_effects_blocked(e.run["id"])


def test_file_barrier_is_rechecked_inside_commit_window(execution):
    e = execution
    calls = []
    executor = SimpleNamespace(workspace=SimpleNamespace(path=e.workspace), _external_writers={},
                               write_permissions=SimpleNamespace(full_access=True))
    runtime = SimpleNamespace(spec=SimpleNamespace(name="apply_patch"), implementation=SimpleNamespace(executor=executor))

    def commit(**kw):
        _uncertain(e, "run_shell")
        return VerifiedToolExecutionResult(result=kw["execute"]())

    handler = FileChangeToolHandler(SimpleNamespace(store=e.store, execute_workspace_side_effect=commit))
    result = handler._execute_file_effect(e.run["id"], {}, PreparedToolExecution(
        approval_description={}, intent_preconditions={"paths": ["unrelated.txt"]}, transition_reason="test",
    ), threading.Event(), runtime, lambda: calls.append("write") or {"outcome": "success"})
    assert result.result["code"] == "reconciliation_required"
    assert calls == []

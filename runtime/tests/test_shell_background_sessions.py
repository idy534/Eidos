from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import threading
import time

import pytest

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel
from eidos_runtime.runtime.loop import RuntimeLoop
from eidos_runtime.runtime.resource_registry import ResourceRegistry
from eidos_runtime.runtime.shell_process_manager import ShellProcessManager
from eidos_runtime.sandbox.shell import ShellLaunchSpec


pytestmark = pytest.mark.integration


@pytest.fixture
def resources():
    registry = ResourceRegistry()
    yield registry
    registry.ensure_empty()


@pytest.fixture
def shell_manager(resources):
    manager = ShellProcessManager(resources, owner_run_id="background-test")
    try:
        yield manager
    finally:
        manager.cleanup()


def launch(tmp_path: Path, command: str) -> ShellLaunchSpec:
    return ShellLaunchSpec(
        argv=("/bin/sh", "-c", command), cwd=tmp_path,
        environment={"PATH": "/usr/bin:/bin"}, sandboxed=False, tty=False,
    )


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def background_pid(tmp_path: Path, command: str = "sleep 30") -> tuple[str, Path]:
    pid_file = tmp_path / "background.pid"
    return f"{command} & echo $! > {shlex.quote(str(pid_file))}", pid_file


def test_command_returns_promptly_while_its_background_process_keeps_running(tmp_path, shell_manager):
    command, pid_file = background_pid(tmp_path)
    started = time.monotonic()
    result = shell_manager.start(launch(tmp_path, command), yield_time_ms=10_000)
    assert time.monotonic() - started < 5
    data = result["data"]
    assert result["code"] == "shell_running"
    assert data["executionStatus"] == "running"
    assert data["backgroundProcesses"] is True
    assert data["leaderExitCode"] == 0
    assert data["exitCode"] is None
    assert "background processes keep running" in result["summary"]
    pid = int(pid_file.read_text())
    assert alive(pid)

    # The exit is reported once; a later wait waits for the session as usual.
    waited_from = time.monotonic()
    still_running = shell_manager.wait(data["sessionId"], yield_time_ms=300)
    assert time.monotonic() - waited_from >= 0.25
    assert still_running["data"]["executionStatus"] == "running"
    assert alive(pid)

    stopped = shell_manager.control(data["sessionId"], "terminate", yield_time_ms=2_000)
    assert stopped["data"]["executionStatus"] == "exited"
    assert stopped["data"]["termination"] == "terminated"
    assert stopped["data"]["exitCode"] == 0
    assert not alive(pid)


def test_final_result_waits_for_background_writes(tmp_path, shell_manager):
    late = tmp_path / "late.txt"
    command = f"(sleep 0.5; printf late > {shlex.quote(str(late))}) >/dev/null 2>&1 &"
    first = shell_manager.start(launch(tmp_path, command), yield_time_ms=10_000)
    assert first["data"]["executionStatus"] == "running"
    assert first["data"]["backgroundProcesses"] is True

    final = shell_manager.wait(first["data"]["sessionId"], yield_time_ms=5_000)
    assert final["code"] == "ok"
    assert final["data"]["executionStatus"] == "exited"
    assert final["data"]["termination"] == "exit"
    assert final["data"]["exitCode"] == 0
    assert final["reconciliationRequired"] is False
    # The write happened before the final result, never after it.
    assert late.read_text() == "late"


def test_background_output_on_inherited_pipes_is_still_captured(tmp_path, shell_manager):
    first = shell_manager.start(
        launch(tmp_path, "(sleep 0.3; echo from-background) &"), yield_time_ms=10_000,
    )
    assert first["data"]["backgroundProcesses"] is True
    final = shell_manager.wait(first["data"]["sessionId"], yield_time_ms=5_000)
    assert final["data"]["executionStatus"] == "exited"
    assert "from-background" in final["data"]["stdout"]


def test_failed_command_reports_its_exit_code_while_background_runs(tmp_path, shell_manager):
    command, pid_file = background_pid(tmp_path)
    result = shell_manager.start(launch(tmp_path, f"{command}; exit 3"), yield_time_ms=10_000)
    assert result["data"]["leaderExitCode"] == 3
    shell_manager.control(result["data"]["sessionId"], "terminate", yield_time_ms=2_000)
    assert not alive(int(pid_file.read_text()))


def test_run_cleanup_stops_adopted_background_processes(tmp_path, resources):
    manager = ShellProcessManager(resources, owner_run_id="background-cleanup")
    command, pid_file = background_pid(tmp_path)
    result = manager.start(launch(tmp_path, command), yield_time_ms=10_000)
    pid = int(pid_file.read_text())
    assert manager.has_running()
    manager.cleanup()
    assert not manager.has_running()
    assert not manager.is_running(result["data"]["sessionId"])
    assert not alive(pid)


def test_run_keeps_a_background_service_until_it_finishes(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    session = store.create_session(str(workspace))
    run, _ = store.create_run(session["id"], "Start the preview service", approval_mode="full_access")
    command, pid_file = background_pid(workspace)
    observed: dict[str, object] = {}

    def result() -> dict[str, object]:
        row = store.connection.execute(
            "SELECT result_json FROM tool_calls WHERE provider_call_id='service'",
        ).fetchone()
        return json.loads(row[0])

    class ServiceModel(ScriptedModel):
        def complete(model, *args, **kwargs):
            if model._index == 1:
                # The next step sees the service still running in its session.
                observed["running"] = result()
                observed["alive"] = alive(int(pid_file.read_text()))
                model.responses = (*model.responses, ModelResponse(text="Service checked"))
            return super().complete(*args, **kwargs)

    try:
        model = ServiceModel([ModelResponse(tool_calls=(ModelToolCall(
            "service", "run_shell", {"command": command, "yieldTimeMs": 10_000},
        ),))])
        RuntimeLoop(store, model, lambda _: None).run(run["id"], threading.Event())
        running = observed["running"]
        assert running["data"]["executionStatus"] == "running", running
        assert running["data"]["backgroundProcesses"] is True
        assert observed["alive"] is True
        assert store.read_run(run["id"])["status"] == "succeeded"
        assert store.connection.execute(
            "SELECT reconciliation_required FROM runs WHERE id=?", (run["id"],),
        ).fetchone()[0] == 0
        # Run completion stops the owned service and commits its final result.
        assert not alive(int(pid_file.read_text()))
        final = result()
        assert final["data"]["executionStatus"] == "exited"
        assert final["data"]["termination"] == "terminated"
        assert final["data"]["exitCode"] == 0
        assert final["reconciliationRequired"] is False
        assert store.connection.execute(
            "SELECT status FROM durable_intents WHERE run_id=?", (run["id"],),
        ).fetchone()[0] == "completed"
    finally:
        store.close()


def test_run_end_still_marks_an_interrupted_foreground_command_uncertain(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    session = store.create_session(str(workspace))
    run, _ = store.create_run(session["id"], "Start a long command", approval_mode="full_access")
    try:
        model = ScriptedModel([
            ModelResponse(tool_calls=(ModelToolCall("long", "run_shell", {"command": "sleep 30", "yieldTimeMs": 250}),)),
            ModelResponse(text="Stopped waiting"),
        ])
        RuntimeLoop(store, model, lambda _: None).run(run["id"], threading.Event())
        row = store.connection.execute(
            "SELECT result_json FROM tool_calls WHERE provider_call_id='long'",
        ).fetchone()
        final = json.loads(row[0])
        assert final["data"]["termination"] == "canceled"
        assert final["reconciliationRequired"] is True
    finally:
        store.close()


def test_controls_still_reach_a_session_whose_command_exited(tmp_path, shell_manager):
    command, pid_file = background_pid(tmp_path, "cat >/dev/null")
    result = shell_manager.start(launch(tmp_path, command), yield_time_ms=10_000)
    assert result["data"]["backgroundProcesses"] is True
    closed = shell_manager.control(result["data"]["sessionId"], "close_stdin", yield_time_ms=2_000)
    assert closed["data"]["controlAction"] == "close_stdin"
    assert not alive(int(pid_file.read_text()))

from __future__ import annotations

from dataclasses import replace
import errno
import json
import os
from pathlib import Path
import shlex
import socket
import sys
import threading

import pytest
from pydantic import ValidationError

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel
from eidos_runtime.runtime.loop import RuntimeLoop
from eidos_runtime.runtime.resource_registry import ResourceRegistry
from eidos_runtime.runtime.shell_output_buffer import ShellOutputBuffer
from eidos_runtime.runtime.shell_process_manager import ShellProcessManager, ShellProcessSessionNotFound
from eidos_runtime.sandbox.seatbelt import is_seatbelt_usable
from eidos_runtime.sandbox.shell import ShellLaunchSpec, ShellProcessStartError
from eidos_runtime.tools.contracts import ControlShellInput, RunShellInput
from eidos_runtime.tools.read_tool_output import read_tool_output_entry


pytestmark = pytest.mark.integration


@pytest.fixture
def shell_manager():
    resources = ResourceRegistry()
    manager = ShellProcessManager(resources, owner_run_id="pty-test")
    try:
        yield manager
    finally:
        manager.cleanup()
        resources.ensure_empty()


def launch(tmp_path: Path, code: str, *, tty: bool = True) -> ShellLaunchSpec:
    return ShellLaunchSpec(
        argv=(sys.executable, "-I", "-c", code), cwd=tmp_path,
        environment={"PATH": "/usr/bin:/bin", "TERM": "dumb"}, sandboxed=False,
        tty=tty, columns=100, rows=24,
    )


def test_pty_contracts_keep_pipe_default_and_reject_invalid_controls():
    assert RunShellInput(command="true").tty is False
    assert RunShellInput(command="true", tty=True).columns == 100
    for arguments in (
        {"sessionId": "shell", "action": "resize"},
        {"sessionId": "shell", "action": "resize", "columns": True, "rows": 24},
        {"sessionId": "shell", "action": "resize", "columns": 501, "rows": 24},
        {"sessionId": "shell", "action": "terminate", "rows": 24},
        {"sessionId": "shell", "action": "kill_pid", "pid": 1},
    ):
        with pytest.raises(ValidationError):
            ControlShellInput.model_validate(arguments)
    assert ControlShellInput.model_validate({
        "session_id": "shell", "action": "terminate", "yield_time_ms": 0,
    }).yieldTimeMs == 250


def test_pty_has_control_terminal_and_roundtrips_unicode_and_resize(tmp_path, shell_manager):
    code = (
        "import os,json,sys; print(json.dumps([os.isatty(i) for i in (0,1,2)]),flush=True); "
        "fd=os.open('/dev/tty',os.O_RDWR); os.close(fd); "
        "print(input('prompt> '),flush=True); print(os.get_terminal_size(),flush=True)"
    )
    running = shell_manager.start(launch(tmp_path, code), yield_time_ms=250)
    data = running["data"]
    assert "[true, true, true]" in data["stdout"]
    assert "prompt>" in data["stdout"]
    assert data["transport"] == "pty" and data["outputMerged"]
    assert data["stderr"] == ""
    resized = shell_manager.control(data["sessionId"], "resize", columns=120, rows=40)
    assert (resized["data"]["columns"], resized["data"]["rows"]) == (120, 40)
    completed = shell_manager.write_stdin(data["sessionId"], "甲乙\r", yield_time_ms=2000)
    assert completed["data"]["exitCode"] == 0
    assert "甲乙" in completed["data"]["stdout"]
    assert "columns=120, lines=40" in completed["data"]["stdout"]


def test_handled_pty_ctrl_c_keeps_process_alive_until_explicit_stop(tmp_path, shell_manager):
    code = (
        "import signal,time; signal.signal(signal.SIGINT,lambda *_:print('INT_HANDLED',flush=True)); "
        "print('READY',flush=True); time.sleep(30)"
    )
    running = shell_manager.start(launch(tmp_path, code), yield_time_ms=250)
    session_id = running["data"]["sessionId"]
    interrupted = shell_manager.write_stdin(session_id, "\x03", yield_time_ms=250)
    assert interrupted["data"]["executionStatus"] == "running"
    assert "INT_HANDLED" in interrupted["data"]["stdout"]
    stopped = shell_manager.control(session_id, "terminate", yield_time_ms=2000)
    assert stopped["data"]["executionStatus"] == "exited"
    assert stopped["data"]["exitCode"] != 0
    assert not stopped["reconciliationRequired"]


def test_pipe_eof_and_pty_half_close_have_distinct_behavior(tmp_path, shell_manager):
    running = shell_manager.start(launch(tmp_path, "import sys; print(repr(sys.stdin.read()),flush=True)", tty=False), yield_time_ms=0)
    completed = shell_manager.control(running["data"]["sessionId"], "close_stdin", yield_time_ms=2000)
    assert completed["data"]["exitCode"] == 0
    assert completed["data"]["stdout"] == "''\n"
    with pytest.raises(RuntimeError, match="shell_not_a_terminal"):
        # Use a new live PIPE rather than a finished process.
        pipe = shell_manager.start(launch(tmp_path, "import time; time.sleep(30)", tty=False), yield_time_ms=0)
        shell_manager.control(pipe["data"]["sessionId"], "resize", columns=100, rows=30)
    terminal = shell_manager.start(launch(tmp_path, "input()"), yield_time_ms=250)
    with pytest.raises(RuntimeError, match="shell_terminal_cannot_close_stdin"):
        shell_manager.control(terminal["data"]["sessionId"], "close_stdin")
    assert shell_manager.is_running(terminal["data"]["sessionId"])
    other = ShellProcessManager(owner_run_id="other-run")
    with pytest.raises(ShellProcessSessionNotFound):
        other.control(terminal["data"]["sessionId"], "terminate")
    other.cleanup()


def test_cancel_reaps_pty_and_its_background_child(tmp_path, shell_manager):
    child_pid_file = tmp_path / "child-pid"
    spec = replace(launch(tmp_path, ""), argv=("/bin/sh", "-c", f"sleep 30 & echo $! > {shlex.quote(str(child_pid_file))}; wait"))
    running = shell_manager.start(spec, yield_time_ms=250)
    child_pid = int(child_pid_file.read_text())
    shell_manager.cleanup()
    assert not shell_manager.has_running()
    with pytest.raises(ProcessLookupError):
        os.kill(child_pid, 0)
    assert not shell_manager.is_running(running["data"]["sessionId"])


def test_pty_does_not_execute_before_execution_start_callback(tmp_path, shell_manager):
    marker = tmp_path / "must-not-exist"
    def fail_start():
        raise RuntimeError("fixture execution start failed")
    with pytest.raises(RuntimeError, match="fixture execution start"):
        shell_manager.start(launch(tmp_path, f"from pathlib import Path; Path({str(marker)!r}).touch()"),
                            on_started=fail_start)
    assert not marker.exists()


def test_pty_setup_failure_does_not_execute_or_leak_descriptors(tmp_path, shell_manager, monkeypatch):
    import eidos_runtime.runtime.shell_process_transport as transport
    allocated = []
    real_openpty = os.openpty
    def openpty():
        pair = real_openpty()
        allocated.extend(pair)
        return pair
    monkeypatch.setattr(transport.os, "openpty", openpty)
    monkeypatch.setattr(transport.os, "pipe", lambda: (_ for _ in ()).throw(OSError(errno.EMFILE, "fixture")))
    with pytest.raises(ShellProcessStartError):
        shell_manager.start(launch(tmp_path, "print('must-not-run')"))
    for descriptor in allocated:
        with pytest.raises(OSError) as error:
            os.fstat(descriptor)
        assert error.value.errno == errno.EBADF


def test_cancel_before_pty_gate_release_does_not_execute_command(tmp_path, shell_manager):
    marker = tmp_path / "must-not-exist"
    cancel = threading.Event()
    result = shell_manager.start(
        launch(tmp_path, f"from pathlib import Path; Path({str(marker)!r}).touch()"),
        cancel=cancel, on_started=cancel.set, yield_time_ms=250,
    )
    assert result["data"]["executionStatus"] == "exited"
    assert not marker.exists()


def test_missing_helper_fails_before_command_start(tmp_path, shell_manager, monkeypatch):
    import eidos_runtime.runtime.shell_process_transport as transport
    monkeypatch.setattr(transport, "PTY_HELPER", tmp_path / "missing-helper.py")
    started = threading.Event()
    with pytest.raises(ShellProcessStartError):
        shell_manager.start(launch(tmp_path, "print('must-not-run')"), on_started=started.set)
    assert not started.is_set()


def test_large_output_keeps_latest_prompt_and_final_evidence(tmp_path, shell_manager):
    deltas = []
    code = (
        "import sys; print('HEAD',flush=True); sys.stdout.write('x'*400000); "
        "sys.stdout.flush(); print('LATEST_PROMPT',flush=True); sys.stdin.readline(); "
        "print('FINAL_TEST_RESULT',flush=True)"
    )
    running = shell_manager.start(launch(tmp_path, code, tty=False), yield_time_ms=250, on_output=deltas.append)
    session_id = running["data"]["sessionId"]
    assert "HEAD" in running["data"]["stdout"]
    assert "LATEST_PROMPT" in running["data"]["stdout"]
    assert running["data"]["truncated"]
    completed = shell_manager.write_stdin(session_id, "continue\n", yield_time_ms=2000)
    assert "FINAL_TEST_RESULT" in completed["data"]["stdout"]
    assert "LATEST_PROMPT" not in completed["data"]["stdout"]
    cumulative = shell_manager.read_output(session_id)["data"]
    assert "HEAD" in cumulative["stdout"] and "FINAL_TEST_RESULT" in cumulative["stdout"]
    assert sum(len(text.encode()) for text in deltas) <= 256 * 1024


@pytest.mark.parametrize("chunk_size", [1, 2, 5, 64])
def test_retained_output_keeps_utf8_and_incremental_cursors(chunk_size):
    buffer = ShellOutputBuffer(64)
    content = ("甲乙丙丁" * 30 + "终点").encode()
    after = 0
    incremental = []
    for offset in range(0, len(content), chunk_size):
        buffer.append(content[offset:offset + chunk_size])
        observed = buffer.read(after)
        incremental.append(observed.text)
        after = observed.next_offset
    buffer.finished = True
    assert "�" not in buffer.read().text
    assert buffer.read().text.endswith("终点")
    assert "�" not in "".join(incremental)
    assert buffer.retained_bytes <= 64
    assert "output bytes omitted" in buffer.read().text


@pytest.mark.parametrize("mode", ["manual", "auto_review", "full_access"])
@pytest.mark.platform
def test_runtime_pty_keeps_permissions_and_persists_actual_transport(tmp_path, mode):
    if not is_seatbelt_usable():
        pytest.skip("Native macOS Seatbelt is unavailable")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".agents").mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    terminal_descriptors = os.openpty()
    other_terminal = os.ttyname(terminal_descriptors[1])
    try:
        session = store.create_session(str(workspace))
        run, _ = store.create_run(session["id"], "Check terminal", approval_mode=mode)
        code = (
            "import os,json,socket; fd=os.open('/dev/tty',os.O_RDWR); os.close(fd); "
            "result={'tty':[os.isatty(i) for i in (0,1,2)]}; "
            "\ntry: open('.agents/blocked','w').write('probe'); result['write_allowed']=True"
            "\nexcept OSError: result['write_allowed']=False"
            f"\ntry: fd=os.open({other_terminal!r},os.O_RDWR); os.close(fd); result['other_terminal_write']=True"
            "\nexcept OSError: result['other_terminal_write']=False"
            f"\ns=socket.socket(); s.settimeout(1); result['network_errno']=s.connect_ex(('127.0.0.1',{listener.getsockname()[1]})); s.close()"
            "\nprint(json.dumps(result),flush=True)"
        )
        command = f"{shlex.quote(sys.executable)} -I -c {shlex.quote(code)}"
        model = ScriptedModel([
            ModelResponse(tool_calls=(ModelToolCall("terminal", "run_shell", {"command": command, "tty": True, "yieldTimeMs": 1000}),)),
            ModelResponse(text="Checked"),
        ])
        approvals = []
        RuntimeLoop(store, model, lambda _: None, shell_available=True,
                    request_approval=lambda request: approvals.append(request)).run(run["id"], threading.Event())
        row = store.connection.execute(
            "SELECT result_json FROM tool_calls WHERE provider_call_id='terminal'"
        ).fetchone()
        result = json.loads(row[0])
        assert result["data"]["exitCode"] == 0, result
        observed = json.loads(result["data"]["stdout"])
        assert observed["tty"] == [True, True, True]
        assert result["data"]["transport"] == "pty"
        assert result["data"]["outputMerged"] is True
        assert observed["write_allowed"] == (mode == "full_access")
        assert observed["other_terminal_write"] == (mode == "full_access")
        assert (observed["network_errno"] == 0) == (mode == "full_access")
        assert not approvals
        assert store.connection.execute("SELECT status FROM durable_intents WHERE run_id=?", (run["id"],)).fetchone()[0] == "completed"
    finally:
        listener.close()
        for descriptor in terminal_descriptors:
            os.close(descriptor)
        store.close()


def test_live_output_revision_prevents_paging_changed_retained_view(tmp_path, shell_manager):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    try:
        session = store.create_session(str(workspace))
        run, _ = store.create_run(session["id"], "read output")
        # The manager's owner is independent of the repository's access check.
        running = shell_manager.start(launch(workspace, "import sys; print('first',flush=True); input(); print('second',flush=True); input()", tty=False), yield_time_ms=250)
        item = store.create_tool_item(run["id"], 1, 0, "live", "run_shell", "{}")
        store.complete_tool_item(item["id"], json.dumps(running))
        entry = read_tool_output_entry(store, run["id"], shell_manager)
        first = entry.adapter.execute({"callId": "live", "maxBytes": 4}, threading.Event())
        revision = first["data"]["outputRevision"]
        shell_manager.write_stdin(running["data"]["sessionId"], "continue\n", yield_time_ms=250)
        changed = entry.adapter.execute({"callId": "live", "offsetBytes": 4, "outputRevision": revision}, threading.Event())
        assert changed["code"] == "output_snapshot_changed"
        tail = entry.adapter.execute({"callId": "live", "fromEnd": True}, threading.Event())
        assert "second" in tail["data"]["content"]
        assert tail["data"]["outputRevision"] != revision
    finally:
        store.close()


@pytest.mark.parametrize("finish", ["input", "terminate"])
def test_runtime_controls_use_intents_and_keep_terminal_facts_after_restart(tmp_path, finish):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    data_directory = tmp_path / "data"
    store = SessionStore(data_directory)
    store.initialize()
    session = store.create_session(str(workspace))
    run, _ = store.create_run(session["id"], "Interact with terminal", approval_mode="full_access")

    def result(call_id):
        row = store.connection.execute(
            "SELECT result_json FROM tool_calls WHERE provider_call_id=?", (call_id,),
        ).fetchone()
        return json.loads(row[0])

    code = "import os; print('READY',flush=True); value=input(); print(value,os.get_terminal_size(),flush=True)"
    command = f"{shlex.quote(sys.executable)} -I -c {shlex.quote(code)}"

    class InteractionModel(ScriptedModel):
        def complete(model, *args, **kwargs):
            if model._index in (1, 2, 3):
                terminal = result("terminal")
                assert terminal["data"]["executionStatus"] == "running", terminal
                identifier = terminal["data"]["sessionId"]
                call = (
                    ModelToolCall("resize", "control_shell", {"sessionId": identifier, "action": "resize", "columns": 120, "rows": 40}),
                    ModelToolCall("invalid", "control_shell", {"sessionId": identifier, "action": "close_stdin"}),
                    ModelToolCall("finish", "write_stdin" if finish == "input" else "control_shell",
                                  {"sessionId": identifier, "yieldTimeMs": 2000,
                                   **({"chars": "完成\r"} if finish == "input" else {"action": "terminate"})}),
                )[model._index - 1]
                model.responses = (*model.responses, ModelResponse(tool_calls=(call,)))
            elif model._index == 4:
                model.responses = (*model.responses, ModelResponse(text="Terminal checked"))
            return super().complete(*args, **kwargs)

    try:
        model = InteractionModel([ModelResponse(tool_calls=(ModelToolCall(
            "terminal", "run_shell", {"command": command, "tty": True, "yieldTimeMs": 250},
        ),))])
        RuntimeLoop(store, model, lambda _: None).run(run["id"], threading.Event())
        resized, invalid, ended = result("resize"), result("invalid"), result("terminal")
        assert resized["data"]["controlAction"] == "resize"
        assert (resized["data"]["columns"], resized["data"]["rows"]) == (120, 40)
        assert invalid["code"] == "shell_control_failed"
        assert not invalid["sideEffectsMayExist"] and not invalid["reconciliationRequired"]
        assert ended["data"]["transport"] == "pty" and ended["data"]["executionStatus"] == "exited"
        assert not ended["reconciliationRequired"]
        if finish == "input":
            assert ended["data"]["exitCode"] == 0
            assert "完成" in ended["data"]["stdout"] and "columns=120, lines=40" in ended["data"]["stdout"]
        else:
            assert ended["data"]["termination"] == "terminated"
        assert store.read_run(run["id"])["status"] == "succeeded"
        assert [row[0] for row in store.connection.execute(
            "SELECT status FROM durable_intents WHERE run_id=?", (run["id"],),
        )] == ["completed"] * 4
    finally:
        store.close()

    reopened = SessionStore(data_directory)
    reopened.initialize()
    try:
        persisted = json.loads(reopened.connection.execute(
            "SELECT result_json FROM tool_calls WHERE provider_call_id='terminal'",
        ).fetchone()[0])
        assert persisted == ended
        next_run, _ = reopened.create_run(session["id"], "Old terminal is unavailable", approval_mode="full_access")
        model = ScriptedModel([
            ModelResponse(tool_calls=(ModelToolCall("stale", "control_shell", {
                "sessionId": ended["data"]["sessionId"], "action": "terminate",
            }),)), ModelResponse(text="Old terminal is closed"),
        ])
        RuntimeLoop(reopened, model, lambda _: None).run(next_run["id"], threading.Event())
        stale = json.loads(reopened.connection.execute(
            "SELECT result_json FROM tool_calls WHERE provider_call_id='stale'",
        ).fetchone()[0])
        assert stale["code"] == "shell_session_not_found"
        assert not stale["sideEffectsMayExist"]
        assert reopened.connection.execute(
            "SELECT count(*) FROM durable_intents WHERE run_id=?", (next_run["id"],),
        ).fetchone()[0] == 0
    finally:
        reopened.close()

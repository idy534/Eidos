from __future__ import annotations

import json
from pathlib import Path
import sys
import threading

import pytest
from pydantic import ValidationError

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel
from eidos_runtime.runtime.loop import RuntimeLoop
from eidos_runtime.runtime.resource_registry import ResourceRegistry
from eidos_runtime.runtime.shell_process_manager import ShellProcessManager
from eidos_runtime.sandbox.shell import ShellLaunchSpec
from eidos_runtime.tools.contracts import RunShellInput, WriteStdinInput
from eidos_runtime.tools.workspace import ToolExecutor, builtin_tool_registry


def test_codex_arguments_normalize_before_execution_without_losing_raw_payload(tmp_path: Path) -> None:
    arguments = {"cmd": "pwd", "workdir": ".", "yield_time_ms": 0}
    with ToolExecutor(tmp_path) as executor:
        registry = builtin_tool_registry(executor)
        entry = registry.get("run_shell")
        assert entry is not None
        result = entry.validate_arguments(arguments)
        stdin = registry.get("write_stdin")
        assert stdin is not None
        input_result = stdin.validate_arguments({"session_id": "shell", "chars": "continue\n"})
    assert result.valid
    assert result.normalized_arguments == {"command": "pwd", "cwd": ".", "yieldTimeMs": 250}
    assert arguments == {"cmd": "pwd", "workdir": ".", "yield_time_ms": 0}
    assert RunShellInput.ignored_argument_names(arguments) == ()
    assert input_result.valid
    assert input_result.normalized_arguments == {
        "sessionId": "shell", "chars": "continue\n", "yieldTimeMs": 250,
    }


def test_canonical_command_and_directory_take_precedence_over_aliases() -> None:
    parsed, ignored = RunShellInput.filter_arguments_json(json.dumps({
        "command": "printf canonical", "cmd": "printf alias",
        "cwd": ".", "workdir": "different", "yieldTimeMs": 750, "yield_time_ms": 1000,
    }))
    assert parsed.command == "printf canonical"
    assert parsed.cwd == "."
    assert parsed.yieldTimeMs == 750
    assert ignored == ()
    for command in ("", False, ["pwd"]):
        with pytest.raises(ValidationError):
            RunShellInput.filter_arguments_json(json.dumps({"command": command, "cmd": "pwd"}))


def test_aliases_do_not_expand_the_model_visible_schema() -> None:
    for contract, required, aliases in (
        (RunShellInput, ["command"], {"cmd", "workdir", "yield_time_ms"}),
        (WriteStdinInput, ["sessionId"], {"session_id", "yield_time_ms"}),
    ):
        schema = contract.model_json_schema(by_alias=True)
        assert schema["required"] == required
        assert not aliases.intersection(schema["properties"])
        assert schema["additionalProperties"] is False


def test_stdin_defaults_keep_input_responsive_and_polls_longer() -> None:
    assert WriteStdinInput(sessionId="shell").yieldTimeMs == 30_000
    assert WriteStdinInput(sessionId="shell", chars="").yieldTimeMs == 30_000
    assert WriteStdinInput(sessionId="shell", chars="continue\n").yieldTimeMs == 250
    assert WriteStdinInput(sessionId="shell", chars="\x03").yieldTimeMs == 250
    parsed = WriteStdinInput.model_validate_json(json.dumps({
        "session_id": "shell", "chars": "continue\n", "yield_time_ms": 1500,
    }))
    assert parsed.model_dump() == {"sessionId": "shell", "chars": "continue\n", "yieldTimeMs": 1500}
    assert WriteStdinInput.model_validate_json(parsed.model_dump_json()) == parsed
    for value, expected in ((0, 250), (249, 250), (120_000, 60_000)):
        assert WriteStdinInput.model_validate_json(json.dumps({
            "session_id": "shell", "yield_time_ms": value,
        })).yieldTimeMs == expected
    for value in (False, "1000", -1, None):
        with pytest.raises(ValidationError):
            WriteStdinInput.model_validate_json(json.dumps({"session_id": "shell", "yield_time_ms": value}))


@pytest.mark.parametrize(("exit_code", "workdir"), [(0, "nested"), (7, "nested"), (0, None)])
@pytest.mark.integration
def test_codex_call_executes_once_in_requested_directory_and_survives_restart(
    tmp_path: Path, exit_code: int, workdir: str | None,
) -> None:
    workspace = tmp_path / "workspace"
    nested = workspace / "nested"
    nested.mkdir(parents=True)
    target = nested if workdir else workspace
    data = tmp_path / "data"
    store = SessionStore(data)
    store.initialize()
    try:
        session = store.create_session(str(workspace))
        run, _ = store.create_run(session["id"], "Run once", approval_mode="full_access")
        arguments = {
            "cmd": f"printf x >> once.txt; pwd; exit {exit_code}",
            "yield_time_ms": 0,
        }
        if workdir:
            arguments["workdir"] = workdir
        model = ScriptedModel([
            ModelResponse(tool_calls=(ModelToolCall("codex-shell", "run_shell", arguments),)),
            ModelResponse(text="Result received"),
        ])
        RuntimeLoop(store, model, lambda _: None).run(run["id"], threading.Event())
        row = store.connection.execute(
            "SELECT t.arguments_json, t.raw_arguments_json, t.result_json FROM tool_calls t "
            "JOIN items i ON i.id=t.item_id WHERE i.run_id=?", (run["id"],),
        ).fetchone()
        normalized = json.loads(row["arguments_json"])
        result = json.loads(row["result_json"])
        expected = {"command": arguments["cmd"], "yieldTimeMs": 250}
        if workdir:
            expected["cwd"] = workdir
        assert normalized == expected
        assert json.loads(row["raw_arguments_json"]) == arguments
        assert result["data"]["stdout"].strip() == str(target.resolve())
        assert result["data"]["exitCode"] == exit_code
        assert result["code"] == ("ok" if exit_code == 0 else "shell_exit_nonzero")
        assert not result["reconciliationRequired"]
        assert (target / "once.txt").read_text() == "x"
        if workdir:
            assert not (workspace / "once.txt").exists()
        assert store.connection.execute(
            "SELECT count(*) FROM durable_intents WHERE run_id=?", (run["id"],),
        ).fetchone()[0] == 1
    finally:
        store.close()
    reopened = SessionStore(data)
    reopened.initialize()
    try:
        assert reopened.read_run(run["id"])["status"] == "succeeded"
        assert (target / "once.txt").read_text() == "x"
        assert reopened.connection.execute(
            "SELECT count(*) FROM tool_calls t JOIN items i ON i.id=t.item_id WHERE i.run_id=?",
            (run["id"],),
        ).fetchone()[0] == 1
    finally:
        reopened.close()


@pytest.mark.integration
def test_codex_stdin_aliases_follow_the_existing_process_and_release_resources(tmp_path: Path) -> None:
    resources = ResourceRegistry()
    manager = ShellProcessManager(resources, owner_run_id="usability-test")
    launch = ShellLaunchSpec(
        argv=(sys.executable, "-c", "import sys; print('ready', flush=True); "
              "print(sys.stdin.readline().strip(), flush=True); sys.stdin.readline()"),
        cwd=tmp_path, environment={"PATH": "/usr/bin:/bin"}, sandboxed=False,
    )
    try:
        running = manager.start(launch, yield_time_ms=250)
        assert running["code"] == "shell_running"
        arguments = WriteStdinInput.model_validate_json(json.dumps({
            "session_id": running["data"]["sessionId"], "chars": "hello\n",
        }))
        assert arguments.yieldTimeMs == 250
        result = manager.write_stdin(
            arguments.sessionId, arguments.chars, yield_time_ms=arguments.yieldTimeMs,
        )
        assert result["code"] == "shell_running"
        assert result["data"]["sessionId"] == running["data"]["sessionId"]
        assert "hello" in result["data"]["stdout"]
        assert not result["reconciliationRequired"]
        completed = manager.write_stdin(arguments.sessionId, "exit\n", yield_time_ms=3000)
        assert completed["code"] == "ok"
        assert completed["data"]["sessionId"] == arguments.sessionId
    finally:
        manager.cleanup()
        resources.ensure_empty()

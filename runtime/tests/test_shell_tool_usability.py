from __future__ import annotations

import json
import logging
from pathlib import Path
import sqlite3
import sys
import threading
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from eidos_runtime.context.builder import _replayed_function_arguments
from eidos_runtime.db.database import DATABASE_NAME
from eidos_runtime.db.schema import V52_SCHEMA_SQL
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel
from eidos_runtime.persistence.session_history import HistoryRecord
from eidos_runtime.protocol.schemas import ItemDto
from eidos_runtime.runtime.engine import RuntimeEngine
from eidos_runtime.runtime.loop_guard import tool_call_fingerprint
from eidos_runtime.runtime.resource_registry import ResourceRegistry
from eidos_runtime.runtime.shell_process_manager import ShellProcessManager
from eidos_runtime.runtime.tool_dispatcher import ToolDispatcher
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


@pytest.mark.parametrize(("value", "expected", "dropped"), [
    ("  检查未提交的改动  ", "检查未提交的改动", None),
    ("", None, "empty"),
    (" \t ", None, "empty"),
    (None, None, "not_string"),
    (False, None, "not_string"),
    # Longer text is kept here and shortened after redaction.
    ("x" * 121, "x" * 121, None),
    ("x" * 2_001, None, "too_long"),
    ("first\nsecond", None, "unsafe_characters"),
    ("\u202ecommand", None, "unsafe_characters"),
])
def test_shell_display_intent_is_optional_and_excluded_from_execution(tmp_path, value, expected, dropped):
    with ToolExecutor(tmp_path) as executor:
        registry = builtin_tool_registry(executor)
        entry = registry.get("run_shell")
        definition = entry.model_definition()
        assert "Include display_intent for every run_shell call, including follow-up calls" in definition.description
        # The model is told to write the description first; execution does not need it.
        assert definition.parameters_json_schema["required"] == ["display_intent", "command"]
        assert next(iter(definition.parameters_json_schema["properties"])) == "display_intent"
        assert definition.parameters_json_schema["properties"]["display_intent"]["maxLength"] == 120
        assert "display_intent" not in entry.spec.input_schema["properties"]
        assert entry.spec.input_schema["required"] == ["command"]
        dispatcher = ToolDispatcher(registry)
        original = {"command": "git status --short"}
        baseline = dispatcher.validate(ModelResponse(tool_calls=(
            ModelToolCall("shell", "run_shell", original),
        )))
        result = dispatcher.validate(ModelResponse(tool_calls=(
            ModelToolCall("shell", "run_shell", {**original, "display_intent": value}),
        )))
        assert result.error_code is None
        assert result.tool_calls[0].display_intent == expected
        assert baseline.dropped_display_intents == ()
        assert result.dropped_display_intents == ((("shell", dropped),) if dropped else ())
        assert result.tool_calls[0].arguments == original
        assert result.tool_calls[0].raw_payload.arguments == original
        assert tool_call_fingerprint(result.tool_calls) == tool_call_fingerprint(baseline.tool_calls)
        assert dispatcher.plan_pending_approval(
            baseline.tool_calls[0], entry.contract_fingerprint,
        ).descriptor is entry


def test_shell_display_intent_item_matches_desktop_protocol_fixture():
    fixture = json.loads(
        (Path(__file__).resolve().parents[2] / "protocol/fixtures/v1.json").read_text()
    )["shellDisplayIntent"]["item"]
    assert ItemDto.model_validate(fixture).to_json_value() == fixture


@pytest.mark.parametrize(("arguments_json", "display_intent", "expected"), [
    (
        '{"command":"git status --short"}', "检查未提交的改动",
        '{"display_intent":"检查未提交的改动","command":"git status --short"}',
    ),
    ('{"command":"git status --short"}', None, '{"command":"git status --short"}'),
    # Cleared or cut arguments are replayed exactly as stored.
    ("{}", "检查未提交的改动", "{}"),
    ('{"command":"git sta', "检查未提交的改动", '{"command":"git sta'),
    (None, "检查未提交的改动", "{}"),
])
def test_replayed_shell_arguments_start_with_the_saved_display_intent(
    arguments_json, display_intent, expected,
):
    assert _replayed_function_arguments(arguments_json, display_intent) == expected


def test_inherited_shell_history_keeps_display_intent_for_replay():
    fixture = json.loads(
        (Path(__file__).resolve().parents[2] / "protocol/fixtures/v1.json").read_text()
    )["shellDisplayIntent"]["item"]
    record = HistoryRecord.model_construct(
        item=ItemDto.model_validate(fixture), run=SimpleNamespace(id=fixture["runId"]),
    )
    assert record.fact(None, replay=False).display_intent == fixture["toolCall"]["displayIntent"]


@pytest.mark.integration
def test_overlong_display_intent_is_redacted_before_it_is_shortened(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    try:
        session = store.create_session(str(workspace))
        run, _ = store.create_run(session["id"], "Run once", approval_mode="full_access")
        # Cutting at 120 characters first would keep the start of the token.
        intent = "查" * 105 + " Bearer fake-fixture-secret-987654321 之后的说明"
        model = ScriptedModel([
            ModelResponse(tool_calls=(ModelToolCall("shell", "run_shell", {
                "command": "true", "display_intent": intent,
            }),)),
            ModelResponse(text="Result received"),
        ])
        RuntimeEngine(store, model, lambda _event: None).run(run["id"], threading.Event())
        saved = store.connection.execute("SELECT display_intent FROM tool_calls").fetchone()[0]
        assert saved == "查" * 105 + " Bearer [REDAC…"
        assert len(saved) == 120
        replayed = json.loads(next(
            item["arguments"] for item in model.contexts[-1] if item.get("type") == "tool_call"
        ))
        assert replayed["display_intent"] == saved
        assert store.read_run(run["id"])["status"] == "succeeded"
    finally:
        store.close()


def test_shell_display_migration_rolls_back_when_second_column_conflicts(tmp_path):
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    path = data / DATABASE_NAME
    with sqlite3.connect(path) as connection:
        connection.executescript(V52_SCHEMA_SQL + """
            ALTER TABLE tool_calls ADD COLUMN display_command TEXT;
            PRAGMA user_version=52;
        """)
    path.chmod(0o600)
    store = SessionStore(data)
    try:
        store.initialize()
        assert store.database.health_code == "schema_migration_failed"
    finally:
        store.close()
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 52
        assert not any(
            column[1] == "display_intent"
            for column in connection.execute("PRAGMA table_info(tool_calls)")
        )


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
    tmp_path: Path, exit_code: int, workdir: str | None, caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="eidos.runtime")
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
        supplied_arguments = dict(arguments)
        expected_intent = None
        if exit_code == 7:
            supplied_arguments["display_intent"] = "   "
        elif workdir:
            supplied_arguments["display_intent"] = "检查目录 Bearer fake-fixture-secret-987654321"
            expected_intent = "检查目录 Bearer [REDACTED:bearer_token]"
        model = ScriptedModel([
            ModelResponse(tool_calls=(ModelToolCall("codex-shell", "run_shell", supplied_arguments),)),
            ModelResponse(text="Result received"),
        ])
        notifications = []
        RuntimeEngine(store, model, notifications.append).run(run["id"], threading.Event())
        row = store.connection.execute(
            "SELECT t.arguments_json, t.raw_arguments_json, t.result_json, t.display_intent FROM tool_calls t "
            "JOIN items i ON i.id=t.item_id WHERE i.run_id=?", (run["id"],),
        ).fetchone()
        normalized = json.loads(row["arguments_json"])
        result = json.loads(row["result_json"])
        expected = {"command": arguments["cmd"], "yieldTimeMs": 250}
        if workdir:
            expected["cwd"] = workdir
        assert normalized == expected
        assert json.loads(row["raw_arguments_json"]) == arguments
        assert row["display_intent"] == expected_intent
        started = next(
            event["params"]["item"]["toolCall"] for event in notifications
            if event.get("method") == "item/started" and "toolCall" in event["params"]["item"]
        )
        assert started.get("displayIntent") == expected_intent
        # The next request shows the model its saved description, ahead of the executed arguments.
        replayed = json.loads(next(
            item["arguments"] for item in model.contexts[-1] if item.get("type") == "tool_call"
        ))
        assert replayed == ({"display_intent": expected_intent} if expected_intent else {}) | normalized
        assert next(iter(replayed)) == ("display_intent" if expected_intent else "command")
        dropped = [
            record.reason_code for record in caplog.records
            if record.getMessage() == "shell_display_intent_dropped"
        ]
        assert dropped == (["empty"] if exit_code == 7 else [])
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
        shell = next(
            item["toolCall"] for item in reopened.read_session_snapshot(session["id"])["items"]
            if item["kind"] == "command_execution"
        )
        assert shell.get("displayIntent") == expected_intent
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

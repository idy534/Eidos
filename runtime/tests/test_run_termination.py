from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import pytest
from pydantic import ValidationError

from eidos_runtime.db.database import Database
from eidos_runtime.db.errors import InvalidRunStateError
from eidos_runtime.db.run_termination_migration import migrate_run_termination
from eidos_runtime.db.schema import SCHEMA_VERSION
from eidos_runtime.db.storage import DATABASE_NAME, SessionStore
from eidos_runtime.domain.run_termination import RunTermination
from eidos_runtime.model.client import CustomToolPayload, ModelRequestError, ModelRequestFailure, ModelResponse, ModelToolCall, ScriptedModel
from eidos_runtime.protocol.schemas import RunDto
from eidos_runtime.runtime.engine import RuntimeEngine


@pytest.fixture
def store(tmp_path: Path):
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    value = SessionStore(data)
    value.initialize()
    session = value.create_session(str(workspace))
    yield value, session, data
    value.close()


def test_terminal_reason_commits_with_event_and_survives_reopen(store):
    value, session, data = store
    run, _ = value.create_run(session["id"], "explain failure")
    cause = RunTermination(stage="runtime", reason="TOOL_INFRASTRUCTURE_FAILURE")
    mutation = value.fail_run_committed(run["id"], "TOOL_INFRASTRUCTURE_FAILURE", termination=cause)
    assert mutation.value["termination"] == cause.to_wire_dict(exclude_none=True)
    assert mutation.events[-1]["payload"]["termination"] == mutation.value["termination"]
    value.close()
    reopened = SessionStore(data)
    try:
        reopened.initialize()
        assert reopened.read_run(run["id"])["termination"] == mutation.value["termination"]
        assert reopened.read_session_snapshot(session["id"])["runs"][0]["termination"] == mutation.value["termination"]
    finally:
        reopened.close()


def test_late_failure_does_not_overwrite_cancel_or_add_failure_event(store):
    value, session, _ = store
    run, _ = value.create_run(session["id"], "cancel")
    value.cancel_run(run["id"])
    before = value.read_run(run["id"])
    with pytest.raises(InvalidRunStateError):
        value.fail_run_committed(run["id"], "MODEL_PROTOCOL_ERROR",
                                 termination=RunTermination(stage="model", reason="invalid_response"))
    assert value.read_run(run["id"]) == before


def test_event_write_failure_rolls_back_the_terminal_cause(store, monkeypatch):
    value, session, _ = store
    run, _ = value.create_run(session["id"], "transaction failure")
    before = value.read_run(run["id"])

    def fail_event(_name):
        raise sqlite3.OperationalError("injected event failure")

    monkeypatch.setattr("eidos_runtime.db.events.hit_fault", fail_event)
    with pytest.raises(sqlite3.OperationalError):
        value.fail_run_committed(run["id"], "INTERNAL_ERROR")
    assert value.read_run(run["id"]) == before


@pytest.mark.parametrize("code,status", [("provider_timeout", 504), ("authentication_failed", 403),
                                        ("protocol_error", None)])
def test_sampling_failure_keeps_specific_reason_and_terminal_attempt(store, code, status):
    value, session, _ = store

    class FailingModel:
        def complete(self, _context, _cancel, _on_text_delta, **_options):
            raise ModelRequestError(ModelRequestFailure(code=code, retryable=False, status_code=status))

    run, _ = value.create_run(session["id"], "failure")
    RuntimeEngine(value, FailingModel(), lambda _: None).run(run["id"], threading.Event())
    failed = value.read_run(run["id"])
    attempt = value.read_model_attempts(run["id"])[-1]
    assert failed["status"] == "failed"
    assert failed["termination"]["reason"] == code
    assert failed["termination"]["modelAttemptId"] == attempt["id"]
    if status:
        assert failed["termination"]["httpStatus"] == status


def test_cause_cannot_reference_an_attempt_from_another_run(store):
    value, session, _ = store
    run, _ = value.create_run(session["id"], "failure")
    with pytest.raises(InvalidRunStateError, match="source"):
        value.fail_run_committed(run["id"], "MODEL_PROTOCOL_ERROR", termination=RunTermination(
            stage="model", reason="protocol_error", step_id="other-step", model_attempt_id="other-attempt",
        ))
    assert value.read_run(run["id"])["status"] == "running"


def test_transport_retry_keeps_context_identity_but_links_the_last_failed_attempt(store):
    value, session, _ = store

    class RetryThenFail:
        calls = 0

        def complete(self, _context, _cancel, _on_text_delta, **_options):
            self.calls += 1
            raise ModelRequestError(ModelRequestFailure(
                code="provider_unavailable" if self.calls == 1 else "authentication_failed",
                retryable=self.calls == 1, status_code=503 if self.calls == 1 else 401,
            ))

    run, _ = value.create_run(session["id"], "retry then fail")
    RuntimeEngine(value, RetryThenFail(), lambda _: None).run(run["id"], threading.Event())
    attempts = value.read_model_attempts(run["id"])
    assert len(attempts) == 2
    cause = value.read_run(run["id"])["termination"]
    assert cause["reason"] == "authentication_failed"
    assert cause["modelAttemptId"] == attempts[-1]["id"]
    assert cause["modelAttemptId"] != attempts[0]["id"]


def test_tool_input_kind_failure_preserves_tool_and_actual_terminal_attempt(store):
    value, session, _ = store
    run, _ = value.create_run(session["id"], "invalid tool input")
    bad = ModelResponse(tool_calls=(ModelToolCall("bad-read", "read_file", CustomToolPayload(input="invalid")),))
    RuntimeEngine(value, ScriptedModel([bad, bad]), lambda _: None).run(run["id"], threading.Event())
    failed = value.read_run(run["id"])
    cause = failed["termination"]
    assert failed["status"] == "failed"
    assert cause["reason"] == "TOOL_ARGUMENT_CONTRACT_VIOLATION"
    assert cause["toolName"] == "read_file"
    assert cause["modelAttemptId"] == value.read_model_attempts(run["id"])[-1]["id"]


def test_v23_upgrade_preserves_state_and_adds_no_invented_causes(
    store, downgrade_session_fork_schema,
):
    value, session, data = store
    run, _ = value.create_run(session["id"], "keep this run")
    value.close()
    connection = sqlite3.connect(data / DATABASE_NAME)
    downgrade_session_fork_schema(connection)
    connection.execute("DROP TABLE sidebar_pins")
    connection.execute("DROP INDEX sidebar_pins_event_revision")
    connection.execute("ALTER TABLE runs DROP COLUMN termination_json")
    connection.execute("PRAGMA user_version=23")
    connection.commit()
    connection.close()
    database = Database(data)
    try:
        database.initialize()
        connection = database.connection()
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert "termination_json" in [row[1] for row in connection.execute("PRAGMA table_info(runs)")]
        row = connection.execute("SELECT user_input, termination_json FROM runs WHERE id=?", (run["id"],)).fetchone()
        assert tuple(row) == ("keep this run", None)
    finally:
        database.close()


def test_migration_failure_rolls_back_schema_and_version(monkeypatch):
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE runs(id TEXT PRIMARY KEY)")
    connection.execute("PRAGMA user_version=23")
    monkeypatch.setattr("eidos_runtime.db.run_termination_migration.RUN_TERMINATION_SCHEMA_SQL",
                        "ALTER TABLE runs ADD COLUMN termination_json TEXT;\nINSERT INTO missing_table VALUES (1);\n")
    with pytest.raises(sqlite3.OperationalError):
        migrate_run_termination(connection)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 23
    assert "termination_json" not in [row[1] for row in connection.execute("PRAGMA table_info(runs)")]
    assert not connection.in_transaction
    connection.close()


def test_shared_protocol_vectors_reject_unknown_and_invalid_cause_fields():
    fixture = json.loads((Path(__file__).resolve().parents[2] / "protocol/fixtures/run-termination-v1.json").read_text())
    assert RunDto.model_validate(fixture["run"]).to_json_value() == fixture["run"]
    for cause in fixture["invalid"]:
        with pytest.raises(ValidationError):
            RunDto.model_validate({**fixture["run"], "termination": cause})

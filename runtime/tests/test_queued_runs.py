from __future__ import annotations

import io
import json
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from eidos_runtime.db.errors import InvalidRunStateError, OperationConflictError, ResourceNotFoundError
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.domain.input_reference import InputReference
from eidos_runtime.persistence.queued_runs import QueuedRunInputConflict, QueuedRunRepository
from eidos_runtime.persistence.worktrees import ProjectWorktreeRepository
from eidos_runtime.protocol.queued_runs import QueuedRunChangeResponse, QueuedRunDeleteRequest, QueuedRunUpdateRequest
from eidos_runtime.protocol.server import RuntimeServer
from eidos_runtime.runtime.events import RuntimeEvents
from eidos_runtime.sandbox.sensitive import SensitiveScanner


@pytest.fixture
def queue(tmp_path):
    store = SessionStore(tmp_path / "data")
    store.initialize()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    project = ProjectWorktreeRepository(store.database).get_or_create_project(str(workspace))
    session = store.typed_runtime_repository().create_session(str(workspace), project_id=project.id).value
    yield store, session.id, QueuedRunRepository(store.database)
    store.close()


def change(repo, session_id, run, text, operation=None):
    return repo.change(session_id, run["id"], run["userInput"], text, operation_id=operation or str(uuid4()))


def test_update_preserves_fifo_model_permissions_and_references(queue):
    store, session, repo = queue
    active, _ = store.create_run(session, "active")
    reference = InputReference(id="a" * 64, kind="file", label="notes.txt", source="notes.txt", sha256="b" * 64, status="content", size=12)
    first, item = store.enqueue_run(session, "first", references=[reference], approval_mode="auto_review", work_mode="plan")
    second, _ = store.enqueue_run(session, "second")
    before = store.read_model_profile(first["id"])
    operation = str(uuid4())
    mutation = change(repo, session, first, "edited", operation)
    assert mutation.value.user_input == "edited"
    assert change(repo, session, first, "edited", operation).events == ()
    with pytest.raises(OperationConflictError):
        change(repo, session, first, "different", operation)
    assert store.read_item(item["id"])["content"] == "edited"
    assert store.read_item(item["id"])["references"] == [reference.to_wire_dict()]
    page = repo.list(session)
    assert [value.user_input for value in page.items] == ["edited", "second"]
    assert page.items[0].references == (reference,)
    assert store.read_model_profile(first["id"]) == before
    assert store.read_run(first["id"])["approvalMode"] == "auto_review"
    store.cancel_run(active["id"])
    claimed = store.claim_next_run()
    assert claimed["id"] == first["id"] and claimed["userInput"] == "edited"
    assert store.read_run(second["id"])["status"] == "queued"


def test_delete_removes_only_queued_input_and_does_not_enter_later_context(queue):
    store, session, repo = queue
    active, _ = store.create_run(session, "active")
    first, item = store.enqueue_run(session, "delete this")
    second, _ = store.enqueue_run(session, "second")
    operation = str(uuid4())
    mutation = change(repo, session, first, None, operation)
    assert mutation.value.kind == "deleted"
    assert change(repo, session, first, None, operation).events == ()
    with pytest.raises(ResourceNotFoundError):
        store.read_item(item["id"])
    assert store.read_run(active["id"])["status"] == "running"
    assert [value.run_id for value in repo.list(session).items] == [second["id"]]
    assert not any(fact.content == "delete this" for fact in store.context_projection_facts(second["id"]).items)
    assert store.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    notices = []
    RuntimeEvents(notices.append, store=store).deliver_pending()
    assert any(notice["method"] == "run/queueChanged" and notice["params"]["change"]["kind"] == "deleted" for notice in notices)


@pytest.mark.parametrize("delete", [False, True])
def test_claim_wins_over_stale_edit_or_delete(queue, delete):
    store, session, repo = queue
    run, _ = store.enqueue_run(session, "pending")
    store.claim_next_run()
    with pytest.raises(InvalidRunStateError):
        change(repo, session, run, None if delete else "edited")
    assert store.read_run(run["id"])["status"] == "running"
    assert store.read_run(run["id"])["userInput"] == "pending"


def test_cancel_resume_and_input_conflict_fail_closed(queue):
    store, session, repo = queue
    run, _ = store.enqueue_run(session, "pending")
    change(repo, session, run, "edited")
    with pytest.raises(QueuedRunInputConflict):
        change(repo, session, run, "stale edit")
    with pytest.raises(ResourceNotFoundError):
        change(repo, str(uuid4()), run, None)
    store.cancel_run(run["id"])
    with pytest.raises(InvalidRunStateError):
        change(repo, session, run, None)
    with store.database.transaction() as connection:
        connection.execute("UPDATE runs SET status = 'queued', started_at = 1 WHERE id = ?", (run["id"],))
    assert repo.list(session).items == ()
    with pytest.raises(InvalidRunStateError):
        change(repo, session, store.read_run(run["id"]), "resume edit")


@pytest.mark.parametrize("text", ["edited", None])
def test_event_failure_rolls_back_input_run_event_and_operation(queue, monkeypatch, text):
    store, session, repo = queue
    run, _ = store.enqueue_run(session, "pending")
    before = store.read_session_snapshot(session)
    operation = str(uuid4())
    def fail(*_args, **_kwargs):
        raise RuntimeError("event failed")
    monkeypatch.setattr("eidos_runtime.persistence.queued_runs.append_event", fail)
    with pytest.raises(RuntimeError, match="event failed"):
        change(repo, session, run, text, operation)
    assert store.read_session_snapshot(session) == before
    assert store.connection.execute("SELECT 1 FROM operations WHERE id = ?", (operation,)).fetchone() is None


def test_restart_retains_edits_deletions_and_outbox(queue):
    store, session, repo = queue
    first, _ = store.enqueue_run(session, "first")
    second, _ = store.enqueue_run(session, "second")
    change(repo, session, first, "edited")
    change(repo, session, second, None)
    directory = store.database.data_directory
    store.close()
    reopened = SessionStore(directory)
    try:
        reopened.initialize()
        assert [value.user_input for value in QueuedRunRepository(reopened.database).list(session).items] == ["edited"]
        notices = []
        RuntimeEvents(notices.append, store=reopened).deliver_pending()
        assert notices[-1]["params"]["change"]["kind"] == "deleted"
    finally:
        reopened.close()


def test_queue_pagination_is_independent_of_history_page_and_bounded(queue):
    store, session, repo = queue
    expected = []
    for index in range(105):
        run, _ = store.enqueue_run(session, f"message {index}")
        expected.append(run["id"])
    first = repo.list(session)
    second = repo.list(session, first.next_sequence)
    assert [value.run_id for value in (*first.items, *second.items)] == expected
    assert len(first.items) == 100 and second.next_sequence is None
    run, _ = store.enqueue_run(session, "x" * 65536)
    with pytest.raises(ValueError):
        change(repo, session, run, "")


def test_protocol_fixture_and_registered_application(tmp_path):
    output = io.StringIO()
    server = RuntimeServer(output=output, data_directory=tmp_path / "server-data")
    server.store.initialize()
    server.initialized = True
    server.sensitive = SensitiveScanner()
    workspace = tmp_path / "server-workspace"
    workspace.mkdir()
    session = server.store.create_session(str(workspace))["id"]
    run, _ = server.store.enqueue_run(session, "pending")
    try:
        server.handle({"jsonrpc": "2.0", "id": "client-edit", "method": "run/updateQueued", "params": {
            "sessionId": session, "runId": run["id"], "expectedUserInput": "pending", "userInput": "edited", "operationId": str(uuid4()),
        }})
        messages = [json.loads(line) for line in output.getvalue().splitlines()]
        result = next(value for value in messages if value.get("id") == "client-edit")
        assert "result" in result, result
        assert result["result"]["change"]["userInput"] == "edited"
    finally:
        server.close()
    fixture = json.loads((Path(__file__).parents[2] / "protocol/fixtures/queued-runs-v1.json").read_text())
    for value in fixture["validRequests"]:
        (QueuedRunUpdateRequest if "userInput" in value else QueuedRunDeleteRequest).model_validate(value)
    for value in fixture["invalidRequests"]:
        with pytest.raises(ValidationError):
            (QueuedRunUpdateRequest if "userInput" in value else QueuedRunDeleteRequest).model_validate(value)
    for value in fixture["validResponses"]:
        QueuedRunChangeResponse.model_validate(value)
    for value in fixture["invalidResponses"]:
        with pytest.raises(ValidationError):
            QueuedRunChangeResponse.model_validate(value)

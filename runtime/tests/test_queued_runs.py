from __future__ import annotations

import io
import json
import threading
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from eidos_runtime.db.errors import InvalidRunStateError, OperationConflictError, ResourceNotFoundError
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.domain.input_reference import InputReference
from eidos_runtime.persistence.queued_runs import QueuedRunInputConflict, QueuedRunRepository, SteerTargetUnavailable, QueuedSteerUnsupported
from eidos_runtime.context.builder import ContextBuilder
from eidos_runtime.persistence.worktrees import ProjectWorktreeRepository
from eidos_runtime.protocol.queued_runs import QueuedRunChangeResponse, QueuedRunDeleteRequest, QueuedRunUpdateRequest, QueuedRunSteerRequest
from eidos_runtime.protocol.server import RuntimeServer
from eidos_runtime.runtime.events import RuntimeEvents
from eidos_runtime.runtime.engine import RuntimeEngine
from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel
from eidos_runtime.persistence import queued_runs as queued_run_module
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
    claimed = store.claim_next_run_committed()
    assert claimed.value["id"] == first["id"] and claimed.value["userInput"] == "edited"
    assert store.read_run(second["id"])["status"] == "queued"


def test_queued_inputs_stay_out_of_current_context_and_compaction_until_fifo_start(queue):
    store, session, repo = queue
    active, _ = store.create_run(session, "current task")
    first, first_item = store.enqueue_run(session, "next task")
    second, second_item = store.enqueue_run(session, "last task")
    for projection in (store.context_projection_facts, store.compaction_candidate_facts):
        assert not {first_item["id"], second_item["id"]} & {item.item_id for item in projection(active["id"]).items}
    context = ContextBuilder(store).build(active["id"]).model_context
    assert not {"next task", "last task"} & {item.get("content") for item in context}
    store.cancel_run(active["id"])
    assert store.claim_next_run_committed().value["id"] == first["id"]
    assert [item.content for item in store.context_projection_facts(first["id"]).items if item.kind == "user_message"] == ["current task", "next task"]
    store.cancel_run(first["id"])
    assert store.claim_next_run_committed().value["id"] == second["id"]
    assert [row.user_input for row in repo.list(session).items] == []
    # Even a later completed Run cannot rewrite an older Run's projection.
    assert second_item["id"] not in {item.item_id for item in store.context_projection_facts(first["id"]).items}


@pytest.mark.parametrize("restart", [False, True])
def test_fifo_context_keeps_previous_run_complete_before_queued_goal(queue, restart):
    store, session, _ = queue
    active, _ = store.create_run(session, "Create a presentation")
    queued, user = store.enqueue_run(session, "Add a culture page")
    later, _ = store.enqueue_run(session, "Keep this queued")
    store.enqueue_input(active["id"], "Include local food")
    model = ScriptedModel([
        ModelResponse(tool_calls=(ModelToolCall("list", "list_files", {}),)),
        ModelResponse(text="The original presentation is complete"),
    ])
    RuntimeEngine(store, model, lambda _: None).run(active["id"], threading.Event())
    assert store.read_run(active["id"])["status"] == "succeeded"

    def check(current):
        for projection in (current.context_projection_facts, current.compaction_candidate_facts):
            facts = projection(queued["id"])
            # Summary candidates exclude the protected current goal; online
            # history retains it after the complete preceding Run.
            online = projection == current.context_projection_facts
            previous = facts.items[:-1] if online else facts.items
            if online:
                assert facts.items[-1].item_id == user["id"]
            else:
                assert user["id"] not in {item.item_id for item in facts.items}
            assert facts.current_user_goal_id == user["id"]
            assert all(item.run_id == active["id"] for item in previous)
            assert [item.content for item in facts.items if item.kind == "user_message"] == [
                "Create a presentation", "Include local food",
            ] + (["Add a culture page"] if online else [])
            assert previous[-1].content == "The original presentation is complete"
            assert sum(item.provider_call_id == "list" for item in facts.items) == 1
        context = ContextBuilder(current).build(queued["id"]).model_context
        assert context[-1]["content"] == "Add a culture page"
        assert sum(item.get("callId") == "list" for item in context) == 2
        assert "Keep this queued" not in [item.get("content") for item in context]
        assert current.read_run(later["id"])["status"] == "queued"

    assert store.claim_next_run_committed().value["id"] == queued["id"]
    check(store)
    if restart:
        directory = store.database.data_directory
        store.close()
        reopened = SessionStore(directory)
        reopened.initialize()
        try:
            check(reopened)
        finally:
            reopened.close()


def steer(repo, session, source, target, operation=None):
    return repo.change(session, source["id"], source["userInput"], None,
                       target_run_id=target["id"], operation_id=operation or str(uuid4()))


def test_steer_transfers_exactly_one_message_and_preserves_references_until_boundary(queue):
    store, session, repo = queue
    active, _ = store.create_run(session, "active", approval_mode="manual")
    reference = InputReference(id="a" * 64, kind="file", label="notes", source="notes", sha256="b" * 64, status="content", size=12)
    source, _ = store.enqueue_run(session, "steer this", references=[reference], approval_mode="full_access")
    other, _ = store.enqueue_run(session, "leave queued")
    operation = str(uuid4())
    result = steer(repo, session, source, active, operation)
    assert result.value.kind == "steered" and result.value.target_run_id == active["id"]
    assert steer(repo, session, source, active, operation).events == ()
    assert [row.run_id for row in repo.list(session).items] == [other["id"]]
    pending = store.read_item(result.value.item_id)
    assert pending["runId"] == active["id"] and pending["status"] == "in_progress"
    assert pending["references"] == [reference.to_wire_dict()]
    assert store.read_run(active["id"]).get("approvalMode", "manual") == "manual"
    assert "steer this" not in [item.content for item in store.context_projection_facts(active["id"]).items]
    assert store.consume_pending_input_facts(active["id"]) == ((pending["id"], "steer this"),)
    assert store.consume_pending_input_facts(active["id"]) == ()
    assert store.read_item(pending["id"])["status"] == "completed"
    assert [item.content for item in store.context_projection_facts(active["id"]).items].count("steer this") == 1
    notices = []
    RuntimeEvents(notices.append, store=store).deliver_pending()
    assert any(n["method"] == "item/completed" and n["params"]["item"]["id"] == pending["id"] for n in notices)
    assert any(n["method"] == "run/queueChanged" and n["params"]["change"]["kind"] == "steered" for n in notices)
    assert store.connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_steer_rejects_stale_target_or_source_without_losing_message(queue):
    store, session, repo = queue
    active, _ = store.create_run(session, "active")
    source, _ = store.enqueue_run(session, "pending")
    with pytest.raises(SteerTargetUnavailable):
        steer(repo, session, source, {"id": str(uuid4())})
    with pytest.raises(QueuedRunInputConflict):
        steer(repo, session, {**source, "userInput": "stale"}, active)
    store.cancel_run(active["id"])
    with pytest.raises(SteerTargetUnavailable):
        steer(repo, session, source, active)
    assert [row.run_id for row in repo.list(session).items] == [source["id"]]
    store.claim_next_run_committed()
    with pytest.raises(InvalidRunStateError):
        steer(repo, session, source, active)


def test_steer_rejects_a_target_with_a_cancel_request(queue):
    store, session, repo = queue
    active, _ = store.create_run(session, "active")
    source, _ = store.enqueue_run(session, "pending")
    store.request_cancel_committed(active["id"])
    with pytest.raises(SteerTargetUnavailable):
        steer(repo, session, source, active)
    assert [row.run_id for row in repo.list(session).items] == [source["id"]]
    assert not store.has_pending_input(active["id"])


def test_multiple_steers_keep_acceptance_order_and_survive_restart_without_duplicate_items(queue):
    store, session, repo = queue
    active, _ = store.create_run(session, "active")
    first, _ = store.enqueue_run(session, "first queued")
    second, _ = store.enqueue_run(session, "second queued")
    a = steer(repo, session, second, active).value
    b = steer(repo, session, first, active).value
    directory = store.database.data_directory
    store.close()
    reopened = SessionStore(directory)
    try:
        reopened.initialize()
        if reopened.read_run(active["id"])["status"] == "queued":
            reopened.claim_next_run_committed()
        assert reopened.consume_pending_input_facts(active["id"]) == ((a.item_id, "second queued"), (b.item_id, "first queued"))
        assert reopened.consume_pending_input_facts(active["id"]) == ()
        assert [item.content for item in reopened.context_projection_facts(active["id"]).items if item.kind == "user_message"] == ["active", "second queued", "first queued"]
    finally:
        reopened.close()


def test_steer_event_failure_rolls_back_both_queues_and_item(queue, monkeypatch):
    store, session, repo = queue
    active, _ = store.create_run(session, "active")
    source, _ = store.enqueue_run(session, "pending")
    before = store.read_session_snapshot(session)
    original = queued_run_module.append_event
    def fail_queue(connection, event_type, *args, **kwargs):
        if str(event_type) == "run.queue_changed":
            raise RuntimeError("queue event failed")
        return original(connection, event_type, *args, **kwargs)
    monkeypatch.setattr("eidos_runtime.persistence.queued_runs.append_event", fail_queue)
    with pytest.raises(RuntimeError, match="queue event failed"):
        steer(repo, session, source, active)
    assert store.read_session_snapshot(session) == before
    assert not store.has_pending_input(active["id"])


def test_steer_keeps_extension_selections_queued(queue):
    store, session, repo = queue
    active, _ = store.create_run(session, "active")
    selection = InputReference(id="c" * 64, kind="agent", label="agent", source="agent", sha256="d" * 64, status="selection", size=0)
    source, _ = store.enqueue_run(session, "pending", references=[selection])
    with pytest.raises(QueuedSteerUnsupported):
        steer(repo, session, source, active)
    assert [row.run_id for row in repo.list(session).items] == [source["id"]]


@pytest.mark.parametrize("with_tool", [False, True])
def test_steer_during_sampling_continues_same_run_without_consuming_other_queue_inputs(queue, with_tool):
    store, session, repo = queue
    active, _ = store.create_run(session, "active")
    source, _ = store.enqueue_run(session, "steer during sampling")
    other, _ = store.enqueue_run(session, "next run only")
    (store.workspace_for_session(session).path / "notes.txt").write_text("read this")
    class Model:
        contexts = []
        def complete(self, context, _cancel, on_text_delta, **_kwargs):
            self.contexts.append(context)
            if len(self.contexts) == 1:
                steer(repo, session, source, active)
                if with_tool:
                    return ModelResponse(tool_calls=(ModelToolCall("read", "read_file", {"path": "notes.txt"}),))
            on_text_delta("done")
            return ModelResponse(text="done")
    model = Model()
    RuntimeEngine(store, model, lambda _message: None).run(active["id"], threading.Event())
    assert store.read_run(active["id"])["status"] == "succeeded"
    assert len(model.contexts) == 2
    assert "steer during sampling" not in [item.get("content") for item in model.contexts[0]]
    assert [item.get("content") for item in model.contexts[1]].count("steer during sampling") == 1
    assert all("next run only" not in [item.get("content") for item in context] for context in model.contexts)
    if with_tool:
        context = model.contexts[1]
        assert next(i for i, item in enumerate(context) if item.get("content") == "steer during sampling") > next(i for i, item in enumerate(context) if item.get("type") == "tool_result")
    assert [row.run_id for row in repo.list(session).items] == [other["id"]]


def test_steer_survives_restart_and_cancel_does_not_inject_it_into_later_runs(queue):
    store, session, repo = queue
    active, _ = store.create_run(session, "active")
    source, _ = store.enqueue_run(session, "pending steer")
    other, _ = store.enqueue_run(session, "later task")
    result = steer(repo, session, source, active)
    directory = store.database.data_directory
    store.close()
    reopened = SessionStore(directory)
    try:
        reopened.initialize()
        assert reopened.has_pending_input(active["id"])
        assert reopened.read_item(result.value.item_id)["runId"] == active["id"]
        assert [row.run_id for row in QueuedRunRepository(reopened.database).list(session).items] == [other["id"]]
        reopened.cancel_run(active["id"])
        assert reopened.claim_next_run_committed().value["id"] == other["id"]
        assert "pending steer" not in [item.content for item in reopened.context_projection_facts(other["id"]).items]
    finally:
        reopened.close()


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
    store.claim_next_run_committed()
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
    for value in fixture["validSteerRequests"]:
        QueuedRunSteerRequest.model_validate(value)
    for value in fixture["invalidSteerRequests"]:
        with pytest.raises(ValidationError):
            QueuedRunSteerRequest.model_validate(value)
    for value in fixture["validResponses"]:
        QueuedRunChangeResponse.model_validate(value)
    for value in fixture["invalidResponses"]:
        with pytest.raises(ValidationError):
            QueuedRunChangeResponse.model_validate(value)

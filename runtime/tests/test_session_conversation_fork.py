from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from eidos_runtime.db.errors import (
    InvalidRunStateError,
    OperationConflictError,
    ResourceNotFoundError,
)
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.persistence.session_history import SessionHistoryRepository
from eidos_runtime.protocol.session_fork import SessionForkRequest


@pytest.fixture
def store(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    value = SessionStore(tmp_path / "data")
    value.initialize()
    assert value.health_state == "ready"
    yield value, str(value.create_session(str(workspace))["id"])
    value.close()


def answer(store: SessionStore, session: str, text: str) -> tuple[str, str]:
    run, _ = store.enqueue_run(session, "question " + text)
    item_id = str(uuid4())
    with store.database.transaction() as connection:
        connection.execute(
            "INSERT INTO items (id,session_id,run_id,ordinal,model_step_index,kind,status,content,created_at,completed_at) "
            "VALUES (?,?,?,2,1,'assistant_message','completed',?,2,2)",
            (item_id, session, run["id"], text),
        )
        connection.execute(
            "UPDATE runs SET status='succeeded',completed_at=2 WHERE id=?", (run["id"],)
        )
    return str(run["id"]), item_id


def request(
    session: str, item: str, operation: str | None = None
) -> SessionForkRequest:
    return SessionForkRequest.model_validate(
        {
            "operationId": operation or str(uuid4()),
            "sourceSessionId": session,
            "sourceItemId": item,
            "target": {"kind": "same_workspace"},
        }
    )


def fork(store: SessionStore, req: SessionForkRequest) -> str:
    return (
        SessionHistoryRepository(store.database)
        .fork(req, session_id=str(uuid4()))
        .value
    )


def test_protocol_uses_the_shared_strict_fixture():
    import json
    from pydantic import ValidationError
    from eidos_runtime.protocol.session_fork import SessionForkResponse

    fixture = json.loads(
        (
            Path(__file__).parents[2] / "protocol/fixtures/session-fork-v1.json"
        ).read_text()
    )
    for name, model in (
        ("Requests", SessionForkRequest),
        ("Responses", SessionForkResponse),
    ):
        for value in fixture["valid" + name]:
            model.model_validate(value)
        for value in fixture["invalid" + name]:
            with pytest.raises(ValidationError):
                model.model_validate(value)


def test_cancel_scope_and_limit_fail_without_publishing_history(store, monkeypatch):
    from eidos_runtime.persistence.session_history import (
        HistoryCaptureCancelled,
        HistoryLimitExceeded,
    )

    value, source = store
    _, item = answer(value, source, "answer")
    other = value.create_session(value.read_session(source)["workspaceRoot"])
    with pytest.raises(ResourceNotFoundError):
        fork(value, request(other["id"], item))
    with pytest.raises(HistoryCaptureCancelled):
        SessionHistoryRepository(value.database).fork(
            request(source, item), session_id=str(uuid4()), cancelled=lambda: True
        )
    monkeypatch.setattr(
        "eidos_runtime.persistence.session_history.MAX_HISTORY_ITEMS", 1
    )
    with pytest.raises(HistoryLimitExceeded):
        fork(value, request(source, item))
    assert value.connection.execute("SELECT count(*) FROM sessions").fetchone()[0] == 2
    assert (
        value.connection.execute(
            "SELECT count(*) FROM session_history_entries"
        ).fetchone()[0]
        == 0
    )


def test_history_excerpt_can_reference_inherited_messages_without_the_source(store):
    from eidos_runtime.persistence.input_context import InputContextRepository

    value, source = store
    _, item = answer(value, source, "original")
    target = fork(value, request(source, item))
    inherited_id = value.read_session_snapshot(target)["inheritedHistory"][-1]["item"][
        "id"
    ]
    value.delete_session(source)
    _, excerpt = InputContextRepository(value.database).history_excerpt(
        target, [inherited_id]
    )
    assert "original" in excerpt
    assert "question original" not in excerpt
    with pytest.raises(ValueError, match="history_selection_unavailable"):
        InputContextRepository(value.database).history_excerpt(target, [item])


def test_tool_batch_replays_in_order_and_inherited_results_remain_readable(store):
    import hashlib
    import json
    import threading
    from pydantic_ai.messages import (
        ModelResponse as PAIResponse,
        ModelRequest,
        ToolReturnPart,
    )
    from eidos_runtime.context.builder import ContextBuilder
    from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel
    from eidos_runtime.model.pydantic_ai_client import encode_context
    from eidos_runtime.runtime.engine import RuntimeEngine

    value, source = store
    run, _ = value.create_run(source, "list")
    model = ScriptedModel(
        [
            ModelResponse(
                text="progress",
                tool_calls=(
                    ModelToolCall("one", "list_files", {}),
                    ModelToolCall("two", "list_files", {}),
                ),
            ),
            ModelResponse(text="answer"),
        ]
    )
    RuntimeEngine(value, model, lambda _: None).run(run["id"], threading.Event())
    selected = value.read_session_snapshot(source)["items"][-1]
    target = fork(value, request(source, selected["id"]))
    history = SessionHistoryRepository(value.database).page(target, limit=20).records
    progress = next(
        entry.item.id for entry in history if entry.item.content == "progress"
    )
    with pytest.raises(InvalidRunStateError, match="final answer"):
        fork(value, request(target, progress))
    tools = [entry.item.tool_call for entry in history if entry.item.tool_call]
    value.delete_session(source)
    for tool in tools:
        content = tool.result_json
        digest = hashlib.sha256(content.encode()).hexdigest()
        assert (
            value.read_tool_text(target, tool.id, "result", digest, 0).content
            == content
        )
        with pytest.raises(ResourceNotFoundError):
            value.read_tool_text(source, tool.id, "result", digest, 0)
    current, _ = value.create_run(target, "continue")
    messages = encode_context(ContextBuilder(value).build(current["id"]).model_context)
    batch = next(
        message
        for message in messages
        if isinstance(message, PAIResponse) and message.tool_calls
    )
    assert [call.tool_call_id for call in batch.tool_calls] == ["one", "two"]
    returns = [
        part.tool_call_id
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, ToolReturnPart)
    ]
    assert returns == ["one", "two"]
    assert (
        value.connection.execute("SELECT count(*) FROM tool_calls").fetchone()[0] == 0
    )
    assert "progress" in json.dumps(value.read_session_snapshot(target))


def test_prefix_is_independent_and_never_creates_an_executable_run(store):
    value, source = store
    answer(value, source, "first")
    _, selected = answer(value, source, "second")
    answer(value, source, "later")
    target = fork(value, request(source, selected))
    snapshot = value.read_session_snapshot(target)
    assert snapshot["runs"] == []
    assert snapshot["items"] == []
    history = snapshot["inheritedHistory"]
    assert [entry["item"].get("content") for entry in history] == [
        "question first",
        "first",
        "question second",
        "second",
    ]
    value.delete_session(source)
    from eidos_runtime.db.layout import collect_unreferenced_blobs

    collect_unreferenced_blobs(value.database, value.database.json_blobs)
    snapshot = value.read_session_snapshot(target)
    assert len(snapshot["inheritedHistory"]) == 4
    run, _ = value.enqueue_run(target, "continue")
    facts = value.context_projection_facts(str(run["id"]))
    assert [item.content for item in facts.items] == [
        "question first",
        "first",
        "question second",
        "second",
        "continue",
    ]


def test_replay_conflict_and_deleted_target_do_not_create_another_session(store):
    value, source = store
    _, item = answer(value, source, "answer")
    req = request(source, item)
    target = fork(value, req)
    assert fork(value, req) == target
    _, another = answer(value, source, "another")
    with pytest.raises(OperationConflictError):
        fork(value, request(source, another, req.operation_id))
    value.delete_session(target)
    with pytest.raises(ResourceNotFoundError):
        fork(value, req)


def test_nested_fork_survives_ancestor_deletion_and_history_paginates(store):
    value, source = store
    answer(value, source, "first")
    _, selected = answer(value, source, "second")
    target = fork(value, request(source, selected))
    page = value.read_session_snapshot(target, item_limit=2)
    assert [entry["item"].get("content") for entry in page["inheritedHistory"]] == [
        "question second",
        "second",
    ]
    older = value.read_session_snapshot(
        target, item_limit=2, before_item_id=page["previousItemId"]
    )
    assert [entry["item"].get("content") for entry in older["inheritedHistory"]] == [
        "question first",
        "first",
    ]
    value.delete_session(source)
    first_answer = older["inheritedHistory"][-1]["item"]["id"]
    child = fork(value, request(target, first_answer))
    value.delete_session(target)
    assert len(value.read_session_snapshot(child)["inheritedHistory"]) == 2


def test_active_and_incomplete_answers_cannot_be_forked(store):
    value, source = store
    run, item = answer(value, source, "answer")
    with value.database.transaction() as connection:
        connection.execute("UPDATE runs SET status='running' WHERE id=?", (run,))
    with pytest.raises(InvalidRunStateError):
        fork(value, request(source, item))
    with value.database.transaction() as connection:
        connection.execute("UPDATE runs SET status='succeeded' WHERE id=?", (run,))
        connection.execute("UPDATE items SET incomplete=1 WHERE id=?", (item,))
    with pytest.raises(InvalidRunStateError, match="completed answer"):
        fork(value, request(source, item))


def test_queued_later_input_is_excluded_even_if_it_was_inserted_before_the_answer(
    store,
):
    value, source = store
    run, _ = value.enqueue_run(source, "first")
    later, _ = value.enqueue_run(source, "queued later")
    item = str(uuid4())
    with value.database.transaction() as connection:
        connection.execute(
            "INSERT INTO items (id,session_id,run_id,ordinal,model_step_index,kind,status,content,created_at,completed_at) "
            "VALUES (?,?,?,2,1,'assistant_message','completed','answer',3,3)",
            (item, source, run["id"]),
        )
        connection.execute(
            "UPDATE runs SET status='succeeded' WHERE id=?", (run["id"],)
        )
    target = fork(value, request(source, item))
    entries = value.read_session_snapshot(target)["inheritedHistory"]
    assert [entry["item"]["content"] for entry in entries] == ["first", "answer"]
    assert value.read_run(str(later["id"]))["status"] == "queued"


def test_old_answer_fork_ignores_a_revision_after_the_cutoff(store):
    from eidos_runtime.persistence.response_actions import ResponseActionRepository

    value, source = store
    old_run, old_item = answer(value, source, "original")
    replacement, _ = value.enqueue_run(source, "replacement")
    ResponseActionRepository(value).record_revision(
        run_id=str(replacement["id"]), source_run_id=old_run, revision_kind="edit"
    )
    target = fork(value, request(source, old_item))
    assert [
        entry["item"]["content"]
        for entry in value.read_session_snapshot(target)["inheritedHistory"]
    ] == ["question original", "original"]


def test_input_snapshots_remain_owned_by_the_fork_after_source_deletion_and_gc(store):
    import base64
    import hashlib
    from eidos_runtime.domain.input_reference import InputReference, InputSnapshot
    from eidos_runtime.persistence.input_context import InputContextRepository
    from eidos_runtime.db.layout import collect_unreferenced_blobs

    value, source = store
    content = "original attachment " + "x" * 40000
    digest = hashlib.sha256(content.encode()).hexdigest()
    reference = InputReference(
        id=digest,
        kind="image",
        label="image.png",
        source="/old/image.png",
        sha256=digest,
        status="content",
        size=len(content),
    )
    snapshot = InputSnapshot(
        reference=reference,
        text=content,
        image=base64.b64encode(b"snapshot bytes").decode(),
        mime="image/png",
    )
    inputs = InputContextRepository(value.database)
    inputs.save(snapshot)
    run, item = answer(value, source, "with attachment")
    with value.database.transaction() as connection:
        connection.execute(
            "UPDATE items SET input_references_json=? WHERE run_id=? AND kind='user_message'",
            ("[" + reference.model_dump_json(by_alias=True) + "]", run),
        )
        connection.execute(
            "UPDATE input_references SET created_at=0 WHERE id=?", (digest,)
        )
    target = fork(value, request(source, item))
    value.delete_session(source)
    other_id = "a" * 64
    inputs.save(InputSnapshot(reference=reference.model_copy(update={"id": other_id})))
    collect_unreferenced_blobs(value.database, value.database.json_blobs)
    assert inputs.read(digest) == snapshot
    assert (
        value.read_session_snapshot(target)["inheritedHistory"][0]["item"][
            "references"
        ][0]["id"]
        == digest
    )


def test_model_replay_is_private_and_survives_fork_source_deletion_and_restart(store):
    import json
    import threading
    from eidos_runtime.context.builder import ContextBuilder
    from eidos_runtime.model.client import ModelResponse, ScriptedModel
    from eidos_runtime.model.replay import ModelReasoningPart
    from eidos_runtime.model.pydantic_ai_client import encode_context
    from eidos_runtime.runtime.engine import RuntimeEngine

    value, source = store
    run, _ = value.create_run(source, "hello")
    RuntimeEngine(
        value,
        ScriptedModel(
            [
                ModelResponse(
                    text="answer",
                    reasoning_parts=(
                        ModelReasoningPart(
                            content="private replay",
                            id="reasoning_content",
                            provider_name="deepseek",
                        ),
                    ),
                )
            ]
        ),
        lambda _: None,
    ).run(run["id"], threading.Event())
    selected = next(
        item
        for item in value.read_session_snapshot(source)["items"]
        if item["kind"] == "assistant_message"
    )
    target = fork(value, request(source, selected["id"]))
    assert "private replay" not in json.dumps(value.read_session_snapshot(target))
    value.delete_session(source)
    data = value.database.data_directory
    value.close()
    reopened = SessionStore(data)
    reopened.initialize()
    try:
        current, _ = reopened.create_run(target, "continue")
        messages = encode_context(
            ContextBuilder(reopened).build(current["id"]).model_context
        )
        assert "private replay" in str(messages)
    finally:
        reopened.close()


def test_storage_failure_rolls_back_session_history_event_and_operation(
    store, monkeypatch
):
    import sqlite3
    from eidos_runtime.runtime.fault_injection import injected_faults

    value, source = store
    _, item = answer(value, source, "answer")
    before = value.pending_outbox_count()

    class Failure:
        def hit(self, point):
            if point == "sqlite_commit_failure":
                raise sqlite3.OperationalError("fixture commit failure")

    with injected_faults(Failure()), pytest.raises(sqlite3.OperationalError):
        fork(value, request(source, item))
    assert value.connection.execute("SELECT count(*) FROM sessions").fetchone()[0] == 1
    assert (
        value.connection.execute(
            "SELECT count(*) FROM session_history_entries"
        ).fetchone()[0]
        == 0
    )
    assert value.pending_outbox_count() == before


def test_unknown_side_effect_evidence_does_not_block_a_frozen_history_fork(store):
    value, source = store
    run, item = answer(value, source, "answer")
    with value.database.transaction() as connection:
        connection.execute(
            "UPDATE runs SET reconciliation_required=1 WHERE id=?", (run,)
        )
    forked = fork(value, request(source, item))
    assert forked != source
    assert value.connection.execute("SELECT count(*) FROM sessions").fetchone()[0] == 2
    assert value.read_run(run)["reconciliationRequired"]


@pytest.mark.parametrize("online_bytes", [768 * 1024, 8 * 1024 * 1024])
def test_a_large_single_turn_keeps_its_recent_answer_within_context_budget(store, monkeypatch, online_bytes):
    monkeypatch.setattr("eidos_runtime.db.repositories.context.CONTEXT_PROJECTION_HARD_MAX_BYTES", online_bytes)
    value, source = store
    run, _ = value.enqueue_run(source, "one long turn")
    selected = None
    with value.database.transaction() as connection:
        for index in range(1, 61):
            selected = str(uuid4())
            connection.execute(
                "INSERT INTO items(id,session_id,run_id,ordinal,model_step_index,kind,status,content,created_at,completed_at) "
                "VALUES(?,?,?,?,?,'assistant_message','completed',?,2,2)",
                (
                    selected,
                    source,
                    run["id"],
                    index + 1,
                    index,
                    f"step {index}:" + "x" * 30000,
                ),
            )
        connection.execute(
            "UPDATE runs SET status='succeeded',completed_at=2 WHERE id=?", (run["id"],)
        )
    target = fork(value, request(source, selected))
    current, _ = value.create_run(target, "continue")
    facts = value.context_projection_facts(current["id"])
    assert any(
        item.content.startswith("step 60:")
        for item in facts.items
        if item.kind == "assistant_message"
    )
    assert facts.candidate_overflow == (online_bytes == 768 * 1024)
    if not facts.candidate_overflow:
        assert len(facts.items) == 62
    assert (
        value.connection.execute(
            "SELECT count(*) FROM session_history_entries WHERE session_id=?", (target,)
        ).fetchone()[0]
        == 61
    )


@pytest.mark.parametrize("online_bytes", [768 * 1024, 8 * 1024 * 1024])
def test_long_inherited_history_can_be_compacted_without_losing_stored_records(store, monkeypatch, online_bytes):
    from eidos_runtime.context.compactor import ContextCompactor

    monkeypatch.setattr("eidos_runtime.db.repositories.context.CONTEXT_PROJECTION_HARD_MAX_BYTES", online_bytes)
    value, source = store
    for index in range(45):
        _, item = answer(value, source, str(index) + "x" * 18000)
    target = fork(value, request(source, item))
    run, _ = value.create_run(target, "continue")
    facts = value.context_projection_facts(run["id"])
    assert facts.candidate_overflow == (online_bytes == 768 * 1024)
    summary = ContextCompactor(value).compact(run["id"], "pre_turn")
    assert summary.source_item_ids
    assert (
        value.connection.execute(
            "SELECT count(*) FROM session_history_entries WHERE session_id=?", (target,)
        ).fetchone()[0]
        == 90
    )


def test_inherited_long_assistant_text_can_be_paged_after_source_deletion(store):
    import hashlib
    value, source = store
    text = "完整回答" * 6000
    _, item = answer(value, source, text)
    target = fork(value, request(source, item))
    inherited = value.read_session_snapshot(target)["inheritedHistory"][-1]["item"]
    assert inherited["contentBytes"] == len(text.encode())
    item = inherited["id"]
    value.delete_session(source)
    digest = hashlib.sha256(text.encode()).hexdigest()
    offset = 0
    parts = []
    while offset < len(text):
        page = value.read_tool_text(target, item, "content", digest, offset)
        parts.append(page.content)
        offset = page.next_offset
    assert "".join(parts) == text

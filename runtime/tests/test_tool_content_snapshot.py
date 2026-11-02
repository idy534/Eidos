from __future__ import annotations

import base64
import io
import json
import sqlite3
import threading

from PIL import Image
from pydantic_ai.messages import BinaryContent, ToolReturnPart, UserPromptPart
import pytest

from eidos_runtime.context.builder import ContextBuilder
from eidos_runtime.context.budget import estimate_model_request_budget
from eidos_runtime.db.json_blobs import JsonBlobCorruptionError, JsonBlobReference
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.domain.tool_content import ToolBinaryContent, ToolContentSnapshot, ToolTextContent
from eidos_runtime.model.client import ModelProfileSnapshot, ModelResponse, ModelToolCall, ScriptedModel
from eidos_runtime.model.native_custom import encode_responses_context
from eidos_runtime.model.pydantic_ai_client import encode_context
from eidos_runtime.persistence.tool_content import ToolContentRepository
from eidos_runtime.runtime.engine import RuntimeEngine


@pytest.fixture
def store(tmp_path):
    value = SessionStore(tmp_path / "data")
    value.initialize()
    try:
        yield value
    finally:
        value.close()


def _profile() -> ModelProfileSnapshot:
    return ModelProfileSnapshot(
        provider_id="fixture", model_id="vision", context_window_tokens=131072,
        max_output_tokens=4096, request_timeout_seconds=10, supports_images=True,
        supports_tools=True, supports_json_schema_output=False, supports_reasoning=False,
    )


def _start(store, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = store.create_session(str(workspace))
    run, _ = store.create_run(session["id"], "validate", model_id="vision", model_profile=_profile())
    store.increment_model_step(run["id"])
    return workspace, session, run


def _commit(store, run, content, order=0, **statuses):
    item = store.create_tool_item(run["id"], 1, order, f"call-{order}", "validator", "{}")
    result = json.dumps({"toolName": "validator", "outcome": "success", "code": "ok", "summary": "Validated", "data": {"report": "passed"}})
    store.complete_tool_item_once_committed(
        item["id"], result, model_content=content,
        item_status=statuses.get("item_status", "completed"),
        tool_status=statuses.get("tool_status", "completed"),
    )
    return item


def _tool_results(context):
    return [item for item in context if item.get("type") == "tool_result"]


@pytest.mark.parametrize("mime", ["image/png", "audio/wav", "application/octet-stream"])
def test_saved_tool_observations_survive_source_deletion_restart_and_gc(store, tmp_path, mime):
    workspace, _, run = _start(store, tmp_path)
    source = workspace / "temporary-validation.bin"
    original = b"captured validation content"
    source.write_bytes(original)
    content = ToolContentSnapshot(items=(
        ToolTextContent(text="validation passed"),
        ToolBinaryContent.from_bytes(source.read_bytes(), mime),
    ))
    item = _commit(store, run, content)
    source.unlink()
    row = store.connection.execute("SELECT result_json, model_result_json, ui_result_json FROM tool_calls WHERE item_id=?", (item["id"],)).fetchone()
    assert "modelContentSnapshot" not in row["result_json"]
    assert "modelContentSnapshot" not in row["ui_result_json"]
    reference = json.loads(row["model_result_json"])["modelContentSnapshot"]
    store.collect_unreferenced_blobs()
    store.close()
    reopened = SessionStore(tmp_path / "data")
    try:
        reopened.initialize()
        reopened.collect_unreferenced_blobs()
        assert ToolContentRepository(reopened.database).read(reference) == content
        result = _tool_results(ContextBuilder(reopened).build(run["id"]).model_context)[0]
        assert base64.b64decode(result["toolContent"]["items"][1]["data"]) == original
        assert "modelContentSnapshot" not in result["result"]
        # Unsupported media remains saved, and does not become an invalid model request.
        encoded = encode_context((
            {"type": "tool_call", "name": "validator", "callId": "call-0", "arguments": "{}", "responseId": "response"},
            result,
        ), supports_images=False)
        assert all(isinstance(value, str) for value in encoded[-1].parts[0].content)
    finally:
        reopened.close()


def test_runtime_captures_image_before_a_later_step_deletes_it(store, tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "preview.png"
    output = io.BytesIO()
    Image.new("RGB", (2, 2), "blue").save(output, format="PNG")
    original = output.getvalue()
    source.write_bytes(original)
    session = store.create_session(str(workspace))
    run, _ = store.create_run(session["id"], "inspect", model_id="vision", model_profile=_profile())

    class DeletingModel(ScriptedModel):
        def complete(self, *args, **kwargs):
            if self._index == 1:
                source.unlink()
            return super().complete(*args, **kwargs)

    model = DeletingModel([
        ModelResponse(tool_calls=(ModelToolCall("image", "view_image", {"path": "preview.png"}),)),
        ModelResponse(tool_calls=(ModelToolCall("list", "list_files", {}),)),
        ModelResponse(text="done"),
    ])
    RuntimeEngine(store, model, lambda _: None).run(run["id"], threading.Event())
    assert store.read_run(run["id"])["status"] == "succeeded"
    assert not source.exists()
    for context in model.contexts[1:]:
        messages = encode_context(context, supports_images=True)
        binaries = [value for message in messages for part in message.parts if isinstance(part, ToolReturnPart) and isinstance(part.content, list) for value in part.content if isinstance(value, BinaryContent)]
        assert [value.data for value in binaries] == [original]
    attempts = store.read_model_attempts(run["id"])
    frozen = store.context_snapshot_repository().read_for_model_attempt(attempts[-1]["id"])
    assert frozen.model_context == model.contexts[-1]
    store.collect_unreferenced_blobs()
    assert store.context_snapshot_repository().read_for_model_attempt(attempts[-1]["id"]).model_context == frozen.model_context


def test_content_commit_failure_rolls_back_fact_and_event_and_gc_removes_orphan(store, tmp_path, monkeypatch):
    _, _, run = _start(store, tmp_path)
    item = store.create_tool_item(run["id"], 1, 0, "call-0", "validator", "{}")
    count = store.connection.execute("SELECT count(*) FROM events").fetchone()[0]

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("injected commit failure")

    monkeypatch.setattr("eidos_runtime.db.repositories.execution.append_event", fail)
    with pytest.raises(sqlite3.OperationalError):
        store.complete_tool_item_once_committed(
            item["id"], '{"outcome":"success"}', item_status="completed", tool_status="completed",
            model_content=ToolContentSnapshot(items=(ToolTextContent(text="report"),)),
        )
    row = store.connection.execute("SELECT status, model_result_json FROM tool_calls WHERE item_id=?", (item["id"],)).fetchone()
    assert tuple(row) == ("running", None)
    assert store.connection.execute("SELECT count(*) FROM events").fetchone()[0] == count
    store.collect_unreferenced_blobs()
    assert not list((store.data_directory / "blobs" / "tool-result-content").rglob("*.json.gz"))


def test_cancel_does_not_accept_a_late_content_snapshot(store, tmp_path):
    _, _, run = _start(store, tmp_path)
    item = store.create_tool_item(run["id"], 1, 0, "call-0", "validator", "{}")
    store.cancel_run(run["id"])
    mutation = store.complete_tool_item_once_committed(
        item["id"], '{"outcome":"success"}', item_status="completed", tool_status="completed",
        model_content=ToolContentSnapshot(items=(ToolTextContent(text="late report"),)),
    )
    assert not mutation.events
    row = store.connection.execute("SELECT model_result_json FROM tool_calls WHERE item_id=?", (item["id"],)).fetchone()
    assert "modelContentSnapshot" not in (row[0] or "")


def test_latest_content_is_selected_when_the_request_media_budget_is_full(store, tmp_path, monkeypatch):
    _, _, run = _start(store, tmp_path)
    old = ToolContentSnapshot(items=(ToolBinaryContent.from_bytes(b"old", "audio/wav"),))
    new = ToolContentSnapshot(items=(ToolBinaryContent.from_bytes(b"new", "audio/wav"),))
    _commit(store, run, old)
    _commit(store, run, new, order=1)
    monkeypatch.setattr("eidos_runtime.context.builder.MAX_CONTEXT_CONTENT_BYTES", len(new.model_dump_json(by_alias=True).encode()))
    results = _tool_results(ContextBuilder(store).build(run["id"]).model_context)
    assert results[0]["toolContentOmitted"] is True
    assert results[1]["toolContent"] == new.model_dump(mode="json", by_alias=True)


def test_missing_saved_content_fails_at_context_loading_without_source_fallback(store, tmp_path):
    _, _, run = _start(store, tmp_path)
    item = _commit(store, run, ToolContentSnapshot(items=(ToolTextContent(text="report"),)))
    stored = store.connection.execute("SELECT model_result_json FROM tool_calls WHERE item_id=?", (item["id"],)).fetchone()[0]
    reference = JsonBlobReference.from_json(json.loads(stored)["modelContentSnapshot"])
    (store.database.json_blobs.root / reference.relative_path).unlink()
    with pytest.raises(JsonBlobCorruptionError):
        ContextBuilder(store).build(run["id"])


def test_media_payload_is_not_counted_as_base64_text_in_context_budget():
    def budget(size):
        snapshot = ToolContentSnapshot(items=(ToolBinaryContent.from_bytes(b"x" * size, "image/png"),))
        return estimate_model_request_budget(({
            "type": "tool_result", "callId": "image", "name": "validator", "result": "ok",
            "toolContent": snapshot.model_dump(mode="json", by_alias=True),
        },), instructions="", tool_definitions=(), context_window_tokens=131072, request_max_output_tokens=4096)
    assert budget(32).estimated_input_tokens == budget(1024 * 1024).estimated_input_tokens


def test_responses_api_uses_the_same_saved_content_for_function_and_custom_outputs():
    snapshot = ToolContentSnapshot(items=(ToolBinaryContent.from_bytes(b"image", "image/png"),)).model_dump(mode="json", by_alias=True)
    for payload_kind in ("function", "custom"):
        output = encode_responses_context(({
            "type": "tool_result", "callId": "image", "name": "validator", "result": "ok",
            "payloadKind": payload_kind, "toolContent": snapshot,
        },), supports_images=True)[0]
        assert output["output"][1] == {"type": "input_image", "image_url": "data:image/png;base64,aW1hZ2U="}


def test_chat_custom_tool_history_retains_saved_content():
    snapshot = ToolContentSnapshot(items=(ToolBinaryContent.from_bytes(b"image", "image/png"),)).model_dump(mode="json", by_alias=True)
    messages = encode_context((
        {"type": "tool_call", "callId": "custom", "name": "validator", "payloadKind": "custom", "input": "validate", "responseId": "response"},
        {"type": "tool_result", "callId": "custom", "name": "validator", "payloadKind": "custom", "result": "ok", "toolContent": snapshot},
    ), supports_images=True)
    prompt = messages[-1].parts[0]
    assert isinstance(prompt, UserPromptPart)
    assert next(value.data for value in prompt.content if isinstance(value, BinaryContent)) == b"image"

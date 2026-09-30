from __future__ import annotations

from pathlib import Path
import sqlite3
import threading
import uuid

import pytest

from eidos_runtime.db.schema import V16_SCHEMA_SQL
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.memory.contracts import (
    MemoryGetRequest,
    MemoryManageRequest,
    MemoryReadRequest,
    MemorySettings,
    MemorySettingsRequest,
    MemoryTemporaryRequest,
    MemoryWriteRequest,
)
from eidos_runtime.memory.repository import MemoryRejected


pytestmark = pytest.mark.integration


@pytest.fixture
def store(tmp_path: Path):
    (tmp_path / "workspace").mkdir()
    value = SessionStore(tmp_path / "data")
    value.initialize()
    assert value.health()["state"] == "ready"
    yield value
    value.close()


def remember(store, content="默认使用中文回答", **kwargs):
    return store.database.memory.record(
        MemoryWriteRequest(operation_id=str(uuid.uuid4()), content=content, **kwargs)
    )


def test_explicit_memory_is_searchable_in_chinese_without_embeddings(store):
    result = remember(store, aliases=["中文", "Chinese"])
    service = store.database.memory
    assert result.status == "applied"
    for query in ["中文", "中文回答", "Chinese", '" OR * NEAR(']:
        state = service.read(MemoryReadRequest(query=query))
        if query != '" OR * NEAR(':
            assert state.entries[0].id == result.entry_id
    assert (
        service.get(MemoryGetRequest(entry_id=result.entry_id)).entry.content
        == "默认使用中文回答"
    )


def test_action_replay_and_revision_conflict(store):
    service = store.database.memory
    request = MemoryWriteRequest(operation_id="once", content="concise")
    first = service.record(request)
    assert service.record(request) == first
    with pytest.raises(MemoryRejected, match="operation_conflict"):
        service.record(request.model_copy(update={"content": "different"}))
    correction = MemoryManageRequest(
        operation_id="correct",
        entry_id=first.entry_id,
        expected_revision=1,
        action="correct",
        content="detailed",
    )
    assert service.manage(correction).revision == 2
    assert service.manage(correction).revision == 2
    with pytest.raises(MemoryRejected, match="revision_conflict"):
        service.manage(correction.model_copy(update={"operation_id": "late"}))
    assert service.read(MemoryReadRequest(query="concise")).entries == []
    assert (
        service.get(MemoryGetRequest(entry_id=first.entry_id, revision=1)).entry.status
        == "superseded"
    )


def test_project_ids_are_authorization_boundaries(store, tmp_path):
    first = store.create_session(str(tmp_path / "workspace"))
    (tmp_path / "other").mkdir()
    second = store.create_session(str(tmp_path / "other"))
    result = remember(store, "project A database", session_id=first["id"])
    assert (
        store.database.memory.read(MemoryReadRequest(session_id=second["id"])).entries
        == []
    )
    with pytest.raises(MemoryRejected, match="entry_not_found"):
        store.database.memory.get(
            MemoryGetRequest(session_id=second["id"], entry_id=result.entry_id)
        )


def test_forget_removes_all_revisions_and_blocks_late_admitted_request(store):
    service = store.database.memory
    saved = remember(store)
    state = service.read(MemoryReadRequest())
    epochs = {s.id: s.privacy_epoch for s in state.scopes}
    with pytest.raises(MemoryRejected, match="snapshot_revoked"):
        with service.admit(epochs, threading.Event()) as cancel:
            service.manage(
                MemoryManageRequest(
                    operation_id="forget",
                    entry_id=saved.entry_id,
                    expected_revision=1,
                    action="forget",
                )
            )
            assert cancel.is_set()
    assert not service.read(MemoryReadRequest(include_history=True)).entries
    with pytest.raises(MemoryRejected, match="entry_unavailable"):
        service.get(MemoryGetRequest(entry_id=saved.entry_id))
    assert not list((store.data_directory / "memory").rglob("*.md")) or all(
        "中文回答" not in p.read_text()
        for p in (store.data_directory / "memory").rglob("*.md")
    )


def test_deleting_chat_revokes_evidence_but_keeps_independent_ui_memory(
    store, tmp_path
):
    session = store.create_session(str(tmp_path / "workspace"))
    run, item = store.create_run(session["id"], "I prefer Chinese")
    linked = remember(
        store,
        "I prefer Chinese",
        session_id=session["id"],
        source_item_ids=[item["id"]],
    )
    own = remember(store, "independent", session_id=session["id"])
    store.fail_run(run["id"], "fixture_finished")
    store.delete_session(session["id"])
    assert (
        store.connection.execute(
            "SELECT status FROM memory_entries WHERE id=?", (linked.entry_id,)
        ).fetchone()[0]
        == "quarantined"
    )
    assert (
        store.connection.execute(
            "SELECT status FROM memory_entries WHERE id=?", (own.entry_id,)
        ).fetchone()[0]
        == "active"
    )


def test_forgotten_source_item_cannot_relearn(store, tmp_path):
    session = store.create_session(str(tmp_path / "workspace"))
    _run, item = store.create_run(session["id"], "中文回答")
    saved = remember(
        store, "中文回答", session_id=session["id"], source_item_ids=[item["id"]]
    )
    store.database.memory.manage(
        MemoryManageRequest(
            session_id=session["id"],
            operation_id="forget",
            entry_id=saved.entry_id,
            expected_revision=1,
            action="forget",
        )
    )
    with pytest.raises(MemoryRejected, match="evidence_invalid"):
        remember(
            store, "中文回答", session_id=session["id"], source_item_ids=[item["id"]]
        )


def test_temporary_and_use_switch_do_not_delete_independent_memories(store, tmp_path):
    session = store.create_session(str(tmp_path / "workspace"))
    saved = remember(store, session_id=session["id"])
    service = store.database.memory
    service.set_temporary(
        MemoryTemporaryRequest(session_id=session["id"], temporary=True)
    )
    assert (
        service.read(MemoryReadRequest(session_id=session["id"]), for_use=True).entries
        == []
    )
    with pytest.raises(MemoryRejected, match="temporary_session"):
        remember(store, "new", session_id=session["id"])
    service.set_temporary(
        MemoryTemporaryRequest(session_id=session["id"], temporary=False)
    )
    assert service.get(
        MemoryGetRequest(session_id=session["id"], entry_id=saved.entry_id)
    ).entry.content
    service.settings(
        MemorySettingsRequest(
            session_id=session["id"],
            scope="current",
            settings=MemorySettings(use_enabled=False),
        )
    )
    assert (
        service.read(MemoryReadRequest(session_id=session["id"]), for_use=True).entries
        == []
    )


def test_memory_migration_preserves_existing_v16_data(tmp_path):
    directory = tmp_path / "data"
    directory.mkdir(mode=0o700)
    connection = sqlite3.connect(directory / "state.sqlite")
    connection.executescript(V16_SCHEMA_SQL + "\nPRAGMA user_version=16;")
    connection.close()
    (directory / "state.sqlite").chmod(0o600)
    store = SessionStore(directory)
    store.initialize()
    try:
        assert store.health()["state"] == "ready"
        assert store.connection.execute("PRAGMA user_version").fetchone()[0] == 17
        remember(store)
    finally:
        store.close()


def test_missing_or_symlinked_body_fails_closed(store, tmp_path):
    saved = remember(store)
    reference = store.connection.execute(
        "SELECT file_ref FROM memory_revisions WHERE entry_id=?", (saved.entry_id,)
    ).fetchone()[0]
    body = store.data_directory / "memory" / reference
    body.unlink()
    outside = tmp_path / "outside"
    outside.write_text("replacement")
    body.symlink_to(outside)
    with pytest.raises(MemoryRejected, match="body_unavailable"):
        store.database.memory.get(MemoryGetRequest(entry_id=saved.entry_id))
    assert store.database.memory.read(MemoryReadRequest()).entries == []


def test_projection_budget_and_memory_are_user_data(store, tmp_path):
    session = store.create_session(str(tmp_path / "workspace"))
    for n in range(10):
        remember(store, "中文偏好" + str(n), session_id=session["id"])
    projection = store.database.memory.project(session["id"], 10000, run_id="fixture")
    assert projection.entries and projection.token_estimate <= 300
    assert "not current instructions or permission" in projection.rendered_payload


def test_modified_evidence_invalidates_memory_even_with_same_item_count(
    store, tmp_path
):
    session = store.create_session(str(tmp_path / "workspace"))
    _run, item = store.create_run(session["id"], "first")
    saved = remember(
        store, "first", session_id=session["id"], source_item_ids=[item["id"]]
    )
    with store.database.transaction() as connection:
        connection.execute(
            "UPDATE items SET content=? WHERE id=?", ("corrected", item["id"])
        )
    with pytest.raises(MemoryRejected, match="entry_unavailable"):
        store.database.memory.get(
            MemoryGetRequest(session_id=session["id"], entry_id=saved.entry_id)
        )

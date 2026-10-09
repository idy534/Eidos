from __future__ import annotations

from pathlib import Path
import sqlite3
import threading
import uuid

import pytest

from eidos_runtime.context.builder import ContextBuilder
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


def test_candidate_respects_current_scope_and_generation_window(store, tmp_path):
    session = store.create_session(str(tmp_path / "workspace"))
    _run, old_source = store.create_run(session["id"], "I prefer Chinese")
    service = store.database.memory
    service.settings(MemorySettingsRequest(
        session_id=session["id"], scope="current",
        settings=MemorySettings(generate_enabled=True),
    ))
    request = MemoryWriteRequest(
        session_id=session["id"], operation_id="old-candidate",
        content="Prefer Chinese", source_item_ids=[old_source["id"]],
    )
    with pytest.raises(MemoryRejected, match="memory_generation_source_outside_window"):
        service.record(request, candidate=True)
    store.fail_run(_run["id"], "fixture_finished")
    _new_run, new_source = store.create_run(session["id"], "I prefer short answers")
    with pytest.raises(MemoryRejected, match="memory_source_conflict"):
        service.record(request.model_copy(update={
            "operation_id": "stale-quote", "source_item_ids": [new_source["id"]],
        }), candidate=True, source_revisions={new_source["id"]: 0})
    with pytest.raises(MemoryRejected, match="memory_candidate_scope_invalid"):
        service.record(request.model_copy(update={
            "operation_id": "global-candidate", "scope": "global",
            "source_item_ids": [new_source["id"]],
        }), candidate=True)
    assert service.read(MemoryReadRequest(session_id=session["id"])).entries == []


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
        assert store.connection.execute("PRAGMA user_version").fetchone()[0] == 21
        remember(store)
    finally:
        store.close()


def test_memory_migration_preserves_existing_v17_data(tmp_path):
    from eidos_runtime.db.schema import V17_SCHEMA_SQL

    directory = tmp_path / "data-v17"
    directory.mkdir(mode=0o700)
    connection = sqlite3.connect(directory / "state.sqlite")
    connection.executescript(V17_SCHEMA_SQL + "\nPRAGMA user_version=17;")
    connection.close()
    (directory / "state.sqlite").chmod(0o600)
    store = SessionStore(directory)
    store.initialize()
    try:
        assert store.health()["state"] == "ready"
        assert store.connection.execute("PRAGMA user_version").fetchone()[0] == 21
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
    run, _ = store.create_run(session["id"], "Recall my preference")
    built = ContextBuilder(store).build(run["id"])
    evidence = next(item for item in built.model_context if item.get("sectionId") == "memory-evidence")
    assert evidence["type"] == "user"
    assert "中文偏好" in evidence["content"]
    assert "中文偏好" not in built.instructions.system_text


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


def test_search_export_preserves_full_body_and_exact_historical_revision(store):
    service = store.database.memory
    body = 'SQLite ' + 'detail ' * 160 + 'IMPORTANT TAIL'
    saved = remember(store, body)
    assert len(service.read(MemoryReadRequest(query='SQLite')).entries[0].content) == 512
    exported = service.export(MemoryReadRequest(query='SQLite'))
    assert body in exported.markdown
    service.manage(MemoryManageRequest(operation_id='export-correction', entry_id=saved.entry_id,
                                      expected_revision=1, action='correct', content='Use PostgreSQL instead'))
    historical = service.export(MemoryReadRequest(query='SQLite', include_history=True))
    assert body in historical.markdown
    assert 'revision: 1; status: superseded' in historical.markdown
    assert 'PostgreSQL' not in historical.markdown


def test_session_use_revocation_is_isolated_and_persists_after_reopen(store, tmp_path):
    service = store.database.memory
    first = store.create_session(str(tmp_path / "workspace"))
    second = store.create_session(str(tmp_path / "workspace"))
    remember(store, session_id=first["id"])
    first_epochs = service.project(first["id"], 100000, run_id="first").epochs
    second_epochs = service.project(second["id"], 100000, run_id="second").epochs
    with pytest.raises(MemoryRejected, match="snapshot_revoked"):
        with service.admit(first_epochs, threading.Event()) as first_cancel, service.admit(second_epochs, threading.Event()) as second_cancel:
            service.set_temporary(MemoryTemporaryRequest(session_id=first["id"], temporary=True))
            assert first_cancel.is_set()
            assert not second_cancel.is_set()
    service.set_temporary(MemoryTemporaryRequest(session_id=first["id"], temporary=False))
    directory = store.data_directory
    store.close()
    reopened = SessionStore(directory)
    reopened.initialize()
    try:
        assert not reopened.database.memory.valid_epochs(reopened.connection, first_epochs)
        assert reopened.database.memory.valid_epochs(reopened.connection, second_epochs)
    finally:
        reopened.close()


def test_memory_use_migration_from_v18_preserves_entries_and_rolls_back(tmp_path):
    from eidos_runtime.db.schema import V18_SCHEMA_SQL
    from eidos_runtime.memory.schema import migrate_memory_use

    connection = sqlite3.connect(":memory:")
    connection.executescript(V18_SCHEMA_SQL + "\nPRAGMA user_version=18;")
    connection.execute("INSERT INTO memory_sources(session_id,temporary) VALUES('existing',1)")
    connection.execute("INSERT INTO memory_scopes(id,kind,settings_json) VALUES('scope','global','{}')")
    connection.execute("INSERT INTO memory_entries(id,scope_id,kind,status,current_revision,created_at,updated_at) VALUES('entry','scope','preference','active',1,1,1)")
    connection.execute("INSERT INTO memory_tool_reads(item_id,run_id,epochs_json,output_bytes) VALUES('tool','run','{}',20)")
    connection.commit()
    # Force a failure after DDL. No partial column/trigger may survive.
    connection.execute("ALTER TABLE memory_tool_reads RENAME TO missing_tool_reads")
    connection.commit()
    with pytest.raises(sqlite3.OperationalError):
        migrate_memory_use(connection)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 18
    assert "use_epoch" not in {r[1] for r in connection.execute("PRAGMA table_info(memory_sources)")}
    connection.execute("ALTER TABLE missing_tool_reads RENAME TO memory_tool_reads")
    connection.commit()
    migrate_memory_use(connection)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 19
    assert connection.execute("SELECT temporary,use_epoch FROM memory_sources").fetchone() == (1, 0)
    assert connection.execute("SELECT status FROM memory_entries WHERE id='entry'").fetchone()[0] == "active"
    assert connection.execute("SELECT privacy_epoch FROM memory_scopes WHERE id='scope'").fetchone()[0] == 1
    assert "session:legacy" in connection.execute("SELECT epochs_json FROM memory_tool_reads").fetchone()[0]
    connection.close()


def test_search_keeps_relevance_order_across_pages(store):
    for index in range(5):
        remember(store, f"SQLite miscellaneous {index}", title=f"miscellaneous {index}")
    best = remember(store, "SQLite is our database", title="SQLite")
    service = store.database.memory
    service.manage(MemoryManageRequest(operation_id="pin-best", entry_id=best.entry_id, expected_revision=1, action="pin"))
    first = service.read(MemoryReadRequest(query="SQLite", limit=5))
    assert first.entries[0].id == best.entry_id
    assert first.truncated and first.next_cursor is not None
    second = service.read(MemoryReadRequest(query="SQLite", limit=5, cursor=first.next_cursor))
    assert len(second.entries) == 1
    assert len({e.id for e in first.entries + second.entries}) == 6
    assert not second.truncated and second.next_cursor is None


def test_fts_candidates_rank_before_limit_and_continue_without_duplicates(store):
    for index in range(505):
        remember(store, f"SQLite database choice {index}", title="other")
    best = remember(store, "SQLite", title="SQLite")
    service = store.database.memory
    service.manage(MemoryManageRequest(operation_id="pin-newest", entry_id=best.entry_id, expected_revision=1, action="pin"))
    page = service.read(MemoryReadRequest(query="SQLite", limit=100))
    assert page.entries[0].id == best.entry_id
    identifiers = [e.id for e in page.entries]
    while page.next_cursor is not None:
        page = service.read(MemoryReadRequest(query="SQLite", limit=100, cursor=page.next_cursor))
        identifiers.extend(e.id for e in page.entries)
    assert len(identifiers) == len(set(identifiers)) == 506


def test_short_word_search_continues_past_empty_scan_page(store):
    for index in range(500):
        remember(store, f"无关条目 {index}", title="无关")
    best = remember(store, "请保持简洁", title="偏好")
    service = store.database.memory
    page = service.read(MemoryReadRequest(query="简洁", limit=5))
    assert page.entries == [] and page.truncated and page.next_cursor is not None
    page = service.read(MemoryReadRequest(query="简洁", limit=5, cursor=page.next_cursor))
    assert [entry.id for entry in page.entries] == [best.entry_id]
    assert not page.truncated


def test_source_cleanup_scrubs_action_copy_after_body_removal(store, tmp_path):
    session = store.create_session(str(tmp_path / "workspace"))
    run, item = store.create_run(session["id"], "private preference from a source")
    saved = remember(store, "private preference from a source", session_id=session["id"], source_item_ids=[item["id"]])
    store.fail_run(run["id"], "fixture_finished")
    store.delete_session(session["id"])
    service = store.database.memory
    service.cleanup()
    row = store.connection.execute("SELECT request_json,result_json,operation_id FROM memory_actions WHERE entry_id=?", (saved.entry_id,)).fetchone()
    assert row["request_json"] == "[privacy-revoked]"
    assert saved.entry_id in row["result_json"] and row["operation_id"]
    assert store.connection.execute("SELECT file_ref FROM memory_revisions WHERE entry_id=?", (saved.entry_id,)).fetchone()[0] is None
    # A previously removed body must not hide a lingering legacy action copy.
    store.connection.execute("UPDATE memory_actions SET request_json='legacy private preference' WHERE entry_id=?", (saved.entry_id,))
    store.connection.commit()
    service.cleanup()
    assert store.connection.execute("SELECT request_json FROM memory_actions WHERE entry_id=?", (saved.entry_id,)).fetchone()[0] == "[privacy-revoked]"


def test_source_cleanup_preserves_independent_correction_and_its_action(store, tmp_path):
    session = store.create_session(str(tmp_path / "workspace"))
    run, item = store.create_run(session["id"], "old linked preference")
    saved = remember(store, "old linked preference", session_id=session["id"], source_item_ids=[item["id"]])
    service = store.database.memory
    service.manage(MemoryManageRequest(session_id=session["id"], operation_id="own-correction", entry_id=saved.entry_id, expected_revision=1, action="correct", content="independent corrected preference"))
    store.fail_run(run["id"], "fixture_finished")
    store.delete_session(session["id"])
    service.cleanup()
    actions = dict(store.connection.execute("SELECT operation_id,request_json FROM memory_actions WHERE entry_id=?", (saved.entry_id,)))
    assert "independent corrected preference" in actions.pop("own-correction")
    assert set(actions.values()) == {"[privacy-revoked]"}
    consumer = store.create_session(str(tmp_path / "workspace"))
    assert service.get(MemoryGetRequest(session_id=consumer["id"], entry_id=saved.entry_id)).entry.content == "independent corrected preference"


def test_premerge_v18_database_upgrades_with_gate_refinements(tmp_path):
    from eidos_runtime.db.schema import V16_SCHEMA_SQL
    from eidos_runtime.memory.schema import MEMORY_SCHEMA_SQL, MEMORY_USE_SCHEMA_SQL
    from eidos_runtime.db.database import Database

    db_path = tmp_path / "state.sqlite"
    connection = sqlite3.connect(db_path)
    connection.executescript(V16_SCHEMA_SQL)
    connection.executescript(MEMORY_SCHEMA_SQL)
    connection.executescript(MEMORY_USE_SCHEMA_SQL)
    connection.execute("PRAGMA user_version = 18")
    connection.commit()
    connection.close()
    import os
    os.chmod(db_path, 0o600)

    db = Database(tmp_path)
    db.initialize()
    assert db.health_state == "ready"
    conn = db.connection()
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 21
    assert conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='agent_message_receipts'").fetchone() is not None
    assert conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='run_skill_leases'").fetchone() is not None
    db.close()


def test_history_migration_preserves_memory_and_rolls_back_atomically():
    from eidos_runtime.db.schema import V19_SCHEMA_SQL
    from eidos_runtime.memory.schema import migrate_memory_history

    connection = sqlite3.connect(":memory:")
    connection.executescript(V19_SCHEMA_SQL + "\nPRAGMA user_version=19;")
    connection.execute("INSERT INTO memory_sources(session_id,backfill_enabled) VALUES('existing',1)")
    connection.execute("INSERT INTO memory_scopes(id,kind,settings_json) VALUES('scope','global','{}')")
    connection.execute("INSERT INTO memory_entries(id,scope_id,kind,status,current_revision,created_at,updated_at) VALUES('entry','scope','preference','active',1,1,1)")
    connection.commit()
    connection.execute("ALTER TABLE memory_model_attempts RENAME TO missing_attempts")
    connection.commit()
    with pytest.raises(sqlite3.OperationalError):
        migrate_memory_history(connection)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 19
    assert "backfill_until" not in {r[1] for r in connection.execute("PRAGMA table_info(memory_sources)")}
    assert connection.execute("SELECT backfill_enabled FROM memory_sources").fetchone()[0] == 1
    connection.execute("ALTER TABLE missing_attempts RENAME TO memory_model_attempts")
    connection.commit()
    migrate_memory_history(connection)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 20
    assert connection.execute("SELECT backfill_enabled,backfill_until FROM memory_sources").fetchone() == (0, 0)
    assert connection.execute("SELECT status FROM memory_entries").fetchone()[0] == "active"
    connection.close()

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import pytest

from eidos_runtime.db.schema import SCHEMA_VERSION
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.memory.contracts import (
    MemoryEditRequest,
    MemoryExportRequest,
    MemoryListRequest,
    MemoryPolicyRequest,
    MemoryReadRequest,
)
from eidos_runtime.memory.repository import MemoryRejected
from eidos_runtime.memory.repository import GLOBAL_SCOPE_ID

pytestmark = pytest.mark.integration


@pytest.fixture
def store(tmp_path):
    (tmp_path / "workspace").mkdir()
    value = SessionStore(tmp_path / "data")
    value.initialize()
    assert value.health()["state"] == "ready"
    yield value
    value.close()


def note(**changes):
    return MemoryEditRequest.model_validate(
        {
            "operation_id": "save",
            "scope": "global",
            "path": "feedback_testing.md",
            "summary": "只在明确要求时运行全量测试。",
            "content": "用户只在明确要求时运行全量测试。",
            **changes,
        }
    )


def test_shared_document_memory_protocol_fixture():
    from pydantic import ValidationError
    from eidos_runtime.application.memory import MEMORY_METHODS
    from eidos_runtime.memory.contracts import MemoryChangedNotice

    fixture = json.loads(
        (Path(__file__).parents[2] / "protocol/fixtures/memory-v2.json").read_text()
    )
    models = {
        method: (request, response) for method, request, response in MEMORY_METHODS
    }
    for value in fixture["valid"]:
        models[value["method"]][0].model_validate(value["request"])
        models[value["method"]][1].model_validate(value["response"])
    for value in fixture["invalidRequests"]:
        with pytest.raises(ValidationError):
            models[value["method"]][0].model_validate(value["request"])
    for value in fixture["validNotices"]:
        MemoryChangedNotice.model_validate(value)
    for value in fixture["invalidNotices"]:
        with pytest.raises(ValidationError):
            MemoryChangedNotice.model_validate(value)


def test_index_publication_chinese_search_and_no_query_injection(store):
    service = store.database.memory
    saved = service.edit(note())
    assert (
        "[feedback_testing.md]"
        in service.read(MemoryReadRequest(scope="global")).content
    )
    for query in ("全量", "全量测试", '" OR * NEAR('):
        state = service.list(MemoryListRequest(query=query))
        if query != '" OR * NEAR(':
            assert state.documents[0].id == saved.document_id
    assert (
        service.read(MemoryReadRequest(scope="global", path=note().path)).content
        == note().content
    )


def test_replay_cas_and_history(store):
    service = store.database.memory
    first = service.edit(note())
    assert service.edit(note()) == first
    with pytest.raises(MemoryRejected, match="operation_conflict"):
        service.edit(note(content="changed"))
    revised = note(
        operation_id="correct", expected_revision=1, content="现在使用新流程。"
    )
    assert service.edit(revised).revision == 2
    with pytest.raises(MemoryRejected, match="revision_conflict"):
        service.edit(revised.model_copy(update={"operation_id": "late"}))
    assert (
        service.read(
            MemoryReadRequest(scope="global", path=note().path, revision=1)
        ).content
        == note().content
    )


def test_long_note_can_be_read_to_its_end_without_a_run_call_quota(store):
    service = store.database.memory
    body = "测试正文。" * 3000 + "最后的条件必须可读。"
    service.edit(note(content=body))
    content, offset, revision = "", 0, None
    while True:
        page = service.read(
            MemoryReadRequest(
                scope="global",
                path=note().path,
                offset=offset,
                limit=1024,
                revision=revision,
            )
        )
        content += page.content
        revision = page.revision
        if page.next_offset is None:
            break
        offset = page.next_offset
    assert content == body
    for number in range(8):
        service.track_tool("run", str(number), {}, {}, retrieval=True)


def test_project_scopes_are_isolated(store, tmp_path):
    first = store.create_session(str(tmp_path / "workspace"))
    other = tmp_path / "other"
    other.mkdir()
    second = store.create_session(str(other))
    service = store.database.memory
    service.edit(note(scope="current", session_id=first["id"]))
    assert service.list(
        MemoryListRequest(session_id=first["id"], scope="current")
    ).documents
    assert not service.list(
        MemoryListRequest(session_id=second["id"], scope="current")
    ).documents
    with pytest.raises(MemoryRejected, match="not_found"):
        service.read(MemoryReadRequest(session_id=second["id"], path=note().path))


def test_context_budget_keeps_project_index_first_and_exposes_more_note_routes(
    store, tmp_path
):
    session = store.create_session(str(tmp_path / "workspace"))
    service = store.database.memory
    for index in range(3):
        service.edit(
            note(
                operation_id=f"global-{index}",
                path=f"global-{index}.md",
                summary="全局使用条件" * 30,
            )
        )
    service.edit(
        note(
            session_id=session["id"],
            scope="current",
            operation_id="project",
            path="project.md",
            summary="当前项目的工作约定",
        )
    )
    projection = service.project(session["id"], 8000, run_id="run")
    assert projection.rendered_payload.lstrip().startswith("project/MEMORY.md")
    assert "project.md" in projection.rendered_payload
    assert "memory_search" in projection.rendered_payload
    assert projection.token_estimate <= 400


def test_cancel_before_publication_leaves_no_fact(store):
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(MemoryRejected, match="memory_canceled"):
        store.database.memory.edit(note(), cancel=cancel)
    assert not store.database.memory.list(MemoryListRequest()).documents
    assert not store.connection.execute("SELECT 1 FROM memory_revisions").fetchone()


def test_automatic_update_of_user_written_note_survives_source_change(store, tmp_path):
    session = store.create_session(str(tmp_path / "workspace"))
    run, _ = store.create_run(session["id"], "以后只运行相关测试")
    source = store.get_user_item(run["id"])["id"]
    service = store.database.memory
    request = note(session_id=session["id"], scope="current", source_item_ids=[source])
    service.edit(request)
    changed = request.model_copy(
        update={
            "operation_id": "automatic",
            "expected_revision": 1,
            "content": "自动补充的新约定",
        }
    )
    assert service.edit(changed, automatic=True).revision == 2
    with store.database.transaction() as connection:
        connection.execute(
            "UPDATE items SET content='修改原消息' WHERE id=?", (source,)
        )
    service.cleanup()
    assert (
        service.read(
            MemoryReadRequest(session_id=session["id"], path=request.path)
        ).content
        == changed.content
    )
    assert (
        service.read(
            MemoryReadRequest(session_id=session["id"], path=request.path, revision=1)
        ).content
        == request.content
    )


@pytest.mark.parametrize(
    "change", ["edit", "delete", "rewrite", "rewind", "session_delete"]
)
def test_saved_versions_remain_independent_of_chat_changes(store, tmp_path, change):
    from eidos_runtime.memory.lifecycle import invalidate_source

    session = store.create_session(str(tmp_path / "workspace"))
    reader = store.create_session(str(tmp_path / "workspace"))
    run, _ = store.create_run(session["id"], "旧规则来源")
    source = store.get_user_item(run["id"])["id"]
    service = store.database.memory
    request = note(session_id=session["id"], scope="current", source_item_ids=[source])
    service.edit(request, automatic=True)
    service.edit(
        request.model_copy(
            update={
                "operation_id": "user-edit",
                "expected_revision": 1,
                "summary": "用户独立修改后的规则",
                "content": "用户独立修改后的规则",
            }
        )
    )
    historical = service.read(
        MemoryReadRequest(
            session_id=reader["id"],
            path=request.path,
            revision=1,
        ),
        for_use=True,
    )
    if change == "session_delete":
        from eidos_runtime.model.client import ModelResponse, ScriptedModel
        from eidos_runtime.runtime.engine import RuntimeEngine

        RuntimeEngine(
            store, ScriptedModel([ModelResponse(text="完成")]), lambda _: None
        ).run(run["id"], threading.Event())
    legacy_epochs = {**historical.epochs, "session:" + session["id"]: 0}
    service.track_tool(
        "reader-run", "historical-read", legacy_epochs, {}, retrieval=True
    )
    with service.admit(legacy_epochs, threading.Event()) as cancel:
        with store.database.transaction() as connection:
            if change == "edit":
                connection.execute(
                    "UPDATE items SET content='来源已更正' WHERE id=?", (source,)
                )
            elif change == "delete":
                connection.execute("DELETE FROM items WHERE id=?", (source,))
            elif change == "session_delete":
                store.delete_session(session["id"])
            else:
                invalidate_source(
                    connection,
                    session["id"],
                    reason=change,
                    run_id=run["id"],
                    after_ordinal=-1 if change == "rewind" else None,
                )
        assert not cancel.is_set()
    service.cleanup()
    assert (
        store.connection.execute(
            "SELECT revoked FROM memory_tool_reads WHERE item_id='historical-read'"
        ).fetchone()[0]
        == 0
    )
    assert (
        service.read(
            MemoryReadRequest(session_id=reader["id"], path=request.path, revision=1)
        ).content
        == request.content
    )
    assert (
        service.read(
            MemoryReadRequest(session_id=reader["id"], path=request.path)
        ).content
        == "用户独立修改后的规则"
    )


def test_deleted_automatic_note_cannot_be_relearned_from_its_old_source(
    store, tmp_path
):
    session = store.create_session(str(tmp_path / "workspace"))
    run, _ = store.create_run(session["id"], "以后只运行相关测试")
    source = store.connection.execute(
        "SELECT id FROM items WHERE run_id=? AND kind='user_message'", (run["id"],)
    ).fetchone()[0]
    service = store.database.memory
    request = note(session_id=session["id"], scope="current", source_item_ids=[source])
    service.edit(request, automatic=True)
    service.edit(
        note(
            session_id=session["id"],
            scope="current",
            operation_id="forget",
            expected_revision=1,
            action="delete",
            content=None,
        )
    )
    with pytest.raises(MemoryRejected, match="forgotten_source"):
        service.edit(
            request.model_copy(update={"operation_id": "learn-again"}), automatic=True
        )


def test_mode_change_during_preparation_rejects_publication(store, monkeypatch):
    service = store.database.memory
    prepare = service.repository.files.prepare
    changed = False

    def change_mode(*arguments):
        nonlocal changed
        if not changed:
            changed = True
            service.policy(MemoryPolicyRequest(scope="global", mode="read"))
        return prepare(*arguments)

    monkeypatch.setattr(service.repository.files, "prepare", change_mode)
    with pytest.raises(MemoryRejected, match="automatic_disabled"):
        service.edit(note(), automatic=True)
    assert not service.list(MemoryListRequest()).documents


def test_reset_failure_rolls_back_schema_and_version(tmp_path, monkeypatch):
    from eidos_runtime.db.schema import V39_SCHEMA_SQL
    from eidos_runtime.memory import document_schema

    with sqlite3.connect(tmp_path / "state.sqlite") as connection:
        connection.executescript(V39_SCHEMA_SQL + "PRAGMA user_version=39;")
        connection.execute(
            "INSERT INTO memory_scopes(id,kind,settings_json) VALUES(?,'global','{}')",
            (GLOBAL_SCOPE_ID,),
        )
        connection.commit()
        monkeypatch.setattr(
            document_schema,
            "RESET_DOCUMENT_MEMORY_SQL",
            document_schema.RESET_DOCUMENT_MEMORY_SQL
            + "\nSELECT nonexistent_column FROM memory_scopes;",
        )
        with pytest.raises(sqlite3.OperationalError):
            document_schema.reset_document_memory(connection)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 39
        assert (
            connection.execute(
                "SELECT settings_json FROM memory_scopes WHERE id=?", (GLOBAL_SCOPE_ID,)
            ).fetchone()[0]
            == "{}"
        )
        assert not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='memory_documents'"
        ).fetchone()


def test_source_changes_while_preparing_files_do_not_block_saved_notes(
    store, tmp_path, monkeypatch
):
    session = store.create_session(str(tmp_path / "workspace"))
    run, _ = store.create_run(session["id"], "以后只运行相关测试")
    source = store.connection.execute(
        "SELECT id FROM items WHERE run_id=? AND kind='user_message'", (run["id"],)
    ).fetchone()[0]
    service = store.database.memory
    request = note(session_id=session["id"], scope="current", source_item_ids=[source])
    prepare = service.repository.files.prepare

    def edit_source(*args):
        with store.database.transaction() as connection:
            connection.execute(
                "UPDATE items SET content='changed' WHERE id=?", (source,)
            )
        return prepare(*args)

    monkeypatch.setattr(service.repository.files, "prepare", edit_source)
    service.edit(request, automatic=True)
    assert service.list(MemoryListRequest(session_id=session["id"])).documents


def test_forget_revokes_requests_and_removes_bodies(store):
    service = store.database.memory
    service.edit(note())
    scope = service.list(MemoryListRequest()).scopes[0]
    with pytest.raises(MemoryRejected, match="snapshot_revoked"):
        with service.admit({scope.id: scope.privacy_epoch}, threading.Event()) as guard:
            service.edit(
                note(
                    operation_id="delete",
                    action="delete",
                    content=None,
                    expected_revision=1,
                )
            )
            assert guard.is_set()
    assert not service.list(MemoryListRequest()).documents
    assert (
        store.connection.execute("SELECT file_ref FROM memory_revisions").fetchone()[0]
        is None
    )


def test_scope_modes_control_memory_access(store, tmp_path):
    session = store.create_session(str(tmp_path / "workspace"))
    service = store.database.memory
    service.policy(
        MemoryPolicyRequest(session_id=session["id"], scope="current", mode="read")
    )
    assert service.access(session["id"]).read_enabled
    # Global auto memory remains available while the project is read-only.
    assert service.access(session["id"]).generate_enabled
    service.edit(note(session_id=session["id"]))
    for scope in ("current", "global"):
        service.policy(
            MemoryPolicyRequest(session_id=session["id"], scope=scope, mode="off")
        )
    assert not service.access(session["id"]).read_enabled
    with pytest.raises(MemoryRejected, match="read_disabled"):
        service.read(
            MemoryReadRequest(
                session_id=session["id"], scope="global", path=note().path
            ),
            for_use=True,
        )
    # Explicit user editing remains available even when model use is disabled.
    service.edit(
        note(session_id=session["id"], operation_id="explicit", path="user_note.md")
    )
    service.policy(
        MemoryPolicyRequest(session_id=session["id"], scope="current", mode="auto")
    )
    assert service.access(session["id"]).generate_enabled


@pytest.mark.parametrize(
    "path",
    [
        "../outside.md",
        "/outside.md",
        "a/../b.md",
        "a//b.md",
        "a\\b.md",
        ".private.md",
        "note.txt",
        "MEMORY.md",
    ],
)
def test_document_path_validation(path):
    with pytest.raises(ValueError):
        note(path=path)


def test_tampered_body_fails_closed(store):
    service = store.database.memory
    service.edit(note())
    reference = store.connection.execute(
        "SELECT file_ref FROM memory_revisions"
    ).fetchone()[0]
    (service.repository.files.root / reference).write_text("篡改后的正文")
    with pytest.raises(MemoryRejected, match="body_unavailable"):
        service.read(MemoryReadRequest(scope="global", path=note().path))
    assert not service.list(MemoryListRequest()).documents


def test_export_pages_cover_the_whole_scope(store):
    service = store.database.memory
    for index in range(19):
        service.edit(note(operation_id=str(index), path=f"note-{index:02}.md"))
    first = service.export(MemoryExportRequest(scope="global"))
    second = service.export(
        MemoryExportRequest(scope="global", cursor=first.next_cursor)
    )
    assert (
        first.generation == second.generation
        and len(first.files) + len(second.files) == 20
    )
    assert second.next_cursor is None


def test_restart_keeps_new_notes(store):
    root = store.data_directory
    store.database.memory.edit(note())
    store.close()
    reopened = SessionStore(root)
    reopened.initialize()
    try:
        assert (
            reopened.connection.execute("PRAGMA user_version").fetchone()[0]
            == SCHEMA_VERSION
        )
        assert (
            reopened.database.memory.read(
                MemoryReadRequest(scope="global", path=note().path)
            ).content
            == note().content
        )
    finally:
        reopened.close()


def test_v39_upgrade_discards_legacy_memory_and_preserves_chat(tmp_path):
    from eidos_runtime.memory.schema import (
        MEMORY_SCHEMA_SQL,
        MEMORY_USE_SCHEMA_SQL,
        MEMORY_HISTORY_SCHEMA_SQL,
    )

    root = tmp_path / "data"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(root)
    store.initialize()
    session = store.create_session(str(workspace))
    run, _ = store.create_run(session["id"], "保留这段真实聊天")
    store.close()
    with sqlite3.connect(root / "state.sqlite") as connection:
        for (name,) in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND (name LIKE 'memory_%' OR name LIKE '%_memory%' OR name LIKE '%_origin_memory')"
        ).fetchall():
            connection.execute(f'DROP TRIGGER "{name}"')
        for (name,) in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'memory_%' AND name NOT LIKE 'memory_fts_%'"
        ).fetchall():
            connection.execute(f'DROP TABLE "{name}"')
        connection.execute("DROP TABLE memory_fts_word")
        old = MEMORY_SCHEMA_SQL.replace(
            "ALTER TABLE context_snapshots ADD COLUMN memory_revoked INTEGER NOT NULL DEFAULT 0;",
            "",
        )
        connection.executescript(
            old + MEMORY_USE_SCHEMA_SQL + MEMORY_HISTORY_SCHEMA_SQL
        )
        connection.execute(
            "INSERT INTO memory_scopes(id,kind,settings_json) VALUES(?,'global',?)",
            (
                GLOBAL_SCOPE_ID,
                json.dumps({"useEnabled": True, "generateEnabled": False}),
            ),
        )
        connection.execute(
            "ALTER TABLE step_resolution_snapshots DROP COLUMN memory_revoked"
        )
        connection.execute(
            "INSERT INTO hook_invocations(id,run_id,plugin_id,plugin_hash,hook_key,event,event_key,status,input_json,output_json,created_at) VALUES('old-memory-hook',?,'plugin','hash','key','PostToolUse','post:item','completed',?,? ,1)",
            (
                run["id"],
                json.dumps(
                    {"tool_name": "memory_record", "tool_input": {"content": "旧记忆"}}
                ),
                '{"stdout":"旧记忆"}',
            ),
        )
        connection.execute(
            "INSERT INTO hook_pending_events VALUES(?,'post:item',?)",
            (run["id"], '{"tool_name":"memory_record"}'),
        )
        connection.execute("PRAGMA user_version=39")
    (root / "memory").mkdir()
    (root / "memory" / "old.md").write_text("旧记忆")
    (root / "memories").mkdir()
    (root / "memories" / "old.md").write_text("更旧的记忆")
    (root / "memories.sqlite").write_text("不应打开的旧辅助库")
    (root / "hook-transcripts").mkdir()
    (root / "hook-transcripts" / "cached.jsonl").write_text("旧记忆工具载荷")
    upgraded = SessionStore(root)
    upgraded.initialize()
    try:
        assert upgraded.health()["state"] == "ready"
        assert upgraded.database.memory.list(MemoryListRequest()).documents == []
        assert (
            upgraded.database.memory.list(MemoryListRequest()).scopes[0].mode == "read"
        )
        assert (
            not (root / "memory" / "old.md").exists()
            and not (root / "memories").exists()
        )
        assert not (root / "memories.sqlite").exists()
        assert not (root / "hook-transcripts").exists()
        assert upgraded.connection.execute(
            "SELECT input_json,output_json FROM hook_invocations WHERE id='old-memory-hook'"
        ).fetchone()[:] == ("{}", None)
        assert not upgraded.connection.execute(
            "SELECT 1 FROM hook_pending_events"
        ).fetchone()
        assert (
            upgraded.connection.execute(
                "SELECT content FROM items WHERE run_id=? AND kind='user_message'",
                (run["id"],),
            ).fetchone()[0]
            == "保留这段真实聊天"
        )
        assert not upgraded.connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='memory_jobs'"
        ).fetchone()
    finally:
        upgraded.close()

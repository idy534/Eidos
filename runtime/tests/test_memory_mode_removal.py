from __future__ import annotations

import sqlite3
import threading

import pytest
from pydantic import ValidationError

from eidos_runtime.application.memory import MEMORY_METHODS
from eidos_runtime.db.database import Database
from eidos_runtime.db.layout import _migrate_state_schema
from eidos_runtime.db.schema import SCHEMA_VERSION, V41_SCHEMA_SQL
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.memory import remove_temporary_migration
from eidos_runtime.memory.contracts import (
    MemoryEditRequest,
    MemoryReadRequest,
    MemoryState,
)
from eidos_runtime.model.client import ModelResponse, ScriptedModel
from eidos_runtime.runtime.engine import RuntimeEngine

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("entry", ["database", "layout"])
def test_v41_upgrade_removes_mode_preserves_notes_and_keeps_old_sources_revoked(
    tmp_path, entry
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    directory = tmp_path / "data"
    store = SessionStore(directory)
    store.initialize()
    session = store.create_session(str(workspace))
    ordinary = store.create_session(str(workspace))
    source_ids = []
    for current in (session, ordinary):
        run, _ = store.create_run(current["id"], "已有消息")
        source_ids.append(store.get_user_item(run["id"])["id"])
        RuntimeEngine(
            store, ScriptedModel([ModelResponse(text="完成")]), lambda _: None
        ).run(run["id"], threading.Event())
    service = store.database.memory
    service.edit(
        MemoryEditRequest(
            scope="global",
            operation_id="save",
            path="user_note.md",
            summary="用户笔记",
            content="保留已有笔记。",
        )
    )
    before = service.read(
        MemoryReadRequest(
            session_id=session["id"], scope="global", path="user_note.md"
        ),
        for_use=True,
    )
    legacy_epochs = {**before.epochs, "session:" + session["id"]: 0}
    store.close()
    with sqlite3.connect(directory / "state.sqlite") as connection:
        # Recreate the actual v41 columns before running all later migrations.
        connection.execute("ALTER TABLE memory_documents DROP COLUMN archived")
        connection.execute(
            "ALTER TABLE memory_sources ADD COLUMN temporary INTEGER NOT NULL DEFAULT 0"
        )
        connection.execute(
            "UPDATE memory_sources SET temporary=1 WHERE session_id=?", (session["id"],)
        )
        connection.execute("PRAGMA user_version=41")
    if entry == "layout":
        owner = Database(directory)
        owner._connection = sqlite3.connect(directory / "state.sqlite")
        try:
            _migrate_state_schema(owner)
        finally:
            owner.close()
    reopened = SessionStore(directory)
    reopened.initialize()
    try:
        assert reopened.health()["state"] == "ready"
        assert (
            reopened.connection.execute("PRAGMA user_version").fetchone()[0]
            == SCHEMA_VERSION
        )
        assert "temporary" not in {
            row[1]
            for row in reopened.connection.execute("PRAGMA table_info(memory_sources)")
        }
        service = reopened.database.memory
        assert service.access(session["id"]).read_enabled
        assert service.access(session["id"]).generate_enabled
        assert not service.valid_epochs(reopened.connection, legacy_epochs)
        assert (
            service.read(MemoryReadRequest(scope="global", path="user_note.md")).content
            == before.content
        )
        assert (
            reopened.connection.execute(
                "SELECT eligible FROM memory_source_items WHERE item_id=?",
                (source_ids[0],),
            ).fetchone()[0]
            == 0
        )
        assert (
            reopened.connection.execute(
                "SELECT eligible FROM memory_source_items WHERE item_id=?",
                (source_ids[1],),
            ).fetchone()[0]
            == 1
        )
        request = MemoryEditRequest(
            session_id=session["id"],
            operation_id="old-source",
            path="project_note.md",
            summary="规则",
            content="已有规则",
            source_item_ids=[source_ids[0]],
        )
        saved = service.edit(request, automatic=True)
        assert (
            reopened.connection.execute(
                "SELECT count(*) FROM memory_evidence WHERE document_id=?",
                (saved.document_id,),
            ).fetchone()[0]
            == 0
        )
        run, _ = reopened.create_run(session["id"], "以后遵循新的工作约定")
        fresh = reopened.get_user_item(run["id"])["id"]
        service.edit(
            request.model_copy(
                update={
                    "operation_id": "new-source",
                    "source_item_ids": [fresh],
                    "expected_revision": 1,
                    "content": "新的工作约定",
                }
            ),
            automatic=True,
        )
        assert (
            service.read(
                MemoryReadRequest(session_id=session["id"], path="project_note.md"),
                for_use=True,
            ).content
            == "新的工作约定"
        )
    finally:
        reopened.close()


def test_removal_migration_failure_rolls_back_column_and_version(monkeypatch):
    with sqlite3.connect(":memory:") as connection:
        connection.executescript(V41_SCHEMA_SQL + "PRAGMA user_version=41;")
        monkeypatch.setattr(
            remove_temporary_migration,
            "REMOVE_TEMPORARY_MEMORY_SQL",
            remove_temporary_migration.REMOVE_TEMPORARY_MEMORY_SQL
            + "SELECT missing_column FROM memory_sources;",
        )
        with pytest.raises(sqlite3.OperationalError):
            remove_temporary_migration.migrate_remove_temporary_memory(connection)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 41
        assert "temporary" in {
            row[1] for row in connection.execute("PRAGMA table_info(memory_sources)")
        }


def test_removed_mode_is_not_part_of_the_runtime_contract():
    assert "memory/temporary" not in {method for method, _, _ in MEMORY_METHODS}
    with pytest.raises(ValidationError):
        MemoryState.model_validate({"scopes": [], "documents": [], "temporary": True})

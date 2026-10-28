from __future__ import annotations

import sqlite3

import pytest

from eidos_runtime.db.schema import SCHEMA_VERSION, V40_SCHEMA_SQL
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.memory.contracts import MemoryEditRequest, MemoryReadRequest
from eidos_runtime.memory import revocation_migration

pytestmark = pytest.mark.integration


@pytest.fixture
def v40_memory(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    session = store.create_session(str(workspace))
    reader = store.create_session(str(workspace))
    run, _ = store.create_run(session["id"], "旧规则来源")
    source = store.get_user_item(run["id"])["id"]
    service = store.database.memory
    request = MemoryEditRequest(
        session_id=session["id"],
        operation_id="automatic",
        path="feedback.md",
        summary="旧规则",
        content="旧规则",
        source_item_ids=[source],
    )
    service.edit(request, automatic=True)
    service.edit(
        request.model_copy(
            update={
                "operation_id": "user-edit",
                "expected_revision": 1,
                "summary": "用户自己的规则",
                "content": "用户自己的规则",
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
    service.track_tool(
        "reader-run", "historical-read", historical.epochs, {}, retrieval=True
    )
    with sqlite3.connect(":memory:") as legacy:
        legacy.executescript(V40_SCHEMA_SQL)
        triggers = legacy.execute(
            "SELECT name,sql FROM sqlite_master WHERE type='trigger' "
            "AND name IN ('memory_source_change','memory_source_delete')"
        ).fetchall()
    with store.database.transaction() as connection:
        connection.execute(
            "ALTER TABLE memory_sources ADD COLUMN temporary INTEGER NOT NULL DEFAULT 0"
        )
        for name, sql in triggers:
            connection.execute(f"DROP TRIGGER {name}")
            connection.execute(sql)
        connection.execute("ALTER TABLE memory_documents DROP COLUMN archived")
        connection.execute("DROP TABLE personalization_settings")
        connection.execute("PRAGMA user_version=40")
        connection.execute(
            "UPDATE items SET content='来源已更正' WHERE id=?", (source,)
        )
    service.cleanup()
    assert service.valid_epochs(store.connection, historical.epochs)
    yield store, reader["id"], historical
    store.close()


@pytest.mark.parametrize("database_name", ["state.sqlite", "eidos.db"])
def test_v40_upgrade_revokes_stale_reads_and_preserves_user_notes(
    v40_memory, database_name
):
    store, reader, historical = v40_memory
    data = store.data_directory
    assert data is not None
    store.close()
    if database_name != "state.sqlite":
        (data / "state.sqlite").rename(data / database_name)
    upgraded = SessionStore(data)
    upgraded.initialize()
    try:
        assert upgraded.health()["state"] == "ready"
        assert (
            upgraded.connection.execute("PRAGMA user_version").fetchone()[0]
            == SCHEMA_VERSION
        )
        service = upgraded.database.memory
        service.cleanup()
        assert not service.valid_epochs(upgraded.connection, historical.epochs)
        assert (
            upgraded.connection.execute(
                "SELECT revoked FROM memory_tool_reads WHERE item_id='historical-read'"
            ).fetchone()[0]
            == 1
        )
        current = service.read(MemoryReadRequest(session_id=reader, path="feedback.md"))
        assert current.revision == 2
        assert current.document.user_owned
        assert current.content == "用户自己的规则"
        generation = service.access(reader).scopes[-1].generation
    finally:
        upgraded.close()
    reopened = SessionStore(data)
    reopened.initialize()
    try:
        assert (
            reopened.database.memory.access(reader).scopes[-1].generation == generation
        )
    finally:
        reopened.close()


def test_v40_upgrade_failure_rolls_back_triggers_epochs_and_version(
    v40_memory, monkeypatch
):
    store, reader, historical = v40_memory
    before = store.connection.execute(
        "SELECT name,sql FROM sqlite_master WHERE type='trigger' AND name LIKE 'memory_source_%' ORDER BY name"
    ).fetchall()
    monkeypatch.setattr(
        revocation_migration,
        "MEMORY_SOURCE_REVOCATION_SQL",
        revocation_migration.MEMORY_SOURCE_REVOCATION_SQL
        + "\nSELECT nonexistent_column FROM memory_scopes;",
    )
    with pytest.raises(sqlite3.OperationalError):
        revocation_migration.migrate_memory_source_revocation(store.connection)
    assert store.connection.execute("PRAGMA user_version").fetchone()[0] == 40
    assert store.database.memory.valid_epochs(store.connection, historical.epochs)
    after = store.connection.execute(
        "SELECT name,sql FROM sqlite_master WHERE type='trigger' AND name LIKE 'memory_source_%' ORDER BY name"
    ).fetchall()
    assert [tuple(row) for row in after] == [tuple(row) for row in before]
    reference = store.connection.execute(
        "SELECT r.file_ref FROM memory_revisions r JOIN memory_documents d ON d.id=r.document_id AND d.current_revision=r.revision WHERE d.path='feedback.md'"
    ).fetchone()[0]
    assert store.database.memory.repository.files.read(reference) == "用户自己的规则"

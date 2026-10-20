from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest

from eidos_runtime.application.search import SearchApplication
from eidos_runtime.db.repositories.sessions import SessionRepository
from eidos_runtime.db.schema import V32_SCHEMA_SQL
from eidos_runtime.db.search_migration import migrate_search
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.persistence.search import SearchRepository, search_text
from eidos_runtime.protocol.search import SearchRequest, ReadAroundRequest
from eidos_runtime.protocol.search import SearchResponse
from eidos_runtime.protocol.search import FileFindRequest, FileFindResponse
from pydantic import ValidationError
from eidos_runtime.protocol.server import RuntimeServer
from eidos_runtime.protocol.session_fork import SessionForkRequest
from eidos_runtime.persistence.session_history import SessionHistoryRepository


@pytest.fixture
def store(tmp_path: Path):
    value = SessionStore(tmp_path / "data")
    value.initialize()
    assert value.health_state == "ready"
    yield value
    value.close()


def session(store, tmp_path, title="旧会话"):
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    value = store.create_session(str(workspace))
    store.rename_session(value["id"], title)
    return value


def complete_search(repo, **kwargs):
    for _ in range(100):
        result = repo.search(SearchRequest(**kwargs))
        if not result.indexing:
            return result
    pytest.fail("index did not finish")


def test_search_all_sessions_short_chinese_code_and_title(store, tmp_path):
    ids = []
    for index in range(56):
        value = session(store, tmp_path, f"会话 {index}")
        ids.append(value["id"])
    run, item = store.create_run(ids[-1], "中文线程 `owner_lock` and OR * []")
    repo = SearchRepository(store.database)
    for query in ["中文", "线", "线程", "owner_lock", "OR * []"]:
        result = complete_search(repo, query=query)
        assert [hit.session_id for hit in result.items] == [ids[-1]]
        assert result.items[0].item_id == item["id"]
    assert complete_search(repo, query="会话 55").items[0].match_kind == "title"
    assert store.read_run(run["id"])["status"] == "running"


def test_update_delete_and_restart_never_revive_stale_text(store, tmp_path):
    value = session(store, tmp_path)
    _run, item = store.create_run(value["id"], "obsolete needle")
    repo = SearchRepository(store.database)
    assert complete_search(repo, query="needle").items
    with store.database.transaction() as connection:
        connection.execute(
            "UPDATE items SET content='replacement text' WHERE id=?", (item["id"],)
        )
    assert not complete_search(repo, query="needle").items
    assert complete_search(repo, query="replacement").items
    data = store.database.data_directory
    store.close()
    reopened = SessionStore(data)
    reopened.initialize()
    try:
        assert complete_search(
            SearchRepository(reopened.database), query="replacement"
        ).items
        with reopened.database.transaction() as connection:
            connection.execute("DELETE FROM items WHERE id=?", (item["id"],))
        assert not complete_search(
            SearchRepository(reopened.database), query="replacement"
        ).items
    finally:
        reopened.close()


def test_occurrences_paginate_without_losing_messages_and_scope_is_bound(
    store, tmp_path
):
    value = session(store, tmp_path)
    store.create_run(value["id"], "needle needle needle")
    repo = SearchRepository(store.database)
    first = complete_search(repo, query="needle", session_id=value["id"], limit=2)
    assert [hit.occurrence for hit in first.items] == [0, 1]
    second = repo.search(
        SearchRequest(
            query="needle", session_id=value["id"], cursor=first.next_cursor, limit=2
        )
    )
    assert [hit.occurrence for hit in second.items] == [2]
    with pytest.raises(Exception, match="cursor"):
        repo.search(SearchRequest(query="different", cursor=first.next_cursor))


def test_read_around_reaches_old_item_without_loading_all_history(store, tmp_path):
    value = session(store, tmp_path)
    run, target = store.create_run(value["id"], "the anchor")
    # A real long transcript within a single queued Run, without model calls.
    with store.database.transaction() as connection:
        for index in range(2, 221):
            connection.execute(
                "INSERT INTO items(id,session_id,run_id,ordinal,kind,status,content,created_at) VALUES(?,?,?,?, 'assistant_message','completed',?,1)",
                (str(uuid4()), value["id"], run["id"], index, f"later {index}"),
            )
    app = SearchApplication(
        SearchRepository(store.database), SessionRepository(store.database)
    )
    around = app.read_around(
        ReadAroundRequest(session_id=value["id"], item_id=target["id"])
    )
    assert target["id"] in {item.id for item in around.items}
    assert len(around.items) <= 40
    assert len(store.read_session_snapshot(value["id"])["items"]) <= 200


def test_true_old_schema_backfill_and_transaction_rollback(tmp_path):
    connection = sqlite3.connect(":memory:")
    connection.executescript(V32_SCHEMA_SQL + "PRAGMA user_version=32;")
    migrate_search(connection)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 33
    assert connection.execute(
        "SELECT name FROM sqlite_master WHERE name='search_item_insert'"
    ).fetchone()
    bad = sqlite3.connect(":memory:")
    bad.executescript(
        V32_SCHEMA_SQL + "CREATE TABLE search_pending(x); PRAGMA user_version=32;"
    )
    with pytest.raises(sqlite3.Error):
        migrate_search(bad)
    assert bad.execute("PRAGMA user_version").fetchone()[0] == 32
    assert (
        bad.execute(
            "SELECT name FROM sqlite_master WHERE name='search_documents'"
        ).fetchone()
        is None
    )
    bad.close()
    connection.close()


def test_markdown_projection_keeps_visible_text_and_code():
    assert (
        search_text("**中文** [标签](https://hidden.invalid)\n\n```py\nowner_lock\n```")
        == "中文 标签 owner_lock"
    )


def test_frozen_history_search_and_anchor_survive_source_deletion(store, tmp_path):
    source = session(store, tmp_path)
    run, _ = store.create_run(source["id"], "question")
    answer = str(uuid4())
    with store.database.transaction() as connection:
        connection.execute(
            "INSERT INTO items(id,session_id,run_id,ordinal,kind,status,content,created_at,completed_at) VALUES(?,?,?,2,'assistant_message','completed','frozen needle',2,2)",
            (answer, source["id"], run["id"]),
        )
        connection.execute(
            "UPDATE runs SET status='succeeded',completed_at=2 WHERE id=?", (run["id"],)
        )
    target = str(uuid4())
    history = SessionHistoryRepository(store.database)
    history.fork(
        SessionForkRequest(
            operation_id=str(uuid4()),
            source_session_id=source["id"],
            source_item_id=answer,
            target={"kind": "same_workspace"},
        ),
        session_id=target,
    )
    store.delete_session(source["id"])
    repo = SearchRepository(store.database)
    result = complete_search(repo, query="needle", session_id=target)
    assert result.items[0].session_id == target
    anchor = result.items[0].item_id
    assert anchor != answer
    around = SearchApplication(repo, SessionRepository(store.database)).read_around(
        ReadAroundRequest(session_id=target, item_id=anchor)
    )
    assert anchor in {entry.item.id for entry in around.inherited_history}


def test_real_v32_upgrade_backfills_existing_messages(store, tmp_path):
    value = session(store, tmp_path)
    store.create_run(value["id"], "backfill needle")
    with store.database.transaction() as connection:
        triggers = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'search_%'"
        ).fetchall()
        for row in triggers:
            connection.execute(f'DROP TRIGGER "{row[0]}"')
        connection.execute("DROP TABLE search_fts")
        connection.execute("DROP TABLE search_documents")
        connection.execute("DROP TABLE search_pending")
        connection.execute("DROP TABLE search_index_state")
        connection.execute("PRAGMA user_version=32")
    data = store.database.data_directory
    store.close()
    reopened = SessionStore(data)
    reopened.initialize()
    try:
        assert reopened.health_state == "ready"
        assert (
            complete_search(SearchRepository(reopened.database), query="backfill")
            .items[0]
            .session_id
            == value["id"]
        )
    finally:
        reopened.close()


def test_runtime_protocol_search_and_around(store, tmp_path):
    output = io.StringIO()
    server = RuntimeServer(output, data_directory=tmp_path / "server-data")
    try:
        server.store.initialize()
        server.initialized = True
        value = session(server.store, tmp_path)
        _run, item = server.store.create_run(value["id"], "protocol needle")
        server.handle(
            {
                "jsonrpc": "2.0",
                "id": "client-search",
                "method": "search/sessions",
                "params": {"query": "needle"},
            }
        )
        server.handle(
            {
                "jsonrpc": "2.0",
                "id": "client-around",
                "method": "session/readAround",
                "params": {"sessionId": value["id"], "itemId": item["id"]},
            }
        )
        replies = {
            entry["id"]: entry
            for line in output.getvalue().splitlines()
            if "id" in (entry := json.loads(line))
        }
        assert replies["client-search"]["result"]["items"][0]["itemId"] == item["id"]
        assert replies["client-around"]["result"]["items"][0]["id"] == item["id"]
    finally:
        server.close()


def test_shared_protocol_fixture():
    fixture = json.loads(
        (Path(__file__).parents[2] / "protocol/fixtures/search.json").read_text()
    )
    for value in fixture["validRequests"]:
        SearchRequest.model_validate(value)
    for value in fixture["invalidRequests"]:
        with pytest.raises(ValidationError):
            SearchRequest.model_validate(value)
    for value in fixture["validResponses"]:
        SearchResponse.model_validate(value)
    for value in fixture["invalidResponses"]:
        with pytest.raises(ValidationError):
            SearchResponse.model_validate(value)
    for name, model in [
        ("FileRequests", FileFindRequest),
        ("FileResponses", FileFindResponse),
    ]:
        for value in fixture["valid" + name]:
            model.model_validate(value)
        for value in fixture["invalid" + name]:
            with pytest.raises(ValidationError):
                model.model_validate(value)

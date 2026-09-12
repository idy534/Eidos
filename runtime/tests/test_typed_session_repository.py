from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from eidos_runtime.db.database import Database
from eidos_runtime.db.repositories.sessions import SessionRepository
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.domain.session import DeletedSession, Session, SessionPage
from eidos_runtime.persistence.errors import PersistenceCorruptionError


@pytest.fixture
def repository(tmp_path: Path) -> tuple[Database, SessionRepository, Path]:
    data = tmp_path / "data"
    workspace = tmp_path / "workspace"
    data.mkdir(mode=0o700)
    workspace.mkdir()
    database = Database(data)
    database.initialize()
    assert database.health_state == "ready"
    try:
        yield database, SessionRepository(database), workspace
    finally:
        database.close()


def test_repository_returns_typed_sessions_pages_and_title_mutations(
    repository: tuple[Database, SessionRepository, Path],
) -> None:
    _database, sessions, workspace = repository
    first = sessions.create_session(str(workspace))
    second_workspace = workspace.parent / "workspace-2"
    second_workspace.mkdir()
    second = sessions.create_session(str(second_workspace))

    assert isinstance(first, Session)
    assert first.title is None
    page = sessions.list_sessions(limit=1)
    assert isinstance(page, SessionPage)
    assert page.items == (second,)
    assert page.next_cursor is not None
    next_page = sessions.list_sessions(limit=1, cursor=page.next_cursor)
    assert next_page.items == (first,)
    assert next_page.next_cursor is None
    assert sessions.read_session(first.id) == first

    renamed = sessions.rename_session(first.id, "Typed title")
    assert isinstance(renamed, Session)
    assert renamed.title == "Typed title"
    started = sessions.begin_title_generation_committed(first.id)
    finished = sessions.finish_title_generation_committed(first.id, "Generated")
    assert isinstance(started.value, Session)
    assert isinstance(finished.value, Session)
    assert finished.value.title == "Typed title"


def test_repository_idempotent_replay_remains_typed(
    repository: tuple[Database, SessionRepository, Path],
) -> None:
    _database, sessions, workspace = repository
    operation_id = "11111111-1111-4111-8111-111111111111"

    first = sessions.create_session(str(workspace), operation_id=operation_id)
    replay = sessions.create_session(str(workspace), operation_id=operation_id)

    assert isinstance(replay, Session)
    assert replay == first


def test_repository_delete_returns_typed_result_without_changing_delete_semantics(
    repository: tuple[Database, SessionRepository, Path],
) -> None:
    _database, sessions, workspace = repository
    session = sessions.create_session(str(workspace))

    deleted = sessions.delete_session(session.id)

    assert deleted == DeletedSession(deleted_session_id=session.id)
    assert sessions.read_session(session.id) is None


def test_legacy_store_keeps_existing_wire_dictionary_contract(tmp_path: Path) -> None:
    data = tmp_path / "store-data"
    workspace = tmp_path / "store-workspace"
    data.mkdir(mode=0o700)
    workspace.mkdir()
    store = SessionStore(data)
    store.initialize()
    try:
        created = store.create_session(str(workspace))
        listed = store.list_sessions(limit=1)
        renamed = store.rename_session(created["id"], "Legacy title")
        started = store.begin_title_generation_committed(created["id"])
        finished = store.finish_title_generation_committed(created["id"], "Generated")
        deleted = store.delete_session(created["id"])

        assert isinstance(created, dict)
        assert created == {
            "id": created["id"],
            "workspaceRoot": str(workspace.resolve()),
            "taskStatus": "new",
            "createdAt": created["createdAt"],
            "updatedAt": created["updatedAt"],
        }
        assert listed["items"] == [created]
        assert renamed["title"] == "Legacy title"
        assert isinstance(started.value, dict)
        assert isinstance(finished.value, dict)
        assert deleted == {"deletedSessionId": created["id"]}
    finally:
        store.close()


def test_corrupted_persisted_session_type_fails_closed(
    repository: tuple[Database, SessionRepository, Path],
) -> None:
    database, sessions, workspace = repository
    session = sessions.create_session(str(workspace))
    database.connection().execute(
        "UPDATE sessions SET created_at = 'not-an-integer' WHERE id = ?",
        (session.id,),
    )
    database.connection().commit()

    with pytest.raises(PersistenceCorruptionError) as captured:
        sessions.read_session(session.id)

    assert captured.value.code == "persistence_value_invalid"
    assert captured.value.record == "session"
    assert captured.value.field == "created_at"


def test_title_update_and_event_roll_back_together(
    repository: tuple[Database, SessionRepository, Path],
) -> None:
    _database, sessions, workspace = repository
    session = sessions.create_session(str(workspace))

    with patch(
        "eidos_runtime.db.repositories.sessions.append_event",
        side_effect=RuntimeError("fixture event failure"),
    ):
        with pytest.raises(RuntimeError, match="fixture event failure"):
            sessions.rename_session(session.id, "Must roll back")

    persisted = sessions.read_session(session.id)
    assert persisted is not None
    assert persisted.title is None


def test_read_tool_text_paging_and_verification(
    repository: tuple[Database, SessionRepository, Path],
) -> None:
    import hashlib
    from eidos_runtime.db.errors import InvalidCursorError
    from eidos_runtime.file_limits import TOOL_TEXT_PAGE_CHARACTERS
    from eidos_runtime.models.tool_text import ToolTextPage

    database, sessions, workspace = repository
    session = sessions.create_session(str(workspace))

    content = "line of diff\n" * 2000
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    connection = database.connection()
    connection.execute(
        """
        INSERT INTO runs (id, session_id, user_input, model_id, model_profile_json, status, created_at, updated_at)
        VALUES ('run-1', ?, 'prompt', 'deepseek-flash', '{}', 'running', 1000, 1000)
        """,
        (session.id,),
    )
    connection.execute(
        """
        INSERT INTO items (id, session_id, run_id, ordinal, kind, status, created_at)
        VALUES ('item-1', ?, 'run-1', 0, 'tool_call', 'completed', 1000)
        """,
        (session.id,),
    )
    connection.execute(
        """
        INSERT INTO tool_calls (
            id, item_id, model_step_index, batch_order, provider_call_id,
            tool_name, status, arguments_json, approval_status, started_at,
            approval_diff
        ) VALUES (
            'tc-1', 'item-1', 0, 0, 'p-1',
            'apply_patch', 'completed', '{}', 'approved', 1000,
            ?
        )
        """,
        (content,),
    )
    connection.commit()

    page1 = sessions.read_tool_text(session.id, "tc-1", "diff", digest, 0)
    assert isinstance(page1, ToolTextPage)
    assert page1.sha256 == digest
    assert page1.total_characters == len(content)
    assert page1.next_offset == min(len(content), TOOL_TEXT_PAGE_CHARACTERS)
    assert page1.content == content[:page1.next_offset]

    if page1.next_offset < page1.total_characters:
        page2 = sessions.read_tool_text(session.id, "tc-1", "diff", digest, page1.next_offset)
        assert page2.content == content[page1.next_offset:page2.next_offset]

    with pytest.raises(InvalidCursorError):
        sessions.read_tool_text(session.id, "tc-1", "diff", "0" * 64, 0)

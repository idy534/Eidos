from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path
import sqlite3
import threading

import pytest
from pydantic import ValidationError

from eidos_runtime.domain.session import (
    DeletedSession,
    Session,
    SessionPage,
    SessionTaskStatus,
)
from eidos_runtime.persistence.conversion import (
    ReadTransaction,
    RowReader,
    WriteTransaction,
    decode_json_object,
    read_transaction,
    sqlite_safe_integer,
    utc_datetime_from_millis,
    utc_datetime_to_millis,
    write_transaction,
)
from eidos_runtime.persistence.errors import PersistenceCorruptionError
from eidos_runtime.persistence.mappers.session import session_from_row


RUNTIME_PACKAGE = Path(__file__).resolve().parents[1] / "eidos_runtime"


@pytest.mark.parametrize(
    "status",
    [
        SessionTaskStatus.NEW,
        SessionTaskStatus.IN_PROGRESS,
        SessionTaskStatus.COMPLETED,
        SessionTaskStatus.FAILED,
        SessionTaskStatus.CANCELED,
    ],
)
def test_session_domain_records_are_strict_frozen_and_preserve_nullable_title(
    status: SessionTaskStatus,
) -> None:
    created_at = datetime(2026, 8, 1, 1, 2, 3, 456_000, tzinfo=UTC)
    session = Session(
        id="session-1",
        workspace_root="/workspace",
        title=None,
        task_status=status,
        created_at=created_at,
        updated_at=created_at,
    )

    assert session.title is None
    assert session.task_status is status
    assert SessionPage(items=(session,), next_cursor=None).items == (session,)
    assert DeletedSession(deleted_session_id=session.id).deleted_session_id == session.id
    with pytest.raises(ValidationError):
        session.title = "changed"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        Session.model_validate({
            **session.to_internal_dict(),
            "created_at": 1,
        })


def test_row_reader_and_json_decoder_fail_closed_with_stable_diagnostics() -> None:
    reader = RowReader(
        {
            "name": "session",
            "optional": None,
            "count": 4,
            "payload": '{"items":[1,true,null]}',
        },
        record="session",
    )

    assert reader.text("name") == "session"
    assert reader.optional_text("optional") is None
    assert reader.integer("count") == 4
    assert decode_json_object(
        reader.value("payload"), record="session", field="payload"
    ) == {"items": [1, True, None]}

    for column, code in (
        ("missing", "persistence_column_missing"),
        ("optional", "persistence_value_invalid"),
    ):
        with pytest.raises(PersistenceCorruptionError) as captured:
            reader.text(column)
        assert captured.value.code == code
        assert captured.value.record == "session"
        assert captured.value.field == column
        assert str(captured.value) == code

    with pytest.raises(PersistenceCorruptionError) as captured:
        decode_json_object("{", record="session", field="payload")
    assert captured.value.code == "persistence_json_invalid"
    assert captured.value.record == "session"
    assert captured.value.field == "payload"


def test_sqlite_integer_and_utc_millisecond_conversion_are_exact() -> None:
    value = 1_775_012_523_456
    converted = utc_datetime_from_millis(
        value, record="session", field="created_at"
    )

    assert converted.tzinfo is UTC
    assert utc_datetime_to_millis(converted) == value
    assert sqlite_safe_integer(-(2**63), record="fact", field="value") == -(2**63)
    assert sqlite_safe_integer(2**63 - 1, record="fact", field="value") == 2**63 - 1
    for invalid in (True, "1", 2**63, -(2**63) - 1):
        with pytest.raises(PersistenceCorruptionError) as captured:
            sqlite_safe_integer(invalid, record="fact", field="value")
        assert captured.value.code == "persistence_value_invalid"

    with pytest.raises(PersistenceCorruptionError):
        utc_datetime_from_millis(True, record="session", field="created_at")
    with pytest.raises(ValueError, match="UTC millisecond precision"):
        utc_datetime_to_millis(datetime(2026, 8, 1, 0, 0, 0, 1, tzinfo=UTC))


def test_transaction_protocols_accept_sqlite_connections() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        assert isinstance(connection, ReadTransaction)
        assert isinstance(connection, WriteTransaction)
    finally:
        connection.close()


def test_read_and_write_transaction_helpers_preserve_commit_and_rollback(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "transactions.db"
    connection = sqlite3.connect(database_path)
    observer = sqlite3.connect(database_path)
    lock = threading.RLock()
    try:
        connection.execute("CREATE TABLE facts (value TEXT NOT NULL)")
        connection.commit()

        connection.execute("INSERT INTO facts VALUES ('uncommitted')")
        with read_transaction(lock, connection) as transaction:
            assert transaction.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 1
        assert observer.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 0
        connection.rollback()

        with write_transaction(lock, connection) as transaction:
            transaction.execute("INSERT INTO facts VALUES ('committed')")
        assert observer.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 1

        with pytest.raises(RuntimeError, match="rollback"):
            with write_transaction(lock, connection) as transaction:
                transaction.execute("INSERT INTO facts VALUES ('rolled-back')")
                raise RuntimeError("rollback")
        assert observer.execute("SELECT COUNT(*) FROM facts").fetchone()[0] == 1
    finally:
        observer.close()
        connection.close()


def test_session_mapper_covers_nullable_enum_and_timestamp_fields() -> None:
    timestamp = 1_775_012_523_456
    for status in SessionTaskStatus:
        session = session_from_row(
            {
                "id": "session-1",
                "workspace_root": "/workspace",
                "title": None,
                "task_status": status.value,
                "created_at": timestamp,
                "updated_at": timestamp,
            }
        )
        assert session == Session(
            id="session-1",
            workspace_root="/workspace",
            title=None,
            task_status=status,
            created_at=utc_datetime_from_millis(
                timestamp, record="session", field="created_at"
            ),
            updated_at=utc_datetime_from_millis(
                timestamp, record="session", field="updated_at"
            ),
        )


@pytest.mark.parametrize(
    ("row", "field"),
    [
        ({}, "id"),
        (
            {
                "id": "session-1",
                "workspace_root": "/workspace",
                "title": None,
                "task_status": "future_status",
                "created_at": 1,
                "updated_at": 1,
            },
            "task_status",
        ),
        (
            {
                "id": "session-1",
                "workspace_root": "/workspace",
                "title": None,
                "task_status": "new",
                "created_at": "1",
                "updated_at": 1,
            },
            "created_at",
        ),
    ],
)
def test_session_mapper_rejects_missing_invalid_enum_and_wrong_column_types(
    row: dict[str, object], field: str
) -> None:
    with pytest.raises(PersistenceCorruptionError) as captured:
        session_from_row(row)

    assert captured.value.record == "session"
    assert captured.value.field == field


def test_typed_session_seam_has_no_protocol_or_sqlite_dependency_in_domain() -> None:
    session_repository = RUNTIME_PACKAGE / "db" / "repositories" / "sessions.py"
    domain = RUNTIME_PACKAGE / "domain" / "session.py"
    mapper = RUNTIME_PACKAGE / "persistence" / "mappers" / "session.py"

    repository_imports = _imports(session_repository)
    domain_imports = _imports(domain)
    mapper_imports = _imports(mapper)

    assert not any(name.startswith("eidos_runtime.protocol") for name in repository_imports)
    assert not any(name.startswith("eidos_runtime.protocol") for name in mapper_imports)
    assert "sqlite3" not in domain_imports
    assert not any(name.startswith("eidos_runtime.db") for name in domain_imports)
    assert not any(name.startswith("eidos_runtime.protocol") for name in domain_imports)


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    names.update(
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module is not None
    )
    return names

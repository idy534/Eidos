from __future__ import annotations

import sqlite3

import pytest

from eidos_runtime.db.schema import SCHEMA_VERSION, V47_SCHEMA_VERSION
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.persistence.browser_grants import BrowserGrantRepository
from eidos_runtime.runtime.event_projector import EventProjector


@pytest.fixture
def workspace(tmp_path):
    path = tmp_path / "workspace"
    path.mkdir()
    return path


@pytest.fixture
def store(tmp_path):
    value = SessionStore(tmp_path / "data")
    value.initialize()
    yield value
    value.close()


def _grant(repository: BrowserGrantRepository, session_id: str, plugin_hash: str = "hash-1"):
    return repository.grant(
        session_id=session_id, plugin_id="eidos-browser", plugin_version="1.0.0",
        plugin_hash=plugin_hash, source_approval_id=None,
    )


def test_grant_is_session_scoped_and_commits_one_event(store, workspace):
    repository = BrowserGrantRepository(store.database)
    first = store.create_session(str(workspace))
    second = store.create_session(str(workspace))
    mutation = _grant(repository, first["id"])
    assert mutation.value.status == "active"
    assert len(mutation.events) == 1
    assert repository.active(first["id"]) == mutation.value
    assert repository.active(second["id"]) is None
    replay = _grant(repository, first["id"])
    assert replay.value == mutation.value and replay.events == ()
    notification = EventProjector().project(mutation.events[0])
    assert [value["method"] for value in notification] == ["browser/grantChanged"]
    assert notification[0]["params"] == {"sessionId": first["id"], "active": True}


def test_plugin_change_replaces_the_grant(store, workspace):
    repository = BrowserGrantRepository(store.database)
    session = store.create_session(str(workspace))
    old = _grant(repository, session["id"]).value
    new = _grant(repository, session["id"], plugin_hash="hash-2").value
    assert new.id != old.id and new.plugin_hash == "hash-2"
    reason = store.connection.execute(
        "SELECT revoke_reason FROM session_browser_grants WHERE id = ?", (old.id,),
    ).fetchone()[0]
    assert reason == "plugin_changed"


def test_revoke_and_revoke_all_keep_history(store, workspace):
    repository = BrowserGrantRepository(store.database)
    first = store.create_session(str(workspace))
    second = store.create_session(str(workspace))
    _grant(repository, first["id"])
    _grant(repository, second["id"])
    revoked = repository.revoke(first["id"], "user")
    assert revoked.value is True and len(revoked.events) == 1
    assert EventProjector().project(revoked.events[0])[0]["params"]["active"] is False
    assert repository.revoke(first["id"], "user").value is False
    assert repository.revoke_all("profile_cleared").value == 1
    assert repository.active(second["id"]) is None
    rows = store.connection.execute(
        "SELECT status, revoke_reason FROM session_browser_grants ORDER BY creation_seq",
    ).fetchall()
    assert [tuple(row) for row in rows] == [("revoked", "user"), ("revoked", "profile_cleared")]


def test_schema_rejects_two_active_grants_or_an_inconsistent_revocation(store, workspace):
    session = store.create_session(str(workspace))
    _grant(BrowserGrantRepository(store.database), session["id"])
    with pytest.raises(sqlite3.IntegrityError):
        store.connection.execute(
            "INSERT INTO session_browser_grants (id, session_id, profile, access, plugin_id, plugin_version, "
            "plugin_hash, status, created_at) VALUES ('second', ?, 'default', 'interact', 'p', '1', 'h', 'active', 1)",
            (session["id"],),
        )
    with pytest.raises(sqlite3.IntegrityError):
        store.connection.execute(
            "UPDATE session_browser_grants SET status = 'revoked' WHERE session_id = ?", (session["id"],),
        )
    store.connection.rollback()


def test_session_delete_removes_its_grants(store, workspace):
    repository = BrowserGrantRepository(store.database)
    session = store.create_session(str(workspace))
    _grant(repository, session["id"])
    store.delete_session(session["id"])
    assert store.connection.execute("SELECT count(*) FROM session_browser_grants").fetchone()[0] == 0


def _downgrade_to_v47(data_directory, extra_sql: str = "") -> None:
    store = SessionStore(data_directory)
    store.initialize()
    store.close()
    path = data_directory / "state.sqlite"
    connection = sqlite3.connect(path)
    connection.executescript(
        "DROP TABLE session_browser_grants;" + extra_sql + f"PRAGMA user_version={V47_SCHEMA_VERSION};"
    )
    connection.close()


def test_v47_database_upgrades_to_browser_grants(tmp_path, workspace):
    data = tmp_path / "data"
    _downgrade_to_v47(data)
    store = SessionStore(data)
    store.initialize()
    try:
        assert store.connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 48
        session = store.create_session(str(workspace))
        assert _grant(BrowserGrantRepository(store.database), session["id"]).value.status == "active"
    finally:
        store.close()


def test_failed_browser_grant_migration_rolls_back_to_v47(tmp_path):
    data = tmp_path / "data"
    # A conflicting table makes CREATE TABLE fail inside the migration.
    _downgrade_to_v47(data, "CREATE TABLE session_browser_grants (marker TEXT);")
    store = SessionStore(data)
    store.initialize()
    assert store.connection is None
    assert store.health_code == "schema_migration_failed"
    store.close()
    path = data / "state.sqlite"
    connection = sqlite3.connect(path)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == V47_SCHEMA_VERSION
        columns = [row[1] for row in connection.execute("PRAGMA table_info(session_browser_grants)")]
        assert columns == ["marker"]
    finally:
        connection.close()


def test_shared_browser_fixture_matches_closed_contracts():
    import json
    from pathlib import Path

    from pydantic import ValidationError

    from eidos_runtime.db.events import BrowserGrantChangedPayload
    from eidos_runtime.models.browser import (
        BrowserCloseRequest, BrowserCloseResponse, BrowserGrantRevokeRequest,
        BrowserGrantRevokeResponse, BrowserStatus, BrowserStatusRequest,
    )

    fixture = json.loads((Path(__file__).parents[2] / "protocol/fixtures/browser-v1.json").read_text())
    BrowserStatusRequest.model_validate(fixture["statusRequest"])
    BrowserStatus.model_validate(fixture["status"])
    BrowserGrantRevokeRequest.model_validate(fixture["revokeRequest"])
    BrowserGrantRevokeResponse.model_validate(fixture["revokeResponse"])
    BrowserCloseRequest.model_validate(fixture["closeRequest"])
    BrowserCloseResponse.model_validate(fixture["closeResponse"])
    BrowserGrantChangedPayload.model_validate(fixture["grantChanged"])
    with pytest.raises(ValidationError):
        BrowserStatus.model_validate({**fixture["status"], "endpoint": "http://127.0.0.1:9222"})
    with pytest.raises(ValidationError):
        BrowserStatus.model_validate({**fixture["status"], "resource": "downloading"})

from __future__ import annotations

import sqlite3
import json
import zipfile

import pytest

from eidos_runtime.db.agent_roles_migration import migrate_agent_roles
from eidos_runtime.db.schema import V20_SCHEMA_SQL
from eidos_runtime.db.storage import SessionStore


def _legacy_database(path: str = ":memory:") -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.executescript(V20_SCHEMA_SQL + "\nPRAGMA user_version=20;")
    for name in ("parent", "explorer", "worker"):
        connection.execute(
            "INSERT INTO sessions(id,workspace_root,created_at,updated_at) VALUES(?, '/workspace',1,1)",
            (name,),
        )
        connection.execute(
            "INSERT INTO runs(id,session_id,user_input,model_profile_json,status,created_at,updated_at) "
            "VALUES(?,?, 'task','{}','queued',1,1)", (name + "-run", name),
        )
    for ordinal, role in enumerate(("explorer", "worker"), 1):
        connection.execute(
            "INSERT INTO items(id,session_id,run_id,ordinal,model_step_index,kind,status,content,created_at) "
            "VALUES(?, 'parent','parent-run',?,1,'tool_call','completed','',1)",
            ("spawn-" + role, ordinal),
        )
        connection.execute(
            "INSERT INTO agent_delegations(id,parent_run_id,child_session_id,child_run_id,spawn_item_id,"
            "task_name,role,task,created_at,required_for_completion) VALUES(?, 'parent-run',?,?,?, ?,?,'task',1,0)",
            (role + "-agent", role, role + "-run", "spawn-" + role, role, role),
        )
    connection.execute(
        "INSERT INTO agent_messages(id,parent_run_id,sender_session_id,recipient_session_id,content,created_at) "
        "VALUES('message','parent-run','parent','explorer','existing evidence',1)"
    )
    connection.execute(
        "INSERT INTO agent_waits(run_id,item_id,agent_ids_json,deadline_at,status) "
        "VALUES('parent-run',NULL,'[\"explorer-agent\"]',100,'pending')"
    )
    connection.execute("UPDATE runs SET status='waiting_agents' WHERE id='parent-run'")
    connection.commit()
    return connection


def test_role_migration_preserves_delegations_messages_waits_and_indexes() -> None:
    connection = _legacy_database()
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        delegations = connection.execute("SELECT * FROM agent_delegations ORDER BY id").fetchall()
        messages = connection.execute("SELECT * FROM agent_messages").fetchall()
        waits = connection.execute("SELECT * FROM agent_waits").fetchall()
        connection.execute("CREATE INDEX agent_role_lookup ON agent_delegations(role)")
        connection.commit()
        migrate_agent_roles(connection)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 21
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT * FROM agent_delegations ORDER BY id").fetchall() == delegations
        assert connection.execute("SELECT * FROM agent_messages").fetchall() == messages
        assert connection.execute("SELECT * FROM agent_waits").fetchall() == waits
        indexes = {row[1] for row in connection.execute("PRAGMA index_list(agent_delegations)")}
        assert {"agent_delegations_parent", "agent_role_lookup"} <= indexes
        connection.execute("UPDATE agent_delegations SET role='default' WHERE id='explorer-agent'")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE agent_delegations SET role='custom' WHERE id='worker-agent'")
    finally:
        connection.close()


def test_role_migration_rolls_back_when_existing_foreign_keys_are_invalid() -> None:
    connection = _legacy_database()
    try:
        connection.execute("UPDATE agent_delegations SET child_run_id='missing' WHERE role='worker'")
        connection.commit()
        with pytest.raises(sqlite3.IntegrityError, match="foreign key violation"):
            migrate_agent_roles(connection)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 20
        assert connection.execute("SELECT role FROM agent_delegations ORDER BY role").fetchall() == [("explorer",), ("worker",)]
        assert connection.execute("SELECT 1 FROM sqlite_master WHERE name='agent_delegations_roles'").fetchone() is None
        assert connection.execute("SELECT 1 FROM sqlite_master WHERE name='agent_delegations_parent'").fetchone() is not None
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("UPDATE agent_delegations SET role='default' WHERE role='explorer'")
    finally:
        connection.close()


def test_database_startup_upgrades_v20_and_keeps_existing_agent_roles(tmp_path) -> None:
    directory = tmp_path / "data"
    directory.mkdir(mode=0o700)
    database = directory / "state.sqlite"
    connection = _legacy_database(str(database))
    connection.close()
    database.chmod(0o600)
    store = SessionStore(directory)
    store.initialize()
    try:
        assert store.health()["state"] == "ready"
        assert store.connection.execute("PRAGMA user_version").fetchone()[0] == 21
        assert store.connection.execute("SELECT role FROM agent_delegations ORDER BY role").fetchall()[0][0] == "explorer"
        assert store.connection.execute("SELECT status FROM runs WHERE id='parent-run'").fetchone()[0] == "waiting_agents"
        assert store.connection.execute("SELECT status FROM agent_waits").fetchone()[0] == "pending"
        assert store.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        store.close()


def test_offline_v20_backup_restores_with_role_migration(tmp_path) -> None:
    from eidos_runtime.memory.backup import restore

    database = tmp_path / "legacy.sqlite"
    connection = _legacy_database(str(database))
    connection.close()
    archive_path = tmp_path / "backup.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.write(database, "state.sqlite")
        archive.writestr("manifest.json", json.dumps({
            "format": "eidos-memory-backup-v1", "schemaVersion": 20,
            "privacyEpochs": {}, "memoryRefs": [],
        }))
    destination = tmp_path / "restored"
    restore(archive_path, destination)
    with sqlite3.connect(destination / "state.sqlite") as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 21
        assert connection.execute("SELECT role FROM agent_delegations ORDER BY role").fetchall() == [("explorer",), ("worker",)]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []

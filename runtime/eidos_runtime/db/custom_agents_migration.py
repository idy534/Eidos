import sqlite3

from eidos_runtime.db.agent_roles_migration import AGENT_ROLE_CHECK

CUSTOM_AGENTS_SCHEMA_SQL = """
CREATE TABLE run_agent_catalogs (
    run_id TEXT PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
    snapshot_json TEXT NOT NULL
);
ALTER TABLE agent_delegations ADD COLUMN definition_json TEXT;
ALTER TABLE agent_delegations ADD COLUMN spawn_request_json TEXT;
"""


def migrate_custom_agents(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE name='agent_delegations' AND type='table'"
    ).fetchone()
    if row is None or AGENT_ROLE_CHECK not in row[0]:
        raise sqlite3.OperationalError("agent roles schema is invalid")
    rebuilt = (
        row[0]
        .replace(
            "CREATE TABLE agent_delegations", "CREATE TABLE agent_delegations_custom", 1
        )
        .replace(
            'CREATE TABLE "agent_delegations"',
            "CREATE TABLE agent_delegations_custom",
            1,
        )
        .replace(AGENT_ROLE_CHECK, "length(role) BETWEEN 1 AND 64", 1)
    )
    indexes = [
        row[0]
        for row in connection.execute(
            "SELECT sql FROM sqlite_master WHERE tbl_name='agent_delegations' AND type IN ('index','trigger') AND sql IS NOT NULL"
        )
    ]
    try:
        connection.executescript(
            "BEGIN IMMEDIATE;\n" + rebuilt + ";\n"
            "INSERT INTO agent_delegations_custom SELECT * FROM agent_delegations;\n"
            "DROP TABLE agent_delegations;\nALTER TABLE agent_delegations_custom RENAME TO agent_delegations;\n"
            + ";\n".join(indexes)
            + ";\n"
            + CUSTOM_AGENTS_SCHEMA_SQL
        )
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise sqlite3.IntegrityError("agent migration foreign key violation")
        connection.execute("PRAGMA user_version = 29")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise

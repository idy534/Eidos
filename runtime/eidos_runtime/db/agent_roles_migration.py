"""Preserve existing delegations while adding the built-in default role."""
import sqlite3


LEGACY_AGENT_ROLE_CHECK = "role IN ('explorer', 'worker')"
AGENT_ROLE_CHECK = "role IN ('default', 'explorer', 'worker')"


def migrate_agent_roles(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='agent_delegations'"
    ).fetchone()
    if row is None or LEGACY_AGENT_ROLE_CHECK not in row[0]:
        raise sqlite3.OperationalError("agent roles schema is invalid")
    table_sql = row[0]
    rebuilt = table_sql.replace(
        "CREATE TABLE agent_delegations", "CREATE TABLE agent_delegations_roles", 1
    ).replace(
        'CREATE TABLE "agent_delegations"', "CREATE TABLE agent_delegations_roles", 1
    ).replace(LEGACY_AGENT_ROLE_CHECK, AGENT_ROLE_CHECK, 1)
    indexes = [row[0] for row in connection.execute(
        "SELECT sql FROM sqlite_master WHERE tbl_name='agent_delegations' "
        "AND type IN ('index','trigger') AND sql IS NOT NULL"
    )]
    try:
        connection.executescript(
            "BEGIN IMMEDIATE;\n" + rebuilt + ";\n"
            "INSERT INTO agent_delegations_roles SELECT * FROM agent_delegations;\n"
            "DROP TABLE agent_delegations;\n"
            "ALTER TABLE agent_delegations_roles RENAME TO agent_delegations;\n"
            + ";\n".join(indexes) + ";\n"
        )
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise sqlite3.IntegrityError("agent roles migration foreign key violation")
        connection.execute("PRAGMA user_version = 21")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise

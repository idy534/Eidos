"""Upgrade the Run CHECK constraint using SQLite's table rebuild procedure."""
import sqlite3


def migrate_planning(connection: sqlite3.Connection, schema: str) -> None:
    table_sql = connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='runs'").fetchone()[0]
    indexes = [row[0] for row in connection.execute("SELECT sql FROM sqlite_master WHERE tbl_name='runs' AND type IN ('index', 'trigger') AND sql IS NOT NULL")]
    rebuilt = table_sql.replace('CREATE TABLE runs', 'CREATE TABLE runs_planning', 1).replace('CREATE TABLE "runs"', 'CREATE TABLE runs_planning', 1)
    rebuilt = rebuilt.replace("'waiting_approval',", "'waiting_approval', 'waiting_input',")
    connection.execute('PRAGMA foreign_keys = OFF')
    try:
        connection.executescript('BEGIN IMMEDIATE;\n' + rebuilt + ';\nINSERT INTO runs_planning SELECT * FROM runs;\nDROP TABLE runs;\nALTER TABLE runs_planning RENAME TO runs;\n' + ';\n'.join(indexes) + ';\n' + schema)
        if connection.execute('PRAGMA foreign_key_check').fetchone() is not None:
            raise sqlite3.IntegrityError('planning migration foreign key violation')
        connection.execute('PRAGMA user_version = 15')
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.execute('PRAGMA foreign_keys = ON')

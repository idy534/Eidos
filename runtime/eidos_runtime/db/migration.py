from __future__ import annotations

import sqlite3

from eidos_runtime.db.errors import StorageError
from eidos_runtime.db.migrations import v009_to_v010, v010_to_v011, v011_to_v012


def migrate_schema(
    connection: sqlite3.Connection,
    *,
    current_version: int,
    target_version: int,
) -> None:
    migration = {
        (v009_to_v010.FROM_VERSION, v009_to_v010.TO_VERSION): v009_to_v010,
        (v010_to_v011.FROM_VERSION, v010_to_v011.TO_VERSION): v010_to_v011,
        (v011_to_v012.FROM_VERSION, v011_to_v012.TO_VERSION): v011_to_v012,
    }.get((current_version, target_version))
    if migration is None:
        raise StorageError("schema_revision_unsupported")
    try:
        connection.execute("BEGIN IMMEDIATE")
        migration.migrate(connection)
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("integrity check failed")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise RuntimeError("foreign key check failed")
        connection.commit()
    except (
        v009_to_v010.InvalidV9SchemaError,
        v010_to_v011.InvalidV10SchemaError,
        v011_to_v012.InvalidV11SchemaError,
    ) as error:
        if connection.in_transaction:
            connection.rollback()
        raise StorageError("schema_revision_unsupported") from error
    except Exception as error:
        if connection.in_transaction:
            connection.rollback()
        raise StorageError("schema_migration_failed") from error

from __future__ import annotations

import sqlite3

from eidos_runtime.db.errors import StorageError
from eidos_runtime.db.migrations import v009_to_v010


def migrate_schema(
    connection: sqlite3.Connection,
    *,
    current_version: int,
    target_version: int,
) -> None:
    if (current_version, target_version) != (
        v009_to_v010.FROM_VERSION,
        v009_to_v010.TO_VERSION,
    ):
        raise StorageError("schema_revision_unsupported")
    try:
        connection.execute("BEGIN IMMEDIATE")
        v009_to_v010.migrate(connection)
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("integrity check failed")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise RuntimeError("foreign key check failed")
        connection.commit()
    except v009_to_v010.InvalidV9SchemaError as error:
        if connection.in_transaction:
            connection.rollback()
        raise StorageError("schema_revision_unsupported") from error
    except Exception as error:
        if connection.in_transaction:
            connection.rollback()
        raise StorageError("schema_migration_failed") from error

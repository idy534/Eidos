from __future__ import annotations

import sqlite3

from eidos_runtime.db.errors import StorageError
from eidos_runtime.db.migrations import (
    v009_to_v010,
    v010_to_v011,
    v011_to_v012,
    v012_to_v013,
    v013_to_v014,
    v014_to_v015,
    v015_to_v016,
    v016_to_v017,
    v017_to_v018,
)


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
        (v012_to_v013.FROM_VERSION, v012_to_v013.TO_VERSION): v012_to_v013,
        (v013_to_v014.FROM_VERSION, v013_to_v014.TO_VERSION): v013_to_v014,
        (v014_to_v015.FROM_VERSION, v014_to_v015.TO_VERSION): v014_to_v015,
        (v015_to_v016.FROM_VERSION, v015_to_v016.TO_VERSION): v015_to_v016,
        (v016_to_v017.FROM_VERSION, v016_to_v017.TO_VERSION): v016_to_v017,
        (v017_to_v018.FROM_VERSION, v017_to_v018.TO_VERSION): v017_to_v018,
    }.get((current_version, target_version))
    if migration is None:
        raise StorageError("schema_revision_unsupported")
    foreign_keys_disabled = (
        current_version,
        target_version,
    ) == (v017_to_v018.FROM_VERSION, v017_to_v018.TO_VERSION)
    if foreign_keys_disabled:
        # SQLite cannot replace a referenced parent table while foreign-key
        # enforcement is enabled. The migration remains atomic and runs the
        # integrity checks before re-enabling enforcement.
        connection.commit()
        connection.execute("PRAGMA foreign_keys = OFF")
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
        v012_to_v013.InvalidV12SchemaError,
        v013_to_v014.InvalidV13SchemaError,
        v014_to_v015.InvalidV14SchemaError,
        v015_to_v016.InvalidV15SchemaError,
        v016_to_v017.InvalidV16SchemaError,
        v017_to_v018.InvalidV17SchemaError,
    ) as error:
        if connection.in_transaction:
            connection.rollback()
        raise StorageError("schema_revision_unsupported") from error
    except Exception as error:
        if connection.in_transaction:
            connection.rollback()
        raise StorageError("schema_migration_failed") from error
    finally:
        if foreign_keys_disabled:
            connection.execute("PRAGMA foreign_keys = ON")

"""Explicit local data backup; only the current document-memory format is accepted."""

from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import stat
import tempfile
import zipfile
from typing import Literal

from pydantic import Field, ValidationError

from eidos_runtime.application.agent_catalog import AgentCatalog
from eidos_runtime.db.layout import collect_unreferenced_blobs, iter_blob_references
from eidos_runtime.db.json_blobs import JsonBlobStore
from eidos_runtime.db.schema import (
    SCHEMA_VERSION,
    V40_SCHEMA_VERSION,
    V41_SCHEMA_VERSION,
    V42_SCHEMA_VERSION,
    V43_SCHEMA_VERSION,
    V44_SCHEMA_VERSION,
    V45_SCHEMA_VERSION,
    V46_SCHEMA_VERSION,
)
from eidos_runtime.memory.publication import MemoryFiles
from eidos_runtime.memory.repository import MemoryRejected
from eidos_runtime.memory.service import MemoryService
from eidos_runtime.models import EidosFrozenStrictModel

FORMAT = "eidos-data-backup-v2"


class _Manifest(EidosFrozenStrictModel):
    format: Literal["eidos-data-backup-v2"]
    schema_version: int
    memory_refs: list[str] = Field(max_length=100000)
    encrypted: Literal[False]


def backup(service: MemoryService, destination: Path) -> None:
    destination = destination.absolute()
    if destination.suffix != ".zip":
        raise MemoryRejected("data_backup_path_invalid")
    database = service.database
    blobs = database.json_blobs
    descriptor, name = tempfile.mkstemp(prefix=".eidos-backup-", dir=destination.parent)
    os.fchmod(descriptor, 0o600)
    os.close(descriptor)
    staged = Path(name)
    try:
        with database.lock, blobs.lock, tempfile.TemporaryDirectory() as scratch:
            service.cleanup()
            from eidos_runtime.persistence.session_history import (
                SessionHistoryRepository,
            )

            history = SessionHistoryRepository(database)
            while (
                int(
                    database.connection()
                    .execute(
                        "SELECT value FROM memory_metadata WHERE key='history_cleanup_cursor'"
                    )
                    .fetchone()[0]
                )
                >= 0
            ):
                history.scrub_revoked_memory()
            collect_unreferenced_blobs(database, blobs)
            connection = database.connection()
            if connection.in_transaction:
                raise MemoryRejected("data_backup_busy")
            state = Path(scratch) / "state.sqlite"
            with sqlite3.connect(state) as copied:
                connection.backup(copied)
            references = {
                row[0]
                for row in connection.execute(
                    "SELECT file_ref FROM memory_revisions WHERE file_ref IS NOT NULL UNION SELECT file_ref FROM memory_indexes"
                )
            }
            with zipfile.ZipFile(
                staged, "w", compression=zipfile.ZIP_DEFLATED
            ) as archive:
                archive.write(state, "state.sqlite")
                for reference in sorted(references):
                    archive.writestr(
                        "memory/" + reference,
                        service.repository.files.read(reference).encode(),
                    )
                for path in blobs.root.rglob("*.json.gz"):
                    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                    with os.fdopen(descriptor, "rb") as stream:
                        metadata = os.fstat(stream.fileno())
                        if (
                            not stat.S_ISREG(metadata.st_mode)
                            or metadata.st_nlink != 1
                            or metadata.st_size > 32 * 1024 * 1024
                        ):
                            raise MemoryRejected("data_backup_blob_invalid")
                        archive.writestr(
                            "blobs/" + path.relative_to(blobs.root).as_posix(),
                            stream.read(),
                        )
                if database.data_directory is not None:
                    for agent_name, raw in AgentCatalog(
                        database.data_directory
                    ).backup_files():
                        archive.writestr("agents/" + agent_name, raw)
                archive.writestr(
                    "manifest.json",
                    json.dumps(
                        {
                            "format": FORMAT,
                            "schemaVersion": SCHEMA_VERSION,
                            "memoryRefs": sorted(references),
                            "encrypted": False,
                        }
                    ),
                )
        with staged.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(staged, destination)
    finally:
        staged.unlink(missing_ok=True)


def restore(archive_path: Path, destination: Path) -> None:
    if destination.exists():
        raise MemoryRejected("data_restore_requires_new_directory")
    staged = Path(tempfile.mkdtemp(prefix=".eidos-restore-", dir=destination.parent))
    try:
        with zipfile.ZipFile(archive_path) as archive:
            entries = archive.infolist()
            if (
                len(entries) > 100000
                or sum(item.file_size for item in entries) > 1024 * 1024 * 1024
            ):
                raise MemoryRejected("data_restore_too_large")
            names: set[str] = set()
            for item in entries:
                relative = PurePosixPath(item.filename)
                if (
                    item.filename in names
                    or not relative.parts
                    or str(relative) != item.filename
                    or relative.is_absolute()
                    or ".." in relative.parts
                    or "\\" in item.filename
                    or stat.S_ISLNK(item.external_attr >> 16)
                    or item.is_dir()
                    or (item.external_attr >> 16 & 0o170000) not in {0, stat.S_IFREG}
                    or (
                        item.filename not in {"manifest.json", "state.sqlite"}
                        and relative.parts[0] not in {"memory", "blobs", "agents"}
                    )
                ):
                    raise MemoryRejected("data_restore_member_invalid")
                names.add(item.filename)
                if relative.parts[0] == "memory":
                    try:
                        MemoryFiles.reference_parts("/".join(relative.parts[1:]))
                    except ValueError:
                        raise MemoryRejected("data_restore_memory_invalid") from None
            if (
                "manifest.json" not in names
                or archive.getinfo("manifest.json").file_size > 16 * 1024 * 1024
            ):
                raise MemoryRejected("data_restore_manifest_invalid")
            try:
                manifest = _Manifest.model_validate_json(archive.read("manifest.json"))
            except ValidationError:
                raise MemoryRejected("data_restore_format_unsupported") from None
            if manifest.schema_version not in {
                V40_SCHEMA_VERSION,
                V41_SCHEMA_VERSION,
                V42_SCHEMA_VERSION,
                V43_SCHEMA_VERSION,
                V44_SCHEMA_VERSION,
                V45_SCHEMA_VERSION,
                V46_SCHEMA_VERSION,
                SCHEMA_VERSION,
            }:
                raise MemoryRejected("data_restore_format_unsupported")
            for item in entries:
                relative = PurePosixPath(item.filename)
                path = staged.joinpath(*relative.parts)
                directory = staged
                for part in relative.parts[:-1]:
                    directory = directory / part
                    directory.mkdir(exist_ok=True, mode=0o700)
                with archive.open(item) as source, path.open("xb") as target:
                    shutil.copyfileobj(source, target, 64 * 1024)
                    target.flush()
                    os.fsync(target.fileno())
                path.chmod(0o600)
            if "state.sqlite" not in names:
                raise MemoryRejected("data_restore_database_missing")
        with sqlite3.connect(staged / "state.sqlite") as connection:
            if (
                connection.execute("PRAGMA user_version").fetchone()[0]
                != manifest.schema_version
                or connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
            ):
                raise MemoryRejected("data_restore_database_invalid")
            expected = {
                row[0]
                for row in connection.execute(
                    "SELECT file_ref FROM memory_revisions WHERE file_ref IS NOT NULL UNION SELECT file_ref FROM memory_indexes"
                )
            }
            if expected != set(manifest.memory_refs):
                raise MemoryRejected("data_restore_memory_invalid")
            for reference in expected:
                try:
                    MemoryFiles.reference_parts(reference)
                except ValueError:
                    raise MemoryRejected("data_restore_memory_invalid") from None
                path = staged / "memory" / reference
                if not path.is_file():
                    raise MemoryRejected("data_restore_memory_invalid")
                metadata = path.stat()
                connection.execute(
                    "UPDATE memory_revisions SET file_size=?,file_mtime_ns=? WHERE file_ref=?",
                    (metadata.st_size, metadata.st_mtime_ns, reference),
                )
            if manifest.schema_version == V40_SCHEMA_VERSION:
                from eidos_runtime.memory.revocation_migration import migrate_memory_source_revocation

                connection.commit()
                migrate_memory_source_revocation(connection)
            if connection.execute("PRAGMA user_version").fetchone()[0] == V41_SCHEMA_VERSION:
                from eidos_runtime.memory.remove_temporary_migration import migrate_remove_temporary_memory

                connection.commit()
                migrate_remove_temporary_memory(connection)
            if connection.execute("PRAGMA user_version").fetchone()[0] == V42_SCHEMA_VERSION:
                from eidos_runtime.memory.flow_migration import migrate_memory_flow
                migrate_memory_flow(connection)
            if connection.execute("PRAGMA user_version").fetchone()[0] == V43_SCHEMA_VERSION:
                from eidos_runtime.db.personalization_migration import migrate_personalization
                connection.commit()
                migrate_personalization(connection)
            if connection.execute("PRAGMA user_version").fetchone()[0] == V44_SCHEMA_VERSION:
                from eidos_runtime.db.project_workspaces_migration import migrate_project_workspaces
                connection.commit()
                migrate_project_workspaces(connection)
            if connection.execute("PRAGMA user_version").fetchone()[0] == V45_SCHEMA_VERSION:
                from eidos_runtime.db.project_binding_migration import migrate_project_bindings
                connection.commit()
                migrate_project_bindings(connection)
            if connection.execute("PRAGMA user_version").fetchone()[0] == V46_SCHEMA_VERSION:
                from eidos_runtime.db.worktree_git_identity_migration import migrate_worktree_git_identity
                connection.commit()
                migrate_worktree_git_identity(connection)
            connection.execute("UPDATE memory_scopes SET mode='read',privacy_epoch=privacy_epoch+1")
            connection.execute(
                "UPDATE memory_metadata SET value='0' WHERE key='legacy_cleanup_pending'"
            )
            JsonBlobStore(staged).verify_references(
                iter_blob_references(connection), byte_limit=1024 * 1024 * 1024
            )
        staged.rename(destination)
    except BaseException:
        shutil.rmtree(staged)
        raise


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Restore a current Eidos backup offline into a new directory"
    )
    parser.add_argument("archive", type=Path)
    parser.add_argument("destination", type=Path)
    arguments = parser.parse_args()
    restore(arguments.archive, arguments.destination)

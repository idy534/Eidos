from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import stat
import tempfile
import zipfile

from eidos_runtime.db.layout import collect_unreferenced_blobs
from eidos_runtime.db.schema import SCHEMA_VERSION
from eidos_runtime.memory.contracts import MemorySettings
from eidos_runtime.memory.publication import MemoryFiles
from eidos_runtime.memory.repository import MemoryRejected
from eidos_runtime.memory.service import MemoryService


def backup(service: MemoryService, destination: Path) -> None:
    """Online SQLite backup and immutable bodies, protected by the cleanup lock.

    This is an explicitly requested plaintext archive. Provider configuration and
    credentials are not exported. Workspace files are not part of this archive.
    """
    database = service.database
    blobs = database.json_blobs
    destination = destination.absolute()
    if destination.suffix != ".zip":
        raise MemoryRejected("memory_backup_path_invalid")
    descriptor, name = tempfile.mkstemp(prefix=".eidos-backup-", dir=destination.parent)
    os.fchmod(descriptor, 0o600)
    os.close(descriptor)
    staged = Path(name)
    try:
        with database.lock, blobs.lock, tempfile.TemporaryDirectory() as scratch:
            # Cleanup and the copied state share the privacy publication lock.
            service.cleanup()
            collect_unreferenced_blobs(database, blobs)
            connection = database.connection()
            if connection.in_transaction:
                raise MemoryRejected("memory_backup_busy")
            state = Path(scratch) / "state.sqlite"
            with sqlite3.connect(state) as copied:
                connection.backup(copied)
            references = {
                row[0]
                for row in connection.execute(
                    "SELECT file_ref FROM memory_revisions WHERE file_ref IS NOT NULL"
                )
            }
            for row in connection.execute(
                "SELECT summary_ref,catalog_ref FROM memory_generations"
            ):
                references.update(row)
            manifest = {
                "format": "eidos-memory-backup-v1",
                "schemaVersion": SCHEMA_VERSION,
                "encrypted": False,
                "privacyEpochs": {
                    r[0]: r[1]
                    for r in connection.execute(
                        "SELECT id,privacy_epoch FROM memory_scopes"
                    )
                },
                "memoryRefs": sorted(references),
                "warning": "Contains private chat and memory data. Old backups can restore previously forgotten content. Provider configuration and workspace files are excluded.",
            }
            with zipfile.ZipFile(
                staged, "w", compression=zipfile.ZIP_DEFLATED
            ) as archive:
                archive.write(state, "state.sqlite")
                for reference in sorted(references):
                    revision = connection.execute(
                        "SELECT file_size,file_mtime_ns FROM memory_revisions WHERE file_ref=?",
                        (reference,),
                    ).fetchone()
                    if revision is not None and tuple(
                        revision
                    ) != service.repository.files.signature(reference):
                        raise MemoryRejected("memory_body_unavailable")
                    body = service.repository.files.read(reference)
                    archive.writestr("memory/" + reference, body.encode())
                for path in sorted(blobs.root.rglob("*.json.gz")):
                    # JSON blobs are the existing immutable exact-context/input
                    # store. Copy under its lock so its GC cannot remove a block.
                    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                    with os.fdopen(descriptor, "rb") as stream:
                        metadata = os.fstat(stream.fileno())
                        if (
                            not stat.S_ISREG(metadata.st_mode)
                            or metadata.st_nlink != 1
                            or metadata.st_size > 32 * 1024 * 1024
                        ):
                            raise MemoryRejected("memory_backup_blob_invalid")
                        archive.writestr(
                            "blobs/" + path.relative_to(blobs.root).as_posix(),
                            stream.read(),
                        )
                archive.writestr(
                    "manifest.json", json.dumps(manifest, ensure_ascii=False)
                )
        with staged.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(staged, destination)
    finally:
        staged.unlink(missing_ok=True)


def restore(archive_path: Path, destination: Path) -> None:
    """Restore offline to a new data directory; never merge or overwrite epochs."""
    if destination.exists():
        raise MemoryRejected("memory_restore_requires_new_directory")
    staged = Path(tempfile.mkdtemp(prefix=".eidos-restore-", dir=destination.parent))
    try:
        with zipfile.ZipFile(archive_path) as archive:
            if archive.getinfo("manifest.json").file_size > 1024 * 1024:
                raise MemoryRejected("memory_backup_manifest_invalid")
            manifest = json.loads(archive.read("manifest.json"))
            if (
                manifest.get("format") != "eidos-memory-backup-v1"
                or manifest.get("schemaVersion") != SCHEMA_VERSION
            ):
                raise MemoryRejected("memory_backup_incompatible")
            seen: set[str] = set()
            total = 0
            for item in archive.infolist():
                parts = PurePosixPath(item.filename).parts
                total += item.file_size
                if (
                    not parts
                    or item.filename in seen
                    or item.filename.startswith("/")
                    or ".." in parts
                    or "\\" in item.filename
                    or total > 1024 * 1024 * 1024
                    or (
                        parts[0] not in {"memory", "blobs"}
                        and item.filename not in {"manifest.json", "state.sqlite"}
                    )
                    or stat.S_ISLNK(item.external_attr >> 16)
                ):
                    raise MemoryRejected("memory_backup_invalid")
                seen.add(item.filename)
                target = staged.joinpath(*parts)
                parent = staged
                for part in parts[:-1]:
                    parent = parent / part
                    parent.mkdir(exist_ok=True, mode=0o700)
                with target.open("xb") as stream, archive.open(item) as source:
                    os.chmod(target, 0o600)
                    shutil.copyfileobj(source, stream)
        files = MemoryFiles(staged)
        with sqlite3.connect(staged / "state.sqlite") as connection:
            if (
                connection.execute("PRAGMA user_version").fetchone()[0]
                != SCHEMA_VERSION
                or connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
                or connection.execute("PRAGMA foreign_key_check").fetchone()
            ):
                raise MemoryRejected("memory_backup_invalid")
            epochs = {
                r[0]: r[1]
                for r in connection.execute(
                    "SELECT id,privacy_epoch FROM memory_scopes"
                )
            }
            if epochs != manifest["privacyEpochs"]:
                raise MemoryRejected("memory_backup_manifest_invalid")
            references = {
                r[0]
                for r in connection.execute(
                    "SELECT file_ref FROM memory_revisions WHERE file_ref IS NOT NULL"
                )
            }
            for row in connection.execute(
                "SELECT summary_ref,catalog_ref FROM memory_generations"
            ):
                references.update(row)
            if references != set(manifest["memoryRefs"]):
                raise MemoryRejected("memory_backup_manifest_invalid")
            for reference in references:
                files.read(reference)
                size, modified = files.signature(reference)
                connection.execute(
                    "UPDATE memory_revisions SET file_size=?,file_mtime_ns=? WHERE file_ref=?",
                    (size, modified, reference),
                )
            # An old backup cannot know subsequent privacy revocations. Preserve
            # its tombstones, but require fresh user consent before any learning.
            for scope_id, settings_json in connection.execute(
                "SELECT id,settings_json FROM memory_scopes"
            ).fetchall():
                settings = MemorySettings.model_validate_json(settings_json).model_copy(
                    update={"generate_enabled": False}
                )
                connection.execute(
                    "UPDATE memory_scopes SET settings_json=? WHERE id=?",
                    (settings.model_dump_json(), scope_id),
                )
            connection.execute("UPDATE memory_sources SET backfill_enabled=0")
            connection.execute(
                "UPDATE memory_jobs SET state='canceled',lease_token=NULL,lease_until=NULL WHERE state IN ('queued','running','retry_wait','paused_budget','blocked_model')"
            )
        os.rename(staged, destination)
    finally:
        if staged.exists():
            shutil.rmtree(staged)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Restore an Eidos plaintext memory/state backup offline into a NEW data directory."
    )
    parser.add_argument("archive", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    restore(args.archive, args.destination)

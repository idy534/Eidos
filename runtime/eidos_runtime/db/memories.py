from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import stat
import tempfile
import threading
import time
from typing import Mapping

from eidos_runtime.db.errors import StorageError
from eidos_runtime.db.thread_history import SqliteConnectionOwner


_MAX_MEMORY_BYTES = 4 * 1024 * 1024


@dataclass(frozen=True)
class MemoryRecord:
    id: str
    kind: str
    content: str
    content_sha256: str
    metadata: dict[str, str]
    created_at: int
    updated_at: int


class MemoryStore:
    """Content-addressed memory bodies with SQLite metadata authority."""

    def __init__(
        self,
        data_directory: Path,
        database: SqliteConnectionOwner,
    ) -> None:
        self.root = data_directory / "memories"
        self.database = database
        self.lock = threading.RLock()
        _prepare_private_directory(self.root)
        _prepare_private_directory(self.root / "content")
        self.garbage_collect()

    def put(
        self,
        *,
        memory_id: str,
        kind: str,
        content: str,
        metadata: Mapping[str, str],
    ) -> MemoryRecord:
        with self.lock:
            return self._put(
                memory_id=memory_id,
                kind=kind,
                content=content,
                metadata=metadata,
            )

    def _put(
        self,
        *,
        memory_id: str,
        kind: str,
        content: str,
        metadata: Mapping[str, str],
    ) -> MemoryRecord:
        if not memory_id or not kind or not _safe_kind(kind):
            raise ValueError("memory identity is invalid")
        if not all(isinstance(key, str) and isinstance(value, str) for key, value in metadata.items()):
            raise ValueError("memory metadata is invalid")
        raw = content.encode("utf-8")
        if len(raw) > _MAX_MEMORY_BYTES:
            raise ValueError("memory content exceeds size limit")
        digest = hashlib.sha256(raw).hexdigest()
        relative_path = f"content/{digest[:2]}/{digest}.md"
        path = self.root.joinpath(*PurePosixPath(relative_path).parts)
        _prepare_private_directory(path.parent)
        _write_content(path, raw)
        now = time.time_ns() // 1_000_000
        encoded_metadata = json.dumps(
            dict(metadata),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        with self.database.transaction() as connection:
            existing = connection.execute(
                "SELECT created_at FROM memory_records WHERE id = ?",
                (memory_id,),
            ).fetchone()
            created_at = int(existing[0]) if existing is not None else now
            connection.execute(
                """
                INSERT INTO memory_records (
                    id, kind, relative_path, content_sha256, metadata_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    kind = excluded.kind,
                    relative_path = excluded.relative_path,
                    content_sha256 = excluded.content_sha256,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                (
                    memory_id,
                    kind,
                    relative_path,
                    digest,
                    encoded_metadata,
                    created_at,
                    now,
                ),
            )
        self.garbage_collect()
        return self.read(memory_id)

    def read(self, memory_id: str) -> MemoryRecord:
        with self.lock:
            return self._read(memory_id)

    def _read(self, memory_id: str) -> MemoryRecord:
        row = self.database.connection().execute(
            "SELECT * FROM memory_records WHERE id = ?", (memory_id,)
        ).fetchone()
        if row is None:
            raise LookupError("memory not found")
        digest = str(row["content_sha256"])
        expected_path = f"content/{digest[:2]}/{digest}.md"
        if str(row["relative_path"]) != expected_path:
            raise StorageError("memory_path_invalid")
        path = self.root.joinpath(*PurePosixPath(expected_path).parts)
        raw = _read_content(path)
        if hashlib.sha256(raw).hexdigest() != digest:
            raise StorageError("memory_checksum_invalid")
        try:
            content = raw.decode("utf-8")
            metadata = json.loads(row["metadata_json"])
        except (UnicodeDecodeError, TypeError, json.JSONDecodeError) as error:
            raise StorageError("memory_record_invalid") from error
        if not isinstance(metadata, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in metadata.items()
        ):
            raise StorageError("memory_record_invalid")
        return MemoryRecord(
            id=str(row["id"]),
            kind=str(row["kind"]),
            content=content,
            content_sha256=digest,
            metadata=metadata,
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
        )

    def garbage_collect(self) -> int:
        with self.lock:
            return self._garbage_collect()

    def _garbage_collect(self) -> int:
        retained = {
            str(row[0])
            for row in self.database.connection().execute(
                "SELECT relative_path FROM memory_records"
            )
        }
        deleted = 0
        content_root = self.root / "content"
        for directory, directory_names, file_names in os.walk(
            content_root, topdown=True, followlinks=False
        ):
            base = Path(directory)
            for name in directory_names:
                if (base / name).is_symlink():
                    raise StorageError("memory_directory_invalid")
            directory_deleted = False
            for name in file_names:
                path = base / name
                if path.is_symlink():
                    raise StorageError("memory_file_invalid")
                relative = path.relative_to(self.root).as_posix()
                if relative in retained:
                    continue
                _read_content(path)
                path.unlink()
                deleted += 1
                directory_deleted = True
            if directory_deleted:
                _fsync_directory(base)
        return deleted


def _write_content(path: Path, value: bytes) -> None:
    if path.exists():
        if _read_content(path) != value:
            raise StorageError("memory_content_conflict")
        return
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.stem}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=True) as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        if temporary.exists():
            temporary.unlink()
        raise


def _read_content(path: Path) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.getuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_size > _MAX_MEMORY_BYTES
        ):
            raise StorageError("memory_file_invalid")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
    finally:
        os.close(descriptor)


def _safe_kind(value: str) -> bool:
    return all(
        character.isascii() and (character.isalnum() or character in "-_")
        for character in value
    )


def _prepare_private_directory(path: Path) -> None:
    if path.is_symlink():
        raise StorageError("memory_directory_invalid")
    if not path.exists():
        path.mkdir(mode=0o700, parents=True)
    metadata = path.stat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise StorageError("memory_directory_invalid")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = ["MemoryRecord", "MemoryStore"]

from __future__ import annotations

from dataclasses import dataclass
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import stat
import tempfile
import threading
from typing import Iterable
import zlib

from eidos_runtime.db.errors import StorageError


_REFERENCE_KEY = "$eidosBlob"
_REFERENCE_VERSION = 1
_MAX_JSON_BLOB_BYTES = 32 * 1024 * 1024


class JsonBlobCorruptionError(StorageError):
    pass


@dataclass(frozen=True)
class JsonBlobReference:
    kind: str
    relative_path: str
    sha256: str
    uncompressed_bytes: int
    compressed_bytes: int

    def to_json(self) -> str:
        return json.dumps(
            {
                _REFERENCE_KEY: {
                    "version": _REFERENCE_VERSION,
                    "kind": self.kind,
                    "relativePath": self.relative_path,
                    "sha256": self.sha256,
                    "uncompressedBytes": self.uncompressed_bytes,
                    "compressedBytes": self.compressed_bytes,
                }
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, value: str) -> JsonBlobReference | None:
        try:
            envelope = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return None
        if not isinstance(envelope, dict) or set(envelope) != {_REFERENCE_KEY}:
            return None
        raw = envelope[_REFERENCE_KEY]
        if not isinstance(raw, dict) or set(raw) != {
            "version",
            "kind",
            "relativePath",
            "sha256",
            "uncompressedBytes",
            "compressedBytes",
        }:
            raise JsonBlobCorruptionError("blob reference is invalid")
        version = raw["version"]
        kind = raw["kind"]
        relative_path = raw["relativePath"]
        digest = raw["sha256"]
        uncompressed_bytes = raw["uncompressedBytes"]
        compressed_bytes = raw["compressedBytes"]
        if version != _REFERENCE_VERSION:
            raise JsonBlobCorruptionError("blob reference version is invalid")
        if (
            not isinstance(kind, str)
            or not kind
            or not _safe_kind(kind)
            or not isinstance(relative_path, str)
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or not isinstance(uncompressed_bytes, int)
            or isinstance(uncompressed_bytes, bool)
            or not 0 <= uncompressed_bytes <= _MAX_JSON_BLOB_BYTES
            or not isinstance(compressed_bytes, int)
            or isinstance(compressed_bytes, bool)
            or not 0 < compressed_bytes <= _MAX_JSON_BLOB_BYTES
        ):
            raise JsonBlobCorruptionError("blob reference is invalid")
        expected = _relative_path(kind, digest)
        if relative_path != expected:
            raise JsonBlobCorruptionError("blob reference path is invalid")
        return cls(
            kind=kind,
            relative_path=relative_path,
            sha256=digest,
            uncompressed_bytes=uncompressed_bytes,
            compressed_bytes=compressed_bytes,
        )


class JsonBlobStore:
    """Content-addressed gzip storage for immutable bounded JSON payloads."""

    def __init__(self, data_directory: Path) -> None:
        self.root = data_directory / "blobs"
        self.lock = threading.RLock()
        _prepare_private_directory(self.root)

    def put_json(self, kind: str, value: str) -> str:
        with self.lock:
            return self._put_json(kind, value)

    def _put_json(self, kind: str, value: str) -> str:
        if not _safe_kind(kind):
            raise ValueError("blob kind is invalid")
        raw = value.encode("utf-8")
        if len(raw) > _MAX_JSON_BLOB_BYTES:
            raise ValueError("json blob exceeds size limit")
        try:
            json.loads(value)
        except json.JSONDecodeError as error:
            raise ValueError("json blob is invalid") from error
        digest = hashlib.sha256(raw).hexdigest()
        compressed = gzip.compress(raw, mtime=0)
        relative_path = _relative_path(kind, digest)
        destination = self.root.joinpath(*PurePosixPath(relative_path).parts)
        _prepare_private_directory(destination.parent.parent)
        _prepare_private_directory(destination.parent)
        if destination.exists():
            reference = JsonBlobReference(
                kind=kind,
                relative_path=relative_path,
                sha256=digest,
                uncompressed_bytes=len(raw),
                compressed_bytes=len(compressed),
            )
            if self.read(reference) != value:
                raise JsonBlobCorruptionError("existing blob content is invalid")
            return reference.to_json()

        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{digest}.", suffix=".tmp", dir=destination.parent
        )
        temporary = Path(temporary_name)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                stream.write(compressed)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, destination)
            _fsync_directory(destination.parent)
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            if temporary.exists():
                temporary.unlink()
            raise
        reference = JsonBlobReference(
            kind=kind,
            relative_path=relative_path,
            sha256=digest,
            uncompressed_bytes=len(raw),
            compressed_bytes=len(compressed),
        )
        return reference.to_json()

    def read_json(self, stored: str, *, expected_kind: str) -> str:
        reference = JsonBlobReference.from_json(stored)
        if reference is None:
            return stored
        if reference.kind != expected_kind:
            raise JsonBlobCorruptionError("blob reference kind is invalid")
        return self.read(reference)

    def read(self, reference: JsonBlobReference) -> str:
        with self.lock:
            return self._read(reference)

    def _read(self, reference: JsonBlobReference) -> str:
        path = self.root.joinpath(*PurePosixPath(reference.relative_path).parts)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError as error:
            raise JsonBlobCorruptionError("blob file is unavailable") from error
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != os.getuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600
                or metadata.st_size != reference.compressed_bytes
            ):
                raise JsonBlobCorruptionError("blob file metadata is invalid")
            compressed = _read_bounded(descriptor, reference.compressed_bytes)
        finally:
            os.close(descriptor)
        try:
            with gzip.GzipFile(fileobj=io.BytesIO(compressed)) as stream:
                raw = stream.read(reference.uncompressed_bytes + 1)
        except (gzip.BadGzipFile, EOFError, zlib.error) as error:
            raise JsonBlobCorruptionError("blob compression is invalid") from error
        if (
            len(raw) != reference.uncompressed_bytes
            or hashlib.sha256(raw).hexdigest() != reference.sha256
        ):
            raise JsonBlobCorruptionError("blob checksum is invalid")
        try:
            value = raw.decode("utf-8")
            json.loads(value)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise JsonBlobCorruptionError("blob json is invalid") from error
        return value

    def garbage_collect(self, retained_references: Iterable[str]) -> int:
        with self.lock:
            return self._garbage_collect(retained_references)

    def _garbage_collect(self, retained_references: Iterable[str]) -> int:
        retained: set[str] = set()
        for stored in retained_references:
            reference = JsonBlobReference.from_json(stored)
            if reference is not None:
                retained.add(reference.relative_path)
        deleted = 0
        for directory, directory_names, file_names in os.walk(
            self.root, topdown=True, followlinks=False
        ):
            base = Path(directory)
            safe_directories: list[str] = []
            for name in directory_names:
                candidate = base / name
                if candidate.is_symlink():
                    raise JsonBlobCorruptionError(
                        "blob directory must not be a symlink"
                    )
                safe_directories.append(name)
            directory_names[:] = safe_directories
            for name in file_names:
                path = base / name
                if path.is_symlink():
                    raise JsonBlobCorruptionError("blob file is invalid")
                relative = path.relative_to(self.root).as_posix()
                if relative in retained:
                    continue
                metadata = path.stat()
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_uid != os.getuid()
                ):
                    raise JsonBlobCorruptionError("blob file is invalid")
                path.unlink()
                deleted += 1
            if deleted:
                _fsync_directory(base)
        return deleted


def _relative_path(kind: str, digest: str) -> str:
    return f"{kind}/{digest[:2]}/{digest}.json.gz"


def _safe_kind(value: str) -> bool:
    return bool(value) and all(
        character.isascii() and (character.isalnum() or character == "-")
        for character in value
    )


def _read_bounded(descriptor: int, expected_bytes: int) -> bytes:
    chunks: list[bytes] = []
    remaining = expected_bytes
    while remaining:
        chunk = os.read(descriptor, min(remaining, 64 * 1024))
        if not chunk:
            raise JsonBlobCorruptionError("blob file is truncated")
        chunks.append(chunk)
        remaining -= len(chunk)
    if os.read(descriptor, 1):
        raise JsonBlobCorruptionError("blob file exceeds expected size")
    return b"".join(chunks)


def _prepare_private_directory(path: Path) -> None:
    if path.is_symlink():
        raise JsonBlobCorruptionError("blob directory must not be a symlink")
    if not path.exists():
        path.mkdir(mode=0o700, parents=True)
    metadata = path.stat()
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise JsonBlobCorruptionError("blob directory metadata is invalid")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


__all__ = [
    "JsonBlobCorruptionError",
    "JsonBlobReference",
    "JsonBlobStore",
]

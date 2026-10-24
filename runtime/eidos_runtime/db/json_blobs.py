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
            or uncompressed_bytes < 0
            or not isinstance(compressed_bytes, int)
            or isinstance(compressed_bytes, bool)
            or compressed_bytes <= 0
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
    """Content-addressed JSON storage; callers own payload budgets.

    Reads remain bounded by the persisted byte counts and verify the file and
    digest. Snapshot persistence does not impose a separate task size ceiling.
    """

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
        try:
            json.loads(value)
        except json.JSONDecodeError as error:
            raise ValueError("json blob is invalid") from error
        digest = hashlib.sha256(raw).hexdigest()
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
                compressed_bytes=destination.lstat().st_size,
            )
            if self.read(reference) != value:
                raise JsonBlobCorruptionError("existing blob content is invalid")
            return reference.to_json()

        compressed = gzip.compress(raw, mtime=0)
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
                chunks: list[bytes] = []
                remaining = reference.uncompressed_bytes + 1
                while remaining:
                    chunk = stream.read(min(remaining, 64 * 1024))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    remaining -= len(chunk)
                raw = b"".join(chunks)
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

    def garbage_collect(
        self, retained_references: Iterable[str], *, cancel: threading.Event | None = None,
    ) -> int:
        with self.lock:
            return self._garbage_collect(retained_references, cancel=cancel)

    def verify_references(self, references: Iterable[str], *, byte_limit: int) -> None:
        """Verify an offline restore, including leaf blobs and nested blocks."""
        with self.lock:
            seen: set[str] = set()
            used = 0
            for stored in references:
                reference = JsonBlobReference.from_json(stored)
                pending = [reference] if reference else []
                while pending:
                    value = pending.pop()
                    if value.relative_path in seen:
                        continue
                    seen.add(value.relative_path)
                    used += value.uncompressed_bytes
                    if used > byte_limit or len(seen) > 100000:
                        raise JsonBlobCorruptionError("restore blob limit exceeded")
                    self._read(value)
                    for child_json, kind in self._request_children(value):
                        child = JsonBlobReference.from_json(child_json)
                        if child is None or child.kind != kind:
                            raise JsonBlobCorruptionError(
                                "context block reference is invalid"
                            )
                        pending.append(child)

    def _garbage_collect(
        self, retained_references: Iterable[str], *, cancel: threading.Event | None = None,
    ) -> int:
        if cancel is not None and cancel.is_set():
            return 0
        retained: set[str] = set()
        # Resolve every reference closure before deleting anything. Process one
        # root at a time so the SQLite reference iterator remains streaming.
        for stored in retained_references:
            if cancel is not None and cancel.is_set():
                return 0
            reference = JsonBlobReference.from_json(stored)
            pending = [reference] if reference is not None else []
            while pending:
                if cancel is not None and cancel.is_set():
                    return 0
                reference = pending.pop()
                if reference.relative_path in retained:
                    continue
                retained.add(reference.relative_path)
                for value, kind in self._request_children(reference):
                    child = JsonBlobReference.from_json(value)
                    if child is None or child.kind != kind:
                        raise JsonBlobCorruptionError("context block reference is invalid")
                    pending.append(child)
        deleted = 0
        for directory, directory_names, file_names in os.walk(
            self.root, topdown=True, followlinks=False
        ):
            if cancel is not None and cancel.is_set():
                return deleted
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
            directory_deleted = False
            for name in file_names:
                if cancel is not None and cancel.is_set():
                    if directory_deleted:
                        _fsync_directory(base)
                    return deleted
                path = base / name
                if path.is_symlink():
                    raise JsonBlobCorruptionError("blob file is invalid")
                relative = path.relative_to(self.root).as_posix()
                if relative in retained:
                    continue
                metadata = path.stat()
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
                    raise JsonBlobCorruptionError("blob file is invalid")
                path.unlink()
                deleted += 1
                directory_deleted = True
            if directory_deleted:
                _fsync_directory(base)
        return deleted

    def _request_children(self, reference: JsonBlobReference) -> list[tuple[str, str]]:
        from eidos_runtime.db.request_blocks import StoredContextItem, StoredStepResolution

        if reference.kind not in {"context-snapshot-v2", "step-resolution-v2", "context-item"}:
            return []
        manifest = json.loads(self._read(reference))
        if not isinstance(manifest, dict):
            raise JsonBlobCorruptionError("context manifest is invalid")
        try:
            if reference.kind == "context-item":
                if "$eidosContextItem" not in manifest:
                    return []  # Existing inline content blocks remain supported.
                item = StoredContextItem.model_validate_json(json.dumps(manifest))
                return [
                    (value, kind) for value, kind in (
                        (item.tool_content_ref, "tool-result-content"),
                        (item.input_image_ref, "context-image"),
                    ) if value is not None
                ]
            if reference.kind == "step-resolution-v2":
                step = StoredStepResolution.model_validate_json(json.dumps(manifest))
                return [
                    *((value, "context-item") for value in step.model_context_refs),
                    (step.tool_definitions_ref, "context-tools"),
                ]
            if (
                set(manifest) != {"$eidosContextBlocks", "snapshot", "modelContextRefs", "toolDefinitionsRef"}
                or manifest["$eidosContextBlocks"] != 1
                or not isinstance(manifest["snapshot"], dict)
                or not isinstance(manifest["modelContextRefs"], list)
                or len(manifest["modelContextRefs"]) > 10000
            ):
                raise ValueError("invalid context manifest")
            children = [
                *((value, "context-item") for value in manifest["modelContextRefs"]),
                (manifest["toolDefinitionsRef"], "context-tools"),
            ]
            if any(not isinstance(value, str) for value, _kind in children):
                raise ValueError("invalid context reference")
            return children
        except (TypeError, ValueError):
            raise JsonBlobCorruptionError("context manifest is invalid") from None


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

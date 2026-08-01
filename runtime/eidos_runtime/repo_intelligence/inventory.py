from __future__ import annotations

from enum import StrEnum
import hashlib
import json
import os
from pathlib import Path
import stat
import threading
import time
from typing import Final

from charset_normalizer import from_bytes
from pydantic import Field, model_validator

from eidos_runtime.models import EidosFrozenStrictModel, JsonSafeInt
from eidos_runtime.sandbox.sensitive import SensitiveScanner
from eidos_runtime.workspace.discovery_policy import is_discovery_path_allowed
from eidos_runtime.workspace.discovery_scope import (
    DiscoveryScopeError,
    WorkspaceDiscoveryScope,
)


DEFAULT_MAX_ENTRIES: Final = 20_000
DEFAULT_MAX_SCAN_SECONDS: Final = 5.0
MAX_HASH_BYTES: Final = 8 * 1024 * 1024
MAX_ENCODING_SAMPLE_BYTES: Final = 256 * 1024


class InventoryError(RuntimeError):
    pass


class InventoryCanceled(InventoryError):
    pass


class FileType(StrEnum):
    REGULAR = "regular"
    BINARY = "binary"


class VerificationState(StrEnum):
    VERIFIED = "verified"
    METADATA_ONLY = "metadata_only"


class GitStatusClassification(StrEnum):
    UNKNOWN = "unknown"
    CLEAN = "clean"
    MODIFIED = "modified"
    UNTRACKED = "untracked"
    IGNORED = "ignored"


class FileRecord(EidosFrozenStrictModel):
    path: str = Field(min_length=1)
    file_type: FileType
    language: str | None = None
    size_bytes: JsonSafeInt
    mtime_ns: int
    ctime_ns: int | None = None
    device: int | None = None
    inode: int | None = None
    content_hash: str | None = None
    encoding: str
    generated: bool
    vendor: bool
    ignored: bool
    git_status: GitStatusClassification
    generation: int = Field(ge=0)
    verification_state: VerificationState


class DirectoryRecord(EidosFrozenStrictModel):
    path: str
    device: int | None = None
    inode: int | None = None
    ignored: bool
    generation: int = Field(ge=0)


class InventoryDiagnostic(EidosFrozenStrictModel):
    code: str = Field(min_length=1)
    path: str
    message: str
    recoverable: bool


class RepositoryInventory(EidosFrozenStrictModel):
    schema_version: int = 1
    repository_id: str = Field(min_length=1)
    root: str = Field(min_length=1)
    generation: int = Field(ge=0)
    complete: bool
    files: tuple[FileRecord, ...]
    directories: tuple[DirectoryRecord, ...]
    diagnostics: tuple[InventoryDiagnostic, ...] = ()
    created_at_ms: JsonSafeInt
    snapshot_id: str = Field(min_length=1)
    snapshot_hash: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def verify_snapshot_hash(self) -> RepositoryInventory:
        payload = self.model_dump(
            mode="json",
            exclude={"snapshot_id", "snapshot_hash", "created_at_ms"},
        )
        digest = _canonical_hash(payload)
        if digest != self.snapshot_hash or self.snapshot_id != f"inventory_{digest}":
            raise ValueError("repository inventory snapshot hash mismatch")
        return self


class RepositoryInventoryBuilder:
    def __init__(
        self,
        root: Path,
        *,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        max_scan_seconds: float = DEFAULT_MAX_SCAN_SECONDS,
        max_hash_bytes: int = MAX_HASH_BYTES,
        sensitive_scanner: SensitiveScanner | None = None,
    ) -> None:
        if max_entries < 1 or max_scan_seconds <= 0 or max_hash_bytes < 1:
            raise ValueError("inventory limits are invalid")
        self.root = root.resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("inventory root must be a directory")
        self.max_entries = max_entries
        self.max_scan_seconds = max_scan_seconds
        self.max_hash_bytes = max_hash_bytes
        self.sensitive_scanner = sensitive_scanner
        self._generation = 0
        self._last_snapshot: RepositoryInventory | None = None

    @property
    def last_complete(self) -> RepositoryInventory | None:
        return self._last_snapshot

    def build(
        self,
        *,
        cancel: threading.Event | None = None,
    ) -> RepositoryInventory:
        cancel = cancel or threading.Event()
        deadline = time.monotonic() + self.max_scan_seconds
        repository_id = _repository_id(self.root)
        files: list[FileRecord] = []
        directories: list[DirectoryRecord] = []
        diagnostics: list[InventoryDiagnostic] = []
        entries_seen = 0
        complete = True
        try:
            root_fd = os.open(self.root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        except OSError as error:
            raise InventoryError("inventory_root_unavailable") from error
        try:
            try:
                scope = WorkspaceDiscoveryScope.load(root_fd)
            except DiscoveryScopeError as error:
                raise InventoryError(error.code) from error
            pending: list[tuple[Path, str, bool]] = [(self.root, "", False)]
            while pending:
                if cancel.is_set():
                    raise InventoryCanceled("inventory_canceled")
                if time.monotonic() >= deadline:
                    complete = False
                    diagnostics.append(InventoryDiagnostic(
                        code="INVENTORY_DEADLINE",
                        path="",
                        message="inventory scan deadline reached",
                        recoverable=True,
                    ))
                    break
                directory, relative_parent, inherited_ignored = pending.pop()
                directory_metadata = directory.stat()
                directories.append(DirectoryRecord(
                    path=relative_parent,
                    device=directory_metadata.st_dev,
                    inode=directory_metadata.st_ino,
                    ignored=inherited_ignored,
                    generation=self._generation + 1,
                ))
                try:
                    children = sorted(
                        os.scandir(directory),
                        key=lambda entry: os.fsencode(entry.name),
                    )
                except OSError as error:
                    complete = False
                    diagnostics.append(InventoryDiagnostic(
                        code="DIRECTORY_READ_FAILED",
                        path=relative_parent,
                        message=type(error).__name__,
                        recoverable=True,
                    ))
                    continue
                for child in children:
                    if cancel.is_set():
                        raise InventoryCanceled("inventory_canceled")
                    if time.monotonic() >= deadline:
                        complete = False
                        diagnostics.append(InventoryDiagnostic(
                            code="INVENTORY_DEADLINE",
                            path=relative_parent,
                            message="inventory scan deadline reached",
                            recoverable=True,
                        ))
                        break
                    relative = (
                        f"{relative_parent}/{child.name}"
                        if relative_parent
                        else child.name
                    )
                    if not is_discovery_path_allowed(relative):
                        continue
                    try:
                        ignored = inherited_ignored or scope.is_ignored(
                            relative,
                            is_directory=child.is_dir(follow_symlinks=False),
                        )
                        metadata = child.stat(follow_symlinks=False)
                    except (OSError, DiscoveryScopeError) as error:
                        complete = False
                        diagnostics.append(InventoryDiagnostic(
                            code="ENTRY_VERIFY_FAILED",
                            path=relative,
                            message=type(error).__name__,
                            recoverable=True,
                        ))
                        continue
                    entries_seen += 1
                    if entries_seen > self.max_entries:
                        complete = False
                        diagnostics.append(InventoryDiagnostic(
                            code="INVENTORY_ENTRY_LIMIT",
                            path=relative,
                            message="inventory entry limit reached",
                            recoverable=True,
                        ))
                        break
                    if stat.S_ISDIR(metadata.st_mode):
                        if not ignored:
                            pending.append((Path(child.path), relative, False))
                        continue
                    if not stat.S_ISREG(metadata.st_mode):
                        diagnostics.append(InventoryDiagnostic(
                            code="SPECIAL_FILE_EXCLUDED",
                            path=relative,
                            message="special files are excluded",
                            recoverable=True,
                        ))
                        continue
                    if ignored:
                        continue
                    record = self._file_record(
                        child, relative, metadata, generation=self._generation + 1
                    )
                    files.append(record)
                if entries_seen > self.max_entries or not complete:
                    break
        finally:
            os.close(root_fd)
        if cancel.is_set():
            raise InventoryCanceled("inventory_canceled")
        if complete:
            self._generation += 1
            generation = self._generation
            files = [record.model_copy(update={"generation": generation}) for record in files]
            directories = [record.model_copy(update={"generation": generation}) for record in directories]
        else:
            generation = self._generation
        files.sort(key=lambda record: os.fsencode(record.path))
        directories.sort(key=lambda record: os.fsencode(record.path))
        payload = {
            "schema_version": 1,
            "repository_id": repository_id,
            "root": str(self.root),
            "generation": generation,
            "complete": complete,
            "files": [record.model_dump(mode="json") for record in files],
            "directories": [record.model_dump(mode="json") for record in directories],
            "diagnostics": [diagnostic.model_dump(mode="json") for diagnostic in diagnostics],
        }
        digest = _canonical_hash(payload)
        snapshot = RepositoryInventory(
            schema_version=1,
            repository_id=repository_id,
            root=str(self.root),
            generation=generation,
            complete=complete,
            files=tuple(files),
            directories=tuple(directories),
            diagnostics=tuple(diagnostics),
            created_at_ms=int(time.time() * 1000),
            snapshot_id=f"inventory_{digest}",
            snapshot_hash=digest,
        )
        if complete:
            self._last_snapshot = snapshot
        return snapshot

    def _file_record(
        self,
        entry: os.DirEntry[str],
        relative: str,
        metadata: os.stat_result,
        *,
        generation: int,
    ) -> FileRecord:
        content_hash: str | None = None
        encoding = "binary"
        file_type = FileType.BINARY
        verification_state = VerificationState.METADATA_ONLY
        if metadata.st_size <= self.max_hash_bytes:
            try:
                content = Path(entry.path).read_bytes()
                before = entry.stat(follow_symlinks=False)
                after = entry.stat(follow_symlinks=False)
                if (
                    before.st_dev,
                    before.st_ino,
                    before.st_size,
                    before.st_mtime_ns,
                ) != (
                    after.st_dev,
                    after.st_ino,
                    after.st_size,
                    after.st_mtime_ns,
                ) or len(content) != metadata.st_size:
                    raise OSError("file changed during inventory")
                content_hash = hashlib.sha256(content).hexdigest()
                if b"\x00" not in content:
                    match = from_bytes(content[:MAX_ENCODING_SAMPLE_BYTES]).best()
                    encoding = match.encoding if match is not None and match.encoding else "utf-8"
                    file_type = FileType.REGULAR
                verification_state = VerificationState.VERIFIED
            except (OSError, UnicodeError):
                verification_state = VerificationState.METADATA_ONLY
        return FileRecord(
            path=relative,
            file_type=file_type,
            language=_language_for(relative),
            size_bytes=metadata.st_size,
            mtime_ns=metadata.st_mtime_ns,
            ctime_ns=getattr(metadata, "st_ctime_ns", None),
            device=metadata.st_dev,
            inode=metadata.st_ino or None,
            content_hash=content_hash,
            encoding=encoding,
            generated=_is_generated(relative),
            vendor=_is_vendor(relative),
            ignored=False,
            git_status=GitStatusClassification.UNKNOWN,
            generation=generation,
            verification_state=verification_state,
        )


def _repository_id(root: Path) -> str:
    return hashlib.sha256(str(root).encode("utf-8")).hexdigest()


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")).hexdigest()


def _language_for(path: str) -> str | None:
    suffix = Path(path).suffix.lower()
    return {
        ".py": "python",
        ".ts": "typescript",
        ".tsx": "tsx",
        ".js": "javascript",
        ".jsx": "javascript",
        ".go": "go",
        ".rs": "rust",
        ".java": "java",
        ".sh": "bash",
        ".md": "markdown",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
    }.get(suffix)


def _is_generated(path: str) -> bool:
    lowered = path.lower()
    return any(marker in lowered for marker in (
        "/generated/", ".generated.", ".gen.", "/dist/", "/build/"
    ))


def _is_vendor(path: str) -> bool:
    parts = set(Path(path).parts)
    return bool(parts & {"vendor", "third_party", "node_modules"})


__all__ = [
    "DirectoryRecord",
    "FileRecord",
    "GitStatusClassification",
    "InventoryCanceled",
    "InventoryDiagnostic",
    "InventoryError",
    "RepositoryInventory",
    "RepositoryInventoryBuilder",
]

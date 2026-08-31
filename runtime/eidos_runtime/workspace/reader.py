from __future__ import annotations

from dataclasses import dataclass
import heapq
import os
from pathlib import Path
import stat
import threading
import time
from typing import Iterator, Literal

from eidos_runtime.db.database import WorkspaceIdentity
from eidos_runtime.workspace.discovery_policy import (
    HARD_DISCOVERY_DIRECTORIES,
    is_sensitive_directory,
    is_sensitive_name,
)
from eidos_runtime.workspace.discovery_scope import WorkspaceDiscoveryScope


DEFAULT_DIRECTORY_LIMIT = 500
MAX_DIRECTORY_LIMIT = 2_000
MAX_PREVIEW_BYTES = 512 * 1024
DIRECTORY_READ_TIMEOUT_SECONDS = 5.0


class WorkspacePathError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class WorkspaceDirectoryEntry:
    name: str
    relative_path: str
    kind: Literal["file", "directory"]
    size_bytes: int | None = None
    ignored: bool = False


@dataclass(frozen=True)
class WorkspaceDirectoryListing:
    path: str
    entries: tuple[WorkspaceDirectoryEntry, ...]
    truncated: bool


@dataclass(frozen=True)
class WorkspaceFilePreview:
    path: str
    kind: Literal["text", "markdown", "code", "unavailable"]
    size_bytes: int
    truncated: bool
    content: str | None = None
    language: str | None = None
    reason: Literal["binary", "unsupported"] | None = None


_MARKDOWN_SUFFIXES = frozenset({".md", ".markdown", ".mdx"})
_UNSUPPORTED_SUFFIXES = frozenset({
    ".7z", ".a", ".archive", ".db", ".dmg", ".doc", ".docx", ".gz",
    ".jar", ".pdf", ".rar", ".sqlite", ".sqlite3", ".tar", ".tgz",
    ".xls", ".xlsx", ".zip",
})
_CODE_LANGUAGES = {
    ".bash": "bash", ".c": "c", ".cc": "cpp", ".cpp": "cpp",
    ".css": "css", ".go": "go", ".h": "c", ".hpp": "cpp",
    ".html": "html", ".java": "java", ".js": "javascript",
    ".json": "json", ".jsx": "jsx", ".mjs": "javascript",
    ".py": "python", ".rb": "ruby", ".rs": "rust", ".sh": "bash",
    ".sql": "sql", ".swift": "swift", ".toml": "toml",
    ".ts": "typescript", ".tsx": "tsx", ".xml": "xml",
    ".yaml": "yaml", ".yml": "yaml", ".zsh": "zsh",
}


def capture_workspace_identity(path: Path | str) -> WorkspaceIdentity:
    path = Path(path)
    try:
        if path.is_symlink() or not path.is_dir():
            raise WorkspacePathError("workspace_unavailable")
        resolved = path.resolve(strict=True)
        metadata = resolved.stat()
    except OSError:
        raise WorkspacePathError("workspace_unavailable") from None
    return WorkspaceIdentity(
        path=resolved,
        device=metadata.st_dev,
        inode=metadata.st_ino,
        owner=metadata.st_uid,
    )


class WorkspaceReader:
    """One fd-relative, fail-closed Workspace read boundary."""

    def __init__(self, workspace: Path | WorkspaceIdentity) -> None:
        identity = (
            workspace if isinstance(workspace, WorkspaceIdentity)
            else capture_workspace_identity(workspace)
        )
        flags = os.O_RDONLY | os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            root_fd = os.open(identity.path, flags)
        except OSError:
            raise WorkspacePathError("workspace_unavailable") from None
        metadata = os.fstat(root_fd)
        if (metadata.st_dev, metadata.st_ino, metadata.st_uid) != (
            identity.device, identity.inode, identity.owner,
        ):
            os.close(root_fd)
            raise WorkspacePathError("workspace_identity_changed")
        self.workspace = identity
        self.root_fd = root_fd

    def __enter__(self) -> WorkspaceReader:
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()

    def close(self) -> None:
        if self.root_fd >= 0:
            os.close(self.root_fd)
            self.root_fd = -1

    def list_directory(
        self,
        path: str,
        *,
        limit: int = DEFAULT_DIRECTORY_LIMIT,
        cancel: threading.Event | None = None,
        include_ignored_directories: bool = False,
    ) -> WorkspaceDirectoryListing:
        if limit < 1 or limit > MAX_DIRECTORY_LIMIT:
            raise WorkspacePathError("invalid_directory_limit")
        self._verify_root()
        cancel = cancel or threading.Event()
        scope = WorkspaceDiscoveryScope.load(self.root_fd)
        directory_fd, normalized = self._open_directory_path(path)
        deadline = time.monotonic() + DIRECTORY_READ_TIMEOUT_SECONDS
        try:
            entries = heapq.nsmallest(
                limit + 1,
                self._iter_entries(
                    directory_fd,
                    normalized,
                    scope,
                    cancel,
                    deadline,
                    include_ignored_directories,
                ),
                key=lambda item: item.name.encode("utf-8"),
            )
        finally:
            os.close(directory_fd)
        return WorkspaceDirectoryListing(
            path=normalized,
            entries=tuple(entries[:limit]),
            truncated=len(entries) > limit,
        )

    def read_file_bytes(
        self,
        path: str,
        *,
        limit: int,
        allow_truncation: bool = False,
        cancel: threading.Event | None = None,
    ) -> tuple[bytes, os.stat_result, str, bool]:
        self._verify_root()
        cancel = cancel or threading.Event()
        parts = validate_workspace_relative_path(path)
        parent_fd = self._open_parent(parts)
        try:
            descriptor = self._open_file(parent_fd, parts[-1])
        finally:
            os.close(parent_fd)
        try:
            before = os.fstat(descriptor)
            if not stat.S_ISREG(before.st_mode):
                raise WorkspacePathError("unsupported_file_type")
            if before.st_nlink != 1:
                raise WorkspacePathError("unsupported_file_hardlink")
            if before.st_size > limit and not allow_truncation:
                raise WorkspacePathError("file_too_large")
            remaining = min(before.st_size, limit)
            chunks: list[bytes] = []
            while remaining > 0:
                if cancel.is_set():
                    raise WorkspacePathError("canceled")
                chunk = os.read(descriptor, min(64 * 1024, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            content = b"".join(chunks)
            after = os.fstat(descriptor)
            if (
                before.st_dev, before.st_ino, before.st_size,
                before.st_mtime_ns, before.st_nlink,
            ) != (
                after.st_dev, after.st_ino, after.st_size,
                after.st_mtime_ns, after.st_nlink,
            ):
                raise WorkspacePathError("workspace_changed")
            if before.st_size <= limit and len(content) != before.st_size:
                raise WorkspacePathError("workspace_changed")
            return content, after, "/".join(parts), before.st_size > limit
        finally:
            os.close(descriptor)

    def read_preview(self, path: str) -> WorkspaceFilePreview:
        content_bytes, metadata, normalized, truncated = self.read_file_bytes(
            path,
            limit=MAX_PREVIEW_BYTES,
            allow_truncation=True,
        )
        suffix = Path(normalized).suffix.lower()
        if suffix in _UNSUPPORTED_SUFFIXES:
            return WorkspaceFilePreview(
                path=normalized, kind="unavailable", size_bytes=metadata.st_size,
                truncated=truncated, reason="unsupported",
            )
        try:
            content = content_bytes.decode("utf-8-sig", errors="strict")
        except UnicodeDecodeError:
            if truncated:
                content = content_bytes.decode("utf-8-sig", errors="ignore")
            else:
                return WorkspaceFilePreview(
                    path=normalized, kind="unavailable", size_bytes=metadata.st_size,
                    truncated=False, reason="binary",
                )
        if any(
            character not in {"\n", "\r", "\t"}
            and (ord(character) < 0x20 or 0x7F <= ord(character) <= 0x9F)
            for character in content
        ):
            return WorkspaceFilePreview(
                path=normalized, kind="unavailable", size_bytes=metadata.st_size,
                truncated=truncated, reason="binary",
            )
        if suffix in _MARKDOWN_SUFFIXES:
            kind: Literal["text", "markdown", "code"] = "markdown"
            language = None
        elif suffix in _CODE_LANGUAGES:
            kind = "code"
            language = _CODE_LANGUAGES[suffix]
        else:
            kind = "text"
            language = None
        return WorkspaceFilePreview(
            path=normalized, kind=kind, size_bytes=metadata.st_size,
            truncated=truncated, content=content, language=language,
        )

    def _iter_entries(
        self,
        directory_fd: int,
        directory: str,
        scope: WorkspaceDiscoveryScope,
        cancel: threading.Event,
        deadline: float,
        include_ignored_directories: bool,
    ) -> Iterator[WorkspaceDirectoryEntry]:
        try:
            with os.scandir(directory_fd) as entries:
                for entry in entries:
                    if cancel.is_set():
                        raise WorkspacePathError("canceled")
                    if time.monotonic() > deadline:
                        raise WorkspacePathError("workspace_read_timeout")
                    name = entry.name
                    if name in HARD_DISCOVERY_DIRECTORIES or is_sensitive_name(name):
                        continue
                    try:
                        metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                    except OSError:
                        continue
                    relative = name if directory == "." else f"{directory}/{name}"
                    if stat.S_ISLNK(metadata.st_mode):
                        continue
                    if stat.S_ISDIR(metadata.st_mode):
                        if is_sensitive_directory(name):
                            continue
                        ignored = scope.is_ignored(relative, is_directory=True)
                        if not ignored or include_ignored_directories:
                            yield WorkspaceDirectoryEntry(
                                name, relative, "directory", ignored=ignored
                            )
                    elif stat.S_ISREG(metadata.st_mode) and not scope.is_ignored(
                        relative, is_directory=False
                    ):
                        yield WorkspaceDirectoryEntry(
                            name, relative, "file", metadata.st_size
                        )
        except OSError:
            raise WorkspacePathError("workspace_unavailable") from None

    def _verify_root(self) -> None:
        try:
            metadata = os.fstat(self.root_fd)
        except OSError:
            raise WorkspacePathError("workspace_unavailable") from None
        if (metadata.st_dev, metadata.st_ino, metadata.st_uid) != (
            self.workspace.device, self.workspace.inode, self.workspace.owner,
        ):
            raise WorkspacePathError("workspace_identity_changed")

    def _open_directory_path(self, path: str) -> tuple[int, str]:
        descriptor = os.dup(self.root_fd)
        if path == ".":
            return descriptor, "."
        parts = validate_workspace_relative_path(path)
        try:
            for part in parts:
                next_fd = self._open_directory(descriptor, part)
                os.close(descriptor)
                descriptor = next_fd
        except Exception:
            os.close(descriptor)
            raise
        return descriptor, "/".join(parts)

    def _open_parent(self, parts: tuple[str, ...]) -> int:
        descriptor = os.dup(self.root_fd)
        try:
            for part in parts[:-1]:
                next_fd = self._open_directory(
                    descriptor,
                    part,
                    missing_code="file_unavailable",
                )
                os.close(descriptor)
                descriptor = next_fd
        except Exception:
            os.close(descriptor)
            raise
        return descriptor

    @staticmethod
    def _open_directory(
        parent_fd: int,
        name: str,
        *,
        missing_code: str = "workspace_boundary_violation",
    ) -> int:
        flags = os.O_RDONLY | os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            return os.open(name, flags, dir_fd=parent_fd)
        except FileNotFoundError:
            raise WorkspacePathError(missing_code) from None
        except OSError:
            raise WorkspacePathError("workspace_boundary_violation") from None

    @staticmethod
    def _open_file(parent_fd: int, name: str) -> int:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            return os.open(name, flags, dir_fd=parent_fd)
        except FileNotFoundError:
            raise WorkspacePathError("file_unavailable") from None
        except OSError:
            raise WorkspacePathError("workspace_boundary_violation") from None


def validate_workspace_relative_path(value: str) -> tuple[str, ...]:
    if (
        not value or len(value) > 4096
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise WorkspacePathError("workspace_boundary_violation")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise WorkspacePathError("workspace_boundary_violation")
    parts = tuple(part for part in path.parts if part not in {"", "."})
    if not parts or any(
        is_sensitive_name(part) or is_sensitive_directory(part) for part in parts
    ):
        raise WorkspacePathError(
            "sensitive_path" if parts else "workspace_boundary_violation"
        )
    return parts


def is_workspace_discoverable_path(value: str) -> bool:
    parts = Path(value).parts
    return bool(parts) and not any(
        part in HARD_DISCOVERY_DIRECTORIES
        or is_sensitive_name(part)
        or is_sensitive_directory(part)
        for part in parts
    )


__all__ = [
    "DEFAULT_DIRECTORY_LIMIT", "MAX_DIRECTORY_LIMIT", "WorkspaceDirectoryEntry",
    "WorkspaceDirectoryListing", "WorkspaceFilePreview", "WorkspacePathError",
    "WorkspaceReader", "capture_workspace_identity", "validate_workspace_relative_path",
    "is_workspace_discoverable_path",
]

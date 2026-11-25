from __future__ import annotations

from eidos_runtime.infrastructure import local_files

from collections import deque
from dataclasses import dataclass
import heapq
import hashlib
import os
from pathlib import Path
import re
import stat
import threading
import time
from typing import Iterator, Literal

from rapidfuzz import fuzz

from eidos_runtime.db.database import WorkspaceIdentity
from eidos_runtime.workspace.discovery_policy import (
    HARD_DISCOVERY_DIRECTORIES,
    is_readable_metadata_path,
)
from eidos_runtime.workspace.discovery_scope import WorkspaceDiscoveryScope


DEFAULT_DIRECTORY_LIMIT = 500
MAX_DIRECTORY_LIMIT = 2_000
MAX_FIND_FILES_LIMIT = 500
MAX_SEARCH_DEPTH = 16
MAX_SEARCH_VISITED = 12_000
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
class WorkspaceFileSearchResult:
    entries: tuple[WorkspaceDirectoryEntry, ...]
    truncated: bool


@dataclass(frozen=True)
class WorkspaceFilePreview:
    path: str
    kind: Literal["text", "markdown", "code", "image", "pdf", "html", "unavailable"]
    size_bytes: int
    truncated: bool
    content: str | None = None
    language: str | None = None
    version: str | None = None
    mime_type: str | None = None
    reason: Literal["binary", "unsupported"] | None = None


_MARKDOWN_SUFFIXES = frozenset({".md", ".markdown", ".mdx"})
_UNSUPPORTED_SUFFIXES = frozenset({
    ".7z", ".a", ".archive", ".db", ".dmg", ".doc", ".docx", ".gz",
    ".jar", ".pdf", ".rar", ".sqlite", ".sqlite3", ".tar", ".tgz",
    ".xls", ".xlsx", ".xlsm", ".ppt", ".pptx", ".odp", ".ods", ".odt", ".zip",
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


_ASSET_MIMES = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif", ".svg": "image/svg+xml",
    ".pdf": "application/pdf", ".html": "text/html", ".htm": "text/html",
    ".css": "text/css", ".js": "text/javascript", ".mjs": "text/javascript",
    ".json": "application/json", ".woff": "font/woff", ".woff2": "font/woff2",
}


_ASCII_DIGITS_RE = re.compile(r"[0-9]+")


def _split_name_for_sort(
    name: str,
    kind: Literal["file", "directory"],
) -> tuple[str, str]:
    if kind == "directory":
        return name, ""
    dot = name.rfind(".")
    if dot <= 0 or not name[:dot].strip("."):
        return name, ""
    return name[:dot], name[dot + 1:]


def _natural_sort_tokens(value: str) -> tuple[tuple[int, int], ...]:
    folded = value.casefold()
    tokens: list[tuple[int, int]] = []
    position = 0
    for match in _ASCII_DIGITS_RE.finditer(folded):
        start, end = match.span()
        if start > position:
            for char in folded[position:start]:
                code = ord(char)
                tokens.append((0 if code < 0x30 else 2, code))
        tokens.append((1, int(match.group(0))))
        position = end
    if position < len(folded):
        for char in folded[position:]:
            code = ord(char)
            tokens.append((0 if code < 0x30 else 2, code))
    return tuple(tokens)


def _directory_entry_sort_key(
    entry: WorkspaceDirectoryEntry,
) -> tuple[int, tuple[tuple[int, int], ...], tuple[tuple[int, int], ...], str]:
    kind_rank = 0 if entry.kind == "directory" else 1
    stem, extension = _split_name_for_sort(entry.name, entry.kind)
    return (
        kind_rank,
        _natural_sort_tokens(stem),
        _natural_sort_tokens(extension),
        entry.name,
    )


def preview_mime(suffix: str) -> str | None:
    return _ASSET_MIMES.get(suffix.lower())


def file_version(metadata: os.stat_result) -> str:
    return hashlib.sha256(str((metadata.st_dev, metadata.st_ino, metadata.st_size,
                              metadata.st_mtime_ns, metadata.st_ctime_ns)).encode()).hexdigest()


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


def capture_directory_identity(path: Path | str) -> WorkspaceIdentity:
    """Capture a saved absolute directory path without following any symlink."""

    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise WorkspacePathError("workspace_unavailable")
    descriptor = WorkspaceReader._open_absolute_directory(path)
    try:
        metadata = os.fstat(descriptor)
    except OSError:
        raise WorkspacePathError("workspace_unavailable") from None
    finally:
        os.close(descriptor)
    return WorkspaceIdentity(
        path=path,
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
        try:
            root_fd = self._open_absolute_directory(identity.path)
        except WorkspacePathError:
            raise WorkspacePathError("workspace_unavailable") from None
        metadata = os.fstat(root_fd)
        if (metadata.st_dev, metadata.st_ino, metadata.st_uid) != (
            identity.device, identity.inode, identity.owner,
        ):
            os.close(root_fd)
            raise WorkspacePathError("workspace_identity_changed")
        self.workspace = identity
        self.root_fd = root_fd

    def capture_directory_identity(self, relative_path: str) -> WorkspaceIdentity:
        """Capture a directory below this root without following path symlinks."""

        self._verify_root()
        descriptor, normalized = self._open_directory_path(relative_path)
        try:
            metadata = os.fstat(descriptor)
            self._verify_root()
            path = (
                self.workspace.path
                if normalized == "."
                else self.workspace.path.joinpath(*normalized.split("/"))
            )
            return WorkspaceIdentity(
                path=path,
                device=metadata.st_dev,
                inode=metadata.st_ino,
                owner=metadata.st_uid,
            )
        except OSError:
            raise WorkspacePathError("workspace_unavailable") from None
        finally:
            os.close(descriptor)

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
        allow_metadata = is_readable_metadata_path(normalized)
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
                    allow_metadata,
                ),
                key=_directory_entry_sort_key,
            )
        finally:
            os.close(directory_fd)
        return WorkspaceDirectoryListing(
            path=normalized,
            entries=tuple(entries[:limit]),
            truncated=len(entries) > limit,
        )

    def find_files(
        self,
        query: str = "",
        *,
        limit: int = 100,
        cancel: threading.Event | None = None,
    ) -> WorkspaceFileSearchResult:
        if limit < 1 or limit > MAX_FIND_FILES_LIMIT:
            raise WorkspacePathError("invalid_directory_limit")
        self._verify_root()
        cancel = cancel or threading.Event()
        scope = WorkspaceDiscoveryScope.load(self.root_fd)
        deadline = time.monotonic() + DIRECTORY_READ_TIMEOUT_SECONDS
        needle = query.strip().lower()
        collected: list[tuple[int, WorkspaceDirectoryEntry]] = []
        visited = 0
        truncated_scan = False
        queue: deque[tuple[int, str, int]] = deque([(os.dup(self.root_fd), ".", 0)])
        try:
            while queue and not truncated_scan:
                directory_fd, directory, depth = queue.popleft()
                allow_metadata = is_readable_metadata_path(directory)
                try:
                    with local_files.scan_directory(directory_fd) as entries:
                        for entry in entries:
                            if cancel.is_set():
                                raise WorkspacePathError("canceled")
                            if time.monotonic() > deadline:
                                raise WorkspacePathError("workspace_read_timeout")
                            name = entry.name
                            if name in HARD_DISCOVERY_DIRECTORIES and not allow_metadata:
                                continue
                            try:
                                metadata = local_files.lstat_at(name, directory_fd)
                            except OSError:
                                continue
                            if local_files.is_link(metadata):
                                continue
                            relative = name if directory == "." else f"{directory}/{name}"
                            if stat.S_ISDIR(metadata.st_mode):
                                if scope.is_ignored(relative, is_directory=True):
                                    continue
                                collected.append((depth, WorkspaceDirectoryEntry(name, relative, "directory")))
                                visited += 1
                                if depth + 1 < MAX_SEARCH_DEPTH:
                                    try:
                                        child_fd = local_files.open_directory(name, dir_fd=directory_fd)
                                    except OSError:
                                        child_fd = -1
                                    if child_fd >= 0:
                                        queue.append((child_fd, relative, depth + 1))
                            elif stat.S_ISREG(metadata.st_mode):
                                if scope.is_ignored(relative, is_directory=False):
                                    continue
                                collected.append((depth, WorkspaceDirectoryEntry(name, relative, "file", metadata.st_size)))
                                visited += 1
                            if visited >= MAX_SEARCH_VISITED:
                                truncated_scan = True
                                break
                except OSError:
                    if directory == ".":
                        raise WorkspacePathError("workspace_unavailable") from None
                    continue
                finally:
                    os.close(directory_fd)
        finally:
            while queue:
                leftover_fd, _, _ = queue.popleft()
                try:
                    os.close(leftover_fd)
                except OSError:
                    pass

        if not needle:
            collected.sort(
                key=lambda item: (item[0], 0 if item[1].kind == "directory" else 1, item[1].relative_path.lower())
            )
            selected = tuple(entry for _, entry in collected[:limit])
            return WorkspaceFileSearchResult(
                entries=selected,
                truncated=truncated_scan or len(collected) > limit,
            )

        scored: list[tuple[tuple[int, float, int, str], WorkspaceDirectoryEntry]] = []
        for depth, item in collected:
            key = _score_find_entry(needle, item, depth)
            if key is not None:
                scored.append((key, item))
        scored.sort(key=lambda pair: pair[0])
        selected = tuple(entry for _, entry in scored[:limit])
        return WorkspaceFileSearchResult(
            entries=selected,
            truncated=truncated_scan or len(scored) > limit,
        )

    def read_file_bytes(
        self,
        path: str,
        *,
        limit: int,
        allow_truncation: bool = False,
        offset: int = 0,
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
            if offset < 0 or offset > before.st_size:
                raise WorkspacePathError("invalid_offset")
            os.lseek(descriptor, offset, os.SEEK_SET)
            remaining = min(before.st_size - offset, limit)
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
                before.st_mtime_ns, before.st_ctime_ns, before.st_nlink,
            ) != (
                after.st_dev, after.st_ino, after.st_size,
                after.st_mtime_ns, after.st_ctime_ns, after.st_nlink,
            ):
                raise WorkspacePathError("workspace_changed")
            if len(content) != min(before.st_size - offset, limit):
                raise WorkspacePathError("workspace_changed")
            return content, after, "/".join(parts), before.st_size > limit
        finally:
            os.close(descriptor)

    def stat_file(
        self, path: str, *, cancel: threading.Event | None = None,
    ) -> os.stat_result:
        """Inspect an existing regular file without reading or exposing its contents."""
        if cancel is not None and cancel.is_set():
            raise WorkspacePathError("canceled")
        _, metadata, _, _ = self.read_file_bytes(path, limit=0, allow_truncation=True, cancel=cancel)
        if metadata.st_uid != self.workspace.owner:
            raise WorkspacePathError("workspace_boundary_violation")
        named_root = os.stat(self.workspace.path, follow_symlinks=False)
        if (named_root.st_dev, named_root.st_ino, named_root.st_uid) != (
            self.workspace.device, self.workspace.inode, self.workspace.owner,
        ):
            raise WorkspacePathError("workspace_identity_changed")
        return metadata

    def read_preview(self, path: str, *, limit: int = MAX_PREVIEW_BYTES) -> WorkspaceFilePreview:
        content_bytes, metadata, normalized, truncated = self.read_file_bytes(
            path,
            limit=limit,
            allow_truncation=True,
        )
        suffix = Path(normalized).suffix.lower()
        version = file_version(metadata)
        mime = preview_mime(suffix)
        if mime and (mime.startswith("image/") or mime == "application/pdf"):
            return WorkspaceFilePreview(
                path=normalized, kind="pdf" if mime == "application/pdf" else "image",
                size_bytes=metadata.st_size, truncated=False, version=version, mime_type=mime,
            )
        if suffix in _UNSUPPORTED_SUFFIXES:
            return WorkspaceFilePreview(
                path=normalized, kind="unavailable", size_bytes=metadata.st_size,
                truncated=truncated, reason="unsupported", version=version,
            )
        try:
            content = content_bytes.decode("utf-8-sig", errors="strict")
        except UnicodeDecodeError:
            if truncated:
                content = content_bytes.decode("utf-8-sig", errors="ignore")
            else:
                return WorkspaceFilePreview(
                    path=normalized, kind="unavailable", size_bytes=metadata.st_size,
                    truncated=False, reason="binary", version=version,
                )
        if any(
            character not in {"\n", "\r", "\t"}
            and (ord(character) < 0x20 or 0x7F <= ord(character) <= 0x9F)
            for character in content
        ):
            return WorkspaceFilePreview(
                path=normalized, kind="unavailable", size_bytes=metadata.st_size,
                truncated=truncated, reason="binary", version=version,
            )
        if suffix in _MARKDOWN_SUFFIXES:
            kind: Literal["text", "markdown", "code", "html"] = "markdown"
            language = None
        elif suffix in {".html", ".htm"}:
            kind = "html"
            language = "html"
        elif suffix in _CODE_LANGUAGES:
            kind = "code"
            language = _CODE_LANGUAGES[suffix]
        else:
            kind = "text"
            language = None
        return WorkspaceFilePreview(
            path=normalized, kind=kind, size_bytes=metadata.st_size,
            truncated=truncated, content=content, language=language, version=version, mime_type=mime,
        )

    def _iter_entries(
        self,
        directory_fd: int,
        directory: str,
        scope: WorkspaceDiscoveryScope,
        cancel: threading.Event,
        deadline: float,
        include_ignored_directories: bool,
        allow_metadata: bool,
    ) -> Iterator[WorkspaceDirectoryEntry]:
        try:
            with local_files.scan_directory(directory_fd) as entries:
                for entry in entries:
                    if cancel.is_set():
                        raise WorkspacePathError("canceled")
                    if time.monotonic() > deadline:
                        raise WorkspacePathError("workspace_read_timeout")
                    name = entry.name
                    if name in HARD_DISCOVERY_DIRECTORIES and not allow_metadata:
                        continue
                    try:
                        metadata = local_files.lstat_at(name, directory_fd)
                    except OSError:
                        continue
                    relative = name if directory == "." else f"{directory}/{name}"
                    if local_files.is_link(metadata):
                        continue
                    if stat.S_ISDIR(metadata.st_mode):
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
    def _open_absolute_directory(path: Path) -> int:
        if not path.is_absolute():
            raise WorkspacePathError("workspace_unavailable")
        try:
            descriptor = local_files.open_directory(path.anchor)
        except OSError:
            raise WorkspacePathError("workspace_unavailable") from None
        try:
            for part in path.parts[1:]:
                child = local_files.open_directory(part, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            return descriptor
        except OSError:
            os.close(descriptor)
            raise WorkspacePathError("workspace_unavailable") from None

    @staticmethod
    def _open_directory(
        parent_fd: int,
        name: str,
        *,
        missing_code: str = "workspace_boundary_violation",
    ) -> int:
        try:
            return local_files.open_directory(name, dir_fd=parent_fd)
        except FileNotFoundError:
            raise WorkspacePathError(missing_code) from None
        except OSError:
            raise WorkspacePathError("workspace_boundary_violation") from None

    @staticmethod
    def _open_file(parent_fd: int, name: str) -> int:
        try:
            return local_files.open_file(name, dir_fd=parent_fd)
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
    if path.anchor or ".." in path.parts or not local_files.valid_relative_parts(path.parts):
        raise WorkspacePathError("workspace_boundary_violation")
    parts = tuple(part for part in path.parts if part not in {"", "."})
    if not parts:
        raise WorkspacePathError("workspace_boundary_violation")
    return parts


def is_workspace_discoverable_path(value: str) -> bool:
    parts = Path(value).parts
    return bool(parts) and not any(
        part in HARD_DISCOVERY_DIRECTORIES
        for part in parts
    )


def _score_find_entry(
    needle: str,
    entry: WorkspaceDirectoryEntry,
    depth: int,
) -> tuple[int, float, int, str] | None:
    name_l = entry.name.lower()
    rel_l = entry.relative_path.lower()
    if name_l == needle or rel_l == needle:
        return (0, 0.0, depth, rel_l)
    if name_l.startswith(needle):
        return (1, float(len(name_l) - len(needle)), depth, rel_l)
    name_pos = name_l.find(needle)
    if name_pos >= 0:
        return (2, float(name_pos), depth, rel_l)
    rel_pos = rel_l.find(needle)
    if rel_pos >= 0:
        return (3, float(rel_pos), depth, rel_l)
    ratio = max(fuzz.WRatio(needle, name_l), fuzz.WRatio(needle, rel_l))
    if ratio >= 65.0:
        return (4, -float(ratio), depth, rel_l)
    return None


__all__ = [
    "DEFAULT_DIRECTORY_LIMIT", "MAX_DIRECTORY_LIMIT", "WorkspaceDirectoryEntry",
    "WorkspaceDirectoryListing", "WorkspaceFilePreview", "WorkspaceFileSearchResult",
    "WorkspacePathError", "WorkspaceReader", "capture_workspace_identity",
    "validate_workspace_relative_path", "capture_directory_identity",
    "is_workspace_discoverable_path",
]

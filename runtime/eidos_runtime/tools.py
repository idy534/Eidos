from __future__ import annotations

import hashlib
import heapq
import os
from pathlib import Path
import stat
import threading
import time
from typing import Callable, Iterator

from eidos_runtime.storage import WorkspaceIdentity


MAX_FILE_BYTES = 256 * 1024
MAX_LIST_DEPTH = 5
MAX_LIST_ENTRIES = 2_000
MAX_SEARCH_BYTES = 8 * 1024 * 1024
MAX_SEARCH_ENTRIES = 20_000
MAX_SEARCH_RESULTS = 100
TOOL_DEADLINE_SECONDS = 5.0
EXCLUDED_DIRECTORIES = {
    ".git",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
}
SENSITIVE_DIRECTORIES = {".aws", ".config", ".eidos", ".gnupg", ".kube", ".ssh"}
SENSITIVE_NAMES = {
    ".git-credentials",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "credentials",
    "credentials.json",
    "id_dsa",
    "id_ed25519",
    "id_rsa",
}
SENSITIVE_SUFFIXES = {".key", ".p12", ".pem", ".pfx"}
SENSITIVE_KEYWORDS = {"credential", "secret", "token"}


class ToolCancelled(RuntimeError):
    pass


class WorkspacePathError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ToolExecutor:
    def __init__(self, workspace: Path | WorkspaceIdentity) -> None:
        if isinstance(workspace, WorkspaceIdentity):
            identity = workspace
        else:
            path = workspace.resolve()
            metadata = path.stat()
            identity = WorkspaceIdentity(
                path=path,
                device=metadata.st_dev,
                inode=metadata.st_ino,
                owner=metadata.st_uid,
            )
        flags = os.O_RDONLY | os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            root_fd = os.open(identity.path, flags)
        except OSError:
            raise WorkspacePathError("workspace_unavailable") from None
        metadata = os.fstat(root_fd)
        if (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_uid,
        ) != (identity.device, identity.inode, identity.owner):
            os.close(root_fd)
            raise WorkspacePathError("workspace_identity_changed")

        self.workspace = identity
        self.root_fd = root_fd
        self._tools: dict[
            str, Callable[[dict[str, object], threading.Event], dict[str, object]]
        ] = {
            "list_files": self._list_files,
            "read_file": self._read_file,
            "search_text": self._search_text,
        }

    def __enter__(self) -> ToolExecutor:
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        self.close()

    def close(self) -> None:
        if self.root_fd >= 0:
            os.close(self.root_fd)
            self.root_fd = -1

    @property
    def tool_names(self) -> frozenset[str]:
        return frozenset(self._tools)

    def validate_arguments(self, tool_name: str, arguments: object) -> bool:
        if not isinstance(arguments, dict):
            return False
        if tool_name == "list_files":
            return not arguments
        if tool_name == "read_file":
            return set(arguments) == {"path"} and isinstance(arguments.get("path"), str)
        if tool_name == "search_text":
            query = arguments.get("query")
            return (
                set(arguments) == {"query"}
                and isinstance(query, str)
                and bool(query)
                and len(query.encode("utf-8")) <= 512
            )
        return False

    def execute(
        self,
        tool_name: str,
        arguments: dict[str, object],
        cancel: threading.Event,
    ) -> dict[str, object]:
        tool = self._tools.get(tool_name)
        if tool is None:
            return _error(tool_name, "tool_not_found", "Tool is not available")
        if not self.validate_arguments(tool_name, arguments):
            return _error(tool_name, "invalid_arguments", "Invalid arguments")
        try:
            self._verify_root()
            _check_cancel(cancel)
            return tool(arguments, cancel)
        except ToolCancelled:
            return _error(tool_name, "canceled", "Tool was canceled")
        except WorkspacePathError as error:
            return _error(tool_name, error.code, "Workspace path is unavailable")

    def _list_files(
        self, _arguments: dict[str, object], cancel: threading.Event
    ) -> dict[str, object]:
        paths: list[str] = []
        truncated = False
        deadline = time.monotonic() + TOOL_DEADLINE_SECONDS

        def visit(directory_fd: int, prefix: str, depth: int) -> None:
            nonlocal truncated
            if truncated:
                return
            names, directory_truncated = self._bounded_names(
                directory_fd,
                MAX_LIST_ENTRIES + 1,
                cancel,
                deadline,
            )
            truncated = truncated or directory_truncated
            for name in names:
                _check_budget(cancel, deadline)
                if len(paths) >= MAX_LIST_ENTRIES:
                    truncated = True
                    return
                if _is_sensitive_name(name):
                    continue
                try:
                    metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                except OSError:
                    continue
                relative = f"{prefix}{name}"
                if stat.S_ISLNK(metadata.st_mode):
                    continue
                if stat.S_ISDIR(metadata.st_mode):
                    if name in EXCLUDED_DIRECTORIES or _is_sensitive_directory(name):
                        continue
                    paths.append(f"{relative}/")
                    if depth < MAX_LIST_DEPTH:
                        child_fd = self._open_directory(directory_fd, name)
                        try:
                            visit(child_fd, f"{relative}/", depth + 1)
                        finally:
                            os.close(child_fd)
                elif stat.S_ISREG(metadata.st_mode):
                    paths.append(relative)

        root_fd = os.dup(self.root_fd)
        try:
            visit(root_fd, "", 1)
        finally:
            os.close(root_fd)
        return _success(
            "list_files",
            "Listed files",
            {"paths": paths, "truncated": truncated},
        )

    def _read_file(
        self, arguments: dict[str, object], cancel: threading.Event
    ) -> dict[str, object]:
        path_value = arguments["path"]
        assert isinstance(path_value, str)
        descriptor, normalized_path = self._open_file(path_value)
        try:
            content_bytes, metadata = _read_regular_file(descriptor, cancel)
        finally:
            os.close(descriptor)
        try:
            content = content_bytes.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return _error("read_file", "invalid_utf8", "File is not valid UTF-8")
        return _success(
            "read_file",
            "Read file",
            {
                "path": normalized_path,
                "content": content,
                "sizeBytes": metadata.st_size,
                "sha256": hashlib.sha256(content_bytes).hexdigest(),
            },
        )

    def _search_text(
        self, arguments: dict[str, object], cancel: threading.Event
    ) -> dict[str, object]:
        query = arguments["query"]
        assert isinstance(query, str)
        deadline = time.monotonic() + TOOL_DEADLINE_SECONDS
        scanned_bytes = 0
        scanned_entries = 0
        matches: list[dict[str, object]] = []
        truncated = False

        def visit(directory_fd: int, prefix: str) -> None:
            nonlocal scanned_bytes, scanned_entries, truncated
            if truncated:
                return
            names, names_truncated = self._bounded_names(
                directory_fd,
                MAX_SEARCH_ENTRIES + 1,
                cancel,
                deadline,
            )
            truncated = truncated or names_truncated
            for name in names:
                _check_budget(cancel, deadline)
                scanned_entries += 1
                if scanned_entries > MAX_SEARCH_ENTRIES:
                    truncated = True
                    return
                if _is_sensitive_name(name):
                    continue
                try:
                    metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                except OSError:
                    continue
                if stat.S_ISLNK(metadata.st_mode):
                    continue
                relative = f"{prefix}{name}"
                if stat.S_ISDIR(metadata.st_mode):
                    if name in EXCLUDED_DIRECTORIES or _is_sensitive_directory(name):
                        continue
                    child_fd = self._open_directory(directory_fd, name)
                    try:
                        visit(child_fd, f"{relative}/")
                    finally:
                        os.close(child_fd)
                    if truncated:
                        return
                    continue
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > MAX_FILE_BYTES:
                    continue
                if scanned_bytes + metadata.st_size > MAX_SEARCH_BYTES:
                    truncated = True
                    return
                try:
                    file_fd = self._open_file_at(directory_fd, name)
                    try:
                        content_bytes, stable_metadata = _read_regular_file(file_fd, cancel)
                    finally:
                        os.close(file_fd)
                except WorkspacePathError:
                    continue
                scanned_bytes += stable_metadata.st_size
                try:
                    content = content_bytes.decode("utf-8", errors="strict")
                except UnicodeDecodeError:
                    continue
                for line_number, line in enumerate(content.splitlines(), start=1):
                    column = line.find(query)
                    if column < 0:
                        continue
                    matches.append(
                        {
                            "path": relative,
                            "line": line_number,
                            "column": column + 1,
                            "preview": line[:300],
                        }
                    )
                    if len(matches) >= MAX_SEARCH_RESULTS:
                        truncated = True
                        return

        root_fd = os.dup(self.root_fd)
        try:
            visit(root_fd, "")
        finally:
            os.close(root_fd)
        return _success(
            "search_text",
            "Searched text",
            {
                "matches": matches,
                "scannedBytes": scanned_bytes,
                "truncated": truncated,
            },
        )

    def _verify_root(self) -> None:
        if self.root_fd < 0:
            raise WorkspacePathError("workspace_unavailable")
        metadata = os.fstat(self.root_fd)
        if (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_uid,
        ) != (self.workspace.device, self.workspace.inode, self.workspace.owner):
            raise WorkspacePathError("workspace_identity_changed")

    def _open_file(self, value: str) -> tuple[int, str]:
        parts = _validate_relative_path(value)
        directory_fd = os.dup(self.root_fd)
        try:
            for part in parts[:-1]:
                next_fd = self._open_directory(directory_fd, part)
                os.close(directory_fd)
                directory_fd = next_fd
            descriptor = self._open_file_at(directory_fd, parts[-1])
        finally:
            os.close(directory_fd)
        return descriptor, "/".join(parts)

    @staticmethod
    def _open_directory(parent_fd: int, name: str) -> int:
        flags = os.O_RDONLY | os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            return os.open(name, flags, dir_fd=parent_fd)
        except OSError:
            raise WorkspacePathError("workspace_boundary_violation") from None

    @staticmethod
    def _open_file_at(parent_fd: int, name: str) -> int:
        flags = os.O_RDONLY | os.O_NONBLOCK
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(name, flags, dir_fd=parent_fd)
        except OSError:
            raise WorkspacePathError("file_unavailable") from None
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            os.close(descriptor)
            raise WorkspacePathError("unsupported_file_type")
        return descriptor

    @staticmethod
    def _bounded_names(
        directory_fd: int,
        limit: int,
        cancel: threading.Event,
        deadline: float,
    ) -> tuple[list[str], bool]:
        def names() -> Iterator[str]:
            try:
                with os.scandir(directory_fd) as entries:
                    for entry in entries:
                        _check_budget(cancel, deadline)
                        yield entry.name
            except OSError:
                return

        selected = heapq.nsmallest(limit, names())
        return selected[: limit - 1], len(selected) == limit


def _read_regular_file(
    descriptor: int, cancel: threading.Event
) -> tuple[bytes, os.stat_result]:
    before = os.fstat(descriptor)
    if not stat.S_ISREG(before.st_mode):
        raise WorkspacePathError("unsupported_file_type")
    if before.st_size > MAX_FILE_BYTES:
        raise WorkspacePathError("file_too_large")
    chunks: list[bytes] = []
    remaining = MAX_FILE_BYTES + 1
    while remaining > 0:
        _check_cancel(cancel)
        chunk = os.read(descriptor, min(64 * 1024, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    content = b"".join(chunks)
    after = os.fstat(descriptor)
    if len(content) > MAX_FILE_BYTES:
        raise WorkspacePathError("file_too_large")
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
    ) or len(content) != before.st_size:
        raise WorkspacePathError("workspace_changed")
    return content, after


def _validate_relative_path(value: str) -> tuple[str, ...]:
    if not value or len(value) > 4096:
        raise WorkspacePathError("workspace_boundary_violation")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise WorkspacePathError("workspace_boundary_violation")
    parts = tuple(part for part in path.parts if part not in {"", "."})
    if not parts or any(_is_sensitive_name(part) or _is_sensitive_directory(part) for part in parts):
        code = "sensitive_path" if parts else "workspace_boundary_violation"
        raise WorkspacePathError(code)
    return parts


def _is_sensitive_directory(name: str) -> bool:
    return name.lower() in SENSITIVE_DIRECTORIES


def _is_sensitive_name(name: str) -> bool:
    lowered = name.lower()
    if lowered == ".env.example":
        return False
    return (
        lowered == ".env"
        or lowered.startswith(".env.")
        or lowered in SENSITIVE_NAMES
        or Path(lowered).suffix in SENSITIVE_SUFFIXES
        or any(keyword in lowered for keyword in SENSITIVE_KEYWORDS)
    )


def _check_cancel(cancel: threading.Event) -> None:
    if cancel.is_set():
        raise ToolCancelled


def _check_budget(cancel: threading.Event, deadline: float) -> None:
    _check_cancel(cancel)
    if time.monotonic() >= deadline:
        raise WorkspacePathError("tool_timeout")


def _success(
    tool_name: str, summary: str, data: dict[str, object]
) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "toolName": tool_name,
        "outcome": "success",
        "code": "ok",
        "summary": summary,
        "data": data,
        "sideEffectsMayExist": False,
    }


def _error(tool_name: str, code: str, summary: str) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "toolName": tool_name,
        "outcome": "error",
        "code": code,
        "summary": summary,
        "data": {},
        "sideEffectsMayExist": False,
    }

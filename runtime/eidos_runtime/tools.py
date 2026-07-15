from __future__ import annotations

import ctypes
from dataclasses import dataclass
import difflib
import errno
import fcntl
import hashlib
import heapq
import os
from pathlib import Path
import re
import stat
import sys
import threading
import time
from typing import Callable, Iterator
import uuid

from eidos_runtime.storage import WorkspaceIdentity
from eidos_runtime.seatbelt import secure_workspace_move


MAX_FILE_BYTES = 256 * 1024
MAX_LIST_DEPTH = 5
MAX_LIST_ENTRIES = 2_000
MAX_SEARCH_BYTES = 8 * 1024 * 1024
MAX_SEARCH_ENTRIES = 20_000
MAX_SEARCH_RESULTS = 100
MAX_FILE_CHANGE_BYTES = 256 * 1024
MAX_DIFF_BYTES = 512 * 1024
TOOL_DEADLINE_SECONDS = 5.0
SHELL_PREFLIGHT_DEADLINE_SECONDS = 10.0
MAX_SHELL_PREFLIGHT_ENTRIES = 250_000
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
SHELL_SOURCE_SUFFIXES = {
    ".c",
    ".cc",
    ".cpp",
    ".go",
    ".h",
    ".hpp",
    ".java",
    ".js",
    ".jsx",
    ".md",
    ".mjs",
    ".py",
    ".pyc",
    ".rs",
    ".ts",
    ".tsx",
}
MAX_PEM_SCAN_BYTES = 1024 * 1024
DARWIN_ACL_TYPE_EXTENDED = 0x00000100
DARWIN_REPLACE_SAFE_XATTRS = frozenset({b"com.apple.provenance"})


class ToolCancelled(RuntimeError):
    pass


class WorkspacePathError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class FileChange:
    path: str
    content: bytes
    base_sha256: str | None
    mode: int
    diff: str


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
        self._side_effecting_tools = frozenset({"write_file", "apply_patch"})
        self._shell_tools = frozenset({"run_shell"})

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
        return frozenset(self._tools) | self._side_effecting_tools | self._shell_tools

    def is_side_effecting(self, tool_name: str) -> bool:
        return tool_name in self._side_effecting_tools

    def is_shell(self, tool_name: str) -> bool:
        return tool_name in self._shell_tools

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
        if tool_name == "write_file":
            content = arguments.get("content")
            return (
                set(arguments) == {"path", "content"}
                and isinstance(arguments.get("path"), str)
                and isinstance(content, str)
                and len(content.encode("utf-8")) <= MAX_FILE_CHANGE_BYTES
            )
        if tool_name == "apply_patch":
            patch = arguments.get("patch")
            return (
                set(arguments) == {"path", "patch"}
                and isinstance(arguments.get("path"), str)
                and isinstance(patch, str)
                and len(patch.encode("utf-8")) <= MAX_DIFF_BYTES
            )
        if tool_name == "run_shell":
            command = arguments.get("command")
            cwd = arguments.get("cwd", ".")
            timeout = arguments.get("timeoutSeconds", 120)
            return (
                set(arguments) <= {"command", "cwd", "timeoutSeconds"}
                and "command" in arguments
                and isinstance(command, str)
                and bool(command)
                and len(command.encode("utf-8")) <= 16 * 1024
                and isinstance(cwd, str)
                and isinstance(timeout, int)
                and not isinstance(timeout, bool)
                and 1 <= timeout <= 600
            )
        return False

    def shell_cwd(self, value: str) -> WorkspaceIdentity:
        self._verify_root()
        if value == ".":
            return self.workspace
        parts = _validate_relative_path(value)
        descriptor = os.dup(self.root_fd)
        try:
            for part in parts:
                next_fd = self._open_directory(descriptor, part)
                os.close(descriptor)
                descriptor = next_fd
            path = Path(_fd_path(descriptor))
            if self.workspace.path not in path.parents and path != self.workspace.path:
                raise WorkspacePathError("workspace_boundary_violation")
            metadata = os.fstat(descriptor)
            return WorkspaceIdentity(
                path=path,
                device=metadata.st_dev,
                inode=metadata.st_ino,
                owner=metadata.st_uid,
            )
        finally:
            os.close(descriptor)

    def prepare_shell(
        self, value: str, cancel: threading.Event
    ) -> WorkspaceIdentity:
        self._verify_root()
        self._verify_shell_workspace(cancel)
        cwd = self.shell_cwd(value)
        self._verify_root()
        return cwd

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

    def prepare_file_change(
        self,
        tool_name: str,
        arguments: dict[str, object],
        cancel: threading.Event,
    ) -> FileChange | dict[str, object]:
        if tool_name not in self._side_effecting_tools:
            return _error(tool_name, "tool_not_found", "Tool is not available")
        if not self.validate_arguments(tool_name, arguments):
            return _error(tool_name, "invalid_arguments", "Invalid arguments")
        try:
            self._verify_root()
            _check_cancel(cancel)
            path_value = arguments["path"]
            assert isinstance(path_value, str)
            parts = _validate_relative_path(path_value)
            normalized_path = "/".join(parts)
            existing = self._read_existing_for_change(normalized_path, cancel)
            if tool_name == "write_file":
                content = arguments["content"]
                assert isinstance(content, str)
                candidate = content.encode("utf-8")
            else:
                if existing is None:
                    raise WorkspacePathError("file_unavailable")
                patch_value = arguments["patch"]
                assert isinstance(patch_value, str)
                candidate = _apply_unified_diff(
                    normalized_path,
                    existing[0].decode("utf-8", errors="strict"),
                    patch_value,
                ).encode("utf-8")
            if len(candidate) > MAX_FILE_CHANGE_BYTES:
                raise WorkspacePathError("file_too_large")
            base_content = existing[0] if existing is not None else b""
            base_text = base_content.decode("utf-8", errors="strict")
            candidate_text = candidate.decode("utf-8", errors="strict")
            if _has_unsupported_text_control(base_text) or _has_unsupported_text_control(
                candidate_text
            ):
                raise WorkspacePathError("unsupported_text_content")
            base_sha256 = (
                hashlib.sha256(base_content).hexdigest()
                if existing is not None
                else None
            )
            mode = existing[1] if existing is not None else 0o644
            diff = _build_approval_diff(
                base_text,
                candidate_text,
                (
                    f"a/{normalized_path}" if existing is not None else "/dev/null"
                ),
                f"b/{normalized_path}",
            )
            if existing is None and not diff:
                diff = f"--- /dev/null\n+++ b/{normalized_path}\n"
            if len(diff.encode("utf-8")) > MAX_DIFF_BYTES:
                raise WorkspacePathError("diff_too_large")
            return FileChange(
                path=normalized_path,
                content=candidate,
                base_sha256=base_sha256,
                mode=mode,
                diff=diff,
            )
        except UnicodeDecodeError:
            return _error(tool_name, "invalid_utf8", "File is not valid UTF-8")
        except ToolCancelled:
            return _error(tool_name, "canceled", "Tool was canceled")
        except WorkspacePathError as error:
            return _error(tool_name, error.code, "File change could not be prepared")

    def commit_file_change(
        self,
        tool_name: str,
        change: FileChange,
        cancel: threading.Event,
    ) -> dict[str, object]:
        temporary_name: str | None = None
        preserve_temporary = False
        parent_fd = -1
        try:
            self._verify_root()
            _check_cancel(cancel)
            parts = _validate_relative_path(change.path)
            parent_fd = self._open_parent(parts)
            self._verify_base_version(parent_fd, parts[-1], change.base_sha256, cancel)
            temporary_name = f".eidos-{uuid.uuid4().hex}.tmp"
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(temporary_name, flags, change.mode, dir_fd=self.root_fd)
            try:
                offset = 0
                while offset < len(change.content):
                    _check_cancel(cancel)
                    written = os.write(descriptor, change.content[offset:])
                    if written <= 0:
                        raise WorkspacePathError("file_write_failed")
                    offset += written
                os.fchmod(descriptor, change.mode)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            _check_cancel(cancel)
            self._verify_base_version(parent_fd, parts[-1], change.base_sha256, cancel)
            workspace_path = Path(_fd_path(self.root_fd))
            if workspace_path != self.workspace.path:
                raise WorkspacePathError("workspace_identity_changed")
            source_path = workspace_path / temporary_name
            target_path = workspace_path.joinpath(*parts)
            move_status = secure_workspace_move(
                workspace_path,
                source_path,
                target_path,
                change.base_sha256,
            )
            final_move_started = True
            if move_status == "uncertain":
                preserve_temporary = True
                return _commit_error(
                    tool_name,
                    "file_commit_uncertain",
                    "File change outcome could not be verified",
                    side_effects=True,
                )
            if move_status in {"conflict", "failed"}:
                return _commit_error(
                    tool_name,
                    (
                        "file_version_conflict"
                        if move_status == "conflict"
                        else "sandbox_unavailable"
                    ),
                    "Secure file commit did not change the target",
                    side_effects=False,
                )
            current_parent_fd = self._open_parent(parts)
            os.close(parent_fd)
            parent_fd = current_parent_fd
            try:
                committed_fd = self._open_file_at(parent_fd, parts[-1])
            except WorkspacePathError as error:
                if move_status in {"failed", "conflict"} and error.code == "file_unavailable":
                    return _commit_error(
                        tool_name,
                        (
                            "file_version_conflict"
                            if move_status == "conflict"
                            else "sandbox_unavailable"
                        ),
                        "Secure file commit did not change the target",
                        side_effects=False,
                    )
                raise
            try:
                committed, _metadata = _read_regular_file(
                    committed_fd, threading.Event()
                )
            finally:
                os.close(committed_fd)
            if committed != change.content:
                current_sha256 = hashlib.sha256(committed).hexdigest()
                unchanged = (
                    change.base_sha256 is not None
                    and current_sha256 == change.base_sha256
                )
                if move_status == "conflict":
                    return _commit_error(
                        tool_name,
                        "file_version_conflict",
                        "File changed after approval; the candidate was rolled back",
                        side_effects=False,
                    )
                return _commit_error(
                    tool_name,
                    (
                        "file_version_conflict"
                        if move_status == "conflict" and unchanged
                        else "file_write_failed"
                        if unchanged
                        else "file_write_verification_failed"
                    ),
                    (
                        "Secure file commit failed"
                        if unchanged
                        else "File change outcome is uncertain"
                    ),
                    side_effects=not unchanged,
                )
            temporary_name = None
            try:
                os.fsync(parent_fd)
                os.fsync(self.root_fd)
            except OSError:
                return _commit_error(
                    tool_name,
                    "file_commit_uncertain",
                    "File changed but durability could not be confirmed",
                    side_effects=True,
                    path=change.path,
                    sha256=hashlib.sha256(committed).hexdigest(),
                )
            return _success(
                tool_name,
                "File change committed",
                {
                    "path": change.path,
                    "sha256": hashlib.sha256(committed).hexdigest(),
                    "sizeBytes": len(committed),
                },
            )
        except ToolCancelled:
            return _error(tool_name, "canceled", "Tool was canceled")
        except WorkspacePathError as error:
            if locals().get("move_status") == "conflict":
                return _commit_error(
                    tool_name,
                    "file_version_conflict",
                    "File changed after approval; the candidate was rolled back",
                    side_effects=False,
                )
            if locals().get("final_move_started", False):
                return _commit_error(
                    tool_name,
                    "file_commit_uncertain",
                    "File change outcome could not be verified",
                    side_effects=True,
                )
            return _error(tool_name, error.code, "File change was not committed")
        except OSError:
            if locals().get("move_status") == "conflict":
                return _commit_error(
                    tool_name,
                    "file_version_conflict",
                    "File changed after approval; the candidate was rolled back",
                    side_effects=False,
                )
            if locals().get("final_move_started", False):
                return _commit_error(
                    tool_name,
                    "file_commit_uncertain",
                    "File change outcome could not be verified",
                    side_effects=True,
                )
            return _error(tool_name, "file_write_failed", "File change was not committed")
        finally:
            if temporary_name is not None and not preserve_temporary:
                try:
                    os.unlink(temporary_name, dir_fd=self.root_fd)
                except OSError:
                    pass
            if parent_fd >= 0:
                os.close(parent_fd)

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
        if Path(_fd_path(self.root_fd)) != self.workspace.path:
            raise WorkspacePathError("workspace_identity_changed")

    def _verify_shell_workspace(self, cancel: threading.Event) -> None:
        deadline = time.monotonic() + SHELL_PREFLIGHT_DEADLINE_SECONDS
        entry_count = 0

        def visit(directory_fd: int, depth: int, in_git: bool = False) -> None:
            nonlocal entry_count
            try:
                with os.scandir(directory_fd) as entries:
                    for entry in entries:
                        _check_budget(cancel, deadline)
                        entry_count += 1
                        if entry_count > MAX_SHELL_PREFLIGHT_ENTRIES:
                            raise WorkspacePathError("workspace_scan_limit")
                        name = entry.name
                        try:
                            metadata = os.stat(
                                name, dir_fd=directory_fd, follow_symlinks=False
                            )
                        except OSError:
                            raise WorkspacePathError("workspace_changed") from None
                        if depth == 0 and name == ".git":
                            if stat.S_ISLNK(metadata.st_mode):
                                raise WorkspacePathError("unsupported_workspace_entry")
                            if stat.S_ISREG(metadata.st_mode):
                                if metadata.st_nlink != 1:
                                    raise WorkspacePathError(
                                        "unsupported_workspace_hardlink"
                                    )
                                continue
                            if not stat.S_ISDIR(metadata.st_mode):
                                raise WorkspacePathError("unsupported_workspace_entry")
                            child_fd = self._open_directory(directory_fd, name)
                            try:
                                visit(child_fd, depth + 1, True)
                            finally:
                                os.close(child_fd)
                            continue
                        root_env = depth == 0 and name.lower() == ".env"
                        if not in_git and not root_env and (
                            _is_shell_sensitive_name(name)
                            or _is_sensitive_directory(name)
                        ):
                            raise WorkspacePathError("sensitive_workspace_content")
                        if stat.S_ISLNK(metadata.st_mode):
                            continue
                        if stat.S_ISDIR(metadata.st_mode):
                            child_fd = self._open_directory(directory_fd, name)
                            try:
                                visit(child_fd, depth + 1, in_git)
                            finally:
                                os.close(child_fd)
                            continue
                        if not stat.S_ISREG(metadata.st_mode):
                            raise WorkspacePathError("unsupported_workspace_entry")
                        if metadata.st_nlink != 1:
                            raise WorkspacePathError("unsupported_workspace_hardlink")
                        if not in_git and _contains_sensitive_pem(
                            directory_fd, name, metadata
                        ):
                            raise WorkspacePathError("sensitive_workspace_content")
            except WorkspacePathError:
                raise
            except OSError:
                raise WorkspacePathError("workspace_changed") from None

        descriptor = os.dup(self.root_fd)
        try:
            visit(descriptor, 0)
        finally:
            os.close(descriptor)

    def _read_existing_for_change(
        self, path: str, cancel: threading.Event
    ) -> tuple[bytes, int] | None:
        try:
            descriptor, _normalized = self._open_file(path)
        except WorkspacePathError as error:
            if error.code == "file_unavailable":
                return None
            raise
        try:
            metadata = os.fstat(descriptor)
            mode = stat.S_IMODE(metadata.st_mode)
            if (
                mode & 0o7000
                or metadata.st_nlink != 1
                or metadata.st_uid != os.getuid()
                or getattr(metadata, "st_flags", 0) != 0
                or _has_unsupported_file_metadata(descriptor)
            ):
                raise WorkspacePathError("unsupported_file_metadata")
            content, _stable = _read_regular_file(descriptor, cancel)
            return content, mode
        finally:
            os.close(descriptor)

    def _open_parent(self, parts: tuple[str, ...]) -> int:
        directory_fd = os.dup(self.root_fd)
        try:
            for part in parts[:-1]:
                next_fd = self._open_directory(directory_fd, part)
                os.close(directory_fd)
                directory_fd = next_fd
            return directory_fd
        except Exception:
            os.close(directory_fd)
            raise

    def _verify_base_version(
        self,
        parent_fd: int,
        name: str,
        expected_sha256: str | None,
        cancel: threading.Event,
    ) -> None:
        try:
            descriptor = self._open_file_at(parent_fd, name)
        except WorkspacePathError as error:
            if expected_sha256 is None and error.code == "file_unavailable":
                return
            raise WorkspacePathError("file_version_conflict") from None
        try:
            content, _metadata = _read_regular_file(descriptor, cancel)
        finally:
            os.close(descriptor)
        if expected_sha256 is None or hashlib.sha256(content).hexdigest() != expected_sha256:
            raise WorkspacePathError("file_version_conflict")

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
        except FileNotFoundError:
            raise WorkspacePathError("file_unavailable") from None
        except OSError as error:
            code = (
                "unsupported_file_type"
                if error.errno in {errno.ELOOP, errno.EISDIR}
                else "file_unavailable"
            )
            raise WorkspacePathError(code) from None
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
    if before.st_nlink != 1:
        raise WorkspacePathError("unsupported_file_hardlink")
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
        before.st_nlink,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
        after.st_nlink,
    ) or len(content) != before.st_size:
        raise WorkspacePathError("workspace_changed")
    return content, after


def _validate_relative_path(value: str) -> tuple[str, ...]:
    if (
        not value
        or len(value) > 4096
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise WorkspacePathError("workspace_boundary_violation")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts:
        raise WorkspacePathError("workspace_boundary_violation")
    parts = tuple(part for part in path.parts if part not in {"", "."})
    if not parts or any(_is_sensitive_name(part) or _is_sensitive_directory(part) for part in parts):
        code = "sensitive_path" if parts else "workspace_boundary_violation"
        raise WorkspacePathError(code)
    return parts


def _has_unsupported_file_metadata(descriptor: int) -> bool:
    if sys.platform == "darwin":
        return _darwin_has_unsupported_file_metadata(descriptor)

    listxattr = getattr(os, "listxattr", None)
    if listxattr is None:
        return True
    try:
        return bool(listxattr(descriptor))
    except (OSError, TypeError):
        return True


def _darwin_has_unsupported_file_metadata(descriptor: int) -> bool:
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        flistxattr = libc.flistxattr
        acl_get_fd_np = libc.acl_get_fd_np
        acl_free = libc.acl_free
    except (AttributeError, OSError):
        return True

    flistxattr.argtypes = [
        ctypes.c_int,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_int,
    ]
    flistxattr.restype = ctypes.c_ssize_t
    acl_get_fd_np.argtypes = [ctypes.c_int, ctypes.c_int]
    acl_get_fd_np.restype = ctypes.c_void_p
    acl_free.argtypes = [ctypes.c_void_p]
    acl_free.restype = ctypes.c_int

    ctypes.set_errno(0)
    required = flistxattr(descriptor, None, 0, 0)
    if required < 0:
        return True
    if required:
        names_buffer = ctypes.create_string_buffer(required)
        ctypes.set_errno(0)
        actual = flistxattr(descriptor, names_buffer, required, 0)
        if actual < 0 or actual != required:
            return True
        names = frozenset(
            name for name in names_buffer.raw[:actual].split(b"\0") if name
        )
        if names - DARWIN_REPLACE_SAFE_XATTRS:
            return True

    ctypes.set_errno(0)
    acl = acl_get_fd_np(descriptor, DARWIN_ACL_TYPE_EXTENDED)
    if acl:
        acl_free(acl)
        return True
    return ctypes.get_errno() != errno.ENOENT


HUNK_HEADER = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: .*)?\n?$"
)


def _apply_unified_diff(path: str, original: str, patch: str) -> str:
    lines = patch.splitlines(keepends=True)
    if len(lines) < 3:
        raise WorkspacePathError("invalid_patch")
    old_header = lines[0].rstrip("\r\n")
    new_header = lines[1].rstrip("\r\n")
    if old_header not in {f"--- {path}", f"--- a/{path}"} or new_header not in {
        f"+++ {path}",
        f"+++ b/{path}",
    }:
        raise WorkspacePathError("invalid_patch")
    original_lines = original.splitlines(keepends=True)
    output: list[str] = []
    source_cursor = 0
    index = 2
    saw_hunk = False
    while index < len(lines):
        match = HUNK_HEADER.match(lines[index])
        if match is None:
            raise WorkspacePathError("invalid_patch")
        saw_hunk = True
        old_start = int(match.group(1))
        old_count = int(match.group(2) or "1")
        new_count = int(match.group(4) or "1")
        target_cursor = 0 if old_start == 0 else old_start - 1
        if target_cursor < source_cursor or target_cursor > len(original_lines):
            raise WorkspacePathError("patch_context_mismatch")
        output.extend(original_lines[source_cursor:target_cursor])
        source_cursor = target_cursor
        index += 1
        consumed_old = 0
        produced_new = 0
        while index < len(lines) and not lines[index].startswith("@@ "):
            line = lines[index]
            if not line or line[0] not in {" ", "+", "-"}:
                raise WorkspacePathError("invalid_patch")
            content = line[1:]
            if line[0] in {" ", "-"}:
                if source_cursor >= len(original_lines) or original_lines[source_cursor] != content:
                    raise WorkspacePathError("patch_context_mismatch")
                source_cursor += 1
                consumed_old += 1
            if line[0] in {" ", "+"}:
                output.append(content)
                produced_new += 1
            index += 1
        if consumed_old != old_count or produced_new != new_count:
            raise WorkspacePathError("invalid_patch")
    if not saw_hunk:
        raise WorkspacePathError("invalid_patch")
    output.extend(original_lines[source_cursor:])
    return "".join(output)


def _build_approval_diff(
    original: str,
    candidate: str,
    fromfile: str,
    tofile: str,
) -> str:
    records = list(
        difflib.unified_diff(
            original.splitlines(),
            candidate.splitlines(),
            fromfile=fromfile,
            tofile=tofile,
            lineterm="",
        )
    )
    before_newline = original.endswith("\n")
    after_newline = candidate.endswith("\n")
    if not records:
        if original == candidate:
            return ""
        records = [f"--- {fromfile}", f"+++ {tofile}"]
    diff = "\n".join(records) + "\n"
    if before_newline != after_newline:
        before = "present" if before_newline else "absent"
        after = "present" if after_newline else "absent"
        diff += f"\\ Eidos EOF newline: before={before}, after={after}\n"
    before_endings = _line_ending_summary(original)
    after_endings = _line_ending_summary(candidate)
    if before_endings != after_endings:
        diff += (
            "\\ Eidos line endings: "
            f"before={before_endings}, after={after_endings}\n"
        )
    return diff


def _line_ending_summary(value: str) -> str:
    crlf = value.count("\r\n")
    without_crlf = value.replace("\r\n", "")
    lf = without_crlf.count("\n")
    cr = without_crlf.count("\r")
    endings = [
        f"{label}:{count}"
        for label, count in (("CRLF", crlf), ("LF", lf), ("CR", cr))
        if count
    ]
    return "+".join(endings) if endings else "none"


def _has_unsupported_text_control(value: str) -> bool:
    return any(
        character not in {"\n", "\r", "\t"}
        and (ord(character) < 0x20 or 0x7F <= ord(character) <= 0x9F)
        for character in value
    )


def _is_sensitive_directory(name: str) -> bool:
    return name.lower() in SENSITIVE_DIRECTORIES


def _is_sensitive_name(name: str) -> bool:
    lowered = name.lower()
    if lowered == ".env.example":
        return False
    return (
        lowered.startswith(".eidos-")
        or
        lowered == ".env"
        or lowered.startswith(".env.")
        or lowered in SENSITIVE_NAMES
        or Path(lowered).suffix in SENSITIVE_SUFFIXES
        or any(keyword in lowered for keyword in SENSITIVE_KEYWORDS)
    )


def _is_shell_sensitive_name(name: str) -> bool:
    lowered = name.lower()
    if lowered == ".env.example":
        return False
    suffix = Path(lowered).suffix
    keyword_name = re.search(
        r"(?:^|[._-])(credentials?|secrets?|tokens?)(?:[._-]|$)", lowered
    )
    return (
        lowered.startswith(".eidos-")
        or lowered == ".env"
        or lowered.startswith(".env.")
        or lowered in SENSITIVE_NAMES
        or suffix in SENSITIVE_SUFFIXES - {".pem"}
        or keyword_name is not None and suffix not in SHELL_SOURCE_SUFFIXES
    )


def _contains_sensitive_pem(
    directory_fd: int, name: str, metadata: os.stat_result
) -> bool:
    if Path(name.lower()).suffix != ".pem":
        return False
    if metadata.st_size > MAX_PEM_SCAN_BYTES:
        return True
    flags = os.O_RDONLY | os.O_NONBLOCK
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(name, flags, dir_fd=directory_fd)
        try:
            content = bytearray()
            while len(content) <= MAX_PEM_SCAN_BYTES:
                chunk = os.read(
                    descriptor,
                    min(64 * 1024, MAX_PEM_SCAN_BYTES + 1 - len(content)),
                )
                if not chunk:
                    break
                content.extend(chunk)
            after = os.fstat(descriptor)
        finally:
            os.close(descriptor)
    except OSError:
        raise WorkspacePathError("workspace_changed") from None
    if (
        (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns)
        != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
        or len(content) != metadata.st_size
    ):
        raise WorkspacePathError("workspace_changed")
    return (
        b"PRIVATE KEY-----" in content
        or b"-----BEGIN CERTIFICATE-----" not in content
        or b"-----END CERTIFICATE-----" not in content
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


def _commit_error(
    tool_name: str,
    code: str,
    summary: str,
    *,
    side_effects: bool,
    path: str | None = None,
    sha256: str | None = None,
) -> dict[str, object]:
    data: dict[str, object] = {}
    if path is not None:
        data["path"] = path
    if sha256 is not None:
        data["sha256"] = sha256
    return {
        "schemaVersion": 1,
        "toolName": tool_name,
        "outcome": "error",
        "code": code,
        "summary": summary,
        "data": data,
        "sideEffectsMayExist": side_effects,
    }


def _fd_path(descriptor: int) -> str:
    try:
        raw = fcntl.fcntl(descriptor, fcntl.F_GETPATH, bytes(1024))
        value = raw.split(bytes(1), 1)[0].decode("utf-8", errors="strict")
    except (AttributeError, OSError, UnicodeDecodeError):
        raise WorkspacePathError("workspace_identity_unavailable") from None
    if not value:
        raise WorkspacePathError("workspace_identity_unavailable")
    return value

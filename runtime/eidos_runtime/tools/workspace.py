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
from typing import Iterator
import uuid
import json

from eidos_runtime.protocol.schemas import ToolResultDto
from eidos_runtime.sandbox.sensitive import SensitiveScanError, default_scanner
from eidos_runtime.db.storage import WorkspaceIdentity
from eidos_runtime.sandbox.seatbelt import secure_workspace_move
from eidos_runtime.sandbox.workspace_index import (
    WorkspaceIndex,
    WorkspaceIndexIncomplete,
)
from eidos_runtime.workspace.discovery_scope import (
    DiscoveryScopeError,
    WorkspaceDiscoveryScope,
)
from eidos_runtime.workspace.discovery_policy import (
    HARD_DISCOVERY_DIRECTORIES,
    SENSITIVE_NAMES,
    SENSITIVE_SUFFIXES,
    is_sensitive_directory as _is_sensitive_directory,
    is_sensitive_name as _is_sensitive_name,
)
from eidos_runtime.workspace.search_driver import (
    MAX_RG_PREVIEW_CHARACTERS,
    RipgrepSearchDriver,
    SearchDriverError,
    WorkspaceSearchDriver,
    WorkspaceSearchRequest,
)
from eidos_runtime.workspace.unified_diff import (
    PatchApplyError,
    apply_strict_single_file_patch,
)
from eidos_runtime.tools.registry import (
    ToolProvenance,
    ToolRegistry,
    ToolRegistryEntry,
    ToolSpec,
)
from eidos_runtime.tools.contracts import (
    ApplyPatchInput,
    DeleteFileInput,
    LIST_FILES_MAX_DEPTH,
    LIST_FILES_MAX_ENTRIES,
    ListFilesInput,
    ListFilesResultData,
    ReadFileInput,
    ReadFileRangeInput,
    ReadFileRangeResultData,
    ReadFileResultData,
    RunShellInput,
    RunShellResultData,
    SEARCH_TEXT_MAX_RESULTS,
    SearchTextInput,
    SearchTextResultData,
    WorkspaceResultData,
    WriteFileInput,
    result_model,
)


MAX_FILE_BYTES = 256 * 1024
MAX_READ_FILE_BYTES = 2 * 1024 * 1024
MAX_RANGE_LINES = 2_000
MAX_LIST_DEPTH = LIST_FILES_MAX_DEPTH
MAX_LIST_ENTRIES = LIST_FILES_MAX_ENTRIES
MAX_SEARCH_RESULTS = SEARCH_TEXT_MAX_RESULTS
MAX_FILE_CHANGE_BYTES = 256 * 1024
MAX_DIFF_BYTES = 512 * 1024
TOOL_DEADLINE_SECONDS = 5.0
SHELL_PREFLIGHT_DEADLINE_SECONDS = 10.0
MAX_SHELL_PREFLIGHT_ENTRIES = 250_000
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
    delete: bool = False


_BUILTIN_CONTRACTS = (
    ("list_files", "List bounded regular files under a workspace-relative path (default '.'); supports maxDepth and maxEntries. Results are workspace-relative and may be truncated.", "none", False, 5, "parallel", ListFilesInput, ListFilesResultData, "list_files"),
    ("read_file", "Read one bounded UTF-8 file. Large files return head/tail content; use read_file_range to continue.", "none", False, 5, "parallel", ReadFileInput, ReadFileResultData, "read_file"),
    ("read_file_range", "Read an inclusive bounded line range from one UTF-8 file. Continue from nextLine when present.", "none", False, 5, "parallel", ReadFileRangeInput, ReadFileRangeResultData, "read_file_range"),
    ("search_text", "Search a workspace-relative path (default '.') for a single-line query; supports maxResults, regex, and includeGlobs. Results are workspace-relative, bounded, and may be truncated.", "none", False, 5, "parallel", SearchTextInput, SearchTextResultData, "search_text"),
    ("write_file", "Create or replace one UTF-8 file after approval and verify the final hash. Modifies the workspace.", "workspace", True, 5, "single", WriteFileInput, WorkspaceResultData, "file_change"),
    ("apply_patch", "Apply one strict unified diff to one previously read file after approval. Modifies the workspace.", "workspace", True, 5, "single", ApplyPatchInput, WorkspaceResultData, "file_change"),
    ("delete_file", "Delete one previously read regular UTF-8 file after approval. Modifies the workspace.", "workspace", True, 5, "single", DeleteFileInput, WorkspaceResultData, "file_change"),
    ("run_shell", "Run one shell command after approval. The default sandbox has no network access; for user-requested network access, set sandboxPermissions=with_additional_permissions, additionalPermissions.network.enabled=true, and provide justification instead of refusing without a tool call. Output and workspace changes are bounded and verified.", "shell", True, 600, "single", RunShellInput, RunShellResultData, "run_shell"),
)
TOOL_SPECS = tuple(ToolSpec.model_validate({
    "name": name,
    "description": description,
    "sideEffect": side_effect,
    "approvalRequired": approval,
    "timeoutSeconds": timeout,
    "batchPolicy": batch,
    "visibility": "direct",
    "inputSchema": input_model.model_json_schema(by_alias=True),
    "resultSchema": result_model(data_model).model_json_schema(by_alias=True),
    "modelProjectionPolicy": projection,
    "contractVersion": 1,
}) for (
    name, description, side_effect, approval, timeout, batch,
    input_model, data_model, projection,
) in _BUILTIN_CONTRACTS)
if len({spec.name for spec in TOOL_SPECS}) != len(TOOL_SPECS):
    raise RuntimeError("duplicate tool spec")


def model_tool_definitions() -> list[dict[str, object]]:
    return [{"type": "function", "function": {
        "name": spec.name, "description": spec.description,
        "parameters": spec.input_schema,
    }} for spec in TOOL_SPECS]


class _BuiltinAdapter:
    def __init__(
        self,
        executor: ToolExecutor,
        spec: ToolSpec,
        operation: str,
    ) -> None:
        self.executor = executor
        self.spec = spec
        self.operation = operation
        self.workspace = executor.workspace

    def execute(
        self, arguments: dict[str, object], cancel: threading.Event
    ) -> dict[str, object]:
        return self.executor.execute_read(
            self.spec.name, self.operation, arguments, cancel
        )

    def prepare_file_change(
        self, arguments: dict[str, object], cancel: threading.Event
    ) -> FileChange | dict[str, object]:
        return self.executor._prepare_file_change(
            self.spec.name, self.operation, arguments, cancel
        )

    def commit_file_change(
        self, change: FileChange, cancel: threading.Event
    ) -> dict[str, object]:
        return self.executor.commit_file_change(self.spec.name, change, cancel)

    def prepare_shell(self, cwd: str, cancel: threading.Event) -> WorkspaceIdentity:
        return self.executor.prepare_shell(cwd, cancel)


def builtin_tool_registry(executor: ToolExecutor) -> ToolRegistry:
    operations = (
        "list", "read", "range", "search", "write", "patch", "delete", "shell"
    )
    entries: list[ToolRegistryEntry] = []
    for spec, operation, contract in zip(
        TOOL_SPECS, operations, _BUILTIN_CONTRACTS, strict=True
    ):
        encoded = json.dumps(
            spec.model_dump(mode="json", by_alias=True),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        entries.append(ToolRegistryEntry(
            spec,
            ToolProvenance.model_validate({
                "kind": "builtin",
                "sourceId": "eidos",
                "sourceVersion": "1",
                "contentHash": hashlib.sha256(encoded).hexdigest(),
            }),
            _BuiltinAdapter(executor, spec, operation),
            contract[6],
            contract[7],
        ))
    return ToolRegistry(tuple(entries))


def canonical_tool_result(
    tool_name: str, result: dict[str, object]
) -> dict[str, object]:
    normalized = {
        "schemaVersion": 1,
        "toolContractVersion": 1,
        "toolName": tool_name,
        "outcome": result.get("outcome", "error"),
        "code": result.get("code", "unknown_error"),
        "summary": result.get("summary", "Tool request failed"),
        "data": result.get("data", {}),
        "sideEffectsMayExist": bool(result.get("sideEffectsMayExist", False)),
        "reconciliationRequired": bool(result.get(
            "reconciliationRequired",
            result.get("sideEffectsMayExist", False)
            if result.get("outcome", "error") != "success"
            else False,
        )),
    }
    data_model = next(
        (contract[7] for contract in _BUILTIN_CONTRACTS if contract[0] == tool_name),
        None,
    )
    validated = (
        result_model(data_model).model_validate_json(json.dumps(
            normalized,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        ))
        if data_model is not None
        else ToolResultDto.model_validate(normalized)
    ).model_dump(mode="json", by_alias=True, exclude_unset=True)
    encoded = json.dumps(
        validated, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    if len(encoded) > 512 * 1024:
        return _error(tool_name, "tool_result_too_large", "Tool result exceeded the safe size limit")
    return validated


class ToolExecutor:
    def __init__(
        self,
        workspace: Path | WorkspaceIdentity,
        search_driver: WorkspaceSearchDriver | None = None,
    ) -> None:
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
        self.workspace_index = WorkspaceIndex(identity)
        self.search_driver = search_driver or RipgrepSearchDriver()
        self.registry = builtin_tool_registry(self)

    def __enter__(self) -> ToolExecutor:
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        self.close()

    def close(self) -> None:
        if self.root_fd >= 0:
            os.close(self.root_fd)
            self.root_fd = -1

    def execute(
        self,
        tool_name: str,
        arguments: dict[str, object],
        cancel: threading.Event,
    ) -> dict[str, object]:
        entry = self.registry.get(tool_name)
        validation = entry.validate_arguments(arguments) if entry else None
        if (
            entry is None
            or validation is None
            or not validation.valid
            or validation.normalized_arguments is None
        ):
            return _error(tool_name, "invalid_arguments", "Invalid arguments")
        return entry.adapter.execute(validation.normalized_arguments, cancel)

    def prepare_file_change(
        self,
        tool_name: str,
        arguments: dict[str, object],
        cancel: threading.Event,
    ) -> FileChange | dict[str, object]:
        entry = self.registry.get(tool_name)
        validation = entry.validate_arguments(arguments) if entry else None
        prepare = getattr(entry.adapter, "prepare_file_change", None) if entry else None
        if (
            validation is None
            or not validation.valid
            or validation.normalized_arguments is None
            or prepare is None
        ):
            return _error(tool_name, "invalid_arguments", "Invalid arguments")
        return prepare(validation.normalized_arguments, cancel)

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
                git_dir=self.workspace.git_dir,
                git_common_dir=self.workspace.git_common_dir,
            )
        finally:
            os.close(descriptor)

    def prepare_shell(
        self, value: str, cancel: threading.Event
    ) -> WorkspaceIdentity:
        """Validate only the process launch boundary.

        Workspace-wide indexing is reconciliation work, not a prerequisite for
        starting a bounded shell process. The caller still re-checks the root
        identity and cwd after approval, while ``refresh_workspace_index`` is
        kept for post-execution mutation and safety reconciliation.
        """
        self._verify_root()
        cwd = self.shell_cwd(value)
        self._verify_root()
        return cwd

    def refresh_workspace_index(
        self, cancel: threading.Event
    ):
        self._verify_root()
        self._verify_shell_workspace(cancel)
        return self.workspace_index.manifest()

    def execute_read(
        self,
        tool_name: str,
        operation: str,
        arguments: dict[str, object],
        cancel: threading.Event,
    ) -> dict[str, object]:
        tool = {
            "list": self._list_files,
            "read": self._read_file,
            "range": self._read_file_range,
            "search": self._search_text,
        }.get(operation)
        if tool is None:
            return _error(tool_name, "tool_not_found", "Tool is not available")
        try:
            self._verify_root()
            _check_cancel(cancel)
            scanned_arguments = default_scanner().scan_json(arguments)
            assert isinstance(scanned_arguments, dict)
            result = tool(scanned_arguments, cancel)
            scanned = default_scanner().scan_json(result)
            assert isinstance(scanned, dict)
            if scanned != result:
                return _error(
                    tool_name, "sensitive_content_rejected", "Sensitive content was withheld"
                )
            return canonical_tool_result(tool_name, scanned)
        except SensitiveScanError:
            return _error(tool_name, "sensitive_content_rejected", "Sensitive content was withheld")
        except ToolCancelled:
            return _error(tool_name, "canceled", "Tool was canceled")
        except DiscoveryScopeError as error:
            return _error(tool_name, error.code, "Workspace discovery ignore file is unavailable")
        except SearchDriverError as error:
            code = "canceled" if error.code == "search_backend_canceled" else error.code
            return _error(tool_name, code, "Workspace search backend is unavailable")
        except WorkspacePathError as error:
            return _error(tool_name, error.code, "Workspace path is unavailable")

    def _prepare_file_change(
        self,
        tool_name: str,
        operation: str,
        arguments: dict[str, object],
        cancel: threading.Event,
    ) -> FileChange | dict[str, object]:
        if operation not in {"write", "patch", "delete"}:
            return _error(tool_name, "tool_not_found", "Tool is not available")
        try:
            self._verify_root()
            _check_cancel(cancel)
            scanned_arguments = default_scanner().scan_json(arguments)
            if scanned_arguments != arguments:
                raise SensitiveScanError("sensitive tool arguments")
            path_value = arguments["path"]
            assert isinstance(path_value, str)
            parts = _validate_relative_path(path_value)
            normalized_path = "/".join(parts)
            existing = self._read_existing_for_change(normalized_path, cancel)
            deleting = operation == "delete"
            if deleting:
                if existing is None:
                    raise WorkspacePathError("file_unavailable")
                candidate = b""
            elif operation == "write":
                content = arguments["content"]
                assert isinstance(content, str)
                candidate = content.encode("utf-8")
            else:
                if existing is None:
                    raise WorkspacePathError("file_unavailable")
                patch_value = arguments["patch"]
                assert isinstance(patch_value, str)
                try:
                    candidate = apply_strict_single_file_patch(
                        path=normalized_path,
                        original=existing[0].decode("utf-8", errors="strict"),
                        patch_text=patch_value,
                    ).encode("utf-8")
                except PatchApplyError as error:
                    raise WorkspacePathError(error.code) from None
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
                "/dev/null" if deleting else f"b/{normalized_path}",
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
                delete=deleting,
            )
        except SensitiveScanError:
            return _error(tool_name, "sensitive_content_rejected", "Sensitive arguments were rejected")
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
            if change.delete:
                os.unlink(parts[-1], dir_fd=parent_fd)
                try:
                    os.fsync(parent_fd)
                    os.fsync(self.root_fd)
                except OSError:
                    return _commit_error(
                        tool_name, "file_commit_uncertain",
                        "File was deleted but durability could not be confirmed",
                        side_effects=True,
                    )
                return _success(tool_name, "File deleted", {"path": change.path})
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
        self, arguments: dict[str, object], cancel: threading.Event
    ) -> dict[str, object]:
        scope = WorkspaceDiscoveryScope.load(self.root_fd)
        path_value = arguments["path"]
        max_depth = arguments["maxDepth"]
        max_entries = arguments["maxEntries"]
        assert isinstance(path_value, str)
        assert isinstance(max_depth, int) and not isinstance(max_depth, bool)
        assert isinstance(max_entries, int) and not isinstance(max_entries, bool)
        paths: list[str] = []
        truncated = False
        deadline = time.monotonic() + TOOL_DEADLINE_SECONDS

        directory_fd = os.dup(self.root_fd)
        prefix = ""
        if path_value != ".":
            parts = _validate_relative_path(path_value)
            try:
                for part in parts:
                    next_fd = self._open_directory(directory_fd, part)
                    os.close(directory_fd)
                    directory_fd = next_fd
            except Exception:
                os.close(directory_fd)
                raise
            prefix = f"{path_value}/"

        def visit(directory_fd: int, prefix: str, depth: int) -> None:
            nonlocal truncated
            if truncated:
                return
            names, directory_truncated = self._bounded_names(
                directory_fd,
                max_entries + 1,
                cancel,
                deadline,
            )
            truncated = truncated or directory_truncated
            for name in names:
                _check_budget(cancel, deadline)
                if len(paths) >= max_entries:
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
                    if name in HARD_DISCOVERY_DIRECTORIES or _is_sensitive_directory(name):
                        continue
                    if not scope.is_ignored(relative, is_directory=True):
                        paths.append(f"{relative}/")
                    if depth < max_depth:
                        child_fd = self._open_directory(directory_fd, name)
                        try:
                            visit(child_fd, f"{relative}/", depth + 1)
                        finally:
                            os.close(child_fd)
                elif stat.S_ISREG(metadata.st_mode):
                    if not scope.is_ignored(relative, is_directory=False):
                        paths.append(relative)

        try:
            visit(directory_fd, prefix, 1)
        finally:
            os.close(directory_fd)
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
        content_bytes, metadata, normalized_path = self._read_stable_path(
            path_value, cancel, MAX_READ_FILE_BYTES
        )
        try:
            content = content_bytes.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return _error("read_file", "invalid_utf8", "File is not valid UTF-8")
        if "\x00" in content:
            return _error("read_file", "binary_file", "Binary file content is unavailable")
        if content.startswith("\ufeff"):
            content = content[1:]
        truncated = len(content_bytes) > MAX_FILE_BYTES
        if truncated:
            head = content_bytes[:128 * 1024].decode("utf-8", errors="ignore")
            tail = content_bytes[-128 * 1024:].decode("utf-8", errors="ignore")
            content = head + "\n[...truncated...]\n" + tail
        return _success(
            "read_file",
            "Read file",
            {
                "path": normalized_path,
                "content": content,
                "sizeBytes": metadata.st_size,
                "sha256": hashlib.sha256(content_bytes).hexdigest(),
                "truncated": truncated,
                "truncationReason": "head_tail" if truncated else None,
            },
        )

    def _read_file_range(
        self, arguments: dict[str, object], cancel: threading.Event
    ) -> dict[str, object]:
        path_value = arguments["path"]
        start = arguments["startLine"]
        end = arguments["endLine"]
        assert isinstance(path_value, str) and isinstance(start, int) and isinstance(end, int)
        content_bytes, metadata, normalized_path = self._read_stable_path(
            path_value, cancel, MAX_READ_FILE_BYTES
        )
        try:
            content = content_bytes.decode("utf-8-sig", errors="strict")
        except UnicodeDecodeError:
            return _error("read_file_range", "invalid_utf8", "File is not valid UTF-8")
        if "\x00" in content:
            return _error(
                "read_file_range", "binary_file", "Binary file content is unavailable"
            )
        lines = content.splitlines(keepends=True)
        selected: list[str] = []
        size = 0
        next_line: int | None = None
        for number in range(start, min(end, len(lines)) + 1):
            encoded = lines[number - 1].encode("utf-8")
            if size + len(encoded) > MAX_FILE_BYTES:
                next_line = number
                break
            selected.append(lines[number - 1])
            size += len(encoded)
        if next_line is None and end < len(lines):
            next_line = end + 1
        return _success("read_file_range", "Read file range", {
            "path": normalized_path, "startLine": start,
            "endLine": start + len(selected) - 1 if selected else start - 1,
            "content": "".join(selected), "nextLine": next_line,
            "sizeBytes": metadata.st_size,
            "sha256": hashlib.sha256(content_bytes).hexdigest(),
        })

    def _read_stable_path(
        self, path_value: str, cancel: threading.Event, limit: int
    ) -> tuple[bytes, os.stat_result, str]:
        last_error: WorkspacePathError | None = None
        for _attempt in range(2):
            descriptor, normalized_path = self._open_file(path_value)
            try:
                return (*_read_regular_file(descriptor, cancel, limit=limit), normalized_path)
            except WorkspacePathError as error:
                last_error = error
                if error.code != "workspace_changed":
                    raise
            finally:
                os.close(descriptor)
        assert last_error is not None
        raise last_error

    def _search_text(
        self, arguments: dict[str, object], cancel: threading.Event
    ) -> dict[str, object]:
        scope = WorkspaceDiscoveryScope.load(self.root_fd)
        query = arguments["query"]
        path = arguments["path"]
        regex = arguments["regex"]
        include_globs = arguments["includeGlobs"]
        max_results = arguments["maxResults"]
        assert isinstance(query, str)
        assert isinstance(path, str)
        assert isinstance(regex, bool)
        assert isinstance(include_globs, (list, tuple))
        assert isinstance(max_results, int) and not isinstance(max_results, bool)
        result = self.search_driver.search(
            WorkspaceSearchRequest(
                query=query,
                workspace_path=self.workspace.path,
                deadline=time.monotonic() + TOOL_DEADLINE_SECONDS,
                max_results=max_results,
                max_preview_characters=MAX_RG_PREVIEW_CHARACTERS,
                discovery_scope=scope,
                path=path,
                regex=regex,
                include_globs=tuple(include_globs),
            ),
            cancel,
        )
        self._verify_root()
        return _success(
            "search_text",
            "Searched text",
            {
                "matches": [
                    {
                        "path": match.path,
                        "line": match.line,
                        "column": match.column,
                        "preview": match.preview,
                    }
                    for match in result.matches
                ],
                "scannedBytes": result.scanned_bytes,
                "truncated": result.truncated,
                "truncationReason": result.truncation_reason,
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
        try:
            self.workspace_index.refresh(
                self.root_fd,
                cancel,
                validate=self._validate_workspace_index_entry,
                open_directory=self._open_directory,
                deadline=(
                    time.monotonic()
                    + SHELL_PREFLIGHT_DEADLINE_SECONDS
                ),
            )
        except WorkspaceIndexIncomplete:
            raise WorkspacePathError(
                "WORKSPACE_INDEX_INCOMPLETE"
            ) from None

    def _validate_workspace_index_entry(
        self,
        directory_fd: int,
        name: str,
        metadata: os.stat_result,
        relative: str,
        is_git: bool,
    ) -> None:
        if is_git:
            if stat.S_ISLNK(metadata.st_mode):
                raise WorkspacePathError("unsupported_workspace_entry")
            if stat.S_ISREG(metadata.st_mode):
                if metadata.st_nlink != 1:
                    raise WorkspacePathError(
                        "unsupported_workspace_hardlink"
                    )
            elif not stat.S_ISDIR(metadata.st_mode):
                raise WorkspacePathError("unsupported_workspace_entry")
            if metadata.st_uid != self.workspace.owner:
                raise WorkspacePathError("unsupported_workspace_entry")
            return
        if relative != ".env" and (
            _is_shell_sensitive_name(name)
            or _is_sensitive_directory(name)
        ):
            raise WorkspacePathError("sensitive_workspace_content")
        if stat.S_ISLNK(metadata.st_mode):
            return
        if stat.S_ISDIR(metadata.st_mode):
            return
        if not stat.S_ISREG(metadata.st_mode):
            raise WorkspacePathError("unsupported_workspace_entry")
        if metadata.st_nlink != 1:
            raise WorkspacePathError("unsupported_workspace_hardlink")
        if _contains_sensitive_pem(directory_fd, name, metadata):
            raise WorkspacePathError("sensitive_workspace_content")

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
    descriptor: int, cancel: threading.Event, *, limit: int = MAX_FILE_BYTES
) -> tuple[bytes, os.stat_result]:
    before = os.fstat(descriptor)
    if not stat.S_ISREG(before.st_mode):
        raise WorkspacePathError("unsupported_file_type")
    if before.st_nlink != 1:
        raise WorkspacePathError("unsupported_file_hardlink")
    if before.st_size > limit:
        raise WorkspacePathError("file_too_large")
    chunks: list[bytes] = []
    remaining = limit + 1
    while remaining > 0:
        _check_cancel(cancel)
        chunk = os.read(descriptor, min(64 * 1024, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    content = b"".join(chunks)
    after = os.fstat(descriptor)
    if len(content) > limit:
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


def _ascii_lower(value: str) -> str:
    return "".join(chr(ord(char) + 32) if "A" <= char <= "Z" else char for char in value)


def _check_budget(cancel: threading.Event, deadline: float) -> None:
    _check_cancel(cancel)
    if time.monotonic() >= deadline:
        raise WorkspacePathError("tool_timeout")


def _success(
    tool_name: str, summary: str, data: dict[str, object]
) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "toolContractVersion": 1,
        "toolName": tool_name,
        "outcome": "success",
        "code": "ok",
        "summary": summary,
        "data": data,
        "sideEffectsMayExist": False,
        "reconciliationRequired": False,
    }


def _error(tool_name: str, code: str, summary: str) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "toolContractVersion": 1,
        "toolName": tool_name,
        "outcome": "error",
        "code": code,
        "summary": summary,
        "data": {},
        "sideEffectsMayExist": False,
        "reconciliationRequired": False,
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
        "toolContractVersion": 1,
        "toolName": tool_name,
        "outcome": "error",
        "code": code,
        "summary": summary,
        "data": data,
        "sideEffectsMayExist": side_effects,
        "reconciliationRequired": side_effects,
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

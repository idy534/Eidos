from __future__ import annotations

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
import threading
import time
from contextlib import contextmanager
from typing import Callable, Iterator, Literal
import uuid
import json

from pydantic import BaseModel, ValidationError

from eidos_runtime.protocol.schemas import ToolResultDto
from eidos_runtime.sandbox.sensitive import SensitiveScanError, default_scanner
from eidos_runtime.db.storage import WorkspaceIdentity
from eidos_runtime.sandbox.file_metadata import (
    FileMetadataCloneUnavailable,
    FileMetadataError,
    clone_file_with_metadata,
    copy_file_acl,
    copy_replace_metadata,
)
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
    SENSITIVE_NAMES,
    SENSITIVE_SUFFIXES,
    is_sensitive_directory as _is_sensitive_directory,
    is_sensitive_name as _is_sensitive_name,
)
from eidos_runtime.workspace.codex_patch import (
    AddFile,
    DeleteFile,
    PatchError as CodexPatchError,
    UpdateFile,
    apply_update,
    encode_patch,
    patch_grammar,
    parse_patch,
)
from eidos_runtime.workspace.search_driver import (
    MAX_RG_PREVIEW_CHARACTERS,
    RipgrepFileEnumerator,
    RipgrepSearchDriver,
    SearchDriverError,
    WorkspaceSearchDriver,
    WorkspaceSearchRequest,
)
from eidos_runtime.workspace.reader import WorkspacePathError, WorkspaceReader
from eidos_runtime.tools.registry import (
    ToolProvenance,
    ToolRegistry,
    ToolRegistryEntry,
    ToolSpec,
)
from eidos_runtime.model.client import CustomToolFormat
from eidos_runtime.tools.contracts import (
    ApplyPatchInput,
    ApplyPatchResultData,
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
class ToolCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class FileChange:
    path: str
    content: bytes
    base_sha256: str | None
    mode: int
    diff: str
    delete: bool = False
    kind: Literal["add", "update", "delete", "move"] = "update"
    old_content: bytes | None = None
    old_path: str | None = None
    new_path: str | None = None
    create_missing_parent: bool = False
    destination_base_sha256: str | None = None
    destination_mode: int = 0o644
    destination_old_content: bytes | None = None


@dataclass(frozen=True)
class AppliedPatchChange:
    path: str
    kind: Literal["add", "update", "delete", "move"]
    old_path: str | None = None
    new_path: str | None = None
    old_content: str | None = None
    new_content: str | None = None

    def as_dict(self) -> dict[str, object]:
        value: dict[str, object] = {
            "path": self.path,
            "kind": self.kind,
        }
        if self.old_path is not None:
            value["oldPath"] = self.old_path
        if self.new_path is not None:
            value["newPath"] = self.new_path
        return value


@dataclass(frozen=True)
class AppliedPatchDelta:
    changes: tuple[AppliedPatchChange, ...] = ()

    def append(self, change: AppliedPatchChange) -> "AppliedPatchDelta":
        return AppliedPatchDelta((*self.changes, change))

    def as_dicts(self) -> list[dict[str, object]]:
        return [change.as_dict() for change in self.changes]


@dataclass(frozen=True)
class PreparedPatch:
    changes: tuple[FileChange, ...]
    diff: str

    @property
    def path(self) -> str:
        return self.changes[0].path if self.changes else "."

    @property
    def base_sha256(self) -> str | None:
        return self.changes[0].base_sha256 if len(self.changes) == 1 else None


@dataclass(frozen=True)
class ResolvedAuthorizedPath:
    root: Path
    relative_path: str
    authority: Literal["workspace", "active_skill"]
    writable: bool


_BUILTIN_CONTRACTS = (
    ("list_files", "List bounded regular files under a workspace-relative path or an absolute path inside the workspace or active Skill root (default '.'); supports maxDepth and maxEntries. Results are relative to the selected root and may be truncated.", "none", False, 5, "parallel", ListFilesInput, ListFilesResultData, "list_files"),
    ("read_file", "Read one bounded UTF-8 file from the workspace or an active Skill root. The path may be workspace-relative or an authorized absolute path; active Skill roots are read-only. For other Skill resources, use skill_read_resource. Large files return head/tail content; use read_file_range to continue.", "none", False, 5, "parallel", ReadFileInput, ReadFileResultData, "read_file"),
    ("read_file_range", "Read an inclusive bounded line range from one UTF-8 file in the workspace or an active Skill root. The path may be workspace-relative or an authorized absolute path; active Skill roots are read-only. For other Skill resources, use skill_read_resource. Continue from nextLine when present.", "none", False, 5, "parallel", ReadFileRangeInput, ReadFileRangeResultData, "read_file_range"),
    ("search_text", "Search a workspace-relative path or an absolute path inside the workspace or active Skill root (default '.') for a single-line query; supports maxResults, regex, and includeGlobs. Results are relative to the selected root, bounded, and may be truncated.", "none", False, 5, "parallel", SearchTextInput, SearchTextResultData, "search_text"),
    ("apply_patch", "Apply structured Add, Update, Delete, and Move changes to workspace files. Paths, base hashes, and final contents are verified before commit.", "workspace", False, 5, "single", ApplyPatchInput, ApplyPatchResultData, "file_change"),
    ("run_shell", "Run one shell command in the macOS workspace sandbox. Set timeoutSeconds for the command deadline; do not add an external timeout wrapper. For commands that need network access, such as installing dependencies or downloading sources, set networkAccess=request and provide justification. Eidos will request approval and keep macOS Seatbelt. Additional path access and unsandboxed execution also require approval. The legacy sandboxPermissions and additionalPermissions fields remain supported for compatibility. Do not assume GNU timeout, zsh glob behavior, or use tail/head as output boundaries; do not add pipefail unless the command requires it. Eidos bounds and verifies output and workspace changes without rewriting the command.", "shell", False, 600, "single", RunShellInput, RunShellResultData, "run_shell"),
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


def _tool_specs(
    *, supports_custom_tools: bool, supports_tool_grammar: bool
) -> tuple[ToolSpec, ...]:
    if not (supports_custom_tools and supports_tool_grammar):
        return TOOL_SPECS
    patch_format = CustomToolFormat(
        type="grammar", syntax="lark", definition=patch_grammar()
    )
    return tuple(
        spec.model_copy(update={
            "description": (
                "Apply a Codex Patch to workspace files. This is a FREEFORM "
                "tool, so do not wrap the patch in JSON. Input must be raw "
                "Codex Patch text beginning with `*** Begin Patch` and ending "
                "with `*** End Patch`. Paths, base hashes, and final contents "
                "are verified before commit."
            ),
            "input_kind": "custom",
            "input_schema": None,
            "input_format": patch_format,
        }) if spec.name == "apply_patch" else spec
        for spec in TOOL_SPECS
    )


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
        self, arguments: object, cancel: threading.Event
    ) -> FileChange | PreparedPatch | dict[str, object]:
        return self.executor._prepare_file_change(
            self.spec.name, self.operation, arguments, cancel
        )

    def commit_file_change(
        self, change: FileChange, cancel: threading.Event
    ) -> dict[str, object]:
        return self.executor.commit_file_change(self.spec.name, change, cancel)

    def commit_patch(
        self, change: PreparedPatch, cancel: threading.Event
    ) -> tuple[dict[str, object], AppliedPatchDelta]:
        return self.executor.commit_patch(self.spec.name, change, cancel)

    def prepare_shell(self, cwd: str, cancel: threading.Event) -> WorkspaceIdentity:
        return self.executor.prepare_shell(cwd, cancel)


def builtin_tool_registry(
    executor: ToolExecutor,
    *,
    supports_custom_tools: bool = False,
    supports_tool_grammar: bool = False,
) -> ToolRegistry:
    operations = ("list", "read", "range", "search", "patch", "shell")
    specs = _tool_specs(
        supports_custom_tools=supports_custom_tools,
        supports_tool_grammar=supports_tool_grammar,
    )
    entries: list[ToolRegistryEntry] = []
    for spec, operation, contract in zip(
        specs, operations, _BUILTIN_CONTRACTS, strict=True
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
            None if spec.input_kind == "custom" else contract[6],
            contract[7],
        ))
    return ToolRegistry(tuple(entries))


def canonical_tool_result(
    tool_name: str,
    result: dict[str, object],
    *,
    data_model: type[BaseModel] | None = None,
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
    if data_model is None:
        data_model = next(
            (
                contract[7]
                for contract in _BUILTIN_CONTRACTS
                if contract[0] == tool_name
            ),
            None,
        )
        if data_model is None and tool_name == "read_tool_output":
            from eidos_runtime.tools.read_tool_output import ReadToolOutputResultData

            data_model = ReadToolOutputResultData
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
        *,
        supports_custom_tools: bool = False,
        supports_tool_grammar: bool = False,
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
        self.reader = WorkspaceReader(identity)
        self.workspace_index = WorkspaceIndex(identity)
        self.search_driver = search_driver or RipgrepSearchDriver()
        self._active_skill_roots: Callable[[], tuple[Path, ...]] = lambda: ()
        self.supports_custom_tools = supports_custom_tools
        self.supports_tool_grammar = supports_tool_grammar
        self.registry = builtin_tool_registry(
            self,
            supports_custom_tools=supports_custom_tools,
            supports_tool_grammar=supports_tool_grammar,
        )

    def __enter__(self) -> ToolExecutor:
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        self.close()

    def close(self) -> None:
        self.reader.close()
        if self.root_fd >= 0:
            os.close(self.root_fd)
            self.root_fd = -1

    def set_active_skill_roots(
        self, roots: Callable[[], tuple[Path, ...]]
    ) -> None:
        self._active_skill_roots = roots

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
        arguments: object,
        cancel: threading.Event,
    ) -> FileChange | PreparedPatch | dict[str, object]:
        entry = self.registry.get(tool_name)
        if entry is None and tool_name in {"write_file", "delete_file"}:
            operation = "write" if tool_name == "write_file" else "delete"
            return self._prepare_file_change(tool_name, operation, arguments, cancel)
        prepare = getattr(entry.adapter, "prepare_file_change", None) if entry else None
        if entry is None:
            return _error(tool_name, "invalid_arguments", "Invalid arguments")
        if entry.spec.input_kind == "custom":
            validation = entry.validate_custom_input(arguments)
            normalized = validation.normalized_input
        else:
            validation = entry.validate_arguments(
                arguments,
                enforce_size=entry.spec.name != "apply_patch",
            )
            normalized = validation.normalized_arguments
        if (
            validation is None
            or not validation.valid
            or normalized is None
            or prepare is None
        ):
            return _error(tool_name, "invalid_arguments", "Invalid arguments")
        return prepare(normalized, cancel)

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
            summary = (
                "Path is outside the authorized workspace or active Skill roots."
                if error.code == "path_outside_authorized_roots"
                else "Workspace path is unavailable"
            )
            return _error(tool_name, error.code, summary)

    def _prepare_file_change(
        self,
        tool_name: str,
        operation: str,
        arguments: object,
        cancel: threading.Event,
    ) -> FileChange | PreparedPatch | dict[str, object]:
        if operation not in {"write", "patch", "delete"}:
            return _error(tool_name, "tool_not_found", "Tool is not available")
        if operation == "patch":
            return self._prepare_codex_patch(tool_name, arguments, cancel)
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
            else:
                content = arguments["content"]
                assert isinstance(content, str)
                candidate = content.encode("utf-8")
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
            diff = _build_change_diff(
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

    def _prepare_codex_patch(
        self,
        tool_name: str,
        arguments: object,
        cancel: threading.Event,
    ) -> PreparedPatch | dict[str, object]:
        """Prepare structured or raw changes against workspace evidence.

        The parser owns raw patch syntax. The encoder remains only for the
        structured compatibility path. This method keeps path validation, read
        evidence, diff generation, and commit metadata in the existing
        Workspace executor.
        """
        try:
            self._verify_root()
            _check_cancel(cancel)
            if isinstance(arguments, str):
                scanned_input = default_scanner().scan_text(arguments)
                if scanned_input.text != arguments:
                    raise SensitiveScanError("sensitive tool input")
                patch_value = arguments
            else:
                scanned_arguments = default_scanner().scan_json(arguments)
                if scanned_arguments != arguments:
                    raise SensitiveScanError("sensitive tool arguments")
                try:
                    # ToolRegistry has already validated model JSON and normalized
                    # tuple fields to lists. ``strict=False`` only restores those
                    # JSON container shapes; StrictStr and literal fields remain
                    # strict, while this second validation keeps the Workspace
                    # boundary independent from its caller.
                    request = ApplyPatchInput.model_validate(arguments, strict=False)
                except ValidationError:
                    return _error(
                        tool_name,
                        "TOOL_ARGUMENT_CONTRACT_VIOLATION",
                        "Invalid structured apply_patch arguments",
                    )
                patch_value = encode_patch(request)
            hunks = parse_patch(patch_value)
            if not hunks:
                return _error(
                    tool_name,
                    "patch_format_error",
                    "Patch format error: patch contains no file hunks",
                )
            planned: dict[str, tuple[bytes, int] | None] = {}
            prepared: list[FileChange] = []
            diffs: list[str] = []
            for hunk in hunks:
                _check_cancel(cancel)
                if isinstance(hunk, AddFile):
                    kind = "add"
                elif isinstance(hunk, DeleteFile):
                    kind = "delete"
                elif isinstance(hunk, UpdateFile):
                    kind = "update"
                else:
                    raise CodexPatchError(
                        "patch_format_error", "Patch format error: unknown file hunk"
                    )
                path_value = hunk.path
                path = resolve_workspace_write_path(path_value, self.workspace.path)
                existing = planned.get(path, _MISSING)
                if existing is _MISSING:
                    existing = self._read_existing_for_change(
                        path, cancel, allow_missing_parents=True
                    )
                    planned[path] = existing
                if kind == "add":
                    content_value = hunk.content
                    candidate = content_value.encode("utf-8")
                    _validate_patch_content(candidate)
                    if len(candidate) > MAX_FILE_CHANGE_BYTES:
                        raise WorkspacePathError("file_too_large")
                    old_content = None if existing is None else existing[0]
                    mode = 0o644 if existing is None else existing[1]
                    change = _make_patch_change(
                        path=path,
                        candidate=candidate,
                        existing=existing,
                        mode=mode,
                        kind="add",
                        create_missing_parent=True,
                    )
                    planned[path] = (candidate, mode)
                    prepared.append(change)
                    diffs.append(_patch_diff(path, old_content, candidate))
                    continue
                if kind == "delete":
                    if existing is None:
                        raise WorkspacePathError("file_unavailable")
                    _validate_patch_content(existing[0])
                    change = _make_patch_change(
                        path=path,
                        candidate=b"",
                        existing=existing,
                        mode=existing[1],
                        kind="delete",
                        delete=True,
                    )
                    planned[path] = None
                    prepared.append(change)
                    diffs.append(_patch_diff(path, existing[0], b"", delete=True))
                    continue
                if kind != "update":
                    raise CodexPatchError(
                        "patch_format_error",
                        f"Patch format error in hunk for {path}: unknown hunk kind '{kind}'",
                    )
                if existing is None:
                    raise WorkspacePathError("file_unavailable")
                original_text = existing[0].decode("utf-8", errors="strict")
                candidate_text = apply_update(original_text, hunk)
                candidate = candidate_text.encode("utf-8")
                _validate_patch_content(candidate)
                if len(candidate) > MAX_FILE_CHANGE_BYTES:
                    raise WorkspacePathError("file_too_large")
                move_value = hunk.move_to
                if move_value is not None:
                    if not isinstance(move_value, str):
                        raise CodexPatchError(
                            "patch_format_error",
                            f"Patch format error in Update File hunk for {path}: move destination is invalid",
                        )
                    destination = resolve_workspace_write_path(
                        move_value, self.workspace.path
                    )
                    destination_existing = planned.get(destination, _MISSING)
                    if destination_existing is _MISSING:
                        destination_existing = self._read_existing_for_change(
                            destination, cancel, allow_missing_parents=True
                        )
                        planned[destination] = destination_existing
                    change = _make_patch_change(
                        path=path,
                        candidate=candidate,
                        existing=existing,
                        mode=existing[1],
                        kind="move",
                        old_path=path,
                        new_path=destination,
                        create_missing_parent=True,
                        destination=destination_existing,
                        destination_mode=(
                            existing[1]
                            if destination_existing is None
                            else destination_existing[1]
                        ),
                    )
                    planned[path] = None
                    planned[destination] = (candidate, existing[1])
                    prepared.append(change)
                    diffs.append(_patch_diff(path, existing[0], candidate, destination))
                else:
                    change = _make_patch_change(
                        path=path,
                        candidate=candidate,
                        existing=existing,
                        mode=existing[1],
                        kind="update",
                    )
                    planned[path] = (candidate, existing[1])
                    prepared.append(change)
                    diffs.append(_patch_diff(path, existing[0], candidate))
            diff = "".join(diffs)
            if len(diff.encode("utf-8")) > MAX_DIFF_BYTES:
                raise WorkspacePathError("diff_too_large")
            return PreparedPatch(tuple(prepared), diff)
        except SensitiveScanError:
            return _error(
                tool_name,
                "sensitive_content_rejected",
                "Sensitive arguments were rejected",
            )
        except UnicodeDecodeError:
            return _error(tool_name, "invalid_utf8", "File is not valid UTF-8")
        except ToolCancelled:
            return _error(tool_name, "canceled", "Tool was canceled")
        except CodexPatchError as error:
            if error.code == "patch_format_error":
                location = f" at line {error.line_number}" if error.line_number else ""
                target = f" for {error.target_path}" if error.target_path else ""
                summary = f"Patch format error{location}{target}: {error.message}"
            else:
                summary = error.message
            return _error(tool_name, error.code, summary)
        except WorkspacePathError as error:
            return _error(
                tool_name,
                error.code,
                f"File change could not be prepared: {error.code}",
            )

    def commit_file_change(
        self,
        tool_name: str,
        change: FileChange,
        cancel: threading.Event,
    ) -> dict[str, object]:
        temporary_name: str | None = None
        preserve_temporary = False
        parent_fd = -1
        source_fd = -1
        try:
            self._verify_root()
            _check_cancel(cancel)
            parts = _validate_relative_path(change.path)
            parent_fd = self._open_parent(
                parts, create_missing=change.create_missing_parent
            )
            source_fd = self._open_verified_base(
                parent_fd, parts[-1], change.base_sha256, cancel
            )
            if change.delete:
                if source_fd >= 0:
                    os.close(source_fd)
                    source_fd = -1
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
            cloned_metadata = False
            if source_fd >= 0:
                try:
                    clone_file_with_metadata(
                        source_fd,
                        self.root_fd,
                        temporary_name,
                    )
                    cloned_metadata = True
                except FileMetadataCloneUnavailable:
                    try:
                        os.unlink(temporary_name, dir_fd=self.root_fd)
                    except FileNotFoundError:
                        pass
                except FileMetadataError as error:
                    raise WorkspacePathError(
                        "file_metadata_preservation_failed"
                    ) from error
            if cloned_metadata:
                descriptor = _open_cloned_file_for_write(
                    self.root_fd,
                    temporary_name,
                )
            else:
                flags = os.O_RDWR | os.O_CREAT | os.O_EXCL
                if hasattr(os, "O_NOFOLLOW"):
                    flags |= os.O_NOFOLLOW
                descriptor = os.open(
                    temporary_name,
                    flags,
                    change.mode,
                    dir_fd=self.root_fd,
                )
            try:
                if cloned_metadata:
                    os.ftruncate(descriptor, 0)
                if source_fd >= 0 and not cloned_metadata:
                    try:
                        copy_replace_metadata(source_fd, descriptor)
                    except FileMetadataError as error:
                        raise WorkspacePathError(
                            "file_metadata_preservation_failed"
                        ) from error
                offset = 0
                while offset < len(change.content):
                    _check_cancel(cancel)
                    written = os.write(descriptor, change.content[offset:])
                    if written <= 0:
                        raise WorkspacePathError("file_write_failed")
                    offset += written
                os.fchmod(descriptor, change.mode)
                if source_fd >= 0 and cloned_metadata:
                    try:
                        copy_file_acl(source_fd, descriptor)
                    except FileMetadataError as error:
                        raise WorkspacePathError(
                            "file_metadata_preservation_failed"
                        ) from error
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            if source_fd >= 0:
                os.close(source_fd)
                source_fd = -1
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
            current_parent_fd = self._open_parent(
                parts, create_missing=change.create_missing_parent
            )
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
                        "File changed before commit; the candidate was rolled back",
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
                    "File changed before commit; the candidate was rolled back",
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
                    "File changed before commit; the candidate was rolled back",
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
            if source_fd >= 0:
                os.close(source_fd)
            if temporary_name is not None and not preserve_temporary:
                try:
                    os.unlink(temporary_name, dir_fd=self.root_fd)
                except OSError:
                    pass
            if parent_fd >= 0:
                os.close(parent_fd)

    def commit_patch(
        self,
        tool_name: str,
        prepared: PreparedPatch,
        cancel: threading.Event,
    ) -> tuple[dict[str, object], AppliedPatchDelta]:
        """Commit patch changes in order through the existing file primitive."""
        delta = AppliedPatchDelta()
        for change in prepared.changes:
            _check_cancel(cancel)
            if change.kind != "move":
                result = self.commit_file_change(tool_name, change, cancel)
                if _file_change_committed(result):
                    delta = delta.append(_delta_for_file_change(change))
                if result.get("outcome") != "success":
                    return _patch_failure_with_delta(result, delta), delta
                continue

            destination = change.new_path
            source = change.old_path or change.path
            if destination is None:
                return _patch_failure_with_delta(
                    _error(
                        tool_name,
                        "patch_format_error",
                        f"Patch format error in move hunk for {source}: destination is missing",
                    ),
                    delta,
                ), delta
            destination_change = FileChange(
                path=destination,
                content=change.content,
                base_sha256=change.destination_base_sha256,
                mode=change.destination_mode,
                diff=change.diff,
                kind="add" if change.destination_base_sha256 is None else "update",
                old_content=change.destination_old_content,
                create_missing_parent=True,
            )
            destination_result = self.commit_file_change(
                tool_name, destination_change, cancel
            )
            if destination_result.get("outcome") != "success":
                return _patch_failure_with_delta(destination_result, delta), delta
            source_change = FileChange(
                path=source,
                content=b"",
                base_sha256=change.base_sha256,
                mode=change.mode,
                diff=change.diff,
                delete=True,
                kind="delete",
                old_content=change.old_content,
            )
            source_result = self.commit_file_change(tool_name, source_change, cancel)
            if source_result.get("outcome") != "success":
                # The destination is already a real committed change. Keep it
                # visible as an add/update because the move is incomplete.
                delta = delta.append(_delta_for_file_change(destination_change))
                return _patch_failure_with_delta(source_result, delta), delta
            delta = delta.append(
                AppliedPatchChange(
                    path=source,
                    kind="move",
                    old_path=source,
                    new_path=destination,
                    old_content=_decode_patch_bytes(change.old_content),
                    new_content=_decode_patch_bytes(change.content),
                )
            )

        if not delta.changes:
            return _success(
                tool_name,
                "Patch did not change any files",
                {"path": prepared.path, "changes": []},
            ), delta
        summary = "Success. Updated the following files: " + ", ".join(
            _delta_summary(change) for change in delta.changes
        )
        return _success(
            tool_name,
            summary,
            {"path": prepared.path, "changes": delta.as_dicts()},
        ), delta

    def _list_files(
        self, arguments: dict[str, object], cancel: threading.Event
    ) -> dict[str, object]:
        path_value = arguments["path"]
        max_depth = arguments["maxDepth"]
        max_entries = arguments["maxEntries"]
        assert isinstance(path_value, str)
        assert isinstance(max_depth, int) and not isinstance(max_depth, bool)
        assert isinstance(max_entries, int) and not isinstance(max_entries, bool)
        resolved = self._resolve_read_path(path_value)
        with self._authorized_reader(resolved) as reader:
            path_value = resolved.relative_path
            scope = WorkspaceDiscoveryScope.load(reader.root_fd)
            deadline = time.monotonic() + TOOL_DEADLINE_SECONDS
            base = "" if path_value == "." else path_value.rstrip("/")
            base_depth = len(Path(base).parts) if base else 0
            discovered, truncated = RipgrepFileEnumerator().enumerate(
                resolved.root,
                deadline=deadline,
                max_entries=max_entries + 1,
                cancel=cancel,
                path=path_value,
            )
            reader._verify_root()
            entries: set[str] = set()
            for relative in discovered:
                _check_budget(cancel, deadline)
                try:
                    metadata = _stat_relative_path(reader.root_fd, relative)
                except OSError:
                    continue
                if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
                    continue
                parts = Path(relative).parts
                relative_depth = len(parts) - base_depth
                if relative_depth < 1 or scope.is_ignored(relative, is_directory=False):
                    continue
                for depth in range(1, min(relative_depth - 1, max_depth) + 1):
                    directory = "/".join(parts[: base_depth + depth])
                    if not scope.is_ignored(directory, is_directory=True):
                        entries.add(directory + "/")
                if relative_depth <= max_depth:
                    entries.add(relative)
                if len(entries) >= max_entries:
                    truncated = True
                    break
            paths = sorted(entries, key=os.fsencode)[:max_entries]
            return _success(
                "list_files",
                "Listed files",
                {
                    "paths": [_project_read_path(resolved, path) for path in paths],
                    "truncated": truncated,
                },
            )

    def _read_file(
        self, arguments: dict[str, object], cancel: threading.Event
    ) -> dict[str, object]:
        path_value = arguments["path"]
        assert isinstance(path_value, str)
        resolved = self._resolve_read_path(path_value)
        with self._authorized_reader(resolved) as reader:
            content_bytes, metadata, normalized_path, _truncated = reader.read_file_bytes(
                resolved.relative_path, cancel=cancel, limit=MAX_READ_FILE_BYTES
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
                "path": _project_read_path(resolved, normalized_path),
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
        resolved = self._resolve_read_path(path_value)
        content_bytes, metadata, normalized_path = self._read_stable_path(
            resolved, cancel, MAX_READ_FILE_BYTES
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
            "path": _project_read_path(resolved, normalized_path), "startLine": start,
            "endLine": start + len(selected) - 1 if selected else start - 1,
            "content": "".join(selected), "nextLine": next_line,
            "sizeBytes": metadata.st_size,
            "sha256": hashlib.sha256(content_bytes).hexdigest(),
        })

    def _read_stable_path(
        self, resolved: ResolvedAuthorizedPath, cancel: threading.Event, limit: int
    ) -> tuple[bytes, os.stat_result, str]:
        last_error: WorkspacePathError | None = None
        with self._authorized_reader(resolved) as reader:
            for _attempt in range(2):
                descriptor, normalized_path = self._open_file(
                    resolved.relative_path, root_fd=reader.root_fd
                )
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
        assert isinstance(path, str)
        resolved = self._resolve_read_path(path)
        with self._authorized_reader(resolved) as reader:
            scope = WorkspaceDiscoveryScope.load(reader.root_fd)
            result = self.search_driver.search(
                WorkspaceSearchRequest(
                    query=query,
                    workspace_path=resolved.root,
                    deadline=time.monotonic() + TOOL_DEADLINE_SECONDS,
                    max_results=max_results,
                    max_preview_characters=MAX_RG_PREVIEW_CHARACTERS,
                    discovery_scope=scope,
                    path=resolved.relative_path,
                    regex=regex,
                    include_globs=tuple(include_globs),
                ),
                cancel,
            )
            reader._verify_root()
            if resolved.authority == "workspace":
                self._verify_root()
            return _success(
                "search_text",
                "Searched text",
                {
                    "matches": [
                        {
                            "path": _project_read_path(resolved, match.path),
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

    def _resolve_read_path(self, value: str) -> ResolvedAuthorizedPath:
        return resolve_read_path(
            value,
            self.workspace.path,
            tuple(self._active_skill_roots()),
        )

    @contextmanager
    def _authorized_reader(
        self, resolved: ResolvedAuthorizedPath
    ) -> Iterator[WorkspaceReader]:
        if resolved.authority == "workspace":
            yield self.reader
            return
        reader = WorkspaceReader(resolved.root)
        try:
            yield reader
        finally:
            reader.close()

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
        self,
        path: str,
        cancel: threading.Event,
        *,
        allow_missing_parents: bool = False,
    ) -> tuple[bytes, int] | None:
        try:
            descriptor, _normalized = self._open_file(path)
        except WorkspacePathError as error:
            if error.code == "file_unavailable":
                return None
            if (
                allow_missing_parents
                and error.code == "workspace_boundary_violation"
                and _workspace_parent_is_missing(self.workspace.path, path)
            ):
                return None
            raise
        try:
            metadata = os.fstat(descriptor)
            mode = stat.S_IMODE(metadata.st_mode)
            if mode & 0o7000:
                raise WorkspacePathError("unsupported_file_mode")
            if metadata.st_nlink != 1:
                raise WorkspacePathError("unsupported_workspace_hardlink")
            if metadata.st_uid != os.getuid():
                raise WorkspacePathError("unsupported_file_owner")
            if getattr(metadata, "st_flags", 0) != 0:
                raise WorkspacePathError("unsupported_file_flags")
            content, _stable = _read_regular_file(descriptor, cancel)
            return content, mode
        finally:
            os.close(descriptor)

    def _open_parent(
        self, parts: tuple[str, ...], *, create_missing: bool = False
    ) -> int:
        directory_fd = os.dup(self.root_fd)
        try:
            for part in parts[:-1]:
                next_fd = self._open_directory(
                    directory_fd, part, create_missing=create_missing
                )
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
        descriptor = self._open_verified_base(
            parent_fd, name, expected_sha256, cancel
        )
        if descriptor >= 0:
            os.close(descriptor)

    def _open_verified_base(
        self,
        parent_fd: int,
        name: str,
        expected_sha256: str | None,
        cancel: threading.Event,
    ) -> int:
        try:
            descriptor = self._open_file_at(parent_fd, name)
        except WorkspacePathError as error:
            if expected_sha256 is None and error.code == "file_unavailable":
                return -1
            raise WorkspacePathError("file_version_conflict") from None
        try:
            content, _metadata = _read_regular_file(descriptor, cancel)
        except Exception:
            os.close(descriptor)
            raise
        if (
            expected_sha256 is None
            or hashlib.sha256(content).hexdigest() != expected_sha256
        ):
            os.close(descriptor)
            raise WorkspacePathError("file_version_conflict")
        return descriptor

    def _open_file(
        self, value: str, *, root_fd: int | None = None
    ) -> tuple[int, str]:
        parts = _validate_relative_path(value)
        directory_fd = os.dup(self.root_fd if root_fd is None else root_fd)
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
    def _open_directory(
        parent_fd: int, name: str, *, create_missing: bool = False
    ) -> int:
        flags = os.O_RDONLY | os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            return os.open(name, flags, dir_fd=parent_fd)
        except FileNotFoundError:
            if not create_missing:
                raise WorkspacePathError("workspace_boundary_violation") from None
            try:
                os.mkdir(name, 0o755, dir_fd=parent_fd)
                os.fsync(parent_fd)
            except FileExistsError:
                pass
            except OSError:
                raise WorkspacePathError("workspace_boundary_violation") from None
            try:
                return os.open(name, flags, dir_fd=parent_fd)
            except OSError:
                raise WorkspacePathError("workspace_boundary_violation") from None
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


def _open_cloned_file_for_write(root_fd: int, name: str) -> int:
    flags = os.O_RDWR | os.O_NONBLOCK
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        return os.open(name, flags, dir_fd=root_fd)
    except PermissionError:
        # A cloned regular file can retain a read-only mode. The temporary
        # inode is not user-visible, so grant it a private write mode before
        # reopening it for the candidate bytes. The final mode is restored by
        # commit_file_change after the write completes.
        read_flags = os.O_RDONLY | os.O_NONBLOCK
        if hasattr(os, "O_NOFOLLOW"):
            read_flags |= os.O_NOFOLLOW
        descriptor = os.open(name, read_flags, dir_fd=root_fd)
        try:
            os.fchmod(descriptor, 0o600)
        finally:
            os.close(descriptor)
        return os.open(name, flags, dir_fd=root_fd)


def _stat_relative_path(root_fd: int, relative: str) -> os.stat_result:
    parts = Path(relative).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise OSError("invalid relative path")
    opened: list[int] = []
    parent_fd = root_fd
    try:
        for part in parts[:-1]:
            descriptor = os.open(
                part,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_fd,
            )
            opened.append(descriptor)
            parent_fd = descriptor
        descriptor = os.open(
            parts[-1], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent_fd
        )
        opened.append(descriptor)
        return os.fstat(descriptor)
    finally:
        for descriptor in reversed(opened):
            os.close(descriptor)


def resolve_read_path(
    value: str,
    workspace_root: Path,
    active_skill_roots: tuple[Path, ...],
) -> ResolvedAuthorizedPath:
    if (
        not isinstance(value, str)
        or not value
        or "\x00" in value
        or "\\" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise WorkspacePathError("workspace_boundary_violation")
    candidate = Path(value)
    if ".." in candidate.parts:
        raise WorkspacePathError("workspace_boundary_violation")
    workspace_root = Path(workspace_root)
    if not candidate.is_absolute():
        return ResolvedAuthorizedPath(
            workspace_root,
            "." if value == "." else candidate.as_posix(),
            "workspace",
            True,
        )

    roots: list[tuple[Path, Literal["active_skill", "workspace"], bool]] = [
        (Path(root), "active_skill", False)
        for root in active_skill_roots
    ]
    roots.append((workspace_root, "workspace", True))
    for root, authority, writable in sorted(
        roots, key=lambda item: len(item[0].parts), reverse=True
    ):
        if candidate == root or root in candidate.parents:
            relative = candidate.relative_to(root).as_posix()
            return ResolvedAuthorizedPath(
                root,
                relative or ".",
                authority,
                writable,
            )
    raise WorkspacePathError("path_outside_authorized_roots")


def _project_read_path(
    resolved: ResolvedAuthorizedPath, relative_path: str
) -> str:
    if resolved.authority == "workspace":
        return relative_path
    projected = (resolved.root / relative_path).as_posix()
    if relative_path.endswith("/"):
        projected += "/"
    return projected


def resolve_workspace_write_path(value: str, workspace_root: Path) -> str:
    try:
        resolved = resolve_read_path(value, workspace_root, ())
    except WorkspacePathError as error:
        if error.code == "path_outside_authorized_roots":
            raise WorkspacePathError("workspace_boundary_violation") from None
        raise
    return "/".join(_validate_relative_path(resolved.relative_path))


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


_MISSING = object()


def _make_patch_change(
    *,
    path: str,
    candidate: bytes,
    existing: tuple[bytes, int] | None,
    mode: int,
    kind: Literal["add", "update", "delete", "move"],
    delete: bool = False,
    old_path: str | None = None,
    new_path: str | None = None,
    create_missing_parent: bool = False,
    destination: tuple[bytes, int] | None = None,
    destination_mode: int = 0o644,
) -> FileChange:
    old_content = None if existing is None else existing[0]
    base_sha256 = (
        None if existing is None else hashlib.sha256(existing[0]).hexdigest()
    )
    destination_base_sha256 = (
        None
        if destination is None
        else hashlib.sha256(destination[0]).hexdigest()
    )
    destination_old_content = None if destination is None else destination[0]
    return FileChange(
        path=path,
        content=candidate,
        base_sha256=base_sha256,
        mode=mode,
        diff=_patch_diff(
            path, old_content, candidate, new_path, delete=delete
        ),
        delete=delete,
        kind=kind,
        old_content=old_content,
        old_path=old_path,
        new_path=new_path,
        create_missing_parent=create_missing_parent,
        destination_base_sha256=destination_base_sha256,
        destination_mode=destination_mode,
        destination_old_content=destination_old_content,
    )


def _patch_diff(
    path: str,
    old_content: bytes | None,
    new_content: bytes,
    move_path: str | None = None,
    *,
    delete: bool = False,
) -> str:
    old_text = _decode_patch_bytes(old_content) or ""
    new_text = _decode_patch_bytes(new_content) or ""
    fromfile = "/dev/null" if old_content is None else f"a/{path}"
    tofile = "/dev/null" if delete else (
        f"b/{move_path or path}"
    )
    diff = _build_change_diff(old_text, new_text, fromfile, tofile)
    if not diff and old_content is None:
        return f"--- /dev/null\n+++ b/{move_path or path}\n"
    if move_path is not None and diff:
        lines = diff.splitlines(keepends=True)
        if len(lines) >= 2:
            lines[1] = f"+++ b/{move_path}\n"
            diff = "".join(lines)
    return diff


def _decode_patch_bytes(value: bytes | None) -> str | None:
    if value is None:
        return None
    return value.decode("utf-8", errors="strict")


def _validate_patch_content(content: bytes) -> None:
    try:
        text = content.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        raise WorkspacePathError("invalid_utf8") from None
    if _has_unsupported_text_control(text):
        raise WorkspacePathError("unsupported_text_content")


def _file_change_committed(result: dict[str, object]) -> bool:
    return result.get("outcome") == "success" and result.get("code") != "no_changes"


def _delta_for_file_change(change: FileChange) -> AppliedPatchChange:
    if change.kind == "delete":
        new_content = None
    else:
        new_content = _decode_patch_bytes(change.content)
    return AppliedPatchChange(
        path=change.path,
        kind=change.kind,
        old_path=change.old_path,
        new_path=change.new_path,
        old_content=_decode_patch_bytes(change.old_content),
        new_content=new_content,
    )


def _delta_summary(change: AppliedPatchChange) -> str:
    if change.kind == "add":
        return f"A {change.path}"
    if change.kind == "delete":
        return f"D {change.path}"
    if change.kind == "move":
        return f"M {change.old_path or change.path} -> {change.new_path or change.path}"
    return f"M {change.path}"


def _patch_failure_with_delta(
    result: dict[str, object], delta: AppliedPatchDelta
) -> dict[str, object]:
    failed = dict(result)
    data = failed.get("data")
    merged = dict(data) if isinstance(data, dict) else {}
    merged["changes"] = delta.as_dicts()
    if delta.changes and "path" not in merged:
        merged["path"] = delta.changes[-1].path
    failed["data"] = merged
    return failed


def _workspace_parent_is_missing(root: Path, path: str) -> bool:
    parts = tuple(part for part in path.split("/") if part)
    if len(parts) <= 1:
        return False
    return not root.joinpath(*parts[:-1]).exists()


def _build_change_diff(
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

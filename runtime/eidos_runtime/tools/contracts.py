from __future__ import annotations

import hashlib
import json
import math
from pathlib import PurePosixPath
import re
import urllib.parse
from types import UnionType
from typing import Annotated, ClassVar, Literal, Protocol, Union, get_args, get_origin

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    StrictInt,
    StrictStr,
    create_model,
    field_validator,
    model_validator,
)

from eidos_runtime.sandbox.permissions import (
    AdditionalPermissionProfile,
    SandboxPermissions,
)


class StrictToolModel(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


LIST_FILES_MAX_DEPTH = 5
LIST_FILES_MAX_ENTRIES = 2_000
SEARCH_TEXT_MAX_RESULTS = 100
SEARCH_TEXT_MAX_INCLUDE_GLOBS = 32


def _relative_path(value: str, *, allow_dot: bool = False) -> str:
    if not value or "\x00" in value or "\\" in value:
        raise ValueError("invalid_relative_path")
    if allow_dot and value == ".":
        return value
    path = PurePosixPath(value)
    if path.is_absolute() or value.endswith("/") or any(
        part in {"", ".", ".."} for part in path.parts
    ):
        raise ValueError("invalid_relative_path")
    if len(value.encode("utf-8")) > 512:
        raise ValueError("path_too_large")
    return value


def _utf8_limit(value: str, limit: int, code: str) -> str:
    if len(value.encode("utf-8")) > limit:
        raise ValueError(code)
    return value


class ListFilesInput(StrictToolModel):
    path: StrictStr = Field(
        default=".", description="Workspace-relative directory scope; defaults to '.'."
    )
    maxDepth: StrictInt = Field(
        default=LIST_FILES_MAX_DEPTH,
        ge=1,
        le=LIST_FILES_MAX_DEPTH,
        description="Maximum directory depth below path.",
    )
    maxEntries: StrictInt = Field(
        default=LIST_FILES_MAX_ENTRIES,
        ge=1,
        le=LIST_FILES_MAX_ENTRIES,
        description="Maximum workspace-relative entries to return.",
    )

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _relative_path(value, allow_dot=True)


class ReadFileInput(StrictToolModel):
    path: StrictStr = Field(description="Workspace-relative UTF-8 file path.")

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _relative_path(value)


class ReadFileRangeInput(ReadFileInput):
    startLine: StrictInt = Field(ge=1, description="First line, one-based.")
    endLine: StrictInt = Field(ge=1, description="Last requested line, inclusive.")

    @model_validator(mode="after")
    def validate_range(self):
        if self.startLine > self.endLine or self.endLine - self.startLine >= 2_000:
            raise ValueError("invalid_line_range")
        return self


class SearchTextInput(StrictToolModel):
    query: StrictStr = Field(
        min_length=1,
        max_length=512,
        description="Single-line text to find; set regex=true to interpret it as a regular expression.",
    )
    path: StrictStr = Field(
        default=".", description="Workspace-relative search scope; defaults to '.'."
    )
    regex: bool = Field(
        default=False,
        description="Treat query as a regular expression instead of a literal.",
    )
    includeGlobs: tuple[StrictStr, ...] = Field(
        default=(),
        max_length=SEARCH_TEXT_MAX_INCLUDE_GLOBS,
        description="Optional workspace-relative file globs to include.",
    )
    maxResults: StrictInt = Field(
        default=SEARCH_TEXT_MAX_RESULTS,
        ge=1,
        le=SEARCH_TEXT_MAX_RESULTS,
        description="Maximum matching lines to return.",
    )

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _relative_path(value, allow_dot=True)

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        if "\n" in value or "\r" in value:
            raise ValueError("query_must_be_single_line")
        return _utf8_limit(value, 512, "query_too_large")

    @field_validator("includeGlobs")
    @classmethod
    def validate_include_globs(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        for value in values:
            if (
                not value
                or "\x00" in value
                or "\n" in value
                or "\r" in value
                or "\\" in value
                or value.startswith("/")
                or any(part in {"", ".", ".."} for part in value.split("/"))
            ):
                raise ValueError("invalid_include_glob")
            _utf8_limit(value, 512, "include_glob_too_large")
        return values


class WriteFileInput(ReadFileInput):
    content: StrictStr = Field(description="Complete replacement UTF-8 content.")

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        return _utf8_limit(value, 256 * 1024, "content_too_large")


class ApplyPatchInput(ReadFileInput):
    patch: StrictStr = Field(description="Strict unified diff for exactly one file.")

    @field_validator("patch")
    @classmethod
    def validate_patch(cls, value: str) -> str:
        return _utf8_limit(value, 512 * 1024, "patch_too_large")


class DeleteFileInput(ReadFileInput):
    pass


class RunShellInput(StrictToolModel):
    command: StrictStr = Field(min_length=1, max_length=16 * 1024)
    cwd: StrictStr = "."
    timeoutSeconds: StrictInt = Field(default=120, ge=1, le=600)
    sandboxPermissions: SandboxPermissions = SandboxPermissions.USE_DEFAULT
    additionalPermissions: AdditionalPermissionProfile | None = None
    justification: StrictStr | None = Field(default=None, max_length=2_000)

    @field_validator("command")
    @classmethod
    def validate_command(cls, value: str) -> str:
        return _utf8_limit(value, 16 * 1024, "command_too_large")

    @field_validator("cwd")
    @classmethod
    def validate_cwd(cls, value: str) -> str:
        return _relative_path(value, allow_dot=True)

    @model_validator(mode="after")
    def validate_permissions(self):
        if self.additionalPermissions is None:
            if (
                self.sandboxPermissions
                is SandboxPermissions.WITH_ADDITIONAL_PERMISSIONS
            ):
                raise ValueError("additional_permissions_required")
        else:
            self.additionalPermissions.validate_for(self.sandboxPermissions)
        if (
            self.sandboxPermissions is not SandboxPermissions.USE_DEFAULT
            and not self.justification
        ):
            raise ValueError("sandbox_override_justification_required")
        return self


class SkillReadInput(StrictToolModel):
    qualifiedId: StrictStr = Field(min_length=1, max_length=129)

    @field_validator("qualifiedId")
    @classmethod
    def validate_qualified_id(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}:[a-z][a-z0-9-]{0,63}", value):
            raise ValueError("invalid_qualified_skill_id")
        return value


class SkillReadResourceInput(SkillReadInput):
    resourcePath: StrictStr = Field(min_length=1, max_length=512)

    @field_validator("resourcePath")
    @classmethod
    def validate_resource_path(cls, value: str) -> str:
        return _relative_path(value)


class SkillFileInput(StrictToolModel):
    path: StrictStr = Field(min_length=1, max_length=512)
    content: StrictStr = Field(max_length=256 * 1024)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        path = _relative_path(value)
        if path == "SKILL.md":
            raise ValueError("reserved_skill_path")
        return path

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("invalid_skill_content")
        return _utf8_limit(value, 256 * 1024, "skill_content_too_large")


class SkillCreateInput(StrictToolModel):
    name: StrictStr = Field(min_length=1, max_length=64)
    description: StrictStr = Field(min_length=1, max_length=1024)
    instructions: StrictStr = Field(min_length=1, max_length=128 * 1024)
    files: tuple[SkillFileInput, ...] = Field(default=(), max_length=64)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", value):
            raise ValueError("invalid_skill_name")
        return value

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str) -> str:
        if (
            value.strip() != value
            or len(value.splitlines()) != 1
            or "\x00" in value
            or value[0] in "\"'"
            or value[-1] in "\"'"
        ):
            raise ValueError("invalid_skill_description")
        return _utf8_limit(value, 1024, "skill_description_too_large")

    @field_validator("instructions")
    @classmethod
    def validate_instructions(cls, value: str) -> str:
        if not value.strip() or "\x00" in value:
            raise ValueError("invalid_skill_instructions")
        return _utf8_limit(
            value.strip() + "\n", 128 * 1024, "skill_instructions_too_large"
        )

    @model_validator(mode="after")
    def validate_files(self):
        paths = [value.path for value in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("duplicate_skill_path")
        if any(
            parent == other
            for path in paths
            for parent in PurePosixPath(path).parents
            for other in paths
            if parent != PurePosixPath(".")
        ):
            raise ValueError("conflicting_skill_path")
        total = len(self.instructions.encode("utf-8")) + sum(
            len(value.content.encode("utf-8")) for value in self.files
        )
        if total > 8 * 1024 * 1024:
            raise ValueError("skill_too_large")
        return self


class SkillInstallInput(StrictToolModel):
    url: StrictStr = Field(min_length=1, max_length=2048)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        parsed = urllib.parse.urlparse(value)
        parts = [part for part in parsed.path.split("/") if part]
        component = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
        if (
            parsed.scheme != "https"
            or parsed.hostname != "github.com"
            or parsed.port is not None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or len(parts) < 5
            or parts[2] != "tree"
            or not component.fullmatch(parts[0])
            or not component.fullmatch(parts[1].removesuffix(".git"))
            or any(part in {"", ".", ".."} for part in parts[3:])
        ):
            raise ValueError("invalid_skill_url")
        owner, repo, ref = parts[0], parts[1].removesuffix(".git"), parts[3]
        return f"https://github.com/{owner}/{repo}/tree/{ref}/{'/'.join(parts[4:])}"


class ToolSearchInput(StrictToolModel):
    query: StrictStr = Field(min_length=1, max_length=256)
    limit: StrictInt = Field(default=10, ge=1, le=16)

    @field_validator("query")
    @classmethod
    def validate_query(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("empty_tool_search_query")
        return _utf8_limit(normalized, 256, "tool_search_query_too_large")


class SearchMatch(StrictToolModel):
    path: StrictStr
    line: StrictInt = Field(ge=1)
    column: StrictInt = Field(ge=1)
    preview: StrictStr


class ToolSearchHit(StrictToolModel):
    name: StrictStr
    description: StrictStr
    provenance: dict[str, object]
    score: StrictInt = Field(ge=0)


class EmptyResultData(StrictToolModel):
    SUCCESS_REQUIRED: ClassVar[tuple[str, ...]] = ()


class ListFilesResultData(StrictToolModel):
    SUCCESS_REQUIRED: ClassVar[tuple[str, ...]] = ("paths", "truncated")
    paths: tuple[StrictStr, ...] | None = None
    truncated: bool | None = None
    truncationReason: StrictStr | None = None


class ReadFileResultData(StrictToolModel):
    SUCCESS_REQUIRED: ClassVar[tuple[str, ...]] = (
        "path", "content", "sizeBytes", "sha256", "truncated",
    )
    path: StrictStr | None = None
    content: StrictStr | None = None
    sizeBytes: StrictInt | None = Field(default=None, ge=0)
    sha256: StrictStr | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    truncated: bool | None = None
    truncationReason: StrictStr | None = None


class ReadFileRangeResultData(StrictToolModel):
    SUCCESS_REQUIRED: ClassVar[tuple[str, ...]] = (
        "path", "content", "sizeBytes", "sha256", "startLine", "endLine",
    )
    path: StrictStr | None = None
    content: StrictStr | None = None
    sizeBytes: StrictInt | None = Field(default=None, ge=0)
    sha256: StrictStr | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    startLine: StrictInt | None = Field(default=None, ge=1)
    endLine: StrictInt | None = Field(default=None, ge=1)
    nextLine: StrictInt | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_range(self):
        if (
            self.startLine is not None
            and self.endLine is not None
            and self.startLine > self.endLine
        ):
            raise ValueError("invalid_returned_range")
        if (
            self.nextLine is not None
            and self.endLine is not None
            and self.nextLine <= self.endLine
        ):
            raise ValueError("invalid_next_line")
        return self


class SearchTextResultData(StrictToolModel):
    SUCCESS_REQUIRED: ClassVar[tuple[str, ...]] = (
        "matches", "scannedBytes", "truncated",
    )
    matches: tuple[SearchMatch, ...] | None = None
    scannedBytes: StrictInt | None = Field(default=None, ge=0)
    truncated: bool | None = None
    truncationReason: StrictStr | None = None


class WorkspaceResultData(StrictToolModel):
    SUCCESS_REQUIRED: ClassVar[tuple[str, ...]] = ("path",)
    path: StrictStr | None = None
    sha256: StrictStr | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    baseSha256: StrictStr | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    sizeBytes: StrictInt | None = Field(default=None, ge=0)
    commandOutcome: StrictStr | None = None
    workspaceChanged: bool | None = None
    workspaceDiffHash: StrictStr | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    workspaceManifestComplete: bool | None = None
    workspaceManifestTruncated: bool | None = None
    workspaceDiffIncomplete: bool | None = None
    workspaceChangeState: Literal["unchanged", "changed", "unknown"] | None = None
    created: tuple[StrictStr, ...] | None = None
    modified: tuple[StrictStr, ...] | None = None
    deleted: tuple[StrictStr, ...] | None = None


class RunShellResultData(WorkspaceResultData):
    ALLOW_SUCCESS_RECONCILIATION: ClassVar[bool] = True
    SUCCESS_REQUIRED: ClassVar[tuple[str, ...]] = (
        "exitCode", "stdout", "stderr", "truncated", "termination",
        "workspaceChanged",
    )
    exitCode: StrictInt | None = None
    stdout: StrictStr | None = None
    stderr: StrictStr | None = None
    truncated: bool | None = None
    truncationReason: StrictStr | None = None
    termination: StrictStr | None = None
    durationMs: StrictInt | None = Field(default=None, ge=0)
    attemptCount: StrictInt | None = Field(default=None, ge=0, le=2)
    sandboxed: bool | None = None
    sandboxPermissions: SandboxPermissions | None = None
    escalated: bool | None = None
    profileHash: StrictStr | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    sandboxDenialCategory: Literal[
        "filesystem_read",
        "filesystem_write",
        "network",
        "execution",
        "process",
        "unknown",
    ] | None = None
    effectivePermissionsSummary: dict[str, object] | None = None


class SkillReadResultData(StrictToolModel):
    SUCCESS_REQUIRED: ClassVar[tuple[str, ...]] = (
        "qualifiedId", "content", "contentHash", "pluginId",
        "pluginVersion", "pluginHash",
    )
    qualifiedId: StrictStr | None = None
    content: StrictStr | None = None
    contentHash: StrictStr | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )
    pluginId: StrictStr | None = None
    pluginVersion: StrictStr | None = None
    pluginHash: StrictStr | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )


class SkillResourceResultData(SkillReadResultData):
    SUCCESS_REQUIRED: ClassVar[tuple[str, ...]] = (
        "qualifiedId", "resourcePath", "content", "contentHash", "pluginId",
    )
    resourcePath: StrictStr | None = None


class SkillChangeResultData(StrictToolModel):
    SUCCESS_REQUIRED: ClassVar[tuple[str, ...]] = (
        "path", "qualifiedId", "contentHash",
    )
    path: StrictStr | None = None
    qualifiedId: StrictStr | None = None
    contentHash: StrictStr | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$"
    )


class ToolSearchResultData(StrictToolModel):
    SUCCESS_REQUIRED: ClassVar[tuple[str, ...]] = (
        "hits", "totalMatches", "truncated",
    )
    hits: tuple[ToolSearchHit, ...] | None = None
    totalMatches: StrictInt | None = Field(default=None, ge=0)
    truncated: bool | None = None
    truncationReason: StrictStr | None = None


class McpResultData(StrictToolModel):
    SUCCESS_REQUIRED: ClassVar[tuple[str, ...]] = ("isError",)
    text: StrictStr | None = None
    structuredContent: dict[str, object] | None = None
    isError: bool | None = None


class CanonicalToolResultBase(StrictToolModel):
    schemaVersion: Literal[1] = 1
    toolContractVersion: Literal[1] = 1
    toolName: StrictStr
    outcome: Literal[
        "success", "error", "skipped", "rejected", "interrupted",
        "unavailable", "declined",
    ]
    code: StrictStr
    summary: StrictStr
    sideEffectsMayExist: bool = False
    reconciliationRequired: bool = False

    @model_validator(mode="after")
    def validate_json_numbers(self):
        _validate_json_numbers(self.model_dump(mode="python"))
        if self.reconciliationRequired and not self.sideEffectsMayExist:
            raise ValueError("reconciliation_requires_side_effects")
        return self


_RESULT_MODELS: dict[type[BaseModel], type[BaseModel]] = {}


def result_model(data_model: type[BaseModel]) -> type[BaseModel]:
    model = _RESULT_MODELS.get(data_model)
    if model is None:
        required_fields: dict[str, tuple[object, object]] = {}
        for name in data_model.SUCCESS_REQUIRED:
            field = data_model.model_fields[name]
            required_fields[name] = (_without_none(field.annotation), ...)
        success_data_model = create_model(
            f"{data_model.__name__.removesuffix('Data')}SuccessData",
            __base__=data_model,
            **required_fields,
        )
        success_envelope = create_model(
            f"{data_model.__name__.removesuffix('Data')}SuccessResult",
            __base__=CanonicalToolResultBase,
            outcome=(Literal["success"], "success"),
            data=(success_data_model, ...),
            sideEffectsMayExist=(bool, False),
            reconciliationRequired=(
                bool if getattr(data_model, "ALLOW_SUCCESS_RECONCILIATION", False)
                else Literal[False],
                False,
            ),
        )
        failure_envelope = create_model(
            f"{data_model.__name__.removesuffix('Data')}FailureResult",
            __base__=CanonicalToolResultBase,
            outcome=(
                Literal[
                    "error", "skipped", "rejected", "interrupted",
                    "unavailable", "declined",
                ],
                ...,
            ),
            data=(data_model, ...),
        )
        result_union = Annotated[
            Union[success_envelope, failure_envelope],
            Field(discriminator="outcome"),
        ]
        model = type(
            f"{data_model.__name__.removesuffix('Data')}Result",
            (RootModel[result_union],),
            {"__module__": __name__},
        )
        _RESULT_MODELS[data_model] = model
    return model


def _without_none(annotation: object) -> object:
    origin = get_origin(annotation)
    if origin in {Union, UnionType}:
        members = tuple(member for member in get_args(annotation) if member is not type(None))
        if len(members) == 1:
            return members[0]
        return Union[members]  # type: ignore[index]
    return annotation


class ToolUiResult(StrictToolModel):
    schemaVersion: Literal[1] = 1
    toolName: StrictStr
    outcome: StrictStr
    code: StrictStr
    summary: StrictStr
    data: dict[str, object]
    sideEffectsMayExist: bool
    reconciliationRequired: bool


class ToolResultProjection(StrictToolModel):
    canonical_result: dict[str, object]
    model_result: dict[str, object]
    ui_result: dict[str, object]
    log_preview: StrictStr
    progress_fingerprint: StrictStr


class ToolResultProjector(Protocol):
    policy_id: str
    policy_version: int

    def project(
        self,
        descriptor: object,
        canonical_result: dict[str, object],
    ) -> ToolResultProjection: ...


class VersionedToolResultProjector:
    def __init__(
        self,
        policy_id: str,
        *,
        set_fields: frozenset[str] = frozenset(),
    ) -> None:
        self.policy_id = policy_id
        self.policy_version = 1
        self.set_fields = set_fields

    def project(
        self,
        descriptor: object,
        canonical_result: dict[str, object],
    ) -> ToolResultProjection:
        spec = getattr(descriptor, "spec", None)
        tool_name = getattr(spec, "name", None) or canonical_result.get("toolName")
        return _project_tool_result(
            str(tool_name), canonical_result, set_fields=set(self.set_fields)
        )


LIST_FILES_PROJECTOR = VersionedToolResultProjector(
    "list_files", set_fields=frozenset({"paths"})
)
READ_FILE_PROJECTOR = VersionedToolResultProjector("read_file")
READ_FILE_RANGE_PROJECTOR = VersionedToolResultProjector("read_file_range")
SEARCH_TEXT_PROJECTOR = VersionedToolResultProjector("search_text")
FILE_CHANGE_PROJECTOR = VersionedToolResultProjector(
    "file_change", set_fields=frozenset({"created", "modified", "deleted"})
)
SHELL_PROJECTOR = VersionedToolResultProjector(
    "run_shell", set_fields=frozenset({"created", "modified", "deleted"})
)
SKILL_READ_PROJECTOR = VersionedToolResultProjector("skill_read")
SKILL_RESOURCE_PROJECTOR = VersionedToolResultProjector("skill_resource")
SKILL_CHANGE_PROJECTOR = VersionedToolResultProjector("skill_change")
TOOL_SEARCH_PROJECTOR = VersionedToolResultProjector("tool_search")
MCP_PROJECTOR = VersionedToolResultProjector("mcp")
GENERIC_PROJECTOR = VersionedToolResultProjector("generic")

PROJECTORS: dict[str, VersionedToolResultProjector] = {
    projector.policy_id: projector
    for projector in (
        LIST_FILES_PROJECTOR,
        READ_FILE_PROJECTOR,
        READ_FILE_RANGE_PROJECTOR,
        SEARCH_TEXT_PROJECTOR,
        FILE_CHANGE_PROJECTOR,
        SHELL_PROJECTOR,
        SKILL_READ_PROJECTOR,
        SKILL_RESOURCE_PROJECTOR,
        SKILL_CHANGE_PROJECTOR,
        TOOL_SEARCH_PROJECTOR,
        MCP_PROJECTOR,
        GENERIC_PROJECTOR,
    )
}


_MODEL_TOTAL_BYTES = 48 * 1024
_MODEL_STRING_BYTES = 16 * 1024
_MODEL_MAX_DEPTH = 8
_MODEL_MAX_NODES = 1_000
_MODEL_MAX_KEYS = 256
_MODEL_MAX_LIST_ITEMS = 100


def project_tool_result(
    tool_name: str, canonical_result: dict[str, object]
) -> ToolResultProjection:
    policy_id = {
        "list_files": "list_files",
        "read_file": "read_file",
        "read_file_range": "read_file_range",
        "search_text": "search_text",
        "write_file": "file_change",
        "apply_patch": "file_change",
        "delete_file": "file_change",
        "run_shell": "run_shell",
        "skill_read": "skill_read",
        "skill_read_resource": "skill_resource",
        "skill_create": "skill_change",
        "skill_install": "skill_change",
        "tool_search": "tool_search",
    }.get(tool_name, "mcp" if tool_name.startswith("mcp__") else "generic")
    projector = PROJECTORS[policy_id]
    return _project_tool_result(
        tool_name, canonical_result, set_fields=set(projector.set_fields)
    )


def _project_tool_result(
    tool_name: str,
    canonical_result: dict[str, object],
    *,
    set_fields: set[str],
) -> ToolResultProjection:
    data = canonical_result.get("data")
    safe_data = dict(data) if isinstance(data, dict) else {}
    projected: dict[str, object] = {}
    truncated = False
    budget = _ProjectionBudget()
    for key in sorted(safe_data):
        if budget.keys >= _MODEL_MAX_KEYS:
            truncated = True
            break
        budget.keys += 1
        value = safe_data[key]
        if key in {"durationMs"}:
            continue
        value, value_truncated = _bounded_projection_value(
            value, budget, depth=1
        )
        truncated = truncated or value_truncated
        projected[key] = value
    if truncated:
        projected["truncated"] = True
        projected.setdefault(
            "continuation",
            _continuation(tool_name, projected),
        )
    model_result: dict[str, object] = {
        "toolName": tool_name,
        "outcome": canonical_result.get("outcome"),
        "code": canonical_result.get("code"),
        "summary": _bounded_string(str(canonical_result.get("summary", ""))),
        "data": projected,
        "sideEffectsMayExist": canonical_result.get("sideEffectsMayExist", False),
        "reconciliationRequired": canonical_result.get(
            "reconciliationRequired", False
        ),
    }
    model_result = _fit_serialized_budget(model_result)
    fingerprint = hashlib.sha256(
        _canonical_json(
            _semantic(canonical_result, set_fields=set_fields)
        ).encode("utf-8")
    ).hexdigest()
    return ToolResultProjection(
        canonical_result=canonical_result,
        model_result=model_result,
        ui_result=ToolUiResult.model_validate({
            "schemaVersion": 1,
            "toolName": tool_name,
            "outcome": str(canonical_result.get("outcome", "error")),
            "code": str(canonical_result.get("code", "unknown")),
            "summary": str(canonical_result.get("summary", "")),
            "data": safe_data,
            "sideEffectsMayExist": canonical_result.get(
                "sideEffectsMayExist", False
            ),
            "reconciliationRequired": canonical_result.get(
                "reconciliationRequired", False
            ),
        }).model_dump(mode="json"),
        log_preview=(
            f"{tool_name}: {canonical_result.get('outcome', 'error')}/"
            f"{canonical_result.get('code', 'unknown')}"
        )[:256],
        progress_fingerprint=fingerprint,
    )


def _continuation(tool_name: str, data: dict[str, object]) -> str:
    if tool_name == "read_file":
        return "Use read_file_range to continue with a narrower line range."
    if tool_name == "read_file_range" and data.get("nextLine") is not None:
        return f"Continue at startLine={data['nextLine']}."
    if tool_name in {"list_files", "search_text"}:
        return "Refine the path or query to retrieve a smaller result."
    return "Run a narrower command or request to continue."


class _ProjectionBudget:
    def __init__(self) -> None:
        self.nodes = 0
        self.keys = 0
        self.list_items = 0

    def node(self) -> bool:
        self.nodes += 1
        return self.nodes <= _MODEL_MAX_NODES


def _bounded_string(value: str) -> str:
    encoded = value.encode("utf-8")
    return encoded[:_MODEL_STRING_BYTES].decode("utf-8", errors="ignore")


def _bounded_projection_value(
    value: object, budget: _ProjectionBudget, *, depth: int
) -> tuple[object, bool]:
    if not budget.node() or depth > _MODEL_MAX_DEPTH:
        return None, True
    if isinstance(value, str):
        encoded = value.encode("utf-8")
        accepted = encoded[:_MODEL_STRING_BYTES]
        return (
            accepted.decode("utf-8", errors="ignore"),
            len(accepted) < len(encoded),
        )
    if isinstance(value, dict):
        result: dict[str, object] = {}
        truncated = False
        for key in sorted(value):
            if budget.keys >= _MODEL_MAX_KEYS:
                truncated = True
                break
            budget.keys += 1
            safe_key = _bounded_string(str(key))
            if safe_key != key:
                truncated = True
            child, child_truncated = _bounded_projection_value(
                value[key], budget, depth=depth + 1
            )
            result[safe_key] = child
            truncated = truncated or child_truncated
        return result, truncated
    if isinstance(value, (list, tuple)):
        remaining_items = max(0, _MODEL_MAX_LIST_ITEMS - budget.list_items)
        selected = value[:remaining_items]
        budget.list_items += len(selected)
        result: list[object] = []
        truncated = len(selected) < len(value)
        for child in selected:
            bounded, child_truncated = _bounded_projection_value(
                child, budget, depth=depth + 1
            )
            result.append(bounded)
            truncated = truncated or child_truncated
        return result, truncated
    return value, False


def _fit_serialized_budget(value: dict[str, object]) -> dict[str, object]:
    if len(_canonical_json(value).encode("utf-8")) <= _MODEL_TOTAL_BYTES:
        return value
    bounded = dict(value)
    data = dict(bounded.get("data") if isinstance(bounded.get("data"), dict) else {})
    data["truncated"] = True
    data.setdefault("continuation", "Run a narrower request to continue.")
    for key in sorted(
        (key for key in data if key not in {"truncated", "continuation"}),
        reverse=True,
    ):
        if len(_canonical_json({**bounded, "data": data}).encode("utf-8")) <= _MODEL_TOTAL_BYTES:
            break
        data.pop(key)
    bounded["data"] = data
    if len(_canonical_json(bounded).encode("utf-8")) <= _MODEL_TOTAL_BYTES:
        return bounded
    return {
        "toolName": _bounded_string(str(value.get("toolName", ""))),
        "outcome": "error",
        "code": "TOOL_RESULT_PROJECTION_FAILED",
        "summary": "Tool result could not fit the model projection budget",
        "data": {"truncated": True},
        "sideEffectsMayExist": bool(value.get("sideEffectsMayExist", False)),
        "reconciliationRequired": bool(value.get("reconciliationRequired", False)),
    }


def _semantic(value: object, *, set_fields: set[str], field: str | None = None) -> object:
    if isinstance(value, dict):
        return {
            key: _semantic(child, set_fields=set_fields, field=key)
            for key, child in sorted(value.items())
            if key not in {"durationMs", "duration", "timestamp", "createdAt", "completedAt"}
        }
    if isinstance(value, (list, tuple)):
        normalized = [
            _semantic(child, set_fields=set_fields)
            for child in value
        ]
        return (
            sorted(normalized, key=_canonical_json)
            if field in set_fields
            else normalized
        )
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non_finite_json_number")
    return value


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )


def _validate_json_numbers(value: object) -> None:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return
    if isinstance(value, int):
        if abs(value) > 9_007_199_254_740_991:
            raise ValueError("json_integer_out_of_range")
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non_finite_json_number")
        return
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ValueError("json_key_invalid")
            _validate_json_numbers(child)
        return
    if isinstance(value, (list, tuple)):
        for child in value:
            _validate_json_numbers(child)
        return
    raise ValueError("json_value_invalid")

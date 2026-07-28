from __future__ import annotations

import hashlib
import json
import math
from typing import ClassVar, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    create_model,
    model_validator,
)


class StrictToolModel(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")


class ListFilesInput(StrictToolModel):
    pass


class ReadFileInput(StrictToolModel):
    path: StrictStr = Field(description="Workspace-relative UTF-8 file path.")


class ReadFileRangeInput(ReadFileInput):
    startLine: StrictInt = Field(ge=1, description="First line, one-based.")
    endLine: StrictInt = Field(ge=1, description="Last requested line, inclusive.")


class SearchTextInput(StrictToolModel):
    query: StrictStr = Field(
        min_length=1,
        max_length=512,
        description="Literal single-line text to find.",
    )


class WriteFileInput(ReadFileInput):
    content: StrictStr = Field(description="Complete replacement UTF-8 content.")


class ApplyPatchInput(ReadFileInput):
    patch: StrictStr = Field(description="Strict unified diff for exactly one file.")


class DeleteFileInput(ReadFileInput):
    pass


class RunShellInput(StrictToolModel):
    command: StrictStr = Field(min_length=1, max_length=16 * 1024)
    cwd: StrictStr = "."
    timeoutSeconds: StrictInt = Field(default=120, ge=1, le=600)


class SkillReadInput(StrictToolModel):
    qualifiedId: StrictStr = Field(min_length=1, max_length=129)


class SkillReadResourceInput(SkillReadInput):
    resourcePath: StrictStr = Field(min_length=1, max_length=512)


class SkillFileInput(StrictToolModel):
    path: StrictStr = Field(min_length=1, max_length=512)
    content: StrictStr = Field(max_length=256 * 1024)


class SkillCreateInput(StrictToolModel):
    name: StrictStr = Field(min_length=1, max_length=64)
    description: StrictStr = Field(min_length=1, max_length=1024)
    instructions: StrictStr = Field(min_length=1, max_length=128 * 1024)
    files: tuple[SkillFileInput, ...] = Field(default=(), max_length=64)


class SkillInstallInput(StrictToolModel):
    url: StrictStr = Field(min_length=1, max_length=2048)


class ToolSearchInput(StrictToolModel):
    query: StrictStr = Field(min_length=1, max_length=256)
    limit: StrictInt = Field(default=10, ge=1, le=16)


class SearchMatch(StrictToolModel):
    path: StrictStr
    line: StrictInt
    column: StrictInt
    preview: StrictStr


class ToolSearchHit(StrictToolModel):
    name: StrictStr
    description: StrictStr
    provenance: dict[str, object]
    score: StrictInt


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
    sizeBytes: StrictInt | None = None
    sha256: StrictStr | None = None
    truncated: bool | None = None
    truncationReason: StrictStr | None = None


class ReadFileRangeResultData(StrictToolModel):
    SUCCESS_REQUIRED: ClassVar[tuple[str, ...]] = (
        "path", "content", "sizeBytes", "sha256", "startLine", "endLine",
    )
    path: StrictStr | None = None
    content: StrictStr | None = None
    sizeBytes: StrictInt | None = None
    sha256: StrictStr | None = None
    startLine: StrictInt | None = None
    endLine: StrictInt | None = None
    nextLine: StrictInt | None = None


class SearchTextResultData(StrictToolModel):
    SUCCESS_REQUIRED: ClassVar[tuple[str, ...]] = (
        "matches", "scannedBytes", "truncated",
    )
    matches: tuple[SearchMatch, ...] | None = None
    scannedBytes: StrictInt | None = None
    truncated: bool | None = None
    truncationReason: StrictStr | None = None


class WorkspaceResultData(StrictToolModel):
    SUCCESS_REQUIRED: ClassVar[tuple[str, ...]] = ("path",)
    path: StrictStr | None = None
    sha256: StrictStr | None = None
    baseSha256: StrictStr | None = None
    sizeBytes: StrictInt | None = None
    commandOutcome: StrictStr | None = None
    workspaceChanged: bool | None = None
    workspaceDiffHash: StrictStr | None = None
    workspaceManifestComplete: bool | None = None
    workspaceManifestTruncated: bool | None = None
    workspaceDiffIncomplete: bool | None = None
    created: tuple[StrictStr, ...] | None = None
    modified: tuple[StrictStr, ...] | None = None
    deleted: tuple[StrictStr, ...] | None = None


class RunShellResultData(WorkspaceResultData):
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
    durationMs: StrictInt | None = None


class SkillReadResultData(StrictToolModel):
    SUCCESS_REQUIRED: ClassVar[tuple[str, ...]] = (
        "qualifiedId", "content", "contentHash", "pluginId",
        "pluginVersion", "pluginHash",
    )
    qualifiedId: StrictStr | None = None
    content: StrictStr | None = None
    contentHash: StrictStr | None = None
    pluginId: StrictStr | None = None
    pluginVersion: StrictStr | None = None
    pluginHash: StrictStr | None = None


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
    contentHash: StrictStr | None = None


class ToolSearchResultData(StrictToolModel):
    SUCCESS_REQUIRED: ClassVar[tuple[str, ...]] = (
        "hits", "totalMatches", "truncated",
    )
    hits: tuple[ToolSearchHit, ...] | None = None
    totalMatches: StrictInt | None = None
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
        data = getattr(self, "data", None)
        if self.outcome == "success" and isinstance(data, BaseModel):
            missing = [
                field for field in data.SUCCESS_REQUIRED
                if getattr(data, field) is None
            ]
            if missing:
                raise ValueError(
                    "tool_success_result_missing_fields:" + ",".join(missing)
                )
        return self


_RESULT_MODELS: dict[type[BaseModel], type[BaseModel]] = {}


def result_model(data_model: type[BaseModel]) -> type[BaseModel]:
    model = _RESULT_MODELS.get(data_model)
    if model is None:
        model = create_model(
            f"{data_model.__name__.removesuffix('Data')}Envelope",
            __base__=CanonicalToolResultBase,
            data=(data_model, ...),
        )
        _RESULT_MODELS[data_model] = model
    return model


class ToolResultProjection(StrictToolModel):
    canonical_result: dict[str, object]
    model_result: dict[str, object]
    ui_result: dict[str, object]
    log_preview: StrictStr
    progress_fingerprint: StrictStr


_MODEL_TEXT_BYTES = 48 * 1024
_MODEL_LIST_ITEMS = 100


def project_tool_result(
    tool_name: str, canonical_result: dict[str, object]
) -> ToolResultProjection:
    data = canonical_result.get("data")
    safe_data = dict(data) if isinstance(data, dict) else {}
    projected: dict[str, object] = {}
    truncated = False
    remaining = [
        128 * 1024 if tool_name.startswith("skill_") else _MODEL_TEXT_BYTES
    ]
    for key, value in safe_data.items():
        if key in {"durationMs"}:
            continue
        value_budget = (
            [_MODEL_TEXT_BYTES]
            if tool_name.startswith("mcp__")
            and key in {"text", "structuredContent"}
            else remaining
        )
        value, value_truncated = _bounded_projection_value(
            value, value_budget
        )
        truncated = truncated or value_truncated
        projected[key] = value
    if truncated:
        projected["truncated"] = True
        projected.setdefault(
            "continuation",
            _continuation(tool_name, projected),
        )
    model_result = {
        "toolName": tool_name,
        "outcome": canonical_result.get("outcome"),
        "code": canonical_result.get("code"),
        "summary": canonical_result.get("summary"),
        "data": projected,
        "sideEffectsMayExist": canonical_result.get("sideEffectsMayExist", False),
        "reconciliationRequired": canonical_result.get(
            "reconciliationRequired", False
        ),
    }
    fingerprint = hashlib.sha256(
        _canonical_json(_semantic(canonical_result)).encode("utf-8")
    ).hexdigest()
    return ToolResultProjection(
        canonical_result=canonical_result,
        model_result=model_result,
        ui_result={
            "toolName": tool_name,
            "outcome": canonical_result.get("outcome"),
            "code": canonical_result.get("code"),
            "summary": canonical_result.get("summary"),
            "data": safe_data,
            "sideEffectsMayExist": canonical_result.get(
                "sideEffectsMayExist", False
            ),
            "reconciliationRequired": canonical_result.get(
                "reconciliationRequired", False
            ),
        },
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


def _bounded_projection_value(
    value: object, remaining: list[int]
) -> tuple[object, bool]:
    if isinstance(value, str):
        encoded = value.encode("utf-8")
        accepted = encoded[:max(0, remaining[0])]
        remaining[0] -= len(accepted)
        return (
            accepted.decode("utf-8", errors="ignore"),
            len(accepted) < len(encoded),
        )
    if isinstance(value, dict):
        result: dict[str, object] = {}
        truncated = False
        for key in sorted(value):
            child, child_truncated = _bounded_projection_value(
                value[key], remaining
            )
            result[key] = child
            truncated = truncated or child_truncated
        return result, truncated
    if isinstance(value, (list, tuple)):
        selected = value[:_MODEL_LIST_ITEMS]
        result: list[object] = []
        truncated = len(selected) < len(value)
        for child in selected:
            bounded, child_truncated = _bounded_projection_value(
                child, remaining
            )
            result.append(bounded)
            truncated = truncated or child_truncated
        return result, truncated
    return value, False


def _semantic(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: _semantic(child)
            for key, child in sorted(value.items())
            if key not in {"durationMs", "duration", "timestamp", "createdAt", "completedAt"}
        }
    if isinstance(value, (list, tuple)):
        normalized = [_semantic(child) for child in value]
        return sorted(normalized, key=_canonical_json)
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

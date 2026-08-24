"""Strict request and result DTOs for public JSON-RPC business methods.

The protocol registry deliberately knows these DTOs instead of accepting one
object-shaped catch-all model.  Result DTOs remain wire-shaped at this boundary
because several established desktop responses have nested compatibility
records; each public method nevertheless owns a distinct validation type.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import ClassVar, Literal

from pydantic import Field, JsonValue, StrictInt, StrictStr
from pydantic import field_validator, model_validator

from eidos_runtime.protocol.schemas import (
    ClosedModel,
    ContextUsageSnapshotDto,
    EventEnvelopeDto,
    ItemDto,
    McpServerRecordDto,
    PluginRecordDto,
    ProjectDto,
    RunDto,
    SessionDto,
    SessionWorktreeDto,
    WorktreeSettingsDto,
    SkillMetadataDto,
    StepResolutionReviewDto,
)


class MethodRequestDto(ClosedModel):
    """Base type for a method-specific request DTO."""


class MethodResultDto(ClosedModel):
    """Closed base for concrete method-specific response objects."""

    @property
    def root(self) -> dict[str, JsonValue]:
        """Compatibility view for application tests during the DTO migration."""

        return self.to_json_value()


class _CanonicalIdRequest(MethodRequestDto):
    _canonical_id_fields: ClassVar[tuple[str, ...]] = ()

    @model_validator(mode="after")
    def _validate_canonical_ids(self) -> "_CanonicalIdRequest":
        for field_name in self._canonical_id_fields:
            value = getattr(self, field_name)
            if value is None:
                continue
            try:
                if str(uuid.UUID(value)) != value:
                    raise ValueError
            except ValueError as error:
                raise ValueError(f"{field_name} must be a canonical UUID") from error
        return self


class _OperationRequest(_CanonicalIdRequest):
    operation_id: StrictStr | None = Field(default=None, alias="operationId")
    _canonical_id_fields: ClassVar[tuple[str, ...]] = ("operation_id",)


def _include_detached_worktree_branch(
    value: dict[str, JsonValue], worktree: SessionWorktreeDto | None
) -> None:
    if worktree is None or worktree.branch is not None:
        return
    worktree_value = value.get("worktree")
    if isinstance(worktree_value, dict):
        worktree_value["branch"] = None


class SessionCreateRequestDto(_OperationRequest):
    workspace_root: StrictStr | None = Field(
        default=None, alias="workspaceRoot", min_length=1, max_length=4096
    )
    execution_mode: Literal["local", "worktree"] = Field(
        default="local", alias="executionMode"
    )
    base_ref: StrictStr | None = Field(
        default=None, alias="baseRef", min_length=1, max_length=4096
    )
    include_local_changes: bool = Field(
        default=False, alias="includeLocalChanges"
    )


class SessionCreateBranchRequestDto(_OperationRequest):
    session_id: StrictStr = Field(alias="sessionId")
    branch: StrictStr = Field(min_length=1, max_length=4096)
    _canonical_id_fields: ClassVar[tuple[str, ...]] = (
        "operation_id",
        "session_id",
    )


class _SessionGitBranchRequest(_OperationRequest):
    operation_id: StrictStr = Field(alias="operationId")
    session_id: StrictStr = Field(alias="sessionId")
    branch: StrictStr = Field(min_length=1, max_length=4096)
    _canonical_id_fields: ClassVar[tuple[str, ...]] = (
        "operation_id",
        "session_id",
    )


class SessionGitSwitchBranchRequestDto(_SessionGitBranchRequest):
    pass


class SessionGitCreateBranchRequestDto(_SessionGitBranchRequest):
    pass


class GitContextRequestDto(MethodRequestDto):
    workspace_root: StrictStr = Field(
        alias="workspaceRoot", min_length=1, max_length=4096
    )


class ProjectListRequestDto(MethodRequestDto):
    pass


class ProjectCreateRequestDto(_OperationRequest):
    name: StrictStr | None = Field(default=None, max_length=120)
    workspace_root: StrictStr = Field(
        alias="workspaceRoot", min_length=1, max_length=4096
    )

    @field_validator("name")
    @classmethod
    def _normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class ProjectDeleteRequestDto(_OperationRequest):
    project_id: StrictStr = Field(alias="projectId", min_length=1, max_length=256)


class SessionListRequestDto(MethodRequestDto):
    limit: StrictInt = Field(default=50, ge=1, le=200)
    cursor: StrictStr | None = None


class SessionReadRequestDto(_CanonicalIdRequest):
    session_id: StrictStr = Field(alias="sessionId")
    item_limit: StrictInt = Field(default=200, alias="itemLimit", ge=1, le=500)
    before_item_id: StrictStr | None = Field(default=None, alias="beforeItemId")
    _canonical_id_fields: ClassVar[tuple[str, ...]] = ("session_id", "before_item_id")


class SessionGitStatusRequestDto(_CanonicalIdRequest):
    session_id: StrictStr = Field(alias="sessionId")
    _canonical_id_fields: ClassVar[tuple[str, ...]] = ("session_id",)


class SessionGitDiffRequestDto(_CanonicalIdRequest):
    session_id: StrictStr = Field(alias="sessionId")
    scope: Literal["head", "baseline"] = "head"
    compare_ref: StrictStr | None = Field(
        default=None, alias="compareRef", min_length=1, max_length=4096
    )
    path: StrictStr | None = Field(default=None, min_length=1, max_length=4096)
    _canonical_id_fields: ClassVar[tuple[str, ...]] = ("session_id",)

    @field_validator("path")
    @classmethod
    def _validate_path(cls, value: str | None) -> str | None:
        return None if value is None else _git_relative_path(value)


class WorkspaceListDirectoryRequestDto(_CanonicalIdRequest):
    session_id: StrictStr = Field(alias="sessionId")
    path: StrictStr = Field(default=".", min_length=1, max_length=4096)
    limit: StrictInt = Field(default=500, ge=1, le=2_000)
    _canonical_id_fields: ClassVar[tuple[str, ...]] = ("session_id",)


class WorkspaceReadFilePreviewRequestDto(_CanonicalIdRequest):
    session_id: StrictStr = Field(alias="sessionId")
    path: StrictStr = Field(min_length=1, max_length=4096)
    _canonical_id_fields: ClassVar[tuple[str, ...]] = ("session_id",)


class _SessionGitPathsRequest(_OperationRequest):
    session_id: StrictStr = Field(alias="sessionId")
    paths: list[StrictStr] = Field(min_length=1, max_length=512)
    _canonical_id_fields: ClassVar[tuple[str, ...]] = (
        "operation_id",
        "session_id",
    )

    @field_validator("paths")
    @classmethod
    def _validate_paths(cls, values: list[str]) -> list[str]:
        normalized = [_git_relative_path(value) for value in values]
        if len(set(normalized)) != len(normalized):
            raise ValueError("Git paths must be unique")
        return normalized


class SessionGitStageRequestDto(_SessionGitPathsRequest):
    pass


class SessionGitUnstageRequestDto(_SessionGitPathsRequest):
    pass


class SessionGitDiscardRequestDto(_OperationRequest):
    operation_id: StrictStr = Field(alias="operationId")
    session_id: StrictStr = Field(alias="sessionId")
    path: StrictStr = Field(min_length=1, max_length=4096)
    _canonical_id_fields: ClassVar[tuple[str, ...]] = (
        "operation_id",
        "session_id",
    )

    @field_validator("path")
    @classmethod
    def _validate_path(cls, value: str) -> str:
        return _git_relative_path(value)


class SessionGitCommitRequestDto(_OperationRequest):
    session_id: StrictStr = Field(alias="sessionId")
    message: StrictStr = Field(min_length=1, max_length=65536)
    _canonical_id_fields: ClassVar[tuple[str, ...]] = (
        "operation_id",
        "session_id",
    )

    @field_validator("message")
    @classmethod
    def _validate_message(cls, value: str) -> str:
        if not value.strip() or "\x00" in value:
            raise ValueError("Git commit message is invalid")
        return value


class SessionGitRemoteStatusRequestDto(_CanonicalIdRequest):
    session_id: StrictStr = Field(alias="sessionId")
    _canonical_id_fields: ClassVar[tuple[str, ...]] = ("session_id",)


class SessionGitFetchRequestDto(_OperationRequest):
    operation_id: StrictStr = Field(alias="operationId")
    session_id: StrictStr = Field(alias="sessionId")
    remote: StrictStr | None = Field(default=None, min_length=1, max_length=255)
    _canonical_id_fields: ClassVar[tuple[str, ...]] = (
        "operation_id",
        "session_id",
    )


class SessionGitPullRequestDto(_OperationRequest):
    operation_id: StrictStr = Field(alias="operationId")
    session_id: StrictStr = Field(alias="sessionId")
    _canonical_id_fields: ClassVar[tuple[str, ...]] = (
        "operation_id",
        "session_id",
    )


class SessionGitPushRequestDto(_OperationRequest):
    operation_id: StrictStr = Field(alias="operationId")
    session_id: StrictStr = Field(alias="sessionId")
    remote: StrictStr | None = Field(default=None, min_length=1, max_length=255)
    _canonical_id_fields: ClassVar[tuple[str, ...]] = (
        "operation_id",
        "session_id",
    )


class SessionGitMergeRequestDto(_OperationRequest):
    operation_id: StrictStr = Field(alias="operationId")
    session_id: StrictStr = Field(alias="sessionId")
    target: StrictStr = Field(min_length=1, max_length=1024)
    _canonical_id_fields: ClassVar[tuple[str, ...]] = (
        "operation_id",
        "session_id",
    )

    @field_validator("target")
    @classmethod
    def _validate_target(cls, value: str) -> str:
        if "\x00" in value or value.startswith("-"):
            raise ValueError("Git merge target is invalid")
        return value


class SessionGitMergeAbortRequestDto(_OperationRequest):
    operation_id: StrictStr = Field(alias="operationId")
    session_id: StrictStr = Field(alias="sessionId")
    _canonical_id_fields: ClassVar[tuple[str, ...]] = (
        "operation_id",
        "session_id",
    )


class SessionGitRebaseRequestDto(SessionGitMergeRequestDto):
    pass


class SessionGitRebaseContinueRequestDto(SessionGitMergeAbortRequestDto):
    pass


class SessionGitRebaseAbortRequestDto(SessionGitMergeAbortRequestDto):
    pass


def _git_relative_path(value: str) -> str:
    path = Path(value)
    if (
        not value
        or "\x00" in value
        or path.is_absolute()
        or any(part in {"", ".", "..", ".git"} for part in path.parts)
    ):
        raise ValueError("Git path must be workspace-relative")
    return value


class SessionRenameRequestDto(_OperationRequest):
    session_id: StrictStr = Field(alias="sessionId")
    title: StrictStr
    _canonical_id_fields: ClassVar[tuple[str, ...]] = ("operation_id", "session_id")


class SessionDeleteRequestDto(_OperationRequest):
    session_id: StrictStr = Field(alias="sessionId")
    _canonical_id_fields: ClassVar[tuple[str, ...]] = ("operation_id", "session_id")


class SessionHandoffRequestDto(_OperationRequest):
    session_id: StrictStr = Field(alias="sessionId")
    target: Literal["local", "worktree"]
    _canonical_id_fields: ClassVar[tuple[str, ...]] = (
        "operation_id",
        "session_id",
    )


class SessionRestoreWorktreeRequestDto(_OperationRequest):
    session_id: StrictStr = Field(alias="sessionId")
    _canonical_id_fields: ClassVar[tuple[str, ...]] = (
        "operation_id",
        "session_id",
    )


class SettingsReadRequestDto(MethodRequestDto):
    pass


class SettingsUpdateRequestDto(MethodRequestDto):
    automatic_cleanup: bool = Field(alias="automaticCleanup")
    managed_worktree_limit: StrictInt = Field(
        alias="managedWorktreeLimit", ge=1, le=100
    )


class EventListRequestDto(_CanonicalIdRequest):
    session_id: StrictStr = Field(alias="sessionId")
    after_event_id: StrictInt = Field(default=0, alias="afterEventId", ge=0)
    limit: StrictInt = Field(default=200, ge=1, le=500)
    _canonical_id_fields: ClassVar[tuple[str, ...]] = ("session_id",)


class RunStartRequestDto(_OperationRequest):
    session_id: StrictStr = Field(alias="sessionId")
    user_input: StrictStr = Field(alias="userInput", min_length=1, max_length=64 * 1024)
    model_id: StrictStr = Field(alias="modelId", min_length=1, max_length=256)
    _canonical_id_fields: ClassVar[tuple[str, ...]] = ("operation_id", "session_id")

    @model_validator(mode="after")
    def _validate_model_selector(self) -> "RunStartRequestDto":
        super()._validate_canonical_ids()
        if not self.user_input.strip():
            raise ValueError("userInput must not be blank")
        return self


class RunCancelRequestDto(_OperationRequest):
    run_id: StrictStr = Field(alias="runId")
    _canonical_id_fields: ClassVar[tuple[str, ...]] = ("operation_id", "run_id")


class RunPauseRequestDto(_OperationRequest):
    run_id: StrictStr = Field(alias="runId")
    _canonical_id_fields: ClassVar[tuple[str, ...]] = ("operation_id", "run_id")


class RunResumeRequestDto(_OperationRequest):
    run_id: StrictStr = Field(alias="runId")
    _canonical_id_fields: ClassVar[tuple[str, ...]] = ("operation_id", "run_id")


class RunStatusRequestDto(_CanonicalIdRequest):
    run_id: StrictStr = Field(alias="runId")
    _canonical_id_fields: ClassVar[tuple[str, ...]] = ("run_id",)


class ContextUsageRequestDto(_CanonicalIdRequest):
    run_id: StrictStr = Field(alias="runId")
    _canonical_id_fields: ClassVar[tuple[str, ...]] = ("run_id",)


class ModelPresetsRequestDto(MethodRequestDto):
    pass


class ModelListRequestDto(MethodRequestDto):
    pass


class ModelCreateRequestDto(MethodRequestDto):
    provider: StrictStr = Field(min_length=1, max_length=64)
    model_id: StrictStr = Field(alias="modelId", min_length=1, max_length=256)
    api_key: StrictStr = Field(alias="apiKey", min_length=1, max_length=512)


class ModelUpdateRequestDto(MethodRequestDto):
    id: StrictStr = Field(min_length=1, max_length=256)
    provider: StrictStr = Field(min_length=1, max_length=64)
    model_id: StrictStr = Field(alias="modelId", min_length=1, max_length=256)
    api_key: StrictStr | None = Field(default=None, alias="apiKey", max_length=512)


class ModelDeleteRequestDto(MethodRequestDto):
    id: StrictStr = Field(min_length=1, max_length=256)


class PluginListRequestDto(MethodRequestDto):
    pass


class PluginImportRequestDto(_OperationRequest):
    source_path: StrictStr = Field(alias="sourcePath", min_length=1, max_length=4096)

    @field_validator("source_path")
    @classmethod
    def _validate_absolute_path(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("sourcePath must be absolute")
        return value


class PluginSetEnabledRequestDto(_OperationRequest):
    plugin_id: StrictStr = Field(alias="pluginId", min_length=1)
    enabled: bool


class PluginRemoveRequestDto(_OperationRequest):
    plugin_id: StrictStr = Field(alias="pluginId", min_length=1)


class SkillListRequestDto(MethodRequestDto):
    pass


class SkillReadRequestDto(MethodRequestDto):
    qualified_id: StrictStr = Field(alias="qualifiedId", min_length=1)


class McpListRequestDto(MethodRequestDto):
    pass


class McpSetEnabledRequestDto(_OperationRequest):
    plugin_id: StrictStr = Field(alias="pluginId", min_length=1)
    server_id: StrictStr = Field(alias="serverId", min_length=1)
    enabled: bool
    consent: bool

    @field_validator("consent")
    @classmethod
    def _require_consent(cls, value: bool) -> bool:
        if value is not True:
            raise ValueError("consent must be true")
        return value


class ExtensionReadRequestDto(MethodRequestDto):
    pass


class ExtensionReadEventsRequestDto(MethodRequestDto):
    after_event_id: StrictInt = Field(default=0, alias="afterEventId", ge=0)
    limit: StrictInt = Field(default=200, ge=1, le=500)


class CheckpointCreateRequestDto(_OperationRequest):
    run_id: StrictStr = Field(alias="runId")
    _canonical_id_fields: ClassVar[tuple[str, ...]] = ("operation_id", "run_id")


class CheckpointListRequestDto(_CanonicalIdRequest):
    run_id: StrictStr = Field(alias="runId")
    _canonical_id_fields: ClassVar[tuple[str, ...]] = ("run_id",)


class CheckpointRewindRequestDto(_OperationRequest):
    checkpoint_id: StrictStr = Field(alias="checkpointId")
    _canonical_id_fields: ClassVar[tuple[str, ...]] = ("operation_id", "checkpoint_id")


class CheckpointForkRequestDto(_OperationRequest):
    checkpoint_id: StrictStr = Field(alias="checkpointId")
    workspace_root: StrictStr | None = Field(
        default=None, alias="workspaceRoot", min_length=1, max_length=4096
    )
    _canonical_id_fields: ClassVar[tuple[str, ...]] = ("operation_id", "checkpoint_id")


class _SessionResponseDto(MethodResultDto, SessionDto):
    def to_json_value(self) -> dict[str, JsonValue]:
        value = super().to_json_value()
        _include_detached_worktree_branch(value, self.worktree)
        if self.execution_mode is not None:
            value["executionMode"] = self.execution_mode
        return value


class SessionCreateResponseDto(_SessionResponseDto):
    pass


class SessionCreateBranchResponseDto(MethodResultDto):
    session_id: StrictStr = Field(alias="sessionId")
    worktree_id: StrictStr = Field(alias="worktreeId")
    branch: StrictStr
    head: StrictStr


class SessionListResponseDto(MethodResultDto):
    items: list[SessionDto]
    next_cursor: StrictStr | None = Field(default=None, alias="nextCursor")

    def to_json_value(self) -> dict[str, JsonValue]:
        value = super().to_json_value()
        for item, item_value in zip(self.items, value.get("items", []), strict=True):
            if isinstance(item_value, dict):
                _include_detached_worktree_branch(item_value, item.worktree)
                if item.execution_mode is not None:
                    item_value["executionMode"] = item.execution_mode
        return value


class ProjectListResponseDto(MethodResultDto):
    items: list[ProjectDto]


class ProjectCreateResponseDto(MethodResultDto, ProjectDto):
    pass


class ProjectDeleteResponseDto(MethodResultDto):
    deleted_project_id: StrictStr = Field(alias="deletedProjectId")


class SessionReadResponseDto(MethodResultDto):
    session: SessionDto
    runs: list[RunDto]
    items: list[ItemDto]
    step_resolutions: list[StepResolutionReviewDto] = Field(alias="stepResolutions")
    previous_item_id: StrictStr | None = Field(default=None, alias="previousItemId")
    through_event_id: StrictInt | None = Field(default=None, alias="throughEventId")

    def to_json_value(self) -> dict[str, JsonValue]:
        value = super().to_json_value()
        session_value = value.get("session")
        if isinstance(session_value, dict):
            _include_detached_worktree_branch(session_value, self.session.worktree)
            if self.session.execution_mode is not None:
                session_value["executionMode"] = self.session.execution_mode
        return value


class SessionGitStatusResponseDto(MethodResultDto):
    worktree_id: StrictStr | None = Field(default=None, alias="worktreeId")
    branch: StrictStr | None = None
    head: StrictStr
    base_ref: StrictStr | None = Field(default=None, alias="baseRef")
    base_commit: StrictStr | None = Field(default=None, alias="baseCommit")
    dirty: bool
    staged_count: StrictInt = Field(alias="stagedCount", ge=0)
    unstaged_count: StrictInt = Field(alias="unstagedCount", ge=0)
    untracked_count: StrictInt = Field(alias="untrackedCount", ge=0)
    conflict_count: StrictInt = Field(alias="conflictCount", ge=0)
    staged_files: list[StrictStr] = Field(alias="stagedFiles")
    unstaged_files: list[StrictStr] = Field(alias="unstagedFiles")
    untracked_files: list[StrictStr] = Field(alias="untrackedFiles")
    conflict_files: list[StrictStr] = Field(alias="conflictFiles")
    observed_at: StrictInt = Field(alias="observedAt", ge=0)

    def to_json_value(self) -> dict[str, JsonValue]:
        value = super().to_json_value()
        value["worktreeId"] = self.worktree_id
        value["branch"] = self.branch
        value["baseRef"] = self.base_ref
        value["baseCommit"] = self.base_commit
        return value


class GitContextResponseDto(MethodResultDto):
    git_available: bool = Field(alias="gitAvailable")
    current_branch: StrictStr | None = Field(default=None, alias="currentBranch")
    head: StrictStr | None = None
    branches: list[StrictStr]
    dirty: bool = False
    changed_file_count: StrictInt = Field(default=0, alias="changedFileCount", ge=0)

    def to_json_value(self) -> dict[str, JsonValue]:
        value = super().to_json_value()
        value["currentBranch"] = self.current_branch
        value["head"] = self.head
        value["dirty"] = self.dirty
        value["changedFileCount"] = self.changed_file_count
        return value


class SessionGitFileStatDto(ClosedModel):
    path: StrictStr
    additions: StrictInt = Field(ge=0)
    deletions: StrictInt = Field(ge=0)
    stats_incomplete: bool = Field(alias="statsIncomplete")


class SessionGitDiffResponseDto(MethodResultDto):
    scope: Literal["head", "baseline"]
    compare_ref: StrictStr | None = Field(default=None, alias="compareRef")
    base_commit: StrictStr | None = Field(default=None, alias="baseCommit")
    head: StrictStr
    dirty: bool
    changed_files: list[StrictStr] = Field(alias="changedFiles")
    unified_diff: StrictStr = Field(alias="unifiedDiff")
    diff_hash: StrictStr = Field(alias="diffHash", min_length=64, max_length=64)
    truncated: bool
    additions: StrictInt = Field(ge=0)
    deletions: StrictInt = Field(ge=0)
    stats_incomplete: bool = Field(alias="statsIncomplete")
    file_stats: list[SessionGitFileStatDto] = Field(alias="fileStats")
    observed_at: StrictInt = Field(alias="observedAt", ge=0)

    def to_json_value(self) -> dict[str, JsonValue]:
        value = super().to_json_value()
        value["compareRef"] = self.compare_ref
        value["baseCommit"] = self.base_commit
        return value


class WorkspaceDirectoryEntryDto(ClosedModel):
    name: StrictStr
    relative_path: StrictStr = Field(alias="relativePath")
    kind: Literal["file", "directory"]
    size_bytes: StrictInt | None = Field(default=None, alias="sizeBytes", ge=0)


class WorkspaceListDirectoryResponseDto(MethodResultDto):
    path: StrictStr
    entries: list[WorkspaceDirectoryEntryDto]
    truncated: bool

    def to_json_value(self) -> dict[str, JsonValue]:
        return self.model_dump(mode="json", by_alias=True, exclude_none=True)


class WorkspaceReadFilePreviewResponseDto(MethodResultDto):
    path: StrictStr
    kind: Literal["text", "markdown", "code", "unavailable"]
    size_bytes: StrictInt = Field(alias="sizeBytes", ge=0)
    truncated: bool
    content: StrictStr | None = None
    language: StrictStr | None = None
    reason: Literal["binary", "unsupported"] | None = None

    def to_json_value(self) -> dict[str, JsonValue]:
        return self.model_dump(
            mode="json", by_alias=True, exclude_none=True
        )


class SessionGitMutationResponseDto(MethodResultDto):
    head: StrictStr
    branch: StrictStr | None = None
    status: SessionGitStatusResponseDto

    def to_json_value(self) -> dict[str, JsonValue]:
        value = super().to_json_value()
        value["branch"] = self.branch
        value["status"] = self.status.to_json_value()
        return value


class SessionGitStageResponseDto(SessionGitMutationResponseDto):
    pass


class SessionGitUnstageResponseDto(SessionGitMutationResponseDto):
    pass


class SessionGitDiscardResponseDto(SessionGitMutationResponseDto):
    pass


class SessionGitCommitResponseDto(SessionGitMutationResponseDto):
    commit: StrictStr


class SessionGitSwitchBranchResponseDto(SessionGitMutationResponseDto):
    pass


class SessionGitCreateBranchResponseDto(SessionGitMutationResponseDto):
    pass


class GitRemoteDto(ClosedModel):
    name: StrictStr


class GitUpstreamDto(ClosedModel):
    remote: StrictStr
    branch: StrictStr


class SessionGitRemoteStatusResponseDto(MethodResultDto):
    branch: StrictStr | None = None
    remotes: list[GitRemoteDto]
    upstream: GitUpstreamDto | None = None
    ahead: StrictInt | None = Field(default=None, ge=0)
    behind: StrictInt | None = Field(default=None, ge=0)

    def to_json_value(self) -> dict[str, JsonValue]:
        value = super().to_json_value()
        value["branch"] = self.branch
        value["upstream"] = (
            self.upstream.to_json_value() if self.upstream is not None else None
        )
        value["ahead"] = self.ahead
        value["behind"] = self.behind
        return value


class SessionGitFetchResponseDto(SessionGitRemoteStatusResponseDto):
    remote: StrictStr
    head: StrictStr


class SessionGitPullResponseDto(SessionGitFetchResponseDto):
    status: SessionGitStatusResponseDto

    def to_json_value(self) -> dict[str, JsonValue]:
        value = super().to_json_value()
        value["status"] = self.status.to_json_value()
        return value


class SessionGitPushResponseDto(SessionGitPullResponseDto):
    pass


class SessionGitMergeResponseDto(SessionGitMutationResponseDto):
    operation_state: Literal["none", "merge", "rebase"] = Field(
        alias="operationState"
    )
    conflict_files: list[StrictStr] = Field(alias="conflictFiles")


class SessionGitMergeAbortResponseDto(SessionGitMergeResponseDto):
    pass


class SessionGitRebaseResponseDto(SessionGitMergeResponseDto):
    pass


class SessionGitRebaseContinueResponseDto(SessionGitRebaseResponseDto):
    pass


class SessionGitRebaseAbortResponseDto(SessionGitRebaseResponseDto):
    pass


class SessionRenameResponseDto(_SessionResponseDto):
    pass


class SessionDeleteResponseDto(MethodResultDto):
    deleted_session_id: StrictStr = Field(alias="deletedSessionId")


class SessionHandoffResponseDto(_SessionResponseDto):
    session_id: StrictStr = Field(alias="sessionId")
    worktree_id: StrictStr | None = Field(default=None, alias="worktreeId")

    def to_json_value(self) -> dict[str, JsonValue]:
        value = super().to_json_value()
        value["sessionId"] = self.session_id
        value["worktreeId"] = self.worktree_id
        return value


class SessionRestoreWorktreeResponseDto(_SessionResponseDto):
    session_id: StrictStr = Field(alias="sessionId")
    worktree_id: StrictStr = Field(alias="worktreeId")

    def to_json_value(self) -> dict[str, JsonValue]:
        value = super().to_json_value()
        value["sessionId"] = self.session_id
        value["worktreeId"] = self.worktree_id
        return value


class SettingsResponseDto(MethodResultDto, WorktreeSettingsDto):
    pass


class SettingsReadResponseDto(SettingsResponseDto):
    pass


class SettingsUpdateResponseDto(SettingsResponseDto):
    pass


class EventListResponseDto(MethodResultDto):
    items: list[EventEnvelopeDto]
    has_more: bool = Field(alias="hasMore")
    through_event_id: StrictInt = Field(alias="throughEventId")


class _RunResponseDto(MethodResultDto, RunDto):
    pass


class RunStartResponseDto(_RunResponseDto):
    pass


class RunCancelResponseDto(_RunResponseDto):
    pass


class ResumeVerificationDto(ClosedModel):
    run_id: StrictStr = Field(alias="runId")
    outcome: Literal[
        "safe_resume",
        "rebuild_context",
        "reindex_required",
        "approval_required",
        "reconciliation_required",
        "workspace_changed",
        "model_unavailable",
        "permission_changed",
        "cannot_resume",
    ]
    reasons: list[StrictStr]
    checked_at: StrictInt = Field(alias="checkedAt")


class LongTaskProgressDto(ClosedModel):
    run_id: StrictStr = Field(alias="runId")
    status: Literal[
        "running", "pause_requested", "paused", "resume_requested",
        "cancel_requested", "canceled", "completed", "failed", "interrupted",
    ]
    safe_point: Literal[
        "before_model", "after_model", "waiting_approval", "waiting_slot",
        "before_tool", "after_tool", "tool_executing", "after_checkpoint",
        "after_repository_generation",
    ] = Field(alias="safePoint")
    progress_sequence: StrictInt = Field(alias="progressSequence", ge=0)
    context_plan_id: StrictStr | None = Field(default=None, alias="contextPlanId")
    context_snapshot_id: StrictStr | None = Field(default=None, alias="contextSnapshotId")
    rule_snapshot_id: StrictStr | None = Field(default=None, alias="ruleSnapshotId")
    inventory_snapshot_id: StrictStr | None = Field(default=None, alias="inventorySnapshotId")
    index_snapshot_id: StrictStr | None = Field(default=None, alias="indexSnapshotId")
    permission_snapshot_hash: StrictStr | None = Field(default=None, alias="permissionSnapshotHash")
    workspace_path: StrictStr = Field(alias="workspacePath")
    workspace_device: StrictInt = Field(alias="workspaceDevice")
    workspace_inode: StrictInt = Field(alias="workspaceInode")
    workspace_owner: StrictInt = Field(alias="workspaceOwner")
    git_head: StrictStr | None = Field(default=None, alias="gitHead")
    side_effects_may_exist: bool = Field(alias="sideEffectsMayExist")
    reconciliation_required: bool = Field(alias="reconciliationRequired")
    pause_requested_at: StrictInt | None = Field(default=None, alias="pauseRequestedAt")
    cancel_requested_at: StrictInt | None = Field(default=None, alias="cancelRequestedAt")
    paused_at: StrictInt | None = Field(default=None, alias="pausedAt")
    resumed_at: StrictInt | None = Field(default=None, alias="resumedAt")
    updated_at: StrictInt = Field(alias="updatedAt")
    last_verification: ResumeVerificationDto | None = Field(
        default=None, alias="lastVerification"
    )


class _RunLifecycleResponseDto(MethodResultDto):
    run: RunDto
    task: LongTaskProgressDto | None = None
    resume_verification: ResumeVerificationDto | None = Field(
        default=None, alias="resumeVerification"
    )


class RunPauseResponseDto(_RunLifecycleResponseDto):
    pass


class RunResumeResponseDto(_RunLifecycleResponseDto):
    pass


class RunStatusResponseDto(_RunLifecycleResponseDto):
    pass


class ContextUsageResponseDto(MethodResultDto):
    context_usage: ContextUsageSnapshotDto | None = Field(
        default=None, alias="contextUsage"
    )


class ModelReasoningDto(ClosedModel):
    default_effort: Literal["high", "max"] = Field(alias="defaultEffort")
    supported_efforts: list[Literal["high", "max"]] = Field(alias="supportedEfforts")


class ModelOptionDto(ClosedModel):
    id: StrictStr
    name: StrictStr
    vendor: StrictStr
    provider: StrictStr
    url: StrictStr
    supports_tool_call: bool = Field(alias="supportsToolCall")
    supports_images: bool = Field(alias="supportsImages")
    supports_reasoning: bool = Field(alias="supportsReasoning")
    reasoning: ModelReasoningDto | None = None


class ModelListResponseDto(MethodResultDto):
    models: list[ModelOptionDto]
    default_model_id: StrictStr | None = Field(alias="defaultModelId")


class _ModelResponseDto(MethodResultDto, ModelOptionDto):
    pass


class ModelCreateResponseDto(_ModelResponseDto):
    pass


class ModelUpdateResponseDto(_ModelResponseDto):
    pass


class ModelDeleteResponseDto(MethodResultDto):
    deleted_model_id: StrictStr = Field(alias="deletedModelId")


class PresetModelDto(ClosedModel):
    id: StrictStr
    name: StrictStr
    url: StrictStr
    supports_tool_call: bool = Field(alias="supportsToolCall")
    supports_images: bool = Field(alias="supportsImages")
    supports_reasoning: bool = Field(alias="supportsReasoning")
    reasoning: ModelReasoningDto | None = None


class ProviderPresetDto(ClosedModel):
    id: Literal["deepseek", "minimax", "kimi"]
    name: StrictStr
    models: list[PresetModelDto]


class ModelPresetsResponseDto(MethodResultDto):
    providers: list[ProviderPresetDto]


class PluginListResponseDto(MethodResultDto):
    plugins: list[PluginRecordDto]


class _PluginResponseDto(MethodResultDto, PluginRecordDto):
    pass


class PluginImportResponseDto(_PluginResponseDto):
    pass


class PluginSetEnabledResponseDto(_PluginResponseDto):
    pass


class PluginRemoveResponseDto(_PluginResponseDto):
    pass


class SkillListResponseDto(MethodResultDto):
    skills: list[SkillMetadataDto]


class SkillSourceDto(ClosedModel):
    plugin_id: StrictStr = Field(alias="pluginId")
    plugin_version: StrictStr = Field(alias="pluginVersion")
    plugin_hash: StrictStr = Field(alias="pluginHash")


class SkillReadResponseDto(MethodResultDto):
    qualified_id: StrictStr = Field(alias="qualifiedId")
    content: StrictStr
    content_hash: StrictStr = Field(alias="contentHash")
    source: SkillSourceDto


class McpListResponseDto(MethodResultDto):
    servers: list[McpServerRecordDto]


class McpSetEnabledResponseDto(MethodResultDto, McpServerRecordDto):
    pass


class ExtensionReadResponseDto(MethodResultDto):
    plugins: list[PluginRecordDto]
    skills: list[SkillMetadataDto]
    servers: list[McpServerRecordDto]
    through_event_id: StrictInt = Field(alias="throughEventId")


class ExtensionReadEventsResponseDto(EventListResponseDto):
    pass


class CheckpointDto(ClosedModel):
    id: StrictStr
    run_id: StrictStr = Field(alias="runId")
    item_ordinal: StrictInt = Field(alias="itemOrdinal", ge=0)
    rule_snapshot_id: StrictStr | None = Field(default=None, alias="ruleSnapshotId")
    repository_snapshot_id: StrictStr | None = Field(default=None, alias="repositorySnapshotId")
    context_snapshot_id: StrictStr | None = Field(default=None, alias="contextSnapshotId")
    compact_summary_id: StrictStr | None = Field(default=None, alias="compactSummaryId")
    workspace_identity_hash: StrictStr = Field(alias="workspaceIdentityHash")
    git_head: StrictStr | None = Field(default=None, alias="gitHead")
    git_snapshot_id: StrictStr | None = Field(default=None, alias="gitSnapshotId")
    permission_snapshot_hash: StrictStr | None = Field(default=None, alias="permissionSnapshotHash")
    model_profile_snapshot_hash: StrictStr = Field(alias="modelProfileSnapshotHash")
    reconciliation_required: bool = Field(alias="reconciliationRequired")
    created_at: StrictInt = Field(alias="createdAt")


class CheckpointCreateResponseDto(MethodResultDto):
    checkpoint: CheckpointDto


class CheckpointListResponseDto(MethodResultDto):
    checkpoints: list[CheckpointDto]


class CheckpointRewindResponseDto(_RunLifecycleResponseDto):
    checkpoint: CheckpointDto


class CheckpointForkResponseDto(MethodResultDto):
    checkpoint: CheckpointDto
    parent_run_id: StrictStr = Field(alias="parentRunId")
    run: RunDto


class RepositorySnapshotReferenceDto(ClosedModel):
    snapshot_id: StrictStr = Field(alias="snapshotId")
    inventory_snapshot_id: StrictStr = Field(alias="inventorySnapshotId")
    index_snapshot_id: StrictStr | None = Field(default=None, alias="indexSnapshotId")
    inventory_generation: StrictInt = Field(alias="inventoryGeneration", ge=0)
    index_generation: StrictInt | None = Field(default=None, alias="indexGeneration", ge=0)
    complete: bool
    created_at: StrictInt = Field(alias="createdAt")


class RepositoryStatusResponseDto(MethodResultDto):
    repository_id: StrictStr = Field(alias="repositoryId")
    workspace_root: StrictStr = Field(alias="workspaceRoot")
    snapshots: list[RepositorySnapshotReferenceDto]
    reconciliation_required: bool = Field(alias="reconciliationRequired")

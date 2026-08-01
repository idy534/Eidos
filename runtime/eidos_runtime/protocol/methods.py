"""Strict request and result DTOs for public JSON-RPC business methods.

The protocol registry deliberately knows these DTOs instead of accepting one
object-shaped catch-all model.  Result DTOs remain wire-shaped at this boundary
because several established desktop responses have nested compatibility
records; each public method nevertheless owns a distinct validation type.
"""

from __future__ import annotations

import uuid
from typing import ClassVar, Literal

from pydantic import Field, JsonValue, StrictFloat, StrictInt, StrictStr
from pydantic import field_validator, model_validator

from eidos_runtime.protocol.schemas import (
    ClosedModel,
    EventEnvelopeDto,
    ItemDto,
    McpServerRecordDto,
    PluginRecordDto,
    RunDto,
    SessionDto,
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


class SessionCreateRequestDto(_OperationRequest):
    workspace_root: StrictStr = Field(
        alias="workspaceRoot", min_length=1, max_length=4096
    )


class SessionListRequestDto(MethodRequestDto):
    limit: StrictInt = Field(default=50, ge=1, le=200)
    cursor: StrictStr | None = None


class SessionReadRequestDto(_CanonicalIdRequest):
    session_id: StrictStr = Field(alias="sessionId")
    item_limit: StrictInt = Field(default=200, alias="itemLimit", ge=1, le=500)
    before_item_id: StrictStr | None = Field(default=None, alias="beforeItemId")
    _canonical_id_fields: ClassVar[tuple[str, ...]] = ("session_id", "before_item_id")


class SessionRenameRequestDto(_OperationRequest):
    session_id: StrictStr = Field(alias="sessionId")
    title: StrictStr
    _canonical_id_fields: ClassVar[tuple[str, ...]] = ("operation_id", "session_id")


class SessionDeleteRequestDto(_OperationRequest):
    session_id: StrictStr = Field(alias="sessionId")
    _canonical_id_fields: ClassVar[tuple[str, ...]] = ("operation_id", "session_id")


class EventListRequestDto(_CanonicalIdRequest):
    session_id: StrictStr = Field(alias="sessionId")
    after_event_id: StrictInt = Field(default=0, alias="afterEventId", ge=0)
    limit: StrictInt = Field(default=200, ge=1, le=500)
    _canonical_id_fields: ClassVar[tuple[str, ...]] = ("session_id",)


class RunStartRequestDto(_OperationRequest):
    session_id: StrictStr = Field(alias="sessionId")
    user_input: StrictStr = Field(alias="userInput", min_length=1, max_length=64 * 1024)
    model_id: StrictStr | None = Field(default=None, alias="modelId")
    profile_id: StrictStr | None = Field(default=None, alias="profileId")
    _canonical_id_fields: ClassVar[tuple[str, ...]] = ("operation_id", "session_id")

    @model_validator(mode="after")
    def _validate_model_selector(self) -> "RunStartRequestDto":
        super()._validate_canonical_ids()
        if not self.user_input.strip():
            raise ValueError("userInput must not be blank")
        if self.model_id is not None and self.profile_id is not None:
            raise ValueError("modelId and profileId cannot both be supplied")
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


class ModelStatusRequestDto(MethodRequestDto):
    pass


class ModelListRequestDto(MethodRequestDto):
    pass


class ModelConfigureRequestDto(MethodRequestDto):
    api_key: StrictStr = Field(alias="apiKey", min_length=1)


class ModelProfileListRequestDto(MethodRequestDto):
    pass


class ModelProfileGetRequestDto(MethodRequestDto):
    profile_id: StrictStr = Field(alias="profileId", min_length=1)


class ModelProfileCreateRequestDto(MethodRequestDto):
    profile: dict[str, JsonValue]
    api_key: StrictStr | None = Field(default=None, alias="apiKey")


class ModelProfileUpdateRequestDto(MethodRequestDto):
    profile_id: StrictStr = Field(alias="profileId", min_length=1)
    profile: dict[str, JsonValue]
    api_key: StrictStr | None = Field(default=None, alias="apiKey")


class ModelProfileDeleteRequestDto(MethodRequestDto):
    profile_id: StrictStr = Field(alias="profileId", min_length=1)


class ModelProfileListPresetsRequestDto(MethodRequestDto):
    pass


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
    workspace_root: StrictStr = Field(
        alias="workspaceRoot", min_length=1, max_length=4096
    )
    _canonical_id_fields: ClassVar[tuple[str, ...]] = ("operation_id", "checkpoint_id")


class _SessionResponseDto(MethodResultDto, SessionDto):
    pass


class SessionCreateResponseDto(_SessionResponseDto):
    pass


class SessionListResponseDto(MethodResultDto):
    items: list[SessionDto]
    next_cursor: StrictStr | None = Field(default=None, alias="nextCursor")


class SessionReadResponseDto(MethodResultDto):
    session: SessionDto
    runs: list[RunDto]
    items: list[ItemDto]
    step_resolutions: list[StepResolutionReviewDto] = Field(alias="stepResolutions")
    previous_item_id: StrictStr | None = Field(default=None, alias="previousItemId")
    through_event_id: StrictInt | None = Field(default=None, alias="throughEventId")


class SessionRenameResponseDto(_SessionResponseDto):
    pass


class SessionDeleteResponseDto(MethodResultDto):
    deleted_session_id: StrictStr = Field(alias="deletedSessionId")


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


class ModelStatusResponseDto(MethodResultDto):
    provider: StrictStr
    model: StrictStr
    configured: bool


class ModelOptionDto(ClosedModel):
    id: StrictStr
    provider: StrictStr
    display_name: StrictStr = Field(alias="displayName")
    configured: bool
    selectable: bool


class ModelListResponseDto(MethodResultDto):
    models: list[ModelOptionDto]
    default_model_id: StrictStr = Field(alias="defaultModelId")


class ModelConfigureResponseDto(ModelStatusResponseDto):
    pass


class RetryPolicyDto(ClosedModel):
    max_attempts: StrictInt = Field(alias="maxAttempts", ge=1, le=10)
    initial_backoff_seconds: StrictFloat = Field(alias="initialBackoffSeconds", ge=0)
    max_backoff_seconds: StrictFloat = Field(alias="maxBackoffSeconds", ge=0)


class ModelProfileDto(ClosedModel):
    schema_version: Literal[1] = Field(alias="schemaVersion")
    id: StrictStr
    name: StrictStr
    provider: StrictStr
    base_url: StrictStr | None = Field(default=None, alias="baseUrl")
    auth_reference: StrictStr = Field(alias="authReference")
    wire_api: Literal["openai_responses", "openai_chat_completions"] = Field(alias="wireApi")
    model_id: StrictStr = Field(alias="modelId")
    context_window: StrictInt | None = Field(default=None, alias="contextWindow", gt=0)
    max_output_tokens: StrictInt | None = Field(default=None, alias="maxOutputTokens", gt=0)
    reasoning_mode: Literal["none", "native", "compatible"] = Field(alias="reasoningMode")
    reasoning_effort: Literal["low", "medium", "high"] | None = Field(
        default=None, alias="reasoningEffort"
    )
    supports_tools: bool | None = Field(default=None, alias="supportsTools")
    supports_parallel_tools: bool | None = Field(default=None, alias="supportsParallelTools")
    supports_images: bool | None = Field(default=None, alias="supportsImages")
    supports_structured_output: bool | None = Field(default=None, alias="supportsStructuredOutput")
    supports_prompt_cache: bool | None = Field(default=None, alias="supportsPromptCache")
    request_timeout: StrictFloat = Field(alias="requestTimeout", gt=0)
    retry_policy: RetryPolicyDto = Field(alias="retryPolicy")
    created_at: StrictStr = Field(alias="createdAt")
    updated_at: StrictStr = Field(alias="updatedAt")


class ModelProfileListResponseDto(MethodResultDto):
    schema_version: Literal[1] = Field(alias="schemaVersion")
    profiles: list[ModelProfileDto]


class _ModelProfileResponseDto(MethodResultDto, ModelProfileDto):
    pass


class ModelProfileGetResponseDto(_ModelProfileResponseDto):
    pass


class ModelProfileCreateResponseDto(_ModelProfileResponseDto):
    pass


class ModelProfileUpdateResponseDto(_ModelProfileResponseDto):
    pass


class ModelProfileDeleteResponseDto(MethodResultDto):
    deleted_profile_id: StrictStr = Field(alias="deletedProfileId")


class ProviderPresetDto(ClosedModel):
    id: StrictStr
    display_name: StrictStr = Field(alias="displayName")
    default_wire_api: Literal[
        "openai_responses", "openai_chat_completions"
    ] = Field(alias="defaultWireApi")
    default_base_url: StrictStr | None = Field(default=None, alias="defaultBaseUrl")
    model_id: None = Field(default=None, alias="modelId")
    capability_hints: dict[StrictStr, bool] = Field(alias="capabilityHints")
    context_window: StrictInt | None = Field(default=None, alias="contextWindow", gt=0)
    max_output_tokens: StrictInt | None = Field(default=None, alias="maxOutputTokens", gt=0)
    compatibility_flags: list[StrictStr] = Field(alias="compatibilityFlags")


class ModelProfileListPresetsResponseDto(MethodResultDto):
    schema_version: Literal[1] = Field(alias="schemaVersion")
    presets: list[ProviderPresetDto]


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

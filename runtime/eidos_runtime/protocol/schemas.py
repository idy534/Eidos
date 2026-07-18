from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    RootModel,
    StrictInt,
    StrictStr,
    model_validator,
)


class ClosedModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)

    def to_json_value(self) -> dict[str, JsonValue]:
        return self.model_dump(
            mode="json", by_alias=True, exclude_none=True, exclude_defaults=True
        )

    @model_validator(mode="after")
    def json_safe_integers(self) -> "ClosedModel":
        def check(value: object) -> None:
            if isinstance(value, bool):
                return
            if isinstance(value, int) and abs(value) > 9_007_199_254_740_991:
                raise ValueError("integer exceeds JSON safe range")
            if isinstance(value, BaseModel):
                for item in value.__dict__.values():
                    check(item)
            elif isinstance(value, dict):
                for item in value.values():
                    check(item)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    check(item)

        for field_value in self.__dict__.values():
            check(field_value)
        return self


class ApprovalDecisionDto(ClosedModel):
    decision: Literal["approve", "reject"]
    feedback: StrictStr | None = None

    @model_validator(mode="after")
    def reject_feedback_only(self) -> "ApprovalDecisionDto":
        if self.decision == "approve" and self.feedback is not None:
            raise ValueError("approve must not include feedback")
        if self.feedback is not None and len(self.feedback.encode("utf-8")) > 2_000:
            raise ValueError("feedback exceeds 2000 bytes")
        return self


class SessionDto(ClosedModel):
    id: StrictStr
    workspace_root: StrictStr = Field(alias="workspaceRoot")
    title: StrictStr | None = None
    task_status: Literal[
        "new", "in_progress", "completed", "failed", "canceled"
    ] = Field(alias="taskStatus")
    created_at: StrictInt = Field(alias="createdAt")
    updated_at: StrictInt = Field(alias="updatedAt")


class PluginRecordDto(ClosedModel):
    schema_version: Literal[1] = Field(alias="schemaVersion")
    id: StrictStr
    name: StrictStr
    version: StrictStr
    description: StrictStr
    content_hash: StrictStr = Field(alias="contentHash")
    enabled: bool
    status: Literal["installed", "removed"]
    installed_at: StrictInt = Field(alias="installedAt")
    updated_at: StrictInt = Field(alias="updatedAt")


class RunPluginSnapshotDto(ClosedModel):
    id: StrictStr
    version: StrictStr
    content_hash: StrictStr = Field(alias="contentHash")


class RunExtensionSnapshotDto(ClosedModel):
    schema_version: Literal[1] = Field(alias="schemaVersion")
    extension_contract_version: Literal[1] = Field(alias="extensionContractVersion")
    plugins: list[RunPluginSnapshotDto]
    skill_catalog_hash: StrictStr = Field(alias="skillCatalogHash")
    mcp_config_hash: StrictStr = Field(alias="mcpConfigHash")


class SkillMetadataDto(ClosedModel):
    schema_version: Literal[1] = Field(alias="schemaVersion")
    qualified_id: StrictStr = Field(alias="qualifiedId")
    name: StrictStr
    description: StrictStr
    plugin_id: StrictStr = Field(alias="pluginId")
    plugin_version: StrictStr = Field(alias="pluginVersion")
    plugin_hash: StrictStr = Field(alias="pluginHash")
    content_hash: StrictStr = Field(alias="contentHash")


class StepToolSnapshotDto(ClosedModel):
    schema_version: Literal[1] = Field(alias="schemaVersion")
    available_names: list[StrictStr] = Field(alias="availableNames")
    direct_names: list[StrictStr] = Field(alias="directNames")
    deferred_names: list[StrictStr] = Field(alias="deferredNames")
    activated_names: list[StrictStr] = Field(alias="activatedNames")
    spec_hashes: dict[StrictStr, StrictStr] = Field(alias="specHashes")
    definitions_hash: StrictStr = Field(alias="definitionsHash")
    tool_set_hash: StrictStr = Field(alias="toolSetHash")


class McpServerRecordDto(ClosedModel):
    schema_version: Literal[1] = Field(alias="schemaVersion")
    plugin_id: StrictStr = Field(alias="pluginId")
    plugin_version: StrictStr = Field(alias="pluginVersion")
    plugin_hash: StrictStr = Field(alias="pluginHash")
    server_id: StrictStr = Field(alias="serverId")
    executable: StrictStr
    argv: list[StrictStr]
    env_names: list[StrictStr] = Field(alias="envNames")
    permission_profile: Literal["connector", "workspace_read"] = Field(
        alias="permissionProfile"
    )
    startup_timeout_seconds: StrictInt = Field(alias="startupTimeoutSeconds")
    tool_timeout_seconds: StrictInt = Field(alias="toolTimeoutSeconds")
    declared_enabled: bool = Field(alias="declaredEnabled")
    consented: bool
    available: bool
    error_code: StrictStr | None = Field(default=None, alias="errorCode")
    updated_at: StrictInt = Field(alias="updatedAt")


class RunDto(ClosedModel):
    id: StrictStr
    session_id: StrictStr = Field(alias="sessionId")
    user_input: StrictStr | None = Field(default=None, alias="userInput")
    model_id: Literal["deepseek-v4-flash", "deepseek-v4-pro"] = Field(
        alias="modelId"
    )
    status: Literal[
        "queued", "running", "waiting_approval", "waiting_user_input",
        "finalizing", "stopped", "succeeded", "failed", "canceled", "interrupted",
    ]
    runtime_state: Literal[
        "queued", "thinking", "tool_executing", "waiting_approval",
        "waiting_user_input", "finalizing", "terminal",
    ] | None = Field(default=None, alias="runtimeState")
    allowed_actions: list[Literal["cancel", "approve", "reject", "continue"]] = Field(
        default_factory=list, alias="allowedActions"
    )
    model_step_count: StrictInt = Field(alias="modelStepCount")
    created_at: StrictInt = Field(alias="createdAt")
    started_at: StrictInt | None = Field(default=None, alias="startedAt")
    updated_at: StrictInt = Field(alias="updatedAt")
    completed_at: StrictInt | None = Field(default=None, alias="completedAt")
    error_code: StrictStr | None = Field(default=None, alias="errorCode")
    pause_reason: StrictStr | None = Field(default=None, alias="pauseReason")
    stop_reason: StrictStr | None = Field(default=None, alias="stopReason")
    side_effects_may_exist: bool = Field(default=False, alias="sideEffectsMayExist")
    extension_snapshot: RunExtensionSnapshotDto | None = Field(
        default=None, alias="extensionSnapshot"
    )
    activated_tools: list[StrictStr] = Field(
        default_factory=list, alias="activatedTools"
    )


class ToolCallDto(ClosedModel):
    id: StrictStr
    item_id: StrictStr = Field(alias="itemId")
    tool_name: StrictStr = Field(alias="toolName")
    model_step_index: StrictInt = Field(alias="modelStepIndex")
    batch_order: StrictInt = Field(alias="batchOrder")
    provider_call_id: StrictStr = Field(alias="providerCallId")
    status: Literal["running", "completed", "failed", "canceled"]
    arguments_json: StrictStr | None = Field(default=None, alias="argumentsJson")
    result_json: StrictStr | None = Field(default=None, alias="resultJson")
    approval_status: Literal["pending", "resolved", "canceled"] | None = Field(default=None, alias="approvalStatus")
    approval_decision: Literal["approve", "reject"] | None = Field(default=None, alias="approvalDecision")
    approval_feedback: StrictStr | None = Field(default=None, alias="approvalFeedback")
    approval_diff: StrictStr | None = Field(default=None, alias="approvalDiff")
    base_sha256: StrictStr | None = Field(default=None, alias="baseSha256")
    provenance: dict[str, JsonValue] | None = None
    tool_set_hash: StrictStr | None = Field(default=None, alias="toolSetHash")
    started_at: StrictInt = Field(alias="startedAt")
    completed_at: StrictInt | None = Field(default=None, alias="completedAt")


class ItemDto(ClosedModel):
    id: StrictStr
    session_id: StrictStr = Field(alias="sessionId")
    run_id: StrictStr = Field(alias="runId")
    ordinal: StrictInt
    model_step_index: StrictInt | None = Field(default=None, alias="modelStepIndex")
    kind: Literal["user_message", "assistant_message", "file_change", "command_execution", "tool_call"]
    status: Literal["in_progress", "completed", "failed", "declined", "canceled"]
    created_at: StrictInt = Field(alias="createdAt")
    content: StrictStr | None = None
    incomplete: bool = False
    completed_at: StrictInt | None = Field(default=None, alias="completedAt")
    tool_call: ToolCallDto | None = Field(default=None, alias="toolCall")


class SearchMatchDto(ClosedModel):
    path: StrictStr
    line: StrictInt
    column: StrictInt
    preview: StrictStr


class ToolSearchHitDto(ClosedModel):
    name: StrictStr
    description: StrictStr
    provenance: dict[str, JsonValue]
    score: StrictInt


class ToolResultDataDto(ClosedModel):
    path: StrictStr | None = None
    paths: list[StrictStr] | None = None
    content: StrictStr | None = None
    size_bytes: StrictInt | None = Field(default=None, alias="sizeBytes")
    sha256: StrictStr | None = None
    base_sha256: StrictStr | None = Field(default=None, alias="baseSha256")
    truncated: bool | None = None
    truncation_reason: StrictStr | None = Field(default=None, alias="truncationReason")
    matches: list[SearchMatchDto] | None = None
    scanned_bytes: StrictInt | None = Field(default=None, alias="scannedBytes")
    start_line: StrictInt | None = Field(default=None, alias="startLine")
    end_line: StrictInt | None = Field(default=None, alias="endLine")
    next_line: StrictInt | None = Field(default=None, alias="nextLine")
    exit_code: StrictInt | None = Field(default=None, alias="exitCode")
    stdout: StrictStr | None = None
    stderr: StrictStr | None = None
    termination: StrictStr | None = None
    duration_ms: StrictInt | None = Field(default=None, alias="durationMs")
    qualified_id: StrictStr | None = Field(default=None, alias="qualifiedId")
    resource_path: StrictStr | None = Field(default=None, alias="resourcePath")
    content_hash: StrictStr | None = Field(default=None, alias="contentHash")
    plugin_id: StrictStr | None = Field(default=None, alias="pluginId")
    plugin_version: StrictStr | None = Field(default=None, alias="pluginVersion")
    plugin_hash: StrictStr | None = Field(default=None, alias="pluginHash")
    text: StrictStr | None = None
    structured_content: dict[str, JsonValue] | None = Field(
        default=None, alias="structuredContent"
    )
    hits: list[ToolSearchHitDto] | None = None
    total_matches: StrictInt | None = Field(default=None, alias="totalMatches")


class ToolResultDto(ClosedModel):
    tool_contract_version: Literal[1] = Field(alias="toolContractVersion")
    schema_version: Literal[1] = Field(alias="schemaVersion")
    tool_name: StrictStr = Field(alias="toolName")
    outcome: Literal["success", "error", "skipped", "rejected", "interrupted", "unavailable", "declined"]
    code: StrictStr
    summary: StrictStr
    data: ToolResultDataDto
    side_effects_may_exist: bool = Field(alias="sideEffectsMayExist")
    reconciliation_required: bool = Field(default=False, alias="reconciliationRequired")


class EventEnvelopeDto(ClosedModel):
    event_contract_version: Literal[1] = Field(alias="eventContractVersion")
    event_id: StrictInt = Field(alias="eventId")
    event_type: StrictStr = Field(alias="eventType")
    occurred_at: StrictInt = Field(alias="occurredAt")
    session_id: StrictStr | None = Field(default=None, alias="sessionId")
    run_id: StrictStr | None = Field(default=None, alias="runId")
    payload: dict[str, JsonValue]


class JsonRpcRequestDto(ClosedModel):
    jsonrpc: Literal["2.0"]
    id: StrictStr
    method: StrictStr
    params: JsonValue


class JsonRpcErrorDto(ClosedModel):
    code: StrictInt
    message: StrictStr
    data: dict[str, JsonValue] | None = None


class JsonRpcSuccessDto(ClosedModel):
    jsonrpc: Literal["2.0"]
    id: StrictStr
    result: dict[str, JsonValue]


class JsonRpcFailureDto(ClosedModel):
    jsonrpc: Literal["2.0"]
    id: StrictStr | None
    error: JsonRpcErrorDto


JsonRpcResponseDto = Annotated[JsonRpcSuccessDto | JsonRpcFailureDto, Field(union_mode="left_to_right")]


class JsonRpcResponse(RootModel[JsonRpcResponseDto]):
    model_config = ConfigDict(strict=True)

    def to_json_value(self) -> dict[str, JsonValue]:
        return self.root.model_dump(mode="json", by_alias=True, exclude_none=False)

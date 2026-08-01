"""Strict request and result DTOs for public JSON-RPC business methods.

The protocol registry deliberately knows these DTOs instead of accepting one
object-shaped catch-all model.  Result DTOs remain wire-shaped at this boundary
because several established desktop responses have nested compatibility
records; each public method nevertheless owns a distinct validation type.
"""

from __future__ import annotations

import uuid
from typing import ClassVar

from pydantic import ConfigDict, Field, JsonValue, RootModel, StrictInt, StrictStr
from pydantic import field_validator, model_validator

from eidos_runtime.protocol.schemas import ClosedModel


class MethodRequestDto(ClosedModel):
    """Base type for a method-specific request DTO."""


class MethodResultDto(RootModel[dict[str, JsonValue]]):
    """Wire-object result with JSON-safe integer enforcement.

    Subclasses are intentionally nominal method contracts.  They keep the
    existing response shape stable while preventing a registration from using
    the old universal ``JsonObjectResult`` model.
    """

    model_config = ConfigDict(strict=True)

    @model_validator(mode="after")
    def _validate_json_safe_integers(self) -> "MethodResultDto":
        def check(value: object) -> None:
            if isinstance(value, bool):
                return
            if isinstance(value, int) and abs(value) > 9_007_199_254_740_991:
                raise ValueError("integer exceeds JSON safe range")
            if isinstance(value, dict):
                for item in value.values():
                    check(item)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    check(item)

        check(self.root)
        return self

    def to_json_value(self) -> dict[str, JsonValue]:
        return self.model_dump(mode="json")


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


class SessionCreateResponseDto(MethodResultDto):
    pass


class SessionListResponseDto(MethodResultDto):
    pass


class SessionReadResponseDto(MethodResultDto):
    pass


class SessionRenameResponseDto(MethodResultDto):
    pass


class SessionDeleteResponseDto(MethodResultDto):
    pass


class EventListResponseDto(MethodResultDto):
    pass


class RunStartResponseDto(MethodResultDto):
    pass


class RunCancelResponseDto(MethodResultDto):
    pass


class RunPauseResponseDto(MethodResultDto):
    pass


class RunResumeResponseDto(MethodResultDto):
    pass


class RunStatusResponseDto(MethodResultDto):
    pass


class ModelStatusResponseDto(MethodResultDto):
    pass


class ModelListResponseDto(MethodResultDto):
    pass


class ModelConfigureResponseDto(MethodResultDto):
    pass


class ModelProfileListResponseDto(MethodResultDto):
    pass


class ModelProfileGetResponseDto(MethodResultDto):
    pass


class ModelProfileCreateResponseDto(MethodResultDto):
    pass


class ModelProfileUpdateResponseDto(MethodResultDto):
    pass


class ModelProfileDeleteResponseDto(MethodResultDto):
    pass


class ModelProfileListPresetsResponseDto(MethodResultDto):
    pass


class PluginListResponseDto(MethodResultDto):
    pass


class PluginImportResponseDto(MethodResultDto):
    pass


class PluginSetEnabledResponseDto(MethodResultDto):
    pass


class PluginRemoveResponseDto(MethodResultDto):
    pass


class SkillListResponseDto(MethodResultDto):
    pass


class SkillReadResponseDto(MethodResultDto):
    pass


class McpListResponseDto(MethodResultDto):
    pass


class McpSetEnabledResponseDto(MethodResultDto):
    pass


class ExtensionReadResponseDto(MethodResultDto):
    pass


class ExtensionReadEventsResponseDto(MethodResultDto):
    pass


class CheckpointCreateResponseDto(MethodResultDto):
    pass


class CheckpointListResponseDto(MethodResultDto):
    pass


class CheckpointRewindResponseDto(MethodResultDto):
    pass


class CheckpointForkResponseDto(MethodResultDto):
    pass

"""Eidos-owned Hook definitions, decisions and durable execution records."""
from __future__ import annotations

from typing import Annotated, Literal

from pydantic import ConfigDict, Field, JsonValue, StrictBool, StrictInt, StrictStr, model_validator

from eidos_runtime.models import EidosFrozenModel


HookEvent = Literal[
    "SessionStart", "SessionEnd", "UserPromptSubmit", "PreToolUse",
    "PermissionRequest", "PostToolUse", "PreCompact", "PostCompact",
    "SubagentStart", "SubagentStop", "Stop", "Interrupt",
]
SUPPORTED_HOOK_EVENTS = frozenset({
    "SessionStart", "SessionEnd", "UserPromptSubmit", "PreToolUse",
    "PermissionRequest", "PostToolUse", "PreCompact", "PostCompact",
    "SubagentStart", "SubagentStop", "Stop", "Interrupt",
})


class HookDefinition(EidosFrozenModel):
    key: StrictStr = Field(min_length=1, max_length=256)
    event: HookEvent
    matcher: str | None = Field(default=None, max_length=1024)
    handler_type: Literal["command", "mcp_tool", "prompt", "agent"] = "command"
    command: str = Field(default="", max_length=32768)
    timeout_seconds: StrictInt = Field(default=600, ge=1, le=600)
    background: StrictBool = False
    status_message: str | None = Field(default=None, max_length=1024)
    additional_context_limit: StrictInt = Field(default=2500, ge=0, le=100000)
    mcp_server: str | None = Field(default=None, max_length=128)
    mcp_tool: str | None = Field(default=None, max_length=128)
    mcp_input: dict[str, JsonValue] = Field(default_factory=dict)

    @property
    def supported(self) -> bool:
        return self.event in SUPPORTED_HOOK_EVENTS and (
            self.handler_type == "command" or (
                self.handler_type == "mcp_tool" and self.event != "SessionEnd"
                and not self.background and bool(self.mcp_server and self.mcp_tool)
            )
        )


class PluginCompatibility(EidosFrozenModel):
    format: Literal["eidos", "codex", "portable"] = "eidos"
    declared_version: str | None = None
    issues: tuple[str, ...] = ()


class HookResult(EidosFrozenModel):
    status: Literal["completed", "failed", "unknown", "skipped"] = "completed"
    action: Literal["pass", "deny", "feedback", "continue", "stop"] = "pass"
    message: str = Field(default="", max_length=8192)
    additional_context: str = Field(default="", max_length=65536)
    updated_input: dict[str, JsonValue] | None = None
    error_code: str | None = None
    system_message: str = Field(default="", max_length=8192)
    exit_code: StrictInt | None = None
    duration_ms: StrictInt = Field(default=0, ge=0)
    workspace_changed: StrictBool = False
    workspace_observation_complete: StrictBool = True
    workspace_changes: tuple[str, ...] = ()
    output_call_id: str | None = None
    context_truncated: StrictBool = False
    permission_decision: Literal["allow", "deny"] | None = None


class HookOutput(EidosFrozenModel):
    stdout: str = Field(default="", max_length=262144)
    stderr: str = Field(default="", max_length=262144)
    raw_truncated: StrictBool = False
    raw_omitted_bytes: StrictInt = Field(default=0, ge=0)


class HookActivity(EidosFrozenModel):
    id: str = Field(min_length=1, max_length=256)
    run_id: str = Field(min_length=1, max_length=256)
    session_id: str = Field(min_length=1, max_length=256)
    source_id: str = Field(min_length=1, max_length=128)
    event: HookEvent
    handler_type: Literal["command", "mcp_tool", "prompt", "agent"]
    background: StrictBool = False
    status: Literal["intent", "running", "completed", "failed", "unknown", "skipped"]
    status_message: str = Field(default="", max_length=1024)
    created_at: StrictInt = Field(ge=0, le=9007199254740991)
    completed_at: StrictInt | None = Field(default=None, ge=0, le=9007199254740991)
    result: HookResult | None = None
    stdout_preview: str = Field(default="", max_length=4096)
    stderr_preview: str = Field(default="", max_length=4096)
    preview_truncated: StrictBool = False


class HookActivityPage(EidosFrozenModel):
    items: tuple[HookActivity, ...] = Field(default=(), max_length=20)
    next_cursor: StrictInt | None = Field(default=None, ge=1, le=9007199254740991)


class HookSourceReview(EidosFrozenModel):
    id: str = Field(min_length=1, max_length=128)
    scope: Literal["user", "project"]
    paths: tuple[Annotated[str, Field(min_length=1, max_length=4096)], ...] = Field(min_length=1, max_length=2)
    content_hash: str = Field(pattern="^[a-f0-9]{64}$")
    definitions: tuple[HookDefinition, ...] = Field(default=(), max_length=128)
    trusted: StrictBool = False
    enabled: StrictBool = True
    error_code: str | None = Field(default=None, max_length=128)


class HookSourceList(EidosFrozenModel):
    sources: tuple[HookSourceReview, ...] = Field(default=(), max_length=2)


class HookConfigurationRequest(EidosFrozenModel):
    model_config = ConfigDict(json_schema_extra={"allOf": [
        {"if": {"properties": {"action": {"const": "read"}}},
         "then": {"properties": {name: {"type": "null"} for name in ("sourceId", "contentHash", "trusted", "enabled")}}},
        {"if": {"required": ["action"], "properties": {"action": {"const": "trust"}}},
         "then": {"required": ["sourceId", "contentHash", "trusted"], "properties": {
             "sourceId": {"type": "string"}, "contentHash": {"type": "string"}, "trusted": {"type": "boolean"},
             "sessionId": {"type": "null"}, "enabled": {"type": "null"}}}},
        {"if": {"required": ["action"], "properties": {"action": {"const": "enable"}}},
         "then": {"required": ["sourceId", "contentHash", "enabled"], "properties": {
             "sourceId": {"type": "string"}, "contentHash": {"type": "string"}, "enabled": {"type": "boolean"},
             "sessionId": {"type": "null"}, "trusted": {"type": "null"}}}},
    ]})
    action: Literal["read", "trust", "enable"] = "read"
    session_id: str | None = Field(default=None, min_length=1, max_length=256)
    source_id: str | None = Field(default=None, min_length=1, max_length=128)
    content_hash: str | None = Field(default=None, pattern="^[a-f0-9]{64}$")
    trusted: StrictBool | None = None
    enabled: StrictBool | None = None

    @model_validator(mode="after")
    def valid_action(self) -> "HookConfigurationRequest":
        if self.action == "read":
            if any(value is not None for value in (self.source_id, self.content_hash, self.trusted, self.enabled)):
                raise ValueError("hook_read_fields_invalid")
        elif not self.source_id or not self.content_hash or self.session_id is not None:
            raise ValueError("hook_source_identity_required")
        elif (self.action == "trust" and (self.trusted is None or self.enabled is not None)
              or self.action == "enable" and (self.enabled is None or self.trusted is not None)):
            raise ValueError("hook_source_action_invalid")
        return self


class HookHistoryRequest(EidosFrozenModel):
    source_id: str | None = Field(default=None, min_length=1, max_length=128)
    session_id: str | None = Field(default=None, min_length=1, max_length=256)
    run_id: str | None = Field(default=None, min_length=1, max_length=256)
    cursor: StrictInt | None = Field(default=None, ge=1, le=9007199254740991)


class HookTranscriptItem(EidosFrozenModel):
    id: str
    kind: str
    content: str
    created_at: int
    tool_name: str | None = None
    call_id: str | None = None
    arguments: str | None = None
    output: str | None = None
    truncated: bool = False


class PluginHookReview(EidosFrozenModel):
    content_hash: StrictStr = Field(pattern="^[a-f0-9]{64}$")
    trusted: StrictBool = False
    definitions: tuple[HookDefinition, ...] = ()
    executable_count: StrictInt = Field(default=0, ge=0, le=128)
    installed_root: str = ""
    data_directory: str = ""
    latest: HookResult | None = None


class HookBinding(EidosFrozenModel):
    plugin_id: str
    plugin_hash: str
    definition: HookDefinition
    source_kind: Literal["plugin", "user", "project"] = "plugin"
    source_paths: tuple[str, ...] = ()


class HookPendingEvent(EidosFrozenModel):
    event_key: str
    fields: dict[str, JsonValue]


class HookOutcome(EidosFrozenModel):
    deny: bool = False
    stop: bool = False
    continuation: tuple[str, ...] = ()
    feedback: tuple[str, ...] = ()
    context: tuple[str, ...] = ()
    updated_input: dict[str, JsonValue] | None = None
    permission_decision: Literal["allow", "deny"] | None = None


class HookStopRequested(Exception):
    """An enabled, trusted Hook explicitly requested the current turn to stop."""


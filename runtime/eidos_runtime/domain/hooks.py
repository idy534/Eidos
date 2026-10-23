"""Eidos-owned Hook definitions, decisions and durable execution records."""
from __future__ import annotations

from typing import Literal

from pydantic import Field, JsonValue, StrictBool, StrictInt, StrictStr

from eidos_runtime.models import EidosFrozenModel


HookEvent = Literal[
    "SessionStart", "SessionEnd", "UserPromptSubmit", "PreToolUse",
    "PermissionRequest", "PostToolUse", "PreCompact", "PostCompact",
    "SubagentStart", "SubagentStop", "Stop", "Interrupt",
]
SUPPORTED_HOOK_EVENTS = frozenset({"UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop", "SubagentStop"})


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

    @property
    def supported(self) -> bool:
        return self.event in SUPPORTED_HOOK_EVENTS and self.handler_type == "command" and not self.background


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
    workspace_changes: tuple[str, ...] = ()
    output_call_id: str | None = None
    context_truncated: StrictBool = False


class HookOutput(EidosFrozenModel):
    stdout: str = Field(default="", max_length=262144)
    stderr: str = Field(default="", max_length=262144)
    raw_truncated: StrictBool = False
    raw_omitted_bytes: StrictInt = Field(default=0, ge=0)


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


class HookStopRequested(Exception):
    """An enabled, trusted Hook explicitly requested the current turn to stop."""


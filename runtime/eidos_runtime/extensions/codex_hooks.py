"""Codex's external Hook configuration and event-specific output adapter."""
from __future__ import annotations

import json
from typing import Annotated, Literal, get_args

from pydantic import ConfigDict, Field, JsonValue, TypeAdapter, ValidationError

from eidos_runtime.domain.hooks import HookDefinition, HookEvent, HookResult
from eidos_runtime.models import EidosFrozenModel


class _ExternalModel(EidosFrozenModel):
    model_config = ConfigDict(alias_generator=None, strict=True)


class _Handler(_ExternalModel):
    type: Literal["command", "mcp_tool", "prompt", "agent"]
    command: str = Field(default="", max_length=32768)
    commandWindows: str | None = Field(default=None, max_length=32768)
    timeout: int | None = Field(default=None, ge=1, le=600)
    statusMessage: str | None = Field(default=None, max_length=1024)
    additionalContextLimit: int = Field(default=2500, ge=0, le=100000)
    asynchronous: bool = Field(default=False, alias="async")
    server: str | None = None
    tool: str | None = None
    input: dict[str, JsonValue] | None = None
    prompt: str | None = None
    model: str | None = None


class _Group(_ExternalModel):
    matcher: str | None = Field(default=None, max_length=1024)
    hooks: list[_Handler] = Field(max_length=32)


class _Config(_ExternalModel):
    description: str | None = Field(default=None, max_length=4096)
    hooks: dict[str, list[_Group]]


class _SpecificOutput(_ExternalModel):
    hookEventName: HookEvent | None = None
    additionalContext: str = Field(default="", max_length=262144)
    permissionDecision: Literal["allow", "deny", "ask"] | None = None
    permissionDecisionReason: str = Field(default="", max_length=8192)
    updatedInput: dict[str, JsonValue] | None = None
    decision: "_PermissionDecision | None" = None


class _PermissionDecision(_ExternalModel):
    behavior: Literal["allow", "deny"]
    message: str = Field(default="", max_length=8192)


class _Output(_ExternalModel):
    decision: Literal["block", "approve"] | None = None
    reason: str = Field(default="", max_length=8192)
    continue_: bool | None = Field(default=None, alias="continue")
    stopReason: str = Field(default="", max_length=8192)
    systemMessage: str = Field(default="", max_length=8192)
    suppressOutput: bool | None = None
    updatedMCPToolOutput: JsonValue = None
    hookSpecificOutput: _SpecificOutput = _SpecificOutput()


def parse_hooks(value: object, source: str) -> tuple[HookDefinition, ...]:
    config = _Config.model_validate(value)
    definitions = []
    for event, groups in config.hooks.items():
        if event not in get_args(HookEvent) or len(groups) > 32:
            raise ValueError("hook_event_invalid")
        for group_index, group in enumerate(groups):
            if group.matcher not in {None, "", "*"}:
                # Pydantic uses the same mature Rust regex family as Codex.
                try:
                    TypeAdapter(Annotated[str, Field(pattern=group.matcher)])
                except Exception as error:
                    raise ValueError("hook_matcher_invalid") from error
            for index, handler in enumerate(group.hooks):
                if handler.type == "command" and not handler.command:
                    raise ValueError("hook_command_missing")
                if handler.type == "mcp_tool" and not (handler.server and handler.tool):
                    raise ValueError("hook_mcp_tool_missing")
                definitions.append(HookDefinition(
                    key=f"{source}:{event}:{group_index}:{index}", event=event,
                    matcher=group.matcher, handler_type=handler.type, command=handler.command,
                    timeout_seconds=min(handler.timeout or 1, 3) if event in {"Interrupt", "SessionEnd"} else handler.timeout or 600,
                    background=handler.asynchronous and event != "SessionEnd", status_message=handler.statusMessage,
                    additional_context_limit=handler.additionalContextLimit,
                    mcp_server=handler.server, mcp_tool=handler.tool,
                    mcp_input=handler.input or {},
                ))
    if len(definitions) > 128:
        raise ValueError("hook_capacity_exceeded")
    if len(json.dumps([definition.to_wire_dict() for definition in definitions]).encode("utf-8")) > 65536:
        raise ValueError("hook_capacity_exceeded")
    return tuple(definitions)


def matches(definition: HookDefinition, names: tuple[str, ...]) -> bool:
    if definition.event in {"Stop", "UserPromptSubmit", "Interrupt"} or definition.matcher in {None, "", "*"}:
        return True
    matcher = TypeAdapter(Annotated[str, Field(pattern=definition.matcher)])
    for name in names:
        try:
            matcher.validate_python(name)
            return True
        except ValidationError:
            pass
    return False


def parse_output(event: HookEvent, stdout: str, stderr: str, exit_code: int | None) -> HookResult:
    if exit_code == 2:
        message = stderr.strip()[:8192]
        if not message:
            return HookResult(status="failed", error_code="hook_reason_missing", exit_code=exit_code)
        if event in {"Interrupt", "SessionEnd"}:
            return HookResult(status="failed", error_code="hook_command_failed", message=message, exit_code=exit_code)
        action = "deny" if event in {"PreToolUse", "UserPromptSubmit"} else "continue" if event in {"Stop", "SubagentStop"} else "feedback"
        return HookResult(action=action, message=message, context_truncated=len(stderr.strip()) > 8192, exit_code=exit_code)
    if exit_code != 0:
        return HookResult(status="failed", error_code="hook_command_failed", exit_code=exit_code)
    if not stdout.strip():
        return HookResult(exit_code=0)
    try:
        value = json.loads(stdout)
    except (ValueError, RecursionError):
        if event in {"UserPromptSubmit", "SessionStart", "SubagentStart"}:
            return HookResult(additional_context=stdout[:65536], context_truncated=len(stdout) > 65536, exit_code=0)
        if event in {"PreToolUse", "PostToolUse", "PermissionRequest", "PreCompact", "PostCompact"}:
            return HookResult(exit_code=0)
        return HookResult(status="failed", error_code="hook_output_invalid", exit_code=0)
    try:
        output = _Output.model_validate(value)
    except (ValueError, RecursionError):
        return HookResult(status="failed", error_code="hook_output_invalid", exit_code=0)
    specific = output.hookSpecificOutput
    if specific.hookEventName is not None and specific.hookEventName != event:
        return HookResult(status="failed", error_code="hook_output_invalid", exit_code=0)
    base = {"system_message": output.systemMessage, "exit_code": 0,
            "context_truncated": len(specific.additionalContext) > 65536}
    context = specific.additionalContext[:65536]
    message = output.reason or output.stopReason
    if "updatedMCPToolOutput" in output.model_fields_set or output.decision == "approve":
        return HookResult(status="failed", error_code="hook_output_unsupported", **base)
    if "suppressOutput" in output.model_fields_set and event in {"PreToolUse", "PostToolUse", "PermissionRequest"}:
        return HookResult(status="failed", error_code="hook_output_unsupported", **base)
    if event == "PermissionRequest":
        if (specific.updatedInput is not None or specific.permissionDecision is not None
                or {"continue_", "stopReason"} & output.model_fields_set):
            return HookResult(status="failed", error_code="hook_output_unsupported", **base)
        decision = specific.decision
        return HookResult(permission_decision=decision.behavior if decision else None,
            action="deny" if decision and decision.behavior == "deny" else "pass",
            message=decision.message if decision else "", **base)
    if specific.decision is not None:
        return HookResult(status="failed", error_code="hook_output_unsupported", **base)
    if event in {"SessionEnd", "Interrupt"}:
        return HookResult(**base)
    if event == "PreToolUse":
        if {"continue_", "stopReason"} & output.model_fields_set or specific.permissionDecision == "ask":
            return HookResult(status="failed", error_code="hook_output_unsupported", **base)
        if specific.updatedInput is not None and specific.permissionDecision != "allow":
            return HookResult(status="failed", error_code="hook_output_invalid", **base)
        if specific.permissionDecision == "deny" or output.decision == "block":
            return HookResult(action="deny", message=specific.permissionDecisionReason or message,
                additional_context=context, **base)
        return HookResult(updated_input=specific.updatedInput, additional_context=context, **base)
    if specific.updatedInput is not None or specific.permissionDecision is not None:
        return HookResult(status="failed", error_code="hook_output_unsupported", **base)
    if event == "SubagentStart":
        return HookResult(additional_context=context, **base)
    if output.continue_ is False:
        return HookResult(action="feedback" if event == "PostToolUse" else "stop", message=message, additional_context=context, **base)
    if output.decision == "block":
        if not message.strip():
            return HookResult(status="failed", error_code="hook_reason_missing", **base)
        action = "continue" if event in {"Stop", "SubagentStop"} else "deny" if event == "UserPromptSubmit" else "feedback"
        return HookResult(action=action, message=message, additional_context=context, **base)
    return HookResult(additional_context=context, **base)

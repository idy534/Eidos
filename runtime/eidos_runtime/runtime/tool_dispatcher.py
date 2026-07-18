from __future__ import annotations

from dataclasses import dataclass
import threading

from eidos_runtime.model import ModelResponse, ModelToolCall
from eidos_runtime.tools import FileChange, ToolExecutor


@dataclass(frozen=True)
class ToolValidationResult:
    tool_calls: tuple[ModelToolCall, ...]
    error_code: str | None = None


@dataclass(frozen=True)
class ToolDispatchPlan:
    requires_approval: bool
    is_shell: bool


class ToolDispatcher:
    """Owns model ToolCall validation and batch invariants for RuntimeEngine."""

    def __init__(self, tools: ToolExecutor) -> None:
        self._tools = tools

    def validate(self, response: ModelResponse) -> ToolValidationResult:
        if not isinstance(response, ModelResponse):
            return ToolValidationResult((), "invalid_response")
        if not response.text and not response.tool_calls:
            return ToolValidationResult((), "empty_response")
        if len(response.tool_calls) > 16:
            return ToolValidationResult((), "too_many_tool_calls")
        provider_ids: set[str] = set()
        for call in response.tool_calls:
            if (
                not isinstance(call, ModelToolCall)
                or not isinstance(call.provider_call_id, str)
                or not 1 <= len(call.provider_call_id) <= 256
                or call.provider_call_id in provider_ids
                or not isinstance(call.name, str)
                or call.name not in self._tools.tool_names
                or not self._tools.validate_arguments(call.name, call.arguments)
                or not _valid_arguments(call.arguments)
            ):
                return ToolValidationResult((), "invalid_tool_call")
            provider_ids.add(call.provider_call_id)
        if any(
            self._tools.is_side_effecting(call.name) or self._tools.is_shell(call.name)
            for call in response.tool_calls
        ) and len(response.tool_calls) != 1:
            return ToolValidationResult((), "invalid_tool_batch")
        return ToolValidationResult(response.tool_calls)

    def plan(self, call: ModelToolCall) -> ToolDispatchPlan:
        """Classify the validated call without exposing ToolExecutor metadata."""
        return ToolDispatchPlan(
            requires_approval=self._tools.is_side_effecting(call.name) or self._tools.is_shell(call.name),
            is_shell=self._tools.is_shell(call.name),
        )

    def execute_read_only(
        self, call: ModelToolCall, cancel: threading.Event
    ) -> dict[str, object]:
        """Execute only the tools whose existing spec has no side effect."""
        return self._tools.execute(call.name, call.arguments, cancel)

    def prepare_file_change(
        self, tool_name: str, arguments: dict[str, object], cancel: threading.Event
    ) -> FileChange | dict[str, object]:
        return self._tools.prepare_file_change(tool_name, arguments, cancel)

    def commit_file_change(
        self, tool_name: str, change: FileChange, cancel: threading.Event
    ) -> dict[str, object]:
        return self._tools.commit_file_change(tool_name, change, cancel)

    def prepare_shell(self, cwd: str, cancel: threading.Event):
        return self._tools.prepare_shell(cwd, cancel)

    @property
    def workspace(self):
        return self._tools.workspace


def _valid_arguments(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    return all(isinstance(key, str) and _valid_value(item) for key, item in value.items())


def _valid_value(value: object) -> bool:
    if value is None or isinstance(value, (str, bool, int, float)):
        return True
    if isinstance(value, list):
        return all(_valid_value(item) for item in value)
    if isinstance(value, dict):
        return _valid_arguments(value)
    return False

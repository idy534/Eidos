from __future__ import annotations

from dataclasses import dataclass
import threading

from eidos_runtime.model.client import ModelResponse, ModelToolCall
from eidos_runtime.extensions.skills import SkillCreation
from eidos_runtime.tools.registry import StepToolSnapshot, ToolRegistry
from eidos_runtime.tools.workspace import FileChange


@dataclass(frozen=True)
class ToolValidationResult:
    tool_calls: tuple[ModelToolCall, ...]
    error_code: str | None = None


@dataclass(frozen=True)
class ToolDispatchPlan:
    requires_approval: bool
    execution_kind: str

    @property
    def is_shell(self) -> bool:
        return self.execution_kind == "shell"

    @property
    def is_external(self) -> bool:
        return self.execution_kind == "external"

    @property
    def is_eidos_state(self) -> bool:
        return self.execution_kind == "eidos_state"

    @property
    def is_network_eidos_state(self) -> bool:
        return self.execution_kind == "network_eidos_state"


class ToolDispatcher:
    """Owns model ToolCall validation and batch invariants for RuntimeEngine."""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def validate(
        self,
        response: ModelResponse,
        available_names: tuple[str, ...] | None = None,
    ) -> ToolValidationResult:
        if not isinstance(response, ModelResponse):
            return ToolValidationResult((), "invalid_response")
        if not response.text and not response.tool_calls:
            return ToolValidationResult((), "empty_response")
        if len(response.tool_calls) > 16:
            return ToolValidationResult((), "too_many_tool_calls")
        provider_ids: set[str] = set()
        effective_calls: list[ModelToolCall] = []
        for call in response.tool_calls:
            entry = self._registry.get(call.name) if isinstance(call, ModelToolCall) else None
            if available_names is not None and isinstance(call, ModelToolCall) and call.name not in available_names:
                entry = None
            effective = (
                entry.adapter.effective_arguments(call.arguments)
                if entry is not None
                else None
            )
            if (
                not isinstance(call, ModelToolCall)
                or not isinstance(call.provider_call_id, str)
                or not 1 <= len(call.provider_call_id) <= 256
                or call.provider_call_id in provider_ids
                or not isinstance(call.name, str)
                or entry is None
                or effective is None
                or not _valid_arguments(call.arguments)
            ):
                return ToolValidationResult((), "invalid_tool_call")
            provider_ids.add(call.provider_call_id)
            effective_calls.append(ModelToolCall(
                call.provider_call_id, call.name, effective
            ))
        if any(
            self._registry.get(call.name).spec.batch_policy == "single"  # type: ignore[union-attr]
            for call in effective_calls
        ) and len(response.tool_calls) != 1:
            return ToolValidationResult((), "invalid_tool_batch")
        return ToolValidationResult(tuple(effective_calls))

    def model_definitions(
        self, activated_names: tuple[str, ...] = ()
    ) -> list[dict[str, object]]:
        return self._registry.model_definitions(activated_names)

    def snapshot(
        self, activated_names: tuple[str, ...] = ()
    ) -> StepToolSnapshot:
        return self._registry.snapshot(activated_names=activated_names)

    def provenance(self, tool_name: str) -> dict[str, object] | None:
        entry = self._registry.get(tool_name)
        return (
            entry.provenance.model_dump(
                mode="json", by_alias=True, exclude_none=True
            )
            if entry is not None else None
        )

    def plan(self, call: ModelToolCall) -> ToolDispatchPlan:
        """Classify the validated call without exposing ToolExecutor metadata."""
        entry = self._registry.get(call.name)
        if entry is None:
            return ToolDispatchPlan(False, "unavailable")
        return ToolDispatchPlan(entry.spec.approval_required, entry.adapter.execution_kind)

    def execute_read_only(
        self, call: ModelToolCall, cancel: threading.Event
    ) -> dict[str, object]:
        """Execute only the tools whose existing spec has no side effect."""
        entry = self._registry.get(call.name)
        if entry is None:
            return _unavailable(call.name)
        return entry.adapter.execute(call.arguments, cancel)

    def execute_external(
        self, call: ModelToolCall, cancel: threading.Event
    ) -> dict[str, object]:
        entry = self._registry.get(call.name)
        if entry is None or entry.adapter.execution_kind != "external":
            return _unavailable(call.name)
        return entry.adapter.execute(call.arguments, cancel)

    def external_approval_details(self, tool_name: str) -> dict[str, object]:
        entry = self._registry.get(tool_name)
        if entry is None:
            return {}
        adapter = entry.adapter
        connection = getattr(adapter, "connection", None)
        config = getattr(connection, "config", None)
        return {
            "provenance": self.provenance(tool_name),
            "permissionProfile": getattr(config, "permission_profile", None),
            "timeoutSeconds": entry.spec.timeout_seconds,
            "envNames": list(getattr(config, "env_names", ())),
        }

    def consume_activations(self, tool_name: str) -> tuple[str, ...]:
        entry = self._registry.get(tool_name)
        consume = getattr(entry.adapter, "consume_activations", None) if entry else None
        return consume() if consume is not None else ()

    def prepare_file_change(
        self, tool_name: str, arguments: dict[str, object], cancel: threading.Event
    ) -> FileChange | dict[str, object]:
        entry = self._registry.get(tool_name)
        prepare = getattr(entry.adapter, "prepare_file_change", None) if entry else None
        return prepare(arguments, cancel) if prepare else _unavailable(tool_name)

    def commit_file_change(
        self, tool_name: str, change: FileChange, cancel: threading.Event
    ) -> dict[str, object]:
        entry = self._registry.get(tool_name)
        commit = getattr(entry.adapter, "commit_file_change", None) if entry else None
        return commit(change, cancel) if commit else _unavailable(tool_name)

    def prepare_eidos_state(
        self, tool_name: str, arguments: dict[str, object], cancel: threading.Event
    ) -> SkillCreation | dict[str, object]:
        entry = self._registry.get(tool_name)
        prepare = getattr(entry.adapter, "prepare_eidos_state", None) if entry else None
        return prepare(arguments, cancel) if prepare else _unavailable(tool_name)

    def commit_eidos_state(
        self, tool_name: str, change: SkillCreation, cancel: threading.Event
    ) -> dict[str, object]:
        entry = self._registry.get(tool_name)
        commit = getattr(entry.adapter, "commit_eidos_state", None) if entry else None
        return commit(change, cancel) if commit else _unavailable(tool_name)

    def network_approval_details(
        self, tool_name: str, arguments: dict[str, object]
    ) -> dict[str, object]:
        entry = self._registry.get(tool_name)
        details = getattr(entry.adapter, "network_approval_details", None) if entry else None
        return details(arguments) if details else {}

    def download_eidos_state(
        self, tool_name: str, arguments: dict[str, object], cancel: threading.Event
    ) -> SkillCreation | dict[str, object]:
        entry = self._registry.get(tool_name)
        download = getattr(entry.adapter, "download_eidos_state", None) if entry else None
        return download(arguments, cancel) if download else _unavailable(tool_name)

    def prepare_shell(self, tool_name: str, cwd: str, cancel: threading.Event):
        entry = self._registry.get(tool_name)
        prepare = getattr(entry.adapter, "prepare_shell", None) if entry else None
        if prepare is None:
            raise RuntimeError("tool_unavailable")
        return prepare(cwd, cancel)

    @property
    def workspace(self):
        for entry in self._registry.entries:
            workspace = getattr(entry.adapter, "workspace", None)
            if workspace is not None:
                return workspace
        raise RuntimeError("workspace_unavailable")


def _unavailable(tool_name: str) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "toolContractVersion": 1,
        "toolName": tool_name,
        "outcome": "unavailable",
        "code": "tool_unavailable",
        "summary": "Tool is unavailable",
        "data": {},
        "sideEffectsMayExist": False,
        "reconciliationRequired": False,
    }


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

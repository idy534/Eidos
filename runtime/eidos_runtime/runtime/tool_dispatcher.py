from __future__ import annotations

from dataclasses import dataclass

from eidos_runtime.model.client import ModelResponse, ModelToolCall, ModelToolDefinition
from eidos_runtime.tools.registry import (
    StepToolBinding,
    StepToolSnapshot,
    ToolDescriptor,
    ToolRegistry,
)
from eidos_runtime.tools.contracts import ToolResultProjection


@dataclass(frozen=True)
class ToolValidationResult:
    tool_calls: tuple[ModelToolCall, ...]
    error_code: str | None = None


@dataclass(frozen=True)
class ToolDispatchPlan:
    binding: StepToolBinding | None

    @property
    def descriptor(self) -> ToolDescriptor | None:
        return self.binding.descriptor if self.binding is not None else None

    @property
    def requires_approval(self) -> bool:
        return bool(
            self.descriptor
            and self.descriptor.execution_policy
            and self.descriptor.execution_policy.approval_required
        )

    @property
    def timeout_seconds(self) -> int:
        return (
            self.descriptor.execution_policy.timeout_seconds
            if self.descriptor is not None
            and self.descriptor.execution_policy is not None
            else 600
        )

    @property
    def side_effect(self) -> str:
        return (
            self.descriptor.execution_policy.side_effect
            if self.descriptor is not None
            and self.descriptor.execution_policy is not None
            else "external"
        )

    @property
    def contract_hash(self) -> str | None:
        return (
            self.binding.contract_fingerprint
            if self.binding is not None else None
        )

    @property
    def is_shell(self) -> bool:
        return self.side_effect == "shell"

    @property
    def is_external(self) -> bool:
        return self.side_effect == "external"

    @property
    def is_eidos_state(self) -> bool:
        return self.side_effect == "eidos_state"

    @property
    def is_network_eidos_state(self) -> bool:
        return bool(
            self.descriptor and self.descriptor.spec.name == "skill_install"
        )


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
        if (
            response.text
            and not response.tool_calls
            and _contains_provider_control_syntax(response.text)
        ):
            return ToolValidationResult((), "provider_control_syntax")
        if len(response.tool_calls) > 16:
            return ToolValidationResult((), "too_many_tool_calls")
        provider_ids: set[str] = set()
        effective_calls: list[ModelToolCall] = []
        for call in response.tool_calls:
            entry = self._registry.get(call.name) if isinstance(call, ModelToolCall) else None
            if available_names is not None and isinstance(call, ModelToolCall) and call.name not in available_names:
                entry = None
            contract = (
                entry.validate_arguments(call.arguments)
                if entry is not None
                else None
            )
            effective = (
                contract.normalized_arguments
                if contract is not None and contract.valid
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
                return ToolValidationResult(
                    (),
                    (
                        "TOOL_ARGUMENT_CONTRACT_VIOLATION"
                        if entry is not None and (
                            contract is None or not contract.valid
                        )
                        else "invalid_tool_call"
                    ),
                )
            provider_ids.add(call.provider_call_id)
            effective_calls.append(ModelToolCall(
                call.provider_call_id, call.name, effective
            ))
        # Batch policy controls runtime scheduling, not how many calls a model may
        # return in one response. Mixed or side-effecting batches are serialized by
        # ToolCallRuntime; only an all-read parallel-safe batch is run concurrently.
        return ToolValidationResult(tuple(effective_calls))

    def model_definitions(
        self, activated_names: tuple[str, ...] = ()
    ) -> tuple[ModelToolDefinition, ...]:
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

    def plan(
        self,
        call: ModelToolCall,
        expected_binding: StepToolBinding | str | None = None,
    ) -> ToolDispatchPlan:
        """Bind the call to the exact immutable descriptor advertised for its Step."""
        if isinstance(expected_binding, StepToolBinding):
            binding = (
                expected_binding
                if expected_binding.tool_name == call.name
                else None
            )
        else:
            entry = self._registry.get(call.name)
            expected_hash = (
                expected_binding if isinstance(expected_binding, str) else None
            )
            binding = (
                StepToolBinding(call.name, entry.contract_fingerprint, entry)
                if entry is not None
                and (
                    expected_hash is None
                    or entry.contract_fingerprint == expected_hash
                )
                else None
            )
        return ToolDispatchPlan(binding)

    def validate_execution(self, call: ModelToolCall, plan: ToolDispatchPlan) -> bool:
        validation = (
            plan.descriptor.validate_arguments(call.arguments)
            if plan.descriptor is not None
            else None
        )
        return (
            plan.binding is not None
            and plan.descriptor is not None
            and plan.binding.tool_name == call.name
            and plan.binding.contract_fingerprint
            == plan.descriptor.contract_fingerprint
            and validation is not None
            and validation.valid
            and validation.normalized_arguments == call.arguments
        )

    def validate_result(
        self, tool_name: str, result: object
    ) -> dict[str, object]:
        entry = self._registry.get(tool_name)
        if entry is None:
            raise ValueError("tool_contract_unavailable")
        validated = entry.validate_result(result)
        if validated.get("toolName") != tool_name:
            raise ValueError("tool_result_name_mismatch")
        return validated

    def project_result(
        self, tool_name: str, result: dict[str, object]
    ) -> ToolResultProjection:
        entry = self._registry.get(tool_name)
        if entry is None:
            raise ValueError("tool_contract_unavailable")
        assert entry.projector is not None
        return entry.projector.project(entry, result)

    def is_parallel_read_batch(self, calls: tuple[ModelToolCall, ...]) -> bool:
        return len(calls) > 1 and all(
            (entry := self._registry.get(call.name)) is not None
            and entry.spec.batch_policy == "parallel"
            and entry.execution_policy is not None
            and entry.execution_policy.concurrency.mode == "parallel_safe"
            for call in calls
        )


def _contains_provider_control_syntax(text: str) -> bool:
    return "<|DSML|" in text or "<｜DSML｜" in text


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
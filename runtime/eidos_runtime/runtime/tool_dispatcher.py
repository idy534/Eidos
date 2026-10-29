from __future__ import annotations

from dataclasses import dataclass, replace


from eidos_runtime.model.client import (
    CustomToolPayload,
    FunctionToolPayload,
    ModelResponse,
    ModelToolCall,
    ModelToolDefinitionLike,
)
from eidos_runtime.runtime.provider_control import contains_provider_control_syntax
from eidos_runtime.tools.registry import (
    StepToolBinding,
    StepToolSnapshot,
    ToolDescriptor,
    ToolArgumentValidationResult,
    ToolRegistry,
)
from eidos_runtime.tools.contracts import ToolResultProjection
from eidos_runtime.runtime.protocol_diagnostics import (
    ProtocolDiagnostic,
    argument_summary,
)


@dataclass(frozen=True)
class ToolValidationResult:
    tool_calls: tuple[ModelToolCall, ...]
    error_code: str | None = None
    protocol_diagnostic: ProtocolDiagnostic | None = None


@dataclass(frozen=True)
class ToolDispatchPlan:
    binding: StepToolBinding | None
    activation_required: bool = False
    invocation_side_effect: str | None = None

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
        if self.activation_required:
            return "none"
        if self.invocation_side_effect is not None:
            return self.invocation_side_effect
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
        tool_set_hash: str | None = None,
    ) -> ToolValidationResult:
        if not isinstance(response, ModelResponse):
            return ToolValidationResult(
                (),
                "invalid_response",
                ProtocolDiagnostic(
                    stage="response_validation", code="invalid_response"
                ),
            )
        if not response.text and not response.tool_calls:
            # Empty output is a runtime liveness condition rather than a malformed
            # ToolCall protocol. Keep it on the dedicated empty-response path.
            return ToolValidationResult(())
        if response.text and contains_provider_control_syntax(response.text):
            return ToolValidationResult(
                (),
                "provider_control_syntax",
                ProtocolDiagnostic(
                    stage="response_validation", code="provider_control_syntax"
                ),
            )
        provider_ids: set[str] = set()
        effective_calls: list[ModelToolCall] = []
        for call in response.tool_calls:
            registry_entry = (
                self._registry.get(call.name)
                if isinstance(call, ModelToolCall) else None
            )
            entry = registry_entry
            if (
                available_names is not None
                and isinstance(call, ModelToolCall)
                and call.name not in available_names
                and (entry is None or entry.spec.visibility != "deferred")
            ):
                entry = None
            if isinstance(call, ModelToolCall) and isinstance(call.provider_call_id, str) and len(call.provider_call_id) > 256:
                return ToolValidationResult((), "request_capacity", _tool_validation_diagnostic(
                    code="request_capacity", tool_call_index=len(effective_calls),
                    tool_call_count=len(response.tool_calls), call=call, entry=entry,
                    contract=None, tool_set_hash=tool_set_hash,
                ))
            if (
                not isinstance(call, ModelToolCall)
                or not isinstance(call.provider_call_id, str)
                or not 1 <= len(call.provider_call_id) <= 256
                or call.provider_call_id in provider_ids
                or not isinstance(call.name, str)
                or entry is None
            ):
                return ToolValidationResult(
                    (),
                    "invalid_tool_call",
                    _tool_validation_diagnostic(
                        code="invalid_tool_call",
                        tool_call_index=len(effective_calls),
                        tool_call_count=len(response.tool_calls),
                        call=call,
                        entry=entry,
                        contract=None,
                        tool_set_hash=tool_set_hash,
                    ),
                )
            contract = None
            effective_payload = None
            if entry.spec.input_kind == "function":
                if isinstance(call.payload, FunctionToolPayload):
                    contract = entry.validate_arguments(call.payload.arguments)
                    effective_payload = (
                        FunctionToolPayload(
                            arguments=contract.normalized_arguments
                        )
                        if contract.valid
                        and contract.normalized_arguments is not None
                        else call.payload
                    )
            elif entry.spec.input_kind == "custom":
                if isinstance(call.payload, CustomToolPayload):
                    contract = entry.validate_custom_input(call.payload.input)
                    effective_payload = (
                        CustomToolPayload(input=contract.normalized_input)
                        if contract.valid and contract.normalized_input is not None
                        else call.payload
                    )
            if effective_payload is None:
                contract = ToolArgumentValidationResult(
                    valid=False,
                    code="TOOL_ARGUMENT_CONTRACT_VIOLATION",
                    reason_code="tool_input_kind_mismatch",
                )
                error_code = "TOOL_ARGUMENT_CONTRACT_VIOLATION"
                return ToolValidationResult(
                    (),
                    error_code,
                    _tool_validation_diagnostic(
                        code=error_code,
                        tool_call_index=len(effective_calls),
                        tool_call_count=len(response.tool_calls),
                        call=call,
                        entry=entry,
                        contract=contract,
                        tool_set_hash=tool_set_hash,
                    ),
                )
            provider_ids.add(call.provider_call_id)
            effective_calls.append(ModelToolCall(
                call.provider_call_id, call.name, effective_payload,
                raw_payload=call.raw_payload or call.payload,
            ))
        # Batch policy controls runtime scheduling, not how many calls a model may
        # return in one response. Mixed or side-effecting batches are serialized by
        # ToolCallRuntime; only an all-read parallel-safe batch is run concurrently.
        return ToolValidationResult(tuple(effective_calls))

    def model_definitions(
        self, activated_names: tuple[str, ...] = ()
    ) -> tuple[ModelToolDefinitionLike, ...]:
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
        *,
        activation_required: bool = False,
    ) -> ToolDispatchPlan:
        """Bind execution to its Step; inactive descriptors only format feedback."""
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
        effect = None
        if binding and not activation_required:
            from eidos_runtime.tools.automation import AutomationToolRuntime
            from pydantic import ValidationError
            entry = binding.descriptor
            if entry.provenance.kind == "builtin" and entry.provenance.source_id == "eidos.automation" and isinstance(entry.runtime, AutomationToolRuntime):
                try:
                    if entry.validate_arguments(call.arguments).valid:
                        effect = entry.runtime.invocation_effect(call.arguments)
                except (ValueError, ValidationError):
                    pass
        return ToolDispatchPlan(binding, activation_required=activation_required, invocation_side_effect=effect)

    def plan_pending_approval(self, call: ModelToolCall, expected_hash: str) -> ToolDispatchPlan:
        """Accept only the approval-flag change for the new read-only policy."""
        plan = self.plan(call, expected_hash)
        if plan.descriptor is not None:
            return plan
        entry = self._registry.get(call.name)
        if (
            entry is None or entry.execution_policy is None
            or entry.spec.approval_required or entry.execution_policy.approval_required
            or entry.spec.side_effect != "external"
            or not (
                entry.provenance.kind == "mcp"
                or (entry.provenance.kind == "builtin"
                    and entry.provenance.source_id == "eidos.web-search")
            )
        ):
            return plan
        legacy = replace(entry, execution_policy=entry.execution_policy.model_copy(
            update={"approval_required": True},
        ))
        if legacy.contract_fingerprint != expected_hash:
            return plan
        # The pending request still goes through its original approval and
        # evidence checks. Every contract field except this flag must match.
        return self.plan(call, entry.contract_fingerprint)

    def validate_execution(
        self, call: ModelToolCall, plan: ToolDispatchPlan
    ) -> ToolArgumentValidationResult | None:
        if (
            plan.binding is None
            or plan.descriptor is None
            or plan.binding.tool_name != call.name
            or plan.binding.contract_fingerprint
            != plan.descriptor.contract_fingerprint
        ):
            return None
        if (
            plan.descriptor.spec.input_kind == "function"
            and isinstance(call.payload, FunctionToolPayload)
        ):
            validation = plan.descriptor.validate_arguments(call.payload.arguments)
            normalized = (
                FunctionToolPayload(arguments=validation.normalized_arguments)
                if validation.valid and validation.normalized_arguments is not None
                else None
            )
        elif (
            plan.descriptor.spec.input_kind == "custom"
            and isinstance(call.payload, CustomToolPayload)
        ):
            validation = plan.descriptor.validate_custom_input(call.payload.input)
            normalized = (
                CustomToolPayload(input=validation.normalized_input)
                if validation.valid and validation.normalized_input is not None
                else None
            )
        else:
            return None
        if validation.valid and normalized != call.payload:
            return None
        return validation

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

    def is_parallel_read_batch(
        self, calls: tuple[ModelToolCall, ...], available_names: tuple[str, ...] | None = None,
    ) -> bool:
        return len(calls) > 1 and all(self.is_parallel_read_call(call, available_names) for call in calls)

    def is_parallel_read_call(
        self, call: ModelToolCall, available_names: tuple[str, ...] | None = None,
    ) -> bool:
        return (
            (available_names is None or call.name in available_names)
            and (entry := self._registry.get(call.name)) is not None
            and isinstance(call.payload, FunctionToolPayload)
            and entry.spec.batch_policy == "parallel"
            and entry.execution_policy is not None
            and entry.execution_policy.concurrency.mode == "parallel_safe"
        )


def _tool_validation_diagnostic(
    *,
    code: str,
    tool_call_index: int,
    tool_call_count: int,
    call: object,
    entry: object,
    contract: ToolArgumentValidationResult | None,
    tool_set_hash: str | None,
) -> ProtocolDiagnostic:
    arguments = (
        call.arguments
        if isinstance(call, ModelToolCall)
        and isinstance(call.payload, FunctionToolPayload)
        else None
    )
    custom_input = (
        call.payload.input
        if isinstance(call, ModelToolCall)
        and isinstance(call.payload, CustomToolPayload)
        else None
    )
    if custom_input is None:
        argument_bytes, keys, types, truncated = argument_summary(arguments)
        input_bytes = None
    else:
        try:
            encoded_input = custom_input.encode("utf-8")
        except UnicodeEncodeError:
            argument_bytes, keys, types, truncated = None, (), {}, True
            input_bytes = None
        else:
            input_size = len(encoded_input)
            argument_bytes, keys, types, truncated = (
                None, (), {}, input_size > 1_024 * 1024
            )
            input_bytes = min(input_size, 1_024 * 1024)
    tool_name = call.name if isinstance(call, ModelToolCall) else None
    provider_call_id = (
        call.provider_call_id if isinstance(call, ModelToolCall) else None
    )
    contract_fingerprint = getattr(entry, "contract_fingerprint", None)
    return ProtocolDiagnostic(
        stage="tool_validation",
        code=code,
        tool_call_count=tool_call_count,
        tool_call_index=tool_call_index,
        tool_name=_bounded_text(tool_name, 128),
        provider_call_id=_bounded_text(provider_call_id, 256),
        tool_declared=entry is not None,
        tool_set_hash=tool_set_hash,
        contract_fingerprint=contract_fingerprint,
        validation_code=contract.reason_code if contract is not None else None,
        validation_path=contract.path if contract is not None else None,
        argument_bytes=argument_bytes,
        argument_keys=keys,
        argument_types=types,
        arguments_truncated=truncated,
        payload_kind=(call.payload.kind if isinstance(call, ModelToolCall) else None),
        input_bytes=input_bytes,
    )


def _bounded_text(value: object, limit: int) -> str | None:
    if not isinstance(value, str) or not 1 <= len(value) <= limit:
        return None
    return value

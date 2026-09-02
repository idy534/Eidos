from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum

from pydantic import ValidationError

from eidos_runtime.domain.execution import (
    ExecutionSegment,
    Item,
    ItemKind,
    ItemStatus,
    ModelAttempt,
    ModelAttemptStatus,
    SegmentStatus,
    Step,
    StepStatus,
)
from eidos_runtime.domain.run import Run, RunStatus
from eidos_runtime.domain.tool import (
    Approval,
    ApprovalKind,
    ApprovalStatus,
    DurableIntent,
    DurableIntentStatus,
    ToolAttempt,
    ToolAttemptStatus,
    ToolCall,
    ToolCallStatus,
)
from eidos_runtime.persistence.conversion import RowReader, RowValues
from eidos_runtime.persistence.errors import PersistenceCorruptionError


def _reader(row: RowValues | Mapping[str, object], record: str) -> RowReader:
    return RowReader(row, record=record)


def _enum(enum_type: type[StrEnum], value: str, *, record: str, field: str):
    try:
        return enum_type(value)
    except ValueError:
        raise PersistenceCorruptionError(
            "persistence_value_invalid", record=record, field=field
        ) from None


def _build(record: str, factory, values: dict[str, object]):
    try:
        return factory(**values)
    except ValidationError as error:
        location = error.errors(include_url=False)[0].get("loc", ())
        field = str(location[0]) if location else None
        raise PersistenceCorruptionError(
            "persistence_record_invalid", record=record, field=field
        ) from None


def run_from_row(row: RowValues | Mapping[str, object]) -> Run:
    values = _reader(row, "run")
    status = _enum(RunStatus, values.text("status"), record="run", field="status")
    return _build("run", Run, {
        "id": values.text("id"),
        "session_id": values.text("session_id"),
        "user_input": values.text("user_input"),
        "model_id": values.text("model_id"),
        "status": status,
        "model_step_count": values.integer("model_step_count"),
        "reconciliation_required": values.boolean("reconciliation_required"),
        "side_effects_may_exist": values.boolean("side_effects_may_exist"),
        "workspace_version": values.integer("workspace_version"),
        "created_at": values.integer("created_at"),
        "started_at": values.optional_integer("started_at"),
        "updated_at": values.integer("updated_at"),
        "completed_at": values.optional_integer("completed_at"),
        "error_code": values.optional_text("error_code"),
        "stop_reason": values.optional_text("stop_reason"),
        "cancel_requested_at": values.optional_integer("cancel_requested_at"),
        "cancel_completed_at": values.optional_integer("cancel_completed_at"),
    })


def execution_segment_from_row(
    row: RowValues | Mapping[str, object],
) -> ExecutionSegment:
    values = _reader(row, "execution_segment")
    return _build("execution_segment", ExecutionSegment, {
        "id": values.text("id"),
        "run_id": values.text("run_id"),
        "ordinal": values.integer("ordinal"),
        "status": _enum(SegmentStatus, values.text("status"), record="execution_segment", field="status"),
        "step_count": values.integer("step_count"),
        "effective_ms": values.integer("effective_ms"),
        "created_at": values.integer("created_at"),
        "started_at": values.optional_integer("started_at"),
        "completed_at": values.optional_integer("completed_at"),
    })


def step_from_row(row: RowValues | Mapping[str, object]) -> Step:
    values = _reader(row, "step")
    return _build("step", Step, {
        "id": values.text("id"),
        "run_id": values.text("run_id"),
        "segment_id": values.text("segment_id"),
        "ordinal": values.integer("ordinal"),
        "status": _enum(StepStatus, values.text("status"), record="step", field="status"),
        "observed_reconciliation_epoch": values.integer("observed_reconciliation_epoch"),
        "resolution_snapshot_id": values.text("resolution_snapshot_id"),
        "tool_snapshot_json": values.optional_json_text("tool_snapshot_json"),
        "tool_set_hash": values.optional_text("tool_set_hash"),
        "progress_signature_json": values.optional_json_text("progress_signature_json"),
        "created_at": values.integer("created_at"),
        "completed_at": values.optional_integer("completed_at"),
    })


def item_from_row(row: RowValues | Mapping[str, object]) -> Item:
    values = _reader(row, "item")
    return _build("item", Item, {
        "id": values.text("id"),
        "session_id": values.text("session_id"),
        "run_id": values.text("run_id"),
        "ordinal": values.integer("ordinal"),
        "model_step_index": values.optional_integer("model_step_index"),
        "kind": _enum(ItemKind, values.text("kind"), record="item", field="kind"),
        "status": _enum(ItemStatus, values.text("status"), record="item", field="status"),
        "content": values.optional_text("content"),
        "incomplete": values.boolean("incomplete"),
        "created_at": values.integer("created_at"),
        "completed_at": values.optional_integer("completed_at"),
    })


def tool_call_from_row(row: RowValues | Mapping[str, object]) -> ToolCall:
    values = _reader(row, "tool_call")
    approval_status_value = values.optional_text("approval_status")
    return _build("tool_call", ToolCall, {
        "id": values.text("id"),
        "item_id": values.text("item_id"),
        "model_step_index": values.integer("model_step_index"),
        "batch_order": values.integer("batch_order"),
        "provider_call_id": values.text("provider_call_id"),
        "tool_name": values.text("tool_name"),
        "status": _enum(ToolCallStatus, values.text("status"), record="tool_call", field="status"),
        "payload_kind": values.text("payload_kind"),
        "arguments_json": values.json_text("arguments_json"),
        "result_json": values.optional_json_text("result_json"),
        "model_result_json": values.optional_json_text("model_result_json"),
        "approval_status": (
            _enum(ApprovalStatus, approval_status_value, record="tool_call", field="approval_status")
            if approval_status_value is not None else None
        ),
        "approval_decision": values.optional_text("approval_decision"),
        "approval_feedback": values.optional_text("approval_feedback"),
        "approval_diff": values.optional_text("approval_diff"),
        "base_sha256": values.optional_text("base_sha256"),
        "provenance_json": values.optional_json_text("provenance_json"),
        "tool_set_hash": values.optional_text("tool_set_hash"),
        "started_at": values.integer("started_at"),
        "duration_ms": values.optional_integer("duration_ms"),
        "completed_at": values.optional_integer("completed_at"),
    })


def approval_from_row(row: RowValues | Mapping[str, object]) -> Approval:
    values = _reader(row, "approval")
    return _build("approval", Approval, {
        "id": values.text("id"),
        "tool_call_id": values.text("tool_call_id"),
        "run_id": values.text("run_id"),
        "item_id": values.text("item_id"),
        "status": _enum(ApprovalStatus, values.text("status"), record="approval", field="status"),
        "request_hash": values.text("request_hash"),
        "request_json": values.json_text("request_json"),
        "attempt_ordinal": values.integer("attempt_ordinal"),
        "approval_kind": _enum(ApprovalKind, values.text("approval_kind"), record="approval", field="approval_kind"),
        "decision": values.optional_text("decision"),
        "feedback": values.optional_text("feedback"),
        "created_at": values.integer("created_at"),
        "decided_at": values.optional_integer("decided_at"),
    })


def tool_attempt_from_row(row: RowValues | Mapping[str, object]) -> ToolAttempt:
    values = _reader(row, "tool_attempt")
    return _build("tool_attempt", ToolAttempt, {
        "id": values.text("id"),
        "tool_call_id": values.text("tool_call_id"),
        "ordinal": values.integer("ordinal"),
        "sandbox_type": values.text("sandbox_type"),
        "sandbox_requested": values.boolean("sandbox_requested"),
        "effective_permissions_json": values.json_text("effective_permissions_json"),
        "profile_hash": values.optional_text("profile_hash"),
        "escalation_reason": values.optional_text("escalation_reason"),
        "status": _enum(ToolAttemptStatus, values.text("status"), record="tool_attempt", field="status"),
        "started_at": values.integer("started_at"),
        "completed_at": values.optional_integer("completed_at"),
        "result_code": values.optional_text("result_code"),
    })


def durable_intent_from_row(
    row: RowValues | Mapping[str, object],
) -> DurableIntent:
    values = _reader(row, "durable_intent")
    return _build("durable_intent", DurableIntent, {
        "id": values.text("id"),
        "run_id": values.text("run_id"),
        "tool_call_id": values.text("tool_call_id"),
        "execution_nonce": values.text("execution_nonce"),
        "arguments_hash": values.text("arguments_hash"),
        "preconditions_json": values.json_text("preconditions_json"),
        "status": _enum(DurableIntentStatus, values.text("status"), record="durable_intent", field="status"),
        "created_at": values.integer("created_at"),
        "reconciled_at": values.optional_integer("reconciled_at"),
    })


def model_attempt_from_row(
    row: RowValues | Mapping[str, object],
) -> ModelAttempt:
    values = _reader(row, "model_attempt")
    return _build("model_attempt", ModelAttempt, {
        "id": values.text("id"),
        "step_id": values.text("step_id"),
        "ordinal": values.integer("ordinal"),
        "status": _enum(ModelAttemptStatus, values.text("status"), record="model_attempt", field="status"),
        "provider_name": values.optional_text("provider_name"),
        "configured_provider_id": values.optional_text("configured_provider_id"),
        "resolved_model_name": values.optional_text("resolved_model_name"),
        "finish_reason": values.optional_text("finish_reason"),
        "provider_response_id": values.optional_text("provider_response_id"),
        "lease_id": values.optional_text("lease_id"),
        "wire_api": values.optional_text("wire_api"),
        "model_id": values.optional_text("model_id"),
        "request_timeout": values.optional_real("request_timeout"),
        "context_snapshot_id": values.optional_text("context_snapshot_id"),
        "retry_decision_json": values.optional_json_text("retry_decision_json"),
        "usage_json": values.optional_json_text("usage_json"),
        "error_code": values.optional_text("error_code"),
        "http_status": values.optional_integer("http_status"),
        "ttft_ms": values.optional_integer("ttft_ms"),
        "duration_ms": values.optional_integer("duration_ms"),
        "had_progress": values.boolean("had_progress"),
        "response_state": values.optional_text("response_state"),
        "phase": values.optional_text("phase"),
        "tool_call_count": values.integer("tool_call_count"),
        "response_text_sha256": values.optional_text("response_text_sha256"),
        "response_text_bytes": values.integer("response_text_bytes"),
        "protocol_diagnostics_json": values.optional_json_text(
            "protocol_diagnostics_json"
        ),
        "started_at": values.integer("started_at"),
        "completed_at": values.optional_integer("completed_at"),
    })

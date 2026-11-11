from __future__ import annotations

from eidos_runtime.domain.input_reference import InputReference

import json

from collections.abc import Mapping
from enum import StrEnum

from pydantic import ValidationError

from eidos_runtime.domain.approval_policy import ApprovalReview
from eidos_runtime.domain.execution import (
    Item,
    ItemKind,
    ItemStatus,
    Step,
    StepStatus,
)
from eidos_runtime.domain.run import Run, RunStatus
from eidos_runtime.domain.tool import (
    Approval,
    ApprovalKind,
    ApprovalStatus,
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
        "approval_mode": (values.optional_text("approval_mode") if "approval_mode" in values._columns else None) or "manual",
        "work_mode": (values.optional_text("work_mode") if "work_mode" in values._columns else None) or "execute",
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
        "termination": json.loads(values.optional_json_text("termination_json") or "null") if "termination_json" in values._columns else None,
        "cancel_requested_at": values.optional_integer("cancel_requested_at"),
        "cancel_completed_at": values.optional_integer("cancel_completed_at"),
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
        "references": tuple(InputReference.model_validate(value) for value in json.loads(values.optional_text("input_references_json") or "[]")),
        "content": values.optional_text("content"),
        "incomplete": values.boolean("incomplete"),
        "created_at": values.integer("created_at"),
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
        "review": ApprovalReview.model_validate_json(values.text("review_json")) if values.optional_text("review_json") else None,
        "attempt_ordinal": values.integer("attempt_ordinal"),
        "approval_kind": _enum(ApprovalKind, values.text("approval_kind"), record="approval", field="approval_kind"),
        "decision": values.optional_text("decision"),
        "feedback": values.optional_text("feedback"),
        "created_at": values.integer("created_at"),
        "decided_at": values.optional_integer("decided_at"),
    })

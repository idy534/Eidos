from __future__ import annotations

import json
import sqlite3

from eidos_runtime.context.facts import CompactSummary
from eidos_runtime.model.client import ModelUsage
from eidos_runtime.protocol.schemas import (
    ItemDto,
    RunDto,
    StepResolutionReviewDto,
)
from eidos_runtime.runtime.resolution import (
    RuleResolutionSnapshot,
    StepResolutionSnapshot,
)


MAX_SNAPSHOT_TEXT_BYTES = 192 * 1024
MAX_SNAPSHOT_ARGUMENT_STRING_BYTES = 16 * 1024
MAX_SNAPSHOT_INCLUDE_GLOBS = 32
MAX_SNAPSHOT_INCLUDE_GLOB_BYTES = 512

def _plugin_from_row(row: sqlite3.Row) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "id": row["id"],
        "name": row["name"],
        "version": row["version"],
        "description": row["description"],
        "contentHash": row["content_hash"],
        "enabled": bool(row["enabled"]),
        "status": row["status"],
        "installedAt": row["installed_at"],
        "updatedAt": row["updated_at"],
    }

def _load_json_object(value: object) -> dict[str, object] | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None

def _snapshot_item(
    row: sqlite3.Row, tool_row: sqlite3.Row | None
) -> dict[str, object]:
    item = _item_from_row(row, tool_row)
    content = item.get("content")
    if isinstance(content, str):
        item["content"] = _truncate_snapshot_text(content)
    tool_call = item.get("toolCall")
    if isinstance(tool_call, dict):
        projected = dict(tool_call)
        display_arguments = _snapshot_display_arguments(tool_call)
        if display_arguments is None:
            projected.pop("argumentsJson", None)
        else:
            projected["argumentsJson"] = display_arguments
        projected.pop("approvalDiff", None)
        result = projected.get("resultJson")
        if isinstance(result, str):
            projected["resultJson"] = _truncate_snapshot_text(result)
        item["toolCall"] = projected
    return item

def _snapshot_display_arguments(tool_call: dict[str, object]) -> str | None:
    fields_by_tool = {
        "list_files": ("path", "maxDepth", "maxEntries"),
        "read_file": ("path",),
        "read_file_range": ("path", "startLine", "endLine"),
        "search_text": (
            "query", "path", "regex", "maxResults", "includeGlobs"
        ),
        "write_file": ("path",),
        "apply_patch": ("path",),
        "delete_file": ("path",),
        "run_shell": ("command", "cwd", "timeoutSeconds"),
    }
    fields = fields_by_tool.get(tool_call.get("toolName"))
    arguments = _load_json_object(tool_call.get("argumentsJson"))
    if fields is None or arguments is None:
        return None
    projected: dict[str, object] = {}
    for field in fields:
        if field not in arguments:
            continue
        value = arguments[field]
        if field == "includeGlobs":
            if not isinstance(value, (list, tuple)):
                continue
            globs = list(value[:MAX_SNAPSHOT_INCLUDE_GLOBS])
            if not all(
                isinstance(glob, str)
                and len(glob.encode("utf-8")) <= MAX_SNAPSHOT_INCLUDE_GLOB_BYTES
                for glob in globs
            ):
                continue
            projected[field] = globs
        elif field == "regex":
            if isinstance(value, bool):
                projected[field] = value
        elif field in {"maxDepth", "maxEntries", "startLine", "endLine", "maxResults", "timeoutSeconds"}:
            if isinstance(value, int) and not isinstance(value, bool):
                projected[field] = value
        elif isinstance(value, str):
            encoded = value.encode("utf-8")
            projected[field] = encoded[:MAX_SNAPSHOT_ARGUMENT_STRING_BYTES].decode(
                "utf-8", errors="ignore"
            )
    return json.dumps(
        projected,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _step_resolution_review(
    row: sqlite3.Row,
) -> dict[str, object]:
    step = StepResolutionSnapshot.model_validate_json(row["step_snapshot_json"])
    rules = RuleResolutionSnapshot.model_validate_json(row["rule_snapshot_json"])
    return StepResolutionReviewDto.model_validate({
        "id": step.id,
        "stepId": row["step_id"],
        "runId": row["run_id"],
        "stepOrdinal": row["ordinal"],
        "snapshotHash": step.snapshot_hash,
        "requestHash": step.final_request_hash,
        "ruleSnapshotId": rules.id,
        "ruleSnapshotHash": rules.snapshot_hash,
        "rules": [
            {
                **rule.model_dump(mode="json", exclude={"content"}),
            }
            for rule in rules.rules
        ],
        "shadowed": [
            candidate.model_dump(mode="json")
            for candidate in rules.shadowed
        ],
        "warnings": [
            warning.model_dump(mode="json")
            for warning in rules.warnings
        ],
    }).to_json_value()

def _truncate_snapshot_text(value: str) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= MAX_SNAPSHOT_TEXT_BYTES:
        return value
    marker = "\n…[history truncated]"
    budget = MAX_SNAPSHOT_TEXT_BYTES - len(marker.encode("utf-8"))
    prefix = encoded[:budget]
    while True:
        try:
            return prefix.decode("utf-8") + marker
        except UnicodeDecodeError as error:
            prefix = prefix[: error.start]

def _compact_summary_from_row(row: sqlite3.Row | None) -> CompactSummary | None:
    if row is None:
        return None
    metadata: dict[str, object] = {}
    if "summary_metadata_json" in row.keys():
        try:
            parsed = json.loads(row["summary_metadata_json"] or "{}")
        except json.JSONDecodeError:
            parsed = {}
        if isinstance(parsed, dict):
            metadata = parsed

    def metadata_tuple(name: str) -> tuple[str, ...]:
        value = metadata.get(name, ())
        if not isinstance(value, list):
            return ()
        return tuple(item for item in value if isinstance(item, str))

    return CompactSummary(
        task_goal=str(row["task_goal"]),
        constraints=tuple(json.loads(row["constraints_json"])),
        completed_actions=tuple(json.loads(row["completed_actions_json"])),
        workspace_changes=tuple(json.loads(row["workspace_changes_json"])),
        important_facts=tuple(json.loads(row["important_facts_json"])),
        unresolved_problems=tuple(json.loads(row["unresolved_problems_json"])),
        next_actions=tuple(json.loads(row["next_actions_json"])),
        source_item_ids=tuple(json.loads(row["source_item_ids_json"])),
        important_decisions=metadata_tuple("important_decisions"),
        failed_attempts=metadata_tuple("failed_attempts"),
        pending_approvals=metadata_tuple("pending_approvals"),
        uncertain_side_effects=metadata_tuple("uncertain_side_effects"),
    )

def _run_from_row(
    row: sqlite3.Row, *, include_user_input: bool = True
) -> dict[str, object]:
    run: dict[str, object] = {
        "id": row["id"],
        "sessionId": row["session_id"],
        "modelId": row["model_id"],
        "status": row["status"],
        "modelStepCount": row["model_step_count"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }
    allowed_actions = {
        "queued": ["cancel"],
        "running": ["cancel"],
        "waiting_approval": ["approve", "reject", "cancel"],
        "finalizing": ["cancel"],
    }.get(row["status"], [])
    if allowed_actions:
        run["allowedActions"] = allowed_actions
    if row["started_at"] is not None:
        run["startedAt"] = row["started_at"]
    if include_user_input:
        run["userInput"] = row["user_input"]
    if row["completed_at"] is not None:
        run["completedAt"] = row["completed_at"]
    if row["error_code"] is not None:
        run["errorCode"] = row["error_code"]
    if "cancel_requested_at" in row.keys() and row["cancel_requested_at"] is not None:
        run["cancelRequestedAt"] = row["cancel_requested_at"]
    if "cancel_completed_at" in row.keys() and row["cancel_completed_at"] is not None:
        run["cancelCompletedAt"] = row["cancel_completed_at"]
    if "cancel_failure_code" in row.keys() and row["cancel_failure_code"] is not None:
        run["cancelFailureCode"] = row["cancel_failure_code"]
    if "stop_reason" in row.keys() and row["stop_reason"] is not None:
        run["stopReason"] = row["stop_reason"]
    if "side_effects_may_exist" in row.keys():
        run["sideEffectsMayExist"] = bool(row["side_effects_may_exist"])
    if (
        "extension_snapshot_json" in row.keys()
        and row["extension_snapshot_json"] is not None
    ):
        snapshot = json.loads(row["extension_snapshot_json"])
        if isinstance(snapshot, dict):
            run["extensionSnapshot"] = snapshot
    if "activated_tools_json" in row.keys() and row["activated_tools_json"] is not None:
        activated = json.loads(row["activated_tools_json"])
        if isinstance(activated, list):
            run["activatedTools"] = activated
    return RunDto.model_validate(run).to_json_value()

def _model_attempt_from_row(row: sqlite3.Row) -> dict[str, object]:
    usage = (
        ModelUsage.model_validate_json(row["usage_json"])
        if row["usage_json"] is not None else None
    )
    return {
        "id": row["id"],
        "stepId": row["step_id"],
        "ordinal": row["ordinal"],
        "status": row["status"],
        "providerName": row["provider_name"],
        "resolvedModelName": row["resolved_model_name"],
        "finishReason": row["finish_reason"],
        "providerResponseId": row["provider_response_id"],
        "leaseId": row["lease_id"],
        "wireApi": row["wire_api"],
        "modelId": row["model_id"],
        "requestTimeout": row["request_timeout"],
        "contextSnapshotId": row["context_snapshot_id"],
        "retryDecision": (
            json.loads(row["retry_decision_json"])
            if row["retry_decision_json"] is not None else None
        ),
        "usage": usage,
        "errorCode": row["error_code"],
        "httpStatus": row["http_status"],
        "ttftMs": row["ttft_ms"],
        "durationMs": row["duration_ms"],
        "hadProgress": bool(row["had_progress"]),
        "startedAt": row["started_at"],
        "completedAt": row["completed_at"],
    }

def _item_from_row(
    row: sqlite3.Row, tool_row: sqlite3.Row | None
) -> dict[str, object]:
    item: dict[str, object] = {
        "id": row["id"],
        "sessionId": row["session_id"],
        "runId": row["run_id"],
        "ordinal": row["ordinal"],
        "kind": row["kind"],
        "status": row["status"],
        "createdAt": row["created_at"],
    }
    if row["model_step_index"] is not None:
        item["modelStepIndex"] = row["model_step_index"]
    if row["content"] is not None:
        item["content"] = row["content"]
    if "incomplete" in row.keys() and row["incomplete"]:
        item["incomplete"] = True
    if row["completed_at"] is not None:
        item["completedAt"] = row["completed_at"]
    if tool_row is not None:
        tool_call: dict[str, object] = {
            "id": tool_row["id"],
            "itemId": tool_row["item_id"],
            "modelStepIndex": tool_row["model_step_index"],
            "batchOrder": tool_row["batch_order"],
            "providerCallId": tool_row["provider_call_id"],
            "toolName": tool_row["tool_name"],
            "status": tool_row["status"],
            "argumentsJson": tool_row["arguments_json"],
            "startedAt": tool_row["started_at"],
        }
        public_result = (
            tool_row["ui_result_json"]
            if "ui_result_json" in tool_row.keys()
            and tool_row["ui_result_json"] is not None
            else tool_row["result_json"]
        )
        if public_result is not None:
            tool_call["resultJson"] = public_result
        if tool_row["completed_at"] is not None:
            tool_call["completedAt"] = tool_row["completed_at"]
        if tool_row["approval_status"] is not None:
            tool_call["approvalStatus"] = tool_row["approval_status"]
        if tool_row["approval_decision"] is not None:
            tool_call["approvalDecision"] = tool_row["approval_decision"]
        if tool_row["approval_feedback"] is not None:
            tool_call["approvalFeedback"] = tool_row["approval_feedback"]
        if tool_row["approval_diff"] is not None:
            tool_call["approvalDiff"] = tool_row["approval_diff"]
        if tool_row["base_sha256"] is not None:
            tool_call["baseSha256"] = tool_row["base_sha256"]
        if "provenance_json" in tool_row.keys() and tool_row["provenance_json"]:
            provenance = json.loads(tool_row["provenance_json"])
            if isinstance(provenance, dict):
                tool_call["provenance"] = provenance
        if "tool_set_hash" in tool_row.keys() and tool_row["tool_set_hash"]:
            tool_call["toolSetHash"] = tool_row["tool_set_hash"]
        item["toolCall"] = tool_call
    return ItemDto.model_validate(item).to_json_value()


def _json_bytes(value: object) -> int:
    return len(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )

def _bounded_canonical_json(value: object, *, code: str) -> str:
    try:
        encoded = json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise ValueError(code) from None
    if not isinstance(value, dict) or len(encoded) > 256 * 1024:
        raise ValueError(code)
    return encoded.decode("utf-8")

def _json_tuple(values: tuple[str, ...]) -> str:
    return json.dumps(values, ensure_ascii=False, separators=(",", ":"))

from __future__ import annotations

import json
import platform
import time

from pydantic import BaseModel, ConfigDict

from eidos_runtime.context.budget import (
    ContextBudget,
    estimate_model_request_budget,
)
from eidos_runtime.context.facts import ContextFacts
from eidos_runtime.context.repository import RunRepositoryContext
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.model.client import ModelContextItem, ModelToolDefinition
from eidos_runtime.model.instructions import InstructionResolver, StepPermissionPolicy
from eidos_runtime.model.prompts import ResolvedInstructions
from eidos_runtime.extensions.skills import RetainedContextSection
from eidos_runtime.runtime.resolution import RuleResolutionSnapshot


class ContextBuild(BaseModel):
    model_config = ConfigDict(
        frozen=True, extra="forbid", strict=True, arbitrary_types_allowed=True
    )

    model_context: tuple[ModelContextItem, ...]
    instructions: ResolvedInstructions
    budget: ContextBudget
    facts: ContextFacts


class ContextBuilder:
    """Projects normalized SQLite facts into one budgeted model payload."""

    def __init__(
        self,
        store: SessionStore,
    ) -> None:
        self.store = store
        # A provider's input usage describes the request just completed. Keep
        # the local estimate from the request that produced that usage so the
        # next projection can be calibrated without treating the old usage as
        # the next request's size.
        self._last_estimated_input_tokens: int | None = None
        self._last_provider_usage = None
        self._provider_calibration_estimate: int | None = None

    def build(
        self,
        run_id: str,
        *,
        tool_definitions: tuple[ModelToolDefinition, ...] = (),
        retained_context: tuple[RetainedContextSection, ...] = (),
        selected_skill_context: tuple[RetainedContextSection, ...] = (),
        extra_context: tuple[ModelContextItem, ...] = (),
        rule_resolution_snapshot: RuleResolutionSnapshot | None = None,
        step_policy: StepPermissionPolicy | None = None,
        repository_context: RunRepositoryContext | None = None,
        projectless: bool = False,
    ) -> ContextBuild:
        facts = self.store.context_projection_facts(run_id)
        profile = self.store.read_model_profile(run_id)
        instructions = InstructionResolver().resolve(
            rule_snapshot=rule_resolution_snapshot,
            selected_skill_context=selected_skill_context,
            step_policy=step_policy,
        )
        source_ids = set(
            facts.compact_summary.source_item_ids if facts.compact_summary else ()
        )
        # --- User-context layers (Project Rules, Skills) ---
        # These are injected as user messages BEFORE workspace-environment so that
        # the current user request (which comes later in history) has higher priority.
        user_context_messages: list[ModelContextItem] = []
        for layer in instructions.user_context_layers:
            user_context_messages.append({
                "type": "user",
                "sectionId": layer.id,
                "content": layer.content,
            })

        context: list[ModelContextItem] = [*user_context_messages]
        if not projectless:
            workspace = self.store.workspace_for_run(run_id)
            context.append({
                "type": "user",
                "sectionId": "workspace-environment",
                "version": str(facts.workspace_version),
                "content": "Workspace/environment context: " + json.dumps(
                    {
                        "workspace": str(workspace.path),
                        "workspaceVersion": facts.workspace_version,
                        "platform": platform.system(),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            })

        if any(section.role != "user" for section in retained_context):
            raise ValueError(
                "developer retained context must use an instruction layer"
            )
        retained_by_id = {
            section.section_id: section for section in retained_context
        }
        context.extend(
            retained_by_id[key].as_model_item()
            for key in sorted(retained_by_id)
        )
        if repository_context is not None:
            context.extend(repository_context.model_context_items())
        if facts.compact_summary is not None:
            context.append({
                "type": "user",
                "content": "Compact summary:\n" + json.dumps(
                    facts.compact_summary.model_dump(exclude={"source_item_ids"}),
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            })
        workspace_state = facts.workspace_version
        read_result_fingerprints: dict[tuple[str, str, str, int], str] = {}
        for item in facts.items:
            if (
                item.item_id in source_ids
                and item.item_id != facts.current_user_goal_id
            ):
                continue
            if item.kind == "user_message":
                context.append({"type": "user", "content": item.content or ""})
            elif item.kind == "assistant_message":
                context.append({"type": "assistant", "content": item.content or ""})
            elif item.provider_call_id is not None:
                result_json = item.model_result_json or item.result_json or "{}"
                projected_result = result_json
                if item.tool_name in _CONTEXT_DEDUPE_READ_TOOLS:
                    dedupe_key = (
                        item.tool_name,
                        _canonical_json(item.arguments_json or "{}"),
                        _canonical_json(result_json),
                        workspace_state,
                    )
                    duplicate_of = read_result_fingerprints.get(dedupe_key)
                    if duplicate_of is None:
                        read_result_fingerprints[dedupe_key] = item.provider_call_id
                    else:
                        projected_result = _context_deduplicated_result(
                            duplicate_of, workspace_state
                        )
                context.extend((
                    {
                        "type": "tool_call",
                        "callId": item.provider_call_id,
                        "name": item.tool_name or "",
                        "arguments": item.arguments_json or "{}",
                    },
                    {
                        "type": "tool_result",
                        "callId": item.provider_call_id,
                        "name": item.tool_name or "",
                        "result": projected_result,
                    },
                ))
                if _tool_result_changes_workspace(result_json):
                    workspace_state += 1
        context.extend(extra_context)
        if facts.reconciliation_required or facts.active_error_fingerprints:
            context.append({
                "type": "user",
                "content": "Runtime state: " + json.dumps(
                    {
                        "reconciliationRequired": facts.reconciliation_required,
                        "reconciliationEpoch": facts.reconciliation_epoch,
                        "unresolvedErrorFingerprints": facts.active_error_fingerprints,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            })
        provider_usage = self.store.latest_model_usage(run_id)
        if provider_usage is None:
            self._last_provider_usage = None
            self._provider_calibration_estimate = None
        elif provider_usage != self._last_provider_usage:
            if self._last_estimated_input_tokens is not None:
                self._provider_calibration_estimate = self._last_estimated_input_tokens
            self._last_provider_usage = provider_usage
        budget = estimate_model_request_budget(
            tuple(context),
            instructions=instructions.system_text,
            tool_definitions=tool_definitions,
            context_window_tokens=profile.context_window_tokens,
            request_max_output_tokens=profile.max_output_tokens,
            provider_usage=provider_usage,
            provider_calibration_estimate=(
                self._provider_calibration_estimate
                if provider_usage is not None else None
            ),
            usage_updated_at=time.time_ns() // 1_000_000,
        )
        self._last_estimated_input_tokens = budget.estimated_input_tokens
        return ContextBuild(
            model_context=tuple(context),
            instructions=instructions,
            budget=budget,
            facts=facts,
        )


_CONTEXT_DEDUPE_READ_TOOLS = frozenset({
    "list_files", "read_file", "read_file_range", "search_text"
})


def _canonical_json(value: str) -> str:
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return value
    return json.dumps(
        decoded, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )


def _context_deduplicated_result(provider_call_id: str, workspace_state: int) -> str:
    return json.dumps(
        {
            "contextDeduplicated": True,
            "duplicateOf": provider_call_id,
            "summary": "Identical read result already appears for this workspace state.",
            "workspaceVersion": workspace_state,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _tool_result_changes_workspace(result_json: str) -> bool:
    try:
        result = json.loads(result_json)
    except json.JSONDecodeError:
        return False
    if not isinstance(result, dict):
        return False
    if result.get("sideEffectsMayExist") is True or result.get(
        "reconciliationRequired"
    ) is True:
        return True
    data = result.get("data")
    if not isinstance(data, dict):
        return False
    return (
        data.get("workspaceChanged") is True
        or data.get("workspaceChangeState") in {"changed", "unknown"}
    )

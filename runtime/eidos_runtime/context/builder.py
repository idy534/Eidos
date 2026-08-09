from __future__ import annotations

import json
import platform
import time

from pydantic import BaseModel, ConfigDict

from eidos_runtime.context.budget import ContextBudget, estimate_context_budget
from eidos_runtime.context.facts import ContextFacts
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
        workspace = self.store.workspace_for_run(run_id)

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

        # --- Workspace environment context ---
        workspace_env: ModelContextItem = {
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
        }

        # Order: [user-context layers] [workspace-env] [retained] [summary] [history] [extra]
        context: list[ModelContextItem] = [*user_context_messages, workspace_env]

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
                        "result": item.model_result_json or item.result_json or "{}",
                    },
                ))
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
        tool_calls = sum(item.get("type") == "tool_call" for item in context)
        tool_results = sum(item.get("type") == "tool_result" for item in context)

        # system_text only includes system/developer layers; user layers are in context
        payload = {
            "instructions": instructions.system_text,
            "messages": context,
            "tools": [tool.model_dump(mode="json") for tool in tool_definitions],
        }
        budget = estimate_context_budget(
            payload,
            context_window_tokens=profile.context_window_tokens,
            request_max_output_tokens=profile.max_output_tokens,
            message_count=len(context),
            tool_call_count=tool_calls,
            tool_result_count=tool_results,
            provider_usage=self.store.latest_model_usage(run_id),
            usage_updated_at=time.time_ns() // 1_000_000,
        )
        return ContextBuild(
            model_context=tuple(context),
            instructions=instructions,
            budget=budget,
            facts=facts,
        )

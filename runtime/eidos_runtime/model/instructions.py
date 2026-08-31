from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from eidos_runtime.model.prompts import (
    BASE_AGENT_INSTRUCTIONS,
    RUNTIME_POLICY_INSTRUCTIONS,
    SYSTEM_SAFETY_INSTRUCTIONS,
    InstructionLayer,
    ResolvedInstructions,
)


if TYPE_CHECKING:
    from eidos_runtime.extensions.skills import RetainedContextSection
    from eidos_runtime.runtime.resolution import RuleResolutionSnapshot


SYSTEM_SAFETY_AUTHORITY = 500
RUNTIME_AUTHORITY = 400
PROJECT_RULE_AUTHORITY = 200
SELECTED_SKILL_AUTHORITY = 100
SKILL_CATALOG_AUTHORITY = 300


FINALIZATION_POLICY_INSTRUCTIONS = """Tool execution has stopped for the declared stop reason.

Do not call tools, request approval or ask for additional user input.

Give a concise final answer describing the completed work, the blocker or limit reached, and a safe manual strategy when appropriate.

Do not claim unverified completion."""


@dataclass(frozen=True)
class StepPermissionPolicy:
    """Immutable snapshot of the actual runtime permissions for one step.

    Built from the materialized EffectivePermissionProfile and sandbox policy
    captured in the RunResolutionSnapshot at the start of the step. This is
    descriptive only — it tells the model what the runtime permits. Real
    enforcement is performed by the Runtime, ToolRuntime and Sandbox.
    """

    sandbox_mode: str
    """e.g. 'workspace-write', 'read-only', 'unsandboxed', 'none'"""

    workspace_root: str
    writable_roots: tuple[str, ...] = field(default_factory=tuple)
    network_enabled: bool = False
    allow_additional_permissions: bool = True
    network_permission_requestable: bool = False
    allow_escalated_execution: bool = False
    rejected_approval_ids: tuple[str, ...] = field(default_factory=tuple)
    available_tools: tuple[str, ...] = field(default_factory=tuple)


def _build_runtime_permissions_content(policy: StepPermissionPolicy) -> str:
    """Render the <runtime_permissions> block from a StepPermissionPolicy."""
    lines = ["<runtime_permissions>"]
    lines.append(f"Sandbox mode: {policy.sandbox_mode}")
    lines.append(f"Workspace root: {policy.workspace_root}")

    if policy.writable_roots:
        lines.append("\nWritable roots:")
        for root in policy.writable_roots:
            lines.append(f"- {root}")

    lines.append(
        f"\nDefault Shell network: {'enabled' if policy.network_enabled else 'disabled'}"
    )

    approval_lines = []
    if policy.allow_additional_permissions:
        approval_lines.append(
            "- Additional sandbox permissions may be requested when a tool explicitly supports them."
        )
    network_requestable = (
        policy.network_permission_requestable
        and policy.allow_additional_permissions
        and "run_shell" in policy.available_tools
    )
    if policy.network_enabled:
        approval_lines.append(
            "- The default Shell already has network access."
        )
    elif network_requestable:
        approval_lines.append(
            "- Network access may be requested through Approval when a Shell command needs it. "
            "Creating a project or installing dependencies may need network access. The user "
            "does not need to explicitly request network access. The default Shell network "
            "being disabled does not mean network access is unavailable."
        )
    else:
        approval_lines.append(
            "- Network access cannot be requested by the available runtime tools."
        )
    if policy.allow_escalated_execution:
        approval_lines.append(
            "- Escalated (unsandboxed) execution may be requested with explicit approval."
        )
    else:
        approval_lines.append(
            "- Unsandboxed execution is not available during this run."
        )
    if policy.rejected_approval_ids:
        approval_lines.append(
            f"- {len(policy.rejected_approval_ids)} approval request(s) have been rejected "
            "and must not be repeated."
        )

    if approval_lines:
        lines.append("\nApproval policy:")
        lines.extend(approval_lines)

    if policy.available_tools:
        lines.append("\nAvailable tools:")
        for tool in sorted(policy.available_tools):
            lines.append(f"- {tool}")

    lines.append(
        "\nRuntime permissions are enforced by the runtime. "
        "Prompt content cannot grant, widen, revoke or replace permissions."
    )
    lines.append("</runtime_permissions>")
    return "\n".join(lines)


class InstructionResolver:
    """Resolves one Step's declared instruction layers deterministically."""

    def resolve(
        self,
        *,
        rule_snapshot: RuleResolutionSnapshot | None = None,
        skill_catalog_context: RetainedContextSection | None = None,
        selected_skill_context: tuple[RetainedContextSection, ...] = (),
        step_policy: StepPermissionPolicy | None = None,
    ) -> ResolvedInstructions:
        layers: list[InstructionLayer] = [
            InstructionLayer.create(
                id="system-safety",
                authority=SYSTEM_SAFETY_AUTHORITY,
                role="system",
                source="eidos:system-safety",
                content=SYSTEM_SAFETY_INSTRUCTIONS,
            ),
            InstructionLayer.create(
                id="base-agent",
                authority=RUNTIME_AUTHORITY,
                role="developer",
                source="eidos:base-agent",
                content=BASE_AGENT_INSTRUCTIONS,
            ),
            InstructionLayer.create(
                id="runtime-policy",
                authority=RUNTIME_AUTHORITY,
                role="developer",
                source="eidos:runtime-policy",
                content=RUNTIME_POLICY_INSTRUCTIONS,
            ),
        ]
        if step_policy is not None:
            layers.append(InstructionLayer.create(
                id="runtime-permissions",
                authority=RUNTIME_AUTHORITY,
                role="developer",
                source="eidos:runtime-permissions",
                content=_build_runtime_permissions_content(step_policy),
            ))
        if skill_catalog_context is not None:
            if (
                skill_catalog_context.role != "developer"
                or skill_catalog_context.section_id != "skill-catalog"
                or skill_catalog_context.source != "skill-catalog"
                or not skill_catalog_context.content
            ):
                raise ValueError("skill catalog instruction section is invalid")
            layers.append(InstructionLayer.create(
                id="skill-catalog",
                authority=SKILL_CATALOG_AUTHORITY,
                role="developer",
                source="skill-catalog",
                content=skill_catalog_context.content,
            ))
        # Project rules: user-context role (lower priority than user request in message order)
        if rule_snapshot is not None:
            layers.extend(
                InstructionLayer.create(
                    id=f"project-rule:{rule.relative_path}",
                    authority=PROJECT_RULE_AUTHORITY,
                    role="user",
                    source=rule.relative_path,
                    content=(
                        f"Project rules from {rule.relative_path}:\n"
                        f"{rule.content}"
                    ),
                )
                for rule in rule_snapshot.rules
                if rule.content
            )
        # Selected skills: user-context role
        for section in sorted(
            selected_skill_context,
            key=lambda value: value.section_id.encode("utf-8"),
        ):
            if not section.content:
                continue
            prefix = "selected-skill:"
            if section.role != "developer" or not section.section_id.startswith(prefix):
                raise ValueError("selected skill instruction section is invalid")
            qualified_id = section.section_id.removeprefix(prefix)
            if not qualified_id or not section.source:
                raise ValueError("selected skill instruction source is invalid")
            layers.append(InstructionLayer.create(
                id=section.section_id,
                authority=SELECTED_SKILL_AUTHORITY,
                role="user",
                source=section.source,
                content=(
                    f"Selected skill instructions from {qualified_id}:\n"
                    f"{section.content}"
                ),
            ))
        return ResolvedInstructions.create(tuple(layers))

    def for_finalization(
        self,
        instructions: ResolvedInstructions,
    ) -> ResolvedInstructions:
        return ResolvedInstructions.create((
            *instructions.layers,
            InstructionLayer.create(
                id="finalization-policy",
                authority=RUNTIME_AUTHORITY,
                role="developer",
                source="eidos:finalization-policy",
                content=FINALIZATION_POLICY_INSTRUCTIONS,
            ),
        ))

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from eidos_runtime.model.client import (
    ModelContextItem,
    ModelProfileSnapshot,
    ModelToolDefinition,
)
from eidos_runtime.model.prompts import ResolvedInstructions
from eidos_runtime.sandbox.permissions import (
    BasePermissionProfile,
    base_permission_profile_for_workspace,
    materialize_effective_profile,
)
from eidos_runtime.sandbox.seatbelt_policy import BASE_POLICY_PATH


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    )


def canonical_sha256(value: object, *, raw_text: bool = False) -> str:
    encoded = (
        str(value).encode("utf-8")
        if raw_text
        else canonical_json(value).encode("utf-8")
    )
    return hashlib.sha256(encoded).hexdigest()


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class WorkspaceIdentitySnapshot(_FrozenModel):
    path: str
    device: int
    inode: int
    owner: int


class RuleSourceSnapshot(_FrozenModel):
    absolute_path: str
    relative_path: str
    filename: str
    content: str
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    byte_count: int = Field(ge=0)
    included_byte_count: int = Field(ge=0)
    directory_level: int = Field(ge=0)
    selection_reason: Literal[
        "eidos_override",
        "eidos_native",
        "compatibility_fallback",
    ]
    truncated: bool = False


class ShadowedRuleCandidate(_FrozenModel):
    absolute_path: str
    relative_path: str
    filename: str
    directory_level: int = Field(ge=0)
    reason: Literal["higher_precedence_candidate_selected"]


class RuleResolutionWarning(_FrozenModel):
    code: Literal["RULE_BUDGET_TRUNCATED", "RULE_READ_ERROR", "RULE_PATH_OUTSIDE_WORKSPACE"]
    path: str
    message: str


class RuleResolutionSnapshot(_FrozenModel):
    schema_version: Literal[1] = 1
    id: str
    snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    workspace_root: str
    cwd: str
    budget_bytes: int = Field(ge=0)
    used_bytes: int = Field(ge=0)
    rules: tuple[RuleSourceSnapshot, ...] = ()
    shadowed: tuple[ShadowedRuleCandidate, ...] = ()
    warnings: tuple[RuleResolutionWarning, ...] = ()

    @classmethod
    def create(
        cls,
        *,
        workspace_root: str,
        cwd: str,
        budget_bytes: int,
        used_bytes: int,
        rules: tuple[RuleSourceSnapshot, ...],
        shadowed: tuple[ShadowedRuleCandidate, ...],
        warnings: tuple[RuleResolutionWarning, ...],
    ) -> Self:
        values = {
            "schema_version": 1,
            "workspace_root": workspace_root,
            "cwd": cwd,
            "budget_bytes": budget_bytes,
            "used_bytes": used_bytes,
            "rules": rules,
            "shadowed": shadowed,
            "warnings": warnings,
        }
        payload = {
            **values,
            "rules": [rule.model_dump(mode="json") for rule in rules],
            "shadowed": [candidate.model_dump(mode="json") for candidate in shadowed],
            "warnings": [warning.model_dump(mode="json") for warning in warnings],
        }
        digest = canonical_sha256(payload)
        return cls(id=f"rule_{digest}", snapshot_hash=digest, **values)

    @model_validator(mode="after")
    def validate_hash(self) -> Self:
        payload = self.model_dump(mode="json", exclude={"id", "snapshot_hash"})
        if self.snapshot_hash != canonical_sha256(payload) or self.id != f"rule_{self.snapshot_hash}":
            raise ValueError("rule resolution snapshot hash mismatch")
        return self

    def model_instruction(self) -> str | None:
        sections = [
            f"Project rules from {rule.relative_path}:\n{rule.content}"
            for rule in self.rules
            if rule.content
        ]
        return "\n\n".join(sections) or None


class RunResolutionSnapshot(_FrozenModel):
    schema_version: Literal[1] = 1
    id: str
    snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_id: str
    model_profile_snapshot_id: str
    model_profile_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    extension_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    workspace_identity: WorkspaceIdentitySnapshot
    permission_profile_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    permission_profile_json: str
    sandbox_policy_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    sandbox_policy_json: str
    created_at: int = Field(ge=0)

    @classmethod
    def create(cls, **values: object) -> Self:
        payload = {"schema_version": 1, **values}
        digest = canonical_sha256(_json_value(payload))
        return cls(id=f"run_{digest}", snapshot_hash=digest, **payload)

    @model_validator(mode="after")
    def validate_hash(self) -> Self:
        payload = self.model_dump(mode="json", exclude={"id", "snapshot_hash"})
        if self.snapshot_hash != canonical_sha256(payload) or self.id != f"run_{self.snapshot_hash}":
            raise ValueError("run resolution snapshot hash mismatch")
        _validate_canonical_json(self.permission_profile_json)
        BasePermissionProfile.model_validate_json(self.permission_profile_json)
        sandbox = _validate_canonical_json(self.sandbox_policy_json)
        if (
            canonical_sha256(sandbox) != self.sandbox_policy_hash
            or not isinstance(sandbox, dict)
            or sandbox.get("permissionProfileHash")
            != self.permission_profile_hash
        ):
            raise ValueError("run policy snapshot hash mismatch")
        return self


class StepResolutionSnapshot(_FrozenModel):
    schema_version: Literal[1] = 1
    id: str
    snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_snapshot_id: str
    rule_resolution_snapshot_id: str
    rule_resolution_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    tool_set_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    tool_snapshot_json: str
    model_snapshot_id: str
    model_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    extension_snapshot_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    workspace_identity: WorkspaceIdentitySnapshot
    workspace_version: int = Field(ge=0)
    permission_profile_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    permission_profile_json: str
    sandbox_policy_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    sandbox_policy_json: str
    context_payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    context_payload_json: str
    system_prompt_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    final_request_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    final_request_json: str
    created_at: int = Field(ge=0)

    @classmethod
    def create(cls, **values: object) -> Self:
        payload = {"schema_version": 1, **values}
        digest = canonical_sha256(_json_value(payload))
        return cls(id=f"step_{digest}", snapshot_hash=digest, **payload)

    @model_validator(mode="after")
    def validate_hashes(self) -> Self:
        payload = self.model_dump(mode="json", exclude={"id", "snapshot_hash"})
        if self.snapshot_hash != canonical_sha256(payload) or self.id != f"step_{self.snapshot_hash}":
            raise ValueError("step resolution snapshot hash mismatch")
        parsed = {
            name: _validate_canonical_json(getattr(self, name))
            for name in (
                "tool_snapshot_json",
                "permission_profile_json",
                "sandbox_policy_json",
                "context_payload_json",
                "final_request_json",
            )
        }
        if canonical_sha256(parsed["context_payload_json"]) != self.context_payload_hash:
            raise ValueError("context payload hash mismatch")
        tool = parsed["tool_snapshot_json"]
        if not isinstance(tool, dict) or tool.get("toolSetHash") != self.tool_set_hash:
            raise ValueError("tool set hash mismatch")
        BasePermissionProfile.model_validate_json(self.permission_profile_json)
        sandbox = parsed["sandbox_policy_json"]
        if (
            canonical_sha256(sandbox) != self.sandbox_policy_hash
            or not isinstance(sandbox, dict)
            or sandbox.get("permissionProfileHash")
            != self.permission_profile_hash
        ):
            raise ValueError("sandbox policy hash mismatch")
        if canonical_sha256(parsed["final_request_json"]) != self.final_request_hash:
            raise ValueError("final request hash mismatch")
        request = parsed["final_request_json"]
        if not isinstance(request, dict):
            raise ValueError("final request snapshot mismatch")
        system_prompt = request.get("systemPrompt")
        if (
            not isinstance(system_prompt, str)
            or self.system_prompt_hash
            != canonical_sha256(system_prompt, raw_text=True)
        ):
            raise ValueError("system prompt hash mismatch")
        if (
            request.get("messages") != parsed["context_payload_json"]
            or request.get("modelSnapshotId") != self.model_snapshot_id
            or request.get("modelSnapshotHash") != self.model_snapshot_hash
        ):
            raise ValueError("final request snapshot mismatch")
        return self


def _validate_canonical_json(value: str) -> object:
    parsed = json.loads(value)
    if canonical_json(parsed) != value:
        raise ValueError("snapshot JSON is not canonical")
    return parsed


def _json_value(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _json_value(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [_json_value(child) for child in value]
    if isinstance(value, list):
        return [_json_value(child) for child in value]
    return value


def create_run_resolution_snapshot(
    *,
    run_id: str,
    model_profile: ModelProfileSnapshot,
    extension_snapshot: dict[str, object],
    workspace_identity: WorkspaceIdentitySnapshot,
    data_directory: Path | None,
    created_at: int,
) -> RunResolutionSnapshot:
    model_value = model_profile.model_dump(mode="json")
    model_hash = canonical_sha256(model_value)
    model_id = f"model_{model_hash}"
    base_permissions = base_permission_profile_for_workspace(
        Path(workspace_identity.path),
        data_directory,
    )
    permission_json = canonical_json(
        base_permissions.model_dump(mode="json", by_alias=True)
    )
    permission_hash = materialize_effective_profile(
        base_permissions
    ).profile_hash
    base_policy_hash = hashlib.sha256(BASE_POLICY_PATH.read_bytes()).hexdigest()
    sandbox_policy = {
        "approvalPolicy": "side_effects_require_approval",
        "basePolicyHash": base_policy_hash,
        "permissionProfileHash": permission_hash,
        "sandboxType": "macos_seatbelt",
    }
    sandbox_json = canonical_json(sandbox_policy)
    return RunResolutionSnapshot.create(
        run_id=run_id,
        model_profile_snapshot_id=model_id,
        model_profile_snapshot_hash=model_hash,
        extension_snapshot_hash=canonical_sha256(extension_snapshot),
        workspace_identity=workspace_identity,
        permission_profile_hash=permission_hash,
        permission_profile_json=permission_json,
        sandbox_policy_hash=canonical_sha256(sandbox_policy),
        sandbox_policy_json=sandbox_json,
        created_at=created_at,
    )


def create_step_resolution_snapshot(
    *,
    run_snapshot: RunResolutionSnapshot,
    rule_snapshot: RuleResolutionSnapshot,
    tool_snapshot: dict[str, object],
    model_context: tuple[ModelContextItem, ...],
    tool_definitions: tuple[ModelToolDefinition, ...],
    instructions: ResolvedInstructions,
    workspace_version: int,
    created_at: int,
) -> StepResolutionSnapshot:
    context_value = list(model_context)
    context_json = canonical_json(context_value)
    request = {
        "schemaVersion": 1,
        "modelSnapshotId": run_snapshot.model_profile_snapshot_id,
        "modelSnapshotHash": run_snapshot.model_profile_snapshot_hash,
        "systemPrompt": instructions.text,
        "messages": context_value,
        "tools": [
            definition.model_dump(mode="json")
            for definition in tool_definitions
        ],
    }
    request_json = canonical_json(request)
    return StepResolutionSnapshot.create(
        run_snapshot_id=run_snapshot.id,
        rule_resolution_snapshot_id=rule_snapshot.id,
        rule_resolution_snapshot_hash=rule_snapshot.snapshot_hash,
        tool_set_hash=str(tool_snapshot["toolSetHash"]),
        tool_snapshot_json=canonical_json(tool_snapshot),
        model_snapshot_id=run_snapshot.model_profile_snapshot_id,
        model_snapshot_hash=run_snapshot.model_profile_snapshot_hash,
        extension_snapshot_hash=run_snapshot.extension_snapshot_hash,
        workspace_identity=run_snapshot.workspace_identity,
        workspace_version=workspace_version,
        permission_profile_hash=run_snapshot.permission_profile_hash,
        permission_profile_json=run_snapshot.permission_profile_json,
        sandbox_policy_hash=run_snapshot.sandbox_policy_hash,
        sandbox_policy_json=run_snapshot.sandbox_policy_json,
        context_payload_hash=canonical_sha256(context_value),
        context_payload_json=context_json,
        system_prompt_hash=instructions.instructions_hash,
        final_request_hash=canonical_sha256(request),
        final_request_json=request_json,
        created_at=created_at,
    )

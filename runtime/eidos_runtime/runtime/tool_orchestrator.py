from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from enum import StrEnum
import hashlib
import json
from pathlib import Path
import threading
from typing import Callable, Protocol, TypeVar

from pydantic import BaseModel

from eidos_runtime.sandbox.denial import SandboxDenied
from eidos_runtime.sandbox.permissions import (
    AdditionalPermissionProfile,
    BasePermissionProfile,
    SandboxAttempt,
    SandboxPermissions,
    SandboxType,
    materialize_effective_profile,
    unsandboxed_execution_allowed,
)


RequestT = TypeVar("RequestT")


class ExecApprovalRequirement(StrEnum):
    SKIP = "skip"
    NEEDS_APPROVAL = "needs_approval"
    FORBIDDEN = "forbidden"


@dataclass(frozen=True)
class OrchestratorContext:
    tool_call_id: str
    workspace_root: Path
    workspace_identity: tuple[int, int, int]
    cwd: Path
    timeout_seconds: int
    cancel: threading.Event
    base_permissions: BasePermissionProfile
    environment_identity: str = "local"


@dataclass(frozen=True)
class OrchestratorApprovalRequest:
    attempt_ordinal: int
    approval_kind: str
    sandbox_permissions: SandboxPermissions
    additional_permissions: AdditionalPermissionProfile | None
    escalation_reason: str | None
    approval_key: str
    effective_permissions: dict[str, object]


@dataclass(frozen=True)
class OrchestratorResult:
    result: dict[str, object]
    attempt_count: int
    escalated: bool


class ToolRuntime(Protocol[RequestT]):
    def workspace_roots(
        self, request: RequestT, context: OrchestratorContext
    ) -> tuple[str, ...]: ...

    def sandbox_permissions(
        self, request: RequestT
    ) -> SandboxPermissions: ...

    def additional_permissions(
        self, request: RequestT
    ) -> AdditionalPermissionProfile | None: ...

    def approval_requirement(
        self, request: RequestT, context: OrchestratorContext
    ) -> ExecApprovalRequirement: ...

    def escalation_allowed(
        self, request: RequestT, context: OrchestratorContext
    ) -> bool: ...

    def run(
        self,
        request: RequestT,
        attempt: SandboxAttempt,
        context: OrchestratorContext,
    ) -> tuple[dict[str, object], SandboxDenied | None]: ...


class ToolOrchestrator:
    def run(
        self,
        runtime: ToolRuntime[RequestT],
        request: RequestT,
        context: OrchestratorContext,
        *,
        approve: Callable[[OrchestratorApprovalRequest], bool],
        record_attempt: Callable[
            [SandboxAttempt, str, dict[str, object] | None], None
        ] | None = None,
    ) -> OrchestratorResult:
        mode = runtime.sandbox_permissions(request)
        additional = runtime.additional_permissions(request)
        if additional is None:
            if mode is SandboxPermissions.WITH_ADDITIONAL_PERMISSIONS:
                raise ValueError("additional_permissions are required")
        else:
            additional.validate_for(mode)
        effective = materialize_effective_profile(
            context.base_permissions,
            additional
            if mode is SandboxPermissions.WITH_ADDITIONAL_PERMISSIONS
            else None,
        )
        workspace_roots = tuple(
            str(Path(root).resolve(strict=True))
            for root in runtime.workspace_roots(request, context)
        )
        if (
            not workspace_roots
            or not set(workspace_roots).issubset(effective.workspace_roots)
        ):
            raise ValueError("runtime workspace roots exceed effective permissions")
        requirement = runtime.approval_requirement(request, context)
        if requirement is ExecApprovalRequirement.FORBIDDEN:
            return OrchestratorResult(
                _error_result("approval_forbidden", "Execution is forbidden"),
                0,
                False,
            )
        explicit_escalation = mode is SandboxPermissions.REQUIRE_ESCALATED
        if explicit_escalation and not unsandboxed_execution_allowed(effective):
            return OrchestratorResult(
                _error_result(
                    "unsandboxed_execution_forbidden",
                    "Unsandboxed execution would discard a hard confidentiality deny",
                ),
                0,
                False,
            )
        first = self._attempt(
            ordinal=0,
            sandbox=(
                SandboxType.NONE
                if explicit_escalation
                else SandboxType.MACOS_SEATBELT
            ),
            effective=effective,
            workspace_roots=workspace_roots,
            context=context,
        )
        if requirement is ExecApprovalRequirement.NEEDS_APPROVAL and not approve(
            self._approval_request(request, first, mode, additional, context)
        ):
            return OrchestratorResult(
                _error_result("user_rejected", "User rejected the command"),
                0,
                explicit_escalation,
            )
        result, denial = self._run_attempt(
            runtime, request, first, context, record_attempt
        )
        attempt_count = 1
        if (
            denial is None
            or explicit_escalation
            or context.cancel.is_set()
            or not runtime.escalation_allowed(request, context)
            or not unsandboxed_execution_allowed(effective)
        ):
            return OrchestratorResult(
                _attach_metadata(
                    result,
                    attempt_count,
                    explicit_escalation,
                    first,
                    mode,
                    denial,
                ),
                attempt_count,
                explicit_escalation,
            )
        retry = self._attempt(
            ordinal=1,
            sandbox=SandboxType.NONE,
            effective=effective,
            workspace_roots=workspace_roots,
            context=context,
            escalation_reason=denial.summary,
        )
        if not approve(
            self._approval_request(
                request,
                retry,
                SandboxPermissions.REQUIRE_ESCALATED,
                None,
                context,
            )
        ):
            rejected = _error_result(
                "user_rejected_escalation",
                "User rejected unsandboxed retry",
                side_effects_may_exist=True,
            )
            return OrchestratorResult(
                _attach_metadata(
                    rejected, attempt_count, False, first, mode, denial
                ),
                attempt_count,
                False,
            )
        if context.cancel.is_set():
            return OrchestratorResult(
                _attach_metadata(
                    result, attempt_count, False, first, mode, denial
                ),
                attempt_count,
                False,
            )
        result, retry_denial = self._run_attempt(
            runtime, request, retry, context, record_attempt
        )
        attempt_count += 1
        return OrchestratorResult(
            _attach_metadata(
                result,
                attempt_count,
                True,
                retry,
                mode,
                retry_denial or denial,
            ),
            attempt_count,
            True,
        )

    @staticmethod
    def _attempt(
        *,
        ordinal: int,
        sandbox: SandboxType,
        effective,
        workspace_roots: tuple[str, ...],
        context: OrchestratorContext,
        escalation_reason: str | None = None,
    ) -> SandboxAttempt:
        return SandboxAttempt(
            ordinal=ordinal,
            sandbox=sandbox,
            sandboxRequested=sandbox is not SandboxType.NONE,
            permissions=effective,
            sandboxCwd=str(context.cwd),
            workspaceRoots=workspace_roots,
            profileHash=(
                effective.profile_hash
                if sandbox is SandboxType.MACOS_SEATBELT
                else None
            ),
            escalationReason=escalation_reason,
        )

    @staticmethod
    def _approval_request(
        request: RequestT,
        attempt: SandboxAttempt,
        mode: SandboxPermissions,
        additional: AdditionalPermissionProfile | None,
        context: OrchestratorContext,
    ) -> OrchestratorApprovalRequest:
        payload = {
            "environmentIdentity": context.environment_identity,
            "toolCallId": context.tool_call_id,
            "request": _json_value(request),
            "cwd": str(context.cwd),
            "timeoutSeconds": context.timeout_seconds,
            "sandboxPermissions": mode.value,
            "additionalPermissions": (
                additional.model_dump(
                    mode="json", by_alias=True, exclude_none=True
                )
                if additional is not None
                else None
            ),
            "workspaceIdentity": context.workspace_identity,
            "workspaceRoots": attempt.workspace_roots,
            "sandbox": attempt.sandbox.value,
            "attemptOrdinal": attempt.ordinal,
            "profileHash": attempt.profile_hash,
            "escalationReason": attempt.escalation_reason,
        }
        key = hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        return OrchestratorApprovalRequest(
            attempt_ordinal=attempt.ordinal,
            approval_kind=(
                "escalated"
                if attempt.sandbox is SandboxType.NONE
                else "additional_permissions"
                if mode is SandboxPermissions.WITH_ADDITIONAL_PERMISSIONS
                else "default"
            ),
            sandbox_permissions=mode,
            additional_permissions=additional,
            escalation_reason=attempt.escalation_reason,
            approval_key=key,
            effective_permissions=attempt.permissions.summary(),
        )

    @staticmethod
    def _run_attempt(
        runtime: ToolRuntime[RequestT],
        request: RequestT,
        attempt: SandboxAttempt,
        context: OrchestratorContext,
        record_attempt: Callable[
            [SandboxAttempt, str, dict[str, object] | None], None
        ] | None,
    ) -> tuple[dict[str, object], SandboxDenied | None]:
        if record_attempt is not None:
            record_attempt(attempt, "running", None)
        try:
            result, denial = runtime.run(request, attempt, context)
        except Exception:
            if record_attempt is not None:
                record_attempt(attempt, "uncertain", None)
            raise
        if record_attempt is not None:
            record_attempt(
                attempt,
                "completed" if result.get("outcome") == "success" else "failed",
                result,
            )
        return result, denial


def _json_value(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=True, exclude_none=True)
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return _json_safe(value)
    return _json_safe(vars(value))


def _json_safe(value: object) -> object:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(child) for child in value]
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", by_alias=True, exclude_none=True)
    return value


def _attach_metadata(
    result: dict[str, object],
    attempt_count: int,
    escalated: bool,
    attempt: SandboxAttempt,
    requested_mode: SandboxPermissions,
    denial: SandboxDenied | None,
) -> dict[str, object]:
    attached = dict(result)
    data = dict(
        attached.get("data") if isinstance(attached.get("data"), dict) else {}
    )
    data.update(
        {
            "attemptCount": attempt_count,
            "sandboxed": attempt.sandbox is SandboxType.MACOS_SEATBELT,
            "sandboxPermissions": requested_mode.value,
            "escalated": escalated,
            "profileHash": attempt.permissions.profile_hash,
            "sandboxDenialCategory": (
                denial.category.value if denial is not None else None
            ),
            "effectivePermissionsSummary": attempt.permissions.summary(),
        }
    )
    attached["data"] = data
    if attempt.sandbox is SandboxType.NONE:
        attached["sideEffectsMayExist"] = True
    return attached


def _error_result(
    code: str,
    summary: str,
    *,
    side_effects_may_exist: bool = False,
) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "toolName": "run_shell",
        "outcome": "error",
        "code": code,
        "summary": summary,
        "data": {},
        "sideEffectsMayExist": side_effects_may_exist,
        "reconciliationRequired": side_effects_may_exist,
    }

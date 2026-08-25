from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from eidos_runtime.db.storage import WorkspaceIdentity
from eidos_runtime.runtime.tool_orchestrator import (
    ExecApprovalRequirement,
    OrchestratorContext,
)
from eidos_runtime.sandbox.denial import SandboxDenied
from eidos_runtime.sandbox.permissions import (
    AdditionalPermissionProfile,
    SandboxAttempt,
    SandboxPermissions,
)
from eidos_runtime.tools.contracts import RunShellInput


@dataclass(frozen=True)
class ShellOrchestrationRequest:
    input: RunShellInput
    workspace: WorkspaceIdentity
    cwd: WorkspaceIdentity


class ShellOrchestrationRuntime:
    def __init__(
        self,
        execute_attempt: Callable[
            [SandboxAttempt], tuple[dict[str, object], SandboxDenied | None]
        ],
    ) -> None:
        self.execute_attempt = execute_attempt

    def workspace_roots(
        self,
        request: ShellOrchestrationRequest,
        _context: OrchestratorContext,
    ) -> tuple[str, ...]:
        return (str(request.workspace.path),)

    def sandbox_permissions(
        self, request: ShellOrchestrationRequest
    ) -> SandboxPermissions:
        return request.input.sandboxPermissions

    def additional_permissions(
        self, request: ShellOrchestrationRequest
    ) -> AdditionalPermissionProfile | None:
        return request.input.additionalPermissions

    def approval_requirement(
        self,
        request: ShellOrchestrationRequest,
        _context: OrchestratorContext,
    ) -> ExecApprovalRequirement:
        if request.input.sandboxPermissions is SandboxPermissions.USE_DEFAULT:
            return ExecApprovalRequirement.SKIP
        return ExecApprovalRequirement.NEEDS_APPROVAL

    def escalation_allowed(
        self,
        _request: ShellOrchestrationRequest,
        _context: OrchestratorContext,
    ) -> bool:
        return True

    def run(
        self,
        _request: ShellOrchestrationRequest,
        attempt: SandboxAttempt,
        _context: OrchestratorContext,
    ) -> tuple[dict[str, object], SandboxDenied | None]:
        return self.execute_attempt(attempt)

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from eidos_runtime.db.storage import WorkspaceIdentity
from eidos_runtime.git.backend import DulwichGitBackend
from eidos_runtime.git.errors import GitError
from eidos_runtime.runtime.tool_orchestrator import (
    ExecApprovalRequirement,
    OrchestratorContext,
)
from eidos_runtime.sandbox.denial import SandboxDenied
from eidos_runtime.runtime.permission_policy import PermissionPolicyEvaluator, PermissionDisposition
from eidos_runtime.sandbox.permissions import (
    AdditionalPermissionProfile,
    FileSystemAccessMode,
    FileSystemPermissionEntry,
    SandboxAttempt,
    SandboxPermissions,
    merge_permissions,
)
from eidos_runtime.tools.contracts import RunShellInput


@dataclass(frozen=True)
class ShellOrchestrationRequest:
    input: RunShellInput
    workspace: WorkspaceIdentity
    cwd: WorkspaceIdentity
    git_write_roots: tuple[str, ...] = ()


class GitWriteAccessError(ValueError):
    pass


def resolve_git_write_roots(workspace: WorkspaceIdentity) -> tuple[str, ...]:
    try:
        discovery = DulwichGitBackend().discover(workspace.path)
        git_dir = Path(discovery.git_dir).resolve(strict=True)
        git_common_dir = Path(discovery.git_common_dir).resolve(strict=True)
        if (
            workspace.git_dir is not None
            and git_dir != workspace.git_dir.resolve(strict=True)
        ):
            raise GitWriteAccessError("git_repository_identity_changed")
        if (
            workspace.git_common_dir is not None
            and git_common_dir != workspace.git_common_dir.resolve(strict=True)
        ):
            raise GitWriteAccessError("git_repository_identity_changed")
        return tuple(dict.fromkeys((str(git_dir), str(git_common_dir))))
    except GitWriteAccessError:
        raise
    except (GitError, OSError, ValueError) as error:
        raise GitWriteAccessError("git_repository_unavailable") from error


def effective_shell_permissions(
    shell_input: RunShellInput,
    git_write_roots: tuple[str, ...],
) -> AdditionalPermissionProfile | None:
    git_permissions = (
        AdditionalPermissionProfile(
            fileSystem=tuple(
                FileSystemPermissionEntry(
                    path=root,
                    access=FileSystemAccessMode.WRITE,
                )
                for root in git_write_roots
            )
        )
        if git_write_roots
        else None
    )
    merged = merge_permissions(
        shell_input.effective_additional_permissions,
        git_permissions,
    )
    return None if merged.is_empty else merged


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
        return request.input.effective_sandbox_permissions

    def additional_permissions(
        self, request: ShellOrchestrationRequest
    ) -> AdditionalPermissionProfile | None:
        return effective_shell_permissions(
            request.input,
            request.git_write_roots,
        )

    def approval_requirement(
        self,
        request: ShellOrchestrationRequest,
        _context: OrchestratorContext,
    ) -> ExecApprovalRequirement:
        if (
            request.input.effective_sandbox_permissions
            is SandboxPermissions.USE_DEFAULT
        ):
            return ExecApprovalRequirement.SKIP
        decision = PermissionPolicyEvaluator().evaluate(
            _context.base_permissions,
            _context.granted_permissions,
            self.additional_permissions(request),
            unsandboxed=request.input.effective_sandbox_permissions is SandboxPermissions.REQUIRE_ESCALATED,
        )
        return {
            PermissionDisposition.ALLOW: ExecApprovalRequirement.NEEDS_APPROVAL,
            PermissionDisposition.ASK: ExecApprovalRequirement.NEEDS_APPROVAL,
            PermissionDisposition.DENY: ExecApprovalRequirement.FORBIDDEN,
        }[decision.disposition]

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

from __future__ import annotations

from dataclasses import dataclass, field
import errno
import hashlib
import json
import threading
from typing import Callable

import anyio

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.extensions.skill_access import (
    SkillAccess,
)
from eidos_runtime.extensions.skills import SkillCreation
from eidos_runtime.model.client import ModelResponse, ModelToolCall
from eidos_runtime.runtime.approval import (
    APPROVAL_REJECTION_GUIDANCE,
    ApprovalCoordinator,
    ApprovalOutcome,
)
from eidos_runtime.runtime.async_kernel import (
    AsyncKernelClosedError,
    RuntimeAsyncKernel,
)
from eidos_runtime.runtime.contracts import (
    RuntimeCancelled,
    SamplingOutcome,
    StepContext,
    ToolBatchOutcome,
)
from eidos_runtime.runtime.errors import (
    bounded_tool_result,
    safe_tool_result,
    tool_error,
    tool_result,
)
from eidos_runtime.runtime.events import RuntimeEvents
from eidos_runtime.runtime.resource_registry import ResourceRegistry
from eidos_runtime.runtime.reconciliation import (
    ReconciliationDisposition,
    classify_shell_reconciliation,
)
from eidos_runtime.runtime.state_machine import RuntimePhaseTracker, RuntimeState
from eidos_runtime.runtime.tool_dispatcher import ToolDispatcher
from eidos_runtime.runtime.tool_execution import (
    HandlerOutcome,
    PreparedToolExecution,
    ToolExecutionController,
    ToolConcurrencyGate,
    ToolInfrastructureError,
    VerifiedToolExecutionResult,
)
from eidos_runtime.runtime.shell_orchestration import (
    ShellOrchestrationRequest,
    ShellOrchestrationRuntime,
)
from eidos_runtime.runtime.tool_orchestrator import (
    OrchestratorApprovalRequest,
    OrchestratorContext,
    ToolOrchestrator,
)
from eidos_runtime.sandbox.denial import (
    SandboxDenied,
    SandboxDenialCategory,
    detect_sandbox_denial,
)
from eidos_runtime.sandbox.permissions import (
    BasePermissionProfile,
    SandboxAttempt,
    SandboxPermissions,
    SandboxType,
)
from eidos_runtime.sandbox.sensitive import (
    SensitiveScanError,
    SensitiveScanner,
    StreamingSensitiveScanner,
)
from eidos_runtime.sandbox.seatbelt import is_seatbelt_ready
from eidos_runtime.sandbox.shell import run_shell, sandbox_unavailable_result
from eidos_runtime.sandbox.workspace_manifest import (
    attach_workspace_diff,
    diff_workspace_manifests,
)
from eidos_runtime.tools.workspace import (
    AppliedPatchDelta,
    PreparedPatch,
    ToolCancelled,
    WorkspacePathError,
)
from eidos_runtime.tools.contracts import RunShellInput
from eidos_runtime.tools.registry import (
    AdapterToolRuntime,
    EidosStateToolRuntime,
    ExternalToolRuntime,
    ShellToolRuntime,
    WorkspaceMutationRuntime,
)


@dataclass(frozen=True)
class _HandlerDependencies:
    store: SessionStore
    dispatcher: ToolDispatcher
    events: RuntimeEvents
    sensitive: SensitiveScanner
    shell_available: bool
    execute_side_effect: Callable[
        ...,
        tuple[ApprovalOutcome, VerifiedToolExecutionResult | None],
    ]
    authorize_side_effect: Callable[..., ApprovalOutcome]
    execute_workspace_side_effect: Callable[..., VerifiedToolExecutionResult]
    authorize_workspace_side_effect: Callable[..., None]
    resources: ResourceRegistry = field(default_factory=ResourceRegistry)
    base_permissions: BasePermissionProfile | None = None
    skill_access: SkillAccess | None = None


class ReadOnlyToolHandler:
    def __init__(self, dependencies: _HandlerDependencies) -> None:
        self.dependencies = dependencies

    def execute(
        self,
        _run_id: str,
        _item: dict[str, object],
        call: ModelToolCall,
        cancel: threading.Event,
        runtime: AdapterToolRuntime,
    ) -> HandlerOutcome:
        prepared = runtime.prepare(self, call.arguments, cancel)
        raw = runtime.execute(self, prepared, cancel)
        verified = runtime.verify(self, prepared, raw, cancel)
        return HandlerOutcome(
            verified.result,
            "completed",
            activations=verified.activated_tool_names,
        )


class FileChangeToolHandler:
    def __init__(self, dependencies: _HandlerDependencies) -> None:
        self.dependencies = dependencies

    def execute(
        self,
        run_id: str,
        item: dict[str, object],
        call: ModelToolCall,
        cancel: threading.Event,
        runtime: WorkspaceMutationRuntime,
    ) -> HandlerOutcome:
        prepared = runtime.implementation.prepare_file_change(
            call.arguments, cancel  # type: ignore[attr-defined]
        )
        if isinstance(prepared, dict):
            return HandlerOutcome(
                bounded_tool_result(call.name, prepared), "failed", "failed"
            )
        if isinstance(prepared, PreparedPatch):
            if prepared.base_sha256 is not None and not prepared.diff:
                return HandlerOutcome(
                    tool_result(
                        call.name,
                        "success",
                        "no_changes",
                        "Patch did not change any files",
                        {"path": prepared.path, "changes": []},
                    ),
                    "completed",
                )
            paths = [change.path for change in prepared.changes]
            committed_delta = AppliedPatchDelta()
            prepared_execution = PreparedToolExecution(
                approval_description={
                    "kind": "file_change",
                    "summary": f"Modify {len(paths)} files",
                    "paths": paths,
                    "diff": prepared.diff,
                },
                approval_diff=prepared.diff,
                base_sha256=prepared.base_sha256,
                transition_reason="workspace_file_authorized",
                intent_preconditions={
                    "authorization": "workspace",
                    "paths": paths,
                    "baseShas": [
                        change.base_sha256 for change in prepared.changes
                    ],
                },
            )
            self.dependencies.store.record_workspace_change(
                str(item["id"]),
                diff=prepared.diff,
                base_sha256=prepared.base_sha256,
            )

            def execute_patch() -> dict[str, object]:
                nonlocal committed_delta
                result, committed_delta = runtime.implementation.commit_patch(  # type: ignore[attr-defined]
                    prepared, cancel
                )
                return result

            verified = self.dependencies.execute_workspace_side_effect(
                item=item,
                prepared=prepared_execution,
                execute=execute_patch,
            )
            result = verified.result
            if result["outcome"] == "success":
                self.dependencies.store.clear_rejects(run_id)
            changed = bool(committed_delta.changes)
            status = "completed" if result["outcome"] == "success" else "failed"
            return HandlerOutcome(
                result,
                status,
                status,
                workspace_changed=changed,
                diff_hash=(
                    hashlib.sha256(prepared.diff.encode("utf-8")).hexdigest()
                    if changed else None
                ),
            )
        if prepared.base_sha256 is not None and not prepared.diff:
            return HandlerOutcome(
                tool_result(
                    call.name,
                    "success",
                    "no_changes",
                    "File already matches the requested content",
                    {"path": prepared.path, "baseSha256": prepared.base_sha256},
                ),
                "completed",
            )
        prepared_execution = PreparedToolExecution(
            approval_description={
                "kind": "file_change",
                "summary": f"Modify {prepared.path}",
                "diff": prepared.diff,
            },
            base_sha256=prepared.base_sha256,
            transition_reason="workspace_file_authorized",
            intent_preconditions={
                "authorization": "workspace",
                "path": prepared.path,
                "baseSha256": prepared.base_sha256,
            },
        )
        self.dependencies.store.record_workspace_change(
            str(item["id"]),
            diff=prepared.diff,
            base_sha256=prepared.base_sha256,
        )
        verified = self.dependencies.execute_workspace_side_effect(
            item=item,
            prepared=prepared_execution,
            execute=lambda: runtime.implementation.commit_file_change(  # type: ignore[attr-defined]
                prepared, cancel
            ),
        )
        result = verified.result
        if result["outcome"] == "success" and result.get("code") != "no_changes":
            self.dependencies.store.clear_rejects(run_id)
        status = "completed" if result["outcome"] == "success" else "failed"
        changed = result["outcome"] == "success" and result.get("code") != "no_changes"
        return HandlerOutcome(
            result,
            status,
            status,
            workspace_changed=changed,
            diff_hash=(
                hashlib.sha256(prepared.diff.encode("utf-8")).hexdigest()
                if changed else None
            ),
        )


class ShellToolHandler:
    def __init__(self, dependencies: _HandlerDependencies) -> None:
        self.dependencies = dependencies

    def execute(
        self,
        run_id: str,
        item: dict[str, object],
        call: ModelToolCall,
        cancel: threading.Event,
        runtime: ShellToolRuntime,
    ) -> HandlerOutcome:
        shell_input = RunShellInput.model_validate_json(
            json.dumps(call.arguments, ensure_ascii=False)
        )
        if (
            shell_input.sandboxPermissions
            is not SandboxPermissions.REQUIRE_ESCALATED
            and not self.dependencies.shell_available
        ):
            return HandlerOutcome(
                tool_error(
                    call.name, "sandbox_unavailable", "Shell sandbox is unavailable"
                ),
                "failed",
                "failed",
            )
        command = shell_input.command
        cwd_value = shell_input.cwd
        timeout = shell_input.timeoutSeconds
        try:
            cwd = runtime.implementation.prepare_shell(  # type: ignore[attr-defined]
                cwd_value, cancel
            )
        except ToolCancelled:
            raise RuntimeCancelled from None
        except WorkspacePathError as error:
            return HandlerOutcome(
                tool_error(call.name, error.code, "Shell workspace is unsafe"),
                "failed",
                "failed",
            )
        skill_access = self.dependencies.skill_access
        skill_invocation = (
            skill_access.activate_implicit(command, cwd.path)
            if skill_access is not None
            else None
        )
        active_skill_roots = (
            skill_access.active_roots() if skill_access is not None else ()
        )
        base_permissions = self.dependencies.base_permissions
        if base_permissions is None:
            raise RuntimeError("step permission profile is unavailable")
        if active_skill_roots:
            base_permissions = base_permissions.model_copy(update={
                "active_skill_roots": tuple(
                    str(root) for root in active_skill_roots
                ),
            })
        if (
            shell_input.sandboxPermissions
            is not SandboxPermissions.REQUIRE_ESCALATED
            and not is_seatbelt_ready()
        ):
            return HandlerOutcome(
                sandbox_unavailable_result(skill_invocation=skill_invocation),
                "failed",
                "failed",
            )
        workspace_diff = None
        delta_sequence = 0

        def stream_safe_output(text: str) -> None:
            nonlocal delta_sequence
            if cancel.is_set():
                return
            delta_sequence += 1
            mutation = self.dependencies.store.append_item_deltas_committed(
                str(item["id"]), (text,), delta_sequence
            )
            self.dependencies.events.publish(mutation, item=mutation.value)

        manifest_before = (
            runtime.implementation.executor.workspace_index.manifest()  # type: ignore[attr-defined]
        )
        manifest_after = manifest_before
        refresh_error_code: str | None = None

        def execute_shell_attempt(
            attempt: SandboxAttempt,
        ) -> tuple[dict[str, object], SandboxDenied | None]:
            nonlocal manifest_after, refresh_error_code, workspace_diff
            manifest_after = manifest_before
            refresh_error_code = None
            try:
                approved_cwd = runtime.implementation.prepare_shell(  # type: ignore[attr-defined]
                    cwd_value, cancel
                )
                if approved_cwd != cwd:
                    raise WorkspacePathError("workspace_identity_changed")
            except ToolCancelled:
                raise RuntimeCancelled from None
            except WorkspacePathError as error:
                return (
                    tool_error(
                        call.name,
                        error.code,
                        "Shell workspace changed before execution",
                    ),
                    None,
                )
            output_stream = StreamingSensitiveScanner(
                self.dependencies.sensitive,
                on_safe_text=stream_safe_output,
            )
            output_scan_failed = False

            def scan_shell_output(text: str) -> None:
                nonlocal output_scan_failed
                if output_scan_failed:
                    return
                try:
                    output_stream.feed(text)
                except SensitiveScanError:
                    output_scan_failed = True

            try:
                raw_result = run_shell(
                    runtime.implementation.executor.workspace,  # type: ignore[attr-defined]
                    command,
                    approved_cwd,
                    timeout,
                    cancel,
                    scan_shell_output,
                    self.dependencies.resources,
                    str(item["id"]),
                    attempt,
                    active_skill_roots=active_skill_roots,
                    skill_invocation=skill_invocation,
                )
            except PermissionError as error:
                result = tool_error(
                    call.name,
                    "process_start_failed",
                    "Shell process could not be started",
                )
                if skill_invocation is not None:
                    result_data = result.get("data")
                    if not isinstance(result_data, dict):
                        result_data = {}
                        result["data"] = result_data
                    result_data.update(skill_invocation.result_data())
                denial = (
                    SandboxDenied(
                        category=SandboxDenialCategory.PROCESS,
                        summary="Seatbelt denied process start",
                        evidence=str(error),
                    )
                    if attempt.sandbox is SandboxType.MACOS_SEATBELT
                    and error.errno in {errno.EACCES, errno.EPERM}
                    else None
                )
                return result, denial
            try:
                manifest_after = (
                    runtime.implementation.executor.refresh_workspace_index(  # type: ignore[attr-defined]
                        cancel
                    )
                )
            except WorkspacePathError as error:
                manifest_after = (
                    runtime.implementation.executor.workspace_index.manifest()  # type: ignore[attr-defined]
                )
                refresh_error_code = error.code
                if error.code not in {
                    "WORKSPACE_INDEX_INCOMPLETE",
                    "sensitive_workspace_content",
                }:
                    raw_result["reconciliationRequired"] = True
            workspace_diff = diff_workspace_manifests(
                manifest_before, manifest_after
            )
            if (
                raw_result.get("outcome") == "success"
                and attempt.sandbox is SandboxType.MACOS_SEATBELT
            ):
                raw_result["sideEffectsMayExist"] = False
            result = bounded_tool_result(
                call.name, attach_workspace_diff(raw_result, workspace_diff)
            )
            try:
                if output_scan_failed:
                    raise SensitiveScanError("shell output scan failed")
                output_stream.finish()
                result = safe_tool_result(
                    self.dependencies.sensitive, call.name, result
                )
            except SensitiveScanError:
                result = tool_error(
                    call.name,
                    "sensitive_content_rejected",
                    "Shell output was withheld",
                )
            data = (
                result.get("data")
                if isinstance(result.get("data"), dict)
                else {}
            )
            denial = detect_sandbox_denial(
                sandboxed=attempt.sandbox is SandboxType.MACOS_SEATBELT,
                exit_code=(
                    data.get("exitCode")
                    if isinstance(data.get("exitCode"), int)
                    else None
                ),
                stdout=str(data.get("stdout", "")),
                stderr=str(data.get("stderr", "")),
            )
            return result, denial

        def approve(request: OrchestratorApprovalRequest) -> bool:
            summary = request.effective_permissions
            mode = (
                "unsandboxed"
                if request.approval_kind == "escalated"
                else "expanded_sandbox"
                if request.approval_kind == "additional_permissions"
                else "default_sandbox"
            )
            description = {
                "kind": "command_execution",
                "summary": (
                    "Run shell command without the macOS sandbox"
                    if mode == "unsandboxed"
                    else "Run shell command with expanded sandbox permissions"
                    if mode == "expanded_sandbox"
                    else "Run shell command"
                ),
                "command": command,
                "cwd": cwd_value,
                "networkEnabled": bool(summary.get("networkEnabled")),
                "timeoutSeconds": timeout,
                "executionMode": mode,
                "sandboxPermissions": request.sandbox_permissions.value,
                "additionalReadAccess": summary.get("read", []),
                "additionalWriteAccess": summary.get("write", []),
                "additionalExecutableAccess": summary.get("execute", []),
                "attemptOrdinal": request.attempt_ordinal,
                **(
                    {"reason": shell_input.justification}
                    if shell_input.justification is not None
                    else {}
                ),
                **(
                    {"escalationReason": request.escalation_reason}
                    if request.escalation_reason is not None
                    else {}
                ),
            }
            prepared = PreparedToolExecution(
                approval_description=description,
                transition_reason=(
                    "shell_escalation_approval"
                    if request.attempt_ordinal == 1
                    else "shell_approval"
                ),
                intent_preconditions={
                    "command": command,
                    "cwd": cwd_value,
                    "timeoutSeconds": timeout,
                    "sandboxPermissions": shell_input.sandboxPermissions.value,
                    "additionalPermissions": (
                        shell_input.additionalPermissions.model_dump(
                            mode="json", by_alias=True, exclude_none=True
                        )
                        if shell_input.additionalPermissions is not None
                        else None
                    ),
                    "workspaceIdentity": [
                        runtime.implementation.executor.workspace.device,  # type: ignore[attr-defined]
                        runtime.implementation.executor.workspace.inode,  # type: ignore[attr-defined]
                        runtime.implementation.executor.workspace.owner,  # type: ignore[attr-defined]
                    ],
                },
                approval_request={
                    **description,
                    "approvalKey": request.approval_key,
                    "effectivePermissions": request.effective_permissions,
                },
                attempt_ordinal=request.attempt_ordinal,
                approval_kind=request.approval_kind,
            )
            approval = self.dependencies.authorize_side_effect(
                run_id=run_id,
                item=item,
                prepared=prepared,
                cancel=cancel,
            )
            return approval.decision == "approve"

        def record_attempt(
            attempt: SandboxAttempt,
            status: str,
            result: dict[str, object] | None,
        ) -> None:
            self.dependencies.store.record_tool_attempt(
                str(item["id"]),
                ordinal=attempt.ordinal,
                sandbox_type=attempt.sandbox.value,
                sandbox_requested=attempt.sandbox_requested,
                effective_permissions=attempt.permissions.model_dump(
                    mode="json", by_alias=True
                ),
                profile_hash=attempt.profile_hash,
                escalation_reason=attempt.escalation_reason,
                status=status,
                result_code=(
                    str(result.get("code")) if result is not None else None
                ),
            )

        workspace = runtime.implementation.executor.workspace  # type: ignore[attr-defined]
        request = ShellOrchestrationRequest(shell_input, workspace, cwd)
        workspace_execution = PreparedToolExecution(
            approval_description={
                "kind": "command_execution",
                "summary": "Run shell command in the workspace sandbox",
            },
            transition_reason="workspace_shell_authorized",
            intent_preconditions={
                "authorization": "workspace_sandbox",
                "command": command,
                "cwd": cwd_value,
                "timeoutSeconds": timeout,
                "sandboxPermissions": shell_input.sandboxPermissions.value,
                "additionalPermissions": None,
                "workspaceIdentity": [
                    workspace.device,
                    workspace.inode,
                    workspace.owner,
                ],
            },
        )
        orchestration = ToolOrchestrator().run(
            ShellOrchestrationRuntime(execute_shell_attempt),
            request,
            OrchestratorContext(
                tool_call_id=str(item["toolCall"]["id"]),  # type: ignore[index]
                workspace_root=workspace.path,
                workspace_identity=(
                    workspace.device,
                    workspace.inode,
                    workspace.owner,
                ),
                cwd=cwd.path,
                timeout_seconds=timeout,
                cancel=cancel,
                base_permissions=base_permissions,
            ),
            approve=approve,
            authorize_without_approval=lambda: (
                self.dependencies.authorize_workspace_side_effect(
                    item=item,
                    prepared=workspace_execution,
                )
            ),
            record_attempt=record_attempt,
        )
        result = orchestration.result
        if workspace_diff is not None:
            result = attach_workspace_diff(result, workspace_diff)
        if result.get("code") in {"user_rejected", "user_rejected_escalation"}:
            return HandlerOutcome(
                tool_result(
                    call.name,
                    "declined",
                    "user_rejected",
                    APPROVAL_REJECTION_GUIDANCE,
                    result.get("data")
                    if isinstance(result.get("data"), dict)
                    else None,
                    side_effects_may_exist=(
                        result.get("sideEffectsMayExist") is True
                    ),
                    reconciliation_required=(
                        result.get("reconciliationRequired") is True
                    ),
                ),
                "declined",
                "failed",
            )
        if result["outcome"] == "success":
            self.dependencies.store.clear_rejects(run_id)
        status = "completed" if result["outcome"] == "success" else "failed"
        changed = workspace_diff.changed if workspace_diff is not None else False
        reconciliation_disposition = classify_shell_reconciliation(
            result,
            manifest_before_complete=manifest_before.complete,
            manifest_after_complete=manifest_after.complete,
            refresh_error_code=refresh_error_code,
        )
        return HandlerOutcome(
            result,
            status,
            status,
            workspace_changed=changed,
            diff_hash=(
                workspace_diff.diff_hash
                if changed and workspace_diff is not None
                else None
            ),
            reconciliation_disposition=reconciliation_disposition,
        )


class ExternalToolHandler:
    def __init__(self, dependencies: _HandlerDependencies) -> None:
        self.dependencies = dependencies

    def execute(
        self,
        run_id: str,
        item: dict[str, object],
        call: ModelToolCall,
        cancel: threading.Event,
        runtime: ExternalToolRuntime,
    ) -> HandlerOutcome:
        implementation = runtime.implementation
        connection = implementation.connection  # type: ignore[attr-defined]
        config = connection.config
        details = {
            "provenance": runtime.provenance.model_dump(
                mode="json", by_alias=True, exclude_none=True
            ),
            "permissionProfile": config.permission_profile,
            "timeoutSeconds": runtime.spec.timeout_seconds,
            "envNames": list(config.env_names),
        }
        prepared = PreparedToolExecution(
            approval_description={
                "kind": "external_tool",
                "summary": "Call an external MCP tool",
                "toolName": call.name,
                "arguments": call.arguments,
                **details,
            },
            transition_reason="external_approval",
            intent_preconditions={
                "toolName": call.name,
                "provenance": details.get("provenance"),
                "permissionProfile": details.get("permissionProfile"),
                "timeoutSeconds": details.get("timeoutSeconds"),
            },
        )
        approval, verified = self.dependencies.execute_side_effect(
            run_id=run_id,
            item=item,
            prepared=prepared,
            cancel=cancel,
            execute=lambda: implementation.execute(call.arguments, cancel),
        )
        if approval.decision != "approve":
            return HandlerOutcome(
                tool_result(
                    call.name,
                    "declined",
                    "user_rejected",
                    approval.feedback or APPROVAL_REJECTION_GUIDANCE,
                ),
                "declined",
                "failed",
            )
        assert verified is not None
        result = verified.result
        if result["outcome"] == "success":
            self.dependencies.store.clear_rejects(run_id)
        status = "completed" if result["outcome"] == "success" else "failed"
        return HandlerOutcome(result, status, status)


class EidosStateToolHandler:
    def __init__(self, dependencies: _HandlerDependencies) -> None:
        self.dependencies = dependencies

    def execute(
        self,
        run_id: str,
        item: dict[str, object],
        call: ModelToolCall,
        cancel: threading.Event,
        runtime: EidosStateToolRuntime,
    ) -> HandlerOutcome:
        implementation = runtime.implementation
        if runtime.network_prepare:
            details = implementation.network_approval_details(  # type: ignore[attr-defined]
                call.arguments
            )
            network_execution = PreparedToolExecution(
                approval_description={
                    "kind": "network_access",
                    "summary": "Download a public GitHub skill",
                    "toolName": call.name,
                    "hosts": details.get("hosts", []),
                    "target": details.get("target", ""),
                },
                transition_reason="network_approval",
                intent_preconditions={
                    "toolName": call.name,
                    "hosts": details.get("hosts", []),
                    "target": details.get("target", ""),
                },
            )
            approval, verified = self.dependencies.execute_side_effect(
                run_id=run_id,
                item=item,
                prepared=network_execution,
                cancel=cancel,
                execute=lambda: {
                    "prepared": implementation.download_eidos_state(  # type: ignore[attr-defined]
                        call.arguments, cancel
                    )
                },
            )
            if approval.decision != "approve":
                return HandlerOutcome(
                    tool_result(
                        call.name,
                        "declined",
                        "user_rejected_network",
                        approval.feedback or APPROVAL_REJECTION_GUIDANCE,
                    ),
                    "declined",
                )
            assert verified is not None
            prepared = verified.result["prepared"]
        else:
            prepared = implementation.prepare_eidos_state(  # type: ignore[attr-defined]
                call.arguments, cancel
            )
        if isinstance(prepared, dict):
            return HandlerOutcome(
                bounded_tool_result(call.name, prepared), "failed", "failed"
            )
        assert isinstance(prepared, SkillCreation)
        state_execution = PreparedToolExecution(
            approval_description={
                "kind": "file_change",
                "summary": f"Write {prepared.path}",
                "diff": prepared.diff,
            },
            approval_diff=prepared.diff,
            transition_reason="eidos_state_approval",
            intent_preconditions={
                "path": prepared.path,
                "qualifiedId": f"user:{prepared.name}",
                "contentHash": prepared.content_hash,
            },
        )
        approval, verified = self.dependencies.execute_side_effect(
            run_id=run_id,
            item=item,
            prepared=state_execution,
            cancel=cancel,
            execute=lambda: implementation.commit_eidos_state(  # type: ignore[attr-defined]
                prepared, cancel
            ),
        )
        if approval.decision == "reject":
            return HandlerOutcome(
                tool_result(
                    call.name,
                    "declined",
                    "user_rejected",
                    approval.feedback or APPROVAL_REJECTION_GUIDANCE,
                    {"path": prepared.path},
                ),
                "declined",
            )
        assert verified is not None
        result = verified.result
        if result["outcome"] == "success":
            self.dependencies.store.clear_rejects(run_id)
        status = "completed" if result["outcome"] == "success" else "failed"
        return HandlerOutcome(result, status, status)


class ToolCallRuntime:
    """Owns validated ToolCall execution for one immutable Step."""

    def __init__(
        self,
        store: SessionStore,
        dispatcher: ToolDispatcher,
        approval: ApprovalCoordinator,
        events: RuntimeEvents,
        sensitive: SensitiveScanner,
        state_machine: RuntimePhaseTracker,
        *,
        shell_available: bool,
        base_permissions: BasePermissionProfile,
        async_kernel: RuntimeAsyncKernel | None = None,
        resource_registry: ResourceRegistry | None = None,
        skill_access: SkillAccess | None = None,
    ) -> None:
        self.store = store
        self.dispatcher = dispatcher
        self.events = events
        self.sensitive = sensitive
        self.state_machine = state_machine
        self.async_kernel = async_kernel
        self.skill_access = skill_access
        self.concurrency = ToolConcurrencyGate()
        self.controller = ToolExecutionController(
            store,
            dispatcher,
            self,
            events,
            sensitive,
            approval=approval,
            resource_registry=resource_registry,
        )
        dependencies = _HandlerDependencies(
            store,
            dispatcher,
            events,
            sensitive,
            shell_available,
            self.controller.execute_side_effect,
            self.controller.authorize_side_effect,
            self.controller.execute_workspace_side_effect,
            self.controller.authorize_workspace_side_effect,
            self.controller.resources,
            base_permissions,
            self.skill_access,
        )
        self.read_runtime = ReadOnlyToolHandler(dependencies)
        self.workspace_runtime = FileChangeToolHandler(dependencies)
        self.shell_runtime = ShellToolHandler(dependencies)
        self.external_runtime = ExternalToolHandler(dependencies)
        self.eidos_state_runtime = EidosStateToolHandler(dependencies)

    def invoke_read(
        self, runtime: AdapterToolRuntime, run_id: str,
        item: dict[str, object], call: ModelToolCall,
        cancel: threading.Event,
    ) -> HandlerOutcome:
        return self.read_runtime.execute(run_id, item, call, cancel, runtime)

    def invoke_workspace_mutation(
        self, runtime: WorkspaceMutationRuntime, run_id: str,
        item: dict[str, object], call: ModelToolCall,
        cancel: threading.Event,
    ) -> HandlerOutcome:
        return self.workspace_runtime.execute(
            run_id, item, call, cancel, runtime
        )

    def invoke_shell(
        self, runtime: ShellToolRuntime, run_id: str,
        item: dict[str, object], call: ModelToolCall,
        cancel: threading.Event,
    ) -> HandlerOutcome:
        return self.shell_runtime.execute(run_id, item, call, cancel, runtime)

    def invoke_external(
        self, runtime: ExternalToolRuntime, run_id: str,
        item: dict[str, object], call: ModelToolCall,
        cancel: threading.Event,
    ) -> HandlerOutcome:
        return self.external_runtime.execute(
            run_id, item, call, cancel, runtime
        )

    def invoke_eidos_state(
        self, runtime: EidosStateToolRuntime, run_id: str,
        item: dict[str, object], call: ModelToolCall,
        cancel: threading.Event,
    ) -> HandlerOutcome:
        return self.eidos_state_runtime.execute(
            run_id, item, call, cancel, runtime
        )

    def validate(
        self, step: StepContext, sampling: SamplingOutcome
    ) -> ToolBatchOutcome:
        result = self.dispatcher.validate(
            ModelResponse(text=sampling.text, tool_calls=sampling.tool_calls),
            step.tool_snapshot.available_names,
            step.tool_snapshot.tool_set_hash,
        )
        if result.error_code is not None:
            return ToolBatchOutcome(
                status="validation_failed",
                error_code=result.error_code,
                protocol_diagnostic=result.protocol_diagnostic,
            )
        if not result.tool_calls:
            return ToolBatchOutcome(status="no_tools")
        return ToolBatchOutcome(status="ready", tool_calls=result.tool_calls)

    def execute(
        self,
        step: StepContext,
        tool_calls: tuple[ModelToolCall, ...],
        cancel: threading.Event,
    ) -> ToolBatchOutcome:
        self.state_machine.track(RuntimeState.TOOL_EXECUTING, "model_tool_calls")
        if (
            self.dispatcher.is_parallel_read_batch(tool_calls)
            and self._parallel_arguments_are_safe(tool_calls)
            and self.async_kernel is not None
        ):
            self.store.clear_sensitive_tool_inputs(step.run_id)
            return self._execute_parallel_reads(step, tool_calls, cancel)
        errors: list[str] = []
        successes: list[str] = []
        context_facts: list[str] = []
        for batch_order, call in enumerate(tool_calls):
            self._check_cancel(step.run_id, cancel)
            try:
                arguments = self.sensitive.scan_json(call.arguments)
                if arguments != call.arguments:
                    raise SensitiveScanError("sensitive tool arguments")
            except SensitiveScanError:
                failures = self.store.record_sensitive_tool_input(step.run_id)
                self.store.complete_current_step(
                    step.run_id, "failed", reason="sensitive_tool_input"
                )
                if failures >= 2:
                    return ToolBatchOutcome(
                        status="paused",
                        pause_reason="repeated_sensitive_tool_input",
                    )
                self.state_machine.track(RuntimeState.THINKING, "safe_tool_feedback")
                return ToolBatchOutcome(
                    status="sensitive_rejected",
                    error_code="sensitive_tool_input_rejected",
                    feedback=({
                        "type": "tool_error",
                        "code": "sensitive_tool_input_rejected",
                    },),
                )
            assert isinstance(arguments, dict)
            self.store.clear_sensitive_tool_inputs(step.run_id)
            mutation = self.store.create_tool_item_committed(
                step.run_id,
                step.step_index,
                batch_order,
                call.provider_call_id,
                call.name,
                json.dumps(
                    arguments,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                provenance=self.dispatcher.provenance(call.name),
                tool_set_hash=step.tool_snapshot.tool_set_hash,
            )
            item = mutation.value
            self.events.publish(mutation, item=item)
            effective_call = ModelToolCall(
                call.provider_call_id, call.name, arguments
            )
            plan = self.dispatcher.plan(
                effective_call, step.tool_snapshot.binding(call.name)
            )
            assert (
                plan.descriptor is not None
                and plan.descriptor.execution_policy is not None
            )
            with self.concurrency.acquire(
                plan.descriptor.execution_policy.concurrency, cancel
            ):
                outcome = self.controller.execute(
                    run_id=step.run_id,
                    item=item,
                    call=effective_call,
                    plan=plan,
                    cancel=cancel,
                    deadline=None,
                )
            self._check_cancel(step.run_id, cancel)
            if outcome.activations:
                self.store.activate_tools(step.run_id, outcome.activations)
            if outcome.result.get("outcome") != "success":
                errors.append(_result_fingerprint(call.name, outcome.result))
            else:
                successes.append(
                    outcome.progress_fingerprint or _hash_json(outcome.result)
                )
            context_facts.append(_hash_json({
                "toolName": call.name,
                "arguments": arguments,
                "resultFingerprint": (
                    outcome.progress_fingerprint or _hash_json(outcome.result)
                ),
            }))
            if (
                outcome.result.get("reconciliationRequired") is True
                and (
                    plan.is_external
                    or plan.is_eidos_state
                    or (
                        plan.is_shell
                        and outcome.reconciliation_disposition
                        is not ReconciliationDisposition.CONTINUE_READ_ONLY
                    )
                )
            ):
                self.store.complete_current_step(
                    step.run_id,
                    "failed",
                    reason=str(outcome.result.get("code")),
                )
                pause_reason = (
                    "external_tool_reconciliation_required"
                    if plan.is_external
                    else "shell_reconciliation_required"
                    if plan.is_shell
                    else "eidos_state_reconciliation_required"
                )
                mutation = self.store.interrupt_run_committed(step.run_id)
                self.events.publish(mutation, run=mutation.value)
                return ToolBatchOutcome(
                    status="paused", pause_reason=pause_reason
                )

        self.state_machine.track(RuntimeState.THINKING, "tool_batch_completed")
        facts = self.store.context_projection_facts(step.run_id)
        return ToolBatchOutcome(
            status="completed",
            error_fingerprints=tuple(errors),
            workspace_version=facts.workspace_version,
            diff_hash=facts.last_diff_hash,
            successful_tool_result_hashes=tuple(successes),
            context_fact_ids=tuple(context_facts),
            reconciliation_epoch=facts.reconciliation_epoch,
        )

    def _parallel_arguments_are_safe(
        self, calls: tuple[ModelToolCall, ...]
    ) -> bool:
        try:
            return all(self.sensitive.scan_json(call.arguments) == call.arguments for call in calls)
        except SensitiveScanError:
            return False

    def _execute_parallel_reads(
        self,
        step: StepContext,
        calls: tuple[ModelToolCall, ...],
        cancel: threading.Event,
    ) -> ToolBatchOutcome:
        pending: list[tuple[dict[str, object], ModelToolCall]] = []
        for batch_order, call in enumerate(calls):
            self._check_cancel(step.run_id, cancel)
            mutation = self.store.create_tool_item_committed(
                step.run_id,
                step.step_index,
                batch_order,
                call.provider_call_id,
                call.name,
                json.dumps(
                    call.arguments,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                provenance=self.dispatcher.provenance(call.name),
                tool_set_hash=step.tool_snapshot.tool_set_hash,
            )
            pending.append((mutation.value, call))
            self.events.publish(mutation, item=mutation.value)

        batch_cancel = threading.Event()

        class BatchCancellation(threading.Event):
            def is_set(self) -> bool:
                return (
                    super().is_set()
                    or cancel.is_set()
                    or batch_cancel.is_set()
                )

            def wait(self, timeout: float | None = None) -> bool:
                if self.is_set():
                    return True
                return batch_cancel.wait(
                    0.05 if timeout is None else min(timeout, 0.05)
                ) or self.is_set()

        controlled_cancel = BatchCancellation()

        def run(entry: tuple[dict[str, object], ModelToolCall]) -> HandlerOutcome:
            item, call = entry
            try:
                plan = self.dispatcher.plan(
                    call, step.tool_snapshot.binding(call.name)
                )
                assert (
                    plan.descriptor is not None
                    and plan.descriptor.execution_policy is not None
                )
                with self.concurrency.acquire(
                    plan.descriptor.execution_policy.concurrency,
                    controlled_cancel,
                ):
                    return self.controller.execute(
                        run_id=step.run_id,
                        item=item,
                        call=call,
                        plan=plan,
                        cancel=controlled_cancel,
                        deadline=None,
                    )
            except RuntimeCancelled:
                raise
            except ToolInfrastructureError:
                raise
            except Exception:
                return HandlerOutcome(
                    tool_error(call.name, "tool_execution_failed", "Tool execution failed"),
                    "failed",
                    "failed",
                )

        async def coordinate() -> tuple[HandlerOutcome, ...]:
            results: list[HandlerOutcome | None] = [None] * len(pending)
            infrastructure_errors: dict[int, ToolInfrastructureError] = {}
            runtime_cancellations: set[int] = set()

            async def worker(
                index: int,
                entry: tuple[dict[str, object], ModelToolCall],
            ) -> None:
                try:
                    results[index] = await anyio.to_thread.run_sync(run, entry)
                except ToolInfrastructureError as error:
                    infrastructure_errors[index] = error
                    batch_cancel.set()
                except RuntimeCancelled:
                    batch_cancel.set()
                    runtime_cancellations.add(index)

            async with anyio.create_task_group() as group:
                for index, entry in enumerate(pending):
                    group.start_soon(worker, index, entry)

            if infrastructure_errors:
                self.store.complete_current_step(
                    step.run_id,
                    "failed",
                    reason="TOOL_INFRASTRUCTURE_FAILURE",
                )
                raise infrastructure_errors[min(infrastructure_errors)]
            if runtime_cancellations:
                raise RuntimeCancelled
            if any(result is None for result in results):
                raise ToolInfrastructureError("parallel tool result missing")
            return tuple(result for result in results if result is not None)

        assert self.async_kernel is not None
        try:
            outcomes = self.async_kernel.call(coordinate)
        except AsyncKernelClosedError as error:
            self.store.complete_current_step(
                step.run_id,
                "failed",
                reason="TOOL_INFRASTRUCTURE_FAILURE",
            )
            raise ToolInfrastructureError(
                "runtime async kernel is unavailable"
            ) from error

        errors: list[str] = []
        successes: list[str] = []
        context_facts: list[str] = []
        for (item, call), outcome in zip(pending, outcomes, strict=True):
            if outcome.activations:
                self.store.activate_tools(step.run_id, outcome.activations)
            if outcome.result.get("outcome") != "success":
                errors.append(_result_fingerprint(call.name, outcome.result))
            else:
                successes.append(
                    outcome.progress_fingerprint or _hash_json(outcome.result)
                )
            context_facts.append(_hash_json({
                "toolName": call.name,
                "arguments": call.arguments,
                "resultFingerprint": (
                    outcome.progress_fingerprint or _hash_json(outcome.result)
                ),
            }))
            self._check_cancel(step.run_id, cancel)
        self.state_machine.track(RuntimeState.THINKING, "tool_batch_completed")
        facts = self.store.context_projection_facts(step.run_id)
        return ToolBatchOutcome(
            status="completed",
            error_fingerprints=tuple(errors),
            workspace_version=facts.workspace_version,
            diff_hash=facts.last_diff_hash,
            successful_tool_result_hashes=tuple(successes),
            context_fact_ids=tuple(context_facts),
            reconciliation_epoch=facts.reconciliation_epoch,
        )

    def _check_cancel(self, run_id: str, cancel: threading.Event) -> None:
        if cancel.is_set() or self.store.read_run(run_id)["status"] in {
            "canceled",
            "interrupted",
        }:
            raise RuntimeCancelled


def _result_fingerprint(tool_name: str, result: dict[str, object]) -> str:
    return _hash_json(
        {
            "toolName": tool_name,
            "outcome": result.get("outcome"),
            "code": result.get("code"),
        }
    )


def _hash_json(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")).hexdigest()

from __future__ import annotations

from dataclasses import dataclass
import json
import threading

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.extensions.skills import SkillCreation
from eidos_runtime.model.client import ModelResponse, ModelToolCall
from eidos_runtime.runtime.approval import ApprovalCoordinator
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
from eidos_runtime.runtime.state_machine import RuntimePhaseTracker, RuntimeState
from eidos_runtime.runtime.tool_dispatcher import ToolDispatcher
from eidos_runtime.sandbox.sensitive import (
    SensitiveScanError,
    SensitiveScanner,
    StreamingSensitiveScanner,
)
from eidos_runtime.sandbox.shell import run_shell
from eidos_runtime.tools.workspace import ToolCancelled, WorkspacePathError


@dataclass(frozen=True)
class HandlerOutcome:
    result: dict[str, object]
    item_status: str
    tool_status: str = "completed"
    activations: tuple[str, ...] = ()


@dataclass(frozen=True)
class _HandlerDependencies:
    store: SessionStore
    dispatcher: ToolDispatcher
    approval: ApprovalCoordinator
    events: RuntimeEvents
    sensitive: SensitiveScanner
    shell_available: bool

    def safe_result(
        self, tool_name: str, result: dict[str, object]
    ) -> dict[str, object]:
        return safe_tool_result(
            self.sensitive, tool_name, bounded_tool_result(tool_name, result)
        )


class ReadOnlyToolHandler:
    def __init__(self, dependencies: _HandlerDependencies) -> None:
        self.dependencies = dependencies

    def execute(
        self,
        _run_id: str,
        _item: dict[str, object],
        call: ModelToolCall,
        cancel: threading.Event,
    ) -> HandlerOutcome:
        result = self.dependencies.safe_result(
            call.name,
            self.dependencies.dispatcher.execute_read_only(call, cancel),
        )
        return HandlerOutcome(
            result,
            "completed",
            activations=self.dependencies.dispatcher.consume_activations(call.name),
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
    ) -> HandlerOutcome:
        prepared = self.dependencies.dispatcher.prepare_file_change(
            call.name, call.arguments, cancel
        )
        if isinstance(prepared, dict):
            return HandlerOutcome(
                bounded_tool_result(call.name, prepared), "failed", "failed"
            )
        if prepared.base_sha256 is not None and not self.dependencies.store.has_read_evidence(
            run_id, prepared.path, prepared.base_sha256
        ):
            return HandlerOutcome(
                tool_error(
                    call.name,
                    "read_evidence_required",
                    "Read the current file before proposing a change",
                ),
                "failed",
                "failed",
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
        approval = self.dependencies.approval.request(
            run_id,
            item,
            {
                "kind": "file_change",
                "summary": f"Modify {prepared.path}",
                "diff": prepared.diff,
            },
            cancel,
            diff=prepared.diff,
            base_sha256=prepared.base_sha256,
            transition_reason="file_approval",
        )
        if approval.decision == "reject":
            return HandlerOutcome(
                tool_result(
                    call.name,
                    "declined",
                    "user_rejected",
                    "User rejected the file change",
                    {"path": prepared.path},
                ),
                "declined",
            )
        self.dependencies.store.begin_durable_intent(
            str(item["id"]),
            preconditions={
                "path": prepared.path,
                "baseSha256": prepared.base_sha256,
            },
        )
        result = self.dependencies.safe_result(
            call.name,
            self.dependencies.dispatcher.commit_file_change(
                call.name, prepared, cancel
            ),
        )
        if result["outcome"] == "success" and result.get("code") != "no_changes":
            self.dependencies.store.clear_rejects(run_id)
        status = "completed" if result["outcome"] == "success" else "failed"
        return HandlerOutcome(result, status, status)


class ShellToolHandler:
    def __init__(self, dependencies: _HandlerDependencies) -> None:
        self.dependencies = dependencies

    def execute(
        self,
        run_id: str,
        item: dict[str, object],
        call: ModelToolCall,
        cancel: threading.Event,
    ) -> HandlerOutcome:
        if not self.dependencies.shell_available:
            return HandlerOutcome(
                tool_error(
                    call.name, "sandbox_unavailable", "Shell sandbox is unavailable"
                ),
                "failed",
                "failed",
            )
        command = call.arguments["command"]
        cwd_value = call.arguments.get("cwd", ".")
        timeout = call.arguments.get("timeoutSeconds", 120)
        assert isinstance(command, str)
        assert isinstance(cwd_value, str)
        assert isinstance(timeout, int)
        try:
            cwd = self.dependencies.dispatcher.prepare_shell(
                call.name, cwd_value, cancel
            )
        except ToolCancelled:
            raise RuntimeCancelled from None
        except WorkspacePathError as error:
            return HandlerOutcome(
                tool_error(call.name, error.code, "Shell workspace is unsafe"),
                "failed",
                "failed",
            )
        approval = self.dependencies.approval.request(
            run_id,
            item,
            {
                "kind": "command_execution",
                "summary": "Run shell command",
                "command": command,
                "cwd": cwd_value,
                "networkEnabled": False,
                "timeoutSeconds": timeout,
            },
            cancel,
            transition_reason="shell_approval",
        )
        if approval.decision != "approve":
            return HandlerOutcome(
                tool_result(
                    call.name,
                    "declined",
                    "user_rejected",
                    "User rejected the command",
                ),
                "declined",
                "failed",
            )
        self.dependencies.store.begin_durable_intent(
            str(item["id"]),
            preconditions={"cwd": cwd_value, "timeoutSeconds": timeout},
        )
        try:
            approved_cwd = self.dependencies.dispatcher.prepare_shell(
                call.name, cwd_value, cancel
            )
            if approved_cwd != cwd:
                raise WorkspacePathError("workspace_identity_changed")
        except ToolCancelled:
            raise RuntimeCancelled from None
        except WorkspacePathError as error:
            return HandlerOutcome(
                tool_error(
                    call.name,
                    error.code,
                    "Shell workspace changed after approval",
                ),
                "failed",
                "failed",
            )
        output_stream = StreamingSensitiveScanner(self.dependencies.sensitive)
        result = bounded_tool_result(
            call.name,
            run_shell(
                self.dependencies.dispatcher.workspace,
                command,
                approved_cwd,
                timeout,
                cancel,
                output_stream.feed,
            ),
        )
        try:
            safe_output = output_stream.finish().text
            result = safe_tool_result(self.dependencies.sensitive, call.name, result)
        except SensitiveScanError:
            safe_output = ""
            result = tool_error(
                call.name,
                "sensitive_content_rejected",
                "Shell output was withheld",
            )
        if safe_output:
            mutation = self.dependencies.store.append_item_deltas_committed(
                str(item["id"]), (safe_output,), 1
            )
            self.dependencies.events.publish(mutation, item=mutation.value)
        if result["outcome"] == "success":
            self.dependencies.store.clear_rejects(run_id)
        status = "completed" if result["outcome"] == "success" else "failed"
        return HandlerOutcome(result, status, status)


class ExternalToolHandler:
    def __init__(self, dependencies: _HandlerDependencies) -> None:
        self.dependencies = dependencies

    def execute(
        self,
        run_id: str,
        item: dict[str, object],
        call: ModelToolCall,
        cancel: threading.Event,
    ) -> HandlerOutcome:
        details = self.dependencies.dispatcher.external_approval_details(call.name)
        approval = self.dependencies.approval.request(
            run_id,
            item,
            {
                "kind": "external_tool",
                "summary": "Call an external MCP tool",
                "toolName": call.name,
                "arguments": call.arguments,
                **details,
            },
            cancel,
            transition_reason="external_approval",
        )
        if approval.decision != "approve":
            return HandlerOutcome(
                tool_result(
                    call.name,
                    "declined",
                    "user_rejected",
                    "User rejected the external tool",
                ),
                "declined",
                "failed",
            )
        self.dependencies.store.begin_durable_intent(
            str(item["id"]),
            preconditions={
                "toolName": call.name,
                "provenance": details.get("provenance"),
                "permissionProfile": details.get("permissionProfile"),
                "timeoutSeconds": details.get("timeoutSeconds"),
            },
        )
        result = self.dependencies.safe_result(
            call.name,
            self.dependencies.dispatcher.execute_external(call, cancel),
        )
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
    ) -> HandlerOutcome:
        if self.dependencies.dispatcher.plan(call).is_network_eidos_state:
            details = self.dependencies.dispatcher.network_approval_details(
                call.name, call.arguments
            )
            approval = self.dependencies.approval.request(
                run_id,
                item,
                {
                    "kind": "network_access",
                    "summary": "Download a public GitHub skill",
                    "toolName": call.name,
                    "hosts": details.get("hosts", []),
                    "target": details.get("target", ""),
                },
                cancel,
                transition_reason="network_approval",
            )
            if approval.decision != "approve":
                return HandlerOutcome(
                    tool_result(
                        call.name,
                        "declined",
                        "user_rejected_network",
                        "User rejected network access",
                    ),
                    "declined",
                )
            prepared = self.dependencies.dispatcher.download_eidos_state(
                call.name, call.arguments, cancel
            )
        else:
            prepared = self.dependencies.dispatcher.prepare_eidos_state(
                call.name, call.arguments, cancel
            )
        if isinstance(prepared, dict):
            return HandlerOutcome(
                bounded_tool_result(call.name, prepared), "failed", "failed"
            )
        assert isinstance(prepared, SkillCreation)
        approval = self.dependencies.approval.request(
            run_id,
            item,
            {
                "kind": "file_change",
                "summary": f"Write {prepared.path}",
                "diff": prepared.diff,
            },
            cancel,
            diff=prepared.diff,
            transition_reason="eidos_state_approval",
        )
        if approval.decision == "reject":
            return HandlerOutcome(
                tool_result(
                    call.name,
                    "declined",
                    "user_rejected",
                    "User rejected the Eidos state change",
                    {"path": prepared.path},
                ),
                "declined",
            )
        self.dependencies.store.begin_durable_intent(
            str(item["id"]),
            preconditions={
                "path": prepared.path,
                "qualifiedId": f"user:{prepared.name}",
                "contentHash": prepared.content_hash,
            },
        )
        result = self.dependencies.safe_result(
            call.name,
            self.dependencies.dispatcher.commit_eidos_state(
                call.name, prepared, cancel
            ),
        )
        if result["outcome"] == "success":
            self.dependencies.store.clear_rejects(run_id)
        status = "completed" if result["outcome"] == "success" else "failed"
        return HandlerOutcome(result, status, status)


class ToolCallRuntime:
    """Owns validated, serial ToolCall execution for one immutable Step."""

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
    ) -> None:
        self.store = store
        self.dispatcher = dispatcher
        self.events = events
        self.sensitive = sensitive
        self.state_machine = state_machine
        dependencies = _HandlerDependencies(
            store,
            dispatcher,
            approval,
            events,
            sensitive,
            shell_available,
        )
        self.handlers = {
            "read": ReadOnlyToolHandler(dependencies),
            "file": FileChangeToolHandler(dependencies),
            "shell": ShellToolHandler(dependencies),
            "external": ExternalToolHandler(dependencies),
            "eidos_state": EidosStateToolHandler(dependencies),
            "network_eidos_state": EidosStateToolHandler(dependencies),
        }

    def validate(
        self, step: StepContext, sampling: SamplingOutcome
    ) -> ToolBatchOutcome:
        result = self.dispatcher.validate(
            ModelResponse(sampling.text, sampling.tool_calls),
            step.tool_snapshot.available_names,
        )
        if result.error_code is not None:
            return ToolBatchOutcome(
                status="validation_failed", error_code=result.error_code
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
                    mutation = self.store.pause_run_committed(
                        step.run_id, "repeated_sensitive_tool_input"
                    )
                    self.events.publish(mutation, run=mutation.value)
                    self.state_machine.track(
                        RuntimeState.WAITING_USER_INPUT,
                        "repeated_sensitive_tool_input",
                    )
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
            plan = self.dispatcher.plan(effective_call)
            if plan.execution_kind != "read" and self.store.side_effects_blocked(
                step.run_id
            ):
                summary = (
                    "External outcome must be reconciled"
                    if plan.is_external
                    else "A successful read-only observation is required"
                )
                outcome = HandlerOutcome(
                    tool_error(call.name, "reconciliation_required", summary),
                    "failed",
                    "failed",
                )
            else:
                handler = self.handlers.get(plan.execution_kind)
                if handler is None:
                    outcome = HandlerOutcome(
                        tool_error(
                            call.name, "tool_unavailable", "Tool is unavailable"
                        ),
                        "failed",
                        "failed",
                    )
                else:
                    outcome = handler.execute(
                        step.run_id, item, effective_call, cancel
                    )
            mutation = self.store.complete_tool_item_committed(
                str(item["id"]),
                json.dumps(
                    outcome.result,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
                item_status=outcome.item_status,
                tool_status=outcome.tool_status,
            )
            completed = mutation.value
            self.events.publish(mutation, item=completed)
            self._check_cancel(step.run_id, cancel)
            if outcome.activations:
                self.store.activate_tools(step.run_id, outcome.activations)
            if (
                outcome.result.get("reconciliationRequired") is True
                and (plan.is_external or plan.is_eidos_state)
            ):
                self.store.complete_current_step(
                    step.run_id,
                    "failed",
                    reason=str(outcome.result.get("code")),
                )
                pause_reason = (
                    "external_tool_reconciliation_required"
                    if plan.is_external
                    else "eidos_state_reconciliation_required"
                )
                mutation = self.store.pause_run_committed(step.run_id, pause_reason)
                self.state_machine.track(
                    RuntimeState.WAITING_USER_INPUT, pause_reason
                )
                self.events.publish(mutation, run=mutation.value)
                return ToolBatchOutcome(
                    status="paused", pause_reason=pause_reason
                )

        self.store.complete_current_step(step.run_id, "completed")
        self.state_machine.track(RuntimeState.THINKING, "tool_batch_completed")
        updated = self.store.read_run(step.run_id)
        if updated["status"] == "waiting_user_input":
            return ToolBatchOutcome(
                status="paused", pause_reason=str(updated.get("pauseReason"))
            )
        return ToolBatchOutcome(status="completed")

    def _check_cancel(self, run_id: str, cancel: threading.Event) -> None:
        if cancel.is_set() or self.store.read_run(run_id)["status"] in {
            "canceled",
            "interrupted",
        }:
            raise RuntimeCancelled

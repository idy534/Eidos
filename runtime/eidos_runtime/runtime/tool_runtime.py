from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
import hashlib
import json
import threading
from typing import Callable

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.extensions.skills import SkillCreation
from eidos_runtime.model.client import ModelResponse, ModelToolCall
from eidos_runtime.runtime.approval import ApprovalCoordinator, ApprovalOutcome
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
from eidos_runtime.runtime.state_machine import RuntimePhaseTracker, RuntimeState
from eidos_runtime.runtime.tool_dispatcher import ToolDispatcher
from eidos_runtime.runtime.tool_execution import (
    HandlerOutcome,
    PreparedToolExecution,
    ToolExecutionController,
    ToolInfrastructureError,
    VerifiedToolExecutionResult,
)
from eidos_runtime.sandbox.sensitive import (
    SensitiveScanError,
    SensitiveScanner,
    StreamingSensitiveScanner,
)
from eidos_runtime.sandbox.shell import run_shell
from eidos_runtime.sandbox.workspace_manifest import (
    attach_workspace_diff,
    diff_workspace_manifests,
)
from eidos_runtime.tools.workspace import ToolCancelled, WorkspacePathError


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
    resources: ResourceRegistry = field(default_factory=ResourceRegistry)

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
        result = self.dependencies.dispatcher.execute_read_only(call, cancel)
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
        prepared_execution = PreparedToolExecution(
            approval_description={
                "kind": "file_change",
                "summary": f"Modify {prepared.path}",
                "diff": prepared.diff,
            },
            approval_diff=prepared.diff,
            base_sha256=prepared.base_sha256,
            transition_reason="file_approval",
            intent_preconditions={
                "path": prepared.path,
                "baseSha256": prepared.base_sha256,
            },
        )
        approval, verified = self.dependencies.execute_side_effect(
            run_id=run_id,
            item=item,
            prepared=prepared_execution,
            cancel=cancel,
            execute=lambda: self.dependencies.dispatcher.commit_file_change(
                call.name, prepared, cancel
            ),
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
        assert verified is not None
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
        prepared_execution = PreparedToolExecution(
            approval_description={
                "kind": "command_execution",
                "summary": "Run shell command",
                "command": command,
                "cwd": cwd_value,
                "networkEnabled": False,
                "timeoutSeconds": timeout,
            },
            transition_reason="shell_approval",
            intent_preconditions={
                "cwd": cwd_value,
                "timeoutSeconds": timeout,
            },
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

        def execute_shell() -> dict[str, object]:
            nonlocal workspace_diff
            try:
                approved_cwd = self.dependencies.dispatcher.prepare_shell(
                    call.name, cwd_value, cancel
                )
                if approved_cwd != cwd:
                    raise WorkspacePathError("workspace_identity_changed")
            except ToolCancelled:
                raise RuntimeCancelled from None
            except WorkspacePathError as error:
                return tool_error(
                    call.name,
                    error.code,
                    "Shell workspace changed after approval",
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

            manifest_before = (
                self.dependencies.dispatcher.workspace_index.manifest()
            )
            raw_result = run_shell(
                self.dependencies.dispatcher.workspace,
                command,
                approved_cwd,
                timeout,
                cancel,
                scan_shell_output,
                self.dependencies.resources,
                str(item["id"]),
            )
            try:
                manifest_after = (
                    self.dependencies.dispatcher.refresh_workspace_index(
                        cancel
                    )
                )
            except WorkspacePathError:
                manifest_after = (
                    self.dependencies.dispatcher.workspace_index.manifest()
                )
            workspace_diff = diff_workspace_manifests(
                manifest_before, manifest_after
            )
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
            return result

        approval, verified = self.dependencies.execute_side_effect(
            run_id=run_id,
            item=item,
            prepared=prepared_execution,
            cancel=cancel,
            execute=execute_shell,
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
        assert verified is not None
        result = verified.result
        if result["outcome"] == "success":
            self.dependencies.store.clear_rejects(run_id)
        status = "completed" if result["outcome"] == "success" else "failed"
        changed = workspace_diff.changed if workspace_diff is not None else False
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
    ) -> HandlerOutcome:
        details = self.dependencies.dispatcher.external_approval_details(call.name)
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
            execute=lambda: self.dependencies.dispatcher.execute_external(
                call, cancel
            ),
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
    ) -> HandlerOutcome:
        if self.dependencies.dispatcher.plan(call).is_network_eidos_state:
            details = self.dependencies.dispatcher.network_approval_details(
                call.name, call.arguments
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
                    "prepared": self.dependencies.dispatcher.download_eidos_state(
                        call.name, call.arguments, cancel
                    )
                },
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
            assert verified is not None
            prepared = verified.result["prepared"]
        else:
            prepared = self.dependencies.dispatcher.prepare_eidos_state(
                call.name, call.arguments, cancel
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
            execute=lambda: self.dependencies.dispatcher.commit_eidos_state(
                call.name, prepared, cancel
            ),
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
        resource_registry: ResourceRegistry | None = None,
    ) -> None:
        self.store = store
        self.dispatcher = dispatcher
        self.events = events
        self.sensitive = sensitive
        self.state_machine = state_machine
        handlers = {}
        self.controller = ToolExecutionController(
            store,
            dispatcher,
            handlers,
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
            self.controller.resources,
        )
        handlers.update({
            "read": ReadOnlyToolHandler(dependencies),
            "file": FileChangeToolHandler(dependencies),
            "shell": ShellToolHandler(dependencies),
            "external": ExternalToolHandler(dependencies),
            "eidos_state": EidosStateToolHandler(dependencies),
            "network_eidos_state": EidosStateToolHandler(dependencies),
        })
        self.handlers = handlers

    def validate(
        self, step: StepContext, sampling: SamplingOutcome
    ) -> ToolBatchOutcome:
        result = self.dispatcher.validate(
            ModelResponse(text=sampling.text, tool_calls=sampling.tool_calls),
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
        if (
            self.dispatcher.is_parallel_read_batch(tool_calls)
            and self._parallel_arguments_are_safe(tool_calls)
        ):
            self.store.clear_sensitive_tool_inputs(step.run_id)
            return self._execute_parallel_reads(step, tool_calls, cancel)
        errors: list[str] = []
        successes: list[str] = []
        context_facts: list[str] = []
        snapshot_hashes = dict(step.tool_snapshot.spec_hashes)
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
            plan = self.dispatcher.plan(
                effective_call, snapshot_hashes.get(call.name)
            )
            outcome = self.controller.execute(
                run_id=step.run_id,
                item=item,
                call=effective_call,
                plan=plan,
                cancel=cancel,
                deadline=None,
            )
            completed = outcome.item or item
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
                and (plan.is_external or plan.is_eidos_state or plan.is_shell)
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
                mutation = self.store.pause_run_committed(step.run_id, pause_reason)
                self.state_machine.track(
                    RuntimeState.WAITING_USER_INPUT, pause_reason
                )
                self.events.publish(mutation, run=mutation.value)
                return ToolBatchOutcome(
                    status="paused", pause_reason=pause_reason
                )

        self.state_machine.track(RuntimeState.THINKING, "tool_batch_completed")
        updated = self.store.read_run(step.run_id)
        facts = self.store.context_projection_facts(step.run_id)
        return ToolBatchOutcome(
            status=(
                "paused"
                if updated["status"] == "waiting_user_input"
                else "completed"
            ),
            pause_reason=(
                str(updated.get("pauseReason"))
                if updated["status"] == "waiting_user_input"
                else None
            ),
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
                return self.controller.execute(
                    run_id=step.run_id,
                    item=item,
                    call=call,
                    plan=self.dispatcher.plan(
                        call, dict(step.tool_snapshot.spec_hashes).get(call.name)
                    ),
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

        with ThreadPoolExecutor(max_workers=len(pending)) as executor:
            futures: dict[
                Future[HandlerOutcome],
                tuple[dict[str, object], ModelToolCall],
            ] = {
                executor.submit(run, entry): entry for entry in pending
            }
            results: dict[str, HandlerOutcome] = {}
            infrastructure_error: ToolInfrastructureError | None = None
            runtime_cancelled = False
            for future in as_completed(futures):
                item, _call = futures[future]
                try:
                    results[str(item["id"])] = future.result()
                except ToolInfrastructureError as error:
                    infrastructure_error = error
                    batch_cancel.set()
                except RuntimeCancelled:
                    batch_cancel.set()
                    runtime_cancelled = True
            if infrastructure_error is not None:
                self.store.complete_current_step(
                    step.run_id,
                    "failed",
                    reason="TOOL_INFRASTRUCTURE_FAILURE",
                )
                raise infrastructure_error
            if runtime_cancelled:
                raise RuntimeCancelled
            outcomes = [
                results[str(item["id"])] for item, _call in pending
            ]

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

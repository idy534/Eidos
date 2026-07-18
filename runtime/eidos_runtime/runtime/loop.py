from __future__ import annotations

import json
import re
from dataclasses import dataclass
import threading
import time
from typing import Callable

from eidos_runtime.model.client import ModelClient, ModelResponse
from eidos_runtime.runtime.model_runner import ModelRunner, ModelStreamInterrupted
from eidos_runtime.runtime.tool_dispatcher import ToolDispatcher
from eidos_runtime.runtime.approval import ApprovalAdapter, ApprovalRequest
from eidos_runtime.runtime.events import RuntimeEvents
from eidos_runtime.runtime.errors import (
    bounded_tool_result as _bounded_tool_result,
    safe_tool_result,
    tool_error as _tool_error,
)
from eidos_runtime.sandbox.shell import run_shell
from eidos_runtime.sandbox.sensitive import (
    SensitiveContentDenied,
    SensitiveScanError,
    SensitiveScanner,
    StreamingSensitiveScanner,
    default_scanner,
)
from eidos_runtime.db.storage import (
    ContextLimitExceeded,
    InvalidRunStateError,
    RunLimitReached,
    SegmentLimitReached,
    SessionStore,
)
from eidos_runtime.extensions.plugins import PluginCatalog
from eidos_runtime.extensions.skills import SkillCatalog, SkillReadError
from eidos_runtime.extensions.mcp import McpManager
from eidos_runtime.runtime.state_machine import RuntimeState, StateMachine
from eidos_runtime.tools.workspace import (
    ToolCancelled,
    ToolExecutor,
    WorkspacePathError,
)
from eidos_runtime.tools.registry import ToolRegistry
from eidos_runtime.tools.search import tool_search_entry


MAX_ASSISTANT_BYTES = 512 * 1024
FINALIZATION_SECONDS = 60


class RunCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class ApprovalDecision:
    decision: str
    feedback: str | None = None


class RuntimeEngine:
    def __init__(
        self,
        store: SessionStore,
        model: ModelClient,
        notify: Callable[[dict[str, object]], None],
        request_approval: Callable[
            [dict[str, object], threading.Event], ApprovalDecision
        ]
        | None = None,
        shell_available: bool = False,
        monotonic: Callable[[], float] = time.monotonic,
        sensitive: SensitiveScanner | None = None,
        wait_for_execution_slot: Callable[[str, threading.Event], bool] | None = None,
        mcp_sandbox: bool = True,
    ) -> None:
        self.store = store
        self.model = model
        self.approval = ApprovalAdapter(request_approval)
        self.events = RuntimeEvents(notify)
        self.shell_available = shell_available
        self.monotonic = monotonic
        self.active_started: float | None = None
        self.sensitive = sensitive or default_scanner()
        self.wait_for_execution_slot = wait_for_execution_slot
        self.mcp_sandbox = mcp_sandbox
        self.state_machine = StateMachine()

    def run(self, run_id: str, cancel: threading.Event) -> None:
        run = self.store.read_run(run_id)
        user_item = self.store.get_user_item(run_id)
        self._notification("run/started", {"sessionId": run["sessionId"], "run": run})
        self._notification(
            "item/started",
            {
                "sessionId": run["sessionId"],
                "runId": run_id,
                "item": {
                    **{
                        key: value
                        for key, value in user_item.items()
                        if key != "completedAt"
                    },
                    "status": "in_progress",
                },
            },
        )
        self._notification(
            "item/completed",
            {"sessionId": run["sessionId"], "runId": run_id, "item": user_item},
        )

        try:
            context = self.store.model_context(run["sessionId"])
        except ContextLimitExceeded:
            self._fail(run_id, "CONTEXT_INPUT_TOO_LARGE")
            return
        tools = ToolExecutor(self.store.workspace_for_run(run_id))
        extension_snapshot = run.get("extensionSnapshot")
        if not isinstance(extension_snapshot, dict):
            extension_snapshot = {
                "schemaVersion": 1,
                "extensionContractVersion": 1,
                "plugins": [],
                "skillCatalogHash": "",
                "mcpConfigHash": "",
            }
        skills = SkillCatalog(PluginCatalog(self.store))
        mcp = McpManager(
            skills.plugins,
            extension_snapshot,
            self.store.workspace_for_run(run_id).path,
            sandbox=self.mcp_sandbox,
        )
        def build_registry(
            external_entries: tuple[object, ...]
        ) -> ToolRegistry:
            base = ToolRegistry.build(
                builtin_entries=(
                    *tools.registry.entries,
                    *skills.tool_entries(extension_snapshot),
                ),
                external_entries=external_entries,  # type: ignore[arg-type]
            )
            deferred = tuple(
                entry for entry in base.entries if entry.spec.visibility == "deferred"
            )
            return ToolRegistry((
                *base.entries,
                tool_search_entry(deferred),
            ))

        mcp_entries = mcp.start()
        registry = build_registry(mcp_entries)
        dispatcher = ToolDispatcher(registry)
        mentioned_plugins = {
            match.group(1)
            for match in re.finditer(
                r"@([a-z][a-z0-9_-]{0,63})(?::[A-Za-z0-9_-]{1,64})?",
                str(run.get("userInput") or ""),
            )
        }
        mentioned_tools = tuple(
            entry.spec.name for entry in registry.entries
            if entry.spec.visibility == "deferred"
            and entry.provenance.plugin_id in mentioned_plugins
        )
        if mentioned_tools:
            self.store.activate_tools(run_id, mentioned_tools)
        try:
            skill_context = skills.context(
                extension_snapshot, str(run.get("userInput") or "")
            )
        except SkillReadError:
            skill_context = ()

        try:
            while True:
                self._check_cancel(run_id, cancel)
                self._pause_effective_time(run_id)
                current = self.store.read_run(run_id)
                refreshed_mcp = mcp.refresh_if_changed()
                if refreshed_mcp is not None:
                    registry = build_registry(refreshed_mcp)
                    dispatcher = ToolDispatcher(registry)
                try:
                    step_snapshot = dispatcher.snapshot(
                        self.store.activated_tools(run_id)
                    )
                    step_index = self.store.increment_model_step(
                        run_id, tool_snapshot=step_snapshot.as_dict()
                    )
                except SegmentLimitReached as error:
                    reason = (
                        "segment_time_limit" if "time" in str(error)
                        else "segment_step_limit"
                    )
                    paused = self.store.pause_run(run_id, reason)
                    self.state_machine.transition(RuntimeState.WAITING_USER_INPUT, reason)
                    self._notification(
                        "run/updated", {"sessionId": paused["sessionId"], "run": paused}
                    )
                    return
                except RunLimitReached as error:
                    self._finalize(
                        run_id, context, cancel,
                        "max_effective_runtime" if "time" in str(error)
                        else "max_total_steps",
                    )
                    return
                self.active_started = self.monotonic()
                assistant_item: dict[str, object] | None = None
                assistant_bytes = 0
                delta_sequence = 0
                pending_deltas: list[str] = []
                pending_delta_bytes = 0
                last_persisted_at = time.monotonic()

                def flush_deltas() -> None:
                    nonlocal pending_delta_bytes, last_persisted_at
                    if assistant_item is None or not pending_deltas:
                        return
                    self.store.append_item_content(
                        assistant_item["id"], "".join(pending_deltas)
                    )
                    pending_deltas.clear()
                    pending_delta_bytes = 0
                    last_persisted_at = time.monotonic()

                def on_text_delta(delta: str) -> None:
                    nonlocal assistant_item, assistant_bytes, delta_sequence
                    nonlocal pending_delta_bytes
                    self._check_cancel(run_id, cancel)
                    if not isinstance(delta, str) or not delta:
                        return
                    assistant_bytes += len(delta.encode("utf-8"))
                    if assistant_bytes > MAX_ASSISTANT_BYTES:
                        raise ValueError("assistant output is too large")
                    if assistant_item is None:
                        assistant_item = self.store.create_assistant_item(
                            run_id, step_index
                        )
                        self._notification(
                            "item/started",
                            {
                                "sessionId": current["sessionId"],
                                "runId": run_id,
                                "item": assistant_item,
                            },
                        )
                    pending_deltas.append(delta)
                    pending_delta_bytes += len(delta.encode("utf-8"))
                    if (
                        pending_delta_bytes >= 4 * 1024
                        or time.monotonic() - last_persisted_at >= 0.1
                    ):
                        flush_deltas()
                    delta_sequence += 1
                    self._notification(
                        "item/delta",
                        {
                            "sessionId": current["sessionId"],
                            "runId": run_id,
                            "itemId": assistant_item["id"],
                            "sequence": delta_sequence,
                            "delta": delta,
                        },
                    )

                try:
                    result = ModelRunner(self.model, self.sensitive).run(
                        (*context, *skill_context),
                        cancel,
                        on_text_delta,
                        tool_definitions=tuple(dispatcher.model_definitions(
                            step_snapshot.activated_names
                        )),
                    )
                    response = ModelResponse(result.text, result.tool_calls)
                except SensitiveScanError:
                    self.store.complete_current_step(
                        run_id, "failed", reason="sensitive_scan_failed"
                    )
                    paused = self.store.pause_run(run_id, "sensitive_scan_failed")
                    self._notification(
                        "run/updated", {"sessionId": paused["sessionId"], "run": paused}
                    )
                    return
                except RunCancelled:
                    raise
                except ModelStreamInterrupted as interrupted:
                    if interrupted.text:
                        on_text_delta(interrupted.text)
                    flush_deltas()
                    self._check_cancel(run_id, cancel)
                    if assistant_item is not None:
                        incomplete = self.store.mark_assistant_incomplete(
                            str(assistant_item["id"])
                        )
                        self._completed_item(incomplete)
                        self.store.complete_current_step(
                            run_id, "failed", reason="model_stream_interrupted"
                        )
                        paused = self.store.pause_run(
                            run_id, "model_stream_interrupted"
                        )
                        self._notification(
                            "run/updated",
                            {"sessionId": paused["sessionId"], "run": paused},
                        )
                    else:
                        self._fail(run_id, "MODEL_REQUEST_FAILED")
                    return

                self._check_cancel(run_id, cancel)
                flush_deltas()
                if not isinstance(response, ModelResponse):
                    response = ModelResponse()
                if response.text and assistant_item is None:
                    on_text_delta(response.text)

                validation = dispatcher.validate(
                    response, step_snapshot.available_names
                )
                validation_error = validation.error_code
                if validation_error is not None:
                    if assistant_item is not None:
                        completed = self.store.complete_assistant_item(
                            assistant_item["id"]
                        )
                        self._completed_item(completed)
                    errors = self.store.record_protocol_error(run_id)
                    self.store.complete_current_step(
                        run_id, "failed", reason=validation_error
                    )
                    if errors >= 2:
                        self.state_machine.transition(RuntimeState.FAILED, "model_protocol_error")
                        self._fail(run_id, "MODEL_PROTOCOL_ERROR")
                        return
                    context = (*context, {"type": "protocol_error", "code": validation_error})
                    continue

                response = ModelResponse(response.text, validation.tool_calls)

                self.store.clear_protocol_errors(run_id)
                if not response.tool_calls:
                    if assistant_item is None:
                        errors = self.store.record_protocol_error(run_id)
                        self.store.complete_current_step(
                            run_id, "failed", reason="empty_response"
                        )
                        if errors >= 2:
                            self._fail(run_id, "MODEL_PROTOCOL_ERROR")
                            return
                        context = (
                            *context,
                            {"type": "protocol_error", "code": "empty_response"},
                        )
                        continue
                    self.store.complete_current_step(run_id, "completed")
                    completed_item, completed_run = self.store.complete_assistant_and_run(
                        assistant_item["id"], run_id
                    )
                    self._completed_item(completed_item)
                    self._completed_run(completed_run)
                    self.state_machine.transition(RuntimeState.COMPLETED, "run_succeeded")
                    return

                if assistant_item is not None:
                    completed = self.store.complete_assistant_item(assistant_item["id"])
                    self._completed_item(completed)

                sensitive_tool_failed = False
                self.state_machine.transition(RuntimeState.TOOL_EXECUTING, "model_tool_calls")
                for batch_order, tool_call in enumerate(response.tool_calls):
                    self._check_cancel(run_id, cancel)
                    try:
                        scanned_arguments = self.sensitive.scan_json(tool_call.arguments)
                        if scanned_arguments != tool_call.arguments:
                            raise SensitiveScanError("sensitive tool arguments")
                    except SensitiveScanError:
                        failures = self.store.record_sensitive_tool_input(run_id)
                        self.store.complete_current_step(
                            run_id, "failed", reason="sensitive_tool_input"
                        )
                        if failures >= 2:
                            paused = self.store.pause_run(
                                run_id, "repeated_sensitive_tool_input"
                            )
                            self._notification(
                                "run/updated",
                                {"sessionId": paused["sessionId"], "run": paused},
                            )
                            self.state_machine.transition(
                                RuntimeState.WAITING_USER_INPUT,
                                "repeated_sensitive_tool_input",
                            )
                            return
                        context = (*context, {
                            "type": "tool_error",
                            "code": "sensitive_tool_input_rejected",
                        })
                        sensitive_tool_failed = True
                        break
                    assert isinstance(scanned_arguments, dict)
                    self.store.clear_sensitive_tool_inputs(run_id)
                    item = self.store.create_tool_item(
                        run_id,
                        step_index,
                        batch_order,
                        tool_call.provider_call_id,
                        tool_call.name,
                        json.dumps(
                            scanned_arguments,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                        provenance=dispatcher.provenance(tool_call.name),
                        tool_set_hash=step_snapshot.tool_set_hash,
                    )
                    self._notification(
                        "item/started",
                        {
                            "sessionId": item["sessionId"],
                            "runId": run_id,
                            "item": item,
                        },
                    )
                    plan = dispatcher.plan(tool_call)
                    if plan.is_external:
                        if self.store.side_effects_blocked(run_id):
                            result, item_status = (
                                _tool_error(
                                    tool_call.name,
                                    "reconciliation_required",
                                    "External outcome must be reconciled",
                                ),
                                "failed",
                            )
                        else:
                            result, item_status = self._execute_external(
                                run_id, item, tool_call, dispatcher, cancel
                            )
                        completed = self.store.complete_tool_item(
                            item["id"],
                            json.dumps(result, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                            item_status=item_status,
                            tool_status="completed" if item_status == "completed" else "failed",
                        )
                        self._completed_item(completed)
                        self._check_cancel(run_id, cancel)
                        if result.get("reconciliationRequired") is True:
                            self.store.complete_current_step(
                                run_id, "failed", reason=str(result.get("code"))
                            )
                            paused = self.store.pause_run(
                                run_id, "external_tool_reconciliation_required"
                            )
                            self.state_machine.transition(
                                RuntimeState.WAITING_USER_INPUT,
                                "external_tool_reconciliation_required",
                            )
                            self._notification(
                                "run/updated",
                                {"sessionId": paused["sessionId"], "run": paused},
                            )
                            return
                    elif plan.requires_approval and not plan.is_shell:
                        if self.store.side_effects_blocked(run_id):
                            result, item_status = (
                                _tool_error(
                                    tool_call.name,
                                    "reconciliation_required",
                                    "A successful read-only observation is required",
                                ),
                                "failed",
                            )
                        else:
                            result, item_status = self._execute_file_change(
                                run_id,
                                item,
                                tool_call.name,
                                scanned_arguments,
                                dispatcher,
                                cancel,
                            )
                        completed = self.store.complete_tool_item(
                            item["id"],
                            json.dumps(
                                result,
                                ensure_ascii=False,
                                separators=(",", ":"),
                                sort_keys=True,
                            ),
                            item_status=item_status,
                            tool_status=(
                                "failed" if item_status == "failed" else "completed"
                            ),
                        )
                        self._completed_item(completed)
                        self._check_cancel(run_id, cancel)
                    elif plan.is_shell:
                        if self.store.side_effects_blocked(run_id):
                            result, item_status = (
                                _tool_error(
                                    "run_shell",
                                    "reconciliation_required",
                                    "A successful read-only observation is required",
                                ),
                                "failed",
                            )
                        else:
                            result, item_status = self._execute_shell(
                                run_id,
                                item,
                                scanned_arguments,
                                dispatcher,
                                cancel,
                            )
                        completed = self.store.complete_tool_item(
                            item["id"],
                            json.dumps(result, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                            item_status=item_status,
                            tool_status="completed" if item_status == "completed" else "failed",
                        )
                        self._completed_item(completed)
                        self._check_cancel(run_id, cancel)
                    else:
                        result = _bounded_tool_result(
                            tool_call.name,
                            dispatcher.execute_read_only(tool_call, cancel),
                        )
                        result = self._safe_tool_result(tool_call.name, result)
                        item_status = "completed"
                        self._check_cancel(run_id, cancel)
                        completed = self.store.complete_tool_item(
                            item["id"],
                            json.dumps(
                                result,
                                ensure_ascii=False,
                                separators=(",", ":"),
                                sort_keys=True,
                            ),
                            item_status=item_status,
                        )
                        self._completed_item(completed)
                        activations = dispatcher.consume_activations(tool_call.name)
                        if activations:
                            self.store.activate_tools(run_id, activations)

                if sensitive_tool_failed:
                    self.state_machine.transition(RuntimeState.THINKING, "safe_tool_feedback")
                    continue
                self.store.complete_current_step(run_id, "completed")
                self.state_machine.transition(RuntimeState.THINKING, "tool_batch_completed")
                self._pause_effective_time(run_id)
                updated = self.store.read_run(run_id)
                self._notification(
                    "run/updated",
                    {"sessionId": updated["sessionId"], "run": updated},
                )
                if updated["status"] == "waiting_user_input":
                    return
                try:
                    context = self.store.model_context(run["sessionId"])
                except ContextLimitExceeded:
                    self._fail(run_id, "CONTEXT_INPUT_TOO_LARGE")
                    return
        except (RunCancelled, InvalidRunStateError):
            if self.state_machine.state not in {
                RuntimeState.COMPLETED, RuntimeState.FAILED, RuntimeState.CANCELED,
            }:
                self.state_machine.transition(RuntimeState.CANCELED, "run_canceled")
            self.store.complete_current_step(run_id, "canceled", reason="canceled")
            completed = self.store.read_run(run_id)
            if completed["status"] in {"running", "waiting_approval"}:
                completed = self.store.cancel_run(run_id)
            self._completed_canceled_items(run_id)
            self._completed_run(completed)
        finally:
            self._pause_effective_time(run_id)
            mcp.close()
            tools.close()

    def _execute_external(
        self,
        run_id: str,
        item: dict[str, object],
        tool_call: object,
        tools: ToolDispatcher,
        cancel: threading.Event,
    ) -> tuple[dict[str, object], str]:
        from eidos_runtime.model.client import ModelToolCall

        assert isinstance(tool_call, ModelToolCall)
        pending_item = self.store.begin_approval(item["id"], "", None)
        approval_run = self.store.read_run(run_id)
        self._notification(
            "run/updated", {"sessionId": approval_run["sessionId"], "run": approval_run}
        )
        self.state_machine.transition(RuntimeState.WAITING_APPROVAL, "external_approval")
        self._pause_effective_time(run_id)
        details = tools.external_approval_details(tool_call.name)
        approval = self.approval.request(ApprovalRequest({
            "sessionId": pending_item["sessionId"],
            "runId": pending_item["runId"],
            "itemId": pending_item["id"],
            "toolCallId": pending_item["toolCall"]["id"],
            "kind": "external_tool",
            "summary": "Call an external MCP tool",
            "toolName": tool_call.name,
            "arguments": tool_call.arguments,
            **details,
        }), cancel)
        self.active_started = self.monotonic()
        self._check_cancel(run_id, cancel)
        self.store.resolve_approval(
            item["id"], approval.decision, approval.feedback,
            requeue=self.wait_for_execution_slot is not None,
        )
        self._resume_after_approval(run_id, cancel)
        approval_run = self.store.read_run(run_id)
        self.state_machine.transition(
            RuntimeState.WAITING_USER_INPUT
            if approval_run["status"] == "waiting_user_input"
            else RuntimeState.TOOL_EXECUTING,
            "external_approval_resolved",
        )
        if approval.decision != "approve":
            return {
                "schemaVersion": 1,
                "toolContractVersion": 1,
                "toolName": tool_call.name,
                "outcome": "declined",
                "code": "user_rejected",
                "summary": "User rejected the external tool",
                "data": {},
                "sideEffectsMayExist": False,
                "reconciliationRequired": False,
            }, "declined"
        self.store.begin_durable_intent(
            item["id"],
            preconditions={
                "toolName": tool_call.name,
                "provenance": details.get("provenance"),
                "permissionProfile": details.get("permissionProfile"),
                "timeoutSeconds": details.get("timeoutSeconds"),
            },
        )
        result = self._safe_tool_result(
            tool_call.name,
            _bounded_tool_result(
                tool_call.name, tools.execute_external(tool_call, cancel)
            ),
        )
        if result["outcome"] == "success":
            self.store.clear_rejects(run_id)
        return result, "completed" if result["outcome"] == "success" else "failed"

    def _execute_file_change(
        self,
        run_id: str,
        item: dict[str, object],
        tool_name: str,
        arguments: dict[str, object],
        tools: ToolDispatcher,
        cancel: threading.Event,
    ) -> tuple[dict[str, object], str]:
        prepared = tools.prepare_file_change(tool_name, arguments, cancel)
        if isinstance(prepared, dict):
            return _bounded_tool_result(tool_name, prepared), "failed"
        if prepared.base_sha256 is not None and not self.store.has_read_evidence(
            run_id, prepared.path, prepared.base_sha256
        ):
            return _tool_error(
                tool_name,
                "read_evidence_required",
                "Read the current file before proposing a change",
            ), "failed"
        if prepared.base_sha256 is not None and not prepared.diff:
            return {
                "schemaVersion": 1,
                "toolName": tool_name,
                "outcome": "success",
                "code": "no_changes",
                "summary": "File already matches the requested content",
                "data": {"path": prepared.path, "baseSha256": prepared.base_sha256},
                "sideEffectsMayExist": False,
            }, "completed"
        pending_item = self.store.begin_approval(
            item["id"], prepared.diff, prepared.base_sha256
        )
        approval_run = self.store.read_run(run_id)
        self._notification(
            "run/updated", {"sessionId": approval_run["sessionId"], "run": approval_run}
        )
        self.state_machine.transition(RuntimeState.WAITING_APPROVAL, "file_approval")
        self._pause_effective_time(run_id)
        tool_call = pending_item["toolCall"]
        approval = self.approval.request(ApprovalRequest(
                {
                    "sessionId": pending_item["sessionId"],
                    "runId": pending_item["runId"],
                    "itemId": pending_item["id"],
                    "toolCallId": tool_call["id"],
                    "kind": "file_change",
                    "summary": f"Modify {prepared.path}",
                    "diff": prepared.diff,
                }
            ), cancel)
        decision = ApprovalDecision(approval.decision, approval.feedback)
        self.active_started = self.monotonic()
        self._check_cancel(run_id, cancel)
        if (
            decision.decision not in {"approve", "reject"}
            or decision.feedback is not None
            and len(decision.feedback.encode("utf-8")) > 2_000
        ):
            decision = ApprovalDecision("reject")
        self.store.resolve_approval(
            item["id"], decision.decision, decision.feedback,
            requeue=self.wait_for_execution_slot is not None,
        )
        self._resume_after_approval(run_id, cancel)
        approval_run = self.store.read_run(run_id)
        self.state_machine.transition(
            RuntimeState.WAITING_USER_INPUT
            if approval_run["status"] == "waiting_user_input"
            else RuntimeState.TOOL_EXECUTING,
            "file_approval_resolved",
        )
        if decision.decision == "reject":
            return {
                "schemaVersion": 1,
                "toolName": tool_name,
                "outcome": "declined",
                "code": "user_rejected",
                "summary": "User rejected the file change",
                "data": {"path": prepared.path},
                "sideEffectsMayExist": False,
            }, "declined"
        self.store.begin_durable_intent(
            item["id"],
            preconditions={
                "path": prepared.path,
                "baseSha256": prepared.base_sha256,
            },
        )
        result = self._safe_tool_result(tool_name, _bounded_tool_result(
            tool_name, tools.commit_file_change(tool_name, prepared, cancel)
        ))
        if result["outcome"] == "success" and result.get("code") != "no_changes":
            self.store.clear_rejects(run_id)
        return result, "completed" if result["outcome"] == "success" else "failed"

    def _execute_shell(
        self,
        run_id: str,
        item: dict[str, object],
        arguments: dict[str, object],
        tools: ToolDispatcher,
        cancel: threading.Event,
    ) -> tuple[dict[str, object], str]:
        if not self.shell_available:
            return _tool_error("run_shell", "sandbox_unavailable", "Shell sandbox is unavailable"), "failed"
        command = arguments["command"]
        cwd_value = arguments.get("cwd", ".")
        timeout = arguments.get("timeoutSeconds", 120)
        assert isinstance(command, str) and isinstance(cwd_value, str) and isinstance(timeout, int)
        try:
            cwd = tools.prepare_shell("run_shell", cwd_value, cancel)
        except ToolCancelled:
            raise RunCancelled from None
        except WorkspacePathError as error:
            return _tool_error("run_shell", error.code, "Shell workspace is unsafe"), "failed"
        pending_item = self.store.begin_approval(item["id"], "", None)
        approval_run = self.store.read_run(run_id)
        self._notification(
            "run/updated", {"sessionId": approval_run["sessionId"], "run": approval_run}
        )
        self.state_machine.transition(RuntimeState.WAITING_APPROVAL, "shell_approval")
        self._pause_effective_time(run_id)
        approval = self.approval.request(ApprovalRequest({
                "sessionId": pending_item["sessionId"],
                "runId": pending_item["runId"],
                "itemId": pending_item["id"],
                "toolCallId": pending_item["toolCall"]["id"],
                "kind": "command_execution",
                "summary": "Run shell command",
                "command": command,
                "cwd": cwd_value,
                "networkEnabled": False,
                "timeoutSeconds": timeout,
            }
        ), cancel)
        decision = ApprovalDecision(approval.decision, approval.feedback)
        self.active_started = self.monotonic()
        self._check_cancel(run_id, cancel)
        self.store.resolve_approval(
            item["id"], decision.decision, decision.feedback,
            requeue=self.wait_for_execution_slot is not None,
        )
        self._resume_after_approval(run_id, cancel)
        approval_run = self.store.read_run(run_id)
        self.state_machine.transition(
            RuntimeState.WAITING_USER_INPUT
            if approval_run["status"] == "waiting_user_input"
            else RuntimeState.TOOL_EXECUTING,
            "shell_approval_resolved",
        )
        if decision.decision != "approve":
            return {
                "schemaVersion": 1,
                "toolName": "run_shell",
                "outcome": "declined",
                "code": "user_rejected",
                "summary": "User rejected the command",
                "data": {},
                "sideEffectsMayExist": False,
            }, "declined"
        self.store.begin_durable_intent(
            item["id"],
            preconditions={"cwd": cwd_value, "timeoutSeconds": timeout},
        )
        try:
            approved_cwd = tools.prepare_shell("run_shell", cwd_value, cancel)
            if approved_cwd != cwd:
                raise WorkspacePathError("workspace_identity_changed")
        except ToolCancelled:
            raise RunCancelled from None
        except WorkspacePathError as error:
            return _tool_error("run_shell", error.code, "Shell workspace changed after approval"), "failed"
        sequence = 0
        output_stream = StreamingSensitiveScanner(self.sensitive)

        def stream(delta: str) -> None:
            output_stream.feed(delta)

        result = _bounded_tool_result(
            "run_shell",
            run_shell(tools.workspace, command, approved_cwd, timeout, cancel, stream),
        )
        try:
            safe_output = output_stream.finish().text
            result = self._safe_tool_result("run_shell", result)
        except SensitiveScanError:
            result = _tool_error(
                "run_shell", "sensitive_content_rejected", "Shell output was withheld"
            )
            safe_output = ""
        if safe_output:
            sequence += 1
            self._notification(
                "item/delta",
                {
                    "sessionId": item["sessionId"], "runId": item["runId"],
                    "itemId": item["id"], "sequence": sequence, "delta": safe_output,
                },
            )
        if result["outcome"] == "success":
            self.store.clear_rejects(run_id)
        return result, "completed" if result["outcome"] == "success" else "failed"

    def _check_cancel(self, run_id: str, cancel: threading.Event) -> None:
        if cancel.is_set() or self.store.read_run(run_id)["status"] in {
            "canceled",
            "interrupted",
        }:
            raise RunCancelled

    def _resume_after_approval(
        self, run_id: str, cancel: threading.Event
    ) -> None:
        current = self.store.read_run(run_id)
        if current["status"] != "queued":
            return
        if self.wait_for_execution_slot is not None:
            if not self.wait_for_execution_slot(run_id, cancel):
                raise RunCancelled
            return
        claimed = self.store.claim_next_run()
        if claimed is None or claimed["id"] != run_id:
            raise InvalidRunStateError("run could not reacquire execution slot")

    def _safe_tool_result(
        self, tool_name: str, result: dict[str, object]
    ) -> dict[str, object]:
        return safe_tool_result(self.sensitive, tool_name, result)

    def _fail(self, run_id: str, error_code: str) -> None:
        if self.state_machine.state != RuntimeState.FAILED:
            self.state_machine.transition(RuntimeState.FAILED, error_code)
        self._pause_effective_time(run_id)
        self.store.complete_current_step(run_id, "failed", reason=error_code)
        failed = self.store.fail_run(run_id, error_code)
        self._completed_canceled_items(run_id)
        self._completed_run(failed)

    def _pause_effective_time(self, run_id: str) -> None:
        if self.active_started is None:
            return
        elapsed_ms = max(0, int((self.monotonic() - self.active_started) * 1000 + 0.999))
        self.active_started = None
        self.store.add_effective_time(run_id, elapsed_ms)

    def _finalize(
        self,
        run_id: str,
        context: tuple[dict[str, object], ...],
        cancel: threading.Event,
        stop_reason: str,
    ) -> None:
        self.state_machine.transition(RuntimeState.FINALIZING, stop_reason)
        self.store.begin_finalization(run_id)
        finalization_cancel = threading.Event()
        timer = threading.Timer(FINALIZATION_SECONDS, finalization_cancel.set)
        timer.start()
        item: dict[str, object] | None = None
        total_bytes = 0
        final_stream = StreamingSensitiveScanner(self.sensitive)

        def on_delta(delta: str) -> None:
            nonlocal item, total_bytes
            if cancel.is_set() or finalization_cancel.is_set() or not delta:
                return
            total_bytes += len(delta.encode("utf-8"))
            if total_bytes > MAX_ASSISTANT_BYTES:
                finalization_cancel.set()
                return
            final_stream.feed(delta)

        try:
            self.model.complete(
                (*context, {"type": "finalization", "toolsAllowed": False}),
                finalization_cancel,
                on_delta,
                allow_tools=False,
            )
            safe_text = final_stream.finish().text
            if safe_text:
                item = self.store.create_assistant_item(run_id, 80)
                self.store.append_item_content(str(item["id"]), safe_text)
        except Exception:
            pass
        finally:
            timer.cancel()
        if item is not None:
            self._completed_item(self.store.complete_assistant_item(str(item["id"])))
        stopped = self.store.stop_run(run_id, stop_reason)
        self._completed_run(stopped)
        self.state_machine.transition(RuntimeState.COMPLETED, "finalization_stopped")

    def _completed_canceled_items(self, run_id: str) -> None:
        for item in self.store.canceled_items_for_run(run_id):
            self._completed_item(item)

    def _completed_item(self, item: dict[str, object]) -> None:
        notification_item = item
        if item["kind"] == "assistant_message" and "content" in item:
            notification_item = {key: value for key, value in item.items() if key != "content"}
        elif item["kind"] == "file_change" and isinstance(item.get("toolCall"), dict):
            tool_call = {
                key: value
                for key, value in item["toolCall"].items()
                if key not in {"argumentsJson", "approvalDiff"}
            }
            notification_item = {**item, "toolCall": tool_call}
        self._notification(
            "item/completed",
            {
                "sessionId": item["sessionId"],
                "runId": item["runId"],
                "item": notification_item,
            },
        )

    def _completed_run(self, run: dict[str, object]) -> None:
        self._notification(
            "run/completed",
            {"sessionId": run["sessionId"], "run": run},
        )

    def _notification(self, method: str, params: dict[str, object]) -> None:
        self.events.emit(method, params)


# Compatibility for first-phase imports while callers migrate to RuntimeEngine.
RuntimeLoop = RuntimeEngine

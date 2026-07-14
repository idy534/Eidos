from __future__ import annotations

import json
from dataclasses import dataclass
import threading
import time
from typing import Callable

from eidos_runtime.model import ModelClient, ModelResponse, ModelToolCall
from eidos_runtime.shell import run_shell
from eidos_runtime.storage import InvalidRunStateError, SessionStore
from eidos_runtime.tools import ToolCancelled, ToolExecutor, WorkspacePathError


MAX_MODEL_STEPS = 20
MAX_ASSISTANT_BYTES = 512 * 1024


class RunCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class ApprovalDecision:
    decision: str
    feedback: str | None = None


class RuntimeLoop:
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
    ) -> None:
        self.store = store
        self.model = model
        self.notify = notify
        self.request_approval = request_approval
        self.shell_available = shell_available

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

        context = self.store.model_context(run["sessionId"])
        tools = ToolExecutor(self.store.workspace_for_run(run_id))

        try:
            while True:
                self._check_cancel(run_id, cancel)
                current = self.store.read_run(run_id)
                if current["modelStepCount"] >= MAX_MODEL_STEPS:
                    self._fail(run_id, "MAX_STEPS_EXCEEDED")
                    return
                step_index = self.store.increment_model_step(run_id)
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
                    response = self.model.complete(context, cancel, on_text_delta)
                except RunCancelled:
                    raise
                except Exception:
                    flush_deltas()
                    self._check_cancel(run_id, cancel)
                    self._fail(run_id, "MODEL_REQUEST_FAILED")
                    return

                self._check_cancel(run_id, cancel)
                flush_deltas()
                if not isinstance(response, ModelResponse):
                    response = ModelResponse()
                if response.text and assistant_item is None:
                    on_text_delta(response.text)

                validation_error = self._validate_response(response, tools)
                if validation_error is not None:
                    if assistant_item is not None:
                        completed = self.store.complete_assistant_item(
                            assistant_item["id"]
                        )
                        self._completed_item(completed)
                    errors = self.store.record_protocol_error(run_id)
                    if errors >= 2:
                        self._fail(run_id, "MODEL_PROTOCOL_ERROR")
                        return
                    context = (*context, {"type": "protocol_error", "code": validation_error})
                    continue

                self.store.clear_protocol_errors(run_id)
                if not response.tool_calls:
                    if assistant_item is None:
                        errors = self.store.record_protocol_error(run_id)
                        if errors >= 2:
                            self._fail(run_id, "MODEL_PROTOCOL_ERROR")
                            return
                        context = (
                            *context,
                            {"type": "protocol_error", "code": "empty_response"},
                        )
                        continue
                    completed_item, completed_run = self.store.complete_assistant_and_run(
                        assistant_item["id"], run_id
                    )
                    self._completed_item(completed_item)
                    self._completed_run(completed_run)
                    return

                if assistant_item is not None:
                    completed = self.store.complete_assistant_item(assistant_item["id"])
                    self._completed_item(completed)

                for batch_order, tool_call in enumerate(response.tool_calls):
                    self._check_cancel(run_id, cancel)
                    item = self.store.create_tool_item(
                        run_id,
                        step_index,
                        batch_order,
                        tool_call.provider_call_id,
                        tool_call.name,
                        json.dumps(
                            tool_call.arguments,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                    )
                    self._notification(
                        "item/started",
                        {
                            "sessionId": item["sessionId"],
                            "runId": run_id,
                            "item": item,
                        },
                    )
                    if tools.is_side_effecting(tool_call.name):
                        result, item_status = self._execute_file_change(
                            run_id,
                            item,
                            tool_call.name,
                            tool_call.arguments,
                            tools,
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
                    elif tools.is_shell(tool_call.name):
                        result, item_status = self._execute_shell(
                            run_id,
                            item,
                            tool_call.arguments,
                            tools,
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
                            tools.execute(tool_call.name, tool_call.arguments, cancel),
                        )
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

                context = self.store.model_context(run["sessionId"])
                if step_index >= MAX_MODEL_STEPS:
                    self._fail(run_id, "MAX_STEPS_EXCEEDED")
                    return
        except (RunCancelled, InvalidRunStateError):
            completed = self.store.read_run(run_id)
            if completed["status"] in {"running", "waiting_approval"}:
                completed = self.store.cancel_run(run_id)
            self._completed_canceled_items(run_id)
            self._completed_run(completed)
        finally:
            tools.close()

    @staticmethod
    def _validate_response(
        response: ModelResponse, tools: ToolExecutor
    ) -> str | None:
        if not isinstance(response, ModelResponse):
            return "invalid_response"
        if not response.text and not response.tool_calls:
            return "empty_response"
        if len(response.tool_calls) > 16:
            return "too_many_tool_calls"
        provider_ids: set[str] = set()
        for tool_call in response.tool_calls:
            if (
                not isinstance(tool_call, ModelToolCall)
                or not isinstance(tool_call.provider_call_id, str)
                or not 1 <= len(tool_call.provider_call_id) <= 256
                or tool_call.provider_call_id in provider_ids
                or not isinstance(tool_call.name, str)
                or tool_call.name not in tools.tool_names
                or not tools.validate_arguments(tool_call.name, tool_call.arguments)
                or not _valid_tool_arguments(tool_call.arguments)
            ):
                return "invalid_tool_call"
            provider_ids.add(tool_call.provider_call_id)
        if any(
            tools.is_side_effecting(call.name) or tools.is_shell(call.name)
            for call in response.tool_calls
        ) and len(
            response.tool_calls
        ) != 1:
            return "invalid_tool_batch"
        return None

    def _execute_file_change(
        self,
        run_id: str,
        item: dict[str, object],
        tool_name: str,
        arguments: dict[str, object],
        tools: ToolExecutor,
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
        if self.request_approval is None:
            decision = ApprovalDecision("reject")
        else:
            tool_call = pending_item["toolCall"]
            decision = self.request_approval(
                {
                    "sessionId": pending_item["sessionId"],
                    "runId": pending_item["runId"],
                    "itemId": pending_item["id"],
                    "toolCallId": tool_call["id"],
                    "kind": "file_change",
                    "summary": f"Modify {prepared.path}",
                    "diff": prepared.diff,
                },
                cancel,
            )
        self._check_cancel(run_id, cancel)
        if (
            decision.decision not in {"approve", "reject"}
            or decision.feedback is not None
            and len(decision.feedback.encode("utf-8")) > 2_000
        ):
            decision = ApprovalDecision("reject")
        self.store.resolve_approval(
            item["id"], decision.decision, decision.feedback
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
        result = _bounded_tool_result(
            tool_name, tools.commit_file_change(tool_name, prepared, cancel)
        )
        return result, "completed" if result["outcome"] == "success" else "failed"

    def _execute_shell(
        self,
        run_id: str,
        item: dict[str, object],
        arguments: dict[str, object],
        tools: ToolExecutor,
        cancel: threading.Event,
    ) -> tuple[dict[str, object], str]:
        if not self.shell_available:
            return _tool_error("run_shell", "sandbox_unavailable", "Shell sandbox is unavailable"), "failed"
        command = arguments["command"]
        cwd_value = arguments.get("cwd", ".")
        timeout = arguments.get("timeoutSeconds", 120)
        assert isinstance(command, str) and isinstance(cwd_value, str) and isinstance(timeout, int)
        try:
            cwd = tools.prepare_shell(cwd_value, cancel)
        except ToolCancelled:
            raise RunCancelled from None
        except WorkspacePathError as error:
            return _tool_error("run_shell", error.code, "Shell workspace is unsafe"), "failed"
        pending_item = self.store.begin_approval(item["id"], "", None)
        decision = ApprovalDecision("reject") if self.request_approval is None else self.request_approval(
            {
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
            },
            cancel,
        )
        self._check_cancel(run_id, cancel)
        self.store.resolve_approval(item["id"], decision.decision, decision.feedback)
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
        try:
            approved_cwd = tools.prepare_shell(cwd_value, cancel)
            if approved_cwd != cwd:
                raise WorkspacePathError("workspace_identity_changed")
        except ToolCancelled:
            raise RunCancelled from None
        except WorkspacePathError as error:
            return _tool_error("run_shell", error.code, "Shell workspace changed after approval"), "failed"
        sequence = 0

        def stream(delta: str) -> None:
            nonlocal sequence
            sequence += 1
            self._notification(
                "item/delta",
                {
                    "sessionId": item["sessionId"],
                    "runId": item["runId"],
                    "itemId": item["id"],
                    "sequence": sequence,
                    "delta": delta,
                },
            )

        result = _bounded_tool_result(
            "run_shell",
            run_shell(tools.workspace, command, approved_cwd, timeout, cancel, stream),
        )
        return result, "completed" if result["outcome"] == "success" else "failed"

    def _check_cancel(self, run_id: str, cancel: threading.Event) -> None:
        if cancel.is_set() or self.store.read_run(run_id)["status"] in {
            "canceled",
            "interrupted",
        }:
            raise RunCancelled

    def _fail(self, run_id: str, error_code: str) -> None:
        failed = self.store.fail_run(run_id, error_code)
        self._completed_canceled_items(run_id)
        self._completed_run(failed)

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
        self.notify({"jsonrpc": "2.0", "method": method, "params": params})


def _valid_tool_arguments(arguments: object) -> bool:
    try:
        encoded = json.dumps(
            arguments,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError, RecursionError):
        return False
    return len(encoded) <= 64 * 1024


def _bounded_tool_result(
    tool_name: str, result: dict[str, object]
) -> dict[str, object]:
    encoded = json.dumps(
        result,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(encoded) <= 512 * 1024:
        return result
    return {
        "schemaVersion": 1,
        "toolName": tool_name,
        "outcome": "error",
        "code": "tool_result_too_large",
        "summary": "Tool result exceeded the safe size limit",
        "data": {},
        "sideEffectsMayExist": False,
    }


def _tool_error(tool_name: str, code: str, summary: str) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "toolName": tool_name,
        "outcome": "error",
        "code": code,
        "summary": summary,
        "data": {},
        "sideEffectsMayExist": False,
    }

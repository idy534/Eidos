from __future__ import annotations

import json
import threading
import time
from typing import Callable

from eidos_runtime.model import ModelClient, ModelResponse, ModelToolCall
from eidos_runtime.storage import InvalidRunStateError, SessionStore
from eidos_runtime.tools import ToolExecutor


MAX_MODEL_STEPS = 20
MAX_ASSISTANT_BYTES = 512 * 1024


class RunCancelled(RuntimeError):
    pass


class RuntimeLoop:
    def __init__(
        self,
        store: SessionStore,
        model: ModelClient,
        notify: Callable[[dict[str, object]], None],
    ) -> None:
        self.store = store
        self.model = model
        self.notify = notify

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
                    result = _bounded_tool_result(
                        tool_call.name,
                        tools.execute(tool_call.name, tool_call.arguments, cancel),
                    )
                    self._check_cancel(run_id, cancel)
                    completed = self.store.complete_tool_item(
                        item["id"],
                        json.dumps(
                            result,
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                    )
                    self._completed_item(completed)

                context = self.store.model_context(run["sessionId"])
                if step_index >= MAX_MODEL_STEPS:
                    self._fail(run_id, "MAX_STEPS_EXCEEDED")
                    return
        except (RunCancelled, InvalidRunStateError):
            completed = self.store.read_run(run_id)
            if completed["status"] == "running":
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
        return None

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

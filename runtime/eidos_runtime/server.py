from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
import re
import sys
import threading
import unicodedata
import uuid
from typing import Any, BinaryIO, TextIO

from pydantic import ValidationError

from eidos_runtime import __version__
from eidos_runtime.deepseek import DeepSeekChatModel
from eidos_runtime.model import ModelClient, ModelResponse, ModelToolCall, ScriptedModel
from eidos_runtime.model_config import ModelConfigError, ModelConfigStore
from eidos_runtime.runtime_loop import ApprovalDecision, RuntimeEngine
from eidos_runtime.schemas import ApprovalDecisionDto, JsonRpcRequestDto, JsonRpcResponse
from eidos_runtime.sensitive import (
    SensitiveContentDenied,
    SensitiveScanError,
    SensitiveScanner,
)
from eidos_runtime.seatbelt import run_seatbelt_self_test
from eidos_runtime.storage import (
    ActiveRunError,
    InvalidCursorError,
    InvalidRunStateError,
    OperationConflictError,
    OperationInProgressError,
    ResourceNotFoundError,
    SessionStore,
    StorageError,
    WorkspaceBoundaryError,
)


MAX_MESSAGE_BYTES = 1024 * 1024
MAX_REQUEST_ID_BYTES = 128
PROTOCOL_VERSION = 1
CLIENT_REQUEST_ID = re.compile(r"client-[A-Za-z0-9._-]+")
MAX_SESSION_TITLE_BYTES = 120

logger = logging.getLogger("eidos.runtime")


def clean_session_title(value: str) -> str:
    title = "".join(
        " " if unicodedata.category(character).startswith("C") else character
        for character in value
    )
    title = " ".join(title.split()).strip("\"'`“”‘’ ")[:60]
    return title.encode("utf-8")[:MAX_SESSION_TITLE_BYTES].decode(
        "utf-8", errors="ignore"
    ).strip()


@dataclass
class PendingApproval:
    event: threading.Event
    decision: ApprovalDecision | None = None


@dataclass
class WaitingWorker:
    thread: threading.Thread
    cancellation: threading.Event
    resume: threading.Event


def response(request_id: str, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def protocol_error(
    request_id: str | None, code: int, message: str
) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def business_error(request_id: str, code: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {
            "code": -32000,
            "message": "Request failed",
            "data": {"code": code, "retryable": False},
        },
    }


def write_message(output: TextIO, message: dict[str, Any]) -> None:
    if "id" in message and ("result" in message or "error" in message):
        message = JsonRpcResponse.model_validate(message).to_json_value()
    serialized = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
    if len(serialized.encode("utf-8")) > MAX_MESSAGE_BYTES:
        raise ValueError("outbound protocol message exceeds 1 MiB")
    output.write(serialized)
    output.write("\n")
    output.flush()


def read_bounded_line(input_stream: BinaryIO) -> tuple[bytes, bool]:
    line = input_stream.readline(MAX_MESSAGE_BYTES + 2)
    if not line:
        return b"", False
    if len(line) <= MAX_MESSAGE_BYTES + 1:
        return line, False

    while line and not line.endswith(b"\n"):
        line = input_stream.readline(MAX_MESSAGE_BYTES + 2)
    return b"", True


def valid_request_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and CLIENT_REQUEST_ID.fullmatch(value) is not None
        and len(value) <= MAX_REQUEST_ID_BYTES
    )


def valid_initialize_params(params: object) -> bool:
    if not isinstance(params, dict) or set(params) != {"client", "protocolVersion"}:
        return False
    client = params.get("client")
    return (
        isinstance(client, dict)
        and set(client) == {"name", "version"}
        and isinstance(client.get("name"), str)
        and bool(client["name"])
        and isinstance(client.get("version"), str)
        and bool(client["version"])
        and isinstance(params.get("protocolVersion"), int)
    )


class RuntimeServer:
    def __init__(
        self,
        output: TextIO,
        data_directory: Path | None = None,
        model: ModelClient | None = None,
    ) -> None:
        self.output = output
        self.initialized = False
        self.shutting_down = False
        self.store = SessionStore(data_directory)
        self.model_config = ModelConfigStore(data_directory)
        self.model = model
        self.output_lock = threading.RLock()
        self.worker_lock = threading.RLock()
        self.worker: threading.Thread | None = None
        self.active_run_id: str | None = None
        self.active_cancel: threading.Event | None = None
        self.waiting_workers: dict[str, WaitingWorker] = {}
        self.approval_lock = threading.RLock()
        self.pending_approvals: dict[str, PendingApproval] = {}
        self.shell_available = False
        self.sensitive: SensitiveScanner | None = None

    def handle(self, message: object) -> None:
        if not isinstance(message, dict):
            self.send(protocol_error(None, -32600, "Invalid Request"))
            return

        if self._is_server_response(message):
            self.handle_approval_response(message)
            return

        try:
            request = JsonRpcRequestDto.model_validate({
                **message, "params": message.get("params", {})
            })
        except ValidationError:
            self.send(protocol_error(None, -32600, "Invalid Request"))
            return
        request_id = request.id
        if not valid_request_id(request_id):
            self.send(protocol_error(None, -32600, "Invalid Request"))
            return

        method = request.method
        params = request.params

        if method == "initialize":
            self.initialize(request_id, params)
            return
        if method == "runtime/shutdown":
            self.shutdown(request_id, params)
            return
        if not self.initialized:
            self.send(business_error(request_id, "RUNTIME_NOT_INITIALIZED"))
            return
        if method == "runtime/health":
            if params != {}:
                self.send(protocol_error(request_id, -32602, "Invalid params"))
            else:
                self.send(response(request_id, self.store.health()))
            return
        if self.store.health_state != "ready":
            self.send(business_error(request_id, "STORAGE_HEALTH_ONLY"))
            return

        if method == "session/create":
            self.create_session(request_id, params)
            return
        if method == "session/list":
            self.list_sessions(request_id, params)
            return
        if method == "session/read":
            self.read_session(request_id, params)
            return
        if method == "event/list":
            self.list_events(request_id, params)
            return
        if method == "run/start":
            self.start_run(request_id, params)
            return
        if method == "run/cancel":
            self.cancel_run(request_id, params)
            return
        if method == "run/continue":
            self.continue_run(request_id, params)
            return
        if method == "model/status":
            self.model_status(request_id, params)
            return
        if method == "model/configure":
            self.configure_model(request_id, params)
            return

        self.send(protocol_error(request_id, -32601, "Method not found"))

    def initialize(self, request_id: str, params: object) -> None:
        if not valid_initialize_params(params):
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        if self.initialized:
            self.send(business_error(request_id, "INVALID_STATE"))
            return
        if params["protocolVersion"] != PROTOCOL_VERSION:
            self.send(business_error(request_id, "PROTOCOL_VERSION_UNSUPPORTED"))
            return

        try:
            self.store.initialize()
            if self.store.health_state == "ready":
                self.sensitive = SensitiveScanner()
                self.model_config.initialize()
                configured_key = self.model_config.api_key()
                if self.model is None and configured_key is not None:
                    self.model = DeepSeekChatModel(configured_key)
        except (StorageError, ModelConfigError, SensitiveScanError):
            logger.exception("Runtime storage initialization failed")
            self.send(business_error(request_id, "INTERNAL_ERROR"))
            return

        if self.store.health_state == "ready":
            seatbelt = run_seatbelt_self_test()
            if seatbelt.available:
                logger.info("Seatbelt self-test passed")
                self.shell_available = True
            else:
                logger.warning(
                    "Seatbelt self-test failed; Shell remains unavailable: %s",
                    ",".join(seatbelt.failures),
                )

        self.initialized = True
        self.send(
            response(
                request_id,
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "runtimeVersion": __version__,
                    "capabilities": {
                        "runShell": self.shell_available,
                        "modelConfigured": self.model is not None,
                    },
                },
            )
        )
        logger.info("Runtime initialized")
        self._schedule_next()

    def create_session(self, request_id: str, params: object) -> None:
        if (
            not isinstance(params, dict)
            or set(params) - {"workspaceRoot", "operationId"}
            or "workspaceRoot" not in params
            or not isinstance(params.get("workspaceRoot"), str)
            or ("operationId" in params and not _is_canonical_uuid(params["operationId"]))
        ):
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        try:
            session = self.store.create_session(
                params["workspaceRoot"], operation_id=params.get("operationId")
            )
        except WorkspaceBoundaryError:
            self.send(business_error(request_id, "WORKSPACE_BOUNDARY_VIOLATION"))
            return
        except OperationConflictError:
            self.send(business_error(request_id, "OPERATION_ID_REUSED"))
            return
        except OperationInProgressError:
            self.send(business_error(request_id, "OPERATION_IN_PROGRESS"))
            return
        self.send(response(request_id, session))

    def list_sessions(self, request_id: str, params: object) -> None:
        if not isinstance(params, dict) or set(params) - {"limit", "cursor"}:
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        limit = params.get("limit", 50)
        cursor = params.get("cursor")
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= 200
            or (cursor is not None and not isinstance(cursor, str))
        ):
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        try:
            result = self.store.list_sessions(limit=limit, cursor=cursor)
        except InvalidCursorError:
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        self.send(response(request_id, result))

    def read_session(self, request_id: str, params: object) -> None:
        if (
            not isinstance(params, dict)
            or set(params) - {"sessionId", "itemLimit", "beforeItemId"}
            or not _is_canonical_uuid(params.get("sessionId"))
        ):
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        item_limit = params.get("itemLimit", 200)
        before_item_id = params.get("beforeItemId")
        if (
            not isinstance(item_limit, int)
            or isinstance(item_limit, bool)
            or not 1 <= item_limit <= 500
            or (before_item_id is not None and not _is_canonical_uuid(before_item_id))
        ):
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        session = self.store.read_session(params["sessionId"])
        if session is None:
            self.send(business_error(request_id, "RESOURCE_NOT_FOUND"))
            return
        try:
            snapshot = self.store.read_session_snapshot(
                params["sessionId"],
                item_limit=item_limit,
                before_item_id=before_item_id,
            )
        except ResourceNotFoundError:
            self.send(business_error(request_id, "RESOURCE_NOT_FOUND"))
            return
        self.send(response(request_id, snapshot))

    def list_events(self, request_id: str, params: object) -> None:
        if (
            not isinstance(params, dict)
            or set(params) - {"sessionId", "afterEventId", "limit"}
            or not _is_canonical_uuid(params.get("sessionId"))
        ):
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        after_event_id = params.get("afterEventId", 0)
        limit = params.get("limit", 200)
        if (
            not isinstance(after_event_id, int) or isinstance(after_event_id, bool)
            or after_event_id < 0
            or not isinstance(limit, int) or isinstance(limit, bool)
            or not 1 <= limit <= 500
        ):
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        self.send(response(request_id, self.store.list_events(
            params["sessionId"], after_event_id=after_event_id, limit=limit
        )))

    def start_run(self, request_id: str, params: object) -> None:
        if (
            not isinstance(params, dict)
            or set(params) - {"sessionId", "userInput", "operationId"}
            or not {"sessionId", "userInput"} <= set(params)
            or not _is_canonical_uuid(params.get("sessionId"))
            or ("operationId" in params and not _is_canonical_uuid(params["operationId"]))
            or not isinstance(params.get("userInput"), str)
            or not params["userInput"].strip()
            or len(params["userInput"].encode("utf-8")) > 64 * 1024
        ):
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        if self.model is None:
            self.send(business_error(request_id, "INVALID_STATE"))
            return
        try:
            user_input = self._scan_text(params["userInput"])
        except SensitiveContentDenied:
            self.send(business_error(request_id, "SENSITIVE_CONTENT_REJECTED"))
            return
        except SensitiveScanError:
            self.send(business_error(request_id, "SENSITIVE_SCAN_FAILED"))
            return
        operation_id = params.get("operationId")
        if isinstance(operation_id, str):
            try:
                replay = self.store.operation_result(
                    operation_id,
                    "run/start",
                    {"sessionId": params["sessionId"], "userInput": user_input},
                )
            except OperationConflictError:
                self.send(business_error(request_id, "OPERATION_ID_REUSED"))
                return
            except OperationInProgressError:
                self.send(business_error(request_id, "OPERATION_IN_PROGRESS"))
                return
            if isinstance(replay, dict) and isinstance(replay.get("run"), dict):
                self.send(response(request_id, replay["run"]))
                return
        session = self.store.read_session(params["sessionId"])
        if session is None:
            self.send(business_error(request_id, "RESOURCE_NOT_FOUND"))
            return
        session_title: str | None = None
        if "title" not in session:
            try:
                session_title = clean_session_title(
                    self.model.generate_title(user_input, threading.Event())
                )
                if session_title:
                    session_title = clean_session_title(self._scan_text(session_title))
            except Exception:
                logger.warning("Session title generation failed; using query fallback")
                session_title = ""
            session_title = session_title or clean_session_title(user_input) or "新任务"
        with self.worker_lock:
            try:
                run, _user_item = self.store.enqueue_run(
                    params["sessionId"], user_input,
                    operation_id=params.get("operationId"),
                    session_title=session_title,
                )
            except ResourceNotFoundError:
                self.send(business_error(request_id, "RESOURCE_NOT_FOUND"))
                return
            except WorkspaceBoundaryError:
                self.send(business_error(request_id, "WORKSPACE_BOUNDARY_VIOLATION"))
                return
            except OperationConflictError:
                self.send(business_error(request_id, "OPERATION_ID_REUSED"))
                return
            except OperationInProgressError:
                self.send(business_error(request_id, "OPERATION_IN_PROGRESS"))
                return

            start_gate: threading.Event | None = None
            if self.worker is None or not self.worker.is_alive():
                claimed = self.store.claim_next_run()
                if claimed is not None:
                    start_gate = self._start_worker_locked(str(claimed["id"]))
                    run = self.store.read_run(str(run["id"]))
            try:
                self.send(response(request_id, run))
            except Exception:
                if self.active_cancel is not None:
                    self.active_cancel.set()
                try:
                    self.store.interrupt_run(run["id"])
                except (ResourceNotFoundError, InvalidRunStateError):
                    pass
                raise
            finally:
                if start_gate is not None:
                    start_gate.set()

    def cancel_run(self, request_id: str, params: object) -> None:
        if (
            not isinstance(params, dict)
            or set(params) - {"runId", "operationId"}
            or "runId" not in params
            or not _is_canonical_uuid(params.get("runId"))
            or ("operationId" in params and not _is_canonical_uuid(params["operationId"]))
        ):
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        try:
            current = self.store.read_run(params["runId"])
        except ResourceNotFoundError:
            self.send(business_error(request_id, "RESOURCE_NOT_FOUND"))
            return
        if current["status"] not in {
            "queued", "running", "waiting_approval", "waiting_user_input", "canceled"
        }:
            self.send(business_error(request_id, "INVALID_STATE"))
            return
        with self.worker_lock:
            if self.active_run_id == params["runId"] and self.active_cancel is not None:
                self.active_cancel.set()
                worker = self.worker
            elif params["runId"] in self.waiting_workers:
                waiting = self.waiting_workers[params["runId"]]
                waiting.cancellation.set()
                worker = waiting.thread
            else:
                worker = None
        try:
            if worker is None:
                current = self.store.cancel_run(
                    params["runId"], operation_id=params.get("operationId")
                )
            else:
                worker.join(timeout=6.0)
                current = self.store.cancel_run(
                    params["runId"], operation_id=params.get("operationId")
                )
        except InvalidRunStateError:
            current = self.store.read_run(params["runId"])
        except OperationConflictError:
            self.send(business_error(request_id, "OPERATION_ID_REUSED"))
            return
        except OperationInProgressError:
            self.send(business_error(request_id, "OPERATION_IN_PROGRESS"))
            return
        self.send(response(request_id, current))

    def continue_run(self, request_id: str, params: object) -> None:
        if (
            not isinstance(params, dict)
            or set(params) - {"runId", "userInput", "operationId"}
            or not {"runId", "userInput"} <= set(params)
            or not _is_canonical_uuid(params.get("runId"))
            or not isinstance(params.get("userInput"), str)
            or not params["userInput"].strip()
            or len(params["userInput"].encode("utf-8")) > 64 * 1024
            or ("operationId" in params and not _is_canonical_uuid(params["operationId"]))
        ):
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        try:
            user_input = self._scan_text(params["userInput"])
        except SensitiveContentDenied:
            self.send(business_error(request_id, "SENSITIVE_CONTENT_REJECTED"))
            return
        except SensitiveScanError:
            self.send(business_error(request_id, "SENSITIVE_SCAN_FAILED"))
            return
        try:
            run = self.store.continue_run(
                params["runId"], user_input,
                operation_id=params.get("operationId"),
            )
        except InvalidRunStateError:
            self.send(business_error(request_id, "INVALID_STATE"))
            return
        except OperationConflictError:
            self.send(business_error(request_id, "OPERATION_ID_REUSED"))
            return
        except OperationInProgressError:
            self.send(business_error(request_id, "OPERATION_IN_PROGRESS"))
            return
        self._schedule_next()
        run = self.store.read_run(params["runId"])
        self.send(response(request_id, run))

    def model_status(self, request_id: str, params: object) -> None:
        if not isinstance(params, dict) or params:
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        try:
            status = self.model_config.public_status()
        except ModelConfigError:
            self.send(business_error(request_id, "INTERNAL_ERROR"))
            return
        if self.model is not None:
            status["configured"] = True
        self.send(response(request_id, status))

    def configure_model(self, request_id: str, params: object) -> None:
        if (
            not isinstance(params, dict)
            or set(params) != {"apiKey"}
            or not isinstance(params.get("apiKey"), str)
        ):
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        with self.worker_lock:
            if self.worker is not None and self.worker.is_alive():
                self.send(business_error(request_id, "RUN_ALREADY_ACTIVE"))
                return
            try:
                self.model_config.save_api_key(params["apiKey"])
                key = self.model_config.api_key()
                if key is None:
                    raise ModelConfigError("model configuration was not saved")
                self.model = DeepSeekChatModel(key)
            except ValueError:
                self.send(protocol_error(request_id, -32602, "Invalid params"))
                return
            except (OSError, ModelConfigError):
                logger.exception("Model configuration failed")
                self.send(business_error(request_id, "INTERNAL_ERROR"))
                return
        self._schedule_next()
        self.send(response(request_id, self.model_config.public_status()))

    def shutdown(self, request_id: str, params: object) -> None:
        if not isinstance(params, dict) or params:
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        self.shutting_down = True
        with self.worker_lock:
            if self.active_cancel is not None:
                self.active_cancel.set()
            active_run_id = self.active_run_id
            worker = self.worker
            waiting = list(self.waiting_workers.values())
            for entry in waiting:
                entry.cancellation.set()
        if worker is not None:
            worker.join(timeout=6.0)
        for entry in waiting:
            entry.thread.join(timeout=6.0)
        if (worker is None or not worker.is_alive()) and all(
            not entry.thread.is_alive() for entry in waiting
        ):
            self.store.close()
        self.send(response(request_id, {}))
        logger.info("Runtime shutdown requested")

    def send(self, message: dict[str, object]) -> None:
        with self.output_lock:
            write_message(self.output, message)

    def request_approval(
        self, params: dict[str, object], cancel: threading.Event
    ) -> ApprovalDecision:
        request_id = f"server-approval-{uuid.uuid4()}"
        pending = PendingApproval(threading.Event())
        with self.approval_lock:
            self.pending_approvals[request_id] = pending
        try:
            self._park_active_worker(str(params["runId"]), cancel)
            self.send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "item/requestApproval",
                    "params": params,
                }
            )
            while not pending.event.wait(0.1):
                if cancel.is_set():
                    return ApprovalDecision("reject")
            return pending.decision or ApprovalDecision("reject")
        finally:
            with self.approval_lock:
                self.pending_approvals.pop(request_id, None)

    def handle_approval_response(self, message: dict[str, object]) -> None:
        request_id = message["id"]
        assert isinstance(request_id, str)
        with self.approval_lock:
            pending = self.pending_approvals.get(request_id)
            if pending is None:
                return
            result = message.get("result")
            try:
                parsed = ApprovalDecisionDto.model_validate(result)
            except ValidationError:
                pending.decision = ApprovalDecision("reject")
            else:
                try:
                    feedback = (
                        self._scan_text(parsed.feedback)
                        if parsed.feedback is not None else None
                    )
                except SensitiveScanError:
                    pending.decision = ApprovalDecision("reject")
                else:
                    pending.decision = ApprovalDecision(parsed.decision, feedback)
            pending.event.set()

    def _scan_text(self, value: str) -> str:
        if self.sensitive is None:
            raise SensitiveScanError("sensitive scanner unavailable")
        return self.sensitive.scan_text(value).text

    @staticmethod
    def _is_server_response(message: dict[str, object]) -> bool:
        return (
            message.get("jsonrpc") == "2.0"
            and isinstance(message.get("id"), str)
            and str(message["id"]).startswith("server-")
            and "method" not in message
            and set(message) <= {"jsonrpc", "id", "result", "error"}
            and (("result" in message) != ("error" in message))
        )

    def wait_for_worker(self, timeout: float = 5.0) -> None:
        with self.worker_lock:
            workers = [self.worker] + [entry.thread for entry in self.waiting_workers.values()]
        for worker in {worker for worker in workers if worker is not None}:
            worker.join(timeout=timeout)

    def close(self) -> None:
        with self.worker_lock:
            cancellation = self.active_cancel
            worker = self.worker
            active_run_id = self.active_run_id
            waiting = list(self.waiting_workers.items())
        if cancellation is not None:
            cancellation.set()
        for _run_id, entry in waiting:
            entry.cancellation.set()
        if active_run_id is not None and not self.shutting_down:
            try:
                self.store.interrupt_run(active_run_id)
            except (ResourceNotFoundError, InvalidRunStateError):
                pass
        if worker is not None:
            worker.join(timeout=6.0)
        for _run_id, entry in waiting:
            entry.thread.join(timeout=6.0)
        if (worker is None or not worker.is_alive()) and all(
            not entry.thread.is_alive() for _run_id, entry in waiting
        ):
            self.store.close()

    def _run_worker(
        self,
        run_id: str,
        cancellation: threading.Event,
        start_gate: threading.Event,
    ) -> None:
        start_gate.wait()
        try:
            RuntimeEngine(
                self.store,
                self.model,
                self.send,
                self.request_approval,
                self.shell_available,
                sensitive=self.sensitive,
                wait_for_execution_slot=self._wait_for_execution_slot,
            ).run(run_id, cancellation)
        except Exception:
            logger.exception("Run worker failed")
            try:
                run = self.store.read_run(run_id)
                if run["status"] in {"running", "waiting_approval"}:
                    failed = self.store.fail_run(run_id, "INTERNAL_ERROR")
                    for item in self.store.canceled_items_for_run(run_id):
                        notification_item = item
                        if item["kind"] == "file_change" and isinstance(
                            item.get("toolCall"), dict
                        ):
                            notification_item = {
                                **item,
                                "toolCall": {
                                    key: value
                                    for key, value in item["toolCall"].items()
                                    if key not in {"argumentsJson", "approvalDiff"}
                                },
                            }
                        self.send(
                            {
                                "jsonrpc": "2.0",
                                "method": "item/completed",
                                "params": {
                                    "sessionId": item["sessionId"],
                                    "runId": item["runId"],
                                    "item": notification_item,
                                },
                            }
                        )
                    self.send(
                        {
                            "jsonrpc": "2.0",
                            "method": "run/completed",
                            "params": {"sessionId": failed["sessionId"], "run": failed},
                        }
                    )
            except Exception:
                logger.exception("Run worker cleanup failed")
        finally:
            should_schedule = False
            with self.worker_lock:
                if self.active_run_id == run_id:
                    self.active_run_id = None
                    self.active_cancel = None
                    self.worker = None
                    should_schedule = not self.shutting_down
                waiting = self.waiting_workers.pop(run_id, None)
                if waiting is not None:
                    should_schedule = should_schedule or not self.shutting_down
            if should_schedule:
                self._schedule_next()

    def _start_worker_locked(self, run_id: str) -> threading.Event:
        cancellation = threading.Event()
        start_gate = threading.Event()
        worker = threading.Thread(
            target=self._run_worker,
            args=(run_id, cancellation, start_gate),
            name=f"eidos-run-{run_id}",
            daemon=True,
        )
        self.active_run_id = run_id
        self.active_cancel = cancellation
        self.worker = worker
        worker.start()
        return start_gate

    def _schedule_next(self) -> None:
        if self.model is None or self.store.health_state != "ready":
            return
        with self.worker_lock:
            if self.worker is not None and self.worker.is_alive():
                return
            claimed = self.store.claim_next_run()
            if claimed is None:
                return
            run_id = str(claimed["id"])
            waiting = self.waiting_workers.pop(run_id, None)
            if waiting is not None:
                self.active_run_id = run_id
                self.active_cancel = waiting.cancellation
                self.worker = waiting.thread
                waiting.resume.set()
            else:
                gate = self._start_worker_locked(run_id)
                gate.set()

    def _park_active_worker(
        self, run_id: str, cancellation: threading.Event
    ) -> None:
        with self.worker_lock:
            if self.active_run_id != run_id or self.worker is not threading.current_thread():
                return
            self.waiting_workers[run_id] = WaitingWorker(
                thread=self.worker,
                cancellation=cancellation,
                resume=threading.Event(),
            )
            self.active_run_id = None
            self.active_cancel = None
            self.worker = None
        self._schedule_next()

    def _wait_for_execution_slot(
        self, run_id: str, cancellation: threading.Event
    ) -> bool:
        with self.worker_lock:
            waiting = self.waiting_workers.get(run_id)
        if waiting is None:
            return False
        self._schedule_next()
        while not waiting.resume.wait(0.1):
            if cancellation.is_set() or self.shutting_down:
                return False
        return not cancellation.is_set()


def _is_canonical_uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def _model_from_environment() -> ModelClient | None:
    fixture = os.environ.get("EIDOS_FAKE_MODEL")
    if fixture == "write":
        return ScriptedModel(
            [
                ModelResponse(
                    tool_calls=(
                        ModelToolCall(
                            "fake-write-1",
                            "write_file",
                            {"path": "approved.txt", "content": "approved\n"},
                        ),
                    )
                ),
                ModelResponse(text="Fake model completed after the approved write."),
            ]
        )
    if fixture == "shell":
        return ScriptedModel(
            [
                ModelResponse(
                    tool_calls=(
                        ModelToolCall(
                            "fake-shell-1",
                            "run_shell",
                            {"command": "printf desktop-shell-ok", "timeoutSeconds": 5},
                        ),
                    )
                ),
                ModelResponse(text="Fake model completed after the approved command."),
            ]
        )
    if fixture != "1":
        return None
    return ScriptedModel(
        [
            ModelResponse(
                tool_calls=(
                    ModelToolCall("fake-read-1", "read_file", {"path": "README.md"}),
                )
            ),
            ModelResponse(text="Fake model completed after reading README.md."),
        ]
    )


def run() -> int:
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    server = RuntimeServer(sys.stdout, model=_model_from_environment())

    try:
        while not server.shutting_down:
            raw_line, too_large = read_bounded_line(sys.stdin.buffer)
            if too_large:
                server.send(protocol_error(None, -32600, "Invalid Request"))
                continue
            if not raw_line:
                break

            try:
                message = json.loads(raw_line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                server.send(protocol_error(None, -32700, "Parse error"))
                continue

            try:
                server.handle(message)
            except Exception:
                logger.exception("Runtime request failed")
                request_id = message.get("id") if isinstance(message, dict) else None
                if valid_request_id(request_id):
                    server.send(business_error(request_id, "INTERNAL_ERROR"))
    finally:
        server.close()

    return 0

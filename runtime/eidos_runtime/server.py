from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import sys
import threading
from typing import Any, BinaryIO, TextIO
import uuid

from eidos_runtime import __version__
from eidos_runtime.deepseek import DeepSeekChatModel
from eidos_runtime.model import ModelClient, ModelResponse, ModelToolCall, ScriptedModel
from eidos_runtime.model_config import ModelConfigError, ModelConfigStore
from eidos_runtime.runtime_loop import RuntimeLoop
from eidos_runtime.seatbelt import run_seatbelt_self_test
from eidos_runtime.storage import (
    ActiveRunError,
    InvalidCursorError,
    InvalidRunStateError,
    ResourceNotFoundError,
    SessionStore,
    StorageError,
    WorkspaceBoundaryError,
)


MAX_MESSAGE_BYTES = 1024 * 1024
PROTOCOL_VERSION = 1

logger = logging.getLogger("eidos.runtime")


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
    return isinstance(value, str) and value.startswith("client-") and len(value) > 7


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

    def handle(self, message: object) -> None:
        if not isinstance(message, dict):
            self.send(protocol_error(None, -32600, "Invalid Request"))
            return

        request_id = message.get("id")
        if (
            message.get("jsonrpc") != "2.0"
            or not valid_request_id(request_id)
            or not isinstance(message.get("method"), str)
            or set(message) - {"jsonrpc", "id", "method", "params"}
        ):
            self.send(protocol_error(None, -32600, "Invalid Request"))
            return

        method = message["method"]
        params = message.get("params", {})

        if method == "initialize":
            self.initialize(request_id, params)
            return
        if method == "runtime/shutdown":
            self.shutdown(request_id, params)
            return
        if not self.initialized:
            self.send(business_error(request_id, "RUNTIME_NOT_INITIALIZED"))
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
        if method == "run/start":
            self.start_run(request_id, params)
            return
        if method == "run/cancel":
            self.cancel_run(request_id, params)
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
            self.model_config.initialize()
            configured_key = self.model_config.api_key()
            if self.model is None and configured_key is not None:
                self.model = DeepSeekChatModel(configured_key)
        except (StorageError, ModelConfigError):
            logger.exception("Runtime storage initialization failed")
            self.send(business_error(request_id, "INTERNAL_ERROR"))
            return

        seatbelt = run_seatbelt_self_test()
        if seatbelt.available:
            logger.info("Seatbelt self-test passed")
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
                        "runShell": False,
                        "modelConfigured": self.model is not None,
                    },
                },
            )
        )
        logger.info("Runtime initialized")

    def create_session(self, request_id: str, params: object) -> None:
        if (
            not isinstance(params, dict)
            or set(params) != {"workspaceRoot"}
            or not isinstance(params.get("workspaceRoot"), str)
        ):
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        try:
            session = self.store.create_session(params["workspaceRoot"])
        except WorkspaceBoundaryError:
            self.send(business_error(request_id, "WORKSPACE_BOUNDARY_VIOLATION"))
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

    def start_run(self, request_id: str, params: object) -> None:
        if (
            not isinstance(params, dict)
            or set(params) != {"sessionId", "userInput"}
            or not _is_canonical_uuid(params.get("sessionId"))
            or not isinstance(params.get("userInput"), str)
            or not params["userInput"].strip()
            or len(params["userInput"].encode("utf-8")) > 64 * 1024
        ):
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        if self.model is None:
            self.send(business_error(request_id, "INVALID_STATE"))
            return
        with self.worker_lock:
            if self.worker is not None and self.worker.is_alive():
                self.send(business_error(request_id, "RUN_ALREADY_ACTIVE"))
                return
            try:
                run, _user_item = self.store.create_run(
                    params["sessionId"], params["userInput"]
                )
            except ResourceNotFoundError:
                self.send(business_error(request_id, "RESOURCE_NOT_FOUND"))
                return
            except ActiveRunError:
                self.send(business_error(request_id, "RUN_ALREADY_ACTIVE"))
                return

            cancellation = threading.Event()
            start_gate = threading.Event()
            worker = threading.Thread(
                target=self._run_worker,
                args=(run["id"], cancellation, start_gate),
                name=f"eidos-run-{run['id']}",
                daemon=True,
            )
            self.active_run_id = run["id"]
            self.active_cancel = cancellation
            self.worker = worker
            try:
                worker.start()
                self.send(response(request_id, run))
            except Exception:
                cancellation.set()
                try:
                    self.store.interrupt_run(run["id"])
                except (ResourceNotFoundError, InvalidRunStateError):
                    pass
                raise
            finally:
                start_gate.set()

    def cancel_run(self, request_id: str, params: object) -> None:
        if (
            not isinstance(params, dict)
            or set(params) != {"runId"}
            or not _is_canonical_uuid(params.get("runId"))
        ):
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        try:
            canceled = self.store.cancel_run(params["runId"])
        except ResourceNotFoundError:
            self.send(business_error(request_id, "RESOURCE_NOT_FOUND"))
            return
        except InvalidRunStateError:
            self.send(business_error(request_id, "INVALID_STATE"))
            return
        with self.worker_lock:
            if self.active_run_id == params["runId"] and self.active_cancel is not None:
                self.active_cancel.set()
        self.send(response(request_id, canceled))

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
        self.send(response(request_id, self.model_config.public_status()))

    def shutdown(self, request_id: str, params: object) -> None:
        if not isinstance(params, dict) or params:
            self.send(protocol_error(request_id, -32602, "Invalid params"))
            return
        with self.worker_lock:
            if self.active_cancel is not None:
                self.active_cancel.set()
            active_run_id = self.active_run_id
            worker = self.worker
        if active_run_id is not None:
            try:
                self.store.cancel_run(active_run_id)
            except (ResourceNotFoundError, InvalidRunStateError):
                pass
        if worker is not None:
            worker.join(timeout=0.75)
        if worker is None or not worker.is_alive():
            self.store.close()
        self.send(response(request_id, {}))
        self.shutting_down = True
        logger.info("Runtime shutdown requested")

    def send(self, message: dict[str, object]) -> None:
        with self.output_lock:
            write_message(self.output, message)

    def wait_for_worker(self, timeout: float = 5.0) -> None:
        with self.worker_lock:
            worker = self.worker
        if worker is not None:
            worker.join(timeout=timeout)

    def close(self) -> None:
        with self.worker_lock:
            cancellation = self.active_cancel
            worker = self.worker
            active_run_id = self.active_run_id
        if cancellation is not None:
            cancellation.set()
        if active_run_id is not None and not self.shutting_down:
            try:
                self.store.interrupt_run(active_run_id)
            except (ResourceNotFoundError, InvalidRunStateError):
                pass
        if worker is not None:
            worker.join(timeout=0.75)
        if worker is None or not worker.is_alive():
            self.store.close()

    def _run_worker(
        self,
        run_id: str,
        cancellation: threading.Event,
        start_gate: threading.Event,
    ) -> None:
        start_gate.wait()
        try:
            RuntimeLoop(self.store, self.model, self.send).run(run_id, cancellation)
        except Exception:
            logger.exception("Run worker failed")
            try:
                run = self.store.read_run(run_id)
                if run["status"] == "running":
                    failed = self.store.fail_run(run_id, "INTERNAL_ERROR")
                    for item in self.store.canceled_items_for_run(run_id):
                        self.send(
                            {
                                "jsonrpc": "2.0",
                                "method": "item/completed",
                                "params": {
                                    "sessionId": item["sessionId"],
                                    "runId": item["runId"],
                                    "item": item,
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
            with self.worker_lock:
                if self.active_run_id == run_id:
                    self.active_run_id = None
                    self.active_cancel = None


def _is_canonical_uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def _model_from_environment() -> ModelClient | None:
    if os.environ.get("EIDOS_FAKE_MODEL") != "1":
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

from __future__ import annotations

import json
import logging
from pathlib import Path
import sys
from typing import Any, BinaryIO, TextIO
import uuid

from eidos_runtime import __version__
from eidos_runtime.seatbelt import run_seatbelt_self_test
from eidos_runtime.storage import (
    InvalidCursorError,
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
    output.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")))
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
    def __init__(self, output: TextIO, data_directory: Path | None = None) -> None:
        self.output = output
        self.initialized = False
        self.shutting_down = False
        self.store = SessionStore(data_directory)

    def handle(self, message: object) -> None:
        if not isinstance(message, dict):
            write_message(self.output, protocol_error(None, -32600, "Invalid Request"))
            return

        request_id = message.get("id")
        if (
            message.get("jsonrpc") != "2.0"
            or not valid_request_id(request_id)
            or not isinstance(message.get("method"), str)
            or set(message) - {"jsonrpc", "id", "method", "params"}
        ):
            write_message(self.output, protocol_error(None, -32600, "Invalid Request"))
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
            write_message(
                self.output,
                business_error(request_id, "RUNTIME_NOT_INITIALIZED"),
            )
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

        write_message(self.output, protocol_error(request_id, -32601, "Method not found"))

    def initialize(self, request_id: str, params: object) -> None:
        if not valid_initialize_params(params):
            write_message(self.output, protocol_error(request_id, -32602, "Invalid params"))
            return
        if self.initialized:
            write_message(self.output, business_error(request_id, "INVALID_STATE"))
            return
        if params["protocolVersion"] != PROTOCOL_VERSION:
            write_message(
                self.output,
                business_error(request_id, "PROTOCOL_VERSION_UNSUPPORTED"),
            )
            return

        try:
            self.store.initialize()
        except StorageError:
            logger.exception("Runtime storage initialization failed")
            write_message(self.output, business_error(request_id, "INTERNAL_ERROR"))
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
        write_message(
            self.output,
            response(
                request_id,
                {
                    "protocolVersion": PROTOCOL_VERSION,
                    "runtimeVersion": __version__,
                    "capabilities": {"runShell": False},
                },
            ),
        )
        logger.info("Runtime initialized")

    def create_session(self, request_id: str, params: object) -> None:
        if (
            not isinstance(params, dict)
            or set(params) != {"workspaceRoot"}
            or not isinstance(params.get("workspaceRoot"), str)
        ):
            write_message(self.output, protocol_error(request_id, -32602, "Invalid params"))
            return
        try:
            session = self.store.create_session(params["workspaceRoot"])
        except WorkspaceBoundaryError:
            write_message(
                self.output,
                business_error(request_id, "WORKSPACE_BOUNDARY_VIOLATION"),
            )
            return
        write_message(self.output, response(request_id, session))

    def list_sessions(self, request_id: str, params: object) -> None:
        if not isinstance(params, dict) or set(params) - {"limit", "cursor"}:
            write_message(self.output, protocol_error(request_id, -32602, "Invalid params"))
            return
        limit = params.get("limit", 50)
        cursor = params.get("cursor")
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= 200
            or (cursor is not None and not isinstance(cursor, str))
        ):
            write_message(self.output, protocol_error(request_id, -32602, "Invalid params"))
            return
        try:
            result = self.store.list_sessions(limit=limit, cursor=cursor)
        except InvalidCursorError:
            write_message(self.output, protocol_error(request_id, -32602, "Invalid params"))
            return
        write_message(self.output, response(request_id, result))

    def read_session(self, request_id: str, params: object) -> None:
        if (
            not isinstance(params, dict)
            or set(params) != {"sessionId"}
            or not _is_canonical_uuid(params.get("sessionId"))
        ):
            write_message(self.output, protocol_error(request_id, -32602, "Invalid params"))
            return
        session = self.store.read_session(params["sessionId"])
        if session is None:
            write_message(self.output, business_error(request_id, "RESOURCE_NOT_FOUND"))
            return
        write_message(
            self.output,
            response(request_id, {"session": session, "runs": [], "items": []}),
        )

    def shutdown(self, request_id: str, params: object) -> None:
        if not isinstance(params, dict) or params:
            write_message(self.output, protocol_error(request_id, -32602, "Invalid params"))
            return
        self.store.close()
        write_message(self.output, response(request_id, {}))
        self.shutting_down = True
        logger.info("Runtime shutdown requested")


def _is_canonical_uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value
    except ValueError:
        return False


def run() -> int:
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    server = RuntimeServer(sys.stdout)

    try:
        while not server.shutting_down:
            raw_line, too_large = read_bounded_line(sys.stdin.buffer)
            if too_large:
                write_message(sys.stdout, protocol_error(None, -32600, "Invalid Request"))
                continue
            if not raw_line:
                break

            try:
                message = json.loads(raw_line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                write_message(sys.stdout, protocol_error(None, -32700, "Parse error"))
                continue

            try:
                server.handle(message)
            except Exception:
                logger.exception("Runtime request failed")
                request_id = message.get("id") if isinstance(message, dict) else None
                if valid_request_id(request_id):
                    write_message(sys.stdout, business_error(request_id, "INTERNAL_ERROR"))
    finally:
        server.store.close()

    return 0

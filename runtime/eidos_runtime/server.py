from __future__ import annotations

import json
import logging
import sys
from typing import Any, BinaryIO, TextIO

from eidos_runtime import __version__


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
    def __init__(self, output: TextIO) -> None:
        self.output = output
        self.initialized = False
        self.shutting_down = False

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

    def shutdown(self, request_id: str, params: object) -> None:
        if not isinstance(params, dict) or params:
            write_message(self.output, protocol_error(request_id, -32602, "Invalid params"))
            return
        write_message(self.output, response(request_id, {}))
        self.shutting_down = True
        logger.info("Runtime shutdown requested")


def run() -> int:
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    server = RuntimeServer(sys.stdout)

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

    return 0

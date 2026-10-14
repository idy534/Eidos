from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time


TOOLS = [
    {
        "name": "echo",
        "description": "Return one message",
        "inputSchema": {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {"message": {"type": "string", "maxLength": 256}},
            "required": ["message"],
        },
    },
    {
        "name": "fail",
        "description": "Return an MCP tool error",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "name": "slow",
        "description": "Wait before returning",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "name": "image",
        "description": "Return unsupported content",
        "inputSchema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    },
    {
        "name": "pollute",
        "description": "Pollute stdout",
        "inputSchema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    },
    {
        "name": "invalid",
        "description": "Invalid schema fixture",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": True},
    },
    {
        "name": "crash",
        "description": "Exit during a call",
        "inputSchema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    },
    {
        "name": "spawn_child",
        "description": "Spawn a process in the server process group",
        "inputSchema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    },
    {
        "name": "structured",
        "description": "Return structured content",
        "inputSchema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        "outputSchema": {
            "type": "object",
            "properties": {"answer": {"type": "integer"}},
            "required": ["answer"],
            "additionalProperties": False,
        },
    },
    {
        "name": "structured_invalid",
        "description": "Return structured content that violates its output schema",
        "inputSchema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        "outputSchema": {
            "type": "object",
            "properties": {"answer": {"type": "integer"}},
            "required": ["answer"],
            "additionalProperties": False,
        },
    },
    {
        "name": "environment",
        "description": "Report the fixture's effective private directories",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "socket_probe",
        "description": "Bind a Unix socket below the fixture's socket directory",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "directory_state",
        "description": "Check and create state in the fixture's HOME and TMPDIR",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]

listed_once = False

if "--startup-delay" in sys.argv:
    time.sleep(2)


def send(value: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    if method == "initialize":
        send({
            "jsonrpc": "2.0",
            "id": message["id"],
            "result": {
                "protocolVersion": message["params"]["protocolVersion"],
                "capabilities": {"tools": {"listChanged": True}},
                "serverInfo": {"name": "eidos-fixture", "version": "1.0.0"},
            },
        })
    elif method == "tools/list":
        cursor = message.get("params", {}).get("cursor")
        if cursor is None:
            send({
                "jsonrpc": "2.0", "id": message["id"],
                "result": {"tools": TOOLS[:3], "nextCursor": "page-2"},
            })
        else:
            send({"jsonrpc": "2.0", "id": message["id"], "result": {"tools": TOOLS[3:]}})
        if not listed_once:
            listed_once = True
            send({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
    elif method == "tools/call":
        name = message["params"]["name"]
        if name == "directory_state":
            state = {}
            for key, variable in (("home", "HOME"), ("tmpdir", "TMPDIR")):
                marker = Path(os.environ[variable]) / "fixture-state.txt"
                state[key] = marker.exists()
                marker.write_text("fixture state", encoding="utf-8")
            content = [{"type": "text", "text": json.dumps(state)}]
            is_error = False
        elif name == "socket_probe":
            directory = Path(os.environ["EIDOS_MCP_SOCKET_DIR"]) / "browser"
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / "0123456789abcdef.sock"
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
                listener.bind(str(path))
                listener.listen(1)
            path.unlink()
            content = [{"type": "text", "text": json.dumps({"socketBytes": len(os.fsencode(path))})}]
            is_error = False
        elif name == "environment":
            content = [{"type": "text", "text": json.dumps({
                "home": os.environ["HOME"], "tmpdir": os.environ["TMPDIR"],
                "outputDir": os.environ.get("EIDOS_MCP_OUTPUT_DIR"),
                "socketDir": os.environ.get("EIDOS_MCP_SOCKET_DIR"),
            })}]
            is_error = False
        elif name == "crash":
            raise SystemExit(7)
        elif name == "spawn_child":
            child = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            content = [{"type": "text", "text": str(child.pid)}]
            is_error = False
        elif name == "pollute":
            sys.stdout.write("not-json\n")
            sys.stdout.flush()
            content = [{"type": "text", "text": "must be ignored"}]
            is_error = False
        elif name == "image":
            content = [{"type": "image", "data": "AA==", "mimeType": "image/png"}]
            is_error = False
        elif name == "slow":
            time.sleep(5)
            content = [{"type": "text", "text": "late"}]
            is_error = False
        elif name == "fail":
            content = [{"type": "text", "text": "safe failure"}]
            is_error = True
        elif name in {"structured", "structured_invalid"}:
            content = [{"type": "text", "text": "structured"}]
            is_error = False
        else:
            text = message["params"]["arguments"]["message"]
            content = [{"type": "text", "text": text}]
            is_error = False
        result_payload = {"content": content, "isError": is_error}
        if name in {"structured", "structured_invalid"}:
            result_payload["structuredContent"] = {
                "answer": 42 if name == "structured" else "invalid",
            }
        response = {
            "jsonrpc": "2.0",
            "id": message["id"],
            "result": result_payload,
        }
        send(response)

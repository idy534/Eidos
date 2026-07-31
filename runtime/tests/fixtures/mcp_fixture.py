from __future__ import annotations

import json
import subprocess
import sys
import time


TOOLS = [
    {
        "name": "echo",
        "description": "Return one message",
        "inputSchema": {
            "type": "object",
            "properties": {"message": {"type": "string", "maxLength": 256}},
            "required": ["message"],
            "additionalProperties": False,
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
        if name == "crash":
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
        elif name == "structured":
            content = [{"type": "text", "text": "structured"}]
            is_error = False
        else:
            text = message["params"]["arguments"]["message"]
            content = [{"type": "text", "text": text}]
            is_error = False
        result_payload = {"content": content, "isError": is_error}
        if name == "structured":
            result_payload["structuredContent"] = {"answer": 42}
        response = {
            "jsonrpc": "2.0",
            "id": message["id"],
            "result": result_payload,
        }
        send(response)

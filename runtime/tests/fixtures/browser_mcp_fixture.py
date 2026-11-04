"""Stand-in for Playwright MCP 0.0.84 over stdio, without Node or Chrome.

It lists the pinned upstream tool names and annotations, records each call's
arguments and the launch arguments into $TMPDIR, and returns a page snapshot.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

READ_ONLY = {
    "browser_console_messages", "browser_find", "browser_network_request", "browser_network_requests",
    "browser_snapshot", "browser_take_screenshot", "browser_wait_for",
}
NAMES = [
    "browser_click", "browser_close", "browser_console_messages", "browser_drag", "browser_drop",
    "browser_emulate_media", "browser_evaluate", "browser_file_upload", "browser_fill_form", "browser_find",
    "browser_handle_dialog", "browser_hover", "browser_navigate", "browser_navigate_back",
    "browser_network_request", "browser_network_requests", "browser_press_key", "browser_resize",
    "browser_run_code_unsafe", "browser_select_option", "browser_snapshot", "browser_tabs",
    "browser_take_screenshot", "browser_type", "browser_wait_for",
]
PNG = "iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAEElEQVR4nGP8zwACTGCSAQANHQEDgslx/wAAAABJRU5ErkJggg=="
SCHEMA = {
    "type": "object",
    "properties": {
        "url": {"type": "string"}, "target": {"type": "string"}, "element": {"type": "string"},
        "text": {"type": "string"}, "action": {"type": "string"}, "index": {"type": "number"},
        "paths": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}
TOOLS = [
    {
        "name": name, "description": f"Fixture {name}", "inputSchema": SCHEMA,
        "annotations": {"readOnlyHint": name in READ_ONLY},
    }
    for name in NAMES
]
# Tests may keep the log outside the connection directory, which is removed on close.
LOG = Path(os.environ.get("BROWSER_FIXTURE_LOG") or Path(os.environ["TMPDIR"]) / "browser-fixture.jsonl")
EXTENSION = os.environ.get("PLAYWRIGHT_MCP_EXTENSION") == "1"
RELAY = "ws%3A%2F%2F127.0.0.1%3A50123%2Fextension%2F5733f635-0f0a-41ef-9972-305b283803db"
with LOG.open("a", encoding="utf-8") as stream:
    stream.write(json.dumps({"launch": sys.argv[1:], "cwd": os.getcwd(), "extension": EXTENSION and {
        # Names and shapes only; the token value is never recorded.
        "token": bool(os.environ.get("PLAYWRIGHT_MCP_EXTENSION_TOKEN")),
        "helper": os.access(os.environ.get("PLAYWRIGHT_MCP_EXECUTABLE_PATH", ""), os.X_OK),
        "preload": os.environ.get("NODE_OPTIONS", "").startswith("--require "),
    }}) + "\n")


def open_connect_page(url: str) -> None:
    """What Playwright's relay does: run the configured "browser" with the page URL."""
    subprocess.run([os.environ["PLAYWRIGHT_MCP_EXECUTABLE_PATH"], "--profile-directory=Default", url],
                   check=False, timeout=10)


def send(value: dict[str, object]) -> None:
    sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    message = json.loads(line)
    method = message.get("method")
    if method == "initialize":
        with LOG.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"client": message["params"].get("clientInfo", {}).get("name")}) + "\n")
        send({"jsonrpc": "2.0", "id": message["id"], "result": {
            "protocolVersion": message["params"]["protocolVersion"],
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "Playwright", "version": "0.0.84"},
        }})
    elif method == "tools/list":
        send({"jsonrpc": "2.0", "id": message["id"], "result": {"tools": TOOLS}})
    elif method == "tools/call":
        name = message["params"]["name"]
        arguments = message["params"].get("arguments", {})
        with LOG.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"call": name, "arguments": arguments}) + "\n")
        if EXTENSION and arguments.get("url") == "https://connect.test/":
            open_connect_page("https://evil.test/connect.html")
            open_connect_page(
                f"chrome-extension://mmlmfjhmonkocbjadbfplnigmagldckm/connect.html?mcpRelayUrl={RELAY}"
                "&client=%7B%22name%22%3A%22Eidos+%C2%B7+t%22%7D&protocolVersion=2&token=secret-token"
            )
        if EXTENSION and arguments.get("url") == "https://unpaired.test/":
            send({"jsonrpc": "2.0", "id": message["id"], "result": {"content": [{"type": "text", "text": (
                "### Error\nError: Playwright extension did not connect within 30s after opening the connect page."
            )}], "isError": True}})
            continue
        content: list[dict[str, object]] = [{
            "type": "text",
            "text": f"### Result\nran {name}\n### Page state\n- Page URL: https://example.com/\n- Page Title: Example Domain\n"
                    "### Snapshot\n```yaml\n- heading \"Example Domain\" [ref=e2]\n```",
        }]
        if name == "browser_take_screenshot":
            content[0]["text"] = (
                "### Result\n- [Screenshot of viewport](../../../../private/tmp/eidos-mcp-x/tmp/browser-output/page-1.png)\n"
                + str(content[0]["text"]).split("\n", 2)[2]
            )
            content.append({"type": "image", "data": PNG, "mimeType": "image/png"})
        send({"jsonrpc": "2.0", "id": message["id"], "result": {"content": content, "isError": False}})

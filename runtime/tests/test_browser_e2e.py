"""Opt-in end-to-end check with real Playwright MCP, Seatbelt and Chrome.

Run with a prepared Playwright MCP install and Node, for example:
EIDOS_BROWSER_E2E_NODE=~/Eidos/Resource/runtimes/node/24.20.0/darwin-arm64/bin/node \\
EIDOS_BROWSER_E2E_ENTRY=/path/to/node_modules/@playwright/mcp/cli.js \\
uv run --locked pytest runtime/tests/test_browser_e2e.py
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import unittest
from unittest.mock import patch

import pytest

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.extensions.browser_plugin import BUNDLED_BROWSER_PLUGIN_ROOT, BrowserLaunch
from eidos_runtime.extensions.plugins import PluginCatalog
from eidos_runtime.extensions.skills import SkillCatalog
from eidos_runtime.infrastructure.browser_host import BrowserHost, find_chrome
from eidos_runtime.infrastructure.browser_runtime import BrowserRuntime
from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel
from eidos_runtime.runtime.approval import ApprovalDecision
from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel
from eidos_runtime.runtime.engine import RuntimeEngine
from eidos_runtime.runtime.resource_registry import ResourceRegistry
from eidos_runtime.runtime.run_resources import RunResources
from eidos_runtime.sandbox.seatbelt import is_seatbelt_usable

pytestmark = pytest.mark.platform
NODE = os.environ.get("EIDOS_BROWSER_E2E_NODE")
ENTRY = os.environ.get("EIDOS_BROWSER_E2E_ENTRY")


@unittest.skipUnless(
    NODE and ENTRY and find_chrome() is not None and is_seatbelt_usable(),
    "set EIDOS_BROWSER_E2E_NODE and EIDOS_BROWSER_E2E_ENTRY with Chrome and Seatbelt available",
)
class BrowserEndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-browser-e2e-")
        root = Path(self.temporary.name).resolve()
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        self.store = SessionStore(root / "data")
        self.store.initialize()
        self.plugins = PluginCatalog(self.store)
        self.plugins.import_directory(BUNDLED_BROWSER_PLUGIN_ROOT)
        self.plugins.set_enabled("eidos-browser", True)
        self.plugins.set_mcp_enabled("eidos-browser", "browser", True)
        self.resources = ResourceRegistry()
        self.kernel = RuntimeAsyncKernel(resource_registry=self.resources)
        self.kernel.start()
        # A short profile path keeps Chrome's socket paths inside macOS limits.
        self.profile = Path(tempfile.mkdtemp(prefix="eidos-e2e-chrome-", dir="/private/tmp"))
        self.host = BrowserHost(self.profile / "default", extra_arguments=("--headless=new",))
        node, entry = Path(str(NODE)).expanduser().resolve(), Path(str(ENTRY)).expanduser().resolve()
        entry_root = next(parent.parent for parent in entry.parents if parent.name == "node_modules")
        launch = lambda _resources: BrowserLaunch(  # noqa: E731
            runtime=BrowserRuntime(node=node, entry=entry, read_roots=(node.parents[1], entry_root)),
            variables={"BROWSER_CDP_ENDPOINT": self.host.endpoint(), "BROWSER_IMAGE_RESPONSES": "allow"},
            ensure_browser=self.host.ensure, chrome_installed=lambda: True,
        )
        self.patch = patch.object(RunResources, "_browser_launch", launch)
        self.patch.start()

    def tearDown(self) -> None:
        self.patch.stop()
        self.host.shutdown()
        self.kernel.close()
        self.store.close()
        shutil.rmtree(self.profile, ignore_errors=True)
        self.temporary.cleanup()

    def run_calls(self, session_id: str, calls) -> dict[str, dict[str, object]]:
        run, _ = self.store.create_run(
            session_id, "Browse", extension_snapshot=SkillCatalog(self.plugins).extension_snapshot(),
        )
        model = ScriptedModel([
            ModelResponse(tool_calls=tuple(
                ModelToolCall(f"call-{i}", f"mcp__browser__{name}", arguments) for i, (name, arguments) in enumerate(calls)
            )),
            ModelResponse(text="Done."),
        ])
        RuntimeEngine(
            self.store, model, lambda _message: None,
            request_approval=lambda _request, _cancel: ApprovalDecision("approve"),
            async_kernel=self.kernel, resource_registry=self.resources, mcp_sandbox=True,
        ).run(run["id"], threading.Event())
        self.contexts = model.contexts
        return {
            row[0].removeprefix("mcp__browser__"): json.loads(row[1])
            for row in self.store.connection.execute(
                "SELECT tool_calls.tool_name, tool_calls.result_json FROM tool_calls "
                "JOIN items ON items.id = tool_calls.item_id WHERE items.run_id = ?", (run["id"],),
            )
        }

    def test_sandboxed_playwright_drives_the_shared_chrome_across_runs(self) -> None:
        session = self.store.create_session(str(self.workspace))
        page = "data:text/html,<title>Eidos E2E</title><h1>Hello browser</h1><button>Go</button>"
        first = self.run_calls(session["id"], [
            ("browser_navigate", {"url": page}), ("browser_snapshot", {}), ("browser_take_screenshot", {}),
        ])
        self.assertEqual({name: value["outcome"] for name, value in first.items()},
                         {name: "success" for name in first}, first)
        self.assertIn("Hello browser", first["browser_snapshot"]["data"]["text"])
        screenshots = [
            item for item in self.contexts[-1]
            if item.get("name") == "mcp__browser__browser_take_screenshot" and item.get("toolContent")
        ]
        self.assertEqual(len(screenshots), 1)
        # A second Run reconnects to the same Chrome and still sees the page.
        second = self.run_calls(session["id"], [("browser_tabs", {"action": "list"})])
        self.assertIn("Eidos E2E", second["browser_tabs"]["data"]["text"])

    def test_downloads_are_denied_in_the_shared_chrome(self) -> None:
        name = f"eidos-e2e-download-{os.getpid()}.txt"
        downloads = Path.home() / "Downloads"
        session = self.store.create_session(str(self.workspace))
        page = f"data:text/html,<a href='data:text/plain,probe' download='{name}'>get file</a>"
        try:
            results = self.run_calls(session["id"], [("browser_navigate", {"url": page}), ("browser_snapshot", {})])
            text = str(results["browser_snapshot"]["data"]["text"])
            target = text.split("[ref=", 1)[1].split("]", 1)[0]
            self.run_calls(session["id"], [("browser_click", {"element": "get file link", "target": target})])
            import time
            time.sleep(2)
            self.assertFalse((downloads / name).exists())
        finally:
            (downloads / name).unlink(missing_ok=True)

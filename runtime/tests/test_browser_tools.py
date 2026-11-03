from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.extensions.browser_plugin import BUNDLED_BROWSER_PLUGIN_ROOT, BrowserLaunch
from eidos_runtime.extensions.plugins import PluginCatalog
from eidos_runtime.extensions.skills import SkillCatalog
from eidos_runtime.infrastructure.browser_host import BrowserUnavailable
from eidos_runtime.infrastructure.browser_runtime import BrowserRuntime
from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel
from eidos_runtime.persistence.browser_grants import BrowserGrantRepository
from eidos_runtime.runtime.approval import ApprovalDecision
from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel
from eidos_runtime.runtime.engine import RuntimeEngine
from eidos_runtime.runtime.resource_registry import ResourceRegistry
from eidos_runtime.runtime.run_resources import RunResources

FIXTURE = Path(__file__).parent / "fixtures" / "browser_mcp_fixture.py"


class BrowserToolAuthorizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-browser-tools-")
        root = Path(self.temporary.name).resolve()
        self.data = root / "data"
        self.data.mkdir(mode=0o700)
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        (self.workspace / "report.pdf").write_bytes(b"%PDF-1.4\n")
        self.outside = root / "outside.txt"
        self.outside.write_text("secret\n")
        self.store = SessionStore(self.data)
        self.store.initialize()
        self.plugins = PluginCatalog(self.store)
        self.plugins.import_directory(BUNDLED_BROWSER_PLUGIN_ROOT)
        self.plugins.set_enabled("eidos-browser", True)
        self.plugins.set_mcp_enabled("eidos-browser", "browser", True)
        self.resources = ResourceRegistry()
        self.kernel = RuntimeAsyncKernel(resource_registry=self.resources)
        self.kernel.start()
        self.ensured: list[str] = []
        self.chrome = True
        self.ensure_error: str | None = None

        def ensure(_cancel: threading.Event) -> str:
            if self.ensure_error is not None:
                raise BrowserUnavailable(self.ensure_error)
            self.ensured.append("ensure")
            return "http://127.0.0.1:9333"

        self.launch = BrowserLaunch(
            runtime=BrowserRuntime(node=Path(sys.executable), entry=FIXTURE, read_roots=(root, root)),
            variables={"BROWSER_CDP_ENDPOINT": "http://127.0.0.1:9333", "BROWSER_IMAGE_RESPONSES": "allow"},
            ensure_browser=ensure, chrome_installed=lambda: self.chrome,
        )
        self.patch = patch.object(RunResources, "_browser_launch", lambda _resources: self.launch)
        self.patch.start()

    def tearDown(self) -> None:
        self.patch.stop()
        self.kernel.close()
        self.assertEqual(self.resources.active_resources(), ())
        self.store.close()
        self.temporary.cleanup()

    def run_tools(self, session_id: str, calls, *, mode: str = "manual", decision: str = "approve"):
        run, _ = self.store.create_run(
            session_id, "Use the browser", approval_mode=mode,
            extension_snapshot=SkillCatalog(self.plugins).extension_snapshot(),
        )
        model = ScriptedModel([
            ModelResponse(tool_calls=tuple(
                ModelToolCall(f"call-{index}", f"mcp__browser__{name}", arguments)
                for index, (name, arguments) in enumerate(calls)
            )),
            ModelResponse(text="Done."),
        ])
        requests: list[dict[str, object]] = []

        def approve(request, _cancel):
            requests.append(dict(request.payload) if hasattr(request, "payload") else dict(request))
            return ApprovalDecision(decision)

        RuntimeEngine(
            self.store, model, lambda _message: None, request_approval=approve,
            async_kernel=self.kernel, resource_registry=self.resources, mcp_sandbox=False,
        ).run(run["id"], threading.Event())
        results = {
            row["tool_name"].removeprefix("mcp__browser__"): json.loads(row["result_json"])
            for row in self.store.connection.execute(
                "SELECT tool_calls.tool_name, tool_calls.result_json FROM tool_calls "
                "JOIN items ON items.id = tool_calls.item_id WHERE items.run_id = ?", (run["id"],),
            )
        }
        return run, requests, results

    def test_interaction_asks_once_per_session_and_reads_never_ask(self) -> None:
        session = self.store.create_session(str(self.workspace))
        _, requests, results = self.run_tools(session["id"], [
            ("browser_snapshot", {}), ("browser_navigate", {"url": "https://example.com"}),
        ])
        self.assertEqual([request.get("kind") for request in requests], ["browser_access"])
        self.assertEqual(requests[0]["grantScope"], "session")
        self.assertTrue(requests[0]["riskWarning"])
        self.assertEqual({name: value["outcome"] for name, value in results.items()},
                         {"browser_snapshot": "success", "browser_navigate": "success"})
        grant = BrowserGrantRepository(self.store.database).active(session["id"])
        self.assertIsNotNone(grant)
        self.assertIsNotNone(grant.source_approval_id)
        self.ensured.clear()
        _, again, results = self.run_tools(session["id"], [("browser_click", {"ref": "e2", "element": "link"})])
        self.assertEqual(again, [])
        self.assertEqual(results["browser_click"]["outcome"], "success")
        intent = self.store.connection.execute(
            "SELECT preconditions_json FROM durable_intents ORDER BY rowid DESC LIMIT 1",
        ).fetchone()[0]
        self.assertEqual(json.loads(intent)["authorization"], "browser_session_grant")
        other = self.store.create_session(str(self.workspace))
        self.ensured.clear()
        _, other_requests, _ = self.run_tools(other["id"], [("browser_click", {"ref": "e2", "element": "link"})])
        self.assertEqual([request.get("kind") for request in other_requests], ["browser_access"])

    def test_rejected_access_creates_no_grant_and_does_not_start_chrome(self) -> None:
        session = self.store.create_session(str(self.workspace))
        _, requests, results = self.run_tools(
            session["id"], [("browser_navigate", {"url": "https://example.com"})], decision="reject",
        )
        self.assertEqual(len(requests), 1)
        self.assertEqual(results["browser_navigate"]["outcome"], "declined")
        # The call waited for the user, so Chrome never started for it.
        self.assertIsNone(BrowserGrantRepository(self.store.database).active(session["id"]))
        self.assertEqual(self.ensured, [])

    def test_revoked_grant_asks_again(self) -> None:
        session = self.store.create_session(str(self.workspace))
        self.run_tools(session["id"], [("browser_navigate", {"url": "https://example.com"})])
        BrowserGrantRepository(self.store.database).revoke(session["id"], "user")
        self.ensured.clear()
        _, requests, _ = self.run_tools(session["id"], [("browser_navigate", {"url": "https://example.com"})])
        self.assertEqual([request.get("kind") for request in requests], ["browser_access"])

    def test_full_access_runs_without_approval_or_grant(self) -> None:
        session = self.store.create_session(str(self.workspace))
        _, requests, results = self.run_tools(session["id"], [
            ("browser_navigate", {"url": "https://example.com"}), ("browser_file_upload", {"paths": [str(self.outside)]}),
        ], mode="full_access")
        self.assertEqual(requests, [])
        self.assertEqual(results["browser_navigate"]["outcome"], "success")
        self.assertIsNone(BrowserGrantRepository(self.store.database).active(session["id"]))

    def test_uploads_are_approved_every_time_and_limited_to_the_workspace(self) -> None:
        session = self.store.create_session(str(self.workspace))
        _, requests, results = self.run_tools(session["id"], [("browser_file_upload", {"paths": ["report.pdf"]})])
        self.assertEqual([request.get("kind") for request in requests], ["external_tool"])
        self.assertEqual(requests[0]["arguments"]["paths"], [str(self.workspace / "report.pdf")])
        self.assertEqual(results["browser_file_upload"]["outcome"], "success")
        self.ensured.clear()
        _, again, _ = self.run_tools(session["id"], [("browser_file_upload", {"paths": ["report.pdf"]})])
        self.assertEqual(len(again), 1)
        _, outside_requests, outside = self.run_tools(
            session["id"], [("browser_file_upload", {"paths": [str(self.outside)]})],
        )
        self.assertEqual(outside_requests, [])
        self.assertEqual(outside["browser_file_upload"]["code"], "browser_upload_outside_workspace")

    def test_missing_chrome_fails_before_approval(self) -> None:
        self.chrome = False
        session = self.store.create_session(str(self.workspace))
        _, requests, results = self.run_tools(session["id"], [("browser_navigate", {"url": "https://example.com"})])
        self.assertEqual(requests, [])
        self.assertEqual(results["browser_navigate"]["code"], "browser_unavailable")
        self.assertEqual(self.store.connection.execute("SELECT count(*) FROM durable_intents").fetchone()[0], 0)

    def test_chrome_start_failure_is_a_known_tool_error(self) -> None:
        self.ensure_error = "browser_profile_in_use"
        session = self.store.create_session(str(self.workspace))
        _, _, results = self.run_tools(session["id"], [("browser_snapshot", {})])
        self.assertEqual(results["browser_snapshot"]["code"], "browser_profile_in_use")
        self.assertFalse(results["browser_snapshot"]["reconciliationRequired"])

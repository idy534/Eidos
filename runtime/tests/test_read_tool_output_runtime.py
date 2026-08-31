from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.db.storage import SessionStore  # noqa: E402
from eidos_runtime.model.client import (  # noqa: E402
    ModelResponse,
    ModelToolCall,
    ScriptedModel,
)
from eidos_runtime.runtime.engine import RuntimeEngine  # noqa: E402


class ReadToolOutputRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(
            prefix="eidos-read-output-runtime-"
        )
        root = Path(self.temporary.name)
        self.data = root / "data"
        self.workspace = root / "workspace"
        self.data.mkdir(mode=0o700)
        self.workspace.mkdir()
        self.store = SessionStore(self.data)
        self.store.initialize()
        self.session = self.store.create_session(str(self.workspace))

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _persist_terminal_shell_output(
        self, *, provider_call_id: str, stdout: str, stderr: str = ""
    ) -> None:
        historical_run, _ = self.store.create_run(
            self.session["id"], "Produce shell output"
        )
        item = self.store.create_tool_item(
            historical_run["id"],
            1,
            0,
            provider_call_id,
            "run_shell",
            json.dumps({"command": "printf persisted"}),
        )
        result = {
            "schemaVersion": 1,
            "toolContractVersion": 1,
            "toolName": "run_shell",
            "outcome": "success",
            "code": "ok",
            "summary": "Command completed",
            "data": {
                "exitCode": 0,
                "stdout": stdout,
                "stderr": stderr,
                "truncated": False,
                "omittedBytes": 0,
                "termination": "exit",
                "workspaceChanged": False,
            },
            "sideEffectsMayExist": False,
            "reconciliationRequired": False,
        }
        self.store.complete_tool_item(
            item["id"],
            json.dumps(result, ensure_ascii=False, separators=(",", ":")),
            item_status="completed",
            tool_status="completed",
        )
        self.store.fail_run(historical_run["id"], "fixture_complete")

    def _current_read_item(self, session_id: str) -> dict[str, object]:
        snapshot = self.store.read_session_snapshot(session_id)
        tool_items = [
            item
            for item in snapshot["items"]
            if item.get("toolCall", {}).get("toolName") == "read_tool_output"
        ]
        self.assertEqual(len(tool_items), 1)
        return tool_items[0]

    def test_engine_reads_persisted_output_and_exposes_committed_result(self) -> None:
        self._persist_terminal_shell_output(
            provider_call_id="persisted-shell",
            stdout="persisted stdout",
            stderr="persisted stderr",
        )
        run, _ = self.store.create_run(self.session["id"], "Read shell output")
        model = ScriptedModel(
            [
                ModelResponse(
                    tool_calls=(
                        ModelToolCall(
                            "read-call",
                            "read_tool_output",
                            {
                                "callId": "persisted-shell",
                                "stream": "stdout",
                                "maxBytes": 16,
                            },
                        ),
                    )
                ),
                ModelResponse(text="I found the persisted shell output."),
            ]
        )

        with patch(
            "eidos_runtime.runtime.tool_runtime.run_shell",
            side_effect=AssertionError("read_tool_output must not run Shell"),
        ) as run_shell:
            RuntimeEngine(self.store, model, lambda _message: None).run(
                run["id"], threading.Event()
            )

        run_shell.assert_not_called()
        self.assertEqual(self.store.read_run(run["id"])["status"], "succeeded")

        read_item = self._current_read_item(self.session["id"])
        tool_call = read_item["toolCall"]
        self.assertEqual(tool_call["status"], "completed")
        committed = json.loads(tool_call["resultJson"])
        self.assertEqual(committed["outcome"], "success")
        self.assertEqual(committed["code"], "ok")
        self.assertEqual(committed["data"]["callId"], "persisted-shell")
        self.assertEqual(committed["data"]["stream"], "stdout")
        self.assertEqual(committed["data"]["content"], "persisted stdout")

        visible_results = [
            entry
            for entry in model.contexts[1]
            if entry.get("type") == "tool_result"
            and entry.get("name") == "read_tool_output"
        ]
        self.assertEqual(len(visible_results), 1)
        visible = json.loads(visible_results[0]["result"])
        self.assertEqual(visible["outcome"], "success")
        self.assertEqual(visible["code"], "ok")
        self.assertEqual(visible["data"]["callId"], "persisted-shell")
        self.assertEqual(visible["data"]["content"], "persisted stdout")

        tool_names = [
            item["toolCall"]["toolName"]
            for item in self.store.read_session_snapshot(self.session["id"])["items"]
            if item.get("toolCall") is not None
        ]
        self.assertEqual(tool_names, ["run_shell", "read_tool_output"])

    def test_engine_preserves_read_failure_error_code_in_commit_and_context(self) -> None:
        run, _ = self.store.create_run(
            self.session["id"], "Read missing shell output"
        )
        model = ScriptedModel(
            [
                ModelResponse(
                    tool_calls=(
                        ModelToolCall(
                            "read-missing-call",
                            "read_tool_output",
                            {"callId": "missing-shell", "maxBytes": 4},
                        ),
                    )
                ),
                ModelResponse(text="No persisted shell output was found."),
            ]
        )

        with patch(
            "eidos_runtime.runtime.tool_runtime.run_shell",
            side_effect=AssertionError("read_tool_output must not run Shell"),
        ) as run_shell:
            RuntimeEngine(self.store, model, lambda _message: None).run(
                run["id"], threading.Event()
            )

        run_shell.assert_not_called()
        self.assertEqual(self.store.read_run(run["id"])["status"], "succeeded")
        read_item = self._current_read_item(self.session["id"])
        committed = json.loads(read_item["toolCall"]["resultJson"])
        self.assertEqual(committed["outcome"], "error")
        self.assertEqual(committed["code"], "tool_output_not_available")

        visible_results = [
            entry
            for entry in model.contexts[1]
            if entry.get("type") == "tool_result"
            and entry.get("name") == "read_tool_output"
        ]
        self.assertEqual(len(visible_results), 1)
        visible = json.loads(visible_results[0]["result"])
        self.assertEqual(visible["outcome"], "error")
        self.assertEqual(visible["code"], "tool_output_not_available")


if __name__ == "__main__":
    unittest.main()

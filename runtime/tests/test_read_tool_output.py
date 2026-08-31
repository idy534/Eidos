from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest

from pydantic import ValidationError


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.db.storage import SessionStore  # noqa: E402
from eidos_runtime.runtime.run_resources import RunResources  # noqa: E402
from eidos_runtime.tools.contracts import project_tool_result  # noqa: E402
from eidos_runtime.tools.read_tool_output import (  # noqa: E402
    READ_TOOL_OUTPUT_MODEL_PAGE_BYTES,
    ReadToolOutputInput,
    read_tool_output_entry,
)
from eidos_runtime.tools.workspace import canonical_tool_result  # noqa: E402


class ReadToolOutputTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-read-output-")
        root = Path(self.temporary.name)
        self.data = root / "data"
        self.workspace = root / "workspace"
        self.data.mkdir(mode=0o700)
        self.workspace.mkdir()
        self.store = SessionStore(self.data)
        self.store.initialize()
        self.session = self.store.create_session(str(self.workspace))
        self.run, _ = self.store.create_run(self.session["id"], "read output")

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _shell_item(
        self,
        provider_call_id: str,
        *,
        stdout: str = "",
        stderr: str = "",
        status: str = "completed",
        include_raw_metadata: bool = True,
    ) -> dict[str, object]:
        item = self.store.create_tool_item(
            self.run["id"], 1, 0, provider_call_id, "run_shell", "{}"
        )
        data: dict[str, object] = {
            "exitCode": 0,
            "stdout": stdout,
            "stderr": stderr,
            "termination": "exit",
            "workspaceChanged": False,
        }
        if include_raw_metadata:
            data.update({"truncated": True, "omittedBytes": 17})
        result = {
            "schemaVersion": 1,
            "toolContractVersion": 1,
            "toolName": "run_shell",
            "outcome": "success",
            "code": "ok",
            "summary": "Command completed",
            "data": data,
            "sideEffectsMayExist": False,
            "reconciliationRequired": False,
        }
        self.store.complete_tool_item(
            item["id"],
            json.dumps(result, ensure_ascii=False, separators=(",", ":")),
            item_status=status,
            tool_status=status,
        )
        return item

    def test_reads_bounded_forward_pages_and_tail(self) -> None:
        self._shell_item("shell-call", stdout="0123456789")
        entry = read_tool_output_entry(self.store, self.run["id"])

        first = entry.adapter.execute(
            {
                "callId": "shell-call",
                "stream": "stdout",
                "offsetBytes": 0,
                "maxBytes": 4,
            },
            threading.Event(),
        )
        second = entry.adapter.execute(
            {
                "callId": "shell-call",
                "stream": "stdout",
                "offsetBytes": 4,
                "maxBytes": 4,
            },
            threading.Event(),
        )
        tail = entry.adapter.execute(
            {
                "callId": "shell-call",
                "stream": "stdout",
                "maxBytes": 4,
                "fromEnd": True,
            },
            threading.Event(),
        )

        self.assertEqual(first["outcome"], "success")
        self.assertEqual(first["data"]["content"], "0123")
        self.assertEqual(first["data"]["startByte"], 0)
        self.assertEqual(first["data"]["endByte"], 4)
        self.assertEqual(first["data"]["nextOffset"], 4)
        self.assertTrue(first["data"]["hasMoreAfter"])
        self.assertEqual(second["data"]["content"], "4567")
        self.assertEqual(tail["data"]["content"], "6789")
        self.assertEqual(tail["data"]["startByte"], 6)
        self.assertTrue(tail["data"]["hasMoreBefore"])
        self.assertFalse(tail["data"]["hasMoreAfter"])

    def test_tail_adjusts_to_utf8_boundary_and_reports_actual_start(self) -> None:
        self._shell_item("utf8-call", stdout="a😀z")
        entry = read_tool_output_entry(self.store, self.run["id"])

        result = entry.adapter.execute(
            {
                "callId": "utf8-call",
                "stream": "stdout",
                "maxBytes": 4,
                "fromEnd": True,
            },
            threading.Event(),
        )

        self.assertEqual(result["outcome"], "success")
        self.assertEqual(result["data"]["content"], "z")
        self.assertEqual(result["data"]["startByte"], 5)
        self.assertEqual(result["data"]["endByte"], len("a😀z".encode()))

    def test_forward_offset_inside_utf8_character_is_rejected(self) -> None:
        self._shell_item("offset-call", stdout="abéz")
        entry = read_tool_output_entry(self.store, self.run["id"])

        result = entry.adapter.execute(
            {
                "callId": "offset-call",
                "stream": "stdout",
                "offsetBytes": 3,
                "maxBytes": 4,
            },
            threading.Event(),
        )

        self.assertEqual(result["outcome"], "error")
        self.assertEqual(result["code"], "invalid_output_offset")

    def test_missing_raw_metadata_stays_null(self) -> None:
        self._shell_item(
            "legacy-call",
            stdout="legacy",
            include_raw_metadata=False,
        )
        entry = read_tool_output_entry(self.store, self.run["id"])

        result = entry.adapter.execute(
            {
                "callId": "legacy-call",
                "stream": "stdout",
                "maxBytes": 4,
            },
            threading.Event(),
        )

        self.assertEqual(result["outcome"], "success")
        self.assertIsNone(result["data"]["rawTruncated"])
        self.assertIsNone(result["data"]["rawOmittedBytes"])

    def test_only_current_session_terminal_shell_results_are_readable(self) -> None:
        self._shell_item("failed-call", stdout="failed", status="failed")
        self._shell_item("canceled-call", stdout="canceled", status="canceled")
        self.store.create_tool_item(
            self.run["id"], 1, 0, "running-call", "run_shell", "{}"
        )
        wrong_tool = self._shell_item("wrong-tool", stdout="wrong")
        no_result = self._shell_item("no-result", stdout="missing")
        assert self.store.connection is not None
        with self.store.lock, self.store.connection as connection:
            connection.execute(
                "UPDATE tool_calls SET tool_name = 'other_tool' WHERE item_id = ?",
                (wrong_tool["id"],),
            )
            connection.execute(
                "UPDATE tool_calls SET result_json = NULL WHERE item_id = ?",
                (no_result["id"],),
            )
        self.store.fail_run(self.run["id"], "fixture_failed")

        other_workspace = Path(self.temporary.name) / "other-workspace"
        other_workspace.mkdir()
        other_session = self.store.create_session(str(other_workspace))
        other_run, _ = self.store.create_run(other_session["id"], "other")
        other_item = self.store.create_tool_item(
            other_run["id"], 1, 0, "other-call", "run_shell", "{}"
        )
        self.store.complete_tool_item(
            other_item["id"],
            json.dumps({"data": {"stdout": "other"}, "outcome": "success"}),
            item_status="completed",
            tool_status="completed",
        )
        entry = read_tool_output_entry(self.store, self.run["id"])

        failed = entry.adapter.execute(
            {"callId": "failed-call", "maxBytes": 4}, threading.Event()
        )
        canceled = entry.adapter.execute(
            {"callId": "canceled-call", "maxBytes": 4}, threading.Event()
        )
        running_result = entry.adapter.execute(
            {"callId": "running-call", "maxBytes": 4}, threading.Event()
        )
        other = entry.adapter.execute(
            {"callId": "other-call", "maxBytes": 4}, threading.Event()
        )
        wrong_tool_result = entry.adapter.execute(
            {"callId": "wrong-tool", "maxBytes": 4}, threading.Event()
        )
        no_result_result = entry.adapter.execute(
            {"callId": "no-result", "maxBytes": 4}, threading.Event()
        )

        self.assertEqual(failed["data"]["content"], "fail")
        self.assertEqual(canceled["data"]["content"], "canc")
        self.assertEqual(running_result["code"], "tool_output_not_available")
        self.assertEqual(other["code"], "tool_output_not_available")
        self.assertEqual(wrong_tool_result["code"], "tool_output_not_available")
        self.assertEqual(no_result_result["code"], "tool_output_not_available")

    def test_control_bytes_fit_model_projection_without_losing_page_offset(self) -> None:
        self._shell_item("control-call", stdout="\x00" * (16 * 1024))
        entry = read_tool_output_entry(self.store, self.run["id"])

        result = entry.adapter.execute(
            {
                "callId": "control-call",
                "stream": "stdout",
                "maxBytes": 16 * 1024,
            },
            threading.Event(),
        )

        self.assertEqual(result["outcome"], "success")
        data = result["data"]
        self.assertLessEqual(
            len(data["content"].encode("utf-8")),
            READ_TOOL_OUTPUT_MODEL_PAGE_BYTES,
        )
        self.assertTrue(data["hasMoreAfter"])
        canonical = canonical_tool_result("read_tool_output", result)
        projection = project_tool_result("read_tool_output", canonical)
        model = projection.model_result
        self.assertNotEqual(model["code"], "TOOL_RESULT_PROJECTION_FAILED")
        self.assertEqual(model["data"]["content"], data["content"])
        self.assertEqual(model["data"]["endByte"], data["endByte"])
        self.assertEqual(model["data"]["nextOffset"], data["nextOffset"])
        self.assertLessEqual(
            len(json.dumps(model, ensure_ascii=False, separators=(",", ":")).encode()),
            48 * 1024,
        )

    def test_close_and_reopen_keeps_persisted_output_readable(self) -> None:
        self._shell_item("reopen-call", stdout="persisted")
        self.store.fail_run(self.run["id"], "fixture_failed")
        self.store.close()
        self.store = SessionStore(self.data)
        self.store.initialize()

        entry = read_tool_output_entry(self.store, self.run["id"])
        result = entry.adapter.execute(
            {"callId": "reopen-call", "maxBytes": 4}, threading.Event()
        )

        self.assertEqual(result["outcome"], "success")
        self.assertEqual(result["data"]["content"], "pers")

    def test_current_session_later_run_can_read_previous_run_output(self) -> None:
        self._shell_item("previous-run-call", stdout="previous")
        self.store.fail_run(self.run["id"], "fixture_failed")
        later_run, _ = self.store.create_run(self.session["id"], "later")

        entry = read_tool_output_entry(self.store, later_run["id"])
        result = entry.adapter.execute(
            {"callId": "previous-run-call", "maxBytes": 4}, threading.Event()
        )

        self.assertEqual(result["outcome"], "success")
        self.assertEqual(result["data"]["content"], "prev")

    def test_read_does_not_clear_reconciliation_when_step_completes(self) -> None:
        self._shell_item("barrier-call", stdout="evidence")
        self.store.increment_model_step(self.run["id"])
        assert self.store.connection is not None
        self.store.connection.execute(
            """
            UPDATE runs
            SET reconciliation_required = 1, side_effects_may_exist = 1
            WHERE id = ?
            """,
            (self.run["id"],),
        )
        self.store.connection.commit()

        entry = read_tool_output_entry(self.store, self.run["id"])
        result = entry.adapter.execute(
            {"callId": "barrier-call", "maxBytes": 4}, threading.Event()
        )
        self.assertEqual(result["outcome"], "success")
        read_item = self.store.create_tool_item(
            self.run["id"], 1, 1, "read-tool-call", "read_tool_output", "{}"
        )
        self.store.complete_tool_item(
            read_item["id"],
            json.dumps(result, ensure_ascii=False, separators=(",", ":")),
        )

        self.store.complete_current_step(self.run["id"], "completed")
        state = self.store.connection.execute(
            "SELECT reconciliation_required, side_effects_may_exist FROM runs WHERE id = ?",
            (self.run["id"],),
        ).fetchone()
        self.assertEqual(tuple(state), (1, 1))

    def test_sensitive_output_is_rejected_before_return(self) -> None:
        self._shell_item("secret-call", stdout="sk-" + "x" * 16)
        entry = read_tool_output_entry(self.store, self.run["id"])

        result = entry.adapter.execute(
            {"callId": "secret-call", "maxBytes": 16}, threading.Event()
        )

        self.assertEqual(result["outcome"], "error")
        self.assertEqual(result["code"], "sensitive_content_rejected")

    def test_duplicate_call_id_fails_closed(self) -> None:
        self._shell_item("duplicate-call", stdout="first")
        self._shell_item("duplicate-call", stdout="second")
        entry = read_tool_output_entry(self.store, self.run["id"])

        result = entry.adapter.execute(
            {"callId": "duplicate-call", "maxBytes": 4}, threading.Event()
        )

        self.assertEqual(result["outcome"], "error")
        self.assertEqual(result["code"], "ambiguous_tool_call")

    def test_input_requires_call_id_and_bounded_page(self) -> None:
        with self.assertRaises(ValidationError):
            ReadToolOutputInput.model_validate({"maxBytes": 4})
        with self.assertRaises(ValidationError):
            ReadToolOutputInput.model_validate({"callId": "x", "maxBytes": 3})
        with self.assertRaises(ValidationError):
            ReadToolOutputInput.model_validate({
                "callId": "x",
                "itemId": "y",
                "maxBytes": 4,
            })

    def test_registry_runtime_accepts_failure_result_with_empty_data(self) -> None:
        entry = read_tool_output_entry(self.store, self.run["id"])
        assert entry.runtime is not None
        cancel = threading.Event()
        prepared = entry.runtime.prepare(
            None,
            {"callId": "missing-call", "maxBytes": 4},
            cancel,
        )
        raw = entry.runtime.execute(None, prepared, cancel)

        validated = entry.validate_result(raw)
        self.assertEqual(validated["outcome"], "error")
        self.assertEqual(validated["code"], "tool_output_not_available")
        self.assertEqual(validated["data"], {})

    def test_run_resources_registers_read_tool_output(self) -> None:
        with RunResources(self.store, self.run["id"], self.run["extensionSnapshot"]) as resources:
            self.assertIsNotNone(resources.registry)
            assert resources.registry is not None
            self.assertIn("read_tool_output", resources.registry.names)


if __name__ == "__main__":
    unittest.main()

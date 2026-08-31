from __future__ import annotations

import copy
import json
import unittest

from eidos_runtime.tools.contracts import project_tool_result


_MODEL_TOTAL_BYTES = 48 * 1024
_MODEL_STREAM_BYTES = 16 * 1024


def _shell_result(stdout: str, stderr: str) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "toolContractVersion": 1,
        "toolName": "run_shell",
        "outcome": "error",
        "code": "nonzero_exit",
        "summary": "Command failed after producing output",
        "data": {
            "exitCode": 7,
            "stdout": stdout,
            "stderr": stderr,
            "truncated": True,
            "truncationReason": "output_limit",
            "originalBytes": 987_654,
            "omittedBytes": 123_456,
            "termination": "exit",
            "attemptCount": 2,
            "escalated": True,
            "sandboxed": True,
            "workspaceChanged": True,
            "workspaceChangeState": "changed",
            "workspaceDiffHash": "a" * 64,
        },
        "sideEffectsMayExist": True,
        "reconciliationRequired": True,
    }


def _model_bytes(value: dict[str, object]) -> int:
    return len(json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8"))


class ShellOutputProjectionTests(unittest.TestCase):
    def test_shell_output_keeps_heads_tails_and_raw_truncation_facts(self) -> None:
        stdout = "stdout-head-" + ("o" * 20_000) + "stdout-tail-50"
        stderr = "stderr-head-" + ("e" * 21_000) + "stderr-tail-188"
        canonical = _shell_result(stdout, stderr)
        original = copy.deepcopy(canonical)

        projection = project_tool_result("run_shell", canonical)
        model = projection.model_result
        data = model["data"]
        assert isinstance(data, dict)

        self.assertLessEqual(_model_bytes(model), _MODEL_TOTAL_BYTES)
        self.assertEqual(projection.canonical_result, original)
        self.assertEqual(canonical, original)
        self.assertEqual(projection.ui_result["data"], original["data"])
        self.assertEqual(model["outcome"], "error")
        self.assertTrue(model["sideEffectsMayExist"])
        self.assertTrue(model["reconciliationRequired"])
        for key, expected in {
            "exitCode": 7,
            "termination": "exit",
            "truncated": True,
            "truncationReason": "output_limit",
            "originalBytes": 987_654,
            "omittedBytes": 123_456,
            "attemptCount": 2,
            "escalated": True,
            "sandboxed": True,
        }.items():
            self.assertEqual(data[key], expected)

        for key, head, tail in (
            ("stdout", "stdout-head-", "stdout-tail-50"),
            ("stderr", "stderr-head-", "stderr-tail-188"),
        ):
            value = data[key]
            self.assertIsInstance(value, str)
            assert isinstance(value, str)
            self.assertLessEqual(len(value.encode("utf-8")), _MODEL_STREAM_BYTES)
            self.assertGreater(len(value.encode("utf-8")), 1_024)
            self.assertTrue(value.startswith(head))
            self.assertTrue(value.endswith(tail))
            self.assertIn("[... model output omitted ...]", value)

        marker = "\n[... model output omitted ...]\n"
        retained_output_bytes = sum(
            len(str(data[key]).replace(marker, "").encode("utf-8"))
            for key in ("stdout", "stderr")
        )
        raw_output_bytes = len(stdout.encode("utf-8")) + len(stderr.encode("utf-8"))
        self.assertTrue(data["modelProjectionTruncated"])
        self.assertEqual(
            data["modelProjectionOmittedBytes"],
            raw_output_bytes - retained_output_bytes,
        )

    def test_shell_projection_keeps_facts_when_escaped_fields_are_large(self) -> None:
        escaped = '\\"\\\\\\n\\t' * 10_000
        canonical = _shell_result(
            "stdout-head-" + ("界\\\"\\\\\\n\\t" * 6_000) + "stdout-tail-50",
            "stderr-head-" + ("层\\\"\\\\\\n\\t" * 6_000) + "stderr-tail-188",
        )
        canonical["summary"] = escaped
        data = canonical["data"]
        assert isinstance(data, dict)
        data["effectivePermissionsSummary"] = {
            "escaped": escaped,
            "nested": {"escaped": escaped},
        }
        data["skillInvocation"] = {
            "skillQualifiedId": "user:review",
            "invocationType": "implicit",
            "source": escaped,
            "provenance": {"locator": escaped},
        }
        original = copy.deepcopy(canonical)

        projection = project_tool_result("run_shell", canonical)
        model = projection.model_result
        projected_data = model["data"]
        assert isinstance(projected_data, dict)

        self.assertLessEqual(_model_bytes(model), _MODEL_TOTAL_BYTES)
        self.assertEqual(canonical, original)
        self.assertEqual(model["outcome"], "error")
        self.assertTrue(model["sideEffectsMayExist"])
        self.assertTrue(model["reconciliationRequired"])
        for key in (
            "exitCode", "termination", "truncated", "truncationReason",
            "originalBytes", "omittedBytes", "attemptCount", "escalated",
            "sandboxed",
        ):
            self.assertIn(key, projected_data)
        self.assertTrue(projected_data["modelProjectionTruncated"])
        self.assertIsInstance(projected_data["modelProjectionOmittedBytes"], int)
        self.assertEqual(model["toolName"], "run_shell")
        self.assertEqual(model["code"], "nonzero_exit")
        for key, head, tail in (
            ("stdout", "stdout-head-", "stdout-tail-50"),
            ("stderr", "stderr-head-", "stderr-tail-188"),
        ):
            value = projected_data[key]
            self.assertIsInstance(value, str)
            assert isinstance(value, str)
            self.assertLessEqual(len(value.encode("utf-8")), _MODEL_STREAM_BYTES)
            self.assertGreater(len(value.encode("utf-8")), 1_024)
            self.assertTrue(value.startswith(head))
            self.assertTrue(value.endswith(tail))
            self.assertIn("[... model output omitted ...]", value)

        marker = "\n[... model output omitted ...]\n"
        retained_output_bytes = sum(
            len(str(projected_data[key]).replace(marker, "").encode("utf-8"))
            for key in ("stdout", "stderr")
        )
        raw_output_bytes = sum(
            len(value.encode("utf-8"))
            for value in (
                "stdout-head-" + ("界\\\"\\\\\\n\\t" * 6_000) + "stdout-tail-50",
                "stderr-head-" + ("层\\\"\\\\\\n\\t" * 6_000) + "stderr-tail-188",
            )
        )
        self.assertEqual(
            projected_data["modelProjectionOmittedBytes"],
            raw_output_bytes - retained_output_bytes,
        )

    def test_shell_projection_separates_raw_and_model_truncation(self) -> None:
        stdout = "stdout-head-" + ("界\\\"\\\\\\n\\t" * 6_000) + "stdout-tail-50"
        stderr = "stderr-head-" + ("层\\\"\\\\\\n\\t" * 6_000) + "stderr-tail-188"
        canonical = _shell_result(stdout, stderr)
        data = canonical["data"]
        assert isinstance(data, dict)
        data["truncated"] = False
        data["omittedBytes"] = 0
        data["originalBytes"] = len(stdout.encode("utf-8")) + len(stderr.encode("utf-8"))

        projection = project_tool_result("run_shell", canonical)
        projected_data = projection.model_result["data"]
        assert isinstance(projected_data, dict)

        self.assertFalse(projected_data["truncated"])
        self.assertEqual(projected_data["omittedBytes"], 0)
        self.assertTrue(projected_data["modelProjectionTruncated"])
        retained_output_bytes = sum(
            len(str(projected_data[key]).replace(
                "\n[... model output omitted ...]\n", ""
            ).encode("utf-8"))
            for key in ("stdout", "stderr")
        )
        self.assertEqual(
            projected_data["modelProjectionOmittedBytes"],
            len(stdout.encode("utf-8"))
            + len(stderr.encode("utf-8"))
            - retained_output_bytes,
        )
        for key, tail in (
            ("stdout", "stdout-tail-50"),
            ("stderr", "stderr-tail-188"),
        ):
            value = projected_data[key]
            self.assertIsInstance(value, str)
            assert isinstance(value, str)
            self.assertTrue(value.endswith(tail))

    def test_short_shell_output_is_exact_without_projection_truncation(self) -> None:
        canonical = _shell_result("short stdout", "short stderr")
        data = canonical["data"]
        assert isinstance(data, dict)
        data["truncated"] = False
        data["omittedBytes"] = 0
        original = copy.deepcopy(canonical)

        projection = project_tool_result("run_shell", canonical)
        projected_data = projection.model_result["data"]
        assert isinstance(projected_data, dict)

        self.assertEqual(projected_data["stdout"], "short stdout")
        self.assertEqual(projected_data["stderr"], "short stderr")
        self.assertFalse(projected_data["truncated"])
        self.assertEqual(projected_data["omittedBytes"], 0)
        self.assertNotIn("modelProjectionTruncated", projected_data)
        self.assertNotIn("modelProjectionOmittedBytes", projected_data)
        self.assertNotIn("modelProjectionContinuation", projected_data)
        self.assertEqual(canonical, original)

    def test_shell_projection_continuation_reads_existing_output(self) -> None:
        canonical = _shell_result("x" * 40_000, "short")

        projection = project_tool_result("run_shell", canonical)
        data = projection.model_result["data"]
        assert isinstance(data, dict)
        continuation = data["modelProjectionContinuation"]
        self.assertIn("read_tool_output", continuation)
        self.assertIn("callId=", continuation)
        self.assertIn("stream=stdout", continuation)
        self.assertIn("fromEnd=true", continuation)
        self.assertIn("existing", continuation.lower())
        self.assertIn("not rerun", continuation.lower())

    def test_non_shell_projection_keeps_original_truncation_contract(self) -> None:
        canonical = {
            "schemaVersion": 1,
            "toolContractVersion": 1,
            "toolName": "read_file",
            "outcome": "success",
            "code": "ok",
            "summary": "Read file",
            "data": {
                "path": "src/output.txt",
                "content": "x" * 300_000,
                "truncated": False,
            },
            "sideEffectsMayExist": False,
            "reconciliationRequired": False,
        }

        projection = project_tool_result("read_file", canonical)
        data = projection.model_result["data"]
        assert isinstance(data, dict)
        self.assertTrue(data["truncated"])
        self.assertIn("continuation", data)
        self.assertNotIn("modelProjectionTruncated", data)
        self.assertNotIn("modelProjectionContinuation", data)


if __name__ == "__main__":
    unittest.main()

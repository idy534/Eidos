from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

from pydantic import ValidationError


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.tools.contracts import RunShellInput  # noqa: E402
from eidos_runtime.tools.workspace import (  # noqa: E402
    TOOL_SPECS,
    canonical_tool_result,
)


class ToolContractTests(unittest.TestCase):
    def test_registry_and_cross_language_vectors_are_canonical(self) -> None:
        self.assertEqual(len({spec.name for spec in TOOL_SPECS}), len(TOOL_SPECS))
        fixture = json.loads(
            (RUNTIME_ROOT.parent / "protocol" / "fixtures" / "tool-results-v1.json")
            .read_text(encoding="utf-8")
        )
        for vector in fixture["vectors"]:
            result = vector["result"]
            self.assertEqual(canonical_tool_result(result["toolName"], result), result)
            self.assertEqual(
                json.dumps(result, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                vector["canonicalJson"],
            )

    def test_result_rejects_integers_outside_the_json_safe_range(self) -> None:
        with self.assertRaises(ValueError):
            canonical_tool_result("read_file", {
                "outcome": "success", "code": "ok", "summary": "Read file",
                "data": {"sizeBytes": 9_007_199_254_740_992},
                "sideEffectsMayExist": False,
            })

    def test_successful_shell_result_can_report_side_effects(self) -> None:
        result = canonical_tool_result("run_shell", {
            "outcome": "success",
            "code": "ok",
            "summary": "Command completed",
            "data": {
                "exitCode": 0,
                "stdout": "",
                "stderr": "",
                "truncated": False,
                "termination": "exit",
                "workspaceChanged": True,
            },
            "sideEffectsMayExist": True,
        })

        self.assertTrue(result["sideEffectsMayExist"])

    def test_successful_shell_result_can_require_reconciliation(self) -> None:
        result = canonical_tool_result("run_shell", {
            "outcome": "success",
            "code": "ok",
            "summary": "Command completed",
            "data": {
                "exitCode": 0,
                "stdout": "",
                "stderr": "",
                "truncated": False,
                "termination": "exit",
                "workspaceChanged": False,
                "workspaceChangeState": "unknown",
            },
            "sideEffectsMayExist": True,
            "reconciliationRequired": True,
        })

        self.assertTrue(result["reconciliationRequired"])

    def test_shell_permission_contract_is_closed_and_backwards_compatible(self) -> None:
        default = RunShellInput.model_validate({"command": "true"})
        expanded = RunShellInput.model_validate_json(json.dumps({
            "command": "make",
            "sandboxPermissions": "with_additional_permissions",
            "additionalPermissions": {
                "fileSystem": [{
                    "path": "/private/tmp/sdk",
                    "access": "read",
                    "recursive": True,
                }],
                "network": {"enabled": True},
            },
            "justification": "Use the approved SDK",
        }))

        self.assertEqual(default.sandboxPermissions.value, "use_default")
        self.assertIsNone(default.additionalPermissions)
        self.assertEqual(
            expanded.model_dump(mode="json", by_alias=True)[
                "additionalPermissions"
            ]["fileSystem"][0]["access"],
            "read",
        )
        with self.assertRaises(ValidationError):
            RunShellInput.model_validate_json(json.dumps({
                "command": "make",
                "sandboxPermissions": "require_escalated",
            }))
        with self.assertRaises(ValidationError):
            RunShellInput.model_validate_json(json.dumps({
                "command": "make",
                "sandboxPermissions": "use_default",
                "additionalPermissions": {"network": {"enabled": True}},
                "justification": "invalid",
            }))
        with self.assertRaises(ValidationError):
            RunShellInput.model_validate_json(json.dumps({
                "command": "make",
                "unknownPermission": True,
            }))

    def test_shell_tool_tells_model_how_to_request_network_access(self) -> None:
        description = next(
            spec.description for spec in TOOL_SPECS if spec.name == "run_shell"
        )

        self.assertIn("with_additional_permissions", description)
        self.assertIn("additionalPermissions.network.enabled", description)
        self.assertNotIn("network-disabled", description)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

from pydantic import ValidationError


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.tools.contracts import (  # noqa: E402
    ListFilesInput,
    NetworkAccess,
    RunShellInput,
    SearchTextInput,
)
from eidos_runtime.tools.workspace import (  # noqa: E402
    TOOL_SPECS,
    canonical_tool_result,
)
from eidos_runtime.tools.contracts import project_tool_result  # noqa: E402


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

    def test_shell_skill_invocation_metadata_is_canonical_and_projected(self) -> None:
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
                "skillInvocation": {
                    "skillQualifiedId": "user:review",
                    "invocationType": "implicit",
                    "source": "eidos-user",
                    "provenance": {
                        "version": "local",
                        "hash": "source-hash",
                        "locator": "file:///tmp/review/SKILL.md",
                    },
                },
            },
            "sideEffectsMayExist": False,
        })

        self.assertEqual(
            result["data"]["skillInvocation"]["skillQualifiedId"],
            "user:review",
        )
        projection = project_tool_result("run_shell", result)
        self.assertEqual(
            projection.model_result["data"]["skillInvocation"]["invocationType"],
            "implicit",
        )

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

    def test_shell_diagnostics_are_canonical_and_projected(self) -> None:
        result = canonical_tool_result("run_shell", {
            "outcome": "error",
            "code": "nonzero_exit",
            "summary": "Command did not succeed (exit code 7)",
            "data": {
                "exitCode": 7,
                "stdout": "",
                "stderr": "",
                "truncated": True,
                "truncationReason": "output_limit",
                "originalBytes": 300_000,
                "omittedBytes": 37_856,
                "termination": "exit",
                "durationMs": 1,
                "shellKind": "zsh",
                "environmentSource": "captured",
            },
            "sideEffectsMayExist": True,
            "reconciliationRequired": True,
        })

        projection = project_tool_result("run_shell", result)
        for projected in (
            result,
            projection.model_result,
            projection.ui_result,
        ):
            self.assertEqual(projected["data"]["truncationReason"], "output_limit")
            self.assertEqual(projected["data"]["originalBytes"], 300_000)
            self.assertEqual(projected["data"]["omittedBytes"], 37_856)
            self.assertEqual(projected["data"]["shellKind"], "zsh")
            self.assertEqual(projected["data"]["environmentSource"], "captured")

    def test_shell_output_byte_counts_are_bounded_by_contract(self) -> None:
        with self.assertRaises(ValidationError):
            canonical_tool_result("run_shell", {
                "outcome": "error",
                "code": "nonzero_exit",
                "summary": "Command did not succeed (exit code 7)",
                "data": {
                    "exitCode": 7,
                    "stdout": "",
                    "stderr": "",
                    "truncated": True,
                    "truncationReason": "output_limit",
                    "originalBytes": 9_007_199_254_740_992,
                    "omittedBytes": 0,
                    "termination": "exit",
                },
                "sideEffectsMayExist": True,
                "reconciliationRequired": True,
            })

    def test_shell_permission_contract_is_closed_and_backwards_compatible(self) -> None:
        default = RunShellInput.model_validate({"command": "true"})
        network_request = RunShellInput.model_validate_json(json.dumps({
            "command": "npm install",
            "networkAccess": "request",
            "justification": "Install project dependencies",
        }))
        empty_permission_requests = (
            RunShellInput.model_validate_json(json.dumps({
                "command": "npm install",
                "networkAccess": "request",
                "additionalPermissions": {},
                "justification": "Install project dependencies",
            })),
            RunShellInput.model_validate_json(json.dumps({
                "command": "npm install",
                "networkAccess": "request",
                "additionalPermissions": {
                    "network": {"enabled": None},
                },
                "justification": "Install project dependencies",
            })),
        )
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
        self.assertIs(default.networkAccess, NetworkAccess.DEFAULT)
        self.assertIs(network_request.networkAccess, NetworkAccess.REQUEST)
        self.assertEqual(
            network_request.effective_sandbox_permissions.value,
            "with_additional_permissions",
        )
        self.assertIsNotNone(network_request.effective_additional_permissions)
        assert network_request.effective_additional_permissions is not None
        assert network_request.effective_additional_permissions.network is not None
        self.assertTrue(network_request.effective_additional_permissions.network.enabled)
        for empty_request in empty_permission_requests:
            self.assertEqual(
                empty_request.effective_sandbox_permissions,
                network_request.effective_sandbox_permissions,
            )
            self.assertTrue(
                empty_request.effective_additional_permissions is not None
            )
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

        with self.assertRaisesRegex(
            ValidationError, "network_access_justification_required"
        ):
            RunShellInput.model_validate_json(json.dumps({
                "command": "npm install",
                "networkAccess": "request",
            }))
        with self.assertRaisesRegex(ValidationError, "network_access_conflict"):
            RunShellInput.model_validate_json(json.dumps({
                "command": "npm install",
                "networkAccess": "request",
                "sandboxPermissions": "with_additional_permissions",
                "additionalPermissions": {"network": {"enabled": True}},
                "justification": "Install project dependencies",
            }))

        normalized = network_request.model_dump(mode="json", by_alias=True)
        reparsed = RunShellInput.model_validate_json(json.dumps(normalized))
        self.assertEqual(reparsed, network_request)

    def test_shell_tool_tells_model_how_to_request_network_access(self) -> None:
        description = next(
            spec.description for spec in TOOL_SPECS if spec.name == "run_shell"
        )

        self.assertIn("networkAccess=request", description)
        self.assertIn("justification", description)
        self.assertIn("macOS Seatbelt", description)
        self.assertIn("sandboxPermissions", description)
        self.assertIn("timeoutSeconds", description)
        self.assertIn("external timeout", description)
        self.assertIn("pipefail", description)
        self.assertIn("glob", description)
        self.assertNotIn("network-disabled", description)

    def test_discovery_contracts_describe_and_validate_scopes(self) -> None:
        list_default = ListFilesInput.model_validate({})
        search_default = SearchTextInput.model_validate({"query": "ConfigBuilder"})

        self.assertEqual(list_default.path, ".")
        self.assertEqual(list_default.maxDepth, 5)
        self.assertEqual(list_default.maxEntries, 2_000)
        self.assertEqual(search_default.path, ".")
        self.assertEqual(search_default.maxResults, 100)
        self.assertFalse(search_default.regex)
        self.assertEqual(search_default.includeGlobs, ())

        list_spec = next(spec for spec in TOOL_SPECS if spec.name == "list_files")
        search_spec = next(spec for spec in TOOL_SPECS if spec.name == "search_text")
        self.assertIn("path", list_spec.description)
        self.assertIn("maxDepth", list_spec.description)
        self.assertIn("maxEntries", list_spec.description)
        self.assertIn("path", search_spec.description)
        self.assertIn("maxResults", search_spec.description)
        self.assertIn("regex", search_spec.description)
        self.assertIn("includeGlobs", search_spec.description)
        self.assertEqual(
            set(list_spec.input_schema["properties"]),
            {"path", "maxDepth", "maxEntries"},
        )
        self.assertEqual(
            set(search_spec.input_schema["properties"]),
            {"query", "path", "regex", "includeGlobs", "maxResults"},
        )


if __name__ == "__main__":
    unittest.main()

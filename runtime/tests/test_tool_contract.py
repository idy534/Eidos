from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

from pydantic import ValidationError


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.tools.contracts import (  # noqa: E402
    GitWriteAccess,
    ListFilesInput,
    NetworkAccess,
    RunShellInput,
    SearchTextInput,
    WriteStdinInput,
)
from eidos_runtime.tools.workspace import (  # noqa: E402
    TOOL_SPECS,
    canonical_tool_result,
    model_tool_definitions,
)
from eidos_runtime.tools.contracts import project_tool_result  # noqa: E402
from eidos_runtime.tools.registry import ToolSpec  # noqa: E402


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

    def test_workspace_dependencies_model_projection_keeps_only_code_and_data(self) -> None:
        result = canonical_tool_result("workspace_dependencies", {
            "outcome": "success",
            "code": "ok",
            "summary": "Verified workspace dependencies are available",
            "data": {"source": "eidos_runtime"},
            "sideEffectsMayExist": False,
        })

        projection = project_tool_result("workspace_dependencies", result)

        self.assertEqual(
            projection.model_result,
            {"code": "ok", "data": {"source": "eidos_runtime"}},
        )
        self.assertEqual(projection.canonical_result, result)
        self.assertEqual(projection.ui_result["outcome"], "success")

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
        git_network_request = RunShellInput.model_validate_json(json.dumps({
            "command": "git push -u origin HEAD",
            "gitWriteAccess": "request",
            "networkAccess": "request",
            "justification": "Push the current branch",
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
        self.assertIs(default.gitWriteAccess, GitWriteAccess.DEFAULT)
        self.assertIs(network_request.networkAccess, NetworkAccess.REQUEST)
        self.assertIs(
            git_network_request.gitWriteAccess,
            GitWriteAccess.REQUEST,
        )
        self.assertEqual(
            network_request.effective_sandbox_permissions.value,
            "with_additional_permissions",
        )
        self.assertIsNotNone(network_request.effective_additional_permissions)
        assert network_request.effective_additional_permissions is not None
        assert network_request.effective_additional_permissions.network is not None
        self.assertTrue(network_request.effective_additional_permissions.network.enabled)
        self.assertEqual(
            git_network_request.effective_sandbox_permissions.value,
            "with_additional_permissions",
        )
        assert git_network_request.effective_additional_permissions is not None
        assert git_network_request.effective_additional_permissions.network is not None
        self.assertTrue(
            git_network_request.effective_additional_permissions.network.enabled
        )
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
        with self.assertRaisesRegex(
            ValidationError,
            "git_write_access_justification_required",
        ):
            RunShellInput.model_validate_json(json.dumps({
                "command": "git add .",
                "gitWriteAccess": "request",
            }))

        normalized = network_request.model_dump(mode="json", by_alias=True)
        reparsed = RunShellInput.model_validate_json(json.dumps(normalized))
        self.assertEqual(reparsed, network_request)

    def test_run_shell_uses_a_bounded_yield_window(self) -> None:
        schema = RunShellInput.model_json_schema(by_alias=True)
        properties = schema["properties"]

        self.assertNotIn("timeoutSeconds", properties)
        self.assertIn("yieldTimeMs", properties)
        self.assertEqual(
            RunShellInput.model_validate({"command": "true"}).yieldTimeMs,
            10_000,
        )
        self.assertEqual(properties["yieldTimeMs"]["minimum"], 250)
        self.assertEqual(properties["yieldTimeMs"]["maximum"], 30_000)
        for value in (249, 30_001):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                RunShellInput.model_validate({"command": "true", "yieldTimeMs": value})

    def test_shell_observation_budget_preserves_legacy_specs(self) -> None:
        run_shell = next(spec for spec in TOOL_SPECS if spec.name == "run_shell")

        self.assertEqual(run_shell.timeout_seconds, 600)
        self.assertEqual(
            ToolSpec.model_validate(
                run_shell.model_dump(mode="json", by_alias=True)
                | {"timeoutSeconds": 3600}
            ).timeout_seconds,
            3600,
        )

    def test_write_stdin_is_model_visible(self) -> None:
        self.assertIn("write_stdin", {spec.name for spec in TOOL_SPECS})
        self.assertIn(
            "write_stdin",
            {
                definition["function"]["name"]
                for definition in model_tool_definitions()
            },
        )

    def test_shell_tool_tells_model_how_to_request_network_access(self) -> None:
        description = next(
            spec.description for spec in TOOL_SPECS if spec.name == "run_shell"
        )

        self.assertEqual(
            description,
            "Run a shell command in the macOS workspace sandbox. "
            "Returns command output, or a sessionId if the command is still running.",
        )
        for fragment in (
            "gitWriteAccess",
            "networkAccess",
            "justification",
            "request_permissions",
            "additionalPermissions",
            "sandboxPermissions",
            "write_stdin",
            "shell_running",
            "yieldTimeMs",
            "pipefail",
            "glob",
            ".git",
            "Seatbelt",
            "timeoutSeconds",
        ):
            self.assertNotIn(fragment, description)

        properties = RunShellInput.model_json_schema(by_alias=True)["properties"]
        self.assertEqual(
            properties["gitWriteAccess"]["description"],
            'Set to "request" when the command requires writing Git metadata '
            "for the current repository.",
        )
        self.assertEqual(
            properties["networkAccess"]["description"],
            'Set to "request" when the command requires network access.',
        )
        self.assertEqual(
            properties["justification"]["description"],
            "Short user-facing reason for the requested permission.",
        )
        self.assertIn("sessionId", properties["yieldTimeMs"]["description"])

        stdin_description = next(
            spec.description for spec in TOOL_SPECS if spec.name == "write_stdin"
        )
        self.assertEqual(
            stdin_description,
            "Write input to a running shell session or wait for additional "
            "output. Use empty input to poll for output.",
        )
        stdin_properties = WriteStdinInput.model_json_schema(by_alias=True)[
            "properties"
        ]
        self.assertIn("run_shell", stdin_properties["sessionId"]["description"])
        self.assertIn("poll", stdin_properties["chars"]["description"])
        self.assertIn("wait", stdin_properties["yieldTimeMs"]["description"].lower())

    def test_discovery_contracts_describe_and_validate_scopes(self) -> None:
        list_default = ListFilesInput.model_validate({})
        search_default = SearchTextInput.model_validate({"query": "ConfigBuilder"})

        self.assertEqual(list_default.path, ".")
        self.assertEqual(list_default.maxDepth, 5)
        self.assertEqual(list_default.maxEntries, 2_000)
        self.assertEqual(search_default.path, ".")
        self.assertEqual(search_default.maxResults, 100)
        self.assertEqual(search_default.yieldTimeMs, 10_000)
        self.assertFalse(search_default.regex)
        self.assertEqual(search_default.includeGlobs, ())

        list_spec = next(spec for spec in TOOL_SPECS if spec.name == "list_files")
        read_spec = next(spec for spec in TOOL_SPECS if spec.name == "read_file")
        search_spec = next(spec for spec in TOOL_SPECS if spec.name == "search_text")
        self.assertIn("path", list_spec.description)
        self.assertIn("maxDepth", list_spec.description)
        self.assertIn("maxEntries", list_spec.description)
        self.assertIn("path", search_spec.description)
        self.assertIn("workspace-relative", read_spec.description)
        self.assertIn("skill_read_resource", read_spec.description)
        self.assertIn("maxResults", search_spec.description)
        self.assertIn("regex", search_spec.description)
        self.assertIn("includeGlobs", search_spec.description)
        self.assertIn("yieldTimeMs", search_spec.description)
        self.assertIn("search_text_wait", {spec.name for spec in TOOL_SPECS})
        self.assertEqual(
            set(list_spec.input_schema["properties"]),
            {"path", "maxDepth", "maxEntries"},
        )
        self.assertEqual(
            set(search_spec.input_schema["properties"]),
            {
                "query",
                "path",
                "regex",
                "includeGlobs",
                "maxResults",
                "yieldTimeMs",
            },
        )


if __name__ == "__main__":
    unittest.main()

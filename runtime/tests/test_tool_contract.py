from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

from pydantic import ValidationError


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.tools.contracts import (  # noqa: E402
    ApplyPatchInput,
    GitWriteAccess,
    ListFilesInput,
    NetworkAccess,
    ReadFileRangeInput,
    RunShellInput,
    SearchTextInput,
    SearchTextWaitInput,
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
        ignored_permission = RunShellInput.model_validate_json(json.dumps({
            "command": "make",
            "unknownPermission": True,
        }))
        self.assertEqual(ignored_permission.sandboxPermissions.value, "use_default")
        self.assertNotIn("unknownPermission", ignored_permission.model_dump())

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

        self.assertEqual(schema["required"], ["command"])
        self.assertFalse(schema["additionalProperties"])
        self.assertNotIn("timeoutSeconds", properties)
        self.assertIn("yieldTimeMs", properties)
        self.assertNotIn("yield_time_ms", properties)
        self.assertEqual(
            RunShellInput.model_validate({"command": "true"}).yieldTimeMs,
            10_000,
        )
        self.assertEqual(properties["yieldTimeMs"]["minimum"], 250)
        self.assertEqual(properties["yieldTimeMs"]["maximum"], 30_000)
        for value in (0, 1, 249):
            with self.subTest(value=value):
                self.assertEqual(
                    RunShellInput.model_validate({"command": "true", "yieldTimeMs": value}).yieldTimeMs,
                    250,
                )

    def test_run_shell_accepts_yield_alias_with_existing_limits(self) -> None:
        for name in ("yieldTimeMs", "yield_time_ms"):
            for value in (250, 10_000, 30_000):
                with self.subTest(name=name, value=value):
                    arguments = RunShellInput.model_validate_json(json.dumps({
                        "command": "true", name: value,
                    })).model_dump(mode="json", by_alias=True)
                    self.assertEqual(arguments["yieldTimeMs"], value)
                    self.assertNotIn("yield_time_ms", arguments)
            for value in (-1, True, "1000", 1000.5, None):
                with self.subTest(name=name, value=value), self.assertRaises(ValidationError):
                    RunShellInput.model_validate_json(json.dumps({
                        "command": "true", name: value,
                    }))

    def test_polling_waits_cap_large_integers_without_relaxing_input_types(self) -> None:
        for contract, arguments, limit in (
            (RunShellInput, {"command": "true"}, 30_000),
            (SearchTextInput, {"query": "test"}, 30_000),
            (WriteStdinInput, {"sessionId": "shell"}, 60_000),
            (SearchTextWaitInput, {"sessionId": "search"}, 60_000),
        ):
            for value in (limit + 1, 120_000):
                with self.subTest(contract=contract.__name__, value=value):
                    self.assertEqual(contract.model_validate_json(json.dumps({
                        **arguments, "yieldTimeMs": value,
                    })).yieldTimeMs, limit)
            for invalid in (True, "120000", 120000.5, None, -1):
                with self.subTest(contract=contract.__name__, invalid=invalid), self.assertRaises(ValidationError):
                    contract.model_validate_json(json.dumps({**arguments, "yieldTimeMs": invalid}))
            if contract in (SearchTextInput, SearchTextWaitInput):
                with self.assertRaises(ValidationError):
                    contract.model_validate_json(json.dumps({**arguments, "yieldTimeMs": 249}))
        self.assertEqual(RunShellInput.model_validate_json(json.dumps({
            "command": "true", "yield_time_ms": 120_000,
        })).yieldTimeMs, 30_000)

    def test_run_shell_json_prefers_canonical_yield_and_ignores_unknown_fields(self) -> None:
        arguments = RunShellInput.model_validate_json(json.dumps({
            "command": "true", "yieldTimeMs": 1000, "yield_time_ms": 2000,
        }))
        self.assertEqual(arguments.yieldTimeMs, 1000)
        ignored = RunShellInput.model_validate_json(json.dumps({
            "command": "true", "yield_time_ms": 1000, "unknown": True,
        }))
        self.assertEqual(ignored.yieldTimeMs, 1000)
        self.assertNotIn("unknown", ignored.model_dump())

    def test_run_shell_ignores_only_top_level_extra_fields(self) -> None:
        arguments = {
            "command": "true", "shell": "bash", "yield_timeMs": -1,
            "sandbox_permissions": "require_escalated",
        }
        parsed = RunShellInput.model_validate_json(json.dumps(arguments))
        self.assertEqual(parsed, RunShellInput(command="true"))
        self.assertEqual(
            RunShellInput.ignored_argument_names(arguments),
            ("sandbox_permissions", "shell", "yield_timeMs"),
        )
        self.assertEqual(RunShellInput.ignored_argument_names({
            "command": "true", "yield_time_ms": 1000,
        }), ())
        for invalid in (
            {"shell": "bash"},
            {"command": 1, "shell": "bash"},
            {"command": "true", "cwd": False, "shell": "bash"},
            {"command": "true", "yieldTimeMs": "1000", "shell": "bash"},
            {"command": "true", "sandboxPermissions": "unknown", "shell": "bash"},
            {"command": "true", "additionalPermissions": {"unknown": True}},
            {"command": "true", "additionalPermissions": {"network": {"unknown": True}}},
        ):
            with self.subTest(arguments=invalid), self.assertRaises(ValidationError):
                RunShellInput.model_validate_json(json.dumps(invalid))

        with self.assertRaises(ValidationError):
            WriteStdinInput.model_validate_json(json.dumps({
                "sessionId": "shell-1", "unknown": True,
            }))

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

        self.assertTrue(description.startswith(
            "Run a command in a separate shell process. "
            "The working directory defaults to the current Run's workspace. "
            "Set cwd to start in another directory. "
            "Return output or a sessionId for ongoing interaction.",
        ))
        self.assertIn("tty=true", description)
        self.assertIn("control_shell", description)
        for fragment in (
            "gitWriteAccess",
            "networkAccess",
            "justification",
            "request_permissions",
            "additionalPermissions",
            "sandboxPermissions",
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
        self.assertEqual(properties["cwd"]["default"], ".")
        self.assertEqual(
            properties["cwd"]["description"],
            "Starting directory. Defaults to the current Run's workspace when omitted or set to '.'. "
            "Relative paths start at that workspace root; canonical absolute paths follow the current Run permissions.",
        )
        model_shell = next(
            definition["function"] for definition in model_tool_definitions()
            if definition["function"]["name"] == "run_shell"
        )
        self.assertEqual(model_shell["description"], description)
        self.assertEqual(model_shell["parameters"]["properties"]["cwd"], properties["cwd"])
        self.assertEqual(
            properties["gitWriteAccess"]["description"],
            'Set to "request" for Git metadata write permission in the repository containing cwd. '
            'Set cwd to the target repository or its subdirectory and include justification. '
            'In full_access use "default".',
        )
        self.assertEqual(
            properties["networkAccess"]["description"],
            'Set to "request" only when the current Run lacks network access. '
            'In full_access use "default".',
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
            "Write input to a running shell session and return recent output.",
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

        descriptions = {spec.name: spec.description for spec in TOOL_SPECS}
        self.assertEqual(
            descriptions["list_files"],
            "List regular files under a workspace or active Skill directory.",
        )
        self.assertEqual(
            descriptions["read_file"],
            "Read a UTF-8 text file from the workspace or an active Skill root. "
            "For truncated results, continue with read_file_range.",
        )
        self.assertEqual(
            descriptions["read_file_range"],
            "Read an inclusive line range from a UTF-8 text file.",
        )
        self.assertEqual(
            descriptions["search_text"],
            "Search text across workspace or active Skill files.",
        )
        self.assertEqual(
            descriptions["search_text_wait"],
            "Wait for additional results from a running search_text session.",
        )
        self.assertIn("*** Begin Patch", descriptions["apply_patch"])
        self.assertIn("`patch`", descriptions["apply_patch"])
        self.assertIn("*** Add File: path", descriptions["apply_patch"])
        self.assertIn("*** Update File: path", descriptions["apply_patch"])
        self.assertIn("*** Delete File: path", descriptions["apply_patch"])
        self.assertIn("@@", descriptions["apply_patch"])
        self.assertIn("*** Move to: path", descriptions["apply_patch"])
        self.assertIn("*** Update File: app.py", descriptions["apply_patch"])
        for name in (
            "list_files",
            "read_file",
            "read_file_range",
            "search_text",
            "search_text_wait",
        ):
            for fragment in (
                "bounded",
                "skill_read_resource",
                "search_running",
                "Ripgrep",
                "wait window",
            ):
                self.assertNotIn(fragment, descriptions[name])

        list_properties = ListFilesInput.model_json_schema(by_alias=True)[
            "properties"
        ]
        self.assertEqual(
            list_properties["path"]["description"],
            'Directory to list. Defaults to ".".',
        )
        self.assertEqual(
            list_properties["maxDepth"]["description"],
            "Maximum directory depth to traverse.",
        )
        self.assertEqual(
            list_properties["maxEntries"]["description"],
            "Maximum number of entries to return.",
        )
        range_properties = ReadFileRangeInput.model_json_schema(by_alias=True)[
            "properties"
        ]
        self.assertEqual(
            range_properties["startLine"]["description"],
            "First line to read, one-based.",
        )
        self.assertEqual(
            range_properties["endLine"]["description"],
            "Last line to read, inclusive.",
        )
        search_properties = SearchTextInput.model_json_schema(by_alias=True)[
            "properties"
        ]
        self.assertIn("regular expression", search_properties["query"]["description"])
        wait_properties = SearchTextWaitInput.model_json_schema(by_alias=True)[
            "properties"
        ]
        self.assertEqual(
            wait_properties["sessionId"]["description"],
            "Running search session returned by search_text.",
        )
        self.assertEqual(
            wait_properties["yieldTimeMs"]["description"],
            "How long to wait for additional search results. Values above 60000 are capped; the search continues.",
        )
        patch_properties = ApplyPatchInput.model_json_schema(by_alias=True)[
            "properties"
        ]
        self.assertIn("*** Begin Patch", patch_properties["patch"]["description"])
        self.assertIn("Markdown fences", patch_properties["patch"]["description"])
        self.assertIn("search_text_wait", {spec.name for spec in TOOL_SPECS})
        specs = {spec.name: spec for spec in TOOL_SPECS}
        self.assertEqual(
            set(specs["list_files"].input_schema["properties"]),
            {"path", "maxDepth", "maxEntries"},
        )
        self.assertEqual(
            set(specs["search_text"].input_schema["properties"]),
            {
                "query",
                "path",
                "regex",
                "includeGlobs",
                "maxResults",
                "yieldTimeMs",
            },
        )


    def test_truncated_read_file_points_model_at_read_file_range(self) -> None:
        result = canonical_tool_result("read_file", {
            "outcome": "success",
            "code": "ok",
            "summary": "Read file",
            "data": {
                "path": "big.txt",
                "content": "head\n[...truncated...]\ntail\n",
                "sizeBytes": 999_999,
                "sha256": "a" * 64,
                "truncated": True,
                "truncationReason": "head_tail",
            },
            "sideEffectsMayExist": False,
        })
        projection = project_tool_result("read_file", result)

        # The truncated result itself carries no next-step pointer, so the
        # description must name the continuation tool.
        self.assertTrue(projection.model_result["data"]["truncated"])
        description = next(
            spec.description for spec in TOOL_SPECS if spec.name == "read_file"
        )
        self.assertIn("read_file_range", description)

    def test_search_running_result_points_model_at_search_text_wait(self) -> None:
        result = canonical_tool_result("search_text", {
            "outcome": "success",
            "code": "search_running",
            "summary": "Search is still running",
            "data": {
                "matches": [],
                "scannedBytes": 0,
                "truncated": True,
                "truncationReason": "search_running",
                "sessionId": "search-1",
                "continuation": (
                    "Use search_text_wait with this sessionId; do not restart "
                    "the search."
                ),
            },
            "sideEffectsMayExist": False,
        })
        projection = project_tool_result("search_text", result)
        visible = json.dumps(projection.model_result)

        self.assertIn("search_text_wait", visible)
        self.assertIn("search-1", visible)

    def test_running_shell_result_points_model_at_write_stdin(self) -> None:
        description = next(
            spec.description for spec in TOOL_SPECS if spec.name == "run_shell"
        )
        stdin_description = next(
            spec.description for spec in TOOL_SPECS if spec.name == "write_stdin"
        )

        self.assertIn("sessionId", description)
        self.assertIn("session", stdin_description)
        properties = RunShellInput.model_json_schema(by_alias=True)["properties"]
        self.assertIn("write_stdin", properties["yieldTimeMs"]["description"])


if __name__ == "__main__":
    unittest.main()

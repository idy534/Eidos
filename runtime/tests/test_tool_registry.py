from __future__ import annotations

from pathlib import Path
import json
import sys
import threading
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eidos_runtime.model.client import CustomToolPayload, ModelResponse, ModelToolCall  # noqa: E402
from eidos_runtime.runtime.tool_dispatcher import ToolDispatcher  # noqa: E402
from eidos_runtime.tools.registry import (  # noqa: E402
    ToolProvenance,
    ToolRegistry,
    ToolRegistryEntry,
    ToolSpec,
)
from eidos_runtime.model.client import ModelToolDefinition  # noqa: E402
from eidos_runtime.tools.search import tool_search_entry  # noqa: E402
from eidos_runtime.tools.contracts import RunShellInput, RunShellResultData, ToolSearchInput  # noqa: E402
from eidos_runtime.tools.workspace import TOOL_SPECS  # noqa: E402


RESULT_SCHEMA = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


class MemoryAdapter:
    def execute(
        self, arguments: dict[str, object], _cancel: threading.Event
    ) -> dict[str, object]:
        return {
            "schemaVersion": 1,
            "toolContractVersion": 1,
            "toolName": "memory_echo",
            "outcome": "success",
            "code": "ok",
            "summary": "Echoed value",
            "data": {"value": arguments["value"]},
            "sideEffectsMayExist": False,
            "reconciliationRequired": False,
        }


def memory_entry(name: str = "memory_echo") -> ToolRegistryEntry:
    return ToolRegistryEntry(
        spec=ToolSpec.model_validate({
            "name": name,
            "description": "Echo a value from memory.",
            "sideEffect": "none",
            "approvalRequired": False,
            "timeoutSeconds": 5,
            "batchPolicy": "parallel",
            "visibility": "direct",
            "inputSchema": {
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
                "additionalProperties": False,
            },
            "resultSchema": RESULT_SCHEMA,
        }),
        provenance=ToolProvenance.model_validate({
            "kind": "builtin",
            "sourceId": "test",
            "sourceVersion": "1",
            "contentHash": "a" * 64,
        }),
        adapter=MemoryAdapter(),
    )


def shell_entry() -> ToolRegistryEntry:
    base = memory_entry()
    return ToolRegistryEntry(
        next(spec for spec in TOOL_SPECS if spec.name == "run_shell"),
        base.provenance, base.adapter,
        input_model=RunShellInput, result_data_model=RunShellResultData,
    )


class ToolRegistryTests(unittest.TestCase):
    def test_shell_yield_alias_is_normalized_before_dispatch(self) -> None:
        base = memory_entry()
        entry = ToolRegistryEntry(
            next(spec for spec in TOOL_SPECS if spec.name == "run_shell"),
            base.provenance,
            base.adapter,
            input_model=RunShellInput,
            result_data_model=RunShellResultData,
        )
        dispatcher = ToolDispatcher(ToolRegistry((entry,)))
        for name in ("yieldTimeMs", "yield_time_ms"):
            arguments = {"command": "true", name: 1000}
            with self.subTest(name=name):
                validated = dispatcher.validate(ModelResponse(tool_calls=(
                    ModelToolCall("shell-call", "run_shell", arguments),
                )))
                self.assertIsNone(validated.error_code)
                call = validated.tool_calls[0]
                self.assertEqual(call.arguments["yieldTimeMs"], 1000)
                self.assertNotIn("yield_time_ms", call.arguments)
                self.assertEqual(call.raw_payload.arguments, arguments)

    def test_shell_extra_fields_are_removed_before_dispatch(self) -> None:
        base = memory_entry()
        entry = ToolRegistryEntry(
            next(spec for spec in TOOL_SPECS if spec.name == "run_shell"),
            base.provenance, base.adapter,
            input_model=RunShellInput, result_data_model=RunShellResultData,
        )
        dispatcher = ToolDispatcher(ToolRegistry((entry,)))
        arguments = {
            "command": "true", "shell": "bash", "yield_timeMs": 1000,
            "sandbox_permissions": "require_escalated",
        }
        validated = dispatcher.validate(ModelResponse(tool_calls=(
            ModelToolCall("shell-call", "run_shell", arguments),
        )))
        self.assertIsNone(validated.error_code)
        call = validated.tool_calls[0]
        self.assertEqual(call.arguments, {"command": "true"})
        self.assertEqual(call.raw_payload.arguments, arguments)
        self.assertTrue(dispatcher.validate_execution(call, dispatcher.plan(call)))
        self.assertFalse(base.validate_arguments({"value": "x", "shell": "bash"}).valid)

    def test_shell_uses_valid_optional_values_and_discards_invalid_ones(self) -> None:
        entry = shell_entry()
        valid = {"command": "true", "cwd": "nested", "yieldTimeMs": 750}
        for invalid in (
            {"yieldTimeMs": "1500"}, {"yieldTimeMs": [1000]}, {"yieldTimeMs": 249},
            {"yield_time_ms": "1000", "yieldTimeMs": None},
            {"cwd": False}, {"dependencyBindingId": []},
            {"additionalPermissions": "null"}, {"additionalPermissions": []},
            {"additionalPermissions": {"network": {"unexpected": True}}},
            {"networkAccess": True}, {"gitWriteAccess": {}},
            {"sandboxPermissions": "invalid"}, {"justification": ["reason"]},
            {"yieldTimeMs": "1500", "additionalPermissions": "null", "networkAccess": True},
        ):
            arguments = {**valid, **invalid}
            with self.subTest(invalid=invalid):
                result = entry.validate_arguments(arguments)
                self.assertTrue(result.valid, result)
                expected = RunShellInput.model_validate_json(json.dumps({
                    name: value for name, value in arguments.items() if name not in invalid
                })).model_dump(mode="json", exclude_unset=True)
                self.assertEqual(result.normalized_arguments, expected)
                self.assertEqual(set(RunShellInput.ignored_argument_names(arguments)), set(invalid))

    def test_shell_discards_incomplete_permission_groups_without_losing_cwd_or_wait(self) -> None:
        entry = shell_entry()
        valid = {"command": "true", "cwd": "nested", "yieldTimeMs": 750}
        for permissions in (
            {"networkAccess": "request"},
            {"gitWriteAccess": "request", "justification": ["invalid reason"]},
            {"sandboxPermissions": "with_additional_permissions"},
            {"sandboxPermissions": "require_escalated"},
            {"additionalPermissions": {"network": {"enabled": True}}},
            {"networkAccess": "request", "sandboxPermissions": "require_escalated", "justification": "conflict"},
        ):
            arguments = {**valid, **permissions}
            with self.subTest(permissions=permissions):
                result = entry.validate_arguments(arguments)
                self.assertTrue(result.valid, result)
                self.assertEqual(result.normalized_arguments, valid)
                self.assertEqual(set(RunShellInput.ignored_argument_names(arguments)), set(permissions))

        request = entry.validate_arguments({**valid, "networkAccess": "request", "justification": "Download"})
        self.assertTrue(request.valid, request)
        self.assertEqual(request.normalized_arguments["networkAccess"], "request")
        self.assertEqual(request.normalized_arguments["justification"], "Download")
        permissions = {
            "sandboxPermissions": "with_additional_permissions",
            "additionalPermissions": {
                "fileSystem": [{"path": "/private/tmp/sdk", "access": "read", "recursive": True}],
                "network": {"enabled": True},
            },
            "justification": "Read the SDK",
        }
        structured = entry.validate_arguments({**valid, **permissions})
        self.assertTrue(structured.valid, structured)
        for name, value in permissions.items():
            self.assertEqual(structured.normalized_arguments[name], value)
        self.assertEqual(RunShellInput.ignored_argument_names({**valid, **permissions}), ())

    def test_shell_command_stays_required_and_strict(self) -> None:
        entry = shell_entry()
        for arguments in ({}, {"command": ""}, {"command": False}, {"command": ["pwd"]}):
            with self.subTest(arguments=arguments):
                result = entry.validate_arguments({**arguments, "yieldTimeMs": "bad", "shell": "bash"})
                self.assertFalse(result.valid)
                self.assertEqual(result.path, "command")

    def test_search_keeps_100_deferred_tools_out_until_next_snapshot(self) -> None:
        candidates = tuple(
            ToolRegistryEntry(
                memory_entry(f"external_{index:03d}").spec.model_copy(
                    update={
                        "description": f"Capability number {index}",
                        "visibility": "deferred",
                    }
                ),
                memory_entry(f"external_{index:03d}").provenance,
                MemoryAdapter(),
            )
            for index in range(120)
        )
        search = tool_search_entry(candidates)
        registry = ToolRegistry((*candidates, search))
        dispatcher = ToolDispatcher(registry)
        first = dispatcher.snapshot()

        validated = dispatcher.validate(
            ModelResponse(tool_calls=(ModelToolCall(
                "search", "tool_search", {"query": "number 117"}
            ),)),
            first.available_names,
        )
        search_entry = registry.get("tool_search")
        assert search_entry is not None and search_entry.runtime is not None
        cancel = threading.Event()
        prepared = search_entry.runtime.prepare(
            None, validated.tool_calls[0].arguments, cancel
        )
        raw = search_entry.runtime.execute(None, prepared, cancel)
        activated = search_entry.runtime.verify(
            None, prepared, raw, cancel
        ).activated_tool_names
        second = dispatcher.snapshot(activated)

        self.assertEqual(first.available_names, ("tool_search",))
        self.assertEqual(activated[0], "external_117")
        self.assertIn("external_117", second.available_names)
        hidden = dispatcher.validate(
            ModelResponse(tool_calls=(ModelToolCall(
                "hidden", "external_118", {"value": "x"}
            ),)),
            first.available_names,
        )
        self.assertIsNone(hidden.error_code)
        plan = dispatcher.plan(hidden.tool_calls[0], activation_required=True)
        self.assertTrue(plan.activation_required)
        self.assertEqual(plan.side_effect, "none")
        self.assertNotIn("external_118", first.available_names)
        self.assertFalse(dispatcher.is_parallel_read_call(hidden.tool_calls[0], first.available_names))
        self.assertFalse(dispatcher.is_parallel_read_batch(
            (hidden.tool_calls[0], hidden.tool_calls[0]), first.available_names,
        ))

    def test_undeclared_direct_and_unknown_tools_still_fail_validation(self) -> None:
        dispatcher = ToolDispatcher(ToolRegistry((memory_entry(),)))
        for name in ("memory_echo", "missing_tool"):
            with self.subTest(name=name):
                result = dispatcher.validate(ModelResponse(tool_calls=(
                    ModelToolCall("call", name, {"value": "x"}),
                )), ())
                self.assertEqual(result.error_code, "invalid_tool_call")

    def test_inactive_tools_do_not_relax_payload_and_call_id_checks(self) -> None:
        direct = memory_entry("deferred_echo")
        deferred = ToolRegistryEntry(
            direct.spec.model_copy(update={"visibility": "deferred"}),
            direct.provenance, direct.adapter,
        )
        dispatcher = ToolDispatcher(ToolRegistry((deferred,)))
        for calls in (
            (ModelToolCall("same", "deferred_echo", {}), ModelToolCall("same", "deferred_echo", {})),
            (ModelToolCall("", "deferred_echo", {}),),
            (ModelToolCall("bad-kind", "deferred_echo", CustomToolPayload(input="x")),),
        ):
            with self.subTest(calls=calls):
                self.assertIsNotNone(dispatcher.validate(ModelResponse(tool_calls=calls), ()).error_code)

    def test_tool_search_description_routes_deferred_tools_without_semantic_claims(
        self,
    ) -> None:
        search = tool_search_entry(())

        self.assertEqual(
            search.spec.description,
            "Search deferred tools by name, description, or source metadata "
            "and make matching tools available for subsequent calls. "
            "Use this when the required tool is not currently available.",
        )
        self.assertNotIn("semantic", search.spec.description.lower())
        self.assertNotIn("BM25", search.spec.description)
        properties = ToolSearchInput.model_json_schema(by_alias=True)["properties"]
        self.assertEqual(
            properties["query"]["description"],
            "Tool name, capability keywords, description text, "
            "or source metadata to search for.",
        )
        self.assertEqual(
            properties["limit"]["description"],
            "Maximum number of matching tools to return. Defaults to 10.",
        )
        self.assertEqual(set(properties), {"query", "limit"})
    def test_memory_adapter_dispatches_without_a_runtime_name_branch(self) -> None:
        dispatcher = ToolDispatcher(ToolRegistry((memory_entry(),)))
        call = ModelToolCall("call-1", "memory_echo", {"value": "hello"})

        validated = dispatcher.validate(ModelResponse(tool_calls=(call,)))
        entry = dispatcher._registry.get("memory_echo")
        assert entry is not None and entry.runtime is not None
        cancel = threading.Event()
        prepared = entry.runtime.prepare(
            None, validated.tool_calls[0].arguments, cancel
        )
        result = entry.runtime.execute(None, prepared, cancel)

        self.assertIsNone(validated.error_code)
        self.assertEqual(result["data"], {"value": "hello"})

    def test_registry_rejects_duplicate_names(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate_tool_name"):
            ToolRegistry((memory_entry(), memory_entry()))

    def test_registry_rejects_incomplete_provenance_and_open_schema(self) -> None:
        with self.assertRaises(ValueError):
            ToolProvenance.model_validate({
                "kind": "builtin",
                "sourceId": "test",
                "sourceVersion": "1",
                "contentHash": "short",
            })

        invalid = memory_entry()
        invalid_spec = invalid.spec.model_copy(
            update={"input_schema": {"type": "object", "properties": {}}}
        )
        with self.assertRaisesRegex(ValueError, "invalid_tool_schema"):
            ToolRegistry((ToolRegistryEntry(invalid_spec, invalid.provenance, invalid.adapter),))

    def test_model_definitions_are_stable_and_omit_runtime_metadata(self) -> None:
        registry = ToolRegistry((memory_entry(),))

        self.assertEqual(registry.model_definitions(), (
            ModelToolDefinition(
                name="memory_echo",
                description="Echo a value from memory.",
                parameters_json_schema=memory_entry().spec.input_schema,
            ),
        ))

    def test_external_invalid_entry_is_quarantined_without_losing_builtins(self) -> None:
        external = memory_entry("mcp__demo__echo")
        invalid_spec = external.spec.model_copy(
            update={"input_schema": {"type": "object", "properties": {}}}
        )

        registry = ToolRegistry.build(
            builtin_entries=(memory_entry(),),
            external_entries=(ToolRegistryEntry(
                invalid_spec,
                external.provenance.model_copy(update={
                    "kind": "mcp", "plugin_id": "demo", "server_id": "demo",
                }),
                external.adapter,
            ),),
        )

        self.assertEqual(registry.names, frozenset({"memory_echo"}))
        self.assertEqual(registry.quarantined[0].code, "invalid_tool_schema")

    def test_schema_backed_tool_normalizes_declared_defaults(self) -> None:
        base = memory_entry("mcp__demo__defaults")
        entry = ToolRegistryEntry(
            base.spec.model_copy(update={
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "value": {"type": "string"},
                        "mode": {"type": "string", "default": "safe"},
                    },
                    "required": ["value"],
                    "additionalProperties": False,
                },
            }),
            base.provenance.model_copy(update={
                "kind": "mcp", "plugin_id": "demo", "server_id": "demo",
            }),
            base.adapter,
        )

        validated = entry.validate_arguments({"value": "hello"})

        self.assertTrue(validated.valid)
        self.assertEqual(
            validated.normalized_arguments, {"value": "hello", "mode": "safe"},
        )

    def test_step_snapshot_order_and_hash_are_deterministic(self) -> None:
        first = memory_entry("alpha")
        second = memory_entry("zeta")

        left = ToolRegistry((second, first)).snapshot(activated_names=("zeta",))
        right = ToolRegistry((first, second)).snapshot(activated_names=("zeta",))

        self.assertEqual(left, right)
        self.assertEqual(left.available_names, ("alpha", "zeta"))
        self.assertEqual(len(left.tool_set_hash), 64)


if __name__ == "__main__":
    unittest.main()

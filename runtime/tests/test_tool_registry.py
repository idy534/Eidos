from __future__ import annotations

from pathlib import Path
import sys
import threading
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eidos_runtime.model.client import ModelResponse, ModelToolCall  # noqa: E402
from eidos_runtime.runtime.tool_dispatcher import ToolDispatcher  # noqa: E402
from eidos_runtime.tools.registry import (  # noqa: E402
    ToolProvenance,
    ToolRegistry,
    ToolRegistryEntry,
    ToolSpec,
)
from eidos_runtime.model.client import ModelToolDefinition  # noqa: E402
from eidos_runtime.tools.search import tool_search_entry  # noqa: E402


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


class ToolRegistryTests(unittest.TestCase):
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
        self.assertEqual(hidden.error_code, "invalid_tool_call")

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

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pydantic import ValidationError  # noqa: E402

from eidos_runtime.context.builder import ContextBuilder  # noqa: E402
from eidos_runtime.db.storage import SessionStore  # noqa: E402
from eidos_runtime.extensions.plugins import PluginCatalog  # noqa: E402
from eidos_runtime.extensions.skills import SkillCatalog  # noqa: E402
from eidos_runtime.model.client import (  # noqa: E402
    ModelResponse,
    ModelToolCall,
    ScriptedModel,
)
from eidos_runtime.runtime.engine import RuntimeEngine  # noqa: E402
from eidos_runtime.runtime.approval import (  # noqa: E402
    ApprovalCoordinator,
    ApprovalDecision,
)
from eidos_runtime.runtime.events import RuntimeEvents  # noqa: E402
from eidos_runtime.runtime.state_machine import RuntimePhaseTracker  # noqa: E402
from eidos_runtime.runtime.tool_dispatcher import ToolDispatcher  # noqa: E402
from eidos_runtime.runtime.tool_execution import (  # noqa: E402
    HandlerOutcome,
    PreparedToolExecution,
    ToolExecutionController,
)
from eidos_runtime.sandbox.sensitive import default_scanner  # noqa: E402
from eidos_runtime.tools.contracts import (  # noqa: E402
    ReadFileInput,
    ToolResultProjection,
    project_tool_result,
)
from eidos_runtime.tools.workspace import ToolExecutor  # noqa: E402
from eidos_runtime.tools.search import tool_search_entry  # noqa: E402
from eidos_runtime.tools.json_schema import (  # noqa: E402
    BoundedJsonSchema,
    JsonSchemaValidationError,
)


class Phase4ASkillContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-phase4a-")
        root = Path(self.temporary.name)
        self.data = root / "data"
        self.workspace = root / "workspace"
        source = root / "plugin"
        self.data.mkdir(mode=0o700)
        self.workspace.mkdir()
        skill = source / "skills" / "review"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: review\ndescription: Review files.\n---\nUse the checklist.\n",
            encoding="utf-8",
        )
        (source / "plugin.json").write_text(json.dumps({
            "schemaVersion": 1,
            "id": "demo",
            "name": "Demo",
            "version": "1.0.0",
            "description": "Fixture",
            "skills": [{"root": "skills/review"}],
            "mcpServers": [],
        }), encoding="utf-8")
        self.store = SessionStore(self.data)
        self.store.initialize()
        plugins = PluginCatalog(self.store)
        plugins.import_directory(source)
        plugins.set_enabled("demo", True)
        self.skills = SkillCatalog(plugins)
        self.snapshot = self.skills.extension_snapshot()

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_catalog_is_stable_retained_context_and_selection_is_separate(self) -> None:
        catalog = self.skills.catalog_snapshot(self.snapshot)
        reordered = catalog.model_copy(update={"entries": tuple(reversed(catalog.entries))})
        selected = self.skills.select_explicit(
            self.snapshot, "turn-1", "Use @demo:review"
        )

        self.assertEqual(catalog.catalog_hash, reordered.canonical_hash())
        self.assertEqual(selected.selected_qualified_ids, ("demo:review",))
        self.assertNotIn("Use the checklist.", self.skills.render_catalog(catalog).content)
        selected_context = self.skills.render_selected(self.snapshot, selected)
        self.assertEqual(len(selected_context), 1)
        self.assertEqual(selected_context[0].section_id, "selected-skill:demo:review")
        self.assertEqual(selected_context[0].role, "developer")
        self.assertEqual(selected_context[0].source, "demo")
        self.assertIn("Use the checklist.", selected_context[0].content)

    def test_materialized_catalog_ignores_new_skills_until_next_run(self) -> None:
        catalog_a = self.skills.catalog_snapshot(self.snapshot)
        assert self.store.data_directory is not None
        new_skill = self.store.data_directory / "skills" / "new-skill"
        new_skill.mkdir(parents=True)
        (new_skill / "SKILL.md").write_text(
            "---\nname: new-skill\ndescription: New skill.\n---\nNew.\n",
            encoding="utf-8",
        )

        same_run = self.skills.select_explicit(
            catalog_a, "turn-2", "@user:new-skill"
        )
        installed_skill = (
            self.skills.plugins.installed_root("demo")
            / "skills"
            / "review"
            / "SKILL.md"
        )
        installed_skill.write_text(
            "---\nname: review\ndescription: Changed.\n---\nChanged.\n",
            encoding="utf-8",
        )
        existing = self.skills.select_explicit(
            catalog_a, "turn-3", "@demo:review"
        )
        self.assertEqual(same_run.selected_qualified_ids, ())
        selected_context = self.skills.render_selected(catalog_a, existing)
        self.assertEqual(len(selected_context), 1)
        self.assertIn(
            "Use the checklist.",
            selected_context[0].content,
        )

        snapshot_b = self.skills.extension_snapshot()
        catalog_b = self.skills.catalog_snapshot(snapshot_b)
        self.assertNotEqual(catalog_a.catalog_hash, catalog_b.catalog_hash)
        self.assertIn(
            "user:new-skill",
            {entry.qualified_id for entry in catalog_b.entries},
        )

    def test_context_builder_places_one_catalog_before_history_and_uses_projection(self) -> None:
        session = self.store.create_session(str(self.workspace))
        run, _ = self.store.create_run(session["id"], "Use @demo:review")
        item = self.store.create_tool_item(
            run["id"], 1, 0, "call-1", "read_file", '{"path":"large.txt"}'
        )
        canonical = {
            "schemaVersion": 1,
            "toolContractVersion": 1,
            "toolName": "read_file",
            "outcome": "success",
            "code": "ok",
            "summary": "Read file",
            "data": {
                "path": "large.txt",
                "content": "x" * 100_000,
                "sizeBytes": 100_000,
                "sha256": "a" * 64,
                "truncated": False,
            },
            "sideEffectsMayExist": False,
            "reconciliationRequired": False,
        }
        projection = project_tool_result("read_file", canonical)
        self.store.complete_tool_item(
            item["id"],
            json.dumps(canonical),
            model_result_json=json.dumps(projection.model_result),
        )
        catalog_section = self.skills.render_catalog(
            self.skills.catalog_snapshot(self.snapshot)
        )
        selected_context = self.skills.render_selected(
            self.snapshot,
            self.skills.select_explicit(
                self.snapshot, str(run["id"]), "Use @demo:review"
            ),
        )

        built = ContextBuilder(self.store).build(
            run["id"],
            retained_context=(catalog_section,),
            selected_skill_context=selected_context,
        )
        rendered = json.dumps(built.model_context)
        catalog_indexes = [
            index for index, value in enumerate(built.model_context)
            if value.get("sectionId") == "skill-catalog"
        ]
        tool_index = next(
            index for index, value in enumerate(built.model_context)
            if value.get("type") == "tool_result"
        )

        self.assertEqual(catalog_indexes, [2])
        self.assertLess(catalog_indexes[0], tool_index)
        self.assertEqual(rendered.count("Skill Catalog"), 1)
        self.assertNotIn("x" * 100_000, rendered)
        self.assertIn("Use the checklist.", rendered)
        self.assertNotIn("Use the checklist.", built.instructions.system_text)

    def test_tool_round_trip_keeps_one_catalog_in_retained_position(self) -> None:
        session = self.store.create_session(str(self.workspace))
        run, _ = self.store.create_run(
            session["id"],
            "Use @demo:review",
            extension_snapshot=self.snapshot,
        )
        model = ScriptedModel([
            ModelResponse(tool_calls=(
                ModelToolCall("call", "list_files", {}),
            )),
            ModelResponse(text="done"),
        ])

        RuntimeEngine(self.store, model, lambda _message: None).run(
            run["id"], threading.Event()
        )

        self.assertEqual(len(model.contexts), 2)
        for context, instructions in zip(
            model.contexts,
            model.instructions_history,
            strict=True,
        ):
            catalogs = [
                index for index, item in enumerate(context)
                if item.get("sectionId") == "skill-catalog"
            ]
            self.assertEqual(catalogs, [2])
            self.assertEqual(
                sum("Skill Catalog" in str(item.get("content", "")) for item in context),
                1,
            )
            self.assertIn("Use the checklist.", json.dumps(context))
            self.assertNotIn("Use the checklist.", instructions)
        second_tool_result = next(
            index for index, item in enumerate(model.contexts[1])
            if item.get("type") == "tool_result"
        )
        self.assertLess(1, second_tool_result)

    def test_unqualified_ambiguous_skill_is_rejected(self) -> None:
        assert self.store.data_directory is not None
        duplicate = self.store.data_directory / "skills" / "review"
        duplicate.mkdir(mode=0o700, parents=True)
        skill_file = duplicate / "SKILL.md"
        skill_file.write_text(
            "---\nname: review\ndescription: Another review skill.\n---\nBody.\n",
            encoding="utf-8",
        )
        skill_file.chmod(0o600)
        snapshot = self.skills.extension_snapshot()

        with self.assertRaisesRegex(ValueError, "skill_reference_ambiguous"):
            self.skills.select_explicit(snapshot, "turn-2", "Use $review")
        session = self.store.create_session(str(self.workspace))
        run, _ = self.store.create_run(
            session["id"], "Use $review", extension_snapshot=snapshot
        )
        RuntimeEngine(
            self.store, ScriptedModel([]), lambda _message: None
        ).run(run["id"], threading.Event())
        self.assertEqual(
            self.store.read_run(run["id"])["errorCode"],
            "SKILL_REFERENCE_AMBIGUOUS",
        )

    def test_selected_skill_does_not_carry_to_later_run(self) -> None:
        session = self.store.create_session(str(self.workspace))
        first, _ = self.store.create_run(
            session["id"], "Use @demo:review", extension_snapshot=self.snapshot
        )
        first_model = ScriptedModel([ModelResponse(text="done")])
        RuntimeEngine(self.store, first_model, lambda _message: None).run(
            first["id"], threading.Event()
        )
        second, _ = self.store.create_run(
            session["id"], "No skill selected", extension_snapshot=self.snapshot
        )
        second_model = ScriptedModel([ModelResponse(text="done")])
        RuntimeEngine(self.store, second_model, lambda _message: None).run(
            second["id"], threading.Event()
        )

        self.assertIn("Use the checklist.", json.dumps(first_model.contexts[0]))
        self.assertNotIn("Use the checklist.", json.dumps(second_model.contexts[0]))
        self.assertNotIn("Use the checklist.", first_model.instructions_history[0])
        self.assertNotIn("Use the checklist.", second_model.instructions_history[0])

    def test_every_registered_builtin_has_one_authoritative_contract(self) -> None:
        with ToolExecutor(self.workspace) as executor:
            entries = (
                *executor.registry.entries,
                *self.skills.tool_entries(self.snapshot),
                tool_search_entry(()),
            )
            for entry in entries:
                self.assertIsNotNone(entry.input_model, entry.spec.name)
                self.assertIsNotNone(entry.result_data_model, entry.spec.name)
                self.assertEqual(
                    entry.spec.input_schema,
                    entry.input_model.model_json_schema(by_alias=True),
                )
                self.assertEqual(
                    entry.spec.result_schema,
                    entry.result_model_json_schema(),
                )
                samples = {
                    "list_files": {"paths": [], "truncated": False},
                    "read_file": {
                        "path": "a", "content": "", "sizeBytes": 0,
                        "sha256": "a" * 64, "truncated": False,
                    },
                    "read_file_range": {
                        "path": "a", "content": "", "sizeBytes": 0,
                        "sha256": "a" * 64, "startLine": 1, "endLine": 1,
                    },
                    "search_text": {
                        "matches": [], "scannedBytes": 0, "truncated": False,
                    },
                    "write_file": {"path": "a"},
                    "apply_patch": {"path": "a"},
                    "delete_file": {"path": "a"},
                    "run_shell": {
                        "exitCode": 0, "stdout": "", "stderr": "",
                        "truncated": False, "termination": "exit",
                        "workspaceChanged": False,
                    },
                    "skill_read": {
                        "qualifiedId": "demo:review", "content": "",
                        "contentHash": "a" * 64, "pluginId": "demo",
                        "pluginVersion": "1", "pluginHash": "b" * 64,
                    },
                    "skill_read_resource": {
                        "qualifiedId": "demo:review",
                        "resourcePath": "x", "content": "",
                        "contentHash": "a" * 64, "pluginId": "demo",
                    },
                    "skill_create": {
                        "path": "x", "qualifiedId": "user:x",
                        "contentHash": "a" * 64,
                    },
                    "skill_install": {
                        "path": "x", "qualifiedId": "user:x",
                        "contentHash": "a" * 64,
                    },
                    "tool_search": {
                        "hits": [], "totalMatches": 0, "truncated": False,
                    },
                }
                success = {
                    "schemaVersion": 1,
                    "toolContractVersion": 1,
                    "toolName": entry.spec.name,
                    "outcome": "success",
                    "code": "ok",
                    "summary": "sample",
                    "data": samples[entry.spec.name],
                    "sideEffectsMayExist": False,
                    "reconciliationRequired": False,
                }
                error = {
                    **success,
                    "outcome": "error",
                    "code": "sample_error",
                    "data": {},
                }
                self.assertEqual(
                    entry.validate_result(success)["toolName"],
                    entry.spec.name,
                )
                self.assertEqual(
                    entry.validate_result(error)["outcome"], "error"
                )
                with self.assertRaises(ValueError):
                    entry.validate_result({**success, "extra": True})
                with self.assertRaises(ValueError):
                    entry.validate_result({**success, "data": {}})
                invalid_data = {**success, "data": {"extra": True}}
                with self.assertRaises(ValueError):
                    entry.validate_result(invalid_data)


class Phase4AToolContractTests(unittest.TestCase):
    def test_builtin_contracts_are_generated_from_strict_models(self) -> None:
        with self.assertRaises(ValidationError):
            ReadFileInput.model_validate({"path": "a.txt", "extra": True})

        with tempfile.TemporaryDirectory(prefix="eidos-contract-") as directory:
            with ToolExecutor(Path(directory)) as executor:
                for entry in executor.registry.entries:
                    self.assertIsNotNone(entry.input_model)
                    self.assertIsNotNone(entry.result_data_model)
                    self.assertEqual(
                        entry.spec.input_schema,
                        entry.input_model.model_json_schema(by_alias=True),
                    )
                    self.assertEqual(
                        entry.spec.result_schema,
                        entry.result_model_json_schema(),
                    )

    def test_projection_is_bounded_and_semantic_fingerprint_ignores_duration(self) -> None:
        canonical = {
            "schemaVersion": 1,
            "toolContractVersion": 1,
            "toolName": "run_shell",
            "outcome": "success",
            "code": "ok",
            "summary": "Shell completed",
            "data": {
                "exitCode": 0,
                "stdout": "x" * 300_000,
                "stderr": "",
                "durationMs": 1,
                "modified": ["b", "a"],
            },
            "sideEffectsMayExist": False,
            "reconciliationRequired": False,
        }
        first = project_tool_result("run_shell", canonical)
        second = project_tool_result(
            "run_shell",
            {
                **canonical,
                "data": {
                    **canonical["data"],
                    "durationMs": 99,
                    "modified": ["a", "b"],
                },
            },
        )

        self.assertIsInstance(first, ToolResultProjection)
        self.assertLess(len(json.dumps(first.model_result).encode()), 70_000)
        self.assertTrue(first.model_result["data"]["truncated"])
        self.assertEqual(first.progress_fingerprint, second.progress_fingerprint)

    def test_dynamic_schema_validator_is_shared_bounded_and_closed(self) -> None:
        validator = BoundedJsonSchema({
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["safe", "fast"]},
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "value": {"type": "integer", "minimum": 1},
                            "enabled": {"type": "boolean", "const": True},
                        },
                        "required": ["value", "enabled"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["mode", "items"],
            "additionalProperties": False,
        })

        self.assertEqual(
            validator.validate({
                "mode": "safe",
                "items": [{"value": 1, "enabled": True}],
            }),
            {"mode": "safe", "items": [{"value": 1, "enabled": True}]},
        )
        with self.assertRaises(JsonSchemaValidationError):
            validator.validate({
                "mode": "safe",
                "items": [{"value": 0, "enabled": True, "extra": 1}],
            })
        with self.assertRaises(JsonSchemaValidationError):
            BoundedJsonSchema({
                "type": "object",
                "properties": {"x": {"$ref": "https://example.com/schema"}},
                "additionalProperties": False,
            })
        number = BoundedJsonSchema({"type": "number"})
        with self.assertRaises(JsonSchemaValidationError):
            number.validate(float("nan"))


class Phase4ASideEffectContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-phase4a-side-")
        root = Path(self.temporary.name)
        data = root / "data"
        self.workspace = root / "workspace"
        data.mkdir(mode=0o700)
        self.workspace.mkdir()
        self.store = SessionStore(data)
        self.store.initialize()
        session = self.store.create_session(str(self.workspace))
        self.run, _ = self.store.create_run(session["id"], "contract")
        self.store.increment_model_step(self.run["id"])
        self.executor = ToolExecutor(self.workspace)
        self.dispatcher = ToolDispatcher(self.executor.registry)
        self.events = RuntimeEvents(lambda _message: None)
        self.approval = ApprovalCoordinator(
            self.store,
            lambda _request, _cancel: ApprovalDecision("approve"),
            self.events,
            RuntimePhaseTracker(),
            lambda _run_id: None,
            lambda: None,
            lambda _run_id, _cancel: None,
            lambda _run_id, _cancel: None,
            requeue=False,
        )

    def tearDown(self) -> None:
        self.executor.close()
        self.store.close()
        self.temporary.cleanup()

    def _item(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        return self.store.create_tool_item(
            self.run["id"],
            1,
            0,
            f"call-{name}",
            name,
            json.dumps(arguments),
        )

    def _result(
        self, name: str, data: dict[str, object]
    ) -> dict[str, object]:
        return {
            "schemaVersion": 1,
            "toolContractVersion": 1,
            "toolName": name,
            "outcome": "success",
            "code": "ok",
            "summary": "done",
            "data": data,
            "sideEffectsMayExist": False,
            "reconciliationRequired": False,
        }

    def test_invalid_read_result_fails_without_reconciliation(self) -> None:
        class Handler:
            def execute(inner, _run_id, _item, _call, _cancel):
                return HandlerOutcome(
                    self._result("read_file", {"unexpected": True}),
                    "completed",
                )

        handler = Handler()
        class RuntimeContext:
            def invoke_read(inner, _runtime, run_id, item, call, cancel):
                return handler.execute(run_id, item, call, cancel)

        controller = ToolExecutionController(
            self.store,
            self.dispatcher,
            RuntimeContext(),
            self.events,
            default_scanner(),
        )
        call = ModelToolCall("call-read", "read_file", {"path": "a.txt"})
        outcome = controller.execute(
            run_id=self.run["id"],
            item=self._item("read_file", call.arguments),
            call=call,
            plan=self.dispatcher.plan(call),
            cancel=threading.Event(),
            deadline=None,
        )

        self.assertEqual(
            outcome.result["code"], "TOOL_RESULT_CONTRACT_VIOLATION"
        )
        self.assertFalse(outcome.result["sideEffectsMayExist"])
        self.assertFalse(outcome.result["reconciliationRequired"])

    def test_projection_failure_after_authorized_change_preserves_uncertainty(self) -> None:
        class Handler:
            execute_side_effect = None

            def execute(inner, run_id, item, _call, cancel):
                approval, verified = inner.execute_side_effect(
                    run_id=run_id,
                    item=item,
                    prepared=PreparedToolExecution(
                        approval_description={
                            "kind": "file_change",
                            "summary": "Modify a.txt",
                            "diff": "",
                        },
                        intent_preconditions={"path": "a.txt"},
                        transition_reason="file_approval",
                    ),
                    cancel=cancel,
                    execute=lambda: self._result(
                        "write_file",
                        {"path": "a.txt", "sha256": "a" * 64},
                    ),
                )
                self.assertEqual(approval.decision, "approve")
                assert verified is not None
                return HandlerOutcome(verified.result, "completed")

        handler = Handler()
        class RuntimeContext:
            def invoke_workspace_mutation(
                inner, _runtime, run_id, item, call, cancel
            ):
                return handler.execute(run_id, item, call, cancel)

        controller = ToolExecutionController(
            self.store,
            self.dispatcher,
            RuntimeContext(),
            self.events,
            default_scanner(),
            approval=self.approval,
        )
        handler.execute_side_effect = controller.execute_side_effect
        call = ModelToolCall(
            "call-write",
            "write_file",
            {"path": "a.txt", "content": "hello"},
        )
        plan = self.dispatcher.plan(call)
        assert plan.descriptor is not None
        assert plan.descriptor.projector is not None
        with patch.object(
            plan.descriptor.projector,
            "project",
            side_effect=ValueError("projection"),
        ):
            outcome = controller.execute(
                run_id=self.run["id"],
                item=self._item("write_file", call.arguments),
                call=call,
                plan=plan,
                cancel=threading.Event(),
                deadline=None,
            )

        self.assertEqual(
            outcome.result["code"], "TOOL_RESULT_PROJECTION_FAILED"
        )
        self.assertTrue(outcome.result["sideEffectsMayExist"])
        self.assertTrue(outcome.result["reconciliationRequired"])


if __name__ == "__main__":
    unittest.main()

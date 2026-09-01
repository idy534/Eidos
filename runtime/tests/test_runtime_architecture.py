from __future__ import annotations

import ast
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from pydantic import ValidationError

import sys

RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.db.storage import SessionStore  # noqa: E402
from eidos_runtime.model.client import (  # noqa: E402
    ModelResponse,
    ModelToolCall,
    ScriptedModel,
)
from eidos_runtime.model.config import default_profile_snapshot  # noqa: E402


class RuntimeArchitectureTests(unittest.TestCase):
    def test_engine_has_no_concrete_tool_dependencies(self) -> None:
        engine_path = RUNTIME_ROOT / "eidos_runtime" / "runtime" / "engine.py"
        tree = ast.parse(engine_path.read_text(encoding="utf-8"))
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imports.update(
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        )
        self.assertTrue({
            "eidos_runtime.sandbox.shell",
            "eidos_runtime.extensions.skills",
            "eidos_runtime.extensions.mcp",
            "eidos_runtime.tools.workspace",
        }.isdisjoint(imports))

    def test_async_portal_is_owned_only_by_runtime_async_kernel(self) -> None:
        production = RUNTIME_ROOT / "eidos_runtime"
        forbidden = (
            "class _AsyncLoop",
            "asyncio.new_event_loop",
            "asyncio.run_coroutine_threadsafe",
            "MODEL_LOOP",
            "eidos-model-",
            "model event loop did not stop",
        )
        portal_terms = ("start_blocking_portal", "BlockingPortal")
        private_terms = ("pydantic_ai._", "anyio._")
        for path in production.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            with self.subTest(path=path):
                self.assertTrue(all(term not in source for term in forbidden))
                self.assertTrue(all(term not in source for term in private_terms))
                if path.name != "async_kernel.py":
                    self.assertTrue(all(term not in source for term in portal_terms))

    def test_engine_has_no_concrete_tool_execution_methods(self) -> None:
        from eidos_runtime.runtime.engine import RuntimeEngine

        forbidden = {
            "_execute_shell",
            "_execute_file_change",
            "_execute_external",
            "_execute_eidos_state",
            "_execute_network_eidos_state",
        }
        self.assertTrue(forbidden.isdisjoint(RuntimeEngine.__dict__))

    def test_stream_progress_never_replays_the_model_request(self) -> None:
        from eidos_runtime.runtime.engine import RuntimeEngine

        class InterruptedThenCompletedModel:
            calls = 0

            def complete(
                self, _context, _cancel, on_text_delta,
                *, instructions,
                allow_tools=True, tool_definitions=(),
            ):
                self.calls += 1
                if self.calls == 1:
                    on_text_delta("safe progress")
                    raise OSError("fixture")
                on_text_delta("done")
                return ModelResponse(text="done")

        with self.runtime() as (store, session, _workspace):
            run, _ = store.create_run(session["id"], "stream")
            RuntimeEngine(
                store, InterruptedThenCompletedModel(), lambda _message: None
            ).run(run["id"], threading.Event())
            assert store.connection is not None
            self.assertEqual(
                store.connection.execute("SELECT COUNT(*) FROM steps").fetchone()[0],
                1,
            )
            self.assertEqual(
                store.connection.execute(
                    "SELECT COUNT(*) FROM model_attempts"
                ).fetchone()[0],
                1,
            )
            attempt = store.read_model_attempts(run["id"])[0]
            self.assertEqual(attempt["retryDecision"]["reason"], "unsafe_stream_progress")

    def test_step_snapshot_drives_model_validation_and_execution(self) -> None:
        from eidos_runtime.runtime.engine import RuntimeEngine

        with self.runtime() as (store, session, workspace):
            (workspace / "a.txt").write_text("hello", encoding="utf-8")
            run, _ = store.create_run(session["id"], "read a.txt")
            model = ScriptedModel([
                ModelResponse(tool_calls=(ModelToolCall(
                    "read-1", "read_file", {"path": "a.txt"}
                ),)),
                ModelResponse(text="done"),
            ])

            RuntimeEngine(store, model, lambda _message: None).run(
                run["id"], threading.Event()
            )

            assert store.connection is not None
            snapshot = json.loads(store.connection.execute(
                "SELECT tool_snapshot_json FROM steps ORDER BY creation_seq LIMIT 1"
            ).fetchone()[0])
            tool_call = store.connection.execute(
                "SELECT tool_name, tool_set_hash, result_json FROM tool_calls"
            ).fetchone()
            model_names = tuple(
                value.name
                for value in model.tool_definitions_history[0]
            )
            self.assertEqual(model_names, tuple(snapshot["availableNames"]))
            self.assertEqual(tool_call["tool_name"], "read_file")
            self.assertEqual(tool_call["tool_set_hash"], snapshot["toolSetHash"])
            self.assertEqual(json.loads(tool_call["result_json"])["outcome"], "success")

    def test_scripted_model_only_receives_view_image_for_image_capability(self) -> None:
        from eidos_runtime.runtime.engine import RuntimeEngine

        with self.runtime() as (store, session, _workspace):
            image_profile = default_profile_snapshot(
                "deepseek-v4-flash"
            ).model_copy(update={"supports_images": True})
            image_run, _ = store.create_run(
                session["id"],
                "inspect image",
                model_profile=image_profile,
            )
            image_model = ScriptedModel([ModelResponse(text="done")])
            RuntimeEngine(store, image_model, lambda _message: None).run(
                image_run["id"], threading.Event()
            )
            self.assertIn(
                "view_image",
                {tool.name for tool in image_model.tool_definitions_history[0]},
            )

        with self.runtime() as (store, session, _workspace):
            text_run, _ = store.create_run(session["id"], "inspect text")
            text_model = ScriptedModel([ModelResponse(text="done")])
            RuntimeEngine(store, text_model, lambda _message: None).run(
                text_run["id"], threading.Event()
            )
            self.assertNotIn(
                "view_image",
                {tool.name for tool in text_model.tool_definitions_history[0]},
            )

    def test_run_resources_closes_started_resources_when_startup_fails(self) -> None:
        from eidos_runtime.runtime.run_resources import RunResources

        with self.runtime() as (store, session, _workspace):
            run, _ = store.create_run(session["id"], "start")
            with (
                patch(
                    "eidos_runtime.runtime.run_resources.McpManager.start",
                    side_effect=RuntimeError("fixture startup failure"),
                ),
                patch(
                    "eidos_runtime.runtime.run_resources.ToolExecutor.close",
                    autospec=True,
                ) as close_tool_executor,
                patch(
                    "eidos_runtime.runtime.run_resources.McpManager.close",
                    autospec=True,
                ) as close_mcp,
            ):
                with self.assertRaisesRegex(RuntimeError, "fixture startup failure"):
                    with RunResources(store, run["id"], run["extensionSnapshot"]):
                        self.fail("resource startup unexpectedly succeeded")
            close_tool_executor.assert_called_once()
            close_mcp.assert_called_once()

    def test_run_resources_gates_view_image_and_activates_skill_read_root(self) -> None:
        from eidos_runtime.extensions.plugins import PluginCatalog
        from eidos_runtime.extensions.skills import SkillCatalog
        from eidos_runtime.runtime.run_resources import RunResources

        with self.runtime() as (store, session, _workspace):
            assert store.data_directory is not None
            skill = store.data_directory / "skills" / "review"
            skill.mkdir(mode=0o700, parents=True)
            (skill / "SKILL.md").write_text(
                "---\nname: review\ndescription: Review images.\n---\nBody.\n",
                encoding="utf-8",
            )
            (skill / "SKILL.md").chmod(0o600)
            (skill / "agents").mkdir(mode=0o700)
            (skill / "agents" / "eidos.yaml").write_text(
                "dependencies:\n"
                "  tools:\n"
                "    - type: mcp\n"
                "      value: missing-documents\n"
                "      transport: stdio\n",
                encoding="utf-8",
            )
            (skill / "agents" / "eidos.yaml").chmod(0o600)
            catalog = SkillCatalog(PluginCatalog(store))
            extension_snapshot = catalog.extension_snapshot()
            run, _ = store.create_run(
                session["id"], "review", extension_snapshot=extension_snapshot
            )

            with RunResources(
                store,
                run["id"],
                extension_snapshot,
                supports_images=True,
            ) as resources:
                assert resources.registry is not None
                self.assertIn("view_image", resources.registry.names)
                self.assertEqual(resources.skill_access.active_roots(), ())
                entry = resources.registry.get("skill_read")
                assert entry is not None
                result = entry.adapter.execute(
                    {"qualifiedId": "user:review"}, threading.Event()
                )
                self.assertEqual(result["outcome"], "success")
                self.assertEqual(
                    resources.skill_access.active_roots(), (skill.resolve(),)
                )
                skill_root = skill.resolve()
                read_entry = resources.registry.get("read_file")
                list_entry = resources.registry.get("list_files")
                search_entry = resources.registry.get("search_text")
                assert read_entry is not None
                assert list_entry is not None
                assert search_entry is not None
                read_result = read_entry.adapter.execute(
                    {"path": str(skill_root / "SKILL.md")}, threading.Event()
                )
                list_result = list_entry.adapter.execute(
                    {
                        "path": str(skill_root),
                        "maxDepth": 5,
                        "maxEntries": 2_000,
                    },
                    threading.Event(),
                )
                search_result = search_entry.adapter.execute(
                    {
                        "query": "Body.",
                        "path": str(skill_root),
                        "regex": False,
                        "includeGlobs": (),
                        "maxResults": 100,
                    },
                    threading.Event(),
                )
                self.assertEqual(read_result["outcome"], "success")
                self.assertEqual(read_result["data"]["path"], "SKILL.md")
                self.assertIn("SKILL.md", list_result["data"]["paths"])
                self.assertEqual(
                    [match["path"] for match in search_result["data"]["matches"]],
                    ["SKILL.md"],
                )
                self.assertEqual(
                    resources.image_authority().active_skill_roots,
                    (skill.resolve(),),
                )
                self.assertEqual(
                    tuple(
                        (item.value, item.status)
                        for item in resources.skill_dependency_diagnostics
                    ),
                    (("missing-documents", "missing"),),
                )
                warning = next(
                    section
                    for section in resources.retained_context
                    if section.section_id == "skill-dependency-warning"
                )
                self.assertEqual(warning.role, "user")
                self.assertIn("missing-documents", warning.content)

            with RunResources(
                store,
                run["id"],
                extension_snapshot,
                supports_images=False,
            ) as resources:
                assert resources.registry is not None
                self.assertNotIn("view_image", resources.registry.names)

    def test_projectless_run_uses_system_workspace_and_runtime_resources(self) -> None:
        from eidos_runtime.runtime.run_resources import RunResources

        with self.runtime() as (store, _session, _workspace):
            chat_root = (
                Path(store.data_directory)
                / f".{Path(store.data_directory).name}-projectless"
                / "chat-root"
            )
            chat_root.mkdir(parents=True)
            chat = store.typed_runtime_repository().create_session(
                str(chat_root), projectless=True
            ).value
            run, _ = store.create_run(chat.id, "chat")

            with RunResources(store, run["id"], run["extensionSnapshot"]) as resources:
                self.assertIsNotNone(resources.registry)
                names = {entry.spec.name for entry in resources.registry.entries}
                self.assertIn("read_file", names)
                self.assertIn("skill_read", names)
                self.assertIn("workspace_dependencies", names)
                self.assertIsNotNone(resources.tool_executor)
                self.assertIsNotNone(resources.skills)
                self.assertIsNotNone(resources.mcp)
                self.assertEqual(
                    Path(store.workspace_for_run(run["id"]).path),
                    chat_root,
                )
                permission_profile = json.loads(
                    store.read_run_resolution_snapshot(
                        run["id"]
                    ).permission_profile_json
                )
                self.assertEqual(
                    permission_profile["workspaceRoots"],
                    [str(chat_root.resolve())],
                )

    def test_projectless_engine_skips_repository_runtime(self) -> None:
        from eidos_runtime.runtime.engine import RuntimeEngine

        class RejectingRepositoryRuntime:
            def ensure_ready(self, *_args, **_kwargs):
                raise AssertionError("projectless runs must not build a repository index")

        with self.runtime() as (store, _session, _workspace):
            chat_root = (
                Path(store.data_directory)
                / f".{Path(store.data_directory).name}-projectless"
                / "chat-root"
            )
            chat_root.mkdir(parents=True)
            chat = store.typed_runtime_repository().create_session(
                str(chat_root), projectless=True
            ).value
            run, _ = store.create_run(chat.id, "chat")
            model = ScriptedModel([ModelResponse(text="done")])

            RuntimeEngine(
                store,
                model,
                lambda _message: None,
                repository_runtime=RejectingRepositoryRuntime(),
            ).run(run["id"], threading.Event())

            names = {
                value.name for value in model.tool_definitions_history[0]
            }
            self.assertIn("read_file", names)
            self.assertIn("skill_read", names)

    def test_non_retryable_sampling_error_creates_only_one_attempt(self) -> None:
        from eidos_runtime.model.client import ModelRequestError, ModelRequestFailure
        from eidos_runtime.runtime.engine import RuntimeEngine

        class AuthenticationFailure:
            calls = 0

            def complete(self, *_args, **_kwargs):
                self.calls += 1
                raise ModelRequestError(ModelRequestFailure(
                    code="authentication_failed", retryable=False, status_code=401,
                ))

        with self.runtime() as (store, session, _workspace):
            run, _ = store.create_run(session["id"], "authenticate")
            model = AuthenticationFailure()
            RuntimeEngine(store, model, lambda _message: None).run(
                run["id"], threading.Event()
            )
            assert store.connection is not None
            self.assertEqual(model.calls, 1)
            self.assertEqual(
                store.connection.execute(
                    "SELECT COUNT(*) FROM model_attempts"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                store.read_run(run["id"])["errorCode"],
                "MODEL_AUTHENTICATION_FAILED",
            )

    def test_finalization_failure_is_reported_and_uses_no_magic_step(self) -> None:
        from eidos_runtime.runtime.events import RuntimeEvents
        from eidos_runtime.runtime.finalizer import RunFinalizer
        from eidos_runtime.runtime.state_machine import RuntimePhaseTracker
        from eidos_runtime.sandbox.sensitive import SensitiveScanner

        class FinalizationFailure:
            def complete(self, *_args, **_kwargs):
                raise OSError("fixture finalization failure")

        with self.runtime() as (store, session, _workspace):
            run, _ = store.create_run(session["id"], "finalize")
            assert store.connection is not None
            store.connection.execute(
                "UPDATE runs SET model_step_count = 80 WHERE id = ?", (run["id"],)
            )
            store.connection.commit()
            with self.assertLogs("eidos.runtime", level="WARNING") as logs:
                RunFinalizer(
                    store,
                    FinalizationFailure(),
                    RuntimeEvents(lambda _message: None),
                    SensitiveScanner(),
                    RuntimePhaseTracker(),
                ).finalize(
                    run["id"],
                    (),
                    "context_still_over_budget",
                    threading.Event(),
                )
            self.assertEqual(store.read_run(run["id"])["status"], "stopped")
            self.assertEqual(
                store.connection.execute(
                    "SELECT COUNT(*) FROM items WHERE model_step_index = 80"
                ).fetchone()[0],
                0,
            )
            self.assertTrue(any("finalization_model_failed" in line for line in logs.output))

    def test_snapshot_contracts_are_frozen_and_forbid_extra_fields(self) -> None:
        from eidos_runtime.runtime.contracts import RunContext

        with self.runtime() as (store, session, _workspace):
            run, _ = store.create_run(session["id"], "snapshot")
            resolution = store.read_run_resolution_snapshot(run["id"])
            context = RunContext(
                run_id=run["id"],
                session_id=session["id"],
                model_id="deepseek-v4-flash",
                model_profile=default_profile_snapshot("deepseek-v4-flash"),
                model_context=(),
                extension_snapshot=run["extensionSnapshot"],
                extension_snapshot_hash=resolution.extension_snapshot_hash,
                resolution_snapshot=resolution,
            )
        with self.assertRaises(ValidationError):
            context.run_id = "changed"  # type: ignore[misc]
        with self.assertRaises(ValidationError):
            RunContext.model_validate({
                **context.model_dump(),
                "unexpected": True,
            })

    class runtime:
        def __enter__(self):
            self.temporary = tempfile.TemporaryDirectory(prefix="eidos-architecture-")
            root = Path(self.temporary.name)
            data = root / "data"
            workspace = root / "workspace"
            data.mkdir(mode=0o700)
            workspace.mkdir()
            self.store = SessionStore(data)
            self.store.initialize()
            self.session = self.store.create_session(str(workspace))
            return self.store, self.session, workspace

        def __exit__(self, *_error):
            self.store.close()
            self.temporary.cleanup()


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eidos_runtime.model.config import default_profile_snapshot  # noqa: E402
from eidos_runtime.model.client import (  # noqa: E402
    AssistantMessagePhase,
    ModelResponse,
    ScriptedModel,
)
from eidos_runtime.context.project_rules import ProjectRuleResolver  # noqa: E402
from eidos_runtime.db.storage import SessionStore  # noqa: E402
from eidos_runtime.model.instructions import InstructionResolver  # noqa: E402
from eidos_runtime.protocol.server import RuntimeServer  # noqa: E402
from eidos_runtime.runtime.contracts import RuntimeCancelled  # noqa: E402
from eidos_runtime.sandbox.sensitive import SensitiveScanner  # noqa: E402
from eidos_runtime.runtime.events import RuntimeEvents  # noqa: E402
from eidos_runtime.runtime.finalizer import RunFinalizer  # noqa: E402
from eidos_runtime.runtime.async_kernel import (  # noqa: E402
    AsyncTaskState,
    RuntimeAsyncKernel,
)
from eidos_runtime.runtime.resource_registry import RuntimeResourceKind  # noqa: E402
from eidos_runtime.runtime.supervisor import RuntimeShutdownTimeout  # noqa: E402
from eidos_runtime.runtime.state_machine import RuntimePhaseTracker  # noqa: E402


class _BlockingTitleModel:
    profile_snapshot = default_profile_snapshot("deepseek-v4-flash")

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def generate_title(self, _user_input: str, cancel: threading.Event) -> str:
        self.entered.set()
        while not self.release.is_set():
            if cancel.wait(0.01):
                raise RuntimeCancelled
        return "后台标题"


class _CancelEngine:
    def __init__(self, *_args, **_kwargs) -> None:
        pass

    def run(self, _run_id: str, cancel: threading.Event) -> None:
        cancel.wait()
        raise RuntimeCancelled


class _BlockingPluginCatalog:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def import_directory(self, _path: Path) -> dict[str, object]:
        self.entered.set()
        self.release.wait(1)
        return {
            "schemaVersion": 1,
            "id": "fixture-plugin",
            "name": "Fixture",
            "version": "1.0.0",
            "description": "Blocking fixture",
            "contentHash": "a" * 64,
            "enabled": True,
            "status": "installed",
            "installedAt": 1,
            "updatedAt": 1,
        }

    def cleanup_removed(self) -> None:
        pass


class AsyncTitleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-title-")
        root = Path(self.temporary.name)
        data = root / "data"
        self.workspace = root / "workspace"
        data.mkdir(mode=0o700)
        self.workspace.mkdir()
        self.model = _BlockingTitleModel()
        self.output = io.StringIO()
        self.server = RuntimeServer(self.output, data, self.model)
        self.server.store.initialize()
        self.server.model_config.initialize()
        self.server.sensitive = SensitiveScanner()
        self.server.initialized = True
        self.server.async_kernel = RuntimeAsyncKernel(
            resource_registry=self.server.supervisor.resources
        )
        self.server.async_kernel.start()
        self.server.supervisor.bind_async_kernel(self.server.async_kernel)
        self.server.supervisor.engine_factory = _CancelEngine
        self.session = self.server.store.create_session(str(self.workspace))

    def tearDown(self) -> None:
        self.model.release.set()
        try:
            self.server.close()
        except Exception:
            pass
        self.temporary.cleanup()

    def _start(self) -> float:
        started = time.monotonic()
        self.server.handle({
            "jsonrpc": "2.0",
            "id": "client-run",
            "method": "run/start",
            "params": {
                "sessionId": self.session["id"],
                "userInput": "请分析这个仓库",
                "modelId": "deepseek-v4-flash",
            },
        })
        return time.monotonic() - started

    def test_run_start_does_not_wait_for_title_generation(self) -> None:
        elapsed = self._start()
        message = json.loads(self.output.getvalue().splitlines()[0])
        self.assertIn("result", message)
        self.assertLess(elapsed, 0.1)
        self.assertTrue(self.model.entered.wait(1))

    def test_title_task_is_kernel_owned_without_a_dedicated_thread(self) -> None:
        self._start()
        self.assertTrue(self.model.entered.wait(1))

        kinds = {
            resource.kind
            for resource in self.server.supervisor.resources.active_resources()
        }
        self.assertIn(RuntimeResourceKind.MANAGED_TASK, kinds)
        self.assertIn(RuntimeResourceKind.ASYNC_REQUEST, kinds)
        self.assertIn(RuntimeResourceKind.ASYNC_TASK, kinds)
        self.assertFalse(any(
            thread.name.startswith(("eidos-title-", "eidos-plugin-import-"))
            for thread in threading.enumerate()
        ))

    def test_health_responds_while_title_generation_is_running(self) -> None:
        self._start()
        self.assertTrue(self.model.entered.wait(1))

        self.server.handle({
            "jsonrpc": "2.0",
            "id": "client-health",
            "method": "runtime/health",
            "params": {},
        })

        message = json.loads(self.output.getvalue().splitlines()[-1])
        self.assertEqual(message["id"], "client-health")
        self.assertEqual(message["result"]["state"], "ready")

    def test_shutdown_cancels_title_task(self) -> None:
        self._start()
        self.assertTrue(self.model.entered.wait(1))

        self.server.shutdown("client-shutdown", {})

        message = json.loads(self.output.getvalue().splitlines()[-1])
        self.assertEqual(message["id"], "client-shutdown")
        self.assertIn("result", message)
        self.assertFalse(self.server.supervisor.has_active_managed_tasks())

    def test_manual_title_is_not_overwritten_by_background_task(self) -> None:
        self._start()
        self.assertTrue(self.model.entered.wait(1))
        self.server.store.rename_session(self.session["id"], "人工标题")
        self.model.release.set()
        self.assertTrue(self.server.supervisor.wait_managed_tasks(1))

        session = self.server.store.read_session(self.session["id"])
        self.assertEqual(session["title"], "人工标题")

    def test_generated_title_event_is_projected_after_commit(self) -> None:
        self._start()
        self.assertTrue(self.model.entered.wait(1))
        self.model.release.set()
        self.assertTrue(self.server.supervisor.wait_managed_tasks(1))

        messages = [
            json.loads(line) for line in self.output.getvalue().splitlines()
        ]
        title_updates = [
            message for message in messages
            if message.get("method") == "session/titleUpdated"
        ]
        self.assertEqual(title_updates[-1]["params"]["title"], "后台标题")
        persisted = self.server.store.read_session(self.session["id"])
        self.assertEqual(persisted["title"], "后台标题")

    def test_plugin_import_copy_does_not_block_health(self) -> None:
        plugins = _BlockingPluginCatalog()
        self.server.plugins = plugins  # type: ignore[assignment]
        started = time.monotonic()
        self.server.handle({
            "jsonrpc": "2.0",
            "id": "client-plugin",
            "method": "plugin/import",
            "params": {"sourcePath": str(self.workspace)},
        })
        self.assertLess(time.monotonic() - started, 0.1)
        self.assertTrue(plugins.entered.wait(1))

        self.server.handle({
            "jsonrpc": "2.0",
            "id": "client-health-plugin",
            "method": "runtime/health",
            "params": {},
        })
        messages = [
            json.loads(line) for line in self.output.getvalue().splitlines()
        ]
        health = next(
            value for value in messages
            if value.get("id") == "client-health-plugin"
        )
        self.assertEqual(health["result"]["state"], "ready")
        plugins.release.set()
        self.assertTrue(self.server.supervisor.wait_managed_tasks(1))

    def test_managed_task_exception_is_owned_by_kernel_handle(self) -> None:
        def fail(_cancel: threading.Event) -> None:
            raise ValueError("managed failure")

        self.assertTrue(self.server.supervisor.start_managed_task("title", fail))
        self.assertTrue(self.server.supervisor.wait_managed_tasks(1))

        diagnostic = self.server.async_kernel.recent_task_diagnostics()[-1]
        self.assertTrue(diagnostic.owner_id.startswith("managed:"))
        self.assertEqual(diagnostic.state, AsyncTaskState.FAILED)
        self.assertEqual(diagnostic.diagnostic_code, "ASYNC_TASK_FAILED")

    def test_immediately_finishing_managed_task_leaves_no_registration_race(self) -> None:
        completed = threading.Event()

        def finish_immediately(_cancel: threading.Event) -> None:
            completed.set()

        self.assertTrue(
            self.server.supervisor.start_managed_task("immediate", finish_immediately)
        )
        self.assertTrue(completed.wait(1))
        self.assertTrue(self.server.supervisor.wait_managed_tasks(1))
        self.assertFalse(self.server.supervisor.has_active_managed_tasks())
        self.assertFalse(any(
            resource.kind is RuntimeResourceKind.MANAGED_TASK
            for resource in self.server.supervisor.resources.active_resources()
        ))

    def test_reconfiguration_rejects_active_managed_task(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        def block(_cancel: threading.Event) -> None:
            entered.set()
            release.wait(1)

        self.assertTrue(self.server.supervisor.start_managed_task("title", block))
        self.assertTrue(entered.wait(1))
        self.assertFalse(self.server.supervisor.begin_reconfiguration())
        release.set()
        self.assertTrue(self.server.supervisor.wait_managed_tasks(1))

    def test_noncooperative_managed_task_keeps_truthful_shutdown_timeout(self) -> None:
        entered = threading.Event()
        release = threading.Event()

        def block(_cancel: threading.Event) -> None:
            entered.set()
            release.wait(1)

        self.server.supervisor.shutdown_timeout = 0.01
        self.assertTrue(self.server.supervisor.start_managed_task("title", block))
        self.assertTrue(entered.wait(1))
        try:
            with self.assertRaisesRegex(
                RuntimeShutdownTimeout, "RUNTIME_SHUTDOWN_TIMEOUT"
            ):
                self.server.supervisor.shutdown()
            kinds = {
                resource.kind
                for resource in self.server.supervisor.resources.active_resources()
            }
            self.assertIn(RuntimeResourceKind.MANAGED_TASK, kinds)
            self.assertIn(RuntimeResourceKind.ASYNC_TASK, kinds)
        finally:
            release.set()
            self.assertTrue(self.server.supervisor.wait_managed_tasks(1))


class _FailingFinalizationModel(ScriptedModel):
    def complete(self, *_args, **_kwargs):
        raise OSError("model failed")


class FinalizationPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-finalization-")
        root = Path(self.temporary.name)
        self.data = root / "data"
        self.workspace = root / "workspace"
        self.data.mkdir(mode=0o700)
        self.workspace.mkdir()
        self.store = SessionStore(self.data)
        self.store.initialize()
        self.session = self.store.create_session(str(self.workspace))

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _finalize(self, model):
        run, _ = self.store.create_run(self.session["id"], "finalize")
        rules = ProjectRuleResolver().resolve(self.workspace, self.workspace)
        instructions = InstructionResolver().resolve(
            rule_snapshot=rules,
            selected_skill_context=(),
        )
        outcome = RunFinalizer(
            self.store,
            model,
            RuntimeEvents(lambda _message: None),
            SensitiveScanner(),
            RuntimePhaseTracker(),
        ).finalize(
            run["id"],
            (),
            "context_still_over_budget",
            threading.Event(),
            instructions=instructions,
        )
        return run, outcome, instructions

    def test_successful_finalization_persists_completed_attempt(self) -> None:
        run, outcome, _instructions = self._finalize(
            ScriptedModel([ModelResponse(
                text="final answer",
                phase=AssistantMessagePhase.FINAL_ANSWER,
                finish_reason="stop",
            )])
        )

        attempts = self.store.read_finalization_attempts(run["id"])
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["status"], "completed")
        self.assertEqual(attempts[0]["outputItemId"], outcome.item["id"])
        self.assertEqual(outcome.item["content"], "final answer")

    def test_undeclared_finalization_response_is_repaired_before_persistence(
        self,
    ) -> None:
        model = ScriptedModel([
            ModelResponse(
                text="I will provide the final answer next.",
                phase=AssistantMessagePhase.UNKNOWN,
            ),
            ModelResponse(
                text="bounded final answer",
                phase=AssistantMessagePhase.FINAL_ANSWER,
                finish_reason="stop",
            ),
        ])

        run, outcome, _instructions = self._finalize(model)

        attempts = self.store.read_finalization_attempts(run["id"])
        self.assertEqual(len(model.contexts), 2)
        self.assertEqual(
            model.contexts[1][-1],
            {"type": "protocol_error", "code": "undeclared_final_response"},
        )
        self.assertEqual(attempts[0]["status"], "completed")
        self.assertEqual(outcome.item["content"], "bounded final answer")
        snapshot = self.store.read_session_snapshot(self.session["id"])
        self.assertNotIn("I will provide", str(snapshot))

    def test_repeated_undeclared_finalization_response_fails_without_item(
        self,
    ) -> None:
        unknown = ModelResponse(
            text="I will provide the final answer next.",
            phase=AssistantMessagePhase.UNKNOWN,
        )

        run, outcome, _instructions = self._finalize(
            ScriptedModel([unknown, unknown])
        )

        attempts = self.store.read_finalization_attempts(run["id"])
        self.assertIsNone(outcome.item)
        self.assertEqual(outcome.failure_reason, "finalization_protocol_error")
        self.assertEqual(attempts[0]["status"], "model_failed")
        self.assertEqual(
            attempts[0]["errorCode"],
            "finalization_protocol_error",
        )

    def test_model_failure_is_explained_without_assistant_item(self) -> None:
        run, outcome, _instructions = self._finalize(_FailingFinalizationModel([]))

        attempts = self.store.read_finalization_attempts(run["id"])
        self.assertIsNone(outcome.item)
        self.assertEqual(attempts[0]["status"], "model_failed")
        self.assertEqual(
            attempts[0]["errorCode"], "finalization_model_failed"
        )

    def test_recovery_marks_running_finalization_interrupted_without_retry(self) -> None:
        run, _ = self.store.create_run(self.session["id"], "crash")
        self.store.begin_finalization_attempt_committed(
            run["id"], model_id="deepseek-v4-flash"
        )
        self.store.close()
        self.store = SessionStore(self.data)

        self.store.initialize()

        attempts = self.store.read_finalization_attempts(run["id"])
        self.assertEqual(attempts[0]["status"], "interrupted")
        self.assertEqual(attempts[0]["errorCode"], "finalization_interrupted")
        self.assertEqual(self.store.read_run(run["id"])["status"], "interrupted")

    def test_finalization_policy_is_instruction_while_stop_reason_remains_data(self) -> None:
        (self.workspace / "EIDOS.md").write_text(
            "Preserve this project rule during finalization.", encoding="utf-8"
        )
        model = ScriptedModel([ModelResponse(
            text="bounded final answer",
            phase=AssistantMessagePhase.FINAL_ANSWER,
            finish_reason="stop",
        )])

        _run, _outcome, base = self._finalize(model)

        self.assertEqual(model.allow_tools_history, [False])
        self.assertEqual(model.tool_definitions_history, [()])
        self.assertEqual(len(model.instructions_history), 1)
        final_instructions = model.instructions_history[0]
        self.assertIn("system-safety", final_instructions)
        self.assertIn("runtime-policy", final_instructions)
        self.assertIn("project-rule:EIDOS.md", final_instructions)
        self.assertIn("Preserve this project rule", final_instructions)
        self.assertIn("finalization-policy", final_instructions)
        self.assertIn("Do not call tools", final_instructions)
        for layer in base.layers:
            self.assertIn(layer.id, final_instructions)
            self.assertIn(layer.content, final_instructions)

        self.assertEqual(
            model.contexts[0][-1],
            {
                "type": "finalization",
                "toolsAllowed": False,
                "stopReason": "context_still_over_budget",
            },
        )
        self.assertNotIn("Do not call tools", json.dumps(model.contexts[0]))


if __name__ == "__main__":
    unittest.main()

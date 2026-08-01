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
    ModelResponse,
    ScriptedModel,
)
from eidos_runtime.db.storage import SessionStore  # noqa: E402
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
        return {"id": "fixture-plugin"}

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
        self.server.start_run("client-run", {
            "sessionId": self.session["id"],
            "userInput": "请分析这个仓库",
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
        self.server.import_plugin("client-plugin", {
            "sourcePath": str(self.workspace),
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
        workspace = root / "workspace"
        self.data.mkdir(mode=0o700)
        workspace.mkdir()
        self.store = SessionStore(self.data)
        self.store.initialize()
        self.session = self.store.create_session(str(workspace))

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _finalize(self, model):
        run, _ = self.store.create_run(self.session["id"], "finalize")
        outcome = RunFinalizer(
            self.store,
            model,
            RuntimeEvents(lambda _message: None),
            SensitiveScanner(),
            RuntimePhaseTracker(),
        ).finalize(run["id"], (), "max_total_steps", threading.Event())
        return run, outcome

    def test_successful_finalization_persists_completed_attempt(self) -> None:
        run, outcome = self._finalize(
            ScriptedModel([ModelResponse(text="final answer")])
        )

        attempts = self.store.read_finalization_attempts(run["id"])
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["status"], "completed")
        self.assertEqual(attempts[0]["outputItemId"], outcome.item["id"])

    def test_model_failure_is_explained_without_assistant_item(self) -> None:
        run, outcome = self._finalize(_FailingFinalizationModel([]))

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


if __name__ == "__main__":
    unittest.main()

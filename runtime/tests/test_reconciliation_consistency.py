from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.db.errors import (  # noqa: E402
    ReconciliationRequiredError,
)
from eidos_runtime.db.storage import SessionStore  # noqa: E402
from eidos_runtime.extensions.plugins import PluginCatalog  # noqa: E402
from eidos_runtime.extensions.skills import SkillCatalog  # noqa: E402
from eidos_runtime.model.client import (  # noqa: E402
    ModelResponse,
    ModelToolCall,
    ScriptedModel,
)
from eidos_runtime.runtime.approval import ApprovalDecision  # noqa: E402
from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel  # noqa: E402
from eidos_runtime.runtime.engine import RuntimeEngine  # noqa: E402
from eidos_runtime.runtime.resource_registry import ResourceRegistry  # noqa: E402
from eidos_runtime.runtime.reconciliation import (  # noqa: E402
    ReconciliationDisposition,
    classify_shell_reconciliation,
)
from eidos_runtime.runtime.tool_orchestrator import OrchestratorResult  # noqa: E402
from eidos_runtime.runtime.tool_runtime import ToolCallRuntime  # noqa: E402
from eidos_runtime.sandbox.workspace_manifest import (  # noqa: E402
    WorkspaceManifest,
)
from eidos_runtime.tools.workspace import WorkspacePathError  # noqa: E402


class _StatusRecordingModel(ScriptedModel):
    def __init__(self, store: SessionStore, run_id: str, responses) -> None:
        super().__init__(responses)
        self.store = store
        self.run_id = run_id
        self.run_statuses: list[str] = []

    def complete(self, *args, **kwargs):
        self.run_statuses.append(self.store.read_run(self.run_id)["status"])
        return super().complete(*args, **kwargs)


class ReconciliationConsistencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-reconciliation-")
        root = Path(self.temporary.name)
        data = root / "data"
        workspace = root / "workspace"
        data.mkdir(mode=0o700)
        workspace.mkdir()
        self.store = SessionStore(data)
        self.store.initialize()
        session = self.store.create_session(str(workspace))
        self.run, _ = self.store.create_run(session["id"], "reconcile")

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_explicit_top_level_false_wins_over_nonzero_exit_code(self) -> None:
        item = self.store.create_tool_item(
            self.run["id"], 0, 0, "shell", "run_shell", "{}"
        )
        self.store.begin_durable_intent(
            item["id"], preconditions={}, approval_required=False
        )

        self.store.complete_tool_item(
            item["id"],
            json.dumps({
                "outcome": "error",
                "code": "nonzero_exit",
                "summary": "Command failed",
                "data": {},
                "sideEffectsMayExist": True,
                "reconciliationRequired": False,
            }),
            item_status="failed",
            tool_status="completed",
        )

        self.assertFalse(self.store.side_effects_blocked(self.run["id"]))
        self.assertEqual(
            self.store.connection.execute(
                "SELECT status FROM durable_intents WHERE tool_call_id = ?",
                (item["toolCall"]["id"],),
            ).fetchone()[0],
            "completed",
        )

    def test_explicit_top_level_true_keeps_reconciliation_barrier(self) -> None:
        item = self.store.create_tool_item(
            self.run["id"], 0, 0, "shell", "run_shell", "{}"
        )

        self.store.complete_tool_item(
            item["id"],
            json.dumps({
                "outcome": "error",
                "code": "nonzero_exit",
                "summary": "Command failed",
                "data": {},
                "sideEffectsMayExist": True,
                "reconciliationRequired": True,
            }),
            item_status="failed",
            tool_status="failed",
        )

        self.assertTrue(self.store.side_effects_blocked(self.run["id"]))

    def test_known_shell_exit_without_explicit_flag_does_not_open_barrier(self) -> None:
        item = self.store.create_tool_item(
            self.run["id"], 0, 0, "shell", "run_shell", "{}"
        )

        self.store.complete_tool_item(
            item["id"],
            json.dumps({
                "outcome": "error",
                "code": "shell_exit_nonzero",
                "summary": "Command failed",
                "data": {"exitCode": 7, "termination": "exit"},
                "sideEffectsMayExist": True,
            }),
            item_status="failed",
            tool_status="completed",
        )

        self.assertFalse(self.store.side_effects_blocked(self.run["id"]))

    def test_explicit_reconciliation_enters_read_only_mode(self) -> None:
        item = self.store.create_tool_item(
            self.run["id"], 0, 0, "shell", "run_shell", "{}"
        )
        self.store.complete_tool_item(
            item["id"],
            json.dumps({
                "outcome": "error",
                "code": "outcome_unknown",
                "summary": "Command outcome is unknown",
                "data": {},
                "sideEffectsMayExist": True,
                "reconciliationRequired": True,
            }),
            item_status="failed",
            tool_status="failed",
        )

        disposition = classify_shell_reconciliation(
            {
                "outcome": "error",
                "code": "outcome_unknown",
                "reconciliationRequired": True,
            },
            manifest_before_complete=True,
            manifest_after_complete=True,
            refresh_error_code=None,
        )

        self.assertEqual(self.store.read_run(self.run["id"])["status"], "running")
        self.assertIs(
            disposition,
            ReconciliationDisposition.CONTINUE_READ_ONLY,
        )

    def test_reconciliation_barrier_rolls_back_success_completion(self) -> None:
        step_index = self.store.increment_model_step(self.run["id"])
        assistant = self.store.create_assistant_item(self.run["id"], step_index)
        connection = self.store.connection
        connection.execute(
            "UPDATE runs SET reconciliation_required = 1, side_effects_may_exist = 1 "
            "WHERE id = ?",
            (self.run["id"],),
        )
        connection.commit()
        event_count = connection.execute(
            "SELECT COUNT(*) FROM events WHERE run_id = ?",
            (self.run["id"],),
        ).fetchone()[0]

        with self.assertRaisesRegex(
            ReconciliationRequiredError, "reconciliation_required"
        ):
            self.store.complete_assistant_and_run_committed(
                assistant["id"], self.run["id"]
            )

        self.assertEqual(self.store.read_run(self.run["id"])["status"], "running")
        self.assertEqual(
            self.store.read_item(assistant["id"])["status"], "in_progress"
        )
        self.assertEqual(
            connection.execute(
                "SELECT status FROM execution_segments WHERE run_id = ?",
                (self.run["id"],),
            ).fetchone()[0],
            "running",
        )
        self.assertEqual(
            connection.execute(
                "SELECT COUNT(*) FROM events WHERE run_id = ?",
                (self.run["id"],),
            ).fetchone()[0],
            event_count,
        )

    def test_engine_keeps_run_active_until_read_only_verification(self) -> None:
        connection = self.store.connection
        connection.execute(
            "UPDATE runs SET reconciliation_required = 1, side_effects_may_exist = 1 "
            "WHERE id = ?",
            (self.run["id"],),
        )
        connection.commit()
        pending_epoch = self.store.context_projection_facts(
            self.run["id"]
        ).reconciliation_epoch

        model = ScriptedModel([
            ModelResponse(text="answer"),
            ModelResponse(tool_calls=(ModelToolCall(
                "observe-workspace", "list_files", {}
            ),)),
            ModelResponse(text="verified answer"),
        ])
        with patch(
            "eidos_runtime.tools.runtime_workspace.ToolExecutor.refresh_workspace_index",
            return_value=WorkspaceManifest((), True, False),
        ) as refresh:
            RuntimeEngine(
                self.store,
                model,
                lambda _message: None,
            ).run(self.run["id"], threading.Event())

        persisted = self.store.read_run(self.run["id"])
        self.assertEqual(persisted["status"], "succeeded")
        self.assertFalse(self.store.side_effects_blocked(self.run["id"]))
        self.assertFalse(
            self.store.context_projection_facts(
                self.run["id"]
            ).reconciliation_required
        )
        self.assertGreaterEqual(persisted["modelStepCount"], 3)
        self.assertEqual(
            self.store.context_projection_facts(
                self.run["id"]
            ).reconciliation_epoch,
            pending_epoch + 1,
        )
        self.assertGreaterEqual(refresh.call_count, 1)
        self.assertTrue(any(
            "Reconciliation is still required."
            in context_item.get("content", "")
            for context_item in model.contexts[1]
            if context_item.get("type") == "user"
        ))

    def test_shell_exit_nonzero_does_not_restrict_follow_up_tools(self) -> None:
        model = ScriptedModel([
            ModelResponse(tool_calls=(ModelToolCall(
                "shell-attempt",
                "run_shell",
                {"command": "false", "yieldTimeMs": 5000},
            ),)),
            ModelResponse(tool_calls=(ModelToolCall(
                "observe-workspace",
                "list_files",
                {},
            ),)),
            ModelResponse(text="The workspace was inspected."),
        ])
        shell_result = {
            "schemaVersion": 1,
            "toolContractVersion": 1,
            "toolName": "run_shell",
            "outcome": "error",
            "code": "shell_exit_nonzero",
            "summary": "Command exited with a non-zero status",
            "data": {
                "exitCode": 1,
                "stdout": "command output\n",
                "stderr": "command failed\n",
                "truncated": False,
                "termination": "exit",
                "durationMs": 1,
            },
            "sideEffectsMayExist": True,
        }

        with (
            patch("eidos_runtime.runtime.tool_runtime.is_seatbelt_ready", return_value=True),
            patch(
                "eidos_runtime.runtime.shell_process_manager.ShellProcessManager.start",
                return_value=shell_result,
            ),
            patch(
                "eidos_runtime.tools.runtime_workspace.ToolExecutor.refresh_workspace_index",
                side_effect=WorkspacePathError("WORKSPACE_INDEX_INCOMPLETE"),
            ),
        ):
            RuntimeEngine(
                self.store,
                model,
                lambda _message: None,
                shell_available=True,
            ).run(self.run["id"], threading.Event())

        persisted = self.store.read_run(self.run["id"])
        shell = self.store.connection.execute(
            "SELECT status, result_json FROM tool_calls WHERE tool_name = ?",
            ("run_shell",),
        ).fetchone()
        assert shell is not None
        self.assertEqual(shell["status"], "completed")
        shell_result = json.loads(shell["result_json"])
        self.assertFalse(shell_result["reconciliationRequired"])
        shell_calls = self.store.connection.execute(
            "SELECT COUNT(*) FROM tool_calls WHERE tool_name = ?",
            ("run_shell",),
        ).fetchone()[0]
        self.assertEqual(shell_calls, 1)
        self.assertEqual(persisted["status"], "succeeded")
        self.assertEqual(len(model.contexts), 3)
        shell_context = next(
            item
            for item in model.contexts[1]
            if item.get("type") == "tool_result" and item.get("name") == "run_shell"
        )
        context_result = json.loads(shell_context["result"])
        self.assertEqual(context_result["data"]["exitCode"], 1)
        self.assertEqual(context_result["data"]["stdout"], "command output\n")
        self.assertEqual(context_result["data"]["stderr"], "command failed\n")
        self.assertEqual(
            any(
                item.get("type") == "tool_result"
                and item.get("name") == "run_shell"
                for item in model.contexts[1]
            ),
            True,
        )
        self.assertIn(
            "list_files",
            {definition.name for definition in model.tool_definitions_history[1]},
        )
        self.assertIn(
            "run_shell",
            {definition.name for definition in model.tool_definitions_history[1]},
        )
        self.assertFalse(persisted.get("reconciliationRequired", False))
        cleared = self.store.connection.execute(
            "SELECT payload_json FROM events "
            "WHERE run_id = ? AND event_type = 'reconciliation.cleared'",
            (self.run["id"],),
        ).fetchall()
        self.assertEqual(len(cleared), 0)

    def test_first_shell_with_incomplete_baseline_does_not_enter_read_only_mode(self) -> None:
        model = ScriptedModel([
            ModelResponse(tool_calls=(ModelToolCall(
                "shell-attempt",
                "run_shell",
                {"command": "false", "yieldTimeMs": 5000},
            ),)),
            ModelResponse(tool_calls=(ModelToolCall(
                "observe-workspace",
                "list_files",
                {},
            ),)),
            ModelResponse(text="The failed command was inspected."),
        ])
        shell_result = {
            "schemaVersion": 1,
            "toolContractVersion": 1,
            "toolName": "run_shell",
            "outcome": "error",
            "code": "shell_exit_nonzero",
            "summary": "Command exited with a non-zero status",
            "data": {
                "exitCode": 1,
                "stdout": "",
                "stderr": "",
                "truncated": False,
                "termination": "exit",
                "durationMs": 1,
            },
            "sideEffectsMayExist": True,
        }
        incomplete_before = WorkspaceManifest((), False, True)
        complete_after = WorkspaceManifest((), True, False)

        with (
            patch("eidos_runtime.runtime.tool_runtime.is_seatbelt_ready", return_value=True),
            patch(
                "eidos_runtime.runtime.shell_process_manager.ShellProcessManager.start",
                return_value=shell_result,
            ),
            patch(
                "eidos_runtime.sandbox.workspace_index.WorkspaceIndex.manifest",
                return_value=incomplete_before,
            ),
            patch(
                "eidos_runtime.tools.runtime_workspace.ToolExecutor.refresh_workspace_index",
                return_value=complete_after,
            ),
        ):
            RuntimeEngine(
                self.store,
                model,
                lambda _message: None,
                shell_available=True,
            ).run(self.run["id"], threading.Event())

        persisted = self.store.read_run(self.run["id"])
        self.assertEqual(persisted["status"], "succeeded")
        self.assertFalse(persisted.get("reconciliationRequired", False))
        self.assertEqual(len(model.contexts), 3)
        self.assertIn(
            "run_shell",
            {definition.name for definition in model.tool_definitions_history[1]},
        )
        self.assertEqual(
            self.store.connection.execute(
                "SELECT COUNT(*) FROM tool_calls WHERE tool_name = 'run_shell'",
            ).fetchone()[0],
            1,
        )
        self.assertEqual(
            self.store.connection.execute(
                "SELECT COUNT(*) FROM events "
                "WHERE run_id = ? AND event_type = 'reconciliation.cleared'",
                (self.run["id"],),
            ).fetchone()[0],
            0,
        )

    def test_shell_exit_nonzero_does_not_stop_an_independent_shell_in_the_same_batch(
        self,
    ) -> None:
        model = ScriptedModel([
            ModelResponse(tool_calls=(
                ModelToolCall(
                    "first-shell",
                    "run_shell",
                    {"command": "exit 1", "yieldTimeMs": 5000},
                ),
                ModelToolCall(
                    "second-shell",
                    "run_shell",
                    {"command": "printf second-shell", "yieldTimeMs": 5000},
                ),
            )),
            ModelResponse(text="Both shell results were returned."),
        ])
        shell_results = [
            {
                "schemaVersion": 1,
                "toolContractVersion": 1,
                "toolName": "run_shell",
                "outcome": "error",
                "code": "shell_exit_nonzero",
                "summary": "Command failed with exit code 1",
                "data": {
                    "exitCode": 1,
                    "stdout": "first output\n",
                    "stderr": "first failure\n",
                    "truncated": False,
                    "termination": "exit",
                    "durationMs": 1,
                },
                "sideEffectsMayExist": True,
            },
            {
                "schemaVersion": 1,
                "toolContractVersion": 1,
                "toolName": "run_shell",
                "outcome": "success",
                "code": "ok",
                "summary": "Command completed",
                "data": {
                    "exitCode": 0,
                    "stdout": "second-shell",
                    "stderr": "",
                    "truncated": False,
                    "termination": "exit",
                    "durationMs": 1,
                },
                "sideEffectsMayExist": True,
            },
        ]
        invocations: list[str] = []

        def fake_start(launch: object, **_kwargs: object) -> dict[str, object]:
            argv = getattr(launch, "argv")
            command = argv[-1]
            assert isinstance(command, str)
            invocations.append(command)
            return shell_results[len(invocations) - 1]

        with (
            patch("eidos_runtime.runtime.tool_runtime.is_seatbelt_ready", return_value=True),
            patch(
                "eidos_runtime.runtime.shell_process_manager.ShellProcessManager.start",
                side_effect=fake_start,
            ),
            patch(
                "eidos_runtime.tools.runtime_workspace.ToolExecutor.refresh_workspace_index",
                side_effect=WorkspacePathError("WORKSPACE_INDEX_INCOMPLETE"),
            ),
        ):
            RuntimeEngine(
                self.store,
                model,
                lambda _message: None,
                shell_available=True,
            ).run(self.run["id"], threading.Event())

        self.assertEqual(invocations, ["exit 1", "printf second-shell"])
        self.assertEqual(self.store.read_run(self.run["id"])["status"], "succeeded")
        rows = self.store.connection.execute(
            """
            SELECT tool_calls.status AS tool_status, items.status AS item_status,
                   tool_calls.result_json
            FROM tool_calls JOIN items ON items.id = tool_calls.item_id
            WHERE items.run_id = ? AND tool_calls.tool_name = 'run_shell'
            ORDER BY tool_calls.creation_seq
            """,
            (self.run["id"],),
        ).fetchall()
        self.assertEqual(
            [(row["item_status"], row["tool_status"]) for row in rows],
            [("failed", "completed"), ("completed", "completed")],
        )
        self.assertEqual(json.loads(rows[0]["result_json"])["code"], "shell_exit_nonzero")
        self.assertEqual(json.loads(rows[1]["result_json"])["code"], "ok")

    def _assert_shell_refresh_error_keeps_run_active(
        self, error_code: str
    ) -> None:
        model = _StatusRecordingModel(self.store, self.run["id"], [
            ModelResponse(tool_calls=(ModelToolCall(
                "shell-attempt",
                "run_shell",
                {"command": "false", "yieldTimeMs": 5000},
            ),)),
            ModelResponse(tool_calls=(ModelToolCall(
                "observe-workspace", "list_files", {}
            ),)),
            ModelResponse(text="must not complete before verification"),
        ])
        shell_result = {
            "schemaVersion": 1,
            "toolContractVersion": 1,
            "toolName": "run_shell",
            "outcome": "error",
            "code": "shell_exit_nonzero",
            "summary": "Command exited with a non-zero status",
            "data": {
                "exitCode": 1,
                "stdout": "",
                "stderr": "",
                "truncated": False,
                "termination": "exit",
                "durationMs": 1,
            },
            "sideEffectsMayExist": True,
        }

        with (
            patch("eidos_runtime.runtime.tool_runtime.is_seatbelt_ready", return_value=True),
            patch(
                "eidos_runtime.runtime.shell_process_manager.ShellProcessManager.start",
                return_value=shell_result,
            ),
            patch(
                "eidos_runtime.tools.runtime_workspace.ToolExecutor.refresh_workspace_index",
                side_effect=WorkspacePathError(error_code),
            ),
        ):
            RuntimeEngine(
                self.store,
                model,
                lambda _message: None,
                shell_available=True,
            ).run(self.run["id"], threading.Event())

        persisted = self.store.read_run(self.run["id"])
        self.assertNotEqual(persisted["status"], "interrupted")
        self.assertTrue(self.store.side_effects_blocked(self.run["id"]))
        self.assertGreaterEqual(len(model.run_statuses), 2)
        self.assertEqual(model.run_statuses[1], "running")
        read_only_tools = {
            definition.name for definition in model.tool_definitions_history[1]
        }
        self.assertIn("run_shell", read_only_tools)
        self.assertIn("apply_patch", read_only_tools)
        shell_calls = self.store.connection.execute(
            "SELECT COUNT(*) FROM tool_calls WHERE tool_name = ?",
            ("run_shell",),
        ).fetchone()[0]
        self.assertEqual(shell_calls, 1)
        row = self.store.connection.execute(
            "SELECT result_json FROM tool_calls WHERE tool_name = ?",
            ("run_shell",),
        ).fetchone()
        assert row is not None
        self.assertTrue(json.loads(row["result_json"])["reconciliationRequired"])

    def test_workspace_identity_refresh_error_keeps_run_active(self) -> None:
        self._assert_shell_refresh_error_keeps_run_active(
            "workspace_identity_changed"
        )

    def test_unsupported_workspace_hardlink_refresh_error_keeps_run_active(self) -> None:
        self._assert_shell_refresh_error_keeps_run_active(
            "unsupported_workspace_hardlink"
        )

    def test_unsupported_workspace_entry_refresh_error_keeps_run_active(self) -> None:
        self._assert_shell_refresh_error_keeps_run_active(
            "unsupported_workspace_entry"
        )

    def _assert_default_seatbelt_shell_keeps_run_active(
        self, *, code: str, termination: str, exit_code: int | None
    ) -> None:
        model = _StatusRecordingModel(self.store, self.run["id"], [
            ModelResponse(tool_calls=(ModelToolCall(
                "shell-attempt",
                "run_shell",
                {"command": "sleep 60", "yieldTimeMs": 1000},
            ),)),
            ModelResponse(tool_calls=(ModelToolCall(
                "observe-workspace", "list_files", {}
            ),)),
            ModelResponse(text="must not complete before verification"),
        ])
        shell_result = {
            "schemaVersion": 1,
            "toolContractVersion": 1,
            "toolName": "run_shell",
            "outcome": "error",
            "code": code,
            "summary": f"Command ended with {termination}",
            "data": {
                "exitCode": exit_code,
                "stdout": "",
                "stderr": "",
                "truncated": False,
                "termination": termination,
                "durationMs": 1,
            },
            "sideEffectsMayExist": True,
            "reconciliationRequired": True,
        }

        with (
            patch("eidos_runtime.runtime.tool_runtime.is_seatbelt_ready", return_value=True),
            patch(
                "eidos_runtime.runtime.shell_process_manager.ShellProcessManager.start",
                return_value=shell_result,
            ),
            patch(
                "eidos_runtime.tools.runtime_workspace.ToolExecutor.refresh_workspace_index",
                side_effect=[
                    WorkspacePathError("WORKSPACE_INDEX_INCOMPLETE"),
                    WorkspacePathError("WORKSPACE_INDEX_INCOMPLETE"),
                    WorkspaceManifest((), True, False),
                ],
            ),
        ):
            RuntimeEngine(
                self.store,
                model,
                lambda _message: None,
                shell_available=True,
            ).run(self.run["id"], threading.Event())

        persisted = self.store.read_run(self.run["id"])
        self.assertNotEqual(persisted["status"], "interrupted")
        self.assertTrue(persisted["sideEffectsMayExist"])
        self.assertFalse(self.store.side_effects_blocked(self.run["id"]))
        self.assertFalse(
            self.store.context_projection_facts(
                self.run["id"]
            ).reconciliation_required
        )
        self.assertGreaterEqual(len(model.run_statuses), 2)
        self.assertEqual(model.run_statuses[1], "running")
        read_only_tools = {
            definition.name for definition in model.tool_definitions_history[1]
        }
        self.assertIn("run_shell", read_only_tools)
        self.assertIn("apply_patch", read_only_tools)
        calls = self.store.connection.execute(
            "SELECT COUNT(*) FROM tool_calls WHERE tool_name = ?",
            ("run_shell",),
        ).fetchone()[0]
        self.assertEqual(calls, 1)
        row = self.store.connection.execute(
            "SELECT result_json FROM tool_calls WHERE tool_name = ?",
            ("run_shell",),
        ).fetchone()
        assert row is not None
        result = json.loads(row["result_json"])
        self.assertEqual(result["code"], code)
        self.assertEqual(result["data"]["termination"], termination)
        self.assertTrue(result["data"]["sandboxed"])
        self.assertEqual(result["data"]["sandboxPermissions"], "use_default")

    def test_default_seatbelt_shell_timeout_keeps_run_active(self) -> None:
        self._assert_default_seatbelt_shell_keeps_run_active(
            code="timeout", termination="timeout", exit_code=None
        )

    def test_default_seatbelt_shell_background_process_keeps_run_active(self) -> None:
        self._assert_default_seatbelt_shell_keeps_run_active(
            code="background_process", termination="background_process", exit_code=0
        )

    def test_shell_reconciliation_without_trusted_sandbox_metadata_keeps_run_active(self) -> None:
        model = _StatusRecordingModel(self.store, self.run["id"], [
            ModelResponse(tool_calls=(ModelToolCall(
                "shell-attempt",
                "run_shell",
                {"command": "false", "yieldTimeMs": 5000},
            ),)),
            ModelResponse(tool_calls=(ModelToolCall(
                "observe-workspace", "list_files", {}
            ),)),
            ModelResponse(text="must not complete before verification"),
        ])
        incomplete_result = {
            "schemaVersion": 1,
            "toolContractVersion": 1,
            "toolName": "run_shell",
            "outcome": "error",
            "code": "shell_exit_nonzero",
            "summary": "Command outcome is uncertain",
            "data": {
                "exitCode": 1,
                "stdout": "",
                "stderr": "",
                "truncated": False,
                "termination": "exit",
                "durationMs": 1,
                "workspaceChanged": False,
            },
            "sideEffectsMayExist": True,
            "reconciliationRequired": True,
        }

        def return_incomplete_result(*_args: object, **kwargs: object) -> OrchestratorResult:
            authorize = kwargs["authorize_without_approval"]
            assert callable(authorize)
            authorize()
            return OrchestratorResult(
                result=incomplete_result,
                attempt_count=1,
                escalated=False,
            )

        with (
            patch("eidos_runtime.runtime.tool_runtime.is_seatbelt_ready", return_value=True),
            patch(
                "eidos_runtime.runtime.tool_runtime.ToolOrchestrator.run",
                side_effect=return_incomplete_result,
            ),
            patch(
                "eidos_runtime.tools.runtime_workspace.ToolExecutor.refresh_workspace_index",
                side_effect=[
                    WorkspacePathError("WORKSPACE_INDEX_INCOMPLETE"),
                    WorkspaceManifest((), True, False),
                ],
            ),
        ):
            RuntimeEngine(
                self.store,
                model,
                lambda _message: None,
                shell_available=True,
            ).run(self.run["id"], threading.Event())

        persisted = self.store.read_run(self.run["id"])
        self.assertNotEqual(persisted["status"], "interrupted")
        self.assertFalse(self.store.side_effects_blocked(self.run["id"]))
        self.assertFalse(
            self.store.context_projection_facts(
                self.run["id"]
            ).reconciliation_required
        )
        self.assertGreaterEqual(len(model.run_statuses), 2)
        self.assertEqual(model.run_statuses[1], "running")
        read_only_tools = {
            definition.name for definition in model.tool_definitions_history[1]
        }
        self.assertIn("run_shell", read_only_tools)
        self.assertIn("apply_patch", read_only_tools)
        row = self.store.connection.execute(
            "SELECT result_json FROM tool_calls WHERE tool_name = ?",
            ("run_shell",),
        ).fetchone()
        assert row is not None
        result = json.loads(row["result_json"])
        self.assertTrue(result["reconciliationRequired"])
        self.assertNotIn("sandboxed", result["data"])
        self.assertNotIn("effectivePermissionsSummary", result["data"])

    def _assert_permissioned_shell_keeps_run_active(
        self, arguments: dict[str, object], expected_mode: str
    ) -> None:
        model = _StatusRecordingModel(self.store, self.run["id"], [
            ModelResponse(tool_calls=(ModelToolCall(
                "permissioned-shell",
                "run_shell",
                arguments,
            ),)),
            ModelResponse(tool_calls=(ModelToolCall(
                "observe-workspace", "list_files", {}
            ),)),
            ModelResponse(text="must not complete before verification"),
        ])
        shell_result = {
            "schemaVersion": 1,
            "toolContractVersion": 1,
            "toolName": "run_shell",
            "outcome": "error",
            "code": "shell_exit_nonzero",
            "summary": "Command failed after the permissioned attempt",
            "data": {
                "exitCode": 1,
                "stdout": "",
                "stderr": "",
                "truncated": False,
                "termination": "exit",
                "durationMs": 1,
            },
            "sideEffectsMayExist": True,
            "reconciliationRequired": True,
        }
        approvals: list[dict[str, object]] = []

        with (
            patch("eidos_runtime.runtime.tool_runtime.is_seatbelt_ready", return_value=True),
            patch(
                "eidos_runtime.runtime.shell_process_manager.ShellProcessManager.start",
                return_value=shell_result,
            ),
            patch(
                "eidos_runtime.tools.runtime_workspace.ToolExecutor.refresh_workspace_index",
                side_effect=[
                    WorkspacePathError("WORKSPACE_INDEX_INCOMPLETE"),
                    WorkspacePathError("WORKSPACE_INDEX_INCOMPLETE"),
                    WorkspaceManifest((), True, False),
                ],
            ),
        ):
            RuntimeEngine(
                self.store,
                model,
                lambda _message: None,
                request_approval=(
                    lambda params, _cancel: (
                        approvals.append(params) or ApprovalDecision("approve")
                    )
                ),
                shell_available=True,
            ).run(self.run["id"], threading.Event())

        persisted = self.store.read_run(self.run["id"])
        self.assertNotEqual(persisted["status"], "interrupted")
        self.assertFalse(self.store.side_effects_blocked(self.run["id"]))
        self.assertFalse(
            self.store.context_projection_facts(
                self.run["id"]
            ).reconciliation_required
        )
        self.assertGreaterEqual(len(model.run_statuses), 2)
        self.assertEqual(model.run_statuses[1], "running")
        read_only_tools = {
            definition.name for definition in model.tool_definitions_history[1]
        }
        self.assertIn("run_shell", read_only_tools)
        self.assertIn("apply_patch", read_only_tools)
        self.assertEqual(len(approvals), 1)
        self.assertEqual(approvals[0]["sandboxPermissions"], expected_mode)
        calls = self.store.connection.execute(
            "SELECT COUNT(*) FROM tool_calls WHERE tool_name = ?",
            ("run_shell",),
        ).fetchone()[0]
        self.assertEqual(calls, 1)

    def test_escalated_shell_reconciliation_keeps_run_active(self) -> None:
        self._assert_permissioned_shell_keeps_run_active(
            {
                "command": "false",
                "yieldTimeMs": 5000,
                "sandboxPermissions": "require_escalated",
                "justification": "The fixture needs an explicit unsandboxed attempt.",
            },
            "require_escalated",
        )

    def test_additional_permission_shell_reconciliation_keeps_run_active(self) -> None:
        self._assert_permissioned_shell_keeps_run_active(
            {
                "command": "false",
                "yieldTimeMs": 5000,
                "sandboxPermissions": "with_additional_permissions",
                "additionalPermissions": {"network": {"enabled": True}},
                "justification": "The fixture needs an explicit network permission.",
            },
            "with_additional_permissions",
        )

    def test_external_unknown_side_effect_remains_fail_closed_without_replay(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-external-reconciliation-") as directory:
            root = Path(directory)
            data = root / "data"
            workspace = root / "workspace"
            source = root / "plugin"
            data.mkdir(mode=0o700)
            workspace.mkdir()
            source.mkdir()
            (source / "server.py").write_bytes(
                (Path(__file__).parent / "fixtures" / "mcp_fixture.py").read_bytes()
            )
            (source / "plugin.json").write_text(json.dumps({
                "schemaVersion": 1,
                "id": "demo",
                "name": "Demo",
                "version": "1.0.0",
                "description": "Fixture",
                "skills": [],
                "mcpServers": [{
                    "id": "fixture",
                    "executable": sys.executable,
                    "argv": ["server.py"],
                    "envNames": [],
                    "permissionProfile": "workspace_read",
                    "startupTimeoutSeconds": 5,
                    "toolTimeoutSeconds": 1,
                    "enabled": True,
                }],
            }), encoding="utf-8")
            store = SessionStore(data)
            store.initialize()
            plugins = PluginCatalog(store)
            plugins.import_directory(source)
            plugins.set_enabled("demo", True)
            plugins.set_mcp_enabled("demo", "fixture", True)
            session = store.create_session(str(workspace))
            run, _ = store.create_run(
                session["id"],
                "Call the slow external tool",
                extension_snapshot=SkillCatalog(plugins).extension_snapshot(),
            )
            model = _StatusRecordingModel(store, run["id"], [
                ModelResponse(tool_calls=(ModelToolCall(
                    "search",
                    "tool_search",
                    {"query": "slow"},
                ),)),
                ModelResponse(tool_calls=(ModelToolCall(
                    "slow",
                    "mcp__fixture__slow",
                    {},
                ),)),
                ModelResponse(tool_calls=(ModelToolCall(
                    "observe-workspace", "list_files", {}
                ),)),
                ModelResponse(text="verified answer"),
            ])
            pending_epoch = store.context_projection_facts(
                run["id"]
            ).reconciliation_epoch
            resources = ResourceRegistry()
            kernel = RuntimeAsyncKernel(resource_registry=resources)
            kernel.start()
            try:
                RuntimeEngine(
                    store,
                    model,
                    lambda _message: None,
                    request_approval=(
                        lambda _params, _cancel: ApprovalDecision("approve")
                    ),
                    async_kernel=kernel,
                    mcp_sandbox=False,
                    resource_registry=resources,
                ).run(run["id"], threading.Event())
            finally:
                kernel.close()

            persisted = store.read_run(run["id"])
            self.assertNotEqual(persisted["status"], "interrupted")
            self.assertTrue(persisted["sideEffectsMayExist"])
            self.assertFalse(store.side_effects_blocked(run["id"]))
            self.assertFalse(
                store.context_projection_facts(
                    run["id"]
                ).reconciliation_required
            )
            self.assertEqual(
                store.context_projection_facts(
                    run["id"]
                ).reconciliation_epoch,
                pending_epoch + 2,
            )
            self.assertGreaterEqual(len(model.contexts), 3)
            self.assertEqual(model.run_statuses[2], "running")
            read_only_tools = {
                definition.name for definition in model.tool_definitions_history[2]
            }
            self.assertIn("run_shell", read_only_tools)
            self.assertIn("apply_patch", read_only_tools)
            calls = store.connection.execute(
                "SELECT COUNT(*) FROM tool_calls WHERE tool_name = ?",
                ("mcp__fixture__slow",),
            ).fetchone()[0]
            self.assertEqual(calls, 1)
            result = json.loads(store.connection.execute(
                "SELECT result_json FROM tool_calls WHERE tool_name = ?",
                ("mcp__fixture__slow",),
            ).fetchone()[0])
            self.assertIn(result["code"], {"mcp_tool_timeout", "TOOL_TIMEOUT"})
            store.close()

    def test_external_uncertain_refresh_failure_keeps_barrier(self) -> None:
        item = self.store.create_tool_item(
            self.run["id"], 0, 0, "external", "external_tool", "{}"
        )
        self.store.complete_tool_item(
            item["id"],
            json.dumps({
                "outcome": "error",
                "code": "external_outcome_unknown",
                "summary": "External outcome is unknown",
                "data": {},
                "sideEffectsMayExist": True,
                "reconciliationRequired": True,
            }),
            item_status="failed",
            tool_status="failed",
        )
        pending_epoch = self.store.context_projection_facts(
            self.run["id"]
        ).reconciliation_epoch
        runtime = ToolCallRuntime.__new__(ToolCallRuntime)
        runtime.store = self.store
        runtime.events = SimpleNamespace(
            publish=lambda *_args, **_kwargs: None,
        )

        def fail_refresh(_cancel: threading.Event) -> WorkspaceManifest:
            raise WorkspacePathError("workspace_identity_changed")

        runtime.workspace_refresh = fail_refresh

        runtime._refresh_reconciliation_after_result(
            run_id=self.run["id"],
            call=SimpleNamespace(name="external_tool"),
            plan=SimpleNamespace(side_effect="external"),
            outcome=SimpleNamespace(
                result={
                    "outcome": "error",
                    "reconciliationRequired": True,
                },
                reconciliation_disposition=ReconciliationDisposition.CONTINUE,
            ),
            cancel=threading.Event(),
        )

        self.assertTrue(self.store.side_effects_blocked(self.run["id"]))
        self.assertEqual(
            self.store.context_projection_facts(
                self.run["id"]
            ).reconciliation_epoch,
            pending_epoch,
        )

    def test_new_successful_read_clears_barrier_before_success_completion(self) -> None:
        uncertain = self.store.create_tool_item(
            self.run["id"], 0, 0, "shell", "run_shell", "{}"
        )
        self.store.complete_tool_item(
            uncertain["id"],
            json.dumps({
                "outcome": "error",
                "code": "outcome_unknown",
                "summary": "Command outcome is unknown",
                "data": {},
                "sideEffectsMayExist": True,
                "reconciliationRequired": True,
            }),
            item_status="failed",
            tool_status="failed",
        )
        self.assertTrue(self.store.side_effects_blocked(self.run["id"]))
        pending_epoch = self.store.context_projection_facts(
            self.run["id"]
        ).reconciliation_epoch

        read_step = self.store.increment_model_step(self.run["id"])
        read = self.store.create_tool_item(
            self.run["id"], read_step, 0, "read", "read_file", "{}"
        )
        self.store.complete_tool_item(
            read["id"],
            json.dumps({
                "outcome": "success",
                "code": "ok",
                "summary": "File observed",
                "data": {},
                "sideEffectsMayExist": False,
                "reconciliationRequired": False,
            }),
        )
        self.store.complete_current_step(self.run["id"], "completed")
        self.assertTrue(self.store.side_effects_blocked(self.run["id"]))
        mutation = self.store.clear_reconciliation_after_workspace_refresh_committed(
            self.run["id"], pending_epoch
        )
        self.assertIsNotNone(mutation)
        self.assertFalse(self.store.side_effects_blocked(self.run["id"]))
        self.assertEqual(
            self.store.context_projection_facts(
                self.run["id"]
            ).reconciliation_epoch,
            pending_epoch + 1,
        )
        self.assertTrue(self.store.read_run(self.run["id"])["sideEffectsMayExist"])

        final_step = self.store.increment_model_step(self.run["id"])
        assistant = self.store.create_assistant_item(self.run["id"], final_step)
        self.store.complete_current_step(self.run["id"], "completed")
        _, completed = self.store.complete_assistant_and_run(
            assistant["id"], self.run["id"]
        )

        self.assertEqual(completed["status"], "succeeded")
        self.assertFalse(completed.get("reconciliationRequired", False))

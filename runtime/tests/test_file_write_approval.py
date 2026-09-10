from __future__ import annotations

from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from eidos_runtime.model.client import ModelToolCall
from eidos_runtime.runtime.tool_execution import PreparedToolExecution, VerifiedToolExecutionResult
from eidos_runtime.runtime.tool_runtime import FileChangeToolHandler
from eidos_runtime.sandbox.permissions import (
    AdditionalPermissionProfile,
    BasePermissionProfile,
    FileSystemAccessMode,
    FileSystemPermissionEntry,
    base_permission_profile_for_workspace,
    materialize_effective_profile,
)
from eidos_runtime.tools.workspace import ToolExecutor


class FileWriteApprovalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name).resolve()
        self.workspace = root / "workspace"
        self.external = root / "external"
        self.workspace.mkdir()
        self.external.mkdir()
        self.target = self.external / "target.txt"
        self.target.write_text("base\n")
        self.executor = ToolExecutor(self.workspace)
        self.addCleanup(self.executor.close)
        self.base = BasePermissionProfile.for_workspace(workspace_root=self.workspace)
        self.permissions = materialize_effective_profile(
            self.base,
            AdditionalPermissionProfile(fileSystem=(FileSystemPermissionEntry(
                path=str(self.external), access=FileSystemAccessMode.WRITE,
            ),)),
        )
        self.runtime = SimpleNamespace(
            implementation=SimpleNamespace(executor=self.executor),
            spec=SimpleNamespace(name="apply_patch"),
        )
        self.prepared = PreparedToolExecution(
            approval_description={"paths": [str(self.target)]},
            intent_preconditions={}, transition_reason="workspace_file_authorized",
        )
        self.dependencies = SimpleNamespace(
            base_permissions=self.base, skill_access=None,
            store=SimpleNamespace(run_permission_grants=Mock(return_value=None)),
            execute_side_effect=Mock(), execute_workspace_side_effect=Mock(),
        )
        self.handler = FileChangeToolHandler(self.dependencies)

    def effect(self, execute: Mock) -> VerifiedToolExecutionResult:
        return self.handler._execute_file_effect(
            "run", {"id": "item"}, self.prepared, threading.Event(), self.runtime, execute,
        )

    def test_external_rejection_never_invokes_write(self) -> None:
        self.dependencies.execute_side_effect.return_value = (SimpleNamespace(decision="reject"), None)
        execute = Mock()
        with self.executor.external_write_scope((self.target,), self.permissions):
            with patch("eidos_runtime.runtime.tool_runtime.is_seatbelt_ready", return_value=True):
                result = self.effect(execute)
            self.assertFalse(self.executor.external_write_approved)
        execute.assert_not_called()
        self.dependencies.execute_workspace_side_effect.assert_not_called()
        self.assertEqual(result.result["code"], "user_rejected")
        request = self.dependencies.execute_side_effect.call_args.kwargs["prepared"]
        self.assertEqual(request.approval_kind, "additional_permissions")

    def test_external_approval_enables_write_only_inside_callback(self) -> None:
        execute = Mock(return_value={"outcome": "success"})

        def approve(**kwargs):
            self.assertFalse(self.executor.external_write_approved)
            result = kwargs["execute"]()
            self.assertTrue(self.executor.external_write_approved)
            return SimpleNamespace(decision="approve"), VerifiedToolExecutionResult(result=result)

        self.dependencies.execute_side_effect.side_effect = approve
        with self.executor.external_write_scope((self.target,), self.permissions):
            with patch("eidos_runtime.runtime.tool_runtime.is_seatbelt_ready", return_value=True):
                self.effect(execute)
        execute.assert_called_once_with()
        self.assertFalse(self.executor.external_write_approved)
        self.assertEqual(self.executor._external_writers, {})

    def test_missing_sandbox_requires_escalated_approval(self) -> None:
        self.dependencies.execute_side_effect.return_value = (SimpleNamespace(decision="reject"), None)
        execute = Mock()
        with self.executor.external_write_scope((), self.permissions):
            with patch("eidos_runtime.runtime.tool_runtime.is_seatbelt_ready", return_value=False):
                result = self.effect(execute)
        execute.assert_not_called()
        self.dependencies.execute_workspace_side_effect.assert_not_called()
        self.assertEqual(result.result["code"], "user_rejected")
        request = self.dependencies.execute_side_effect.call_args.kwargs["prepared"]
        self.assertEqual(request.approval_kind, "escalated")
        self.assertEqual(request.approval_request["sandboxPermissions"], "require_escalated")

    def test_permanent_deny_does_not_prepare_or_request_approval(self) -> None:
        self.dependencies.base_permissions = BasePermissionProfile.for_workspace(
            workspace_root=self.workspace, protected_paths=(self.external,),
        )
        call = ModelToolCall("call", "apply_patch", {"changes": []})
        with (
            patch.object(self.executor, "external_patch_paths", return_value=(self.target,)),
            patch.object(self.handler, "_execute") as prepare,
        ):
            self.handler.execute("run", {"id": "item"}, call, threading.Event(), self.runtime)
        prepare.assert_not_called()
        self.dependencies.execute_side_effect.assert_not_called()
        self.assertEqual(self.target.read_text(), "base\n")

    def test_external_scope_cannot_commit_before_approval_and_rechecks_version(self) -> None:
        raw_patch = f"*** Begin Patch\n*** Update File: {self.target}\n@@\n-base\n+candidate\n*** End Patch"
        with self.executor.external_write_scope((self.target,), self.permissions):
            prepared = self.executor._prepare_codex_patch("apply_patch", raw_patch, threading.Event())
            self.assertNotIsInstance(prepared, dict)
            change = prepared.changes[0]
            denied = self.executor.commit_file_change("apply_patch", change, threading.Event())
            self.assertEqual(denied["code"], "approval_required")
            self.assertEqual(self.target.read_text(), "base\n")
            self.target.write_text("external edit\n")
            self.executor.external_write_approved = True
            with patch("eidos_runtime.tools.workspace.secure_workspace_move") as commit:
                result = self.executor.commit_file_change("apply_patch", change, threading.Event())
            commit.assert_not_called()
            self.assertEqual(result["code"], "file_version_conflict")
            self.assertFalse(result["sideEffectsMayExist"])
            self.assertEqual(self.target.read_text(), "external edit\n")

    def test_replaced_external_directory_is_rejected_after_approval(self) -> None:
        execute = Mock()

        def approve(**kwargs):
            self.external.rename(self.external.with_name("original-external"))
            self.external.mkdir()
            result = kwargs["execute"]()
            return SimpleNamespace(decision="approve"), VerifiedToolExecutionResult(result=result)

        self.dependencies.execute_side_effect.side_effect = approve
        with self.executor.external_write_scope((self.target,), self.permissions):
            with patch("eidos_runtime.runtime.tool_runtime.is_seatbelt_ready", return_value=True):
                result = self.effect(execute)
                self.assertEqual(result.result["code"], "workspace_identity_changed")
            self.assertFalse(self.executor.external_write_approved)
        execute.assert_not_called()
        self.assertFalse(self.target.exists())
        self.assertEqual((self.external.with_name("original-external") / self.target.name).read_text(), "base\n")

    def test_run_grant_reuses_workspace_execution_without_new_approval(self) -> None:
        execute = Mock(return_value={"outcome": "success"})
        self.dependencies.execute_workspace_side_effect.side_effect = lambda **kwargs: VerifiedToolExecutionResult(result=kwargs["execute"]())
        with self.executor.external_write_scope((self.target,), self.permissions):
            self.executor.external_write_approved = True
            with patch("eidos_runtime.runtime.tool_runtime.is_seatbelt_ready", return_value=True):
                self.effect(execute)
        execute.assert_called_once_with()
        self.dependencies.execute_side_effect.assert_not_called()
        prepared = self.dependencies.execute_workspace_side_effect.call_args.kwargs["prepared"]
        self.assertEqual(prepared.intent_preconditions["authorization"], "run_grant")

    def test_unsandboxed_write_cannot_override_permanent_deny(self) -> None:
        base = BasePermissionProfile.for_workspace(
            workspace_root=self.workspace, protected_paths=(self.external,),
        )
        permissions = materialize_effective_profile(base)
        raw_patch = f"*** Begin Patch\n*** Update File: {self.target}\n@@\n-base\n+candidate\n*** End Patch"
        with self.executor.external_write_scope((self.target,), permissions):
            self.executor.external_write_approved = True
            self.executor.unsandboxed_write = True
            with patch("eidos_runtime.tools.workspace.secure_workspace_move") as commit:
                result = self.executor._prepare_codex_patch("apply_patch", raw_patch, threading.Event())
            self.assertIsInstance(result, dict)
            self.assertEqual(result["code"], "permission_not_requestable")
            commit.assert_not_called()
        self.assertEqual(self.target.read_text(), "base\n")

    def test_existing_external_target_requests_only_that_file(self) -> None:
        call = ModelToolCall("call", "apply_patch", {"changes": []})

        def inspect_scope(*_args):
            entries = [entry for entry in self.executor.write_permissions.entries if entry.source == "additional"]
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0].resolved_path, str(self.target))
            self.assertFalse(entries[0].recursive)
            self.assertFalse(self.executor.write_permissions.allows_file_write(self.external / "sibling.txt"))

        with (
            patch.object(self.executor, "external_patch_paths", return_value=(self.target,)),
            patch.object(self.handler, "_execute", side_effect=inspect_scope),
        ):
            self.handler.execute("run", {"id": "item"}, call, threading.Event(), self.runtime)

    def test_projectless_workspace_uses_existing_permission_without_approval(self) -> None:
        data = self.workspace.parent / ".eidos"
        workspace = data / "..eidos-projectless" / "session"
        workspace.mkdir(parents=True)
        with ToolExecutor(workspace) as executor:
            self.executor = executor
            self.runtime.implementation.executor = executor
            self.dependencies.base_permissions = base_permission_profile_for_workspace(workspace, data)
            execute = Mock(return_value={"outcome": "success"})
            self.dependencies.execute_workspace_side_effect.side_effect = lambda **kwargs: VerifiedToolExecutionResult(
                result=kwargs["execute"](),
            )
            call = ModelToolCall("call", "apply_patch", {"changes": []})
            with (
                patch.object(executor, "external_patch_paths", return_value=()),
                patch.object(self.handler, "_execute", side_effect=lambda *_args: self.effect(execute)),
                patch("eidos_runtime.runtime.tool_runtime.is_seatbelt_ready", return_value=True),
            ):
                self.handler.execute("run", {"id": "item"}, call, threading.Event(), self.runtime)
            execute.assert_called_once_with()
            self.dependencies.execute_side_effect.assert_not_called()

    def test_active_user_skill_routes_through_approval_and_reuses_run_grant(self) -> None:
        data = self.workspace.parent / ".eidos"
        skill = data / "skills" / "review"
        skill.mkdir(parents=True)
        target = skill / "SKILL.md"
        target.write_text("old skill\n")
        self.dependencies.base_permissions = base_permission_profile_for_workspace(self.workspace, data)
        self.dependencies.skill_access = SimpleNamespace(active_roots=lambda: (skill,))
        grant = AdditionalPermissionProfile(fileSystem=(FileSystemPermissionEntry(
            path=str(target), access=FileSystemAccessMode.WRITE, recursive=False,
        ),))
        call = ModelToolCall("call", "apply_patch", {"changes": []})

        for approved in (False, True):
            with self.subTest(existing_grant=approved):
                self.dependencies.execute_side_effect.reset_mock()
                self.dependencies.execute_workspace_side_effect.reset_mock()
                self.dependencies.store.run_permission_grants.return_value = grant if approved else None
                execute = Mock(return_value={"outcome": "success"})
                self.dependencies.execute_workspace_side_effect.side_effect = lambda **kwargs: VerifiedToolExecutionResult(
                    result=kwargs["execute"](),
                )
                self.dependencies.execute_side_effect.return_value = (SimpleNamespace(decision="reject"), None)
                with (
                    patch.object(self.executor, "external_patch_paths", return_value=(target,)),
                    patch.object(self.handler, "_execute", side_effect=lambda *_args: self.effect(execute)),
                    patch("eidos_runtime.runtime.tool_runtime.is_seatbelt_ready", return_value=True),
                ):
                    result = self.handler.execute("run", {"id": "item"}, call, threading.Event(), self.runtime)
                if approved:
                    execute.assert_called_once_with()
                    self.dependencies.execute_side_effect.assert_not_called()
                else:
                    execute.assert_not_called()
                    self.dependencies.execute_side_effect.assert_called_once()
                    self.assertEqual(result.result["code"], "user_rejected")
                    self.dependencies.execute_workspace_side_effect.assert_not_called()
                self.assertEqual(target.read_text(), "old skill\n")

    def test_system_skill_never_reaches_approval_even_with_active_skill_and_grant(self) -> None:
        data = self.workspace.parent / ".eidos"
        system = data / "skills" / ".system"
        system.mkdir(parents=True)
        target = system / "SKILL.md"
        target.write_text("system skill\n")
        self.dependencies.base_permissions = base_permission_profile_for_workspace(self.workspace, data)
        self.dependencies.skill_access = SimpleNamespace(active_roots=lambda: (system,))
        self.dependencies.store.run_permission_grants.return_value = AdditionalPermissionProfile(
            fileSystem=(FileSystemPermissionEntry(
                path=str(target), access=FileSystemAccessMode.WRITE, recursive=False,
            ),),
        )
        call = ModelToolCall("call", "apply_patch", {"changes": []})
        with (
            patch.object(self.executor, "external_patch_paths", return_value=(target,)),
            patch.object(self.handler, "_execute") as prepare,
        ):
            self.handler.execute("run", {"id": "item"}, call, threading.Event(), self.runtime)
        prepare.assert_not_called()
        self.dependencies.execute_side_effect.assert_not_called()
        self.dependencies.execute_workspace_side_effect.assert_not_called()
        self.assertEqual(target.read_text(), "system skill\n")

    def test_relative_active_workspace_skill_also_requests_approval(self) -> None:
        skill = self.workspace / "skills" / "review"
        skill.mkdir(parents=True)
        target = skill / "SKILL.md"
        target.write_text("old skill\n")
        self.dependencies.skill_access = SimpleNamespace(active_roots=lambda: (skill,))
        self.dependencies.execute_side_effect.return_value = (SimpleNamespace(decision="reject"), None)
        execute = Mock()
        call = ModelToolCall("call", "apply_patch", {"changes": [{
            "type": "update", "path": "skills/review/SKILL.md",
            "chunks": [{"oldLines": ["old skill"], "newLines": ["new skill"]}],
        }]})
        with (
            patch.object(self.handler, "_execute", side_effect=lambda *_args: self.effect(execute)),
            patch("eidos_runtime.runtime.tool_runtime.is_seatbelt_ready", return_value=True),
        ):
            result = self.handler.execute("run", {"id": "item"}, call, threading.Event(), self.runtime)
        self.assertEqual(result.result["code"], "user_rejected")
        self.dependencies.execute_side_effect.assert_called_once()
        self.dependencies.execute_workspace_side_effect.assert_not_called()
        execute.assert_not_called()
        request = self.dependencies.execute_side_effect.call_args.kwargs["prepared"].approval_request
        self.assertEqual(request["additionalPermissions"]["fileSystem"][0]["path"], str(target))
        self.assertEqual(target.read_text(), "old skill\n")

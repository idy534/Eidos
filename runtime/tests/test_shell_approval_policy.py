from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from eidos_runtime.db.storage import WorkspaceIdentity
from eidos_runtime.runtime.shell_orchestration import (
    ShellOrchestrationRequest,
    ShellOrchestrationRuntime,
)
from eidos_runtime.runtime.tool_orchestrator import (
    ExecApprovalRequirement,
    OrchestratorContext,
    ToolOrchestrator,
)
from eidos_runtime.sandbox.permissions import (
    AdditionalPermissionProfile,
    BasePermissionProfile,
    NetworkPermissions,
    SandboxPermissions,
)
from eidos_runtime.tools.contracts import NetworkAccess, RunShellInput


class ShellApprovalPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-shell-policy-")
        self.root = Path(self.temporary.name).resolve()
        metadata = self.root.stat()
        self.identity = WorkspaceIdentity(
            self.root,
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_uid,
        )
        self.context = OrchestratorContext(
            tool_call_id="tool-call",
            workspace_root=self.root,
            workspace_identity=(
                metadata.st_dev,
                metadata.st_ino,
                metadata.st_uid,
            ),
            cwd=self.root,
            timeout_seconds=30,
            cancel=threading.Event(),
            base_permissions=BasePermissionProfile.for_workspace(
                workspace_root=self.root,
            ),
        )
        self.runtime = ShellOrchestrationRuntime(
            lambda _attempt: ({"outcome": "success", "code": "ok"}, None)
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_default_workspace_sandbox_does_not_require_approval(self) -> None:
        request = ShellOrchestrationRequest(
            RunShellInput(command="printf safe"),
            self.identity,
            self.identity,
        )

        requirement = self.runtime.approval_requirement(request, self.context)

        self.assertEqual(requirement, ExecApprovalRequirement.SKIP)

    def test_additional_permissions_require_approval(self) -> None:
        request = ShellOrchestrationRequest(
            RunShellInput(
                command="curl https://example.com",
                sandboxPermissions=SandboxPermissions.WITH_ADDITIONAL_PERMISSIONS,
                additionalPermissions=AdditionalPermissionProfile(
                    network=NetworkPermissions(enabled=True)
                ),
                justification="Download a requested file",
            ),
            self.identity,
            self.identity,
        )

        requirement = self.runtime.approval_requirement(request, self.context)

        self.assertEqual(requirement, ExecApprovalRequirement.NEEDS_APPROVAL)

    def test_network_intent_requires_approval_and_effective_seatbelt_network(self) -> None:
        request = ShellOrchestrationRequest(
            RunShellInput(
                command="npm install",
                networkAccess=NetworkAccess.REQUEST,
                justification="Install project dependencies",
            ),
            self.identity,
            self.identity,
        )

        self.assertEqual(
            self.runtime.sandbox_permissions(request),
            SandboxPermissions.WITH_ADDITIONAL_PERMISSIONS,
        )
        additional = self.runtime.additional_permissions(request)
        self.assertIsNotNone(additional)
        assert additional is not None
        self.assertIsNotNone(additional.network)
        assert additional.network is not None
        self.assertTrue(additional.network.enabled)
        self.assertEqual(
            self.runtime.approval_requirement(request, self.context),
            ExecApprovalRequirement.NEEDS_APPROVAL,
        )

        attempts: list[object] = []
        approvals: list[object] = []
        runtime = ShellOrchestrationRuntime(
            lambda attempt: attempts.append(attempt)
            or ({"outcome": "success", "code": "ok"}, None)
        )
        result = ToolOrchestrator().run(
            runtime,
            request,
            self.context,
            approve=lambda approval: approvals.append(approval) or True,
        )

        self.assertEqual(result.result["outcome"], "success")
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0].sandbox.value, "macos_seatbelt")
        self.assertTrue(attempts[0].permissions.network_enabled)
        self.assertEqual(len(approvals), 1)
        self.assertEqual(
            approvals[0].sandbox_permissions,
            SandboxPermissions.WITH_ADDITIONAL_PERMISSIONS,
        )
        self.assertIsNotNone(approvals[0].additional_permissions)
        assert approvals[0].additional_permissions is not None
        assert approvals[0].additional_permissions.network is not None
        self.assertTrue(approvals[0].additional_permissions.network.enabled)

    def test_rejected_network_intent_never_executes(self) -> None:
        request = ShellOrchestrationRequest(
            RunShellInput(
                command="npm install",
                networkAccess=NetworkAccess.REQUEST,
                justification="Install project dependencies",
            ),
            self.identity,
            self.identity,
        )
        attempts: list[object] = []
        runtime = ShellOrchestrationRuntime(
            lambda attempt: attempts.append(attempt)
            or ({"outcome": "success", "code": "ok"}, None)
        )

        result = ToolOrchestrator().run(
            runtime,
            request,
            self.context,
            approve=lambda _approval: False,
        )

        self.assertEqual(result.result["code"], "user_rejected")
        self.assertEqual(attempts, [])

    def test_unsandboxed_execution_requires_approval(self) -> None:
        request = ShellOrchestrationRequest(
            RunShellInput(
                command="printf unsafe",
                sandboxPermissions=SandboxPermissions.REQUIRE_ESCALATED,
                justification="The user requested an unsandboxed command",
            ),
            self.identity,
            self.identity,
        )

        requirement = self.runtime.approval_requirement(request, self.context)

        self.assertEqual(requirement, ExecApprovalRequirement.NEEDS_APPROVAL)


if __name__ == "__main__":
    unittest.main()

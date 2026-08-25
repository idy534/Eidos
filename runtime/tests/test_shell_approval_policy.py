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
)
from eidos_runtime.sandbox.permissions import (
    AdditionalPermissionProfile,
    BasePermissionProfile,
    NetworkPermissions,
    SandboxPermissions,
)
from eidos_runtime.tools.contracts import RunShellInput


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

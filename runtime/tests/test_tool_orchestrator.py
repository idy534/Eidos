from __future__ import annotations

import tempfile
import threading
import unittest
from dataclasses import dataclass
from pathlib import Path

from eidos_runtime.runtime.tool_orchestrator import (
    ExecApprovalRequirement,
    OrchestratorContext,
    ToolOrchestrator,
)
from eidos_runtime.sandbox.denial import (
    SandboxDenied,
    SandboxDenialCategory,
)
from eidos_runtime.sandbox.permissions import (
    AdditionalPermissionProfile,
    BasePermissionProfile,
    NetworkPermissions,
    SandboxPermissions,
    SandboxType,
)


@dataclass
class _Request:
    command: str = "true"
    permissions: SandboxPermissions = SandboxPermissions.USE_DEFAULT
    additional: AdditionalPermissionProfile | None = None


class _Runtime:
    def __init__(self, outcomes: list[tuple[dict[str, object], SandboxDenied | None]]):
        self.outcomes = outcomes
        self.attempts = []

    def workspace_roots(self, _request, context):
        return (str(context.workspace_root),)

    def sandbox_permissions(self, request):
        return request.permissions

    def additional_permissions(self, request):
        return request.additional

    def approval_requirement(self, _request, _context):
        return ExecApprovalRequirement.NEEDS_APPROVAL

    def escalation_allowed(self, _request, _context):
        return True

    def run(self, _request, attempt, _context):
        self.attempts.append(attempt)
        return self.outcomes.pop(0)


class ToolOrchestratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.context = OrchestratorContext(
            tool_call_id="tool-call",
            workspace_root=self.root,
            workspace_identity=(1, 2, 3),
            cwd=self.root,
            timeout_seconds=30,
            cancel=threading.Event(),
            base_permissions=BasePermissionProfile.for_workspace(
                workspace_root=self.root,
            ),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_skipped_approval_authorizes_immediately_before_execution(self) -> None:
        order: list[str] = []

        class WorkspaceRuntime(_Runtime):
            def approval_requirement(self, _request, _context):
                return ExecApprovalRequirement.SKIP

            def run(self, request, attempt, context):
                order.append("execute")
                return super().run(request, attempt, context)

        runtime = WorkspaceRuntime([
            ({"outcome": "success", "code": "ok"}, None)
        ])

        result = ToolOrchestrator().run(
            runtime,
            _Request(),
            self.context,
            approve=lambda _request: self.fail("approval must not be requested"),
            authorize_without_approval=lambda: order.append("authorize"),
        )

        self.assertEqual(result.result["outcome"], "success")
        self.assertEqual(order, ["authorize", "execute"])

    def test_plain_nonzero_failure_does_not_escalate(self) -> None:
        runtime = _Runtime([({"outcome": "error", "code": "nonzero_exit"}, None)])
        approvals = []

        result = ToolOrchestrator().run(
            runtime,
            _Request(),
            self.context,
            approve=lambda request: approvals.append(request) or True,
        )

        self.assertEqual(result.attempt_count, 1)
        self.assertFalse(result.escalated)
        self.assertEqual([attempt.sandbox for attempt in runtime.attempts], [
            SandboxType.MACOS_SEATBELT
        ])
        self.assertEqual(len(approvals), 1)

    def test_policy_compilation_failure_does_not_escalate(self) -> None:
        runtime = _Runtime([
            ({"outcome": "error", "code": "sandbox_unavailable"}, None)
        ])

        result = ToolOrchestrator().run(
            runtime,
            _Request(),
            self.context,
            approve=lambda _request: True,
        )

        self.assertEqual(result.attempt_count, 1)
        self.assertEqual(len(runtime.attempts), 1)

    def test_structured_denial_gets_one_fresh_escalated_approval(self) -> None:
        denied = SandboxDenied(
            category=SandboxDenialCategory.FILESYSTEM_READ,
            summary="Seatbelt denied file read",
            original_exit_code=1,
        )
        runtime = _Runtime([
            ({"outcome": "error", "code": "sandbox_denied"}, denied),
            ({"outcome": "success", "code": "ok"}, None),
        ])
        approvals = []

        result = ToolOrchestrator().run(
            runtime,
            _Request(),
            self.context,
            approve=lambda request: approvals.append(request) or True,
        )

        self.assertEqual(result.attempt_count, 2)
        self.assertTrue(result.escalated)
        self.assertEqual(
            [attempt.sandbox for attempt in runtime.attempts],
            [SandboxType.MACOS_SEATBELT, SandboxType.NONE],
        )
        self.assertEqual([request.attempt_ordinal for request in approvals], [0, 1])
        self.assertNotEqual(approvals[0].approval_key, approvals[1].approval_key)

    def test_rejected_escalation_does_not_start_second_attempt(self) -> None:
        denied = SandboxDenied(
            category=SandboxDenialCategory.NETWORK,
            summary="Seatbelt denied network",
        )
        runtime = _Runtime([
            ({"outcome": "error", "code": "sandbox_denied"}, denied),
        ])
        approvals = []

        result = ToolOrchestrator().run(
            runtime,
            _Request(),
            self.context,
            approve=lambda request: approvals.append(request) or request.attempt_ordinal == 0,
        )

        self.assertEqual(result.attempt_count, 1)
        self.assertEqual(len(runtime.attempts), 1)
        self.assertEqual(result.result["code"], "user_rejected_escalation")

    def test_explicit_escalation_starts_unsandboxed_after_approval(self) -> None:
        runtime = _Runtime([({"outcome": "success", "code": "ok"}, None)])

        result = ToolOrchestrator().run(
            runtime,
            _Request(permissions=SandboxPermissions.REQUIRE_ESCALATED),
            self.context,
            approve=lambda _request: True,
        )

        self.assertEqual(result.attempt_count, 1)
        self.assertTrue(result.escalated)
        self.assertEqual(runtime.attempts[0].sandbox, SandboxType.NONE)

    def test_hard_confidentiality_deny_blocks_unsandboxed_execution(self) -> None:
        runtime = _Runtime([({"outcome": "success", "code": "ok"}, None)])
        context = OrchestratorContext(
            **{
                **self.context.__dict__,
                "base_permissions": BasePermissionProfile.for_workspace(
                    workspace_root=self.root,
                    hard_confidentiality_paths=(self.root / "secret",),
                ),
            }
        )

        result = ToolOrchestrator().run(
            runtime,
            _Request(permissions=SandboxPermissions.REQUIRE_ESCALATED),
            context,
            approve=lambda _request: self.fail("approval must not be requested"),
        )

        self.assertEqual(result.result["code"], "unsandboxed_execution_forbidden")
        self.assertEqual(runtime.attempts, [])

    def test_hard_confidentiality_deny_blocks_retry_after_denial(self) -> None:
        denied = SandboxDenied(
            category=SandboxDenialCategory.FILESYSTEM_READ,
            summary="Seatbelt denied file read",
        )
        runtime = _Runtime([
            ({"outcome": "error", "code": "sandbox_denied"}, denied)
        ])
        approvals = []
        context = OrchestratorContext(
            **{
                **self.context.__dict__,
                "base_permissions": BasePermissionProfile.for_workspace(
                    workspace_root=self.root,
                    hard_confidentiality_paths=(self.root / "secret",),
                ),
            }
        )

        result = ToolOrchestrator().run(
            runtime,
            _Request(),
            context,
            approve=lambda approval: approvals.append(approval) or True,
        )

        self.assertEqual(result.attempt_count, 1)
        self.assertEqual(len(approvals), 1)
        self.assertEqual(len(runtime.attempts), 1)

    def test_cancellation_after_retry_approval_prevents_second_attempt(self) -> None:
        denied = SandboxDenied(
            category=SandboxDenialCategory.NETWORK,
            summary="Seatbelt denied network",
        )
        runtime = _Runtime([
            ({"outcome": "error", "code": "sandbox_denied"}, denied),
            ({"outcome": "success", "code": "ok"}, None),
        ])

        def approve(request) -> bool:
            if request.attempt_ordinal == 1:
                self.context.cancel.set()
            return True

        result = ToolOrchestrator().run(
            runtime, _Request(), self.context, approve=approve
        )

        self.assertEqual(result.attempt_count, 1)
        self.assertEqual(len(runtime.attempts), 1)

    def test_second_denial_never_creates_a_third_attempt(self) -> None:
        denied = SandboxDenied(
            category=SandboxDenialCategory.FILESYSTEM_READ,
            summary="Seatbelt denied file read",
        )
        runtime = _Runtime([
            ({"outcome": "error", "code": "sandbox_denied"}, denied),
            ({"outcome": "error", "code": "still_denied"}, denied),
        ])

        result = ToolOrchestrator().run(
            runtime, _Request(), self.context, approve=lambda _request: True
        )

        self.assertEqual(result.attempt_count, 2)
        self.assertEqual(len(runtime.attempts), 2)

    def test_approval_key_binds_command_cwd_and_workspace_identity(self) -> None:
        child = self.root / "child"
        child.mkdir()

        def key(
            request: _Request,
            context: OrchestratorContext = self.context,
        ) -> str:
            approvals = []
            ToolOrchestrator().run(
                _Runtime([({"outcome": "success", "code": "ok"}, None)]),
                request,
                context,
                approve=lambda approval: approvals.append(approval) or True,
            )
            return approvals[0].approval_key

        changed_cwd = OrchestratorContext(
            **{**self.context.__dict__, "cwd": child}
        )
        changed_workspace = OrchestratorContext(
            **{**self.context.__dict__, "workspace_identity": (1, 2, 4)}
        )
        keys = {
            key(_Request(command="true")),
            key(_Request(command="false")),
            key(_Request(command="true"), changed_cwd),
            key(_Request(command="true"), changed_workspace),
            key(_Request(
                command="true",
                permissions=SandboxPermissions.WITH_ADDITIONAL_PERMISSIONS,
                additional=AdditionalPermissionProfile(
                    network=NetworkPermissions(enabled=True)
                ),
            )),
        }

        self.assertEqual(len(keys), 5)


if __name__ == "__main__":
    unittest.main()

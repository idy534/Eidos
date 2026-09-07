from __future__ import annotations

import threading
from pathlib import Path
from typing import Literal

from eidos_runtime.models import EidosFrozenStrictModel

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.runtime.approval import ApprovalCoordinator, APPROVAL_REJECTION_GUIDANCE
from eidos_runtime.runtime.permission_policy import PermissionDisposition, PermissionPolicyEvaluator
from eidos_runtime.sandbox.permissions import (
    AdditionalPermissionProfile, BasePermissionProfile, materialize_effective_profile,
)


class PermissionIntent(EidosFrozenStrictModel):
    kind: Literal["permission_request"] = "permission_request"
    summary: str = "Allow additional permissions for this run"
    permissions: AdditionalPermissionProfile
    grant_scope: Literal["run"] = "run"
    reason: str | None = None
    command: str | None = None
    cwd: str | None = None
    denial_category: str | None = None


class PermissionRequests:
    def __init__(self, store: SessionStore, approval: ApprovalCoordinator,
                 base: BasePermissionProfile) -> None:
        self.store = store
        self.approval = approval
        self.base = base

    def request(self, run_id: str, item: dict[str, object],
                permissions: AdditionalPermissionProfile, cancel: threading.Event,
                *, reason: str | None = None, command: str | None = None,
                cwd: str | None = None, denial_category: str | None = None,
                completed_result: dict[str, object] | None = None) -> str:
        try:
            permissions = permissions.model_copy(update={
                "file_system": tuple(entry.model_copy(update={
                    "path": str(Path(entry.path).resolve(strict=True)),
                }) for entry in sorted(permissions.file_system, key=lambda entry: (entry.path, entry.access.value, entry.recursive))),
            })
            materialize_effective_profile(self.base, permissions)
        except (OSError, ValueError, RuntimeError):
            return "permission_not_requestable"
        decision = PermissionPolicyEvaluator().evaluate(
            self.base, self.store.run_permission_grants(run_id), permissions,
        )
        if decision.disposition is not PermissionDisposition.ASK:
            return decision.reason_code
        intent = PermissionIntent(
            permissions=permissions, reason=reason, command=command,
            cwd=cwd, denial_category=denial_category,
        )
        description = intent.to_wire_dict()
        outcome = self.approval.request(
            run_id, item, description, cancel, request={**description, **(
                {"completedResult": completed_result} if completed_result is not None else {}
            )},
            transition_reason="permission_request",
        )
        return "permission_granted" if outcome.decision == "approve" else "user_rejected"


def network_permission_result(result: dict[str, object], code: str) -> dict[str, object]:
    granted = code in {"permission_granted", "already_granted"}
    return {**result, "outcome": "error", "code": (
        "permission_granted_retry_required" if granted
        else "user_rejected_network" if code == "user_rejected" else code
    ), "summary": (
        "Network permission was granted for this run. Retry the command; "
        "ordinary run_shell now inherits network access. The command was not replayed."
        if granted else APPROVAL_REJECTION_GUIDANCE if code == "user_rejected"
        else "Network permission cannot be requested."
    )}

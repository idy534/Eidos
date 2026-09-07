from __future__ import annotations

from enum import StrEnum
from pathlib import PurePath

from eidos_runtime.models import EidosFrozenStrictModel
from eidos_runtime.sandbox.permissions import (
    AdditionalPermissionProfile, BasePermissionProfile, FileSystemAccessMode,
    FileSystemPermissionEntry,
)


class PermissionDisposition(StrEnum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class PermissionDecision(EidosFrozenStrictModel):
    disposition: PermissionDisposition
    reason_code: str
    requested_permissions: AdditionalPermissionProfile | None = None


def _contains(entry: FileSystemPermissionEntry, path: str) -> bool:
    return entry.path == path or (
        entry.recursive and PurePath(entry.path) in PurePath(path).parents
    )


class PermissionPolicyEvaluator:
    """Pure policy over canonical permission paths; materialization owns filesystem I/O."""

    def evaluate(
        self,
        base: BasePermissionProfile,
        granted: AdditionalPermissionProfile | None,
        requested: AdditionalPermissionProfile | None,
        *,
        unsandboxed: bool = False,
    ) -> PermissionDecision:
        disposition = PermissionDisposition.ALLOW
        if unsandboxed:
            disposition = (
                PermissionDisposition.DENY if base.hard_confidentiality_denies
                else PermissionDisposition.ASK
            )
        if requested is not None:
            existing = (*base.entries, *(granted.file_system if granted else ()))
            for entry in requested.file_system:
                denied = (*base.permanent_denies, *base.hard_confidentiality_denies)
                protected = (*base.protected_metadata_paths, *(
                    (*base.protected_write_paths, *base.active_skill_roots)
                    if entry.access is FileSystemAccessMode.WRITE else ()
                ))
                if (entry.access is FileSystemAccessMode.WRITE and ".git" in PurePath(entry.path).parts) or any(
                    _contains(block, entry.path) or _contains(entry, block.path)
                    for block in denied
                ) or any(
                    entry.path == path or PurePath(path) in PurePath(entry.path).parents
                    or (entry.recursive and PurePath(entry.path) in PurePath(path).parents)
                    for path in protected
                ):
                    return PermissionDecision(
                        disposition=PermissionDisposition.DENY,
                        reason_code="permission_not_requestable",
                        requested_permissions=requested,
                    )
                if entry.access is not FileSystemAccessMode.DENY and not any(
                    _contains(prior, entry.path)
                    and (not entry.recursive or prior.recursive)
                    and (prior.access == entry.access or (
                        prior.access is FileSystemAccessMode.WRITE
                        and entry.access is FileSystemAccessMode.READ
                    )) for prior in existing
                ):
                    if disposition is not PermissionDisposition.DENY:
                        disposition = PermissionDisposition.ASK
            network = requested.network
            if network is not None and network.enabled is True and not (
                base.network_enabled or (granted and granted.network
                                         and granted.network.enabled is True)
            ) and disposition is not PermissionDisposition.DENY:
                disposition = PermissionDisposition.ASK
        return PermissionDecision(
            disposition=disposition,
            reason_code={
                PermissionDisposition.ALLOW: "already_granted",
                PermissionDisposition.ASK: "approval_required",
                PermissionDisposition.DENY: "permission_not_requestable",
            }[disposition],
            requested_permissions=requested,
        )

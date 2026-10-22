from __future__ import annotations

from enum import StrEnum
from pathlib import PurePath

from eidos_runtime.models import EidosFrozenStrictModel
from eidos_runtime.sandbox.permissions import (
    AdditionalPermissionProfile, BasePermissionProfile, FileSystemAccessMode,
    FileSystemPermissionEntry,
    is_approval_protected_write_exception,
    is_approval_write_exception,
)


class PermissionDisposition(StrEnum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class PermissionDecision(EidosFrozenStrictModel):
    disposition: PermissionDisposition
    reason_code: str
    requested_permissions: AdditionalPermissionProfile | None = None

    @property
    def requires_human_confirmation(self) -> bool:
        return self.reason_code == "dangerous_permission_requires_confirmation"


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
        if base.full_access:
            return PermissionDecision(disposition=PermissionDisposition.ALLOW, reason_code="already_granted", requested_permissions=requested)
        disposition = PermissionDisposition.ASK if unsandboxed else PermissionDisposition.ALLOW
        dangerous = unsandboxed
        if requested is not None:
            existing = (*base.entries, *(granted.file_system if granted else ()))
            for entry in requested.file_system:
                denied = (*base.permanent_denies, *base.hard_confidentiality_denies)
                protected = (*base.protected_metadata_paths, *(
                    base.protected_write_paths
                    if entry.access is FileSystemAccessMode.WRITE else ()
                ))
                protected_request = (
                    entry.access is FileSystemAccessMode.WRITE
                    and any(name in PurePath(entry.path).parts for name in (".git", ".agents", ".eidos"))
                    and not any(
                        PurePath(entry.path) == PurePath(root)
                        or PurePath(entry.path).is_relative_to(root)
                        for root in base.approval_write_roots
                    )
                ) or any(
                    (_contains(block, entry.path) or _contains(entry, block.path)) and not (
                        block in base.permanent_denies
                        and block not in base.hard_confidentiality_denies
                        and entry.access is FileSystemAccessMode.WRITE
                        and is_approval_write_exception(PurePath(entry.path), PurePath(block.path), base.approval_write_roots)
                    )
                    for block in denied
                ) or any(
                    (entry.path == path or PurePath(path) in PurePath(entry.path).parents
                    or (entry.recursive and PurePath(entry.path) in PurePath(path).parents)) and not (
                        entry.access is FileSystemAccessMode.WRITE
                        and (
                            is_approval_protected_write_exception(
                                PurePath(entry.path),
                                PurePath(path),
                                base.approval_write_roots,
                            )
                            if path in base.protected_write_paths
                            else is_approval_write_exception(
                                PurePath(entry.path),
                                PurePath(path),
                                base.approval_write_roots,
                            )
                        )
                    )
                    for path in protected
                )
                if protected_request:
                    dangerous = True
                # Default Shell already permits host reads outside protected
                # scopes. An explicit request adds no access there.
                if entry.access is FileSystemAccessMode.READ and not protected_request:
                    continue
                needs_explicit_grant = protected_request or (entry.access is FileSystemAccessMode.WRITE and any(
                    PurePath(entry.path).is_relative_to(root)
                    or (entry.recursive and PurePath(root).is_relative_to(entry.path))
                    for root in (*base.approval_write_roots, *base.active_skill_roots)
                ))
                prior_entries = (granted.file_system if granted else ()) if needs_explicit_grant else existing
                if entry.access is not FileSystemAccessMode.DENY and not any(
                    _contains(prior, entry.path)
                    and (not entry.recursive or prior.recursive)
                    and (prior.access == entry.access or (
                        prior.access is FileSystemAccessMode.WRITE
                        and entry.access is FileSystemAccessMode.READ
                    )) for prior in prior_entries
                ):
                    disposition = PermissionDisposition.ASK
            network = requested.network
            if network is not None and network.enabled is True and not (
                base.network_enabled or (granted and granted.network
                                         and granted.network.enabled is True)
            ):
                disposition = PermissionDisposition.ASK
        return PermissionDecision(
            disposition=disposition,
            reason_code="dangerous_permission_requires_confirmation" if dangerous and disposition is PermissionDisposition.ASK else {
                PermissionDisposition.ALLOW: "already_granted",
                PermissionDisposition.ASK: "approval_required",
                PermissionDisposition.DENY: "permission_not_requestable",
            }[disposition],
            requested_permissions=requested,
        )

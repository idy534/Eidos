from __future__ import annotations

from enum import StrEnum
import hashlib
import json
import os
from pathlib import Path
from typing import Literal

from pydantic import Field, StrictStr, field_validator, model_validator

from eidos_runtime.protocol.schemas import ClosedModel


class SandboxPermissions(StrEnum):
    USE_DEFAULT = "use_default"
    REQUIRE_ESCALATED = "require_escalated"
    WITH_ADDITIONAL_PERMISSIONS = "with_additional_permissions"


class FileSystemAccessMode(StrEnum):
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    DENY = "deny"


class FileSystemPermissionEntry(ClosedModel):
    path: StrictStr
    access: FileSystemAccessMode
    recursive: bool = True

    @field_validator("path")
    @classmethod
    def absolute_normal_path(cls, value: str) -> str:
        if "\x00" in value or not Path(value).is_absolute():
            raise ValueError("permission path must be absolute")
        return os.path.normpath(value)


class NetworkPermissions(ClosedModel):
    enabled: bool | None = None


class AdditionalPermissionProfile(ClosedModel):
    file_system: tuple[FileSystemPermissionEntry, ...] = Field(
        default=(), alias="fileSystem"
    )
    network: NetworkPermissions | None = None

    @property
    def is_empty(self) -> bool:
        return not self.file_system and (
            self.network is None or self.network.enabled is None
        )

    def validate_for(self, mode: SandboxPermissions) -> None:
        if (
            mode is not SandboxPermissions.WITH_ADDITIONAL_PERMISSIONS
            or self.is_empty
        ):
            raise ValueError("additional_permissions do not match sandbox mode")


class MaterializedFileSystemPermissionEntry(ClosedModel):
    requested_path: StrictStr = Field(alias="requestedPath")
    resolved_path: StrictStr = Field(alias="resolvedPath")
    access: FileSystemAccessMode
    recursive: bool = True
    source: Literal[
        "base",
        "additional",
        "permanent_deny",
        "hard_deny",
        "protected_write",
    ]


class BasePermissionProfile(ClosedModel):
    workspace_roots: tuple[StrictStr, ...] = Field(alias="workspaceRoots")
    entries: tuple[FileSystemPermissionEntry, ...]
    permanent_denies: tuple[FileSystemPermissionEntry, ...] = Field(
        default=(), alias="permanentDenies"
    )
    hard_confidentiality_denies: tuple[FileSystemPermissionEntry, ...] = Field(
        default=(), alias="hardConfidentialityDenies"
    )
    network_enabled: bool = Field(default=False, alias="networkEnabled")
    runtime_roots: tuple[StrictStr, ...] = Field(default=(), alias="runtimeRoots")
    protected_metadata_paths: tuple[StrictStr, ...] = Field(
        default=(), alias="protectedMetadataPaths"
    )
    protected_write_paths: tuple[StrictStr, ...] = Field(
        default=(), alias="protectedWritePaths"
    )

    @classmethod
    def for_workspace(
        cls,
        *,
        workspace_root: Path,
        protected_paths: tuple[Path, ...] = (),
        protected_write_paths: tuple[Path, ...] = (),
        hard_confidentiality_paths: tuple[Path, ...] = (),
        runtime_roots: tuple[Path, ...] = (),
    ) -> "BasePermissionProfile":
        workspace = workspace_root.resolve(strict=True)
        protected = tuple(path.resolve(strict=False) for path in protected_paths)
        protected_write = tuple(
            path.resolve(strict=False) for path in protected_write_paths
        )
        hard = tuple(
            path.resolve(strict=False) for path in hard_confidentiality_paths
        )
        return cls(
            workspaceRoots=(str(workspace),),
            entries=(
                FileSystemPermissionEntry(
                    path=str(workspace),
                    access=FileSystemAccessMode.WRITE,
                ),
            ),
            permanentDenies=tuple(
                FileSystemPermissionEntry(
                    path=str(path),
                    access=FileSystemAccessMode.DENY,
                )
                for path in protected
            ),
            hardConfidentialityDenies=tuple(
                FileSystemPermissionEntry(
                    path=str(path),
                    access=FileSystemAccessMode.DENY,
                )
                for path in hard
            ),
            runtimeRoots=tuple(
                str(path.resolve(strict=True))
                for path in runtime_roots
                if path.exists()
            ),
            protectedMetadataPaths=tuple(str(path) for path in protected),
            protectedWritePaths=tuple(str(path) for path in protected_write),
        )


class EffectivePermissionProfile(ClosedModel):
    workspace_roots: tuple[StrictStr, ...] = Field(alias="workspaceRoots")
    entries: tuple[MaterializedFileSystemPermissionEntry, ...]
    permanent_denies: tuple[MaterializedFileSystemPermissionEntry, ...] = Field(
        alias="permanentDenies"
    )
    hard_confidentiality_denies: tuple[
        MaterializedFileSystemPermissionEntry, ...
    ] = Field(alias="hardConfidentialityDenies")
    network_enabled: bool = Field(alias="networkEnabled")
    runtime_roots: tuple[StrictStr, ...] = Field(alias="runtimeRoots")
    protected_metadata_paths: tuple[StrictStr, ...] = Field(
        alias="protectedMetadataPaths"
    )
    protected_write_paths: tuple[StrictStr, ...] = Field(
        alias="protectedWritePaths"
    )
    profile_hash: StrictStr = Field(alias="profileHash")

    @model_validator(mode="after")
    def valid_hash(self) -> "EffectivePermissionProfile":
        if len(self.profile_hash) != 64:
            raise ValueError("invalid profile hash")
        return self

    def summary(self) -> dict[str, object]:
        additional = tuple(
            entry for entry in self.entries if entry.source == "additional"
        )
        return {
            "read": [
                entry.resolved_path
                for entry in additional
                if entry.access is FileSystemAccessMode.READ
            ],
            "write": [
                entry.resolved_path
                for entry in additional
                if entry.access is FileSystemAccessMode.WRITE
            ],
            "execute": [
                entry.resolved_path
                for entry in additional
                if entry.access is FileSystemAccessMode.EXECUTE
            ],
            "deny": [
                entry.resolved_path
                for entry in (*self.permanent_denies, *self.hard_confidentiality_denies)
            ],
            "networkEnabled": self.network_enabled,
        }


class SandboxType(StrEnum):
    MACOS_SEATBELT = "macos_seatbelt"
    NONE = "none"


class SandboxAttempt(ClosedModel):
    ordinal: int = Field(ge=0, le=1)
    sandbox: SandboxType
    sandbox_requested: bool = Field(alias="sandboxRequested")
    permissions: EffectivePermissionProfile
    sandbox_cwd: StrictStr = Field(alias="sandboxCwd")
    workspace_roots: tuple[StrictStr, ...] = Field(alias="workspaceRoots")
    profile_hash: StrictStr | None = Field(default=None, alias="profileHash")
    escalation_reason: StrictStr | None = Field(
        default=None, alias="escalationReason"
    )


def materialize_effective_profile(
    base: BasePermissionProfile,
    additional: AdditionalPermissionProfile | None = None,
) -> EffectivePermissionProfile:
    protected = tuple(Path(path) for path in base.protected_metadata_paths)
    protected_write = tuple(Path(path) for path in base.protected_write_paths)
    entries = _materialize_entries(base.entries, "base")
    if additional is not None:
        for requested in additional.file_system:
            resolved = Path(requested.path).resolve(strict=True)
            if requested.access is not FileSystemAccessMode.DENY and any(
                _paths_overlap(resolved, path) for path in protected
            ):
                raise ValueError("additional permission targets protected Eidos state")
            if requested.access is FileSystemAccessMode.WRITE and any(
                _paths_overlap(resolved, path) for path in protected_write
            ):
                raise ValueError("additional permission would modify Eidos runtime")
        entries += _materialize_entries(additional.file_system, "additional")
    permanent = _materialize_entries(base.permanent_denies, "permanent_deny")
    hard = _materialize_entries(
        base.hard_confidentiality_denies, "hard_deny"
    )
    if additional is not None:
        hard += tuple(
            entry.model_copy(update={"source": "hard_deny"})
            for entry in entries
            if entry.source == "additional"
            and entry.access is FileSystemAccessMode.DENY
        )
    entries = _deduplicate(entries)
    permanent = _deduplicate(permanent)
    hard = _deduplicate(hard)
    payload = {
        "workspaceRoots": base.workspace_roots,
        "entries": [
            entry.model_dump(mode="json", by_alias=True) for entry in entries
        ],
        "permanentDenies": [
            entry.model_dump(mode="json", by_alias=True) for entry in permanent
        ],
        "hardConfidentialityDenies": [
            entry.model_dump(mode="json", by_alias=True) for entry in hard
        ],
        "networkEnabled": (
            additional.network.enabled
            if additional is not None
            and additional.network is not None
            and additional.network.enabled is not None
            else base.network_enabled
        ),
        "runtimeRoots": base.runtime_roots,
        "protectedMetadataPaths": base.protected_metadata_paths,
        "protectedWritePaths": base.protected_write_paths,
    }
    profile_hash = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return EffectivePermissionProfile(
        workspaceRoots=base.workspace_roots,
        entries=entries,
        permanentDenies=permanent,
        hardConfidentialityDenies=hard,
        networkEnabled=payload["networkEnabled"],
        runtimeRoots=base.runtime_roots,
        protectedMetadataPaths=base.protected_metadata_paths,
        protectedWritePaths=base.protected_write_paths,
        profileHash=profile_hash,
    )


def unsandboxed_execution_allowed(
    effective_profile: EffectivePermissionProfile,
) -> bool:
    return not effective_profile.hard_confidentiality_denies


def _materialize_entries(
    entries: tuple[FileSystemPermissionEntry, ...],
    source: Literal[
        "base",
        "additional",
        "permanent_deny",
        "hard_deny",
        "protected_write",
    ],
) -> tuple[MaterializedFileSystemPermissionEntry, ...]:
    result = []
    for entry in entries:
        resolved = Path(entry.path).resolve(
            strict=source in {"base", "additional"}
        )
        result.append(
            MaterializedFileSystemPermissionEntry(
                requestedPath=entry.path,
                resolvedPath=str(resolved),
                access=entry.access,
                recursive=entry.recursive,
                source=source,
            )
        )
    return tuple(result)


def _deduplicate(
    entries: tuple[MaterializedFileSystemPermissionEntry, ...],
) -> tuple[MaterializedFileSystemPermissionEntry, ...]:
    unique: dict[tuple[str, FileSystemAccessMode, bool], MaterializedFileSystemPermissionEntry] = {}
    for entry in entries:
        unique.setdefault(
            (entry.resolved_path, entry.access, entry.recursive), entry
        )
    return tuple(
        sorted(
            unique.values(),
            key=lambda entry: (
                os.fsencode(entry.resolved_path),
                entry.access.value,
                entry.recursive,
            ),
        )
    )


def _paths_overlap(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents

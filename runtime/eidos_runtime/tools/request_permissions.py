from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import ClassVar

from pydantic import Field, model_validator

from eidos_runtime.models import EidosFrozenStrictModel
from eidos_runtime.sandbox.permissions import AdditionalPermissionProfile, FileSystemAccessMode
from eidos_runtime.tools.contracts import StrictToolModel, result_model
from eidos_runtime.tools.registry import AdapterToolRuntime, ToolProvenance, ToolRegistryEntry, ToolSpec


class RequestPermissionsInput(EidosFrozenStrictModel):
    reason: str | None = Field(default=None, max_length=2000)
    permissions: AdditionalPermissionProfile

    @model_validator(mode="after")
    def actual_permission(self):
        if self.permissions.network and self.permissions.network.enabled is False:
            raise ValueError("permission_request_must_grant_access")
        if not self.permissions.file_system and not (
            self.permissions.network and self.permissions.network.enabled is True
        ):
            raise ValueError("permission_request_empty")
        if any(entry.access is FileSystemAccessMode.DENY for entry in self.permissions.file_system):
            raise ValueError("permission_request_must_grant_access")
        return self


class PermissionResultData(StrictToolModel):
    SUCCESS_REQUIRED: ClassVar[tuple[str, ...]] = ()


@dataclass(frozen=True)
class PermissionToolRuntime(AdapterToolRuntime):
    def invoke(self, context, run_id, item, call, cancel):
        return context.invoke_permission(self, run_id, item, call, cancel)


class PermissionAdapter:
    def execute(self, arguments, cancel):
        raise RuntimeError("permission requests require ApprovalCoordinator")


def request_permissions_entry() -> ToolRegistryEntry:
    spec = ToolSpec(
        name="request_permissions",
        description=("Request additional filesystem or network permissions before continuing "
                     "the task. At least one actual permission is required. The request waits "
                     "for user approval; approved permissions last for the current run."),
        sideEffect="none", approvalRequired=False, timeoutSeconds=600,
        inputSchema=RequestPermissionsInput.model_json_schema(by_alias=True),
        resultSchema=result_model(PermissionResultData).model_json_schema(by_alias=True),
    )
    provenance = ToolProvenance(
        kind="builtin", sourceId="eidos.permissions", sourceVersion="1",
        contentHash=hashlib.sha256(spec.model_dump_json().encode()).hexdigest(),
    )
    adapter = PermissionAdapter()
    return ToolRegistryEntry(
        spec, provenance, adapter, RequestPermissionsInput, PermissionResultData,
        runtime=PermissionToolRuntime(adapter, spec, provenance),
    )

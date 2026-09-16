from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import threading
from typing import ClassVar

from pydantic import Field, StrictInt, StrictStr, ValidationError, field_validator

from eidos_runtime.db.database import WorkspaceIdentity
from eidos_runtime.models import EidosFrozenStrictModel
from eidos_runtime.tools.contracts import _read_path, result_model
from eidos_runtime.tools.registry import ToolProvenance, ToolRegistryEntry, ToolSpec
from eidos_runtime.tools.workspace import canonical_tool_result
from eidos_runtime.workspace.reader import (
    WorkspacePathError,
    WorkspaceReader,
    file_version,
    validate_workspace_relative_path,
)


class OutputDeclaration(EidosFrozenStrictModel):
    path: StrictStr = Field(min_length=1, max_length=512)
    title: StrictStr | None = Field(default=None, min_length=1, max_length=120)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _read_path(value)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str | None) -> str | None:
        if value is not None and (
            not value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value)
        ):
            raise ValueError("invalid_output_title")
        return value


class DeclareOutputsInput(EidosFrozenStrictModel):
    outputs: list[OutputDeclaration] = Field(min_length=1, max_length=20)


class DeclaredOutput(OutputDeclaration):
    size_bytes: StrictInt = Field(ge=0, le=9_007_199_254_740_991)
    version: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")


class DeclareOutputsResultData(EidosFrozenStrictModel):
    SUCCESS_REQUIRED: ClassVar[tuple[str, ...]] = ("execution_root", "outputs")
    execution_root: StrictStr | None = Field(default=None, max_length=4096)
    outputs: list[DeclaredOutput] | None = Field(default=None, min_length=1, max_length=20)


@dataclass(frozen=True)
class DeclareOutputsAdapter:
    workspace: WorkspaceIdentity

    def execute(
        self, arguments: dict[str, object], cancel: threading.Event
    ) -> dict[str, object]:
        try:
            request = DeclareOutputsInput.model_validate(arguments)
            outputs: dict[str, DeclaredOutput] = {}
            with WorkspaceReader(self.workspace) as reader:
                for output in request.outputs:
                    if cancel.is_set():
                        raise WorkspacePathError("canceled")
                    path = Path(output.path)
                    if path.is_absolute():
                        if not path.is_relative_to(self.workspace.path):
                            raise WorkspacePathError("workspace_boundary_violation")
                        path = path.relative_to(self.workspace.path)
                    normalized = "/".join(validate_workspace_relative_path(str(path)))
                    metadata = reader.stat_file(normalized, cancel=cancel)
                    outputs[normalized] = DeclaredOutput(
                        path=normalized, title=output.title,
                        size_bytes=metadata.st_size, version=file_version(metadata),
                    )
                # Recheck the batch before returning; a failed declaration publishes no files.
                for output in outputs.values():
                    if file_version(reader.stat_file(output.path, cancel=cancel)) != output.version:
                        raise WorkspacePathError("workspace_changed")
            if cancel.is_set():
                raise WorkspacePathError("canceled")
            data = DeclareOutputsResultData(
                execution_root=str(self.workspace.path), outputs=list(outputs.values()),
            )
        except ValidationError:
            return _error("invalid_arguments", "Invalid output declaration")
        except WorkspacePathError as error:
            return _error(error.code, "Outputs were not declared: " + error.code)
        except OSError:
            return _error("file_unavailable", "Outputs could not be inspected")
        return canonical_tool_result("declare_outputs", {
            "outcome": "success", "code": "ok",
            "summary": f"Declared {len(outputs)} output files. File existence is verified, not content quality.",
            "data": data.to_wire_dict(exclude_none=True),
            "sideEffectsMayExist": False, "reconciliationRequired": False,
        }, data_model=DeclareOutputsResultData)


def declare_outputs_entry(workspace: WorkspaceIdentity) -> ToolRegistryEntry:
    input_schema = DeclareOutputsInput.model_json_schema(by_alias=True)
    result_schema = result_model(DeclareOutputsResultData).model_json_schema(by_alias=True)
    encoded = json.dumps(
        (input_schema, result_schema), ensure_ascii=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return ToolRegistryEntry(
        spec=ToolSpec.model_validate({
            "name": "declare_outputs",
            "description": (
                "Declare existing files as deliverables for the user, after generation/editing "
                "and before the final answer. Pass 1-20 outputs with path and optional title. "
                "Paths must be workspace-relative or absolute inside this Run's workspace. "
                "Any file format is allowed. Declare only intended deliverables, not sources, "
                "dependencies, caches, build scripts or QA intermediates unless requested. "
                "Each call adds or updates the named files; omitted files are unchanged. "
                "Declare a file again after revising it. This tool inspects file metadata only; "
                "it does not create files, prove authorship or validate their contents. "
                "If any file fails verification, the entire call declares nothing."
            ),
            # The declaration exists only as the controller's normal committed ToolResult.
            "sideEffect": "none", "approvalRequired": False,
            "timeoutSeconds": 5, "batchPolicy": "single", "visibility": "direct",
            "inputSchema": input_schema, "resultSchema": result_schema,
            "modelProjectionPolicy": "generic", "contractVersion": 1,
        }),
        provenance=ToolProvenance.model_validate({
            "kind": "builtin", "sourceId": "eidos.declare-outputs", "sourceVersion": "1",
            "contentHash": hashlib.sha256(encoded).hexdigest(),
        }),
        adapter=DeclareOutputsAdapter(workspace),
        input_model=DeclareOutputsInput, result_data_model=DeclareOutputsResultData,
    )


def _error(code: str, summary: str) -> dict[str, object]:
    return canonical_tool_result("declare_outputs", {
        "outcome": "error", "code": code, "summary": summary, "data": {},
        "sideEffectsMayExist": False, "reconciliationRequired": False,
    }, data_model=DeclareOutputsResultData)

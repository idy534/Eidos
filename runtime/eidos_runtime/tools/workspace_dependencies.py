from __future__ import annotations

import hashlib
from importlib.metadata import PackageNotFoundError, distribution
import json
import os
from pathlib import Path
import stat
import sys
import sysconfig
import threading
from collections.abc import Callable, Mapping
from typing import ClassVar, Literal

from pydantic import Field, StrictStr, ValidationError

from eidos_runtime.models import EidosFrozenStrictModel
from eidos_runtime.tools.contracts import StrictToolModel, result_model
from eidos_runtime.tools.registry import (
    ToolProvenance,
    ToolRegistryEntry,
    ToolSpec,
)
from eidos_runtime.workspace.search_driver import (
    PINNED_RIPGREP_VERSION,
    RipgrepBinaryResolver,
    SearchDriverError,
)


_MAX_EXECUTABLE_BYTES = 256 * 1024 * 1024
_MAX_PYTHON_PATHS = 16


class WorkspaceDependencyError(RuntimeError):
    pass


class WorkspaceExecutable(EidosFrozenStrictModel):
    name: str
    path: str
    version: str
    sha256: str


class WorkspacePythonPackage(EidosFrozenStrictModel):
    name: str
    import_name: str
    version: str


class WorkspaceDependencySnapshot(EidosFrozenStrictModel):
    source: str
    executables: tuple[WorkspaceExecutable, ...]
    python_path: tuple[str, ...]
    python_packages: tuple[WorkspacePythonPackage, ...]


class WorkspaceDependenciesInput(StrictToolModel):
    pass


class WorkspaceExecutableData(StrictToolModel):
    name: StrictStr
    path: StrictStr
    version: StrictStr
    sha256: StrictStr = Field(pattern=r"^[0-9a-f]{64}$")


class WorkspacePythonPackageData(StrictToolModel):
    name: StrictStr
    import_name: StrictStr = Field(alias="importName")
    version: StrictStr


class WorkspaceDependencyDiagnosticData(StrictToolModel):
    index: int = Field(ge=0)
    requirement_type: Literal[
        "python-package", "node-package", "executable"
    ] = Field(alias="requirementType")
    name: StrictStr
    requested_version: StrictStr = Field(alias="requestedVersion")
    required: bool
    status: Literal["ready", "missing", "incompatible"]
    reason: StrictStr = Field(max_length=512)
    available_version: StrictStr | None = Field(
        default=None, alias="availableVersion", max_length=128
    )
    resolved_path: StrictStr | None = Field(
        default=None, alias="resolvedPath", max_length=4_096
    )


class WorkspaceDependencyBindingData(StrictToolModel):
    skill_qualified_id: StrictStr = Field(
        alias="skillQualifiedId", min_length=1, max_length=256
    )
    binding_id: StrictStr = Field(
        alias="bindingId",
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$",
    )
    status: Literal["ready", "missing", "incompatible", "invalid"]
    code: StrictStr | None = Field(default=None, max_length=128)
    manifest_sha256: StrictStr | None = Field(
        default=None, alias="manifestSha256", pattern=r"^[0-9a-f]{64}$"
    )
    requirements_sha256: StrictStr | None = Field(
        default=None, alias="requirementsSha256", pattern=r"^[0-9a-f]{64}$"
    )
    diagnostics: list[WorkspaceDependencyDiagnosticData] = Field(
        default_factory=list, max_length=32
    )


class WorkspaceDependenciesResultData(StrictToolModel):
    SUCCESS_REQUIRED: ClassVar[tuple[str, ...]] = (
        "source",
        "executables",
        "python_path",
        "python_packages",
    )

    source: StrictStr | None = None
    executables: list[WorkspaceExecutableData] | None = None
    python_path: list[StrictStr] | None = Field(default=None, alias="pythonPath")
    python_packages: list[WorkspacePythonPackageData] | None = Field(
        default=None,
        alias="pythonPackages",
    )
    default_dependency_binding_id: StrictStr | None = Field(
        default=None,
        alias="defaultDependencyBindingId",
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,255}$",
    )
    active_skill_dependency_bindings: list[WorkspaceDependencyBindingData] = Field(
        default_factory=list,
        alias="activeSkillDependencyBindings",
        max_length=32,
    )
    manifest_sha256: StrictStr | None = Field(
        default=None,
        alias="manifestSha256",
        pattern=r"^[0-9a-f]{64}$",
    )
    snapshot_sha256: StrictStr | None = Field(
        default=None,
        alias="snapshotSha256",
        pattern=r"^[0-9a-f]{64}$",
    )
    runtime_dependency_error: StrictStr | None = Field(
        default=None,
        alias="runtimeDependencyError",
        max_length=128,
    )


class WorkspaceDependencyCatalog:
    """Resolve trusted application-owned dependencies behind one snapshot interface."""

    def __init__(
        self,
        *,
        python_executable: Path,
        python_paths: tuple[Path, ...],
        ripgrep_executable: Path,
        owner_uid: int,
        python_version: str,
        ripgrep_version: str,
        python_packages: tuple[WorkspacePythonPackage, ...],
    ) -> None:
        self._python_executable = Path(python_executable)
        self._python_paths = tuple(Path(path) for path in python_paths)
        self._ripgrep_executable = Path(ripgrep_executable)
        self._owner_uid = owner_uid
        self._python_version = python_version
        self._ripgrep_version = ripgrep_version
        self._python_packages = python_packages

    @classmethod
    def from_runtime(cls) -> WorkspaceDependencyCatalog:
        try:
            ripgrep = RipgrepBinaryResolver().resolve()
        except SearchDriverError as error:
            raise WorkspaceDependencyError("dependency_unavailable:rg") from error
        python_paths = _explicit_runtime_python_paths()
        return cls(
            python_executable=Path(sys.executable),
            python_paths=python_paths,
            ripgrep_executable=ripgrep,
            owner_uid=os.getuid(),
            python_version=(
                f"{sys.version_info.major}.{sys.version_info.minor}."
                f"{sys.version_info.micro}"
            ),
            ripgrep_version=PINNED_RIPGREP_VERSION,
            python_packages=_runtime_python_packages(python_paths),
        )

    def snapshot(self) -> WorkspaceDependencySnapshot:
        python = _verified_executable(
            "python3", self._python_executable, self._owner_uid, self._python_version
        )
        ripgrep = _verified_executable(
            "rg", self._ripgrep_executable, self._owner_uid, self._ripgrep_version
        )
        python_paths: list[str] = []
        seen: set[Path] = set()
        for candidate in self._python_paths:
            try:
                canonical = candidate.resolve(strict=True)
            except OSError:
                continue
            if not canonical.is_dir() or canonical in seen:
                continue
            seen.add(canonical)
            python_paths.append(str(canonical))
            if len(python_paths) >= _MAX_PYTHON_PATHS:
                break
        return WorkspaceDependencySnapshot(
            source="eidos_runtime",
            executables=(python, ripgrep),
            python_path=tuple(python_paths),
            python_packages=self._python_packages,
        )


class WorkspaceDependenciesTool:
    def __init__(
        self,
        catalog_factory: Callable[[], WorkspaceDependencyCatalog] | None = None,
        metadata_provider: Callable[[], Mapping[str, object]] | None = None,
    ) -> None:
        self._catalog_factory = catalog_factory or WorkspaceDependencyCatalog.from_runtime
        self._metadata_provider = metadata_provider

    def execute(
        self, _arguments: dict[str, object], cancel: threading.Event
    ) -> dict[str, object]:
        if cancel.is_set():
            return _tool_error("tool_canceled", "Dependency discovery was canceled")
        try:
            snapshot = self._catalog_factory().snapshot()
        except WorkspaceDependencyError:
            return _tool_error(
                "workspace_dependencies_unavailable",
                "Verified workspace dependencies are unavailable",
            )
        data: dict[str, object] = {
            "source": snapshot.source,
            "executables": [
                value.model_dump(mode="json") for value in snapshot.executables
            ],
            "pythonPath": list(snapshot.python_path),
            "pythonPackages": [
                value.model_dump(mode="json", by_alias=True)
                for value in snapshot.python_packages
            ],
        }
        if self._metadata_provider is not None:
            try:
                data.update(_bounded_binding_metadata(self._metadata_provider()))
            except Exception:
                # Legacy discovery remains available when a binding projection
                # is unavailable.  A failed binding is never used for Shell.
                data.update({
                    "defaultDependencyBindingId": None,
                    "activeSkillDependencyBindings": [],
                })
        return {
            "schemaVersion": 1,
            "toolContractVersion": 1,
            "toolName": "workspace_dependencies",
            "outcome": "success",
            "code": "ok",
            "summary": "Verified workspace dependencies are available",
            "data": data,
            "sideEffectsMayExist": False,
            "reconciliationRequired": False,
        }


def workspace_dependencies_entry(
    catalog_factory: Callable[[], WorkspaceDependencyCatalog] | None = None,
    metadata_provider: Callable[[], Mapping[str, object]] | None = None,
) -> ToolRegistryEntry:
    input_schema = WorkspaceDependenciesInput.model_json_schema(by_alias=True)
    result_schema = result_model(WorkspaceDependenciesResultData).model_json_schema(
        by_alias=True
    )
    encoded = json.dumps(
        (input_schema, result_schema),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    tool = WorkspaceDependenciesTool(catalog_factory, metadata_provider)
    return ToolRegistryEntry(
        spec=ToolSpec.model_validate({
            "name": "workspace_dependencies",
            "description": (
                "Return verified Eidos-owned executable paths and Python package roots "
                "for workspace artifact tasks. If an active Skill has a runtime "
                "declaration, select the matching activeSkillDependencyBindings entry "
                "by skillQualifiedId; use defaultDependencyBindingId only for an "
                "unbound command. Binding IDs select verified environments and do not "
                "grant paths or environment values."
            ),
            "sideEffect": "none",
            "approvalRequired": False,
            "timeoutSeconds": 5,
            "batchPolicy": "parallel",
            "visibility": "direct",
            "inputSchema": input_schema,
            "resultSchema": result_schema,
            "modelProjectionPolicy": "generic",
            "contractVersion": 1,
        }),
        provenance=ToolProvenance.model_validate({
            "kind": "builtin",
            "sourceId": "eidos.workspace-dependencies",
            "sourceVersion": "1",
            "contentHash": hashlib.sha256(encoded).hexdigest(),
        }),
        adapter=tool,
        input_model=WorkspaceDependenciesInput,
        result_data_model=WorkspaceDependenciesResultData,
    )


def _verified_executable(
    name: str,
    candidate: Path,
    owner_uid: int,
    version: str,
) -> WorkspaceExecutable:
    try:
        canonical = candidate.resolve(strict=True)
        metadata = canonical.stat()
    except OSError as error:
        raise WorkspaceDependencyError(f"dependency_unavailable:{name}") from error
    if not stat.S_ISREG(metadata.st_mode) or not metadata.st_mode & 0o111:
        raise WorkspaceDependencyError(f"dependency_not_executable:{name}")
    if metadata.st_uid not in {0, owner_uid}:
        raise WorkspaceDependencyError(f"dependency_owner_invalid:{name}")
    if metadata.st_size <= 0 or metadata.st_size > _MAX_EXECUTABLE_BYTES:
        raise WorkspaceDependencyError(f"dependency_size_invalid:{name}")
    digest = hashlib.sha256()
    try:
        with canonical.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
    except OSError as error:
        raise WorkspaceDependencyError(f"dependency_unreadable:{name}") from error
    return WorkspaceExecutable(
        name=name,
        path=str(canonical),
        version=version,
        sha256=digest.hexdigest(),
    )


def _tool_error(code: str, summary: str) -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "toolContractVersion": 1,
        "toolName": "workspace_dependencies",
        "outcome": "unavailable" if code == "workspace_dependencies_unavailable" else "interrupted",
        "code": code,
        "summary": summary,
        "data": {},
        "sideEffectsMayExist": False,
        "reconciliationRequired": False,
    }


def _explicit_runtime_python_paths() -> tuple[Path, ...]:
    """Return application/interpreter roots without importing ambient PYTHONPATH."""

    package_root = Path(__file__).resolve().parents[2]
    configured = sysconfig.get_paths()
    candidates = (
        package_root / "python",
        package_root / "lib" / "python",
        package_root,
        Path(configured.get("purelib", "")),
        Path(configured.get("platlib", "")),
    )
    result: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        if not candidate.is_absolute():
            continue
        canonical = candidate.resolve(strict=False)
        if canonical in seen or not canonical.is_dir():
            continue
        seen.add(canonical)
        result.append(canonical)
        if len(result) >= _MAX_PYTHON_PATHS:
            break
    return tuple(result)


def _runtime_python_packages(
    roots: tuple[Path, ...],
) -> tuple[WorkspacePythonPackage, ...]:
    try:
        package = distribution("python-docx")
    except PackageNotFoundError:
        return ()
    location = Path(package.locate_file("")).resolve(strict=False)
    if not any(_path_contained(location, root) for root in roots):
        return ()
    return (
        WorkspacePythonPackage(
            name="python-docx",
            import_name="docx",
            version=package.version,
        ),
    )


def _path_contained(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _bounded_binding_metadata(
    metadata: Mapping[str, object],
) -> dict[str, object]:
    """Keep only the bounded binding projection exposed by the builtin tool."""

    if not isinstance(metadata, Mapping):
        raise TypeError("runtime dependency metadata must be a mapping")
    result: dict[str, object] = {
        "defaultDependencyBindingId": None,
        "activeSkillDependencyBindings": [],
    }
    default = metadata.get("defaultDependencyBindingId")
    if default is not None:
        try:
            validated = WorkspaceDependenciesResultData.model_validate({
                "defaultDependencyBindingId": default,
            })
        except ValidationError:
            validated = None
        if validated is not None:
            result["defaultDependencyBindingId"] = (
                validated.default_dependency_binding_id
            )
    for field_name, output_name in (
        ("runtimeDependencyError", "runtimeDependencyError"),
        ("manifestSha256", "manifestSha256"),
        ("snapshotSha256", "snapshotSha256"),
    ):
        value = metadata.get(field_name)
        if value is None:
            continue
        try:
            validated = WorkspaceDependenciesResultData.model_validate({
                field_name: value,
            })
        except ValidationError:
            continue
        result[output_name] = validated.model_dump(
            mode="json",
            by_alias=True,
            exclude_none=False,
        )[output_name]
    active = metadata.get("activeSkillDependencyBindings")
    if not isinstance(active, (tuple, list)):
        return result
    normalized_active: list[dict[str, object]] = []
    for item in active[:32]:
        if not isinstance(item, Mapping):
            continue
        try:
            validated = WorkspaceDependencyBindingData.model_validate(item)
        except ValidationError:
            continue
        normalized_active.append(validated.model_dump(
            mode="json",
            by_alias=True,
            exclude_none=True,
        ))
    result["activeSkillDependencyBindings"] = normalized_active
    return result


__all__ = [
    "WorkspaceDependencyCatalog",
    "WorkspaceDependencyError",
    "WorkspaceDependencySnapshot",
    "WorkspaceExecutable",
    "WorkspacePythonPackage",
    "WorkspaceDependenciesInput",
    "WorkspaceDependencyBindingData",
    "WorkspaceDependencyDiagnosticData",
    "WorkspaceDependenciesResultData",
    "workspace_dependencies_entry",
]

from __future__ import annotations

import re
from typing import Literal

from packaging.utils import canonicalize_name
from pydantic import Field, field_validator, model_validator

from eidos_runtime.models.base import EidosFrozenStrictModel
from eidos_runtime.models.skill_runtime import RuntimeRequirements


_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_BUNDLE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_EXECUTABLE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_PYTHON_PACKAGE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")
_NODE_PACKAGE_NAME_PATTERN = re.compile(
    r"^(?:@[A-Za-z0-9._~-]+/)?[A-Za-z0-9._~-]{1,128}$"
)
_IMPORT_NAME_PATTERN = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$"
)
MAX_RUNTIME_PATH_CHARS = 1024
MAX_MANIFEST_EXECUTABLES = 256
MAX_MANIFEST_ROOTS = 256
MAX_MANIFEST_PACKAGES = 256
MAX_MANIFEST_FILES = 32 * 1024
MAX_MANIFEST_DEPENDENCIES = 32


def _validate_relative_path(value: str) -> str:
    if not value or len(value) > MAX_RUNTIME_PATH_CHARS:
        raise ValueError("path must be a non-empty bounded relative path")
    if "\x00" in value or "\\" in value:
        raise ValueError("path must use contained POSIX separators")
    if value.startswith("/") or value.startswith("~"):
        raise ValueError("path must be relative")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ValueError("path must not contain dot or empty segments")
    return value


def _validate_non_empty_version(value: str) -> str:
    if not value or len(value) > 128 or any(
        character in value for character in ("\x00", "\r", "\n")
    ):
        raise ValueError("version must be a non-empty bounded string")
    return value


class RuntimeDependencyExecutable(EidosFrozenStrictModel):
    name: str = Field(min_length=1, max_length=128)
    path: str = Field(max_length=MAX_RUNTIME_PATH_CHARS)
    version: str = Field(min_length=1, max_length=128)
    sha256: str = Field(pattern=_SHA256_PATTERN)

    _validate_path = field_validator("path")(_validate_relative_path)
    _validate_version = field_validator("version")(_validate_non_empty_version)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if _EXECUTABLE_NAME_PATTERN.fullmatch(value) is None:
            raise ValueError("executable name must be a simple name")
        return value


class RuntimeDependencyPythonPackage(EidosFrozenStrictModel):
    name: str = Field(min_length=1, max_length=256)
    import_name: str = Field(min_length=1, max_length=256)
    version: str = Field(min_length=1, max_length=128)

    _validate_version = field_validator("version")(_validate_non_empty_version)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if _PYTHON_PACKAGE_NAME_PATTERN.fullmatch(value) is None:
            raise ValueError("Python package name must be a simple distribution name")
        return value

    @field_validator("import_name")
    @classmethod
    def _validate_import_name(cls, value: str) -> str:
        if _IMPORT_NAME_PATTERN.fullmatch(value) is None:
            raise ValueError("Python importName must be a dotted import name")
        return value


class RuntimeDependencyNodePackage(EidosFrozenStrictModel):
    name: str = Field(min_length=1, max_length=256)
    version: str = Field(min_length=1, max_length=128)

    _validate_version = field_validator("version")(_validate_non_empty_version)

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        if _NODE_PACKAGE_NAME_PATTERN.fullmatch(value) is None:
            raise ValueError("Node package name is invalid")
        return value


class RuntimeDependencyInventoryFile(EidosFrozenStrictModel):
    path: str = Field(max_length=MAX_RUNTIME_PATH_CHARS)
    sha256: str = Field(pattern=_SHA256_PATTERN)

    _validate_path = field_validator("path")(_validate_relative_path)


class RuntimeDependencyManifest(EidosFrozenStrictModel):
    schema_version: Literal[1]
    bundle_id: str = Field(min_length=1, max_length=128)
    bundle_version: str = Field(min_length=1, max_length=128)
    target: Literal["darwin-arm64"]
    executables: tuple[RuntimeDependencyExecutable, ...] = Field(
        default=(), max_length=MAX_MANIFEST_EXECUTABLES
    )
    python_path: tuple[str, ...] = Field(default=(), max_length=MAX_MANIFEST_ROOTS)
    python_packages: tuple[RuntimeDependencyPythonPackage, ...] = Field(
        default=(), max_length=MAX_MANIFEST_PACKAGES
    )
    node_modules: str | None = Field(default=None, max_length=MAX_RUNTIME_PATH_CHARS)
    node_loader: str | None = Field(default=None, max_length=MAX_RUNTIME_PATH_CHARS)
    node_packages: tuple[RuntimeDependencyNodePackage, ...] = Field(
        default=(), max_length=MAX_MANIFEST_PACKAGES
    )
    native_bin_paths: tuple[str, ...] = Field(default=(), max_length=MAX_MANIFEST_ROOTS)
    files: tuple[RuntimeDependencyInventoryFile, ...] = Field(
        default=(), max_length=MAX_MANIFEST_FILES
    )

    _validate_python_paths = field_validator("python_path")(
        lambda values: tuple(_validate_relative_path(value) for value in values)
    )
    _validate_node_modules = field_validator("node_modules")(
        lambda value: None if value is None else _validate_relative_path(value)
    )
    _validate_node_loader = field_validator("node_loader")(
        lambda value: None if value is None else _validate_relative_path(value)
    )
    _validate_native_paths = field_validator("native_bin_paths")(
        lambda values: tuple(_validate_relative_path(value) for value in values)
    )

    @field_validator("bundle_id")
    @classmethod
    def _validate_bundle_id(cls, value: str) -> str:
        if _BUNDLE_ID_PATTERN.fullmatch(value) is None:
            raise ValueError("bundleId is invalid")
        return value

    @field_validator("bundle_version")
    @classmethod
    def _validate_bundle_version(cls, value: str) -> str:
        return _validate_non_empty_version(value)

    @model_validator(mode="after")
    def _validate_unique_declarations(self) -> RuntimeDependencyManifest:
        executable_names = [value.name for value in self.executables]
        if len(executable_names) != len(set(executable_names)):
            raise ValueError("executables must not contain duplicate names")
        try:
            python_names = [canonicalize_name(value.name) for value in self.python_packages]
        except (TypeError, ValueError) as error:
            raise ValueError("pythonPackages contains an invalid distribution name") from error
        python_imports = [value.import_name for value in self.python_packages]
        if len(python_names) != len(set(python_names)):
            raise ValueError("pythonPackages must not contain duplicate names")
        if len(python_imports) != len(set(python_imports)):
            raise ValueError("pythonPackages must not contain duplicate importName values")
        node_names = [value.name for value in self.node_packages]
        if len(node_names) != len(set(node_names)):
            raise ValueError("nodePackages must not contain duplicate names")
        inventory_paths = [value.path for value in self.files]
        if len(inventory_paths) != len(set(inventory_paths)):
            raise ValueError("files must not contain duplicate paths")
        if self.node_packages:
            if self.node_modules is None:
                raise ValueError("nodeModules is required when nodePackages is declared")
        if self.node_loader is not None and self.node_loader not in inventory_paths:
            raise ValueError("nodeLoader must be included in files inventory")
        return self


class RuntimeDependencyExecutablePath(EidosFrozenStrictModel):
    name: str = Field(min_length=1, max_length=128)
    path: str = Field(max_length=MAX_RUNTIME_PATH_CHARS)
    version: str = Field(min_length=1, max_length=128)
    sha256: str = Field(pattern=_SHA256_PATTERN)


class RuntimeDependencyPackageRoot(EidosFrozenStrictModel):
    type: Literal["python-package", "node-package"]
    name: str = Field(min_length=1, max_length=256)
    import_name: str | None = Field(default=None, max_length=256)
    version: str = Field(min_length=1, max_length=128)
    path: str = Field(max_length=MAX_RUNTIME_PATH_CHARS)


class RuntimeDependencyVerifiedFile(EidosFrozenStrictModel):
    path: str = Field(max_length=MAX_RUNTIME_PATH_CHARS)
    sha256: str = Field(pattern=_SHA256_PATTERN)


class RuntimeDependencyDiagnostic(EidosFrozenStrictModel):
    index: int = Field(ge=0, le=MAX_MANIFEST_DEPENDENCIES - 1)
    requirement_type: Literal["python-package", "node-package", "executable"]
    name: str = Field(min_length=1, max_length=256)
    requested_version: str = Field(min_length=1, max_length=128)
    required: bool
    status: Literal["ready", "missing", "incompatible"]
    reason: str = Field(min_length=1, max_length=256)
    available_version: str | None = Field(default=None, max_length=128)
    resolved_path: str | None = Field(default=None, max_length=MAX_RUNTIME_PATH_CHARS)


class RuntimeDependencySnapshot(EidosFrozenStrictModel):
    schema_version: Literal[1] = 1
    manifest_path: str = Field(max_length=MAX_RUNTIME_PATH_CHARS)
    bundle_root: str = Field(max_length=MAX_RUNTIME_PATH_CHARS)
    manifest_sha256: str = Field(pattern=_SHA256_PATTERN)
    bundle_id: str = Field(min_length=1, max_length=128)
    bundle_version: str = Field(min_length=1, max_length=128)
    target: Literal["darwin-arm64"]
    snapshot_sha256: str = Field(pattern=_SHA256_PATTERN)
    executables: tuple[RuntimeDependencyExecutablePath, ...] = Field(
        default=(), max_length=MAX_MANIFEST_EXECUTABLES
    )
    python_path: tuple[str, ...] = Field(default=(), max_length=MAX_MANIFEST_ROOTS)
    python_package_roots: tuple[RuntimeDependencyPackageRoot, ...] = Field(
        default=(), max_length=MAX_MANIFEST_PACKAGES
    )
    node_modules: str | None = Field(default=None, max_length=MAX_RUNTIME_PATH_CHARS)
    node_loader: str | None = Field(default=None, max_length=MAX_RUNTIME_PATH_CHARS)
    node_package_roots: tuple[RuntimeDependencyPackageRoot, ...] = Field(
        default=(), max_length=MAX_MANIFEST_PACKAGES
    )
    native_bin_paths: tuple[str, ...] = Field(default=(), max_length=MAX_MANIFEST_ROOTS)
    files: tuple[RuntimeDependencyVerifiedFile, ...] = Field(
        default=(), max_length=MAX_MANIFEST_FILES
    )
    runtime_roots: tuple[str, ...] = Field(default=(), max_length=MAX_MANIFEST_ROOTS)
    protected_write_paths: tuple[str, ...] = Field(
        default=(), max_length=MAX_MANIFEST_ROOTS
    )

    @property
    def inventory(self) -> tuple[RuntimeDependencyVerifiedFile, ...]:
        return self.files

    def canonical_hash(self) -> str:
        return self.snapshot_sha256


class RuntimeDependencyBinding(EidosFrozenStrictModel):
    schema_version: Literal[1] = 1
    binding_id: str = Field(pattern=_SHA256_PATTERN)
    context_id: str = Field(default="", max_length=512)
    requirement_sha256: str = Field(pattern=_SHA256_PATTERN)
    snapshot_sha256: str = Field(pattern=_SHA256_PATTERN)
    requirements: RuntimeRequirements | None = None
    ready: bool
    diagnostics: tuple[RuntimeDependencyDiagnostic, ...] = Field(
        default=(), max_length=MAX_MANIFEST_DEPENDENCIES
    )
    executables: tuple[RuntimeDependencyExecutablePath, ...] = Field(
        default=(), max_length=MAX_MANIFEST_EXECUTABLES
    )
    python_path: tuple[str, ...] = Field(default=(), max_length=MAX_MANIFEST_ROOTS)
    python_package_roots: tuple[RuntimeDependencyPackageRoot, ...] = Field(
        default=(), max_length=MAX_MANIFEST_PACKAGES
    )
    node_modules: str | None = Field(default=None, max_length=MAX_RUNTIME_PATH_CHARS)
    node_loader: str | None = Field(default=None, max_length=MAX_RUNTIME_PATH_CHARS)
    node_package_roots: tuple[RuntimeDependencyPackageRoot, ...] = Field(
        default=(), max_length=MAX_MANIFEST_PACKAGES
    )
    native_bin_paths: tuple[str, ...] = Field(default=(), max_length=MAX_MANIFEST_ROOTS)
    files: tuple[RuntimeDependencyVerifiedFile, ...] = Field(
        default=(), max_length=MAX_MANIFEST_FILES
    )
    runtime_roots: tuple[str, ...] = Field(default=(), max_length=MAX_MANIFEST_ROOTS)
    protected_write_paths: tuple[str, ...] = Field(
        default=(), max_length=MAX_MANIFEST_ROOTS
    )

    @property
    def binding_hash(self) -> str:
        return self.binding_id

    @property
    def resolved_executables(self) -> tuple[RuntimeDependencyExecutablePath, ...]:
        return self.executables

    @property
    def resolved_python_package_roots(
        self,
    ) -> tuple[RuntimeDependencyPackageRoot, ...]:
        return self.python_package_roots

    @property
    def resolved_node_package_roots(
        self,
    ) -> tuple[RuntimeDependencyPackageRoot, ...]:
        return self.node_package_roots


__all__ = [
    "RuntimeDependencyDiagnostic",
    "RuntimeDependencyExecutable",
    "RuntimeDependencyExecutablePath",
    "RuntimeDependencyInventoryFile",
    "RuntimeDependencyBinding",
    "RuntimeDependencyManifest",
    "RuntimeDependencyNodePackage",
    "RuntimeDependencyPackageRoot",
    "RuntimeDependencyPythonPackage",
    "RuntimeDependencySnapshot",
    "RuntimeDependencyVerifiedFile",
]

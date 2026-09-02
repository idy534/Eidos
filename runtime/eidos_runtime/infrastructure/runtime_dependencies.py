from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Iterable
from email import policy
from email.parser import BytesParser

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version
from pydantic import ValidationError
from semantic_version import NpmSpec, Version as NpmVersion

from eidos_runtime.models.runtime_dependencies import (
    RuntimeDependencyBinding,
    RuntimeDependencyDiagnostic,
    RuntimeDependencyExecutable,
    RuntimeDependencyExecutablePath,
    RuntimeDependencyInventoryFile,
    RuntimeDependencyManifest,
    RuntimeDependencyNodePackage,
    RuntimeDependencyPackageRoot,
    RuntimeDependencyPythonPackage,
    RuntimeDependencySnapshot,
    RuntimeDependencyVerifiedFile,
)
from eidos_runtime.models.skill_runtime import (
    ExecutableRequirement,
    NodePackageRequirement,
    PythonPackageRequirement,
    RuntimeRequirements,
)


MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_MANIFEST_FILES = 32 * 1024
MAX_RUNTIME_FILE_BYTES = 512 * 1024 * 1024
MAX_RUNTIME_INVENTORY_BYTES = 1024 * 1024 * 1024
MAX_RUNTIME_TRAVERSAL_ENTRIES = 128 * 1024
MAX_RUNTIME_TRAVERSAL_DEPTH = 128
MAX_RUNTIME_PATH_CHARS = 1024
MAX_PACKAGE_METADATA_BYTES = 2 * 1024 * 1024
_MAX_HASH_CHUNK_BYTES = 1024 * 1024


class RuntimeDependencyCatalogError(ValueError):
    """A bundled dependency catalog is not valid for its declared bundle."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(f"{code}: {detail}")


class RuntimeDependencyVerificationError(RuntimeDependencyCatalogError):
    """A previously resolved binding is no longer safe to launch."""


RuntimeDependencyError = RuntimeDependencyCatalogError


class RuntimeDependencyCatalog:
    """Load, verify, snapshot, and resolve one application-owned bundle."""

    def __init__(
        self,
        *,
        manifest_path: Path,
        bundle_root: Path,
        manifest: RuntimeDependencyManifest,
        manifest_bytes: bytes,
        manifest_sha256: str,
    ) -> None:
        self._manifest_path = manifest_path
        self._bundle_root = bundle_root
        self._manifest = manifest
        self._manifest_bytes = manifest_bytes
        self._manifest_sha256 = manifest_sha256
        self._snapshot = self._build_snapshot()

    @classmethod
    def from_manifest(cls, manifest_path: Path) -> RuntimeDependencyCatalog:
        """Load one rooted JSON manifest and fail only this catalog on error."""

        try:
            path = Path(manifest_path)
            bundle_root = path.parent.resolve(strict=True)
            canonical_manifest_path = path.resolve(strict=True)
            _verify_directory(bundle_root, "bundle_root")
            if not _is_contained(canonical_manifest_path, bundle_root):
                raise RuntimeDependencyCatalogError(
                    "manifest_outside_bundle", "manifest target escapes bundle root"
                )
            _verify_regular_file(canonical_manifest_path, "manifest")
            manifest_bytes = _read_bounded_file(
                canonical_manifest_path,
                MAX_MANIFEST_BYTES,
                "manifest",
            )
            parsed = json.loads(
                manifest_bytes.decode("utf-8"),
                object_pairs_hook=_reject_duplicate_keys,
            )
            if not isinstance(parsed, dict):
                raise RuntimeDependencyCatalogError(
                    "manifest_invalid", "manifest root must be an object"
                )
            manifest = RuntimeDependencyManifest.model_validate_json(manifest_bytes)
            manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
        except RuntimeDependencyCatalogError:
            raise
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValidationError) as error:
            raise RuntimeDependencyCatalogError(
                "manifest_invalid", str(error)
            ) from None
        return cls(
            manifest_path=canonical_manifest_path,
            bundle_root=bundle_root,
            manifest=manifest,
            manifest_bytes=manifest_bytes,
            manifest_sha256=manifest_sha256,
        )

    def snapshot(self) -> RuntimeDependencySnapshot:
        """Return the immutable verified snapshot captured at catalog creation."""

        return self._snapshot

    def resolve(
        self,
        requirements: RuntimeRequirements | None,
        *,
        context_id: str = "",
    ) -> RuntimeDependencyBinding:
        """Resolve declarations against this immutable snapshot in declaration order."""

        _verify_context_id(context_id)
        return self._resolve_with_snapshot(requirements, context_id, self._snapshot)

    def verify_binding(self, binding: RuntimeDependencyBinding) -> None:
        """Re-verify a ready binding immediately before an executable launch."""

        if not isinstance(binding, RuntimeDependencyBinding):
            raise RuntimeDependencyVerificationError(
                "binding_invalid", "binding must be a RuntimeDependencyBinding"
            )
        if not binding.ready:
            raise RuntimeDependencyVerificationError(
                "binding_not_ready", "binding contains missing or incompatible requirements"
            )
        if binding.snapshot_sha256 != self._snapshot.snapshot_sha256:
            raise RuntimeDependencyVerificationError(
                "binding_snapshot_mismatch", "binding is from another catalog snapshot"
            )
        try:
            current_snapshot = self._build_snapshot(reverify=True)
        except RuntimeDependencyCatalogError as error:
            raise RuntimeDependencyVerificationError(
                "binding_verification_failed", str(error)
            ) from None
        if current_snapshot.snapshot_sha256 != binding.snapshot_sha256:
            raise RuntimeDependencyVerificationError(
                "binding_snapshot_changed", "bundle contents changed after resolution"
            )
        current_binding = self._resolve_with_snapshot(
            binding.requirements,
            binding.context_id,
            current_snapshot,
        )
        if current_binding != binding:
            raise RuntimeDependencyVerificationError(
                "binding_changed", "binding declarations or resolved paths changed"
            )

    def _build_snapshot(self, *, reverify: bool = False) -> RuntimeDependencySnapshot:
        if reverify:
            try:
                current_manifest_bytes = _read_bounded_file(
                    self._manifest_path,
                    MAX_MANIFEST_BYTES,
                    "manifest",
                )
            except RuntimeDependencyCatalogError:
                raise
            current_manifest_sha256 = hashlib.sha256(current_manifest_bytes).hexdigest()
            if current_manifest_sha256 != self._manifest_sha256:
                raise RuntimeDependencyCatalogError(
                    "manifest_changed", "manifest bytes changed after catalog creation"
                )
        _verify_directory(self._bundle_root, "bundle_root")
        _validate_manifest_versions(self._manifest)
        inventory = self._verify_inventory()
        inventory_by_relative = {
            entry.path: entry for entry in self._manifest.files
        }
        executable_paths = self._verify_executables(inventory_by_relative)
        python_paths = _verify_roots(
            self._bundle_root,
            self._manifest.python_path,
            "pythonPath",
        )
        native_bin_paths = _verify_roots(
            self._bundle_root,
            self._manifest.native_bin_paths,
            "nativeBinPaths",
        )
        node_modules = None
        if self._manifest.node_modules is not None:
            node_modules = _resolve_declared_path(
                self._bundle_root,
                self._manifest.node_modules,
                "nodeModules",
            )
            _verify_directory(node_modules, "nodeModules")
        node_loader = None
        if self._manifest.node_loader is not None:
            node_loader = _resolve_declared_path(
                self._bundle_root,
                self._manifest.node_loader,
                "nodeLoader",
            )
            _verify_expected_file(
                node_loader,
                inventory_by_relative.get(self._manifest.node_loader),
                "nodeLoader",
            )
        python_package_roots = self._verify_python_packages(
            python_paths,
            inventory_by_relative,
        )
        node_package_roots = self._verify_node_packages(
            node_modules,
            inventory_by_relative,
        )
        audit_roots = [*python_paths, *native_bin_paths]
        if node_modules is not None:
            audit_roots.append(node_modules)
        _verify_inventory_coverage(self._bundle_root, audit_roots, inventory_by_relative)
        runtime_roots = (str(self._bundle_root),)
        snapshot_without_hash = RuntimeDependencySnapshot(
            manifest_path=str(self._manifest_path),
            bundle_root=str(self._bundle_root),
            manifest_sha256=self._manifest_sha256,
            bundle_id=self._manifest.bundle_id,
            bundle_version=self._manifest.bundle_version,
            target=self._manifest.target,
            snapshot_sha256="0" * 64,
            executables=executable_paths,
            python_path=tuple(str(path) for path in python_paths),
            python_package_roots=python_package_roots,
            node_modules=None if node_modules is None else str(node_modules),
            node_loader=None if node_loader is None else str(node_loader),
            node_package_roots=node_package_roots,
            native_bin_paths=tuple(str(path) for path in native_bin_paths),
            files=inventory,
            runtime_roots=runtime_roots,
            protected_write_paths=runtime_roots,
        )
        snapshot_hash = _snapshot_hash(snapshot_without_hash)
        return snapshot_without_hash.model_copy(update={"snapshot_sha256": snapshot_hash})

    def _verify_inventory(self) -> tuple[RuntimeDependencyVerifiedFile, ...]:
        if len(self._manifest.files) > MAX_MANIFEST_FILES:
            raise RuntimeDependencyCatalogError(
                "inventory_too_large", "files inventory exceeds the bounded limit"
            )
        verified: list[RuntimeDependencyVerifiedFile] = []
        canonical_paths: set[Path] = set()
        total_bytes = 0
        for entry in self._manifest.files:
            path = _resolve_declared_path(self._bundle_root, entry.path, "files")
            if path in canonical_paths:
                raise RuntimeDependencyCatalogError(
                    "inventory_duplicate_target", f"{entry.path} resolves to a duplicate file"
                )
            canonical_paths.add(path)
            metadata = _verify_regular_file(path, f"files:{entry.path}")
            total_bytes += metadata.st_size
            if total_bytes > MAX_RUNTIME_INVENTORY_BYTES:
                raise RuntimeDependencyCatalogError(
                    "inventory_too_large", "files inventory exceeds the bounded byte limit"
                )
            _verify_expected_file(path, entry, f"files:{entry.path}")
            verified.append(
                RuntimeDependencyVerifiedFile(path=str(path), sha256=entry.sha256)
            )
        return tuple(sorted(verified, key=lambda value: value.path.encode("utf-8")))

    def _verify_executables(
        self,
        inventory: dict[str, RuntimeDependencyInventoryFile],
    ) -> tuple[RuntimeDependencyExecutablePath, ...]:
        verified: list[RuntimeDependencyExecutablePath] = []
        for entry in self._manifest.executables:
            inventory_entry = inventory.get(entry.path)
            if inventory_entry is None:
                raise RuntimeDependencyCatalogError(
                    "executable_not_in_inventory",
                    f"executable {entry.name!r} is not in files inventory",
                )
            if inventory_entry.sha256 != entry.sha256:
                raise RuntimeDependencyCatalogError(
                    "executable_hash_mismatch",
                    f"executable {entry.name!r} disagrees with files inventory",
                )
            path = _resolve_declared_path(self._bundle_root, entry.path, "executable")
            _verify_expected_file(path, entry, f"executable:{entry.name}", executable=True)
            verified.append(
                RuntimeDependencyExecutablePath(
                    name=entry.name,
                    path=str(path),
                    version=entry.version,
                    sha256=entry.sha256,
                )
            )
        return tuple(verified)

    def _verify_python_packages(
        self,
        python_paths: tuple[Path, ...],
        inventory: dict[str, RuntimeDependencyInventoryFile],
    ) -> tuple[RuntimeDependencyPackageRoot, ...]:
        roots: list[RuntimeDependencyPackageRoot] = []
        for package in self._manifest.python_packages:
            relative_import = package.import_name.replace(".", "/")
            candidates: list[Path] = []
            for python_path in python_paths:
                for candidate in (
                    python_path / relative_import,
                    python_path / f"{relative_import}.py",
                ):
                    try:
                        resolved = _resolve_path_candidate(
                            self._bundle_root,
                            candidate,
                            f"pythonPackage:{package.name}",
                        )
                    except RuntimeDependencyCatalogError as error:
                        if error.code == "path_missing":
                            continue
                        raise
                    if resolved not in candidates:
                        candidates.append(resolved)
            if len(candidates) != 1:
                reason = "not found" if not candidates else "ambiguous roots"
                raise RuntimeDependencyCatalogError(
                    "python_package_root_invalid",
                    f"Python package {package.name!r} has {reason}",
                )
            root = candidates[0]
            if root.is_dir():
                _verify_directory(root, f"pythonPackage:{package.name}")
            else:
                _verify_expected_file(
                    root,
                    _inventory_entry_for_canonical_path(
                        self._bundle_root, root, inventory
                    ),
                    f"pythonPackage:{package.name}",
                )
            _verify_python_distribution_metadata(
                self._bundle_root,
                python_paths,
                package,
                inventory,
            )
            roots.append(
                RuntimeDependencyPackageRoot(
                    type="python-package",
                    name=package.name,
                    import_name=package.import_name,
                    version=package.version,
                    path=str(root),
                )
            )
        return tuple(roots)

    def _verify_node_packages(
        self,
        node_modules: Path | None,
        inventory: dict[str, RuntimeDependencyInventoryFile],
    ) -> tuple[RuntimeDependencyPackageRoot, ...]:
        if self._manifest.node_packages and node_modules is None:
            raise RuntimeDependencyCatalogError(
                "node_modules_missing", "nodePackages require nodeModules"
            )
        if node_modules is None:
            return ()
        roots: list[RuntimeDependencyPackageRoot] = []
        node_modules_relative = self._manifest.node_modules
        if node_modules_relative is None:
            raise RuntimeDependencyCatalogError(
                "node_modules_missing", "nodeModules is required for package roots"
            )
        for package in self._manifest.node_packages:
            package_relative = f"{node_modules_relative}/{package.name}"
            root = _resolve_declared_path(
                self._bundle_root,
                package_relative,
                f"nodePackage:{package.name}",
            )
            _verify_directory(root, f"nodePackage:{package.name}")
            package_json = _resolve_path_candidate(
                self._bundle_root,
                root / "package.json",
                f"nodePackage:{package.name}:package.json",
            )
            _verify_expected_file(
                package_json,
                _inventory_entry_for_canonical_path(
                    self._bundle_root,
                    package_json,
                    inventory,
                ),
                f"nodePackage:{package.name}:package.json",
            )
            _verify_node_package_json(package_json, package)
            roots.append(
                RuntimeDependencyPackageRoot(
                    type="node-package",
                    name=package.name,
                    version=package.version,
                    path=str(root),
                )
            )
        return tuple(roots)

    def _resolve_with_snapshot(
        self,
        requirements: RuntimeRequirements | None,
        context_id: str,
        snapshot: RuntimeDependencySnapshot,
    ) -> RuntimeDependencyBinding:
        requirement_sha256 = _requirements_hash(requirements)
        diagnostics: list[RuntimeDependencyDiagnostic] = []
        if requirements is not None:
            for index, requirement in enumerate(requirements.dependencies):
                if isinstance(requirement, PythonPackageRequirement):
                    diagnostics.append(
                        _resolve_python_requirement(
                            index, requirement, snapshot.python_package_roots
                        )
                    )
                elif isinstance(requirement, NodePackageRequirement):
                    diagnostics.append(
                        _resolve_node_requirement(
                            index,
                            requirement,
                            snapshot.node_package_roots,
                            snapshot.executables,
                            snapshot.node_loader,
                        )
                    )
                elif isinstance(requirement, ExecutableRequirement):
                    diagnostics.append(
                        _resolve_executable_requirement(
                            index, requirement, snapshot.executables
                        )
                    )
                else:
                    raise RuntimeDependencyCatalogError(
                        "requirement_invalid", "unknown RuntimeRequirements variant"
                    )
        ready = all(
            not diagnostic.required or diagnostic.status == "ready"
            for diagnostic in diagnostics
        )
        binding_id = _binding_hash(snapshot.snapshot_sha256, requirement_sha256, context_id)
        return RuntimeDependencyBinding(
            binding_id=binding_id,
            context_id=context_id,
            requirement_sha256=requirement_sha256,
            snapshot_sha256=snapshot.snapshot_sha256,
            requirements=requirements,
            ready=ready,
            diagnostics=tuple(diagnostics),
            executables=snapshot.executables,
            python_path=snapshot.python_path,
            python_package_roots=snapshot.python_package_roots,
            node_modules=snapshot.node_modules,
            node_loader=snapshot.node_loader,
            node_package_roots=snapshot.node_package_roots,
            native_bin_paths=snapshot.native_bin_paths,
            files=snapshot.files,
            runtime_roots=snapshot.runtime_roots,
            protected_write_paths=snapshot.protected_write_paths,
        )


def _verify_python_distribution_metadata(
    bundle_root: Path,
    python_paths: tuple[Path, ...],
    package: RuntimeDependencyPythonPackage,
    inventory: dict[str, RuntimeDependencyInventoryFile],
) -> None:
    candidates: list[tuple[Path, RuntimeDependencyInventoryFile]] = []
    seen_paths: set[Path] = set()
    for entry in inventory.values():
        path = _resolve_declared_path(bundle_root, entry.path, "files")
        if path in seen_paths:
            continue
        for python_path in python_paths:
            try:
                relative = path.relative_to(python_path)
            except ValueError:
                continue
            if (
                len(relative.parts) == 2
                and relative.parts[0].endswith(".dist-info")
                and relative.parts[1] == "METADATA"
            ):
                _verify_expected_file(path, entry, f"pythonMetadata:{entry.path}")
                candidates.append((path, entry))
                seen_paths.add(path)
                break

    if not candidates:
        raise RuntimeDependencyCatalogError(
            "python_metadata_missing",
            f"Python package {package.name!r} has no dist-info METADATA",
        )

    matching: list[tuple[Path, str, str]] = []
    for path, _entry in candidates:
        metadata_name, metadata_version = _read_python_metadata(path)
        if canonicalize_name(metadata_name) == canonicalize_name(package.name):
            matching.append((path, metadata_name, metadata_version))
    if not matching:
        raise RuntimeDependencyCatalogError(
            "python_metadata_mismatch",
            f"Python package {package.name!r} is absent from dist-info METADATA",
        )
    if len(matching) != 1:
        raise RuntimeDependencyCatalogError(
            "python_metadata_ambiguous",
            f"Python package {package.name!r} has multiple matching METADATA files",
        )

    _metadata_path, _metadata_name, metadata_version = matching[0]
    try:
        expected_version = Version(package.version)
        actual_version = Version(metadata_version)
    except InvalidVersion as error:
        raise RuntimeDependencyCatalogError(
            "python_metadata_invalid",
            f"Python package {package.name!r} has an invalid version: {error}",
        ) from None
    if actual_version != expected_version:
        raise RuntimeDependencyCatalogError(
            "python_metadata_mismatch",
            f"Python package {package.name!r} METADATA version disagrees with manifest",
        )


def _read_python_metadata(path: Path) -> tuple[str, str]:
    content = _read_bounded_file(
        path,
        MAX_PACKAGE_METADATA_BYTES,
        f"pythonMetadata:{path}",
    )
    try:
        message = BytesParser(policy=policy.default).parsebytes(
            content,
            headersonly=True,
        )
    except (UnicodeError, ValueError) as error:
        raise RuntimeDependencyCatalogError(
            "python_metadata_invalid",
            f"Python METADATA cannot be parsed: {error}",
        ) from None
    if message.defects:
        raise RuntimeDependencyCatalogError(
            "python_metadata_invalid",
            "Python METADATA contains malformed headers",
        )
    names = [str(value).strip() for value in message.get_all("Name", [])]
    versions = [str(value).strip() for value in message.get_all("Version", [])]
    if len(names) != 1 or not names[0]:
        raise RuntimeDependencyCatalogError(
            "python_metadata_invalid",
            "Python METADATA must contain one non-empty Name header",
        )
    if len(versions) != 1 or not versions[0]:
        raise RuntimeDependencyCatalogError(
            "python_metadata_invalid",
            "Python METADATA must contain one non-empty Version header",
        )
    try:
        canonicalize_name(names[0])
        Version(versions[0])
    except (InvalidVersion, ValueError) as error:
        raise RuntimeDependencyCatalogError(
            "python_metadata_invalid",
            f"Python METADATA has invalid package identity: {error}",
        ) from None
    return names[0], versions[0]


def _verify_node_package_json(
    path: Path,
    package: RuntimeDependencyNodePackage,
) -> None:
    content = _read_bounded_file(
        path,
        MAX_PACKAGE_METADATA_BYTES,
        f"nodePackage:{package.name}:package.json",
    )
    try:
        payload = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeDependencyCatalogError(
            "node_package_metadata_invalid",
            f"Node package.json cannot be parsed: {error}",
        ) from None
    if not isinstance(payload, dict):
        raise RuntimeDependencyCatalogError(
            "node_package_metadata_invalid",
            "Node package.json must contain an object",
        )
    package_name = payload.get("name")
    package_version = payload.get("version")
    if not isinstance(package_name, str) or package_name != package.name:
        raise RuntimeDependencyCatalogError(
            "node_package_metadata_mismatch",
            f"Node package {package.name!r} package.json name disagrees with manifest",
        )
    if not isinstance(package_version, str) or not package_version:
        raise RuntimeDependencyCatalogError(
            "node_package_metadata_invalid",
            f"Node package {package.name!r} package.json has no version",
        )
    try:
        expected_version = NpmVersion(package.version)
        actual_version = NpmVersion(package_version)
    except ValueError as error:
        raise RuntimeDependencyCatalogError(
            "node_package_metadata_invalid",
            f"Node package {package.name!r} has an invalid version: {error}",
        ) from None
    if actual_version != expected_version:
        raise RuntimeDependencyCatalogError(
            "node_package_metadata_mismatch",
            f"Node package {package.name!r} package.json version disagrees with manifest",
        )


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise RuntimeDependencyCatalogError(
                "manifest_duplicate_key", f"duplicate JSON key {key!r}"
            )
        result[key] = value
    return result


def _verify_context_id(context_id: str) -> None:
    if not isinstance(context_id, str):
        raise RuntimeDependencyCatalogError(
            "context_id_invalid", "context_id must be a string"
        )
    if len(context_id) > 512 or "\x00" in context_id:
        raise RuntimeDependencyCatalogError(
            "context_id_invalid", "context_id is not bounded"
        )


def _sha256_json(value: object) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _requirements_hash(requirements: RuntimeRequirements | None) -> str:
    if requirements is None:
        return _sha256_json({"schemaVersion": 1, "dependencies": []})
    return _sha256_json(
        requirements.model_dump(mode="json", by_alias=True, exclude_none=True)
    )


def _snapshot_hash(snapshot: RuntimeDependencySnapshot) -> str:
    payload = snapshot.model_dump(mode="json", by_alias=True, exclude_none=True)
    payload.pop("snapshotSha256", None)
    return _sha256_json(payload)


def _binding_hash(snapshot_sha256: str, requirement_sha256: str, context_id: str) -> str:
    return _sha256_json(
        {
            "schemaVersion": 1,
            "snapshotSha256": snapshot_sha256,
            "requirementSha256": requirement_sha256,
            "contextId": context_id,
        }
    )


def _validate_manifest_versions(manifest: RuntimeDependencyManifest) -> None:
    try:
        Version(manifest.bundle_version)
        for executable in manifest.executables:
            Version(executable.version)
        for package in manifest.python_packages:
            Version(package.version)
        for package in manifest.node_packages:
            NpmVersion(package.version)
    except (InvalidVersion, ValueError) as error:
        raise RuntimeDependencyCatalogError(
            "manifest_version_invalid", str(error)
        ) from None


def _resolve_python_requirement(
    index: int,
    requirement: PythonPackageRequirement,
    packages: tuple[RuntimeDependencyPackageRoot, ...],
) -> RuntimeDependencyDiagnostic:
    matched = next(
        (
            value
            for value in packages
            if canonicalize_name(value.name) == canonicalize_name(requirement.name)
        ),
        None,
    )
    if matched is None:
        return _diagnostic(
            index,
            requirement.kind,
            requirement.name,
            requirement.version,
            requirement.required,
            "missing",
            "package_not_declared",
        )
    if matched.import_name != requirement.import_name:
        return _diagnostic(
            index,
            requirement.kind,
            requirement.name,
            requirement.version,
            requirement.required,
            "incompatible",
            "import_name_mismatch",
            matched.version,
            matched.path,
        )
    matches, reason = _python_version_matches(requirement.version, matched.version)
    return _diagnostic(
        index,
        requirement.kind,
        requirement.name,
        requirement.version,
        requirement.required,
        "ready" if matches else "incompatible",
        "ready" if matches else reason,
        matched.version,
        matched.path,
    )


def _resolve_node_requirement(
    index: int,
    requirement: NodePackageRequirement,
    packages: tuple[RuntimeDependencyPackageRoot, ...],
    executables: tuple[RuntimeDependencyExecutablePath, ...],
    node_loader: str | None,
) -> RuntimeDependencyDiagnostic:
    matched = next(
        (value for value in packages if value.name == requirement.name),
        None,
    )
    if matched is None:
        return _diagnostic(
            index,
            requirement.kind,
            requirement.name,
            requirement.version,
            requirement.required,
            "missing",
            "package_not_declared",
        )
    if node_loader is None or not any(value.name == "node" for value in executables):
        return _diagnostic(
            index,
            requirement.kind,
            requirement.name,
            requirement.version,
            requirement.required,
            "incompatible",
            "node_runtime_unavailable",
            matched.version,
            matched.path,
        )
    matches, reason = _node_version_matches(requirement.version, matched.version)
    return _diagnostic(
        index,
        requirement.kind,
        requirement.name,
        requirement.version,
        requirement.required,
        "ready" if matches else "incompatible",
        "ready" if matches else reason,
        matched.version,
        matched.path,
    )


def _resolve_executable_requirement(
    index: int,
    requirement: ExecutableRequirement,
    executables: tuple[RuntimeDependencyExecutablePath, ...],
) -> RuntimeDependencyDiagnostic:
    matched = next(
        (value for value in executables if value.name == requirement.name),
        None,
    )
    if matched is None:
        return _diagnostic(
            index,
            requirement.kind,
            requirement.name,
            requirement.version,
            requirement.required,
            "missing",
            "executable_not_declared",
        )
    matches, reason = _python_version_matches(requirement.version, matched.version)
    return _diagnostic(
        index,
        requirement.kind,
        requirement.name,
        requirement.version,
        requirement.required,
        "ready" if matches else "incompatible",
        "ready" if matches else reason,
        matched.version,
        matched.path,
    )


def _diagnostic(
    index: int,
    requirement_type: str,
    name: str,
    requested_version: str,
    required: bool,
    status: str,
    reason: str,
    available_version: str | None = None,
    resolved_path: str | None = None,
) -> RuntimeDependencyDiagnostic:
    return RuntimeDependencyDiagnostic(
        index=index,
        requirement_type=requirement_type,
        name=name,
        requested_version=requested_version,
        required=required,
        status=status,
        reason=reason,
        available_version=available_version,
        resolved_path=resolved_path,
    )


def _python_version_matches(requested: str, available: str) -> tuple[bool, str]:
    try:
        available_version = Version(available)
        try:
            specifier = SpecifierSet(requested)
        except InvalidSpecifier:
            specifier = SpecifierSet(f"=={Version(requested)}")
    except (InvalidSpecifier, InvalidVersion) as error:
        return False, f"invalid_python_version_specifier:{error}"
    return available_version in specifier, "version_incompatible"


def _node_version_matches(requested: str, available: str) -> tuple[bool, str]:
    try:
        available_version = NpmVersion(available)
        try:
            specifier = NpmSpec(requested)
        except ValueError:
            specifier = NpmSpec(f"={NpmVersion(requested)}")
    except ValueError as error:
        return False, f"invalid_node_version_specifier:{error}"
    return specifier.match(available_version), "version_incompatible"


def _is_contained(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _resolve_declared_path(root: Path, relative: str, label: str) -> Path:
    if len(relative) > MAX_RUNTIME_PATH_CHARS:
        raise RuntimeDependencyCatalogError(
            "path_invalid", f"{label} path is too long"
        )
    lexical = root.joinpath(*relative.split("/"))
    return _resolve_path_candidate(root, lexical, label)


def _resolve_path_candidate(root: Path, candidate: Path, label: str) -> Path:
    try:
        current = candidate
        while current != root:
            metadata = current.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                _verify_owner(metadata, f"{label} symlink")
                target = current.resolve(strict=True)
                if not _is_contained(target, root):
                    raise RuntimeDependencyCatalogError(
                        "path_symlink_escape", f"{label} symlink escapes bundle root"
                    )
            current = current.parent
        resolved = candidate.resolve(strict=True)
    except RuntimeDependencyCatalogError:
        raise
    except FileNotFoundError:
        raise RuntimeDependencyCatalogError(
            "path_missing", f"{label} path does not exist"
        ) from None
    except OSError as error:
        raise RuntimeDependencyCatalogError(
            "path_invalid", f"{label} path cannot be resolved: {error}"
        ) from None
    if not _is_contained(resolved, root):
        raise RuntimeDependencyCatalogError(
            "path_outside_bundle", f"{label} path escapes bundle root"
        )
    return resolved


def _verify_owner(metadata: os.stat_result, label: str) -> None:
    if metadata.st_uid not in {0, os.getuid()}:
        raise RuntimeDependencyCatalogError(
            "owner_invalid", f"{label} is not owned by root or the current user"
        )


def _verify_directory(path: Path, label: str) -> None:
    try:
        metadata = path.stat()
    except OSError as error:
        raise RuntimeDependencyCatalogError(
            "directory_invalid", f"{label} cannot be read: {error}"
        ) from None
    if not stat.S_ISDIR(metadata.st_mode):
        raise RuntimeDependencyCatalogError(
            "directory_invalid", f"{label} is not a directory"
        )
    _verify_owner(metadata, label)


def _verify_regular_file(path: Path, label: str) -> os.stat_result:
    try:
        metadata = path.stat()
    except OSError as error:
        raise RuntimeDependencyCatalogError(
            "file_invalid", f"{label} cannot be read: {error}"
        ) from None
    if not stat.S_ISREG(metadata.st_mode):
        raise RuntimeDependencyCatalogError(
            "file_type_invalid", f"{label} is not a regular file"
        )
    _verify_owner(metadata, label)
    if metadata.st_nlink > 1:
        raise RuntimeDependencyCatalogError(
            "file_type_invalid", f"{label} is a hard link"
        )
    if metadata.st_size > MAX_RUNTIME_FILE_BYTES:
        raise RuntimeDependencyCatalogError(
            "file_too_large", f"{label} exceeds the bounded file size"
        )
    return metadata


def _read_bounded_file(path: Path, limit: int, label: str) -> bytes:
    metadata = _verify_regular_file(path, label)
    if metadata.st_size > limit:
        raise RuntimeDependencyCatalogError(
            "file_too_large", f"{label} exceeds the bounded manifest size"
        )
    try:
        with path.open("rb") as stream:
            content = stream.read(limit + 1)
    except OSError as error:
        raise RuntimeDependencyCatalogError(
            "file_unreadable", f"{label} cannot be read: {error}"
        ) from None
    if len(content) > limit:
        raise RuntimeDependencyCatalogError(
            "file_too_large", f"{label} exceeds the bounded manifest size"
        )
    return content


def _hash_file(path: Path, label: str) -> str:
    digest = hashlib.sha256()
    total = 0
    try:
        with path.open("rb") as stream:
            while chunk := stream.read(_MAX_HASH_CHUNK_BYTES):
                total += len(chunk)
                if total > MAX_RUNTIME_FILE_BYTES:
                    raise RuntimeDependencyCatalogError(
                        "file_too_large", f"{label} exceeds the bounded file size"
                    )
                digest.update(chunk)
    except RuntimeDependencyCatalogError:
        raise
    except OSError as error:
        raise RuntimeDependencyCatalogError(
            "file_unreadable", f"{label} cannot be read: {error}"
        ) from None
    return digest.hexdigest()


def _verify_expected_file(
    path: Path,
    expected: RuntimeDependencyExecutable
    | RuntimeDependencyInventoryFile
    | None,
    label: str,
    *,
    executable: bool = False,
) -> None:
    if expected is None:
        raise RuntimeDependencyCatalogError(
            "file_not_in_inventory", f"{label} is not in files inventory"
        )
    metadata = _verify_regular_file(path, label)
    if executable and not metadata.st_mode & 0o111:
        raise RuntimeDependencyCatalogError(
            "executable_invalid", f"{label} is not executable"
        )
    actual_hash = _hash_file(path, label)
    if actual_hash != expected.sha256:
        raise RuntimeDependencyCatalogError(
            "hash_mismatch", f"{label} hash does not match the manifest"
        )


def _verify_roots(root: Path, relative_paths: tuple[str, ...], label: str) -> tuple[Path, ...]:
    roots: list[Path] = []
    for relative in relative_paths:
        resolved = _resolve_declared_path(root, relative, label)
        _verify_directory(resolved, f"{label}:{relative}")
        if resolved in roots:
            raise RuntimeDependencyCatalogError(
                "root_duplicate", f"{label} contains duplicate roots"
            )
        roots.append(resolved)
    return tuple(roots)


def _inventory_entry_for_canonical_path(
    bundle_root: Path,
    path: Path,
    inventory: dict[str, RuntimeDependencyInventoryFile],
) -> RuntimeDependencyInventoryFile | None:
    relative = path.relative_to(bundle_root).as_posix()
    for declared_path, entry in inventory.items():
        declared = _resolve_declared_path(bundle_root, declared_path, "files")
        if declared == path:
            return entry
    raise RuntimeDependencyCatalogError(
        "file_not_in_inventory", f"{relative} is not in files inventory"
    )


def _verify_inventory_coverage(
    bundle_root: Path,
    roots: Iterable[Path],
    inventory: dict[str, RuntimeDependencyInventoryFile],
) -> None:
    seen_roots: set[Path] = set()
    inventory_targets = {
        _resolve_declared_path(bundle_root, relative, "files")
        for relative in inventory
    }
    traversed_entries = 0
    discovered_files: set[Path] = set()
    total_bytes = 0
    for root in roots:
        if root in seen_roots:
            continue
        seen_roots.add(root)
        for current, directories, filenames in os.walk(
            root,
            topdown=True,
            followlinks=False,
            onerror=_raise_inventory_walk_error,
        ):
            directories.sort()
            filenames.sort()
            current_path = Path(current)
            try:
                depth = len(current_path.relative_to(root).parts)
            except ValueError:
                raise RuntimeDependencyCatalogError(
                    "inventory_walk_failed", "declared root escaped during traversal"
                ) from None
            if depth > MAX_RUNTIME_TRAVERSAL_DEPTH:
                raise RuntimeDependencyCatalogError(
                    "inventory_too_large", "declared roots exceed traversal depth"
                )
            traversed_entries += len(directories) + len(filenames)
            if traversed_entries > MAX_RUNTIME_TRAVERSAL_ENTRIES:
                raise RuntimeDependencyCatalogError(
                    "inventory_too_large", "declared roots contain too many entries"
                )
            for directory_name in directories:
                directory_path = current_path / directory_name
                try:
                    metadata = directory_path.lstat()
                except OSError as error:
                    raise RuntimeDependencyCatalogError(
                        "inventory_walk_failed", str(error)
                    ) from None
                if stat.S_ISLNK(metadata.st_mode):
                    raise RuntimeDependencyCatalogError(
                        "path_symlink_directory", "directory symlinks are not inventory roots"
                    )
                if not stat.S_ISDIR(metadata.st_mode):
                    raise RuntimeDependencyCatalogError(
                        "file_type_invalid", "declared package root contains a special entry"
                    )
                _verify_owner(metadata, f"inventory directory:{directory_path}")
            for filename in filenames:
                file_path = current_path / filename
                try:
                    metadata = file_path.lstat()
                except OSError as error:
                    raise RuntimeDependencyCatalogError(
                        "inventory_walk_failed", str(error)
                    ) from None
                if stat.S_ISLNK(metadata.st_mode):
                    _verify_owner(metadata, f"inventory symlink:{file_path}")
                    resolved = _resolve_path_candidate(
                        bundle_root,
                        file_path,
                        "inventory symlink",
                    )
                    target_metadata = _verify_regular_file(
                        resolved,
                        f"inventory symlink:{file_path}",
                    )
                    actual_path = resolved
                elif not stat.S_ISREG(metadata.st_mode):
                    raise RuntimeDependencyCatalogError(
                        "file_type_invalid", "declared package root contains a special file"
                    )
                else:
                    _verify_owner(metadata, f"inventory file:{file_path}")
                    target_metadata = metadata
                    actual_path = file_path
                if actual_path not in discovered_files:
                    discovered_files.add(actual_path)
                    total_bytes += target_metadata.st_size
                    if total_bytes > MAX_RUNTIME_INVENTORY_BYTES:
                        raise RuntimeDependencyCatalogError(
                            "inventory_too_large",
                            "declared roots exceed the bounded byte limit",
                        )
                if actual_path not in inventory_targets:
                    raise RuntimeDependencyCatalogError(
                        "inventory_unlisted_file",
                        f"file {file_path.relative_to(bundle_root)} is not in files inventory",
                    )


def _raise_inventory_walk_error(error: OSError) -> None:
    raise RuntimeDependencyCatalogError(
        "inventory_walk_failed",
        f"declared root cannot be traversed: {error}",
    ) from None


__all__ = [
    "MAX_MANIFEST_BYTES",
    "MAX_MANIFEST_FILES",
    "MAX_RUNTIME_FILE_BYTES",
    "MAX_RUNTIME_INVENTORY_BYTES",
    "MAX_RUNTIME_TRAVERSAL_DEPTH",
    "MAX_RUNTIME_TRAVERSAL_ENTRIES",
    "RuntimeDependencyCatalog",
    "RuntimeDependencyCatalogError",
    "RuntimeDependencyError",
    "RuntimeDependencyVerificationError",
]

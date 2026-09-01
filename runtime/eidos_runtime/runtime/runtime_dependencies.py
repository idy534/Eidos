from __future__ import annotations

from collections.abc import Iterable
import hashlib
import json
from pathlib import Path
import time

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.extensions.skill_manifest import SkillAgentMetadata
from eidos_runtime.extensions.skills import SkillCatalog, SkillCatalogSnapshot
from eidos_runtime.infrastructure.runtime_dependencies import (
    RuntimeDependencyCatalog,
    RuntimeDependencyCatalogError,
    RuntimeDependencyVerificationError,
)
from eidos_runtime.models.runtime_dependencies import (
    RuntimeDependencyBinding,
    RuntimeDependencySnapshot,
)
from eidos_runtime.models.runtime_dependency_records import (
    RuntimeDependencyBindingRecord,
    RuntimeDependencySnapshotRecord,
)
from eidos_runtime.models.skill_runtime import RuntimeRequirements
from eidos_runtime.runtime.events import RuntimeEvents
from eidos_runtime.sandbox.dependency_environment import (
    DependencyShellEnvironment,
)
from eidos_runtime.sandbox.permissions import BasePermissionProfile
from eidos_runtime.tools.contracts import RuntimeDependencyBindingProvenance


MAX_CONTEXT_ID_CHARS = 512
MAX_SKILL_ID_CHARS = 256
MAX_DIAGNOSTICS = 32
MAX_DIAGNOSTIC_CHARS = 256
MAX_SNAPSHOT_RECORD_BYTES = 512 * 1024


class RuntimeDependencyPathError(RuntimeDependencyCatalogError):
    """A catalog path is outside the trusted application runtime root."""


class RuntimeDependencyUnavailableError(RuntimeDependencyCatalogError):
    """No verified runtime catalog is available in this environment."""


class RuntimeDependencyCoordinator:
    """Coordinate immutable dependency bindings for one Run.

    The catalog owns manifest parsing, package matching, and file verification.
    This class owns the Run boundary, trusted Skill metadata, persistence, and
    projection into permissions and the child process environment.
    """

    def __init__(
        self,
        run_id: str,
        catalog: RuntimeDependencyCatalog | None = None,
        *,
        store: SessionStore | None = None,
        skills: SkillCatalog | None = None,
        skill_snapshot: SkillCatalogSnapshot | None = None,
        events: RuntimeEvents | None = None,
        catalog_error: RuntimeDependencyCatalogError | None = None,
        forbidden_roots: Iterable[Path | str] = (),
        manifest_path: Path | None = None,
    ) -> None:
        _validate_identifier(run_id, "run_id", MAX_CONTEXT_ID_CHARS)
        self.run_id = run_id
        self.store = store
        self.skills = skills
        self.skill_snapshot = skill_snapshot
        self.events = events
        self.catalog_error = catalog_error
        self._catalog = catalog
        self._manifest_path = manifest_path
        self._catalog_discovery_attempted = (
            catalog is not None or catalog_error is not None
        )
        snapshot = catalog.snapshot() if catalog is not None else None
        if snapshot is not None and not isinstance(
            snapshot, RuntimeDependencySnapshot
        ):
            raise RuntimeDependencyCatalogError(
                "snapshot_invalid",
                "Runtime dependency catalog returned an invalid snapshot",
            )
        self._snapshot = snapshot
        self._forbidden_roots = tuple(
            _canonical_path(value) for value in forbidden_roots
        )
        self._bindings: dict[str, RuntimeDependencyBinding] = {}
        self._skill_bindings: dict[str, RuntimeDependencyBinding] = {}
        self._default: RuntimeDependencyBinding | None = None
        self._snapshot_recorded = False
        if self._snapshot is not None:
            self._validate_snapshot(self._snapshot)

    @classmethod
    def for_run(
        cls,
        store: SessionStore,
        run_id: str,
        *,
        catalog: RuntimeDependencyCatalog | None = None,
        skills: SkillCatalog | None = None,
        skill_snapshot: SkillCatalogSnapshot | None = None,
        events: RuntimeEvents | None = None,
        manifest_path: Path | None = None,
    ) -> "RuntimeDependencyCoordinator":
        """Create a Run coordinator from the explicit Runtime manifest.

        Source development can run without a generated manifest.  In that
        case this coordinator remains disabled and legacy discovery keeps its
        existing Eidos interpreter adapter.  A packaged application fails
        closed when runtime.json is absent or invalid.
        """

        resolved_catalog = catalog
        catalog_error: RuntimeDependencyCatalogError | None = None
        data_directory = store.data_directory
        forbidden = (data_directory,) if data_directory is not None else ()
        try:
            return cls(
                run_id,
                resolved_catalog,
                store=store,
                skills=skills,
                skill_snapshot=skill_snapshot,
                events=events,
                catalog_error=catalog_error,
                forbidden_roots=forbidden,
                manifest_path=manifest_path,
            )
        except RuntimeDependencyCatalogError as error:
            return cls(
                run_id,
                None,
                store=store,
                skills=skills,
                skill_snapshot=skill_snapshot,
                events=events,
                catalog_error=error,
                forbidden_roots=forbidden,
                manifest_path=manifest_path,
            )

    from_run = for_run

    @property
    def available(self) -> bool:
        return self._catalog is not None and self._snapshot is not None

    @property
    def catalog_snapshot(self) -> RuntimeDependencySnapshot | None:
        return self._snapshot

    def set_skill_metadata_source(
        self,
        skills: SkillCatalog,
        skill_snapshot: SkillCatalogSnapshot,
    ) -> None:
        self.skills = skills
        self.skill_snapshot = skill_snapshot

    def default_binding(self) -> RuntimeDependencyBinding | None:
        """Resolve the base binding with ``requirements=None``."""

        if self._ensure_catalog() is None:
            return None
        if self._default is None:
            self._default = self.resolve(
                None,
                context_id=f"{self.run_id}:default",
            )
        return self._default

    def resolve(
        self,
        requirements: RuntimeRequirements | None,
        *,
        context_id: str | None = None,
        qualified_skill_id: str | None = None,
    ) -> RuntimeDependencyBinding:
        """Resolve and durably record one concrete binding."""

        if self._ensure_catalog() is None or self._snapshot is None:
            error_code = (
                self.catalog_error.code
                if self.catalog_error is not None
                else "catalog_unavailable"
            )
            raise RuntimeDependencyUnavailableError(
                error_code,
                "a verified runtime catalog is not available",
            )
        resolved_context = context_id or (
            f"{self.run_id}:default"
            if requirements is None
            else f"{self.run_id}:binding"
        )
        _validate_identifier(resolved_context, "context_id", MAX_CONTEXT_ID_CHARS)
        _validate_run_context(self.run_id, resolved_context)
        binding = self._catalog.resolve(
            requirements,
            context_id=resolved_context,
        )
        self._admit_binding(binding)
        self._bindings[binding.binding_id] = binding
        self._persist_binding(binding, qualified_skill_id=qualified_skill_id)
        return binding

    def binding_for_skill(
        self,
        qualified_skill_id: str,
        metadata: SkillAgentMetadata | None = None,
    ) -> RuntimeDependencyBinding | None:
        """Resolve the declaration from trusted metadata in the frozen Skill snapshot."""

        _validate_identifier(qualified_skill_id, "qualified_skill_id", MAX_SKILL_ID_CHARS)
        existing = self._skill_bindings.get(qualified_skill_id)
        if existing is not None:
            return existing
        trusted_metadata = metadata or self._skill_metadata(qualified_skill_id)
        if trusted_metadata.runtime_dependency_error:
            raise RuntimeDependencyCatalogError(
                "skill_runtime_dependencies_invalid",
                _bounded_text(
                    trusted_metadata.runtime_dependency_error,
                    MAX_DIAGNOSTIC_CHARS,
                ),
            )
        requirements = trusted_metadata.runtime_dependencies
        if requirements is None:
            # Skills without the optional declaration retain legacy behavior.
            return None
        binding = self.resolve(
            requirements,
            context_id=f"{self.run_id}:{qualified_skill_id}",
            qualified_skill_id=qualified_skill_id,
        )
        self._skill_bindings[qualified_skill_id] = binding
        return binding

    def verify_binding(
        self,
        binding: RuntimeDependencyBinding | str,
        *,
        context_id: str | None = None,
        requirements: RuntimeRequirements | None = None,
        qualified_skill_id: str | None = None,
    ) -> RuntimeDependencyBinding:
        """Revalidate a selected binding immediately before process launch."""

        if self._ensure_catalog() is None or self._snapshot is None:
            error_code = (
                self.catalog_error.code
                if self.catalog_error is not None
                else "catalog_unavailable"
            )
            raise RuntimeDependencyUnavailableError(
                error_code,
                "a verified runtime catalog is not available",
            )
        selected = self._lookup_binding(
            binding,
            requirements=requirements,
            qualified_skill_id=qualified_skill_id,
        )
        self._admit_binding(selected)
        if context_id is not None and selected.context_id != context_id:
            raise RuntimeDependencyVerificationError(
                "binding_context_mismatch",
                "dependency binding context does not match the requested context",
            )
        self._catalog.verify_binding(selected)
        return selected

    def resolve_shell_binding(
        self,
        binding_id: str,
        *,
        active_skill_ids: Iterable[str] = (),
        implicit_skill_id: str | None = None,
    ) -> tuple[RuntimeDependencyBinding, str | None]:
        """Select a verified binding for one Shell invocation.

        A declared Skill binding is scoped to that active Skill.  The default
        binding is available only when no active Skill declares dependencies.
        """

        _validate_identifier(binding_id, "binding_id", MAX_CONTEXT_ID_CHARS)
        if implicit_skill_id is not None:
            _validate_identifier(
                implicit_skill_id,
                "qualified_skill_id",
                MAX_SKILL_ID_CHARS,
            )
            binding = self.binding_for_skill(implicit_skill_id)
            if binding is None or binding.binding_id != binding_id:
                raise RuntimeDependencyVerificationError(
                    "binding_skill_mismatch",
                    "dependency binding is not declared by the invoked Skill",
                )
            return (
                self.verify_binding(
                    binding,
                    context_id=f"{self.run_id}:{implicit_skill_id}",
                    qualified_skill_id=implicit_skill_id,
                ),
                implicit_skill_id,
            )

        matched: list[tuple[RuntimeDependencyBinding, str]] = []
        declared_count = 0
        for qualified_id in active_skill_ids:
            _validate_identifier(
                qualified_id,
                "qualified_skill_id",
                MAX_SKILL_ID_CHARS,
            )
            skill_binding = self.binding_for_skill(qualified_id)
            if skill_binding is None:
                continue
            declared_count += 1
            if skill_binding.binding_id == binding_id:
                matched.append((skill_binding, qualified_id))
        if len(matched) > 1:
            raise RuntimeDependencyVerificationError(
                "binding_conflict",
                "dependency binding matches multiple active Skills",
            )
        if matched:
            binding, qualified_id = matched[0]
            return (
                self.verify_binding(
                    binding,
                    context_id=f"{self.run_id}:{qualified_id}",
                    qualified_skill_id=qualified_id,
                ),
                qualified_id,
            )
        if declared_count:
            raise RuntimeDependencyVerificationError(
                "binding_skill_mismatch",
                "dependency binding is not declared by an active Skill",
            )
        default = self.default_binding()
        if default is None or default.binding_id != binding_id:
            raise RuntimeDependencyVerificationError(
                "binding_not_found",
                "dependency binding is not known for this Run",
            )
        return (
            self.verify_binding(
                default,
                context_id=f"{self.run_id}:default",
            ),
            None,
        )

    def shell_environment(
        self,
        binding: RuntimeDependencyBinding,
    ) -> DependencyShellEnvironment:
        """Project a verified binding into the low-level Shell environment port."""

        self._admit_binding(binding)
        selected = binding
        python_executable = _executable_path(
            selected,
            {"python", "python3"},
        )
        node_executable = _executable_path(selected, {"node", "nodejs"})
        return DependencyShellEnvironment(
            binding_id=selected.binding_id,
            python_executable=python_executable,
            python_path=selected.python_path,
            node_executable=node_executable,
            node_modules=selected.node_modules,
            node_loader=selected.node_loader,
            bin_paths=selected.native_bin_paths,
        )

    def permission_profile(
        self,
        base_permissions: BasePermissionProfile,
        binding: RuntimeDependencyBinding,
    ) -> BasePermissionProfile:
        """Protect every verified runtime root from writes."""

        self._admit_binding(binding)
        selected = binding
        roots = _dedupe_paths(
            (
                self._snapshot.bundle_root,
                *self._snapshot.runtime_roots,
                *selected.runtime_roots,
            )
        )
        protected = _dedupe_paths(
            (
                self._snapshot.bundle_root,
                *self._snapshot.protected_write_paths,
                *selected.protected_write_paths,
            )
        )
        return base_permissions.model_copy(update={
            "runtime_roots": _dedupe_paths(
                (*base_permissions.runtime_roots, *roots)
            ),
            "protected_write_paths": _dedupe_paths(
                (*base_permissions.protected_write_paths, *protected)
            ),
        })

    def binding_provenance(
        self,
        binding: RuntimeDependencyBinding,
        *,
        qualified_skill_id: str | None = None,
    ) -> RuntimeDependencyBindingProvenance:
        """Return bounded binding and source hashes for a Shell result."""

        if self._snapshot is None:
            raise RuntimeDependencyUnavailableError(
                "catalog_unavailable",
                "a verified runtime catalog is not available",
            )
        self._admit_binding(binding)
        return RuntimeDependencyBindingProvenance(
            bindingId=binding.binding_id,
            manifestSha256=self._snapshot.manifest_sha256,
            requirementsSha256=binding.requirement_sha256,
            snapshotSha256=binding.snapshot_sha256,
            source=self._snapshot.bundle_id,
            skillQualifiedId=qualified_skill_id,
        )

    def workspace_dependencies_metadata(
        self,
        active_skill_ids: Iterable[str] = (),
        *,
        include_default: bool = True,
    ) -> dict[str, object]:
        """Return binding metadata without returning the full files inventory."""

        default = self.default_binding() if include_default else None
        active: list[dict[str, object]] = []
        for qualified_id in active_skill_ids:
            try:
                binding = self.binding_for_skill(qualified_id)
            except RuntimeDependencyCatalogError as error:
                active.append({
                    "skillQualifiedId": qualified_id,
                    "bindingId": "invalid",
                    "status": "invalid",
                    "code": error.code,
                })
                continue
            if binding is None:
                continue
            active.append({
                "skillQualifiedId": qualified_id,
                "bindingId": binding.binding_id,
                "status": (
                    "ready" if binding.ready else _binding_status(binding)
                ),
                "manifestSha256": self._snapshot.manifest_sha256,
                "requirementsSha256": binding.requirement_sha256,
                "diagnostics": [
                    diagnostic.model_dump(mode="json", by_alias=True)
                    for diagnostic in binding.diagnostics[:MAX_DIAGNOSTICS]
                ],
            })
            if len(active) >= MAX_DIAGNOSTICS:
                break
        metadata: dict[str, object] = {
            "defaultDependencyBindingId": (
                default.binding_id if default is not None else None
            ),
            "activeSkillDependencyBindings": active,
            "manifestSha256": (
                self._snapshot.manifest_sha256 if self._snapshot is not None else None
            ),
            "snapshotSha256": (
                self._snapshot.snapshot_sha256 if self._snapshot is not None else None
            ),
        }
        if self.catalog_error is not None:
            metadata["runtimeDependencyError"] = self.catalog_error.code
        return metadata

    def skill_dependency_warning(
        self,
        active_skill_ids: Iterable[str],
    ):
        """Render bounded diagnostics for unresolved Skill requirements."""

        from eidos_runtime.extensions.skills import RetainedContextSection

        metadata = self.workspace_dependencies_metadata(
            active_skill_ids,
            include_default=False,
        )
        entries = [
            entry
            for entry in metadata["activeSkillDependencyBindings"]
            if entry["status"] != "ready"
        ]
        if self.catalog_error is not None:
            entries.insert(0, {
                "status": "invalid",
                "code": self.catalog_error.code,
            })
        if not entries:
            return None
        encoded = json.dumps(
            entries[:MAX_DIAGNOSTICS],
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return RetainedContextSection(
            section_id="skill-runtime-dependency-warning",
            version=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
            role="user",
            source="runtime-dependencies",
            content=(
                "Selected Skill Runtime dependency status: "
                f"{encoded}. Continue with another tool or tell the user which "
                "required dependency is unavailable."
            ),
        )

    def list_bindings(self) -> tuple[RuntimeDependencyBindingRecord, ...]:
        """Return durable records for this Run after checking their ownership."""

        records = self.store.list_runtime_dependency_bindings(self.run_id) if self.store else ()
        for record in records:
            if record.run_id != self.run_id:
                raise RuntimeDependencyVerificationError(
                    "binding_run_mismatch",
                    "persisted dependency binding belongs to another Run",
                )
        return tuple(records)

    def _skill_metadata(self, qualified_skill_id: str) -> SkillAgentMetadata:
        if self.skills is None or self.skill_snapshot is None:
            raise RuntimeDependencyCatalogError(
                "skill_metadata_unavailable",
                "trusted Skill metadata is not attached to this Run",
            )
        return self.skills.metadata(self.skill_snapshot, qualified_skill_id)

    def _ensure_catalog(self) -> RuntimeDependencyCatalog | None:
        """Discover and validate the application catalog only when required."""

        if self._catalog is not None:
            return self._catalog
        if self._catalog_discovery_attempted:
            return None
        self._catalog_discovery_attempted = True
        try:
            manifest = self._manifest_path or _discover_manifest_path()
            if manifest is None:
                return None
            catalog = RuntimeDependencyCatalog.from_manifest(manifest)
            snapshot = catalog.snapshot()
            if not isinstance(snapshot, RuntimeDependencySnapshot):
                raise RuntimeDependencyCatalogError(
                    "snapshot_invalid",
                    "Runtime dependency catalog returned an invalid snapshot",
                )
            self._validate_snapshot(snapshot)
        except RuntimeDependencyCatalogError as error:
            self.catalog_error = error
            self._catalog = None
            self._snapshot = None
            return None
        self._catalog = catalog
        self._snapshot = snapshot
        return catalog

    def _lookup_binding(
        self,
        binding: RuntimeDependencyBinding | str,
        *,
        requirements: RuntimeRequirements | None,
        qualified_skill_id: str | None,
    ) -> RuntimeDependencyBinding:
        if isinstance(binding, RuntimeDependencyBinding):
            known = self._bindings.get(binding.binding_id)
            if known is not None and known != binding:
                raise RuntimeDependencyVerificationError(
                    "binding_conflict",
                    "binding id is already associated with different data",
                )
            self._admit_binding(binding)
            self._bindings[binding.binding_id] = binding
            return binding
        _validate_identifier(binding, "binding_id", MAX_CONTEXT_ID_CHARS)
        selected = self._bindings.get(binding)
        if selected is not None:
            return selected
        if qualified_skill_id is not None:
            candidate = self.binding_for_skill(qualified_skill_id)
            if candidate is not None and candidate.binding_id == binding:
                return candidate
        if requirements is not None:
            candidate = self.resolve(
                requirements,
                context_id=f"{self.run_id}:binding",
                qualified_skill_id=qualified_skill_id,
            )
            if candidate.binding_id == binding:
                return candidate
        if self._default is not None and self._default.binding_id == binding:
            return self._default
        record = self._read_binding_record(binding)
        if record is not None:
            raise RuntimeDependencyVerificationError(
                "binding_recovery_requires_requirements",
                "persisted binding requires trusted requirements to be reconstructed",
            )
        raise RuntimeDependencyVerificationError(
            "binding_not_found",
            "dependency binding is not known for this Run",
        )

    def _admit_binding(self, binding: RuntimeDependencyBinding) -> None:
        _validate_run_context(self.run_id, binding.context_id)
        if self._snapshot is None:
            return
        if binding.snapshot_sha256 != self._snapshot.snapshot_sha256:
            raise RuntimeDependencyVerificationError(
                "binding_snapshot_mismatch",
                "dependency binding is from another catalog snapshot",
            )
        self._validate_paths(
            _binding_paths(binding),
            _canonical_path(self._snapshot.bundle_root),
        )

    def _validate_snapshot(self, snapshot: RuntimeDependencySnapshot) -> None:
        bundle_root = _canonical_path(snapshot.bundle_root)
        for forbidden in self._forbidden_roots:
            if _contained(bundle_root, forbidden):
                raise RuntimeDependencyPathError(
                    "runtime_root_forbidden",
                    "runtime bundle is inside Eidos data storage",
                )
        self._validate_paths(_snapshot_paths(snapshot), bundle_root)

    def _validate_paths(self, paths: Iterable[str], bundle_root: Path) -> None:
        for value in paths:
            path = _canonical_path(value)
            if not _contained(path, bundle_root):
                raise RuntimeDependencyPathError(
                    "runtime_path_outside_bundle",
                    "resolved dependency path is outside the runtime bundle",
                )
            if any(_contained(path, forbidden) for forbidden in self._forbidden_roots):
                raise RuntimeDependencyPathError(
                    "runtime_path_forbidden",
                    "resolved dependency path is inside Eidos data storage",
                )

    def _persist_binding(
        self,
        binding: RuntimeDependencyBinding,
        *,
        qualified_skill_id: str | None,
    ) -> None:
        self._ensure_snapshot_record()
        if self.store is None:
            return
        diagnostics_json = json.dumps(
            [
                diagnostic.model_dump(mode="json", by_alias=True)
                for diagnostic in binding.diagnostics[:MAX_DIAGNOSTICS]
            ],
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        record = RuntimeDependencyBindingRecord(
            run_id=self.run_id,
            binding_id=binding.binding_id,
            manifest_hash=self._snapshot.manifest_sha256,
            requirements_hash=binding.requirement_sha256,
            qualified_skill_id=qualified_skill_id,
            status="ready" if binding.ready else _binding_status(binding),
            diagnostics_json=diagnostics_json,
            created_at=int(time.time() * 1000),
        )
        existing = self._read_binding_record(binding.binding_id)
        if existing is not None:
            if existing.run_id != self.run_id:
                raise RuntimeDependencyVerificationError(
                    "binding_run_mismatch",
                    "persisted dependency binding belongs to another Run",
                )
            record = record.model_copy(update={"created_at": existing.created_at})
            if existing != record:
                raise RuntimeDependencyVerificationError(
                    "binding_record_conflict",
                    "persisted dependency binding differs from the frozen binding",
                )
            return
        mutation = self.store.persist_runtime_dependency_binding(record)
        if self.events is not None:
            self.events.publish(mutation)

    def _ensure_snapshot_record(self) -> None:
        if self._snapshot_recorded or self.store is None or self._snapshot is None:
            return
        snapshot_json = json.dumps(
            self._snapshot.model_dump(
                mode="json",
                by_alias=True,
                exclude={"files"},
                exclude_none=True,
            ),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
        if len(snapshot_json.encode("utf-8")) > MAX_SNAPSHOT_RECORD_BYTES:
            raise RuntimeDependencyCatalogError(
                "snapshot_record_too_large",
                "pinned Runtime snapshot exceeds the bounded record size",
            )
        record = RuntimeDependencySnapshotRecord(
            run_id=self.run_id,
            manifest_hash=self._snapshot.manifest_sha256,
            catalog_hash=self._snapshot.snapshot_sha256,
            snapshot_json=snapshot_json,
            created_at=int(time.time() * 1000),
        )
        existing = self.store.read_runtime_dependency_snapshot(self.run_id)
        if existing is not None:
            if existing.run_id != self.run_id:
                raise RuntimeDependencyVerificationError(
                    "snapshot_run_mismatch",
                    "persisted Runtime snapshot belongs to another Run",
                )
            if (
                existing.manifest_hash != record.manifest_hash
                or existing.catalog_hash != record.catalog_hash
                or existing.snapshot_json != record.snapshot_json
            ):
                raise RuntimeDependencyVerificationError(
                    "snapshot_record_conflict",
                    "persisted Runtime snapshot differs from the frozen snapshot",
                )
            record = record.model_copy(update={"created_at": existing.created_at})
            self._snapshot_recorded = True
            return
        mutation = self.store.persist_runtime_dependency_snapshot(record)
        if self.events is not None:
            self.events.publish(mutation)
        self._snapshot_recorded = True

    def _read_binding_record(
        self,
        binding_id: str,
    ) -> RuntimeDependencyBindingRecord | None:
        if self.store is None:
            return None
        record = self.store.read_runtime_dependency_binding(binding_id)
        if record is not None and record.run_id != self.run_id:
            raise RuntimeDependencyVerificationError(
                "binding_run_mismatch",
                "persisted dependency binding belongs to another Run",
            )
        return record


def discover_runtime_dependency_catalog(
    *,
    manifest_path: Path | None = None,
) -> RuntimeDependencyCatalog | None:
    path = manifest_path or _discover_manifest_path()
    if path is None:
        return None
    return RuntimeDependencyCatalog.from_manifest(path)


def _discover_manifest_path() -> Path | None:
    package_root = Path(__file__).resolve().parents[2]
    packaged = package_root.name == "app" and not (
        package_root.parent / "pyproject.toml"
    ).is_file()
    if packaged:
        candidate = package_root.parent / "runtime.json"
        if candidate.exists():
            return candidate
        raise RuntimeDependencyCatalogError(
            "manifest_missing",
            "packaged Runtime is missing runtime.json",
        )
    source_manifest = package_root.parent / "build" / "macos-runtime" / "runtime.json"
    if source_manifest.exists():
        return source_manifest
    return None


def _snapshot_paths(snapshot: RuntimeDependencySnapshot) -> tuple[str, ...]:
    paths = [
        snapshot.bundle_root,
        snapshot.manifest_path,
        *snapshot.runtime_roots,
        *snapshot.protected_write_paths,
        *snapshot.python_path,
        *snapshot.native_bin_paths,
    ]
    if snapshot.node_modules is not None:
        paths.append(snapshot.node_modules)
    if snapshot.node_loader is not None:
        paths.append(snapshot.node_loader)
    paths.extend(value.path for value in snapshot.executables)
    paths.extend(value.path for value in snapshot.python_package_roots)
    paths.extend(value.path for value in snapshot.node_package_roots)
    paths.extend(value.path for value in snapshot.files)
    return tuple(paths)


def _binding_paths(binding: RuntimeDependencyBinding) -> tuple[str, ...]:
    paths = [
        *binding.runtime_roots,
        *binding.protected_write_paths,
        *binding.python_path,
        *binding.native_bin_paths,
    ]
    if binding.node_modules is not None:
        paths.append(binding.node_modules)
    if binding.node_loader is not None:
        paths.append(binding.node_loader)
    paths.extend(value.path for value in binding.executables)
    paths.extend(value.path for value in binding.python_package_roots)
    paths.extend(value.path for value in binding.node_package_roots)
    paths.extend(value.path for value in binding.files)
    return tuple(paths)


def _canonical_path(value: Path | str) -> Path:
    path = Path(value)
    if not path.is_absolute() or "\x00" in str(path):
        raise RuntimeDependencyPathError(
            "runtime_path_invalid",
            "runtime paths must be absolute",
        )
    return path.resolve(strict=False)


def _contained(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _validate_identifier(value: str, name: str, limit: int) -> None:
    if not value or len(value) > limit:
        raise RuntimeDependencyVerificationError(
            "identifier_invalid",
            f"{name} is not bounded",
        )
    if any(ord(character) < 0x20 or character in "/\\" for character in value):
        raise RuntimeDependencyVerificationError(
            "identifier_invalid",
            f"{name} contains unsafe characters",
        )


def _validate_run_context(run_id: str, context_id: str) -> None:
    _validate_identifier(context_id, "context_id", MAX_CONTEXT_ID_CHARS)
    if not context_id.startswith(f"{run_id}:"):
        raise RuntimeDependencyVerificationError(
            "binding_context_mismatch",
            "dependency binding belongs to another Run",
        )


def _dedupe_paths(values: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        canonical = str(_canonical_path(value))
        if canonical in seen:
            continue
        seen.add(canonical)
        result.append(canonical)
    return tuple(result)


def _executable_path(
    binding: RuntimeDependencyBinding,
    names: set[str],
) -> str | None:
    for executable in binding.executables:
        if executable.name in names:
            return executable.path
    return None


def _binding_status(binding: RuntimeDependencyBinding) -> str:
    if any(value.status == "incompatible" for value in binding.diagnostics):
        return "incompatible"
    return "missing"


def _bounded_text(value: str, limit: int) -> str:
    return value if len(value) <= limit else value[:limit]


__all__ = [
    "RuntimeDependencyCatalog",
    "RuntimeDependencyCatalogError",
    "RuntimeDependencyCoordinator",
    "RuntimeDependencyPathError",
    "RuntimeDependencyUnavailableError",
    "RuntimeDependencyVerificationError",
    "discover_runtime_dependency_catalog",
]

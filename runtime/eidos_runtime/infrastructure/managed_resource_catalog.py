"""Compose an App catalog with fixed declarations for downloadable resources."""
from __future__ import annotations

import hashlib
from pathlib import Path

from eidos_runtime.infrastructure.managed_resources import ManagedResourceManager, ResourceError
from eidos_runtime.infrastructure.runtime_dependencies import (
    RuntimeDependencyCatalog, RuntimeDependencyVerificationError,
    _snapshot_hash,
)
from eidos_runtime.models.runtime_dependencies import (
    RuntimeDependencyBinding, RuntimeDependencyExecutablePath,
    RuntimeDependencyPackageRoot, RuntimeDependencySnapshot,
    RuntimeDependencyVerifiedFile,
)
from eidos_runtime.models.skill_runtime import RuntimeRequirements


class ManagedResourceCatalog(RuntimeDependencyCatalog):
    """Freeze release declarations, while preparing files only before use.

    Presence is not part of the Run snapshot. A binding is created only after
    its selected packs are ready, and includes exactly those versioned roots.
    The legacy bundled catalog remains unchanged for older packages/tests.
    """

    def __init__(self, base: RuntimeDependencyCatalog, resources: ManagedResourceManager) -> None:
        self.base = base
        self.resources = resources
        self._base_snapshot = base.snapshot()
        index_bytes = resources.index.model_dump_json(by_alias=True).encode()
        self._release_hash = hashlib.sha256(
            self._base_snapshot.manifest_sha256.encode() + index_bytes
        ).hexdigest()
        planned = self._projection(tuple(pack.id for pack in resources.index.packs))
        self._snapshot = planned.model_copy(update={"snapshot_sha256": _snapshot_hash(planned)})

    @property
    def trusted_roots(self) -> tuple[Path, ...]:
        return (Path(self._base_snapshot.bundle_root), self.resources.root)

    def snapshot(self) -> RuntimeDependencySnapshot:
        return self._snapshot

    def _projection(self, pack_ids: tuple[str, ...]) -> RuntimeDependencySnapshot:
        base = self._base_snapshot
        executables = list(base.executables)
        python_packages = list(base.python_package_roots)
        node_packages = list(base.node_package_roots)
        python_paths: list[str] = []
        node_modules = base.node_modules
        roots = list(base.runtime_roots)
        files = list(base.files)
        for pack_id in self.resources.closure(pack_ids):
            pack = self.resources.pack(pack_id)
            root = self.resources.location(pack_id)
            manifest = pack.manifest
            roots.append(str(root))
            files.extend(RuntimeDependencyVerifiedFile(path=str(root / f.path), sha256=f.sha256) for f in manifest.files)
            executables.extend(RuntimeDependencyExecutablePath(
                name=item.name, version=item.version, sha256=item.sha256, path=str(root / item.path),
            ) for item in manifest.executables)
            python_paths.extend(str(root / p) for p in manifest.python_path)
            for item in manifest.python_packages:
                import_path = f"{manifest.python_path[0]}/{item.import_name.split('.')[0]}"
                if any(file.path == import_path + ".py" for file in manifest.files):
                    import_path += ".py"
                python_packages.append(RuntimeDependencyPackageRoot(
                    type="python-package", name=item.name, import_name=item.import_name,
                    version=item.version, path=str(root / import_path),
                ))
            if manifest.node_modules is not None:
                modules = root / manifest.node_modules
                node_modules = node_modules or str(modules)
                node_packages.extend(RuntimeDependencyPackageRoot(
                    type="node-package", name=item.name, version=item.version, path=str(modules / item.name),
                ) for item in manifest.node_packages)
        return base.model_copy(update={
            "manifest_sha256": self._release_hash,
            "snapshot_sha256": "0" * 64,
            "executables": tuple(executables),
            "python_path": tuple(dict.fromkeys((*python_paths, *base.python_path))),
            "python_package_roots": tuple(python_packages),
            "node_modules": node_modules,
            "node_package_roots": tuple(node_packages),
            "node_loader": base.node_loader if node_modules else None,
            "files": tuple(files),
            "runtime_roots": tuple(roots),
            "protected_write_paths": (*base.protected_write_paths, str(self.resources.root)),
        })

    def resolve(self, requirements: RuntimeRequirements | None, *, context_id: str = "") -> RuntimeDependencyBinding:
        return self.resolve_profile(requirements, context_id=context_id, pack_ids=())

    def resolve_profile(
        self, requirements: RuntimeRequirements | None, *, context_id: str, pack_ids: tuple[str, ...],
    ) -> RuntimeDependencyBinding:
        selected = self.resources.closure(pack_ids)
        for pack_id in selected:
            self.resources.verified_catalog(pack_id)
        snapshot = self._projection(selected).model_copy(update={"snapshot_sha256": self._snapshot.snapshot_sha256})
        return self._resolve_with_snapshot(requirements, context_id, snapshot)

    def pack_ids_for_binding(self, binding: RuntimeDependencyBinding) -> tuple[str, ...]:
        return tuple(pack.id for pack in self.resources.index.packs
                     if str(self.resources.location(pack.id)) in binding.runtime_roots)

    def verify_binding(self, binding: RuntimeDependencyBinding) -> None:
        self.base.verify_binding(self.base.resolve(None))
        if binding.snapshot_sha256 != self._snapshot.snapshot_sha256 or not binding.ready:
            raise RuntimeDependencyVerificationError("binding_snapshot_mismatch", "managed binding is not ready for this release")
        current = self.resolve_profile(
            binding.requirements, context_id=binding.context_id, pack_ids=self.pack_ids_for_binding(binding),
        )
        if current != binding:
            raise RuntimeDependencyVerificationError("binding_changed", "managed resource binding changed before launch")

    def document_fonts(self, binding: RuntimeDependencyBinding) -> str | None:
        for pack_id in self.pack_ids_for_binding(binding):
            pack = self.resources.pack(pack_id)
            if pack.font_directory:
                fonts = self.resources.location(pack_id) / pack.font_directory
                if not fonts.is_dir() or fonts.is_symlink():
                    raise ResourceError("resource_not_ready", "document fonts are unavailable")
                return str(fonts)
        return None

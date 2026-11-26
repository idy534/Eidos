from __future__ import annotations

import hashlib
import io
import os
import tarfile

import httpx2 as httpx
import pytest

from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.application.managed_resources import ManagedResourceApplication
from eidos_runtime.infrastructure.managed_resources import ManagedResourceManager, ResourceError
from eidos_runtime.models.managed_resources import ResourceCleanupResponse, ResourceIndex, ResourcePack
from eidos_runtime.models.runtime_dependencies import (
    RuntimeDependencyInventoryFile,
    RuntimeDependencyManifest,
    RuntimeDependencyPythonPackage,
)


def _archive(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as tar:
        for name, data in files.items():
            entry = tarfile.TarInfo(name)
            entry.size = len(data)
            entry.mode = 0o644
            tar.addfile(entry, io.BytesIO(data))
    return output.getvalue()


def _create_manager(tmp_path):
    files = {
        "lib/example/__init__.py": b"VALUE = 'active'\n",
    }
    payload = _archive(files)
    manifest = RuntimeDependencyManifest(
        schema_version=1,
        bundle_id="com.idy.eidos.fixture",
        bundle_version="1.0.0",
        target="darwin-arm64",
        files=tuple(
            RuntimeDependencyInventoryFile(path=path, sha256=hashlib.sha256(data).hexdigest())
            for path, data in files.items()
        ),
        python_path=("lib",),
        python_packages=(RuntimeDependencyPythonPackage(name="example", import_name="example", version="1.0.0"),),
    )
    pack = ResourcePack(
        id="document-python",
        title="Test documents",
        version="1.0.0",
        location="packages/python/documents/1.0.0/cp312-darwin-arm64",
        url="https://github.com/idy534/Eidos/releases/download/test/documents.tar.gz",
        archive_sha256=hashlib.sha256(payload).hexdigest(),
        archive_bytes=len(payload),
        expanded_bytes=sum(map(len, files.values())),
        manifest=manifest,
    )
    root = tmp_path.resolve() / "Resource"
    manager = ManagedResourceManager(
        ResourceIndex(version="1.0.0", packs=(pack,)),
        root=root,
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(200, content=payload))),
    )
    return manager, pack, files


def test_cleanup_legacy_removes_stale_versions_staging_and_orphans_without_touching_active(tmp_path) -> None:
    manager, pack, files = _create_manager(tmp_path)
    active_dir = manager.location(pack.id)
    for rel, content in files.items():
        target = active_dir / rel
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target.write_bytes(content)
    (active_dir / "runtime.json").write_bytes(pack.manifest.model_dump_json(by_alias=True).encode("utf-8"))

    stale_version = manager.root / "packages" / "python" / "documents" / "0.9.0" / "cp312-darwin-arm64" / "stale.bin"
    stale_version.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    stale_bytes = b"old-release-payload"
    stale_version.write_bytes(stale_bytes)

    staging_file = manager.root / ".staging" / "document-python-abc" / "partial.tar.gz"
    staging_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    staging_bytes = b"partial-archive"
    staging_file.write_bytes(staging_bytes)

    orphan_file = manager.root / "legacy-root" / "notes.txt"
    orphan_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    orphan_bytes = b"legacy"
    orphan_file.write_bytes(orphan_bytes)

    removed_count, freed_bytes = manager.cleanup_legacy()
    assert removed_count == 3
    assert freed_bytes == len(stale_bytes) + len(staging_bytes) + len(orphan_bytes)
    assert (active_dir / "lib" / "example" / "__init__.py").is_file()
    assert (active_dir / "runtime.json").is_file()
    assert not stale_version.parent.parent.exists()
    assert not (manager.root / ".staging").exists()
    assert not (manager.root / "legacy-root").exists()

    second_removed, second_freed = manager.cleanup_legacy()
    assert (second_removed, second_freed) == (0, 0)


def test_cleanup_legacy_refuses_when_installation_is_active_and_application_handles_missing_manager(tmp_path) -> None:
    app = ManagedResourceApplication(lambda _name, _fn: True)
    assert app.cleanup() == ResourceCleanupResponse(removed_count=0, freed_bytes=0)

    manager, _pack, _files = _create_manager(tmp_path)
    app.manager = manager
    manager._busy.add("document-python")
    try:
        with pytest.raises(ResourceError, match="cannot clean up resources"):
            manager.cleanup_legacy()
        with pytest.raises(ApplicationError) as exc:
            app.cleanup()
        assert exc.value.code == "OPERATION_IN_PROGRESS"
    finally:
        manager._busy.discard("document-python")
        # Ensure Windows test tmp cleanup succeeds cleanly.
        assert os.path.exists(tmp_path)

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from eidos_runtime.infrastructure.runtime_dependencies import (
    RuntimeDependencyCatalog,
    RuntimeDependencyCatalogError,
    RuntimeDependencyVerificationError,
)
from eidos_runtime.models.skill_runtime import (
    ExecutableRequirement,
    NodePackageRequirement,
    PythonPackageRequirement,
    RuntimeRequirements,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_file(
    root: Path,
    relative_path: str,
    contents: bytes,
    *,
    executable: bool = False,
) -> Path:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(contents)
    if executable:
        path.chmod(0o755)
    return path


def _manifest_payload(root: Path, *, files: tuple[str, ...] | None = None) -> dict[str, object]:
    inventory_paths = files or (
        "bin/python3",
        "bin/node",
        "dependencies/node/runtime-loader.mjs",
        "dependencies/python/docx/__init__.py",
        "dependencies/python/python_docx-1.2.0.dist-info/METADATA",
        "dependencies/node/node_modules/tool/package.json",
    )
    return {
        "schemaVersion": 1,
        "bundleId": "eidos-runtime",
        "bundleVersion": "1.0.0",
        "target": "darwin-arm64",
        "executables": [
            {
                "name": "python3",
                "path": "bin/python3",
                "version": "3.12.13",
                "sha256": _sha256(root / "bin/python3"),
            },
            {
                "name": "node",
                "path": "bin/node",
                "version": "22.12.0",
                "sha256": _sha256(root / "bin/node"),
            },
        ],
        "pythonPath": ["dependencies/python"],
        "pythonPackages": [
            {
                "name": "python-docx",
                "importName": "docx",
                "version": "1.2.0",
            }
        ],
        "nodeModules": "dependencies/node/node_modules",
        "nodeLoader": "dependencies/node/runtime-loader.mjs",
        "nodePackages": [{"name": "tool", "version": "1.4.2"}],
        "nativeBinPaths": ["bin"],
        "files": [
            {"path": path, "sha256": _sha256(root / path)}
            for path in inventory_paths
        ],
    }


def _make_bundle(tmp_path: Path) -> Path:
    root = tmp_path / "runtime-bundle"
    root.mkdir()
    _write_file(root, "bin/python3", b"#!/bin/sh\n", executable=True)
    _write_file(root, "bin/node", b"#!/bin/sh\n", executable=True)
    _write_file(root, "dependencies/node/runtime-loader.mjs", b"export {}\n")
    _write_file(
        root,
        "dependencies/python/docx/__init__.py",
        b"__version__ = '1.2.0'\n",
    )
    _write_file(
        root,
        "dependencies/python/python_docx-1.2.0.dist-info/METADATA",
        b"Metadata-Version: 2.1\nName: python-docx\nVersion: 1.2.0\n",
    )
    _write_file(
        root,
        "dependencies/node/node_modules/tool/package.json",
        b'{"name":"tool","version":"1.4.2"}\n',
    )
    return root


def _write_manifest(root: Path, payload: dict[str, object]) -> Path:
    manifest = root / "runtime-dependencies.json"
    manifest.write_text(
        json.dumps(payload, separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )
    return manifest


def test_from_manifest_returns_verified_snapshot_with_loader_and_roots(
    tmp_path: Path,
) -> None:
    root = _make_bundle(tmp_path)
    manifest = _write_manifest(root, _manifest_payload(root))

    catalog = RuntimeDependencyCatalog.from_manifest(manifest)
    snapshot = catalog.snapshot()

    assert snapshot.manifest_path == str(manifest.resolve())
    assert snapshot.bundle_root == str(root.resolve())
    assert snapshot.bundle_id == "eidos-runtime"
    assert snapshot.bundle_version == "1.0.0"
    assert snapshot.target == "darwin-arm64"
    assert snapshot.node_loader == str((root / "dependencies/node/runtime-loader.mjs").resolve())
    assert snapshot.node_modules == str((root / "dependencies/node/node_modules").resolve())
    assert snapshot.python_path == (str((root / "dependencies/python").resolve()),)
    assert snapshot.runtime_roots == (str(root.resolve()),)
    assert snapshot.protected_write_paths == (str(root.resolve()),)
    assert snapshot.executables[0].path == str((root / "bin/python3").resolve())
    assert snapshot.python_package_roots[0].path == str(
        (root / "dependencies/python/docx").resolve()
    )
    assert snapshot.node_package_roots[0].path == str(
        (root / "dependencies/node/node_modules/tool").resolve()
    )
    assert snapshot.files[-1].path
    assert len(snapshot.manifest_sha256) == 64
    assert len(snapshot.snapshot_sha256) == 64


def test_manifest_rejects_path_traversal_and_missing_loader_inventory(tmp_path: Path) -> None:
    root = _make_bundle(tmp_path)
    payload = _manifest_payload(root)
    payload["pythonPath"] = ["../outside"]
    with pytest.raises(RuntimeDependencyCatalogError, match="path"):
        RuntimeDependencyCatalog.from_manifest(_write_manifest(root, payload))

    payload = _manifest_payload(root, files=(
        "bin/python3",
        "bin/node",
        "dependencies/python/docx/__init__.py",
        "dependencies/python/python_docx-1.2.0.dist-info/METADATA",
        "dependencies/node/node_modules/tool/package.json",
    ))
    with pytest.raises(RuntimeDependencyCatalogError, match="loader|inventory"):
        RuntimeDependencyCatalog.from_manifest(_write_manifest(root, payload))

    for field, value in (
        ("pythonPath", ["."]),
        ("nodeModules", "dependencies/node/node_modules/."),
        ("nodeLoader", "dependencies/node/runtime-loader.mjs/.."),
        ("nativeBinPaths", ["bin/."]),
    ):
        payload = _manifest_payload(root)
        payload[field] = value
        with pytest.raises(RuntimeDependencyCatalogError, match="path"):
            RuntimeDependencyCatalog.from_manifest(_write_manifest(root, payload))


def test_manifest_rejects_tampered_hash_and_escaping_symlink(tmp_path: Path) -> None:
    root = _make_bundle(tmp_path)
    payload = _manifest_payload(root)
    inventory = payload["files"]
    assert isinstance(inventory, list)
    payload["files"] = [
        {
            "path": "bin/python3",
            "sha256": "0" * 64,
        },
        *inventory[1:],
    ]
    with pytest.raises(RuntimeDependencyCatalogError, match="hash"):
        RuntimeDependencyCatalog.from_manifest(_write_manifest(root, payload))

    outside = tmp_path / "outside.py"
    outside.write_text("print('outside')\n", encoding="utf-8")
    (root / "dependencies/python/docx/escape.py").symlink_to(outside)
    payload = _manifest_payload(root, files=(
        "bin/python3",
        "bin/node",
        "dependencies/node/runtime-loader.mjs",
        "dependencies/python/docx/__init__.py",
        "dependencies/python/docx/escape.py",
        "dependencies/python/python_docx-1.2.0.dist-info/METADATA",
        "dependencies/node/node_modules/tool/package.json",
    ))
    with pytest.raises(RuntimeDependencyCatalogError, match="symlink|root"):
        RuntimeDependencyCatalog.from_manifest(_write_manifest(root, payload))


def test_manifest_rejects_unlisted_file_inside_declared_package_root(tmp_path: Path) -> None:
    root = _make_bundle(tmp_path)
    _write_file(
        root,
        "dependencies/python/docx/extra.py",
        b"print('extra')\n",
        executable=True,
    )
    with pytest.raises(RuntimeDependencyCatalogError, match="inventory|unlisted"):
        RuntimeDependencyCatalog.from_manifest(_write_manifest(root, _manifest_payload(root)))


def test_manifest_requires_python_metadata_and_node_package_versions_to_match(
    tmp_path: Path,
) -> None:
    root = _make_bundle(tmp_path)

    metadata = root / "dependencies/python/python_docx-1.2.0.dist-info/METADATA"
    metadata.write_text(
        "Metadata-Version: 2.1\nName: python-docx\nVersion: 9.9.9\n",
        encoding="utf-8",
    )
    payload = _manifest_payload(root)
    files = payload["files"]
    assert isinstance(files, list)
    for entry in files:
        assert isinstance(entry, dict)
        if entry["path"] == "dependencies/python/python_docx-1.2.0.dist-info/METADATA":
            entry["sha256"] = _sha256(metadata)
    with pytest.raises(RuntimeDependencyCatalogError, match="METADATA|version"):
        RuntimeDependencyCatalog.from_manifest(_write_manifest(root, payload))

    package_json = root / "dependencies/node/node_modules/tool/package.json"
    package_json.write_text('{"name":"tool","version":"9.9.9"}\n', encoding="utf-8")
    payload = _manifest_payload(root)
    files = payload["files"]
    assert isinstance(files, list)
    for entry in files:
        assert isinstance(entry, dict)
        if entry["path"] == "dependencies/node/node_modules/tool/package.json":
            entry["sha256"] = _sha256(package_json)
    with pytest.raises(RuntimeDependencyCatalogError, match="package.json|version"):
        RuntimeDependencyCatalog.from_manifest(_write_manifest(root, payload))


def test_manifest_requires_python_metadata_in_inventory(tmp_path: Path) -> None:
    root = _make_bundle(tmp_path)
    payload = _manifest_payload(
        root,
        files=(
            "bin/python3",
            "bin/node",
            "dependencies/node/runtime-loader.mjs",
            "dependencies/python/docx/__init__.py",
            "dependencies/node/node_modules/tool/package.json",
        ),
    )

    with pytest.raises(RuntimeDependencyCatalogError, match="inventory|unlisted|metadata"):
        RuntimeDependencyCatalog.from_manifest(_write_manifest(root, payload))


def test_resolve_is_deterministic_and_context_scoped(tmp_path: Path) -> None:
    root = _make_bundle(tmp_path)
    catalog = RuntimeDependencyCatalog.from_manifest(
        _write_manifest(root, _manifest_payload(root))
    )
    requirements = RuntimeRequirements(
        schemaVersion=1,
        dependencies=(
            PythonPackageRequirement(
                kind="python-package",
                name="python-docx",
                import_name="docx",
                version=">=1.2,<2",
                required=True,
            ),
            NodePackageRequirement(
                kind="node-package", name="tool", version="^1.0.0", required=True
            ),
            ExecutableRequirement(
                kind="executable", name="python3", version="==3.12.13", required=True
            ),
        )
    )

    first = catalog.resolve(requirements, context_id="run-1:skill-a")
    second = catalog.resolve(requirements, context_id="run-1:skill-a")
    other_context = catalog.resolve(requirements, context_id="run-2:skill-a")

    assert first.ready is True
    assert first == second
    assert first.binding_id == second.binding_id
    assert first.binding_id != other_context.binding_id
    assert first.requirements == requirements
    assert first.context_id == "run-1:skill-a"
    assert first.node_loader == catalog.snapshot().node_loader
    assert all(value.status == "ready" for value in first.diagnostics)
    assert first.requirement_sha256 == second.requirement_sha256


def test_resolve_reports_missing_and_incompatible_without_user_environment_probe(
    tmp_path: Path,
) -> None:
    root = _make_bundle(tmp_path)
    catalog = RuntimeDependencyCatalog.from_manifest(
        _write_manifest(root, _manifest_payload(root))
    )
    requirements = RuntimeRequirements(
        schemaVersion=1,
        dependencies=(
            NodePackageRequirement(
                kind="node-package", name="tool", version="^2.0.0", required=True
            ),
            PythonPackageRequirement(
                kind="python-package",
                name="missing-package",
                import_name="missing_package",
                version=">=1",
                required=True,
            ),
            ExecutableRequirement(
                kind="executable",
                name="missing-executable",
                version=">=1",
                required=True,
            ),
        )
    )

    binding = catalog.resolve(requirements)

    assert binding.ready is False
    assert [value.status for value in binding.diagnostics] == [
        "incompatible",
        "missing",
        "missing",
    ]
    assert [value.name for value in binding.diagnostics] == [
        "tool",
        "missing-package",
        "missing-executable",
    ]


def test_node_package_never_resolves_ready_without_node_and_loader(tmp_path: Path) -> None:
    root = _make_bundle(tmp_path)
    payload = _manifest_payload(root)
    executables = payload["executables"]
    assert isinstance(executables, list)
    payload["executables"] = executables[:1]
    payload["nodeLoader"] = None
    catalog = RuntimeDependencyCatalog.from_manifest(
        _write_manifest(root, payload)
    )
    requirements = RuntimeRequirements(
        schemaVersion=1,
        dependencies=(
            NodePackageRequirement(
                kind="node-package",
                name="tool",
                version="^1.0.0",
                required=True,
            ),
        ),
    )

    binding = catalog.resolve(requirements)

    assert binding.ready is False
    assert binding.diagnostics[0].status == "incompatible"
    assert binding.diagnostics[0].reason == "node_runtime_unavailable"


def test_empty_requirements_returns_base_binding_and_verify_rechecks_files(
    tmp_path: Path,
) -> None:
    root = _make_bundle(tmp_path)
    catalog = RuntimeDependencyCatalog.from_manifest(
        _write_manifest(root, _manifest_payload(root))
    )
    binding = catalog.resolve(None)

    assert binding.ready is True
    assert binding.diagnostics == ()
    assert binding.node_loader == catalog.snapshot().node_loader
    catalog.verify_binding(binding)

    loader = root / "dependencies/node/runtime-loader.mjs"
    loader.write_text("export const changed = true;\n", encoding="utf-8")
    with pytest.raises(RuntimeDependencyVerificationError, match="hash|changed"):
        catalog.verify_binding(binding)


def test_manifest_defaults_empty_collections(tmp_path: Path) -> None:
    root = tmp_path / "minimal"
    root.mkdir()
    manifest = _write_manifest(
        root,
        {
            "schemaVersion": 1,
            "bundleId": "minimal",
            "bundleVersion": "1.0.0",
            "target": "darwin-arm64",
        },
    )

    snapshot = RuntimeDependencyCatalog.from_manifest(manifest).snapshot()

    assert snapshot.executables == ()
    assert snapshot.python_path == ()
    assert snapshot.python_package_roots == ()
    assert snapshot.node_modules is None
    assert snapshot.node_loader is None
    assert snapshot.node_package_roots == ()
    assert snapshot.native_bin_paths == ()
    assert snapshot.files == ()


def test_manifest_rejects_untrusted_owner_or_non_regular_executable(tmp_path: Path) -> None:
    root = _make_bundle(tmp_path)
    payload = _manifest_payload(root)
    (root / "bin/python3").unlink()
    (root / "bin/python3").mkdir()
    with pytest.raises(RuntimeDependencyCatalogError, match="executable|regular"):
        RuntimeDependencyCatalog.from_manifest(_write_manifest(root, payload))

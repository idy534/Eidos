import hashlib
import io
import json
import tarfile
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx2 as httpx
import pytest

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.infrastructure.managed_resource_catalog import ManagedResourceCatalog
from eidos_runtime.infrastructure.managed_resources import ManagedResourceManager, ResourceError
from eidos_runtime.infrastructure.runtime_dependencies import RuntimeDependencyCatalog
from eidos_runtime.models.managed_resources import ResourceIndex, ResourcePack
from eidos_runtime.models.runtime_dependencies import (
    RuntimeDependencyExecutable, RuntimeDependencyInventoryFile,
    RuntimeDependencyManifest, RuntimeDependencyPythonPackage,
)
from eidos_runtime.models.skill_runtime import RuntimeRequirements
from eidos_runtime.runtime.runtime_dependencies import RuntimeDependencyCoordinator
from eidos_runtime.sandbox.dependency_environment import apply_dependency_environment

pytestmark = pytest.mark.integration


def manifest(files, *, python=False, executable=False):
    return RuntimeDependencyManifest(
        schema_version=1, bundle_id="com.idy.eidos.fixture", bundle_version="1.0.0", target="darwin-arm64",
        files=tuple(RuntimeDependencyInventoryFile(path=p, sha256=hashlib.sha256(data).hexdigest()) for p, data in files.items()),
        python_path=("lib",) if python else (),
        python_packages=(RuntimeDependencyPythonPackage(name="example", import_name="example", version="1.0.0"),) if python else (),
        executables=(RuntimeDependencyExecutable(name="python3", path="bin/python3", version="3.12.13",
                     sha256=hashlib.sha256(files["bin/python3"]).hexdigest()),) if executable else (),
    )


def archive(files):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as tar:
        for name, data in files.items():
            entry = tarfile.TarInfo(name)
            entry.size, entry.mode = len(data), 0o644
            tar.addfile(entry, io.BytesIO(data))
    return output.getvalue()


@pytest.fixture
def resources(tmp_path):
    files = {
        "lib/example/__init__.py": b"VALUE = 'managed'\n",
        "lib/example-1.0.0.dist-info/METADATA": b"Name: example\nVersion: 1.0.0\n",
    }
    payload = archive(files)
    pack = ResourcePack(
        id="document-python", title="Test documents", version="1.0.0", location="packages/python/documents/1.0.0/cp312-darwin-arm64",
        url="https://github.com/idy534/Eidos/releases/download/test/documents.tar.gz",
        archive_sha256=hashlib.sha256(payload).hexdigest(), archive_bytes=len(payload),
        expanded_bytes=sum(map(len, files.values())), manifest=manifest(files, python=True),
    )
    calls = []

    def transport(request):
        calls.append(str(request.url))
        return httpx.Response(200, content=payload)

    manager = ManagedResourceManager(
        ResourceIndex(version="1.0.0", packs=(pack,)), root=tmp_path.resolve() / "Eidos with spaces/Resource",
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(transport)),
    )
    return manager, calls, payload


def test_clean_install_is_verified_and_reused_after_restart(resources):
    manager, calls, _ = resources
    cancel = threading.Event()
    assert not manager.is_ready("document-python")
    manager.ensure(("document-python",), cancel)
    assert manager.is_ready("document-python")
    assert manager.status().packs[0].phase == "ready"
    restarted = ManagedResourceManager(manager.index, root=manager.root,
        client_factory=lambda: pytest.fail("already installed resources must not download again"))
    restarted.ensure(("document-python",), cancel)
    assert len(calls) == 1
    assert not list((manager.root / ".staging").iterdir())


def test_same_resource_requests_share_installation(resources):
    manager, calls, _ = resources
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(manager.ensure, ("document-python",), threading.Event()) for _ in range(4)]
        for future in futures:
            future.result(timeout=10)
    assert len(calls) == 1


def test_cancel_before_download_does_not_publish(resources):
    manager, calls, _ = resources
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(ResourceError, match="resource_preparation_canceled"):
        manager.ensure(("document-python",), cancel)
    assert not calls and not manager.location("document-python").exists()


def test_checksum_failure_remains_retryable_and_never_publishes(resources):
    manager, _, payload = resources
    manager.client_factory = lambda: httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"x" * len(payload))))
    with pytest.raises(ResourceError, match="resource_archive_mismatch"):
        manager.ensure(("document-python",), threading.Event())
    assert not manager.location("document-python").exists()
    assert manager.status().packs[0].error_code == "resource_archive_mismatch"
    manager.client_factory = lambda: httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=payload)))
    manager.ensure(("document-python",), threading.Event())
    assert manager.is_ready("document-python")


def test_download_redirect_cannot_leave_release_hosts(resources):
    manager, _, _ = resources
    calls = []

    def redirect(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://example.com/private"})

    manager.client_factory = lambda: httpx.Client(transport=httpx.MockTransport(redirect))
    with pytest.raises(ResourceError, match="resource_download_redirected"):
        manager.ensure(("document-python",), threading.Event())
    assert len(calls) == 1


@pytest.mark.parametrize("kind", ["traversal", "absolute", "symlink", "extra", "incomplete"])
def test_malformed_archive_cannot_escape_or_publish(resources, tmp_path, kind):
    manager, _, _ = resources
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as tar:
        entry = tarfile.TarInfo({"traversal": "../escape", "absolute": str(tmp_path / "escape")}.get(kind, "unexpected"))
        entry.size = 0
        if kind == "symlink":
            entry.type, entry.linkname = tarfile.SYMTYPE, "../escape"
        if kind != "incomplete":
            tar.addfile(entry, io.BytesIO())
    payload = output.getvalue()
    old = manager.index.packs[0]
    pack = old.model_copy(update={"archive_bytes": len(payload), "archive_sha256": hashlib.sha256(payload).hexdigest()})
    manager = ManagedResourceManager(ResourceIndex(version="1.0.0", packs=(pack,)), root=manager.root,
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=payload))))
    with pytest.raises(ResourceError):
        manager.ensure(("document-python",), threading.Event())
    assert not (tmp_path / "escape").exists()
    assert not manager.location("document-python").exists()


def test_symlinked_resource_root_is_rejected(resources, tmp_path):
    manager, _, _ = resources
    outside = tmp_path / "outside"
    outside.mkdir()
    manager.root.parent.mkdir(parents=True)
    manager.root.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ResourceError, match="resource_directory_unsafe"):
        manager.ensure(("document-python",), threading.Event())
    assert not list(outside.iterdir())


def test_preparation_records_sqlite_outcome_and_retry(resources, tmp_path):
    manager, _, _ = resources
    store = SessionStore(tmp_path / "data")
    store.initialize()
    from eidos_runtime.application.managed_resources import installation_operation
    manager.installation_operation = installation_operation(store, manager)
    try:
        manager.ensure(("document-python",), threading.Event())
        with store.database.connection() as connection:
            row = connection.execute("SELECT status, result_json FROM async_operations WHERE scope='resource/install'").fetchone()
        assert row["status"] == "completed"
        assert json.loads(row["result_json"])["packId"] == "document-python"
    finally:
        store.close()


def test_catalog_snapshot_stays_fixed_when_resources_become_ready(resources, tmp_path):
    manager, _, _ = resources
    core = tmp_path / "App runtime"
    files = {"bin/python3": b"#!/bin/sh\nexit 0\n"}
    (core / "bin").mkdir(parents=True)
    (core / "bin/python3").write_bytes(files["bin/python3"])
    (core / "bin/python3").chmod(0o700)
    (core / "runtime.json").write_text(manifest(files, executable=True).model_dump_json(by_alias=True))
    catalog = ManagedResourceCatalog(RuntimeDependencyCatalog.from_manifest(core / "runtime.json"), manager)
    frozen = catalog.snapshot()
    coordinator = RuntimeDependencyCoordinator("run-1", catalog)
    base = coordinator.default_binding()
    requirements = RuntimeRequirements.model_validate_json(json.dumps({"schemaVersion":1,"dependencies":[
        {"kind":"python-package","name":"example","importName":"example","version":">=1"}]}))
    with pytest.raises(ResourceError, match="resource_not_ready"):
        coordinator.resolve(requirements, context_id="run-1:documents:python", resource_packs=("document-python",))
    manager.ensure(("document-python",), threading.Event())
    binding = coordinator.resolve(requirements, context_id="run-1:documents:python", resource_packs=("document-python",))
    assert binding.ready and catalog.snapshot() == frozen
    coordinator.verify_binding(base)
    coordinator.verify_binding(binding)
    environment = apply_dependency_environment({"PYTHONHOME":"/wrong", "PYTHONPATH":"/wrong"}, coordinator.shell_environment(binding))
    assert environment["RUNTIME_PYTHON"] == str(core / "bin/python3")
    assert "PYTHONHOME" not in environment
    assert environment["PYTHONPATH"].startswith(str(manager.location("document-python")))
    assert environment["PYTHONNOUSERSITE"] == "1"


def test_resource_rpc_contracts_and_scoped_retry(resources, tmp_path):
    from io import StringIO
    from pathlib import Path
    from eidos_runtime.models.managed_resources import ResourceStatus, ResourceRetryRequest, ResourceRetryResponse
    from eidos_runtime.protocol.server import RuntimeServer

    fixture = json.loads((Path(__file__).resolve().parents[2] / "protocol/fixtures/managed-resources-v1.json").read_text())
    for key, model in (("status", ResourceStatus), ("retryRequest", ResourceRetryRequest), ("retryResponse", ResourceRetryResponse)):
        assert model.model_validate_json(json.dumps(fixture[key])).model_dump(mode="json", by_alias=True) == fixture[key]
    manager, calls, _ = resources
    output = StringIO()
    server = RuntimeServer(output, data_directory=tmp_path / "data")
    server.handle({"jsonrpc": "2.0", "id": "client-initialize", "method": "initialize",
                   "params": {"client": {"name": "resource-test", "version": "1"}, "protocolVersion": 1}})
    server.managed_resources.manager = manager
    try:
        def rpc(method, params, request_id):
            request_id = "client-" + request_id
            server.handle({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
            return next(json.loads(line) for line in output.getvalue().splitlines() if json.loads(line).get("id") == request_id)

        status = rpc("resource/status", {}, "status")["result"]
        assert status["available"] and status["packs"][0]["phase"] == "missing"
        assert not calls
        assert rpc("resource/retry", {"packId": "document-python", "url": "https://example.com"}, "invalid")["error"]["code"] == -32602
        assert rpc("resource/retry", {"packId": "unknown"}, "unknown")["error"]["data"]["code"] == "RESOURCE_UNKNOWN"
        assert rpc("resource/retry", {"packId": "document-python"}, "retry")["result"] == {"accepted": True}
        # Owned work must become quiescent on shutdown; no anonymous download worker.
    finally:
        server.close()


def test_offline_and_disk_full_failures_can_be_retried(resources, monkeypatch):
    import errno
    manager, _, _ = resources
    def offline(request):
        raise httpx.ConnectError("offline", request=request)
    manager.client_factory = lambda: httpx.Client(transport=httpx.MockTransport(offline))
    with pytest.raises(ResourceError, match="resource_install_failed"):
        manager.ensure(("document-python",), threading.Event())
    assert manager.status().packs[0].phase == "failed"
    with monkeypatch.context() as context:
        context.setattr(manager, "_download", lambda *args: (_ for _ in ()).throw(OSError(errno.ENOSPC, "disk full")))
        with pytest.raises(ResourceError, match="resource_disk_full"):
            manager.ensure(("document-python",), threading.Event())
    assert manager.status().packs[0].error_code == "resource_disk_full"


def test_cancel_mid_download_does_not_publish_and_restart_can_retry(resources):
    manager, calls, payload = resources
    cancel = threading.Event()
    class Interrupted(httpx.SyncByteStream):
        def __iter__(self):
            cancel.set()
            yield payload
    manager.client_factory = lambda: httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, stream=Interrupted())))
    with pytest.raises(ResourceError, match="resource_preparation_canceled"):
        manager.ensure(("document-python",), cancel)
    assert not manager.location("document-python").exists()
    restarted = ManagedResourceManager(manager.index, root=manager.root,
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=payload))))
    restarted.ensure(("document-python",), threading.Event())
    assert restarted.is_ready("document-python") and not calls


def test_incompatible_python_abi_is_rejected_without_download(resources, monkeypatch):
    from types import SimpleNamespace
    from eidos_runtime.infrastructure import managed_resources
    manager, calls, _ = resources
    pack = manager.index.packs[0].model_copy(update={"python_abi":"cp312"})
    manager = ManagedResourceManager(ResourceIndex(version="1", packs=(pack,)), root=manager.root,
                                     client_factory=lambda: pytest.fail("incompatible Python must not download"))
    with monkeypatch.context() as context:
        context.setattr(managed_resources.sys, "version_info", SimpleNamespace(major=3, minor=13))
        with pytest.raises(ResourceError, match="resource_python_abi_incompatible"):
            manager.ensure(("document-python",), threading.Event())
    assert not calls


def test_symlinked_outer_resource_parent_is_rejected_before_any_write(resources, tmp_path):
    manager, calls, _ = resources
    outside = tmp_path / "outside"
    outside.mkdir()
    manager.root.parent.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ResourceError, match="resource_directory_unsafe"):
        manager.ensure(("document-python",), threading.Event())
    assert not calls and not list(outside.iterdir())

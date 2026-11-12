import hashlib
import io
import tarfile
import threading

import httpx2 as httpx
import pytest

from eidos_runtime.infrastructure.browser_runtime import (
    BrowserRuntimeUnavailable,
    resolve_browser_runtime,
)
from eidos_runtime.infrastructure.managed_resources import ManagedResourceManager
from eidos_runtime.models.managed_resources import ResourceIndex, ResourcePack
from eidos_runtime.models.runtime_dependencies import (
    RuntimeDependencyExecutable,
    RuntimeDependencyInventoryFile,
    RuntimeDependencyManifest,
    RuntimeDependencyNodePackage,
)


def _manifest(files, **values):
    return RuntimeDependencyManifest(
        schema_version=1, bundle_id="com.idy.eidos.fixture", bundle_version="1.0.0", target="darwin-arm64",
        files=tuple(RuntimeDependencyInventoryFile(path=p, sha256=hashlib.sha256(d).hexdigest()) for p, d in files.items()),
        **values,
    )


def _archive(files, modes=None):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as tar:
        for name, data in files.items():
            entry = tarfile.TarInfo(name)
            entry.size, entry.mode = len(data), (modes or {}).get(name, 0o644)
            tar.addfile(entry, io.BytesIO(data))
    return output.getvalue()


def _pack(pack_id, location, files, manifest, requires=(), modes=None):
    payload = _archive(files, modes)
    return ResourcePack(
        id=pack_id, title=pack_id, version="1.0.0", location=location, requires=requires,
        url=f"https://github.com/idy534/Eidos/releases/download/test/{pack_id}.tar.gz",
        archive_sha256=hashlib.sha256(payload).hexdigest(), archive_bytes=len(payload),
        expanded_bytes=sum(map(len, files.values())), manifest=manifest,
    ), payload


@pytest.fixture
def manager(tmp_path):
    node_files = {"bin/node": b"#!/bin/sh\n"}
    node, node_payload = _pack("node", "runtimes/node/24.20.0/darwin-arm64", node_files, _manifest(
        node_files, executables=(RuntimeDependencyExecutable(
            name="node", path="bin/node", version="24.20.0", sha256=hashlib.sha256(node_files["bin/node"]).hexdigest(),
        ),),
    ), modes={"bin/node": 0o755})
    browser_files = {
        "node_modules/@playwright/mcp/cli.js": b"// cli\n",
        "node_modules/@playwright/mcp/package.json": b'{"name":"@playwright/mcp","version":"0.0.84"}',
    }
    browser, browser_payload = _pack("browser", "packages/node/browser/1.0.0", browser_files, _manifest(
        browser_files, node_modules="node_modules",
        node_packages=(RuntimeDependencyNodePackage(name="@playwright/mcp", version="0.0.84"),),
    ), requires=("node",))
    payloads = {"node": node_payload, "browser": browser_payload}

    def transport(request):
        return httpx.Response(200, content=payloads[str(request.url).rsplit("/", 1)[1].removesuffix(".tar.gz")])

    return ManagedResourceManager(
        ResourceIndex(version="1.0.0", packs=(node, browser)), root=tmp_path.resolve() / "Resource",
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(transport)),
    )


def test_unprepared_runtime_is_reported_without_downloading(manager):
    manager.client_factory = lambda: pytest.fail("resolution must never download")
    with pytest.raises(BrowserRuntimeUnavailable) as raised:
        resolve_browser_runtime(manager)
    assert raised.value.code == "browser_runtime_not_ready"


def test_prepared_runtime_resolves_verified_node_and_entry(manager):
    manager.ensure(("browser",), threading.Event())
    runtime = resolve_browser_runtime(manager)
    assert runtime.node == manager.location("node") / "bin/node"
    assert runtime.entry == manager.location("browser") / "node_modules/@playwright/mcp/cli.js"
    assert runtime.read_roots == (manager.location("node"), manager.location("browser"))


def test_missing_manager_or_pack_is_unavailable(manager):
    with pytest.raises(BrowserRuntimeUnavailable) as raised:
        resolve_browser_runtime(None)
    assert raised.value.code == "browser_runtime_unavailable"
    without_browser = ManagedResourceManager(
        ResourceIndex(version="1.0.0", packs=(manager.pack("node"),)), root=manager.root,
    )
    with pytest.raises(BrowserRuntimeUnavailable) as raised:
        resolve_browser_runtime(without_browser)
    assert raised.value.code == "browser_runtime_unavailable"


def test_tampered_entry_is_not_ready(manager):
    manager.ensure(("browser",), threading.Event())
    (manager.location("browser") / "node_modules/@playwright/mcp/cli.js").unlink()
    with pytest.raises(BrowserRuntimeUnavailable) as raised:
        resolve_browser_runtime(manager)
    assert raised.value.code == "browser_runtime_not_ready"

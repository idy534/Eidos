from __future__ import annotations

import json
from pathlib import Path
import threading

import pytest

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.extensions.mcp import McpManager
from eidos_runtime.extensions.plugin_package import read_package
from eidos_runtime.extensions.plugins import PluginCatalog
from eidos_runtime.extensions.plugins import PluginImportError
from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel
from eidos_runtime.runtime.resource_registry import ResourceRegistry
from eidos_runtime.sandbox.seatbelt import is_seatbelt_usable


def files(*, portable=True, server=None):
    path = "plugin.json" if portable else ".codex-plugin/plugin.json"
    return {path: json.dumps({"name": "packaged-demo", "version": "1.0.0"}).encode(),
            "mcp.json" if portable else ".mcp.json": json.dumps({"mcpServers": {
                "fixture": server or {"type": "stdio", "command": "${RUNTIME_PYTHON}",
                                       "args": ["${PLUGIN_ROOT}/server.py"]},
            }}).encode(), "server.py": b"# fixture\n"}


@pytest.mark.parametrize("portable", [True, False])
def test_portable_and_codex_packages_normalize_bundled_stdio_configuration(portable):
    package = read_package(files(portable=portable))
    assert package.manifest.mcp_servers[0].id == "fixture"
    assert package.manifest.mcp_servers[0].enabled
    assert not package.compatibility.issues


@pytest.mark.parametrize("server,issue", [
    ({"type": "sse", "url": "https://example.com/mcp"}, "mcp_transport_unsupported:fixture"),
    ({"command": "python3", "env": {"TOKEN": "private-value"}}, "mcp_environment_unsupported:fixture"),
    ({"command": "python3", "cwd": "/tmp"}, "mcp_settings_unsupported:fixture"),
])
def test_unsupported_mcp_declarations_are_visible_without_becoming_callable(server, issue):
    package = read_package(files(server=server))
    assert not package.manifest.mcp_servers
    assert package.compatibility.issues == (issue,)
    assert "private-value" not in package.model_dump_json()


def test_environment_references_contain_only_names_in_the_normalized_manifest():
    package = read_package(files(server={"command": "python3", "env": {"TOKEN": "${MY_API_TOKEN}"}}))
    config = package.manifest.mcp_servers[0]
    assert config.env_bindings == {"TOKEN": "MY_API_TOKEN"}
    assert config.env_names == ["TOKEN"]


@pytest.mark.parametrize("command", ["./bin/tool", "${PLUGIN_ROOT}/bin/tool", "${CLAUDE_PLUGIN_ROOT}/bin/tool"])
def test_relative_packaged_executable_is_validated_and_preserved(tmp_path, command):
    source = tmp_path / "source"
    (source / "bin").mkdir(parents=True)
    package_files = files(server={"command": command})
    package_files["bin/tool"] = b"#!/bin/sh\nexit 0\n"
    for name, contents in package_files.items():
        (source / name).write_bytes(contents)
    store = SessionStore(tmp_path / "data")
    store.initialize()
    try:
        catalog = PluginCatalog(store)
        catalog.import_directory(source)
        installed = catalog.installed_root("packaged-demo") / "bin/tool"
        assert installed.stat().st_mode & 0o700 == 0o700
    finally:
        store.close()


def test_inline_metadata_overrides_codex_overlay_and_binds_packaged_onboarding():
    package_files = files()
    package_files["plugin.json"] = json.dumps({
        "name": "packaged-demo", "version": "1.0.0", "license": "MIT",
        "extensions": {"com.openai": {"interface": {"displayName": "Demo integration", "developerName": "Example"},
                                       "onboardingSkill": "./skills/setup/SKILL.md"}},
    }).encode()
    package_files[".codex-plugin/plugin.json"] = b'{"name":"ignored","interface":{"displayName":"Wrong"}}'
    package_files["skills/setup/SKILL.md"] = b"---\nname: setup\ndescription: Configure this plugin.\n---\nRead the setup guide."
    package = read_package(package_files)
    assert package.manifest.id == "packaged-demo"
    assert package.manifest.name == "Demo integration"
    assert package.presentation.developer_name == "Example"
    assert package.presentation.onboarding_skill_id == "packaged-demo:setup"


def test_import_validates_skill_metadata_before_publishing(tmp_path):
    source = tmp_path / "source"
    (source / "skills/broken").mkdir(parents=True)
    (source / "plugin.json").write_text('{"name":"broken","version":"1.0.0"}')
    (source / "skills/broken/SKILL.md").write_text("---\nname: broken\n---\nNo description.")
    store = SessionStore(tmp_path / "data")
    store.initialize()
    try:
        catalog = PluginCatalog(store)
        with pytest.raises(PluginImportError, match="plugin_manifest_invalid"):
            catalog.import_directory(source)
        assert not catalog.list_plugins()
        assert not (catalog.root / "broken").exists()
    finally:
        store.close()


@pytest.mark.parametrize("sandbox", [False, True])
def test_bundled_mcp_runs_through_existing_sdk_manager(tmp_path: Path, sandbox: bool):
    if sandbox and not is_seatbelt_usable():
        pytest.skip("Packaged MCP requires working macOS Seatbelt")
    source = tmp_path / "source"
    source.mkdir()
    package_files = files()
    package_files["server.py"] = (Path(__file__).parent / "fixtures/mcp_fixture.py").read_bytes()
    for name, content in package_files.items():
        (source / name).write_bytes(content)
    store = SessionStore(tmp_path / "data")
    store.initialize()
    plugins = PluginCatalog(store)
    resources = ResourceRegistry()
    kernel = RuntimeAsyncKernel(resource_registry=resources)
    kernel.start()
    manager = None
    try:
        plugins.import_directory(source)
        plugins.set_enabled("packaged-demo", True)
        assert not plugins.list_mcp_servers()[0]["available"]
        plugins.set_mcp_enabled("packaged-demo", "fixture", True)
        manager = McpManager(plugins, plugins.extension_snapshot(), source, sandbox=sandbox,
                             async_kernel=kernel, resource_registry=resources)
        entries = manager.start()
        echo = next(entry for entry in entries if entry.spec.name.endswith("__echo"))
        result = echo.adapter.execute({"message": "packaged hello"}, threading.Event())
        assert result["outcome"] == "success"
        assert result["data"]["text"] == "packaged hello"
    finally:
        if manager:
            manager.close()
        kernel.close()
        assert resources.active_resources() == ()
        store.close()

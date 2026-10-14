from __future__ import annotations

import os
from pathlib import Path
import threading
import sys
from unittest.mock import Mock
from uuid import uuid4

from PIL import Image
import pytest

from eidos_runtime.extensions.contracts import McpServerConfigV1
from eidos_runtime.extensions.mcp import McpToolAdapter
from eidos_runtime.extensions.mcp_outputs import McpOutputDirectory, is_playwright_server
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.extensions.plugins import PluginCatalog
from eidos_runtime.extensions.skills import SkillCatalog
from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel
from eidos_runtime.runtime.resource_registry import ResourceRegistry
from eidos_runtime.runtime.run_resources import RunResources
from eidos_runtime.tools.json_schema import BoundedJsonSchema
from eidos_runtime.tools.registry import VerifiedToolOutput
from eidos_runtime.tools.view_image import ViewImageRootAuthority, ViewImageTool
from eidos_runtime.tools.workspace import resolve_read_path
from eidos_runtime.tools.workspace import ToolExecutor


def _config(executable: str, argv: list[str]) -> McpServerConfigV1:
    return McpServerConfigV1.model_validate({
        "id": "renamed-browser", "executable": executable, "argv": argv,
        "permissionProfile": "connector",
    })


def test_outputs_use_two_levels_and_remain_after_connection_lifetime(tmp_path: Path) -> None:
    run_id = str(uuid4())
    directory = McpOutputDirectory.create("manual", "playwright", run_id)
    assert directory.path == tmp_path / "eidos-mcp" / "manual.playwright" / run_id
    assert directory.path.stat().st_mode & 0o777 == 0o700
    (directory.path / "screenshot.png").write_bytes(b"persisted")
    reopened = McpOutputDirectory.create("manual", "playwright", run_id)
    assert reopened.path == directory.path
    assert (reopened.path / "screenshot.png").read_bytes() == b"persisted"
    assert McpOutputDirectory.create("manual", "playwright", str(uuid4())).path != directory.path
    assert McpOutputDirectory.create("plugin", "playwright", run_id).path != directory.path


def test_home_is_shared_by_service_while_run_files_and_legacy_home_stay_separate() -> None:
    first = McpOutputDirectory.create("manual", "browser", str(uuid4()))
    second = McpOutputDirectory.create("manual", "browser", str(uuid4()))
    legacy_home = first.path / "h"
    legacy_home.mkdir()
    (legacy_home / "config").write_text("legacy state")
    home = first.home_path()
    assert home == first.path.parent / "h"
    assert list(home.iterdir()) == []
    (home / "config").write_text("service state")
    assert second.home_path() == home
    assert (second.home_path() / "config").read_text() == "service state"
    assert first.path != second.path
    assert not (first.path / "t").exists()
    assert not (second.path / "t").exists()
    assert (legacy_home / "config").read_text() == "legacy state"
    for plugin, server in (("plugin", "browser"), ("manual", "other")):
        other = McpOutputDirectory.create(plugin, server, str(uuid4()))
        assert other.home_path() != home
        assert list(other.home_path().iterdir()) == []


def test_output_root_rejects_symlink_and_replaced_run_directory(tmp_path: Path) -> None:
    root = Path(os.environ["EIDOS_MCP_OUTPUT_ROOT"])
    outside = tmp_path / "outside"
    outside.mkdir()
    root.symlink_to(outside, target_is_directory=True)
    with pytest.raises((OSError, ValueError)):
        McpOutputDirectory.create("manual", "browser", str(uuid4()))
    assert list(outside.iterdir()) == []
    root.unlink()
    directory = McpOutputDirectory.create("manual", "browser", str(uuid4()))
    directory.path.rmdir()
    directory.path.symlink_to(outside, target_is_directory=True)
    with pytest.raises((OSError, ValueError)):
        directory.verify()


def test_home_directory_is_private_reusable_and_rejects_symlinks(tmp_path: Path) -> None:
    directory = McpOutputDirectory.create("manual", "browser", str(uuid4()))
    path = directory.home_path()
    assert path == directory.path.parent / "h"
    assert path.stat().st_mode & 0o777 == 0o700
    (path / "keep").write_text("run data")
    assert directory.home_path() == path
    assert (path / "keep").read_text() == "run data"
    path.rename(directory.path / "home-original")
    outside = tmp_path / "outside-environment"
    outside.mkdir()
    path.symlink_to(outside, target_is_directory=True)
    with pytest.raises((OSError, ValueError)):
        directory.home_path()
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("executable,argv", [
    ("npx", ["@playwright/mcp@latest"]),
    ("/opt/homebrew/bin/npx", ["-y", "@playwright/mcp@0.0.78"]),
    ("node", ["/opt/packages/node_modules/@playwright/mcp/cli.js"]),
    ("playwright-mcp", []),
])
def test_playwright_detection_uses_command_not_user_server_name(executable: str, argv: list[str]) -> None:
    assert is_playwright_server(_config(executable, argv))
    assert not is_playwright_server(_config("python", ["server.py"]))


def test_playwright_output_filenames_are_routed_without_changing_inputs() -> None:
    directory = McpOutputDirectory.create("manual", "playwright", str(uuid4()))
    arguments = {"filename": "screen.png", "fullPage": True}
    routed = directory.playwright_arguments("browser_take_screenshot", arguments)
    assert routed == {"filename": str(directory.path / "screen.png"), "fullPage": True}
    assert arguments["filename"] == "screen.png"
    assert directory.playwright_arguments("browser_file_upload", arguments) == arguments
    assert directory.playwright_arguments("browser_take_screenshot", {"filename": "/explicit/file.png"}) == {"filename": "/explicit/file.png"}
    with pytest.raises(ValueError):
        directory.playwright_arguments("browser_take_screenshot", {"filename": "../escape.png"})


def test_adapter_routes_before_call_and_refuses_replaced_output_root(tmp_path: Path) -> None:
    directory = McpOutputDirectory.create("manual", "playwright", str(uuid4()))
    connection = Mock(output_directory=directory, playwright_server=True)
    connection.call.return_value = {"outcome": "success", "code": "ok", "data": {}}
    adapter = McpToolAdapter(
        connection, "browser_take_screenshot", "mcp__browser__browser_take_screenshot",
        BoundedJsonSchema({"type": "object", "properties": {}, "additionalProperties": False}), None,
    )
    cancel = threading.Event()
    assert adapter.execute({"filename": "screen.png"}, cancel)["code"] == "ok"
    connection.call.assert_called_once_with("browser_take_screenshot", {"filename": str(directory.path / "screen.png")}, cancel)
    connection.call.reset_mock()
    assert adapter.execute({"filename": "../escape.png"}, cancel)["code"] == "TOOL_ARGUMENT_CONTRACT_VIOLATION"
    connection.call.assert_not_called()
    directory.path.rmdir()
    directory.path.symlink_to(tmp_path, target_is_directory=True)
    assert adapter.execute({"filename": "screen.png"}, cancel)["code"] == "mcp_output_directory_unavailable"
    connection.call.assert_not_called()


def test_current_run_outputs_are_readable_but_neighbor_runs_are_not(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    current = McpOutputDirectory.create("manual", "playwright", str(uuid4()))
    neighbor = McpOutputDirectory.create("manual", "playwright", str(uuid4()))
    current_image = current.path / "screen.png"
    neighbor_image = neighbor.path / "screen.png"
    Image.new("RGB", (1, 1)).save(current_image)
    Image.new("RGB", (1, 1)).save(neighbor_image)
    home = current.home_path()
    home_image = home / "private.png"
    Image.new("RGB", (1, 1)).save(home_image)
    authority = ViewImageRootAuthority(workspace, mcp_output_roots=(current.path,))
    tool = ViewImageTool(authority)
    result = tool.execute({"path": str(current_image)}, threading.Event())
    assert isinstance(result, VerifiedToolOutput)
    assert result.result["code"] == "ok"
    rejected = tool.execute({"path": str(neighbor_image)}, threading.Event())
    assert isinstance(rejected, dict) and rejected["code"] == "path_outside_authority"
    rejected = tool.execute({"path": str(home_image)}, threading.Event())
    assert isinstance(rejected, dict) and rejected["code"] == "path_outside_authority"
    resolved = resolve_read_path(str(current.path / "snapshot.yml"), workspace, (), (current.path,))
    assert resolved.authority == "mcp_output" and not resolved.writable
    with pytest.raises(ValueError):
        resolve_read_path(str(neighbor_image), workspace, (), (current.path,))
    with pytest.raises(ValueError):
        resolve_read_path(str(home_image), workspace, (), (current.path,))
    (current.path / "snapshot.yml").write_text("page: managed")
    executor = ToolExecutor(workspace)
    executor.set_mcp_output_roots(lambda: (current.path,))
    try:
        result = executor.execute("read_file", {"path": str(current.path / "snapshot.yml")}, threading.Event())
        assert result["code"] == "ok"
        assert "page: managed" in str(result["data"])
        rejected = executor.execute("read_file", {"path": str(neighbor_image)}, threading.Event())
        assert rejected["code"] != "ok"
        rejected = executor.execute("read_file", {"path": str(home_image)}, threading.Event())
        assert rejected["code"] != "ok"
    finally:
        executor.close()


@pytest.mark.integration
def test_run_resources_use_real_run_id_and_grant_only_current_outputs(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    plugins = PluginCatalog(store)
    plugins.create_manual_mcp(
        server_id="fixture", executable=sys.executable,
        argv=[str(Path(__file__).parent / "fixtures" / "mcp_fixture.py")],
        env={}, env_names=[], cwd=str(workspace), permission_profile="connector",
        startup_timeout_seconds=5, tool_timeout_seconds=2,
    )
    plugins.set_mcp_enabled("manual", "fixture", True)
    snapshot = SkillCatalog(plugins).extension_snapshot()
    session = store.create_session(str(workspace))
    run, _ = store.create_run(session["id"], "Inspect outputs", extension_snapshot=snapshot)
    resources = ResourceRegistry()
    kernel = RuntimeAsyncKernel(resource_registry=resources)
    kernel.start()
    try:
        with RunResources(
            store, run["id"], snapshot, async_kernel=kernel,
            resource_registry=resources, mcp_sandbox=False, supports_images=True,
        ) as started:
            assert started.mcp is not None
            output = started.mcp.output_roots()[0]
            assert output.name == run["id"]
            assert started.image_authority().mcp_output_roots == (output,)
            (output / "snapshot.yml").write_text("page: persisted")
            result = started.tool_executor.execute("read_file", {"path": str(output / "snapshot.yml")}, threading.Event())
            assert result["code"] == "ok"
        assert (output / "snapshot.yml").read_text() == "page: persisted"
    finally:
        kernel.close()
        store.close()
    assert resources.active_resources() == ()

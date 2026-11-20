from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from eidos_runtime.db.storage import ResourceNotFoundError, SessionStore
from eidos_runtime.extensions.plugins import PluginCatalog, PluginImportError
from eidos_runtime.persistence.hooks import HookRepository


def package(root: Path, version: str = "1.0.0") -> Path:
    source = root / ("source-" + version)
    (source / "skills/review").mkdir(parents=True, exist_ok=True)
    (source / "skills/review/SKILL.md").write_text(
        "---\nname: review\ndescription: Review code.\n---\nReview carefully.\n",
    )
    (source / "server.py").write_text("# fixture\n")
    (source / "plugin.json").write_text(json.dumps({
        "schemaVersion": 1, "id": "demo", "name": "Demo", "version": version,
        "description": "Activation fixture", "skills": [{"root": "skills/review"}],
        "hooks": {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "true"}]}]}},
        "mcpServers": [
            {"id": "tools", "executable": "python3", "argv": ["server.py"],
             "permissionProfile": "workspace_read", "enabled": True},
            {"id": "dormant", "executable": "python3", "argv": ["server.py"],
             "permissionProfile": "workspace_read", "enabled": False},
            {"id": "remote", "transport": "streamable-http", "url": "https://example.com/mcp",
             "permissionProfile": "connector", "enabled": True},
        ],
    }))
    return source


@pytest.fixture
def catalog(tmp_path):
    store = SessionStore(tmp_path / "data")
    store.initialize()
    try:
        yield PluginCatalog(store)
    finally:
        store.close()


def servers(catalog: PluginCatalog) -> dict[str, dict[str, object]]:
    return {str(server["serverId"]): server for server in catalog.list_mcp_servers()}


def event_count(catalog: PluginCatalog, event_type: str) -> int:
    return catalog.store.connection.execute(
        "SELECT COUNT(*) FROM events WHERE event_type = ?", (event_type,),
    ).fetchone()[0]


def test_activation_enables_the_reviewed_version_with_its_local_resources(catalog, tmp_path):
    imported = catalog.import_directory(package(tmp_path))
    assert not imported["enabled"]
    assert not servers(catalog)["tools"]["consented"]

    activated = catalog.activate("demo", imported["contentHash"])

    assert activated["enabled"]
    assert activated["hookReview"]["trusted"]
    state = servers(catalog)
    assert state["tools"]["consented"] and state["tools"]["available"]
    # Servers the package leaves off, and remote services that need an account, stay closed.
    assert not state["dormant"]["consented"]
    assert not state["remote"]["consented"]
    snapshot = catalog.extension_snapshot()
    assert [hook["pluginId"] for hook in snapshot["hooks"]] == ["demo"]
    assert event_count(catalog, "mcp_server.state_changed") == 1


def test_reactivation_keeps_authorized_server_errors_and_regrants_stopped_servers(catalog, tmp_path):
    imported = catalog.import_directory(package(tmp_path))
    catalog.activate("demo", imported["contentHash"])
    catalog.store.set_mcp_server_state(servers(catalog)["tools"], consented=True,
                                       error_code="mcp_connection_lost")
    events = event_count(catalog, "mcp_server.state_changed")

    catalog.activate("demo", imported["contentHash"])
    assert servers(catalog)["tools"]["errorCode"] == "mcp_connection_lost"
    assert event_count(catalog, "mcp_server.state_changed") == events

    catalog.set_mcp_enabled("demo", "tools", False)
    catalog.set_enabled("demo", False)
    catalog.activate("demo", imported["contentHash"])
    tools = servers(catalog)["tools"]
    assert tools["consented"] and "errorCode" not in tools


def test_stale_review_grants_nothing(catalog, tmp_path):
    first = catalog.import_directory(package(tmp_path, "1.0.0"))
    second = catalog.import_directory(package(tmp_path, "2.0.0"))

    catalog.cleanup_removed()
    assert not (catalog.root / "demo/1.0.0").exists()
    for content_hash in (first["contentHash"], "0" * 64):
        with pytest.raises(ResourceNotFoundError):
            catalog.activate("demo", content_hash)

    record = catalog.store.plugin_record("demo")
    assert record is not None and not record["enabled"]
    assert not HookRepository(catalog.store.database).trusted("demo", second["contentHash"])
    assert not servers(catalog)["tools"]["consented"]


def test_changed_package_contents_grant_nothing(catalog, tmp_path):
    imported = catalog.import_directory(package(tmp_path))
    (catalog.installed_root("demo") / "server.py").write_text("# changed\n")

    with pytest.raises(PluginImportError, match="plugin_unavailable"):
        catalog.activate("demo", imported["contentHash"])

    record = catalog.store.plugin_record("demo")
    assert record is not None and not record["enabled"]
    # The damaged package cannot be listed, so read the stored consent directly.
    assert not catalog.store.mcp_server_state("demo", "tools")["consented"]
    assert not HookRepository(catalog.store.database).trusted("demo", imported["contentHash"])


def test_event_failure_rolls_back_every_grant(catalog, tmp_path):
    imported = catalog.import_directory(package(tmp_path))
    with patch("eidos_runtime.db.repositories.extensions.append_event",
               side_effect=RuntimeError("event failed")):
        with pytest.raises(RuntimeError, match="event failed"):
            catalog.activate("demo", imported["contentHash"])

    record = catalog.store.plugin_record("demo")
    assert record is not None and not record["enabled"]
    assert not HookRepository(catalog.store.database).trusted("demo", imported["contentHash"])
    assert not servers(catalog)["tools"]["consented"]

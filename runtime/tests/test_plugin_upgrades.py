from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from concurrent.futures import ThreadPoolExecutor
import threading
from unittest.mock import patch

import pytest

from eidos_runtime.db.plugin_versions_migration import migrate_plugin_versions
from eidos_runtime.db.repositories.extensions import ExtensionRepository
from eidos_runtime.db.schema import V36_SCHEMA_SQL
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.db.layout import STATE_DATABASE_NAME
from eidos_runtime.extensions.plugins import PluginCatalog, PluginImportError
from eidos_runtime.extensions import plugins as plugin_module
from eidos_runtime.extensions.skills import SkillCatalog
from eidos_runtime.persistence.hooks import HookRepository


def package(root: Path, version: str, *, contents: str = "Review carefully.") -> Path:
    source = root / ("source-" + version)
    (source / "skills/review").mkdir(parents=True, exist_ok=True)
    (source / "skills/review/SKILL.md").write_text(
        "---\nname: review\ndescription: Review code.\nlicense: MIT\n---\n" + contents,
    )
    (source / "server.py").write_text("# fixture\n")
    (source / "plugin.json").write_text(json.dumps({
        "schemaVersion": 1, "id": "demo", "name": "Demo", "version": version,
        "description": "Versioned fixture", "skills": [{"root": "skills/review"}],
        "hooks": {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "true"}]}]}},
        "mcpServers": [{"id": "demo-server", "executable": "python3", "argv": ["server.py"],
                        "permissionProfile": "workspace_read", "enabled": True}],
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


def test_upgrade_preserves_pinned_skill_after_restart_and_cleans_after_run(catalog, tmp_path):
    first = catalog.import_directory(package(tmp_path, "1.0.0"))
    catalog.set_enabled("demo", True)
    skills = SkillCatalog(catalog)
    snapshot = skills.extension_snapshot()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = catalog.store.create_session(str(workspace))
    run, _ = catalog.store.create_run(session["id"], "review", extension_snapshot=snapshot)
    old_root = catalog.installed_root("demo")
    catalog.import_directory(package(tmp_path, "2.0.0", contents="Use the new rules."))
    assert catalog.installed_root("demo") != old_root
    catalog.cleanup_removed()
    assert old_root.exists()
    data = catalog.store.data_directory
    catalog.store.close()
    catalog.store = SessionStore(data)
    catalog.store.initialize()
    assert catalog.package("demo", expected_hash=first["contentHash"]).manifest.version == "1.0.0"
    pinned = SkillCatalog(catalog).read_skill(snapshot, "demo:review")
    assert "Review carefully." in pinned["content"]
    assert "new rules" not in pinned["content"]
    catalog.store.cancel_run(run["id"])
    catalog.cleanup_removed()
    assert not old_root.exists()
    assert catalog.installed_root("demo").exists()


def test_upgrade_resets_enabled_state_trust_and_server_consent(catalog, tmp_path):
    first = catalog.import_directory(package(tmp_path, "1.0.0"))
    catalog.set_enabled("demo", True)
    catalog.set_hook_trust("demo", first["contentHash"], True)
    catalog.set_mcp_enabled("demo", "demo-server", True)
    second = catalog.import_directory(package(tmp_path, "2.0.0"))
    assert not second["enabled"]
    assert not second["hookReview"]["trusted"]
    assert not catalog.list_mcp_servers()[0]["consented"]
    assert second == catalog.import_directory(tmp_path / "source-2.0.0")


def test_failed_upgrade_retains_current_version_and_removes_only_new_files(catalog, tmp_path):
    first = catalog.import_directory(package(tmp_path, "1.0.0"))
    original = catalog.installed_root("demo")
    with patch.object(ExtensionRepository, "replace_plugin_version", side_effect=RuntimeError("commit failed")):
        with pytest.raises(RuntimeError, match="commit failed"):
            catalog.import_directory(package(tmp_path, "2.0.0"))
    assert catalog.list_plugins()[0]["contentHash"] == first["contentHash"]
    assert original.exists()
    assert not (original.parent / "2.0.0").exists()


def test_archived_version_cannot_be_replaced_but_can_be_explicitly_restored(catalog, tmp_path):
    old = package(tmp_path, "1.0.0")
    first = catalog.import_directory(old)
    catalog.import_directory(package(tmp_path, "2.0.0"))
    assert catalog.import_directory(old)["contentHash"] == first["contentHash"]
    catalog.import_directory(tmp_path / "source-2.0.0")
    (old / "server.py").write_text("# changed\n")
    with pytest.raises(PluginImportError, match="plugin_version_conflict"):
        catalog.import_directory(old)


def test_event_failure_rolls_back_version_pointer_and_revoked_permissions(catalog, tmp_path):
    first = catalog.import_directory(package(tmp_path, "1.0.0"))
    catalog.set_enabled("demo", True)
    catalog.set_hook_trust("demo", first["contentHash"], True)
    catalog.set_mcp_enabled("demo", "demo-server", True)
    with patch("eidos_runtime.db.repositories.extensions.append_event", side_effect=RuntimeError("event failed")):
        with pytest.raises(RuntimeError, match="event failed"):
            catalog.import_directory(package(tmp_path, "2.0.0"))
    assert catalog.store.plugin_record("demo")["version"] == "1.0.0"
    assert catalog.store.plugin_record("demo")["enabled"]
    assert HookRepository(catalog.store.database).trusted("demo", first["contentHash"])
    assert catalog.list_mcp_servers()[0]["consented"]
    assert ExtensionRepository(catalog.store.database).plugin_version("demo", version="2.0.0") is None
    assert not (catalog.root / "demo/2.0.0").exists()


def test_concurrent_updates_cannot_overwrite_a_new_current_version(catalog, tmp_path):
    catalog.import_directory(package(tmp_path, "1.0.0"))
    sources = [package(tmp_path, version) for version in ("2.0.0", "3.0.0")]
    ready = threading.Barrier(2)
    install = plugin_module._install_files
    def stage(destination, contents):
        install(destination, contents)
        ready.wait(timeout=5)
    def attempt(source):
        try:
            return catalog.import_directory(source)["version"]
        except PluginImportError as error:
            return str(error)
    with patch.object(plugin_module, "_install_files", side_effect=stage):
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(attempt, sources))
    assert results.count("plugin_version_conflict") == 1
    winner = next(value for value in results if value != "plugin_version_conflict")
    assert catalog.store.plugin_record("demo")["version"] == winner
    assert sorted(path.name for path in (catalog.root / "demo").iterdir()) == sorted(["1.0.0", winner])


def test_cross_plugin_mcp_id_collision_is_rejected_before_install(catalog, tmp_path):
    catalog.import_directory(package(tmp_path, "1.0.0"))
    source = package(tmp_path, "2.0.0")
    value = json.loads((source / "plugin.json").read_text())
    value["id"] = "other"
    (source / "plugin.json").write_text(json.dumps(value))
    with pytest.raises(PluginImportError, match="mcp_server_id_conflict"):
        catalog.import_directory(source)
    assert catalog.store.plugin_record("other") is None


def test_version_migration_backfills_current_packages_and_rolls_back_on_failure():
    connection = sqlite3.connect(":memory:")
    connection.executescript(V36_SCHEMA_SQL + "PRAGMA user_version=36;")
    connection.execute("INSERT INTO plugins VALUES(?,?,?,?,?,?,0,'installed',1,1)",
                       ("demo", "Demo", "1.0.0", "fixture", "{}", "a" * 64))
    connection.commit()
    migrate_plugin_versions(connection)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 37
    assert connection.execute("SELECT * FROM plugin_versions").fetchone() == ("demo", "1.0.0", "a" * 64)
    connection.close()
    broken = sqlite3.connect(":memory:")
    broken.executescript("PRAGMA user_version=36;")
    with pytest.raises(sqlite3.Error):
        migrate_plugin_versions(broken)
    assert broken.execute("PRAGMA user_version").fetchone()[0] == 36
    assert broken.execute("SELECT name FROM sqlite_master WHERE name='plugin_versions'").fetchone() is None
    broken.close()


def test_real_v36_state_database_reopens_with_version_history(
    catalog, tmp_path, downgrade_document_memory_schema
):
    imported = catalog.import_directory(package(tmp_path, "1.0.0"))
    data = catalog.store.data_directory
    catalog.store.close()
    connection = sqlite3.connect(data / STATE_DATABASE_NAME)
    downgrade_document_memory_schema(connection)
    connection.executescript("DROP TABLE mcp_connections; DROP TABLE plugin_versions; PRAGMA user_version=36;")
    connection.close()
    catalog.store = SessionStore(data)
    catalog.store.initialize()
    assert catalog.store.health_state == "ready"
    assert catalog.package("demo", expected_hash=imported["contentHash"]).manifest.version == "1.0.0"

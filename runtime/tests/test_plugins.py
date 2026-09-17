from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eidos_runtime.db.storage import SessionStore  # noqa: E402
from eidos_runtime.extensions.plugins import (  # noqa: E402
    PluginCatalog,
    PluginImportError,
)


def manifest(plugin_id: str = "demo", version: str = "1.0.0") -> dict[str, object]:
    return {
        "schemaVersion": 1,
        "id": plugin_id,
        "name": "Demo plugin",
        "version": version,
        "description": "A local fixture.",
        "skills": [{"root": "skills/review"}],
        "mcpServers": [],
    }


class PluginCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-plugins-")
        root = Path(self.temporary.name)
        self.data = root / "data"
        self.source = root / "source"
        self.data.mkdir(mode=0o700)
        self.source.mkdir()
        (self.source / "skills" / "review").mkdir(parents=True)
        (self.source / "skills" / "review" / "SKILL.md").write_text(
            "---\nname: review\ndescription: Review files.\n---\nRead carefully.\n",
            encoding="utf-8",
        )
        (self.source / "plugin.json").write_text(
            json.dumps(manifest()), encoding="utf-8"
        )
        self.store = SessionStore(self.data)
        self.store.initialize()
        self.catalog = PluginCatalog(self.store)

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_import_is_private_atomic_and_idempotent(self) -> None:
        first = self.catalog.import_directory(self.source)
        second = self.catalog.import_directory(self.source)

        self.assertEqual(first, second)
        self.assertFalse(first["enabled"])
        installed = self.catalog.installed_root("demo")
        self.assertEqual(installed.stat().st_mode & 0o777, 0o700)
        self.assertEqual(
            (installed / "plugin.json").stat().st_mode & 0o777, 0o600
        )
        self.assertFalse(any(path.name.startswith(".import-") for path in installed.parent.iterdir()))

    def test_same_id_and_version_with_different_content_conflicts(self) -> None:
        self.catalog.import_directory(self.source)
        (self.source / "skills" / "review" / "SKILL.md").write_text(
            "changed", encoding="utf-8"
        )

        with self.assertRaisesRegex(PluginImportError, "plugin_version_conflict"):
            self.catalog.import_directory(self.source)

    def test_unknown_manifest_field_and_symlink_are_rejected_without_install(self) -> None:
        invalid = manifest()
        invalid["entrypoint"] = "run.py"
        (self.source / "plugin.json").write_text(json.dumps(invalid), encoding="utf-8")
        with self.assertRaisesRegex(PluginImportError, "plugin_manifest_invalid"):
            self.catalog.import_directory(self.source)
        self.assertEqual(self.catalog.list_plugins(), [])

        (self.source / "plugin.json").write_text(json.dumps(manifest()), encoding="utf-8")
        os.symlink("SKILL.md", self.source / "skills" / "review" / "linked.md")
        with self.assertRaisesRegex(PluginImportError, "plugin_source_invalid"):
            self.catalog.import_directory(self.source)
        self.assertEqual(self.catalog.list_plugins(), [])

    def test_enable_disable_remove_persist_without_deleting_history_row(self) -> None:
        self.catalog.import_directory(self.source)
        enabled = self.catalog.set_enabled("demo", True)
        self.assertTrue(enabled["enabled"])
        removed = self.catalog.remove("demo")
        self.assertEqual(removed["status"], "removed")
        self.assertEqual(self.catalog.remove("demo"), removed)

        self.store.close()
        self.store = SessionStore(self.data)
        self.store.initialize()
        self.catalog = PluginCatalog(self.store)
        self.assertEqual(self.catalog.list_plugins(include_removed=True), [removed])
        self.assertFalse(self.catalog.installed_root("demo").exists())

    def test_run_snapshot_is_immutable_and_active_reference_defers_resource_cleanup(self) -> None:
        plugin = self.catalog.import_directory(self.source)
        self.catalog.set_enabled("demo", True)
        snapshot = self.catalog.extension_snapshot()
        workspace = Path(self.temporary.name) / "workspace"
        workspace.mkdir()
        session = self.store.create_session(str(workspace))
        run, _ = self.store.create_run(
            session["id"], "hold plugin", extension_snapshot=snapshot
        )
        installed = self.catalog.installed_root("demo")

        self.catalog.remove("demo")

        self.assertTrue(installed.exists())
        self.assertEqual(
            self.store.read_run(run["id"])["extensionSnapshot"], snapshot
        )
        self.assertEqual(snapshot["plugins"][0]["contentHash"], plugin["contentHash"])

    def test_manual_mcp_config_persists_without_exposing_environment_values(self) -> None:
        cwd = Path(self.temporary.name) / "manual-cwd"
        cwd.mkdir()
        created = self.catalog.create_manual_mcp(
            server_id="local",
            executable=sys.executable,
            argv=["-c", "fixture"],
            env={"MCP_SECRET": "secret-value"},
            env_names=["PATH"],
            cwd=str(cwd),
            permission_profile="workspace_read",
            startup_timeout_seconds=5,
            tool_timeout_seconds=10,
        )

        self.assertEqual(created["pluginId"], "manual")
        self.assertFalse(created["consented"])
        self.assertEqual(created["cwd"], str(cwd.resolve()))
        self.assertNotIn("secret-value", json.dumps(created))
        self.assertEqual(
            self.catalog.manual_mcp_server_config("local")["env"],
            {"MCP_SECRET": "secret-value"},
        )
        self.assertTrue(all(
            path.stat().st_mode & 0o777 == 0o600
            for path in (self.data / "blobs").rglob("*")
            if path.is_file()
        ))
        self.assertNotIn(
            "secret-value",
            json.dumps(self.catalog.extension_snapshot()),
        )

        self.store.close()
        self.store = SessionStore(self.data)
        self.store.initialize()
        self.catalog = PluginCatalog(self.store)
        self.assertEqual(
            self.catalog.manual_mcp_server_config("local")["env"],
            {"MCP_SECRET": "secret-value"},
        )
        with self.assertRaisesRegex(PluginImportError, "mcp_server_id_conflict"):
            self.catalog.create_manual_mcp(
                server_id="local",
                executable=sys.executable,
                argv=[],
                env={},
                env_names=[],
                cwd=str(cwd),
                permission_profile="workspace_read",
                startup_timeout_seconds=5,
                tool_timeout_seconds=10,
            )
        with self.assertRaisesRegex(PluginImportError, "mcp_cwd_invalid"):
            self.catalog.create_manual_mcp(
                server_id="unknown_cwd",
                executable=sys.executable,
                argv=[],
                env={},
                env_names=[],
                cwd="~definitely-not-a-user",
                permission_profile="workspace_read",
                startup_timeout_seconds=5,
                tool_timeout_seconds=10,
            )

    def test_manual_mcp_config_can_update_and_remove(self) -> None:
        cwd = Path(self.temporary.name) / "manual-cwd"
        cwd.mkdir()
        self.catalog.create_manual_mcp(
            server_id="local",
            executable=sys.executable,
            argv=["-c", "before"],
            env={"MCP_SECRET": "secret-value"},
            env_names=["PATH"],
            cwd=str(cwd),
            permission_profile="workspace_read",
            startup_timeout_seconds=5,
            tool_timeout_seconds=10,
        )
        self.catalog.set_mcp_enabled("manual", "local", True)

        updated = self.catalog.update_manual_mcp(
            server_id="local",
            executable=sys.executable,
            argv=["-c", "after"],
            env={"NEW_SECRET": "new-value"},
            env_names=["PATH"],
            cwd=str(cwd),
            permission_profile="connector",
            startup_timeout_seconds=6,
            tool_timeout_seconds=12,
        )
        self.assertEqual(updated["argv"], ["-c", "after"])
        self.assertEqual(updated["permissionProfile"], "connector")
        self.assertFalse(updated["consented"])
        self.assertEqual(
            self.catalog.manual_mcp_server_config("local")["env"],
            {"MCP_SECRET": "secret-value", "NEW_SECRET": "new-value"},
        )

        self.assertTrue(self.catalog.remove_manual_mcp("local"))
        self.assertEqual(self.store.list_manual_mcp_servers(), [])
        self.assertIsNone(self.store.manual_mcp_server("local"))
        self.assertIsNone(
            self.store.connection.execute(
                "SELECT 1 FROM mcp_server_states WHERE plugin_id = 'manual' AND server_id = 'local'"
            ).fetchone()
        )


if __name__ == "__main__":
    unittest.main()

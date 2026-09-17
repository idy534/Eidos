from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eidos_runtime.db.storage import SessionStore  # noqa: E402
from eidos_runtime.extensions.plugins import PluginCatalog  # noqa: E402
from eidos_runtime.extensions.skill_management import SkillManagement  # noqa: E402
from eidos_runtime.extensions.skills import (  # noqa: E402
    SkillCatalog,
    SkillReadError,
    deploy_system_skills,
)


class SkillManagementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-skill-management-")
        root = Path(self.temporary.name)
        self.data = root / "data"
        self.source = root / "plugin"
        self.workspace = root / "workspace"
        self.data.mkdir(mode=0o700)
        self.workspace.mkdir()
        self.store = SessionStore(self.data)
        self.store.initialize()
        self.plugins = PluginCatalog(self.store)
        self._write_plugin()
        self.plugins.import_directory(self.source)
        self.plugins.set_enabled("demo", True)
        deploy_system_skills(self.data)
        self.manager = SkillManagement(SkillCatalog(self.plugins))

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _write_plugin(self) -> None:
        for name, description, body in (
            ("review", "Review files.", "Review body.\n"),
            ("write", "Write files.", "Write body.\n"),
        ):
            skill_root = self.source / "skills" / name
            skill_root.mkdir(parents=True)
            (skill_root / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: {description}\n---\n{body}",
                encoding="utf-8",
            )
        (self.source / "plugin.json").write_text(json.dumps({
            "schemaVersion": 1,
            "id": "demo",
            "name": "Demo",
            "version": "1.0.0",
            "description": "Fixture",
            "skills": [{"root": "skills/review"}, {"root": "skills/write"}],
            "mcpServers": [],
        }), encoding="utf-8")
        user_root = self.data / "skills" / "personal"
        user_root.mkdir(mode=0o700, parents=True)
        (user_root / "SKILL.md").write_text(
            "---\nname: personal\ndescription: Personal skill.\n---\nPersonal body.\n",
            encoding="utf-8",
        )

    def _manager_after_restart(self) -> SkillManagement:
        self.store.close()
        self.store = SessionStore(self.data)
        self.store.initialize()
        self.plugins = PluginCatalog(self.store)
        return SkillManagement(SkillCatalog(self.plugins))

    def test_lists_sources_and_plugin_availability(self) -> None:
        skills = {skill.qualified_id: skill for skill in self.manager.list()}

        self.assertEqual(skills["user:personal"].source_kind, "user")
        self.assertTrue(skills["user:personal"].enabled)
        self.assertEqual(skills["demo:review"].source_kind, "plugin")
        self.assertTrue(skills["demo:review"].available)
        self.assertIn("system:skill-creator", skills)

        self.plugins.set_enabled("demo", False)
        unavailable = {
            skill.qualified_id: skill for skill in self.manager.list()
        }["demo:review"]
        self.assertFalse(unavailable.available)

    def test_detail_strips_frontmatter_but_keeps_original_markdown(self) -> None:
        detail = self.manager.detail("user:personal")

        self.assertIn("name: personal", detail.content)
        self.assertEqual(detail.body, "Personal body.\n")
        self.assertEqual(
            detail.directory,
            str((self.data / "skills" / "personal").resolve()),
        )

    def test_toggle_persists_and_only_new_snapshots_exclude_skill(self) -> None:
        before = self.manager.catalog.extension_snapshot()
        self.assertIn(
            "user:personal",
            {entry["qualifiedId"] for entry in self.manager.catalog.catalog(before)},
        )

        updated = self.manager.set_enabled("user:personal", False)
        self.assertFalse(updated.enabled)
        after = self.manager.catalog.extension_snapshot()
        self.assertIn("user:personal", after["excludedSkillIds"])
        self.assertNotIn(
            "user:personal",
            {entry["qualifiedId"] for entry in self.manager.catalog.catalog(after)},
        )
        self.assertIn(
            "user:personal",
            {entry["qualifiedId"] for entry in self.manager.catalog.catalog(before)},
        )

        restarted = self._manager_after_restart()
        persisted = {
            skill.qualified_id: skill for skill in restarted.list()
        }["user:personal"]
        self.assertFalse(persisted.enabled)

    def test_plugin_remove_tombstones_one_skill_without_deleting_plugin_files(self) -> None:
        result = self.manager.remove("demo:review")

        self.assertFalse(result.cleanup_pending)
        listed = {skill.qualified_id for skill in self.manager.list()}
        self.assertNotIn("demo:review", listed)
        self.assertIn("demo:write", listed)
        plugin_root = self.plugins.installed_root("demo") / "skills"
        self.assertTrue((plugin_root / "review" / "SKILL.md").is_file())
        self.assertTrue((plugin_root / "write" / "SKILL.md").is_file())

    def test_system_remove_is_rejected(self) -> None:
        with self.assertRaisesRegex(SkillReadError, "system_skill_protected"):
            self.manager.remove("system:skill-creator")

    def test_user_remove_deletes_directory_when_no_run_is_active(self) -> None:
        result = self.manager.remove("user:personal")

        self.assertFalse(result.cleanup_pending)
        self.assertFalse((self.data / "skills" / "personal").exists())
        self.assertNotIn(
            "user:personal",
            {skill.qualified_id for skill in self.manager.list()},
        )

    def test_user_remove_waits_for_active_run_and_retries_after_completion(self) -> None:
        session = self.store.create_session(str(self.workspace))
        run, _ = self.store.create_run(session["id"], "Use the skill")

        result = self.manager.remove("user:personal")
        self.assertTrue(result.cleanup_pending)
        self.assertTrue((self.data / "skills" / "personal").exists())

        connection = self.store.connection
        assert connection is not None
        connection.execute(
            "UPDATE runs SET status = 'succeeded', completed_at = 1 WHERE id = ?",
            (run["id"],),
        )
        self.manager.cleanup()

        self.assertFalse((self.data / "skills" / "personal").exists())
        state = next(
            state for state in self.store.skill_states()
            if state.qualified_id == "user:personal"
        )
        self.assertFalse(state.cleanup_pending)


if __name__ == "__main__":
    unittest.main()

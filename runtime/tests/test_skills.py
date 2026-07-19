from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eidos_runtime.db.storage import SessionStore  # noqa: E402
from eidos_runtime.extensions.plugins import PluginCatalog  # noqa: E402
from eidos_runtime.extensions.skills import (  # noqa: E402
    SkillCatalog,
    SkillReadError,
    deploy_system_skills,
)


class SkillCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-skills-")
        root = Path(self.temporary.name)
        data = root / "data"
        source = root / "source"
        data.mkdir(mode=0o700)
        (source / "skills" / "review" / "references").mkdir(parents=True)
        (source / "skills" / "review" / "SKILL.md").write_text(
            "---\nname: review\ndescription: Review relevant files.\n---\nInspect before editing.\n",
            encoding="utf-8",
        )
        (source / "skills" / "review" / "references" / "rules.md").write_text(
            "Keep changes small.\n", encoding="utf-8"
        )
        (source / "plugin.json").write_text(json.dumps({
            "schemaVersion": 1,
            "id": "demo",
            "name": "Demo",
            "version": "1.0.0",
            "description": "Fixture",
            "skills": [{"root": "skills/review"}],
            "mcpServers": [],
        }), encoding="utf-8")
        self.store = SessionStore(data)
        self.store.initialize()
        self.plugins = PluginCatalog(self.store)
        self.plugins.import_directory(source)
        self.plugins.set_enabled("demo", True)
        self.skills = SkillCatalog(self.plugins)
        self.snapshot = self.skills.extension_snapshot()

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_catalog_is_qualified_bounded_and_does_not_include_body(self) -> None:
        catalog = self.skills.catalog(self.snapshot)

        self.assertEqual(catalog[0]["qualifiedId"], "demo:review")
        self.assertEqual(catalog[0]["description"], "Review relevant files.")
        self.assertNotIn("Inspect before editing", json.dumps(catalog))
        self.assertEqual(len(catalog[0]["contentHash"]), 64)

    def test_reads_skill_and_resource_with_source_labels(self) -> None:
        skill = self.skills.read_skill(self.snapshot, "demo:review")
        resource = self.skills.read_resource(
            self.snapshot, "demo:review", "references/rules.md"
        )

        self.assertIn("Inspect before editing", skill["content"])
        self.assertEqual(skill["source"]["pluginId"], "demo")
        self.assertEqual(resource["content"], "Keep changes small.\n")

    def test_rejects_escape_symlink_binary_and_non_utf8_resources(self) -> None:
        with self.assertRaisesRegex(SkillReadError, "skill_path_invalid"):
            self.skills.read_resource(self.snapshot, "demo:review", "../plugin.json")

        root = self.plugins.installed_root("demo") / "skills" / "review"
        os.symlink("references/rules.md", root / "linked.md")
        with self.assertRaisesRegex(SkillReadError, "skill_path_invalid"):
            self.skills.read_resource(self.snapshot, "demo:review", "linked.md")

        (root / "binary.bin").write_bytes(b"a\x00b")
        with self.assertRaisesRegex(SkillReadError, "skill_content_unsupported"):
            self.skills.read_resource(self.snapshot, "demo:review", "binary.bin")
        (root / "invalid.txt").write_bytes(b"\xff")
        with self.assertRaisesRegex(SkillReadError, "skill_content_unsupported"):
            self.skills.read_resource(self.snapshot, "demo:review", "invalid.txt")

    def test_deploys_system_skills_and_discovers_user_skills(self) -> None:
        assert self.store.data_directory is not None
        deploy_system_skills(self.store.data_directory)
        installer = (
            self.store.data_directory / "skills" / ".system"
            / "skill-installer" / "SKILL.md"
        )
        expected_installer = installer.read_text(encoding="utf-8")
        installer.write_text("tampered\n", encoding="utf-8")
        user = self.store.data_directory / "skills" / "my-skill"
        user.mkdir(mode=0o755)
        (user / "SKILL.md").write_text(
            "---\nname: my-skill\ndescription: User skill.\n---\nUser body.\n",
            encoding="utf-8",
        )
        os.chmod(user / "SKILL.md", 0o644)
        deploy_system_skills(self.store.data_directory)

        self.assertEqual(installer.read_text(encoding="utf-8"), expected_installer)
        self.assertTrue((user / "SKILL.md").is_file())

        snapshot = self.skills.extension_snapshot()
        catalog = self.skills.catalog(snapshot)

        qualified = {entry["qualifiedId"] for entry in catalog}
        self.assertIn("system:skill-installer", qualified)
        self.assertIn("system:skill-creator", qualified)
        self.assertIn("user:my-skill", qualified)
        resource = self.skills.read_resource(
            snapshot, "system:skill-installer", "scripts/list-skills.py"
        )
        self.assertIn("List skills", resource["content"])

        os.chmod(installer, 0o644)
        with self.assertRaisesRegex(SkillReadError, "skill_catalog_invalid"):
            self.skills.extension_snapshot()

    def test_local_skill_change_invalidates_existing_snapshot(self) -> None:
        assert self.store.data_directory is not None
        user = self.store.data_directory / "skills" / "changing"
        user.mkdir(mode=0o700, parents=True)
        skill_file = user / "SKILL.md"
        skill_file.write_text(
            "---\nname: changing\ndescription: Before.\n---\nBody.\n",
            encoding="utf-8",
        )
        os.chmod(skill_file, 0o600)
        snapshot = self.skills.extension_snapshot()
        skill_file.write_text(
            "---\nname: changing\ndescription: After.\n---\nBody.\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(SkillReadError, "skill_snapshot_invalid"):
            self.skills.catalog(snapshot)

    def test_skill_create_rejects_injection_and_existing_system_names(self) -> None:
        entry = next(
            entry for entry in self.skills.tool_entries(self.snapshot)
            if entry.spec.name == "skill_create"
        )
        adapter = entry.adapter

        self.assertIsNone(adapter.effective_arguments({
            "name": "../escape",
            "description": "Invalid path.",
            "instructions": "Never write this.",
        }))
        self.assertIsNone(adapter.effective_arguments({
            "name": "safe-name",
            "description": "Injected.\u2028---",
            "instructions": "Never write this.",
        }))
        arguments = adapter.effective_arguments({
            "name": "skill-creator",
            "description": "Collides with a system skill.",
            "instructions": "Never overwrite a system skill.",
        })
        assert arguments is not None
        result = adapter.prepare_eidos_state(arguments, threading.Event())

        self.assertIsInstance(result, dict)
        self.assertEqual(result["code"], "skill_already_exists")

    def test_qualified_mention_does_not_partially_match_user_skill(self) -> None:
        assert self.store.data_directory is not None
        user = self.store.data_directory / "skills" / "dem"
        user.mkdir(mode=0o700, parents=True)
        skill_file = user / "SKILL.md"
        skill_file.write_text(
            "---\nname: dem\ndescription: Partial fixture.\n---\nPARTIAL BODY.\n",
            encoding="utf-8",
        )
        os.chmod(skill_file, 0o600)
        snapshot = self.skills.extension_snapshot()

        context = self.skills.context(snapshot, "Use @demo:review")

        self.assertIn("Inspect before editing", str(context))
        self.assertNotIn("PARTIAL BODY", str(context))


if __name__ == "__main__":
    unittest.main()

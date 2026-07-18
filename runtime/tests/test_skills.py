from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eidos_runtime.db.storage import SessionStore  # noqa: E402
from eidos_runtime.extensions.plugins import PluginCatalog  # noqa: E402
from eidos_runtime.extensions.skills import SkillCatalog, SkillReadError  # noqa: E402


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
        self.snapshot = self.plugins.extension_snapshot()
        self.skills = SkillCatalog(self.plugins)

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


if __name__ == "__main__":
    unittest.main()

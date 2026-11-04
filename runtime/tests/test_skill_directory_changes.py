from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.db.storage import SessionStore  # noqa: E402
from eidos_runtime.extensions import skills as skills_module  # noqa: E402
from eidos_runtime.extensions.plugins import PluginCatalog  # noqa: E402
from eidos_runtime.extensions.skill_management import SkillManagement  # noqa: E402
from eidos_runtime.extensions.skills import SkillCatalog, SkillReadError  # noqa: E402


class SkillDirectoryChangeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-skill-dir-change-")
        self.data = Path(self.temporary.name) / "data"
        self.data.mkdir(mode=0o700)
        self.store = SessionStore(self.data)
        self.store.initialize()
        self.skill = self.data / "skills" / "deck"
        (self.skill / "scripts").mkdir(mode=0o700, parents=True)
        (self.skill / "SKILL.md").write_text(
            "---\nname: deck\ndescription: Build decks.\n---\nUse scripts/run.py.\n",
            encoding="utf-8",
        )
        (self.skill / "scripts" / "run.py").write_text("print('ok')\n", encoding="utf-8")
        for path in (self.skill / "SKILL.md", self.skill / "scripts" / "run.py"):
            os.chmod(path, 0o600)
        getattr(skills_module, "_TREE_HASH_CACHE", {}).clear()

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def _catalog(self) -> SkillCatalog:
        return SkillCatalog(PluginCatalog(self.store))

    def _run_snapshot(self):
        return self._catalog().extension_snapshot()

    def test_scripts_writing_inside_a_personal_skill_do_not_invalidate_the_run_snapshot(self) -> None:
        run_snapshot = self._run_snapshot()
        # A script run with the Skill directory as cwd leaves caches and outputs.
        (self.skill / "scripts" / "__pycache__").mkdir(mode=0o700)
        (self.skill / "scripts" / "__pycache__" / "run.cpython-312.pyc").write_bytes(b"\x00pyc")
        (self.skill / "output.txt").write_text("generated", encoding="utf-8")

        # A resumed Run or a steering message builds a fresh catalog from the saved snapshot.
        catalog = self._catalog()
        snapshot = catalog.catalog_snapshot(run_snapshot)

        self.assertIn("Use scripts/run.py", catalog.read_skill(snapshot, "user:deck")["content"])
        self.assertEqual(
            "print('ok')\n",
            catalog.read_resource(snapshot, "user:deck", "scripts/run.py")["content"],
        )
        self.assertEqual("generated", catalog.read_resource(snapshot, "user:deck", "output.txt")["content"])

    def test_editing_the_skill_document_still_invalidates_the_run_snapshot(self) -> None:
        run_snapshot = self._run_snapshot()
        (self.skill / "SKILL.md").write_text(
            "---\nname: deck\ndescription: Build decks.\n---\nChanged instructions.\n",
            encoding="utf-8",
        )

        catalog = self._catalog()
        snapshot = catalog.catalog_snapshot(run_snapshot)

        with self.assertRaises(SkillReadError):
            catalog.read_skill(snapshot, "user:deck")

    def test_detail_reads_only_the_skill_document(self) -> None:
        with patch.object(skills_module, "_read_tree", side_effect=AssertionError("tree read")):
            detail = SkillManagement(self._catalog()).detail("user:deck")

        self.assertIn("Use scripts/run.py", detail.body)

    def test_run_snapshots_reuse_tree_hash_until_files_change(self) -> None:
        first = self._run_snapshot()
        with patch.object(skills_module, "_read_tree", side_effect=AssertionError("tree read")):
            second = self._run_snapshot()
        self.assertEqual(first["skillCatalogHash"], second["skillCatalogHash"])

        (self.skill / "scripts" / "run.py").write_text("print('changed')\n", encoding="utf-8")
        self.assertNotEqual(first["skillCatalogHash"], self._run_snapshot()["skillCatalogHash"])

    def test_list_reuses_tree_hash_until_files_change(self) -> None:
        manager = SkillManagement(self._catalog())
        first = {skill.qualified_id: skill for skill in manager.list()}["user:deck"]

        with patch.object(skills_module, "_read_tree", side_effect=AssertionError("tree read")):
            second = {skill.qualified_id: skill for skill in manager.list()}["user:deck"]
        self.assertEqual(first.plugin_hash, second.plugin_hash)

        (self.skill / "scripts" / "run.py").write_text("print('changed')\n", encoding="utf-8")
        third = {skill.qualified_id: skill for skill in manager.list()}["user:deck"]
        self.assertNotEqual(first.plugin_hash, third.plugin_hash)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from eidos_runtime.db.storage import SessionStore  # noqa: E402
from eidos_runtime.extensions.plugins import PluginCatalog  # noqa: E402
from eidos_runtime.extensions.skills import (  # noqa: E402
    SkillCatalog,
    SkillReadError,
    _CodeloadRedirectHandler,
    _download_github_skill,
    _frontmatter,
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

    def test_runtime_installer_downloads_one_complete_public_github_skill(self) -> None:
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as bundle:
            unrelated_symlink = zipfile.ZipInfo("skills-main/AGENTS.md")
            unrelated_symlink.create_system = 3
            unrelated_symlink.external_attr = 0o120777 << 16
            bundle.writestr(unrelated_symlink, "../../AGENTS.md")
            bundle.writestr(
                "skills-main/skills/productivity/grilling/SKILL.md",
                "---\nname: grilling\ndescription: Grill a plan.\n---\nBody.\n",
            )
            bundle.writestr(
                "skills-main/skills/productivity/grilling/scripts/check.py",
                "print('ok')\n",
            )

        class Response:
            def __enter__(self):
                self.data = io.BytesIO(archive.getvalue())
                return self

            def __exit__(self, *_args):
                return False

            def geturl(self) -> str:
                return "https://codeload.github.com/mattpocock/skills/zip/main"

            def read(self, size: int) -> bytes:
                return self.data.read(size)

        with patch("urllib.request.OpenerDirector.open", return_value=Response()):
            name, files = _download_github_skill(
                "https://github.com/mattpocock/skills/tree/main/skills/productivity/grilling",
                threading.Event(),
            )

        self.assertEqual(name, "grilling")
        self.assertEqual(files["scripts/check.py"], b"print('ok')\n")

    def test_runtime_installer_ignores_unknown_frontmatter_fields(self) -> None:
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr(
                "skills-main/skills/productivity/grilling/SKILL.md",
                "---\n"
                "name: grilling\n"
                "description: Grill a plan.\n"
                "license: Complete terms in LICENSE.txt\n"
                "metadata:\n"
                "  author: Example\n"
                "  nested:\n"
                "    name: ignored\n"
                "    description: ignored\n"
                "---\n"
                "Body.\n",
            )

        class Response:
            def __enter__(self):
                self.data = io.BytesIO(archive.getvalue())
                return self

            def __exit__(self, *_args):
                return False

            def geturl(self) -> str:
                return "https://codeload.github.com/mattpocock/skills/zip/main"

            def read(self, size: int) -> bytes:
                return self.data.read(size)

        with patch("urllib.request.OpenerDirector.open", return_value=Response()):
            name, files = _download_github_skill(
                "https://github.com/mattpocock/skills/tree/main/skills/productivity/grilling",
                threading.Event(),
            )

        self.assertEqual(name, "grilling")
        self.assertIn(b"license: Complete terms", files["SKILL.md"])

    def test_catalog_ignores_unknown_frontmatter_fields(self) -> None:
        assert self.store.data_directory is not None
        user = self.store.data_directory / "skills" / "metadata-skill"
        user.mkdir(mode=0o700, parents=True)
        (user / "SKILL.md").write_text(
            "---\n"
            "name: metadata-skill\n"
            "description: Catalog-safe.\n"
            "license: Complete terms in LICENSE.txt\n"
            "metadata:\n"
            "  name: ignored\n"
            "  nested:\n"
            "    description: ignored\n"
            "---\n"
            "Body.\n",
            encoding="utf-8",
        )

        snapshot = self.skills.extension_snapshot()
        entry = next(
            item for item in self.skills.catalog(snapshot)
            if item["qualifiedId"] == "user:metadata-skill"
        )

        self.assertEqual(entry["name"], "metadata-skill")
        self.assertEqual(entry["description"], "Catalog-safe.")
        self.assertNotIn("license", entry)
        self.assertEqual(
            set(entry),
            {
                "schemaVersion",
                "qualifiedId",
                "name",
                "description",
                "pluginId",
                "pluginVersion",
                "pluginHash",
                "contentHash",
            },
        )

    def test_frontmatter_keeps_supported_field_validation_strict(self) -> None:
        invalid_documents = {
            "duplicate_name": (
                "---\nname: valid\nname: duplicate\n"
                "description: Description.\n---\nBody.\n"
            ),
            "invalid_name": (
                "---\nname: not valid\n"
                "description: Description.\n---\nBody.\n"
            ),
            "missing_description": (
                "---\nname: valid\nmetadata:\n"
                "  description: nested only\n---\nBody.\n"
            ),
        }

        for label, document in invalid_documents.items():
            with self.subTest(label=label):
                with self.assertRaisesRegex(
                    SkillReadError, "^skill_metadata_invalid$"
                ):
                    _frontmatter(document)

    def test_runtime_installer_rejects_non_github_url_before_network(self) -> None:
        with patch("urllib.request.OpenerDirector.open") as request:
            with self.assertRaisesRegex(SkillReadError, "skill_url_invalid"):
                _download_github_skill(
                    "https://example.com/mattpocock/skills/tree/main/skills/grilling",
                    threading.Event(),
                )
        request.assert_not_called()

    def test_runtime_installer_rejects_redirect_to_unapproved_host(self) -> None:
        with self.assertRaisesRegex(SkillReadError, "skill_download_redirected"):
            _CodeloadRedirectHandler().redirect_request(
                None, None, 302, "Found", {}, "https://example.com/archive.zip"
            )

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

        self.assertFalse(entry.validate_arguments({
            "name": "../escape",
            "description": "Invalid path.",
            "instructions": "Never write this.",
        }).valid)
        self.assertFalse(entry.validate_arguments({
            "name": "safe-name",
            "description": "Injected.\u2028---",
            "instructions": "Never write this.",
        }).valid)
        validation = entry.validate_arguments({
            "name": "skill-creator",
            "description": "Collides with a system skill.",
            "instructions": "Never overwrite a system skill.",
        })
        assert validation.normalized_arguments is not None
        result = adapter.prepare_eidos_state(
            validation.normalized_arguments, threading.Event()
        )

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
        selected = self.skills.select_explicit(
            snapshot, "turn-1", "Use @demo:review"
        )
        selected_context = self.skills.render_selected(snapshot, selected)

        self.assertNotIn("Inspect before editing", str(context))
        self.assertNotIn("PARTIAL BODY", str(context))
        self.assertEqual(len(selected_context), 1)
        self.assertIn("Inspect before editing", selected_context[0].content)
        self.assertNotIn("PARTIAL BODY", selected_context[0].content)


if __name__ == "__main__":
    unittest.main()

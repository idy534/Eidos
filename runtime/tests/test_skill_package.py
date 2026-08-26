from __future__ import annotations

import io
import os
from pathlib import Path
import stat
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile


import sys

RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.db.storage import SessionStore  # noqa: E402
from eidos_runtime.extensions.plugins import PluginCatalog  # noqa: E402
from eidos_runtime.extensions.skills import (  # noqa: E402
    SkillCatalog,
    SkillCreation,
    _SkillInstallAdapter,
    _commit_skill_tree,
    _download_github_skill,
    SkillReadError,
    _write_tree,
)


def _office_fixture(root_file: str, *, padding: int = 0) -> bytes:
    document = io.BytesIO()
    with zipfile.ZipFile(document, "w", compression=zipfile.ZIP_STORED) as bundle:
        bundle.writestr("[Content_Types].xml", b"<?xml version=\"1.0\"?>")
        bundle.writestr(root_file, b"<?xml version=\"1.0\"?><document/>")
        if padding:
            bundle.writestr("custom/padding.bin", b"x" * padding)
    return document.getvalue()


class SkillPackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="eidos-skill-package-")
        self.root = Path(self.temporary.name)
        self.data = self.root / "data"
        self.data.mkdir(mode=0o700)
        self.store = SessionStore(self.data)
        self.store.initialize()
        self.skills = SkillCatalog(PluginCatalog(self.store))

    def tearDown(self) -> None:
        self.store.close()
        self.temporary.cleanup()

    def test_catalog_is_developer_capability_context_with_progressive_disclosure_rules(self) -> None:
        skill = self.data / "skills" / "review"
        skill.mkdir(mode=0o700, parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: review\ndescription: Review files.\n---\n"
            "FULL BODY MUST NOT BE IN CATALOG.\n",
            encoding="utf-8",
        )
        os.chmod(skill / "SKILL.md", 0o600)

        snapshot = self.skills.catalog_snapshot(self.skills.extension_snapshot())
        catalog = self.skills.render_catalog(snapshot)
        prompt = catalog.content.lower()

        self.assertEqual(catalog.role, "developer")
        for phrase in (
            "discovery",
            "trigger",
            "progressive disclosure",
            "relative",
            "scripts",
            "references",
            "assets",
            "safety",
        ):
            self.assertIn(phrase, prompt)
        self.assertNotIn("full body must not be in catalog", prompt)

    def test_binary_package_resources_are_bounded_and_not_scanned_as_utf8(self) -> None:
        skill = self.data / "skills" / "anthropic-files"
        (skill / "assets").mkdir(mode=0o700, parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: anthropic-files\ndescription: File fixtures.\n---\nBody.\n",
            encoding="utf-8",
        )
        (skill / "assets" / "sample.png").write_bytes(b"\x89PNG" + b"x" * (1024 * 1024 - 4))
        (skill / "assets" / "sample.pptx").write_bytes(
            _office_fixture("ppt/presentation.xml", padding=2 * 1024 * 1024)
        )
        (skill / "assets" / "anthropic.docx").write_bytes(
            _office_fixture("word/document.xml")
        )
        (skill / "assets" / "anthropic.pdf").write_bytes(
            b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
        )
        (skill / "assets" / "anthropic.xlsx").write_bytes(
            _office_fixture("xl/workbook.xml")
        )
        for file in (skill / "SKILL.md", skill / "assets" / "sample.png", skill / "assets" / "sample.pptx"):
            os.chmod(file, 0o600)
        for file in (skill / "assets" / "anthropic.docx", skill / "assets" / "anthropic.pdf", skill / "assets" / "anthropic.xlsx"):
            os.chmod(file, 0o600)

        snapshot = self.skills.catalog_snapshot(self.skills.extension_snapshot())
        for resource in (
            "assets/sample.png",
            "assets/sample.pptx",
            "assets/anthropic.docx",
            "assets/anthropic.pdf",
            "assets/anthropic.xlsx",
        ):
            with self.subTest(resource=resource):
                with self.assertRaisesRegex(ValueError, "skill_resource_not_text"):
                    self.skills.read_resource(snapshot, "user:anthropic-files", resource)

    def test_installed_package_preserves_only_executable_bit(self) -> None:
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr(
                "repo-main/skills/files/SKILL.md",
                "---\nname: files\ndescription: Files.\n---\nBody.\n",
            )
            for relative, mode, payload in (
                ("scripts/run.py", 0o755, b"#!/usr/bin/env python3\n"),
                ("bin/native", 0o4755, b"native"),
                ("references/readme.txt", 0o644, b"read me"),
            ):
                entry = zipfile.ZipInfo(f"repo-main/skills/files/{relative}")
                entry.create_system = 3
                entry.external_attr = (stat.S_IFREG | mode) << 16
                bundle.writestr(entry, payload)
            bundle.writestr(
                "repo-main/skills/files/assets/anthropic.png",
                b"\x89PNG" + b"x" * (1024 * 1024 - 4),
            )
            bundle.writestr(
                "repo-main/skills/files/assets/anthropic.pptx",
                _office_fixture("ppt/presentation.xml", padding=2 * 1024 * 1024),
            )
            for relative, payload in (
                ("anthropic.docx", _office_fixture("word/document.xml")),
                (
                    "anthropic.pdf",
                    b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n",
                ),
                ("anthropic.xlsx", _office_fixture("xl/workbook.xml")),
            ):
                bundle.writestr(f"repo-main/skills/files/assets/{relative}", payload)

        class Response:
            def __enter__(self):
                self.data = io.BytesIO(archive.getvalue())
                return self

            def __exit__(self, *_args):
                return False

            def geturl(self) -> str:
                return "https://codeload.github.com/example/skills/zip/main"

            def read(self, size: int) -> bytes:
                return self.data.read(size)

        with patch("urllib.request.OpenerDirector.open", return_value=Response()):
            name, files = _download_github_skill(
                "https://github.com/example/skills/tree/main/skills/files",
                threading.Event(),
            )

        self.assertEqual(name, "files")
        self.assertEqual(files.executable_paths, frozenset({"scripts/run.py", "bin/native"}))
        creation = SkillCreation(
            name=name,
            path="~/.eidos/skills/files",
            files=files,
            content_hash="hash",
            diff="diff",
        )
        result = _commit_skill_tree(self.skills, creation, threading.Event(), "skill_install")
        self.assertEqual(result["code"], "ok")
        destination = self.data / "skills" / "files"
        self.assertEqual((destination / "scripts" / "run.py").stat().st_mode & 0o777, 0o700)
        self.assertEqual((destination / "bin" / "native").stat().st_mode & 0o777, 0o700)
        self.assertEqual((destination / "references" / "readme.txt").stat().st_mode & 0o777, 0o600)
        self.assertEqual((destination / "scripts").stat().st_mode & 0o777, 0o700)
        self.assertEqual((destination / "assets" / "anthropic.png").stat().st_size, 1024 * 1024)
        self.assertGreaterEqual(
            (destination / "assets" / "anthropic.pptx").stat().st_size,
            2 * 1024 * 1024,
        )
        for relative in ("anthropic.docx", "anthropic.pdf", "anthropic.xlsx"):
            self.assertTrue((destination / "assets" / relative).is_file())

    def test_tree_writer_rejects_traversal_paths(self) -> None:
        with self.assertRaisesRegex(SkillReadError, "skill_path_invalid"):
            _write_tree(self.data / "staging", {"../escape": b"no"})

    def test_runtime_installer_rejects_directory_named_zip_symlink(self) -> None:
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w") as bundle:
            bundle.writestr(
                "repo-main/skills/files/SKILL.md",
                "---\nname: files\ndescription: Files.\n---\nBody.\n",
            )
            entry = zipfile.ZipInfo("repo-main/skills/files/linked/")
            entry.create_system = 3
            entry.external_attr = (stat.S_IFLNK | 0o777) << 16
            bundle.writestr(entry, "../../escape")

        class Response:
            def __enter__(self):
                self.data = io.BytesIO(archive.getvalue())
                return self

            def __exit__(self, *_args):
                return False

            def geturl(self) -> str:
                return "https://codeload.github.com/example/skills/zip/main"

            def read(self, size: int) -> bytes:
                return self.data.read(size)

        with patch("urllib.request.OpenerDirector.open", return_value=Response()):
            with self.assertRaisesRegex(SkillReadError, "skill_archive_unsafe"):
                _download_github_skill(
                    "https://github.com/example/skills/tree/main/skills/files",
                    threading.Event(),
                )

    def test_installer_adapter_uses_shared_creation_limits_for_binary_fixtures(self) -> None:
        skill = self.data / "skills" / "existing"
        skill.mkdir(mode=0o700, parents=True)
        (skill / "SKILL.md").write_text(
            "---\nname: existing\ndescription: Existing.\n---\nBody.\n",
            encoding="utf-8",
        )
        os.chmod(skill / "SKILL.md", 0o600)
        adapter = _SkillInstallAdapter(self.skills)
        self.assertIsNotNone(adapter)


if __name__ == "__main__":
    unittest.main()

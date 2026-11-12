from __future__ import annotations

import io
import json
import os
from pathlib import Path
import shutil
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
from eidos_runtime.extensions.skill_access import SkillAccess  # noqa: E402
from eidos_runtime.extensions.skills import (  # noqa: E402
    SelectedSkillSet,
    SkillCatalog,
    SkillCreation,
    _SkillInstallAdapter,
    _commit_skill_tree,
    _download_github_skill,
    SkillReadError,
    _SkillReadAdapter,
    _SkillResourceAdapter,
    _write_tree,
)
from eidos_runtime.model.prompts import SKILLS_INSTRUCTIONS  # noqa: E402
from eidos_runtime.file_limits import MAX_SKILL_FILE_BYTES  # noqa: E402
from eidos_runtime.tools.contracts import (  # noqa: E402
    SKILL_RESOURCE_METADATA_BYTES,
    SKILL_RESOURCE_MAX_ENTRIES,
    SkillReadResultData,
    SkillResourceResultData,
    result_model,
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

    def resource_skill(self, files: dict[str, bytes], body: str = "Read references/api.md.\n"):
        root = self.data / "skills" / "resource-example"
        root.mkdir(mode=0o700, parents=True)
        (root / "SKILL.md").write_text(
            f"---\nname: resource-example\ndescription: Read example resources.\n---\n{body}"
        )
        for relative, content in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        snapshot = self.skills.catalog_snapshot(self.skills.extension_snapshot())
        access = SkillAccess.from_snapshot(snapshot)
        return root, snapshot, access

    def test_skill_read_exposes_the_activated_root_and_typed_resource_inventory(self) -> None:
        root, snapshot, access = self.resource_skill({
            "references/api.md": "Read 中文.".encode(),
            "references/long.md": ("a" * 4095 + "中").encode(),
            "scripts/run.py": b"print('ready')\n",
            "assets/image.png": b"\x89PNG\r\n\x1a\n",
        })
        result = _SkillReadAdapter(self.skills, snapshot, access.activate_model_read).execute(
            {"qualifiedId": "user:resource-example"}, threading.Event()
        )
        result_model(SkillReadResultData).model_validate(result)
        data = result["data"]
        self.assertEqual(data["skillRoot"], str(root.resolve()))
        self.assertEqual(data["skillRoot"], str(access.activate_model_read("user:resource-example").canonical_root))
        entries = {entry["resourcePath"]: entry for entry in data["resources"]}
        self.assertEqual(set(entries), {"references/api.md", "references/long.md", "scripts/run.py", "assets/image.png"})
        self.assertEqual(entries["references/api.md"]["sizeBytes"], len("Read 中文.".encode()))
        self.assertEqual(entries["references/long.md"]["type"], "text")
        self.assertEqual(entries["scripts/run.py"]["type"], "script")
        self.assertEqual(entries["assets/image.png"]["type"], "binary")
        self.assertFalse(data["resourcesTruncated"])

    def test_resource_inventory_obeys_entry_and_serialized_metadata_limits(self) -> None:
        for long_names in (False, True):
            with self.subTest(long_names=long_names):
                files = {
                    "references/" + ("长" * 60 if long_names else "file") + f"{index:03}.md": b"text"
                    for index in range(SKILL_RESOURCE_MAX_ENTRIES + 5)
                }
                if long_names:
                    shutil.rmtree(self.data / "skills" / "resource-example")
                _root, snapshot, _access = self.resource_skill(files)
                data = self.skills.read_skill(snapshot, "user:resource-example", include_resources=True)
                metadata = {key: data[key] for key in ("skillRoot", "resources", "resourcesTruncated")}
                self.assertTrue(metadata["resourcesTruncated"])
                self.assertGreater(len(metadata["resources"]), 0)
                self.assertLessEqual(len(metadata["resources"]), SKILL_RESOURCE_MAX_ENTRIES)
                self.assertLessEqual(len(json.dumps(metadata, ensure_ascii=False, separators=(",", ":")).encode()), SKILL_RESOURCE_METADATA_BYTES)
                if long_names:
                    self.assertLess(len(metadata["resources"]), SKILL_RESOURCE_MAX_ENTRIES)
        with patch("eidos_runtime.extensions.skills.MAX_SKILL_FILES", 2):
            limited = self.skills.read_skill(snapshot, "user:resource-example", include_resources=True)
        self.assertTrue(limited["resourcesTruncated"])
        self.assertLessEqual(len(limited["resources"]), 1)

    def test_resource_errors_and_directory_results_pass_the_result_contract(self) -> None:
        root, snapshot, access = self.resource_skill({
            "references/api.md": b"Useful reference.",
            "references/nested/details.md": b"Details.",
            "assets/image.png": b"\x89PNG\r\n\x1a\n",
            "assets/invalid.txt": b"\xff\xfe",
        })
        oversized = root / "assets" / "large.txt"
        with oversized.open("wb") as output:
            output.truncate(MAX_SKILL_FILE_BYTES + 1)
        adapter = _SkillResourceAdapter(self.skills, snapshot, access.activate_model_read)

        def read(path):
            result = adapter.execute({"qualifiedId": "user:resource-example", "resourcePath": path}, threading.Event())
            result_model(SkillResourceResultData).model_validate(result)
            return result

        missing = read("references/ap.md")
        self.assertEqual(missing["code"], "skill_resource_not_found")
        self.assertIn("references/api.md", missing["data"]["candidates"])
        self.assertLessEqual(len(missing["data"]["candidates"]), 5)
        self.assertEqual(read("missing/file.md")["code"], "skill_resource_not_found")
        for path in ("assets/image.png", "assets/invalid.txt"):
            binary = read(path)
            self.assertEqual(binary["code"], "skill_resource_not_text")
            self.assertEqual(binary["data"]["absolutePath"], str(root.resolve() / path))
            self.assertIn("view_image", binary["summary"])
            self.assertIn("run_shell", binary["summary"])
        large = read("assets/large.txt")
        self.assertEqual(large["code"], "skill_resource_too_large")
        self.assertEqual(large["data"]["sizeBytes"], MAX_SKILL_FILE_BYTES + 1)
        self.assertEqual(large["data"]["maxBytes"], MAX_SKILL_FILE_BYTES)
        listing = read("references")
        self.assertEqual(listing["outcome"], "success")
        self.assertEqual(listing["data"]["resourceType"], "directory")
        self.assertNotIn("content", listing["data"])
        self.assertEqual(
            {entry["resourcePath"]: entry["type"] for entry in listing["data"]["resources"]},
            {"references/api.md": "text", "references/nested": "directory"},
        )
        self.assertEqual(read(".")["data"]["resourceType"], "directory")

    def test_resource_listing_never_follows_links_or_exposes_special_files(self) -> None:
        root, snapshot, _access = self.resource_skill({"references/api.md": b"Reference."})
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "secret.md").write_text("outside")
        (root / "linked").symlink_to(outside, target_is_directory=True)
        os.mkfifo(root / "pipe")
        listing = self.skills.read_resource(snapshot, "user:resource-example", ".")
        paths = {entry["resourcePath"] for entry in listing["resources"]}
        self.assertNotIn("linked", paths)
        self.assertNotIn("pipe", paths)
        with self.assertRaisesRegex(SkillReadError, "skill_path_invalid"):
            self.skills.read_resource(snapshot, "user:resource-example", "linked/secret.md")

    def test_resource_inventory_withholds_replaced_directories_without_blocking_instructions(self) -> None:
        root, snapshot, access = self.resource_skill({"references/api.md": b"Reference."})
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "secret.md").write_text("outside")
        original_open = os.open

        def replace_directory(path, flags, *args, **kwargs):
            if path == "references" and kwargs.get("dir_fd") is not None:
                (root / "references").rename(root / "previous-references")
                (root / "references").symlink_to(outside, target_is_directory=True)
            return original_open(path, flags, *args, **kwargs)

        with patch("eidos_runtime.extensions.skills.os.open", side_effect=replace_directory):
            skill = self.skills.read_skill(snapshot, "user:resource-example", include_resources=True)
        # The link was never followed and the instructions are still readable.
        self.assertIn("Read references/api.md.", skill["content"])
        self.assertEqual(skill["resources"], [])
        self.assertTrue(skill["resourcesTruncated"])
        self.assertEqual(skill["skillRoot"], str(root.resolve()))
        self.assertNotIn("secret.md", json.dumps(skill))
        # Explicit requests still fail closed: the replaced link is neither
        # listed from the root nor readable as a directory.
        root_listing = self.skills.read_resource(snapshot, "user:resource-example", ".")
        self.assertNotIn("references", {entry["resourcePath"] for entry in root_listing["resources"]})
        with self.assertRaisesRegex(SkillReadError, "skill_path_invalid"):
            self.skills.read_resource(snapshot, "user:resource-example", "references")

    def test_skill_read_without_resources_has_no_inventory(self) -> None:
        _root, snapshot, _access = self.resource_skill({"references/api.md": b"Reference."})
        skill = self.skills.read_skill(snapshot, "user:resource-example")
        self.assertEqual(set(skill), {"qualifiedId", "content", "contentHash", "source"})

    def test_inventory_lists_scripts_and_references_before_assets_and_skips_generated_files(self) -> None:
        files = {f"assets/image{index:03}.png": b"\x89PNG\r\n\x1a\n" for index in range(60)}
        files.update({
            "agents/eidos.yaml": b"interface: {}\n",
            "scripts/run.py": b"print('ready')\n",
            "scripts/__pycache__/run.cpython-312.pyc": b"\x00\x01",
            "scripts/helpers/util.py": b"VALUE = 1\n",
            "references/api.md": b"API\n",
            "reference.md": b"Top level\n",
            "node_modules/pkg/index.js": b"module.exports = 1\n",
            ".git/config": b"[core]\n",
            ".DS_Store": b"\x00",
        })
        _root, snapshot, _access = self.resource_skill(files)
        skill = self.skills.read_skill(snapshot, "user:resource-example", include_resources=True)
        paths =[entry["resourcePath"] for entry in skill["resources"]]
        self.assertTrue(skill["resourcesTruncated"])
        self.assertEqual(len(paths), SKILL_RESOURCE_MAX_ENTRIES)
        self.assertEqual(
            paths[:4],
            ["reference.md", "scripts/run.py", "scripts/helpers/util.py", "references/api.md"],
        )
        # Static assets fill the remaining slots; agent metadata sorts last.
        self.assertTrue(all(path.startswith("assets/") for path in paths[4:]))
        self.assertNotIn("agents/eidos.yaml", paths)
        for noise in ("__pycache__", "node_modules", ".git", ".DS_Store", ".pyc"):
            self.assertFalse(any(noise in path for path in paths), noise)
        with self.assertRaisesRegex(SkillReadError, "skill_resource_not_found"):
            self.skills.read_resource(snapshot, "user:resource-example", "scripts/missing.py")

    @unittest.skipIf(os.getuid() == 0, "directory permissions do not apply to root")
    def test_unreadable_subdirectory_does_not_block_skill_read_or_selection(self) -> None:
        root, snapshot, access = self.resource_skill({
            "references/api.md": b"Reference.", "private/notes.md": b"Notes.",
        })
        (root / "private").chmod(0)
        try:
            skill = self.skills.read_skill(snapshot, "user:resource-example", include_resources=True)
            self.assertIn("Read references/api.md.", skill["content"])
            self.assertEqual(skill["skillRoot"], str(root.resolve()))
            self.assertEqual(skill["resources"], [])
            self.assertTrue(skill["resourcesTruncated"])
            result = _SkillReadAdapter(self.skills, snapshot, access.activate_model_read).execute(
                {"qualifiedId": "user:resource-example"}, threading.Event()
            )
            self.assertEqual(result["outcome"], "success")
            result_model(SkillReadResultData).model_validate(result)
            selected = self.skills.render_selected(
                snapshot,
                SelectedSkillSet(turn_id="turn", selected_qualified_ids=("user:resource-example",)),
            )
            self.assertIn("Read references/api.md.", selected[0].content)
            self.assertIn(str(root.resolve()), selected[0].content)
            # Asking for the unreadable directory explicitly still fails closed.
            with self.assertRaisesRegex(SkillReadError, "skill_path_invalid"):
                self.skills.read_resource(snapshot, "user:resource-example", "private")
        finally:
            (root / "private").chmod(0o700)

    def test_resource_inventory_is_returned_on_the_first_page_only(self) -> None:
        root, snapshot, access = self.resource_skill(
            {"references/api.md": b"Reference."}, body="x" * 30_000,
        )
        adapter = _SkillReadAdapter(self.skills, snapshot, access.activate_model_read)
        first = adapter.execute({"qualifiedId": "user:resource-example"}, threading.Event())
        self.assertEqual(first["outcome"], "success")
        self.assertEqual(first["data"]["skillRoot"], str(root.resolve()))
        self.assertTrue(first["data"]["resources"])
        second = adapter.execute(
            {"qualifiedId": "user:resource-example", "offset": first["data"]["nextOffset"]},
            threading.Event(),
        )
        self.assertEqual(second["outcome"], "success")
        result_model(SkillReadResultData).model_validate(second)
        for key in ("skillRoot", "resources", "resourcesTruncated"):
            self.assertNotIn(key, second["data"])
        self.assertEqual(second["data"]["startByte"], first["data"]["endByte"])

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
        self.assertIn("skill_read", prompt)
        self.assertNotIn("full body must not be in catalog", prompt)
        self.assertIn(
            '  <skill name="user:review">\n'
            "    <description>Review files.</description>\n"
            "  </skill>",
            catalog.content,
        )
        self.assertIn("FULL BODY", self.skills.read_skill(snapshot, "user:review")["content"])
        for field in ("sourceHash", "contentHash", "sourceVersion", '"source"', "SKILL.md"):
            self.assertNotIn(field, catalog.content)
        # Progressive disclosure rules live in the system prompt, not the catalog.
        for phrase in ("skill_read", "skill_read_resource", "relative"):
            self.assertIn(phrase, SKILLS_INSTRUCTIONS)

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

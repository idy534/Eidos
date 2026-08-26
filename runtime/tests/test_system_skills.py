from __future__ import annotations

import importlib.util
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from contextlib import redirect_stderr
from unittest.mock import patch


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.db.storage import SessionStore  # noqa: E402
from eidos_runtime.extensions.plugins import PluginCatalog  # noqa: E402
from eidos_runtime.extensions.skills import SkillCatalog  # noqa: E402


SYSTEM = (
    RUNTIME_ROOT
    / "eidos_runtime" / "resources" / "skills" / ".system"
)


class SystemSkillScriptTests(unittest.TestCase):
    def test_installer_listing_reports_network_failure_without_traceback(self) -> None:
        script = SYSTEM / "skill-installer" / "scripts" / "list-skills.py"
        scripts = str(script.parent)
        sys.path.insert(0, scripts)
        try:
            spec = importlib.util.spec_from_file_location("eidos_skill_list", script)
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        finally:
            sys.path.remove(scripts)
        stderr = io.StringIO()
        with patch.object(
            module, "_request", side_effect=urllib.error.URLError("offline")
        ), redirect_stderr(stderr):
            result = module.main([])
        self.assertEqual(result, 1)
        self.assertIn("Error:", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_skill_creator_initializes_private_valid_user_skill(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-skill-create-") as directory:
            data = Path(directory) / "data"
            script = SYSTEM / "skill-creator" / "scripts" / "init_skill.py"
            validator = SYSTEM / "skill-creator" / "scripts" / "quick_validate.py"
            environment = {**os.environ, "EIDOS_DATA_DIR": str(data)}

            created = subprocess.run(
                [sys.executable, str(script), "My Skill", "--resources", "references"],
                env=environment, text=True, capture_output=True, check=False,
            )
            skill = data / "skills" / "my-skill"
            self.assertEqual(created.returncode, 0, created.stderr)
            self.assertEqual(skill.stat().st_mode & 0o777, 0o700)
            self.assertEqual((skill / "SKILL.md").stat().st_mode & 0o777, 0o600)
            content = (skill / "SKILL.md").read_text(encoding="utf-8")
            (skill / "SKILL.md").write_text(
                content.replace(
                    "TODO describe what this skill does and when Eidos should use it.",
                    "Create small test artifacts when requested.",
                ).replace(
                    "TODO add the smallest workflow and resource guidance needed for this skill.",
                    "Create the requested artifact and verify it.",
                ),
                encoding="utf-8",
            )
            valid = subprocess.run(
                [sys.executable, str(validator), str(skill)],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(valid.returncode, 0, valid.stdout + valid.stderr)
            os.chmod(data, 0o700)
            store = SessionStore(data)
            store.initialize()
            skills = SkillCatalog(PluginCatalog(store))
            self.assertIn(
                "user:my-skill",
                {entry["qualifiedId"] for entry in skills.catalog(skills.extension_snapshot())},
            )
            store.close()

    def test_plugin_creator_generates_only_eidos_v1_contract(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-plugin-create-") as directory:
            parent = Path(directory) / "plugins"
            creator = SYSTEM / "plugin-creator" / "scripts" / "create_basic_plugin.py"
            validator = SYSTEM / "plugin-creator" / "scripts" / "validate_plugin.py"
            created = subprocess.run(
                [sys.executable, str(creator), "Demo Plugin", "--path", str(parent), "--skill", "review"],
                text=True, capture_output=True, check=False,
            )
            plugin = parent / "demo-plugin"
            self.assertEqual(created.returncode, 0, created.stderr)
            self.assertTrue((plugin / "plugin.json").is_file())
            self.assertFalse((plugin / ".codex-plugin").exists())
            skill_file = plugin / "skills" / "review" / "SKILL.md"
            skill_file.write_text(
                skill_file.read_text(encoding="utf-8").replace(
                    "TODO describe when to use review.",
                    "Review code when requested.",
                ).replace("TODO add instructions.", "Inspect the complete diff."),
                encoding="utf-8",
            )
            valid = subprocess.run(
                [sys.executable, str(validator), str(plugin)],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(valid.returncode, 0, valid.stdout + valid.stderr)
            data = Path(directory) / "data"
            data.mkdir(mode=0o700)
            store = SessionStore(data)
            store.initialize()
            imported = PluginCatalog(store).import_directory(plugin)
            self.assertEqual(imported["id"], "demo-plugin")
            store.close()

    def test_installer_rejects_symlinks_and_atomically_installs_valid_skill(self) -> None:
        script = SYSTEM / "skill-installer" / "scripts" / "install-skill-from-github.py"
        scripts = str(script.parent)
        sys.path.insert(0, scripts)
        try:
            spec = importlib.util.spec_from_file_location("eidos_skill_installer", script)
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
        finally:
            sys.path.remove(scripts)
        with tempfile.TemporaryDirectory(prefix="eidos-skill-install-") as directory:
            root = Path(directory)
            source = root / "source" / "safe-skill"
            source.mkdir(parents=True)
            (source / "SKILL.md").write_text(
                "---\nname: safe-skill\ndescription: Safe fixture.\n---\nBody.\n",
                encoding="utf-8",
            )
            (source / "scripts").mkdir()
            (source / "scripts" / "run.py").write_text("#!/usr/bin/env python3\n", encoding="utf-8")
            os.chmod(source / "scripts" / "run.py", 0o755)
            (source / "bin").mkdir()
            (source / "bin" / "native").write_bytes(b"native")
            os.chmod(source / "bin" / "native", 0o4755)
            os.symlink("SKILL.md", source / "linked.md")
            with self.assertRaisesRegex(module.InstallError, "symbolic links"):
                module._read_skill(source)
            (source / "linked.md").unlink()
            original_document = (source / "SKILL.md").read_bytes()
            (source / "SKILL.md").write_bytes(
                b"---\nname: safe-skill\ndescription: Safe fixture.\n---\n"
                + b"x" * (128 * 1024)
            )
            with self.assertRaisesRegex(module.InstallError, "too large"):
                module._read_skill(source)
            (source / "SKILL.md").write_bytes(original_document)
            name, files = module._read_skill(source)
            destination_root = root / "data" / "skills"
            module._private_directory(destination_root)
            destination = destination_root / name
            module._install(destination, files)
            self.assertEqual((destination / "SKILL.md").stat().st_mode & 0o777, 0o600)
            self.assertEqual((destination / "scripts" / "run.py").stat().st_mode & 0o777, 0o700)
            self.assertEqual((destination / "bin" / "native").stat().st_mode & 0o777, 0o700)
            self.assertFalse(any(path.name.startswith(".install-") for path in destination_root.iterdir()))


if __name__ == "__main__":
    unittest.main()

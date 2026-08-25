from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.extensions.skill_manifest import (  # noqa: E402
    SkillManifestError,
    load_skill_agent_metadata,
    parse_skill_manifest,
)


class SkillManifestTests(unittest.TestCase):
    def test_parses_codex_frontmatter_features_and_repairs_limited_colons(self) -> None:
        manifest = parse_skill_manifest(
            "---\n"
            "name:  deploy  service\n"
            "description: |-\n"
            "  Build for AWS: ECS\n"
            "  and verify the result.\n"
            "metadata:\n"
            "  short-description: What's included: builds and tests\n"
            "license: Complete terms in LICENSE.txt\n"
            "compatibility: local\n"
            "allowed-tools: [read_file, search_text]\n"
            "argument-hint: <duration: e.g. 7d>\n"
            "tags: [next,@supabase/ssr]\n"
            "---\nBody.\n",
            "fallback",
        )

        self.assertEqual(manifest.name, "deploy service")
        self.assertEqual(
            manifest.description,
            "Build for AWS: ECS and verify the result.",
        )
        self.assertEqual(
            manifest.short_description,
            "What's included: builds and tests",
        )
        long_short = "x" * 1_025
        long_manifest = parse_skill_manifest(
            "---\nname: long\ndescription: Long skill\n"
            f"metadata:\n  short-description: {long_short}\n---\n",
            "fallback",
        )
        self.assertEqual(long_manifest.short_description, long_short)

    def test_uses_directory_name_when_name_is_missing_and_requires_description(self) -> None:
        manifest = parse_skill_manifest(
            "---\ndescription: Demo skill\n---\nBody.\n",
            lambda: "demo-skill",
        )
        self.assertEqual(manifest.name, "demo-skill")
        empty_name = parse_skill_manifest(
            "---\nname: \"\"\ndescription: Demo skill\n---\nBody.\n",
            lambda: "demo-skill",
        )
        self.assertEqual(empty_name.name, "demo-skill")

        with self.assertRaisesRegex(SkillManifestError, "missing field `description`"):
            parse_skill_manifest("---\nname: demo\n---\n", "demo")

    def test_rejects_malformed_yaml_and_overlong_names(self) -> None:
        with self.assertRaisesRegex(SkillManifestError, "invalid YAML"):
            parse_skill_manifest(
                "---\nname: valid\ndescription: usable\nmetadata:\n  - [broken\n---\n",
                "broken",
            )
        with self.assertRaisesRegex(SkillManifestError, "name"):
            parse_skill_manifest(
                "---\nname: " + "x" * 65 + "\ndescription: too long\n---\n",
                "broken",
            )

        with self.assertRaisesRegex(SkillManifestError, "name"):
            parse_skill_manifest(
                "---\nname: 42\ndescription: usable\n---\n",
                "broken",
            )
        with self.assertRaisesRegex(SkillManifestError, "name"):
            parse_skill_manifest(
                "---\ndescription: usable\n---\n",
                "..",
            )
        with self.assertRaisesRegex(SkillManifestError, "invalid YAML"):
            parse_skill_manifest(
                "---\nname: bad\x01name\ndescription: usable\n---\n",
                "broken",
            )
        with self.assertRaisesRegex(SkillManifestError, "metadata"):
            parse_skill_manifest(
                "---\nname: valid\ndescription: usable\nmetadata: []\n---\n",
                "broken",
            )
        with self.assertRaisesRegex(SkillManifestError, "short-description"):
            parse_skill_manifest(
                "---\nname: valid\ndescription: usable\n"
                "metadata:\n  short-description: 42\n---\n",
                "broken",
            )

    def test_loads_optional_eidos_metadata_and_rejects_asset_escape(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-skill-manifest-") as directory:
            root = Path(directory)
            skill = root / "skill"
            (skill / "agents").mkdir(parents=True)
            (skill / "assets").mkdir()
            (skill / "assets" / "small.png").write_bytes(b"png")
            outside = root / "outside.png"
            outside.write_bytes(b"outside")
            (skill / "assets" / "escape.png").symlink_to(outside)
            (skill / "agents" / "eidos.yaml").write_text(
                "interface:\n"
                "  display_name:  Example  Skill\n"
                "  icon_small: assets/small.png\n"
                "  icon_large: assets/escape.png\n"
                "  escaped: ignored\n"
                "dependencies:\n"
                "  tools:\n"
                "    - type: cli\n"
                "      value: example\n"
                "      description: Example command\n"
                "policy:\n"
                "  allow_implicit_invocation: false\n"
                "  unknown: ignored\n"
                "unknown: ignored\n",
                encoding="utf-8",
            )

            metadata = load_skill_agent_metadata(skill)
            expected_icon = (skill / "assets" / "small.png").resolve()

        assert metadata.interface is not None
        self.assertEqual(metadata.interface.display_name, "Example Skill")
        self.assertEqual(
            metadata.interface.icon_small,
            expected_icon,
        )
        self.assertIsNone(metadata.interface.icon_large)
        assert metadata.dependencies is not None
        self.assertEqual(metadata.dependencies.tools[0].value, "example")
        assert metadata.policy is not None
        self.assertFalse(metadata.policy.allow_implicit_invocation)

    def test_invalid_or_oversized_optional_metadata_fails_open(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-skill-manifest-") as directory:
            skill = Path(directory) / "skill"
            metadata_path = skill / "agents" / "eidos.yaml"
            metadata_path.parent.mkdir(parents=True)

            metadata_path.write_text("interface: [broken\n", encoding="utf-8")
            invalid = load_skill_agent_metadata(skill)
            self.assertIsNone(invalid.interface)

            metadata_path.write_text("x: " + "a" * (70 * 1024), encoding="utf-8")
            metadata = load_skill_agent_metadata(skill)
            self.assertIsNone(metadata.interface)
            self.assertIsNone(metadata.dependencies)
            self.assertIsNone(metadata.policy)

    def test_optional_asset_root_symlink_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-skill-manifest-") as directory:
            root = Path(directory)
            skill = root / "skill"
            (skill / "agents").mkdir(parents=True)
            outside_assets = root / "outside-assets"
            outside_assets.mkdir()
            (outside_assets / "icon.png").write_bytes(b"png")
            (skill / "assets").symlink_to(outside_assets, target_is_directory=True)
            (skill / "agents" / "eidos.yaml").write_text(
                "interface:\n  icon_small: assets/icon.png\n",
                encoding="utf-8",
            )

            metadata = load_skill_agent_metadata(skill)

        self.assertIsNone(metadata.interface)

    def test_system_installer_imports_the_shared_manifest_parser(self) -> None:
        script = (
            RUNTIME_ROOT
            / "eidos_runtime"
            / "resources"
            / "skills"
            / ".system"
            / "skill-installer"
            / "scripts"
            / "install-skill-from-github.py"
        )
        spec = importlib.util.spec_from_file_location("eidos_skill_installer", script)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        self.assertIs(module.parse_skill_manifest, parse_skill_manifest)


if __name__ == "__main__":
    unittest.main()

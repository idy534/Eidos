from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


from eidos_runtime.sandbox.seatbelt import (
    PROFILE_PATH,
    runtime_python_executable,
    secure_workspace_move,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_BUILDER = REPOSITORY_ROOT / "scripts" / "build-macos-runtime.sh"
BUNDLED_SMOKE = REPOSITORY_ROOT / "scripts" / "bundled-runtime-smoke.mjs"
BUNDLED_SEATBELT_SMOKE = REPOSITORY_ROOT / "scripts" / "bundled-seatbelt-smoke.mjs"
MANIFEST_SCRIPT = REPOSITORY_ROOT / "scripts" / "generate-runtime-manifest.mjs"


class RuntimeDistributionSeatbeltTests(unittest.TestCase):
    def test_python_runtime_policy_is_read_only(self) -> None:
        policy = PROFILE_PATH.read_text(encoding="utf-8")
        self.assertIn("(allow file-read* file-test-existence", policy)
        self.assertIn("(allow file-map-executable", policy)
        self.assertIn(
            '(deny file-write*\n  (subpath (param "PYTHON_RUNTIME_ROOT"))',
            policy,
        )
        self.assertNotIn("/Library/Developer/CommandLineTools/usr/bin/python3", policy)

    def test_secure_workspace_move_uses_the_running_python_interpreter(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            source = workspace / "candidate"
            target = workspace / "target"
            source.write_text("candidate", encoding="utf-8")
            completed = subprocess.CompletedProcess([], 0)
            with (
                patch("eidos_runtime.sandbox.seatbelt.is_seatbelt_ready", return_value=True),
                patch("eidos_runtime.sandbox.seatbelt.os.access", return_value=True),
                patch(
                    "eidos_runtime.sandbox.seatbelt.subprocess.run",
                    return_value=completed,
                ) as run,
            ):
                self.assertEqual(
                    secure_workspace_move(workspace, source, target, None),
                    "committed",
                )

            command = run.call_args.args[0]
            self.assertIn(str(runtime_python_executable()), command)
            self.assertNotIn("/Library/Developer/CommandLineTools/usr/bin/python3", command)
            self.assertIn(str(Path(sys.prefix).resolve()), " ".join(command))


class RuntimeDistributionPackagingContractTests(unittest.TestCase):
    def test_builder_keeps_existing_startup_paths_and_adds_dependency_roots(self) -> None:
        builder = RUNTIME_BUILDER.read_text(encoding="utf-8")
        self.assertIn('PYTHON_ROOT="$BUILD_ROOT/python"', builder)
        self.assertIn('APP_ROOT="$BUILD_ROOT/app"', builder)
        self.assertIn('python/bin/python3', builder)
        self.assertIn('dependencies/node/bin/node', builder)
        self.assertIn('dependencies/python', builder)
        self.assertIn('runtime-loader.mjs', builder)
        self.assertIn('runtime.json', builder)

    def test_smokes_bind_node_to_the_bundle_and_clear_inherited_node_options(self) -> None:
        smoke = BUNDLED_SMOKE.read_text(encoding="utf-8")
        seatbelt_smoke = BUNDLED_SEATBELT_SMOKE.read_text(encoding="utf-8")
        self.assertIn("dependencies", smoke)
        self.assertIn("runtime-loader.mjs", smoke)
        self.assertIn("RUNTIME_NODE_MODULES", smoke)
        self.assertIn("NODE_OPTIONS", smoke)
        self.assertIn("delete environment.NODE_OPTIONS", smoke)
        self.assertIn(".cjs", smoke)
        self.assertIn(".mjs", smoke)
        self.assertIn("delete environment.NODE_OPTIONS", seatbelt_smoke)

    def test_manifest_contract_does_not_inventory_runtime_json_itself(self) -> None:
        generator = MANIFEST_SCRIPT.read_text(encoding="utf-8")
        self.assertIn("schemaVersion", generator)
        self.assertIn("bundleId", generator)
        self.assertIn("bundleVersion", generator)
        self.assertIn("nodeLoader", generator)
        self.assertIn("nativeBinPaths", generator)
        self.assertIn("runtime.json", generator)
        self.assertIn("relative", generator)

    def test_node_release_pin_is_machine_readable(self) -> None:
        release = json.loads(
            (
                REPOSITORY_ROOT
                / "resources"
                / "runtime-dependencies"
                / "node"
                / "node-release.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(release["version"], "24.20.0")
        self.assertEqual(release["target"], "darwin-arm64")
        self.assertEqual(
            release["sha256"],
            "b7bf7707070b950ba1ec5f1af3bb6de0f2b1962c5033973d94068ab021ef3014",
        )
        self.assertTrue(release["url"].startswith("https://nodejs.org/"))


if __name__ == "__main__":
    unittest.main()

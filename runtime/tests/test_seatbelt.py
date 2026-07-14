from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.seatbelt import (  # noqa: E402
    SANDBOX_EXECUTABLE,
    SeatbeltProfile,
    run_seatbelt_self_test,
)


class SeatbeltProfileTests(unittest.TestCase):
    def test_profile_uses_static_template_and_path_parameters(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workspace = root / "workspace"
            sandbox_home = root / "home"
            sandbox_tmp = root / "tmp"
            git_directory = workspace / ".git"
            sensitive_path = workspace / ".env"
            for directory in (workspace, sandbox_home, sandbox_tmp, git_directory):
                directory.mkdir(parents=True, exist_ok=True)
            sensitive_path.write_text("secret", encoding="utf-8")

            profile = SeatbeltProfile.create(
                workspace_root=workspace,
                sandbox_home=sandbox_home,
                sandbox_tmp=sandbox_tmp,
                sensitive_path=sensitive_path,
            )
            command = profile.command(["/usr/bin/true"])

            self.assertEqual(command[0], SANDBOX_EXECUTABLE)
            self.assertEqual(command[1], "-f")
            self.assertTrue(command[2].endswith("seatbelt.sbpl"))
            self.assertIn(f"-DWORKSPACE_ROOT={workspace.resolve()}", command)
            self.assertIn(f"-DGIT_DIR={git_directory.resolve()}", command)
            self.assertIn(f"-DSENSITIVE_PATH={sensitive_path.resolve()}", command)
            self.assertIn(f"-DSANDBOX_HOME={sandbox_home.resolve()}", command)
            self.assertIn(f"-DSANDBOX_TMP={sandbox_tmp.resolve()}", command)
            self.assertEqual(command[-2:], ["--", "/usr/bin/true"])
            self.assertEqual(
                set(profile.environment()),
                {"HOME", "TMPDIR", "PATH", "LANG", "LC_ALL", "GIT_OPTIONAL_LOCKS"},
            )

    def test_profile_rejects_workspace_relative_sensitive_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workspace = root / "workspace"
            sandbox_home = root / "home"
            sandbox_tmp = root / "tmp"
            for directory in (workspace, sandbox_home, sandbox_tmp, workspace / ".git"):
                directory.mkdir(parents=True, exist_ok=True)

            with self.assertRaisesRegex(ValueError, "sensitive path must be inside workspace"):
                SeatbeltProfile.create(
                    workspace_root=workspace,
                    sandbox_home=sandbox_home,
                    sandbox_tmp=sandbox_tmp,
                    sensitive_path=root / "outside.env",
                )


@unittest.skipUnless(sys.platform == "darwin", "Seatbelt is macOS-only")
class SeatbeltSmokeTests(unittest.TestCase):
    def test_workspace_write_profile_passes_fail_closed_self_test(self) -> None:
        result = run_seatbelt_self_test()

        self.assertTrue(result.available, result.failures)
        self.assertEqual(result.failures, ())
        self.assertIn("workspace_write", result.passed_checks)
        self.assertIn("external_read_denied", result.passed_checks)
        self.assertIn("git_write_denied", result.passed_checks)
        self.assertIn("sensitive_read_denied", result.passed_checks)
        self.assertIn("symlink_escape_denied", result.passed_checks)
        self.assertIn("loopback_denied", result.passed_checks)
        self.assertIn("timeout_enforced", result.passed_checks)


if __name__ == "__main__":
    unittest.main()

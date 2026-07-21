from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.sandbox.seatbelt import (  # noqa: E402
    SANDBOX_EXECUTABLE,
    SeatbeltProfile,
    run_sandboxed,
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
                {
                    "HOME",
                    "TMPDIR",
                    "PATH",
                    "LANG",
                    "LC_ALL",
                    "GIT_OPTIONAL_LOCKS",
                    "PNPM_CONFIG_PM_ON_FAIL",
                },
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

    def test_profile_accepts_git_worktree_pointer_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workspace = root / "workspace"
            sandbox_home = root / "home"
            sandbox_tmp = root / "tmp"
            for directory in (workspace, sandbox_home, sandbox_tmp):
                directory.mkdir(parents=True, exist_ok=True)
            git_pointer = workspace / ".git"
            git_pointer.write_text("gitdir: /outside/worktree\n", encoding="utf-8")

            profile = SeatbeltProfile.create(
                workspace_root=workspace,
                sandbox_home=sandbox_home,
                sandbox_tmp=sandbox_tmp,
                sensitive_path=workspace / ".env",
            )

            self.assertEqual(profile.git_directory, git_pointer.resolve())


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

    def test_git_worktree_pointer_is_supported_but_not_writable(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-worktree-") as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            home = root / "home"
            sandbox_tmp = root / "tmp"
            for directory in (workspace, home, sandbox_tmp):
                directory.mkdir()
            (workspace / "package.json").write_text(
                '{"packageManager":"pnpm@0.0.1"}\n', encoding="utf-8"
            )
            pointer = workspace / ".git"
            original = "gitdir: /private/tmp/external-worktree\n"
            pointer.write_text(original, encoding="utf-8")
            profile = SeatbeltProfile.create(
                workspace_root=workspace,
                sandbox_home=home,
                sandbox_tmp=sandbox_tmp,
                sensitive_path=workspace / ".env",
            )

            allowed = run_sandboxed(profile, ["/usr/bin/true"])
            denied = run_sandboxed(
                profile, ["/bin/sh", "-c", "printf changed >> .git"]
            )

            self.assertEqual(allowed.returncode, 0, allowed.stderr)
            self.assertNotEqual(denied.returncode, 0)
            self.assertEqual(pointer.read_text(encoding="utf-8"), original)

    @unittest.skipUnless(
        Path("/opt/homebrew/bin/node").is_file()
        and Path("/opt/homebrew/bin/pnpm").is_file(),
        "Homebrew Node toolchain is unavailable",
    )
    def test_fixed_homebrew_node_toolchain_is_readable_and_executable(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-homebrew-toolchain-") as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            home = root / "home"
            sandbox_tmp = root / "tmp"
            for directory in (workspace, home, sandbox_tmp):
                directory.mkdir()
            profile = SeatbeltProfile.create(
                workspace_root=workspace,
                sandbox_home=home,
                sandbox_tmp=sandbox_tmp,
                sensitive_path=workspace / ".env",
            )

            node = run_sandboxed(
                profile,
                ["/bin/sh", "-c", "node -e 'process.stdout.write(\"node-ok\")'"],
            )
            pnpm = run_sandboxed(profile, ["/bin/sh", "-c", "pnpm --version"])

            self.assertEqual(node.returncode, 0, node.stderr)
            self.assertEqual(node.stdout, "node-ok")
            self.assertEqual(pnpm.returncode, 0, pnpm.stderr)
            self.assertRegex(pnpm.stdout.strip(), r"^\d+\.\d+\.\d+$")


if __name__ == "__main__":
    unittest.main()

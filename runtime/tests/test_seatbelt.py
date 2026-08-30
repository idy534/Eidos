from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from unittest.mock import patch


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.sandbox.seatbelt import (  # noqa: E402
    SANDBOX_EXECUTABLE,
    SeatbeltUnavailableError,
    SeatbeltProfile,
    is_seatbelt_usable,
    run_sandboxed,
    run_seatbelt_self_test,
    secure_workspace_move,
)
from eidos_runtime.sandbox.permissions import (  # noqa: E402
    AdditionalPermissionProfile,
    BasePermissionProfile,
    FileSystemAccessMode,
    FileSystemPermissionEntry,
    NetworkPermissions,
    materialize_effective_profile,
)
from eidos_runtime.workspace.search_driver import (  # noqa: E402
    RipgrepBinaryResolver,
    SearchDriverError,
)


class SeatbeltProfileTests(unittest.TestCase):
    def test_profile_path_includes_verified_bundled_ripgrep(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            for directory in (root / "workspace", root / "home", root / "tmp"):
                directory.mkdir(parents=True)
            profile = SeatbeltProfile.create(
                workspace_root=root / "workspace",
                sandbox_home=root / "home",
                sandbox_tmp=root / "tmp",
            )

            self.assertFalse(hasattr(profile, "environment"))

    def test_bundled_ripgrep_runs_inside_the_shell_profile(self) -> None:
        if not is_seatbelt_usable():
            self.skipTest("macOS Seatbelt is unavailable")
        try:
            binary = RipgrepBinaryResolver().resolve()
        except SearchDriverError:
            self.skipTest("bundled ripgrep is unavailable for this platform")
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workspace = root / "workspace"
            home = root / "home"
            sandbox_tmp = root / "tmp"
            for directory in (workspace, home, sandbox_tmp):
                directory.mkdir(parents=True)
            resources = RUNTIME_ROOT / "eidos_runtime" / "resources"
            permissions = materialize_effective_profile(
                BasePermissionProfile.for_workspace(
                    workspace_root=workspace,
                    runtime_roots=(resources,),
                )
            )
            profile = SeatbeltProfile.create(
                workspace_root=workspace,
                sandbox_home=home,
                sandbox_tmp=sandbox_tmp,
                effective_permissions=permissions,
            )

            result = run_sandboxed(
                profile,
                ["/bin/sh", "-c", "rg --version"],
                timeout_seconds=5,
                environment={
                    "HOME": str(home),
                    "TMPDIR": str(sandbox_tmp),
                    "PATH": os.pathsep.join((str(binary.parent), "/usr/bin", "/bin")),
                },
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("ripgrep", result.stdout)

    def test_profile_uses_static_template_and_path_parameters(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workspace = root / "workspace"
            sandbox_home = root / "home"
            sandbox_tmp = root / "tmp"
            git_directory = workspace / ".git"
            for directory in (workspace, sandbox_home, sandbox_tmp, git_directory):
                directory.mkdir(parents=True, exist_ok=True)

            profile = SeatbeltProfile.create(
                workspace_root=workspace,
                sandbox_home=sandbox_home,
                sandbox_tmp=sandbox_tmp,
            )
            if is_seatbelt_usable():
                command = profile.command(["/usr/bin/true"])
                self.assertEqual(command[0], SANDBOX_EXECUTABLE)
                self.assertEqual(command[1], "-f")
                self.assertTrue(command[2].endswith("seatbelt.sbpl"))
                self.assertIn(f"-DWORKSPACE_ROOT={workspace.resolve()}", command)
                self.assertIn(f"-DGIT_DIR={git_directory.resolve()}", command)
                self.assertIn(f"-DGIT_WORKTREE_DIR={git_directory.resolve()}", command)
                self.assertIn(f"-DGIT_COMMON_DIR={git_directory.resolve()}", command)
                self.assertIn(f"-DSANDBOX_HOME={sandbox_home.resolve()}", command)
                self.assertIn(f"-DSANDBOX_TMP={sandbox_tmp.resolve()}", command)
                self.assertIn(
                    f"-DSYSTEM_TMP_ROOT={Path('/tmp').resolve()}",
                    command,
                )
                self.assertFalse(any("SENSITIVE_PATH" in item for item in command))
                self.assertEqual(command[-2:], ["--", "/usr/bin/true"])
            else:
                with self.assertRaises(SeatbeltUnavailableError):
                    profile.command(["/usr/bin/true"])
            self.assertFalse(hasattr(profile, "environment"))

    def test_direct_profile_has_no_git_paths_or_definitions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workspace = root / "workspace"
            sandbox_home = root / "home"
            sandbox_tmp = root / "tmp"
            for directory in (workspace, sandbox_home, sandbox_tmp):
                directory.mkdir(parents=True)

            profile = SeatbeltProfile.create(
                workspace_root=workspace,
                sandbox_home=sandbox_home,
                sandbox_tmp=sandbox_tmp,
            )
            self.assertIsNone(profile.git_directory)
            self.assertIsNone(profile.git_worktree_dir)
            self.assertIsNone(profile.git_common_dir)
            with patch(
                "eidos_runtime.sandbox.seatbelt.is_seatbelt_ready",
                return_value=True,
            ):
                command = profile.command(["/usr/bin/true"])
            self.assertTrue(command[2].endswith("seatbelt-direct.sbpl"))
            self.assertEqual(
                [argument for argument in command if argument.startswith("-DGIT_")],
                [f"-DGIT_DIR={(workspace / '.git').resolve()}"],
            )

    def test_unavailable_seatbelt_fails_before_process_creation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workspace = root / "workspace"
            sandbox_home = root / "home"
            sandbox_tmp = root / "tmp"
            for directory in (workspace, sandbox_home, sandbox_tmp, workspace / ".git"):
                directory.mkdir(parents=True, exist_ok=True)
            profile = SeatbeltProfile.create(
                workspace_root=workspace,
                sandbox_home=sandbox_home,
                sandbox_tmp=sandbox_tmp,
            )

            with (
                patch("eidos_runtime.sandbox.seatbelt.is_seatbelt_usable", return_value=False),
                patch("eidos_runtime.sandbox.seatbelt.subprocess.Popen") as popen,
                self.assertRaises(SeatbeltUnavailableError),
            ):
                run_sandboxed(profile, ["/usr/bin/true"])

            popen.assert_not_called()

    def test_unavailable_seatbelt_never_moves_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            workspace = Path(temporary_directory)
            target = workspace / "target.txt"
            target.write_text("base\n", encoding="utf-8")
            source = workspace / ".candidate.tmp"
            source.write_text("candidate\n", encoding="utf-8")
            expected = hashlib.sha256(b"base\n").hexdigest()

            with (
                patch("eidos_runtime.sandbox.seatbelt.is_seatbelt_usable", return_value=False),
                patch("eidos_runtime.sandbox.seatbelt.os.replace") as replace,
            ):
                status = secure_workspace_move(workspace, source, target, expected)

            self.assertEqual(status, "failed")
            replace.assert_not_called()
            self.assertEqual(target.read_text(encoding="utf-8"), "base\n")
            self.assertEqual(source.read_text(encoding="utf-8"), "candidate\n")

    def test_profile_does_not_require_a_sensitive_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            workspace = root / "workspace"
            sandbox_home = root / "home"
            sandbox_tmp = root / "tmp"
            for directory in (workspace, sandbox_home, sandbox_tmp, workspace / ".git"):
                directory.mkdir(parents=True, exist_ok=True)
            (workspace / ".env").write_text("workspace secret", encoding="utf-8")

            profile = SeatbeltProfile.create(
                workspace_root=workspace,
                sandbox_home=sandbox_home,
                sandbox_tmp=sandbox_tmp,
            )

            self.assertEqual(profile.workspace_root, workspace.resolve())

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
            )

            self.assertEqual(profile.git_directory, git_pointer.resolve())


@unittest.skipUnless(sys.platform == "darwin" and is_seatbelt_usable(), "Seatbelt is macOS-only and requires usable sandbox-exec")
class SeatbeltSmokeTests(unittest.TestCase):
    @staticmethod
    def dynamic_profile(
        root: Path,
        overlay: AdditionalPermissionProfile | None = None,
        *,
        protected_write_paths: tuple[Path, ...] = (),
    ) -> SeatbeltProfile:
        workspace = root / "workspace"
        home = root / "home"
        sandbox_tmp = root / "tmp"
        for directory in (workspace, home, sandbox_tmp):
            directory.mkdir(exist_ok=True)
        effective = materialize_effective_profile(
            BasePermissionProfile.for_workspace(
                workspace_root=workspace,
                protected_write_paths=protected_write_paths,
            ),
            overlay,
        )
        return SeatbeltProfile.create(
            workspace_root=workspace,
            sandbox_home=home,
            sandbox_tmp=sandbox_tmp,
            effective_permissions=effective,
        )

    def test_workspace_write_profile_passes_fail_closed_self_test(self) -> None:
        result = run_seatbelt_self_test()

        self.assertTrue(result.available, result.failures)
        self.assertEqual(result.failures, ())
        self.assertIn("workspace_write", result.passed_checks)
        self.assertIn("external_read_allowed", result.passed_checks)
        self.assertIn("home_read_allowed", result.passed_checks)
        self.assertIn("home_write_denied", result.passed_checks)
        self.assertIn("sandbox_tmp_write", result.passed_checks)
        self.assertIn("system_tmp_write", result.passed_checks)
        self.assertIn("git_write_denied", result.passed_checks)
        self.assertIn("sensitive_read_allowed", result.passed_checks)
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
            )

            allowed = run_sandboxed(profile, ["/usr/bin/true"])
            denied = run_sandboxed(
                profile, ["/bin/sh", "-c", "printf changed >> .git"]
            )

            self.assertEqual(allowed.returncode, 0, allowed.stderr)
            self.assertNotEqual(denied.returncode, 0)
            self.assertEqual(pointer.read_text(encoding="utf-8"), original)

    def test_full_disk_read_keeps_eidos_data_denied(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-full-read-") as temporary:
            root = Path(temporary)
            data = root / "data"
            workspace = data / ".eidos-worktrees" / "wt_1"
            home = root / "home"
            sandbox_tmp = root / "tmp"
            outside = root / "outside"
            for directory in (workspace, home, sandbox_tmp, outside):
                directory.mkdir(parents=True)
            (outside / "ordinary.txt").write_text("ordinary", encoding="utf-8")
            (home / ".gitconfig").write_text(
                "[user]\n\tname = Eidos Native Test\n", encoding="utf-8"
            )
            (data / "models.json").write_text("models", encoding="utf-8")
            (data / "private-token").write_text("token", encoding="utf-8")
            (workspace / ".env").write_text("workspace-env", encoding="utf-8")
            (workspace / ".git").mkdir()
            (workspace / ".git" / "HEAD").write_text("head", encoding="utf-8")
            effective = materialize_effective_profile(
                BasePermissionProfile.for_workspace(
                    workspace_root=workspace,
                    protected_paths=(data,),
                )
            )
            profile = SeatbeltProfile.create(
                workspace_root=workspace,
                sandbox_home=home,
                sandbox_tmp=sandbox_tmp,
                effective_permissions=effective,
            )
            environment = {
                "HOME": str(home),
                "TMPDIR": str(sandbox_tmp),
                "PATH": "/usr/bin:/bin",
            }

            def read(path: Path):
                return run_sandboxed(
                    profile,
                    ["/bin/cat", str(path)],
                    environment=environment,
                )

            self.assertEqual(read(outside / "ordinary.txt").stdout, "ordinary")
            self.assertEqual(read(home / ".gitconfig").returncode, 0)
            git_name = run_sandboxed(
                profile,
                [
                    "/bin/sh",
                    "-c",
                    'cd "$HOME" && git config --global user.name',
                    "eidos-git-config",
                ],
                environment=environment,
            )
            self.assertEqual(git_name.returncode, 0, git_name.stderr)
            self.assertEqual(git_name.stdout.strip(), "Eidos Native Test")
            self.assertEqual(read(workspace / ".env").stdout, "workspace-env")
            self.assertEqual(read(workspace / ".git" / "HEAD").stdout, "head")
            self.assertNotEqual(read(data / "models.json").returncode, 0)
            self.assertNotEqual(read(data / "private-token").returncode, 0)

    def test_workspace_and_temp_write_only_excludes_home_and_outside(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-write-policy-") as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            home = root / "home"
            sandbox_tmp = root / "tmp"
            outside = root / "outside"
            for directory in (workspace, home, sandbox_tmp, outside):
                directory.mkdir()
            profile = SeatbeltProfile.create(
                workspace_root=workspace,
                sandbox_home=home,
                sandbox_tmp=sandbox_tmp,
            )
            environment = {
                "HOME": str(home),
                "TMPDIR": str(sandbox_tmp),
                "PATH": "/usr/bin:/bin",
            }
            system_tmp_file = (
                Path("/tmp") / f"eidos-seatbelt-{os.getpid()}-{uuid.uuid4().hex}"
            )
            try:
                workspace_file = workspace / "workspace.txt"
                tmp_file = sandbox_tmp / "tmp.txt"
                home_file = home / "home.txt"
                outside_file = outside / "outside.txt"
                self.assertEqual(
                    run_sandboxed(
                        profile,
                        ["/usr/bin/touch", str(workspace_file)],
                        environment=environment,
                    ).returncode,
                    0,
                )
                self.assertEqual(
                    run_sandboxed(
                        profile,
                        ["/usr/bin/touch", str(tmp_file)],
                        environment=environment,
                    ).returncode,
                    0,
                )
                self.assertEqual(
                    run_sandboxed(
                        profile,
                        ["/usr/bin/touch", str(system_tmp_file)],
                        environment=environment,
                    ).returncode,
                    0,
                )
                self.assertNotEqual(
                    run_sandboxed(
                        profile,
                        ["/usr/bin/touch", str(home_file)],
                        environment=environment,
                    ).returncode,
                    0,
                )
                self.assertNotEqual(
                    run_sandboxed(
                        profile,
                        ["/usr/bin/touch", str(outside_file)],
                        environment=environment,
                    ).returncode,
                    0,
                )
            finally:
                system_tmp_file.unlink(missing_ok=True)

    def test_non_git_workspace_cannot_create_git_metadata_directory(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-non-git-workspace-") as temporary:
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
            )
            git_directory = workspace / ".git"

            create = run_sandboxed(profile, ["/bin/mkdir", str(git_directory)])
            write = run_sandboxed(
                profile,
                ["/usr/bin/touch", str(git_directory / "config")],
            )

            self.assertNotEqual(create.returncode, 0, create.stderr)
            self.assertNotEqual(write.returncode, 0, write.stderr)
            self.assertFalse(git_directory.exists())

    def test_real_linked_worktree_git_reads_are_allowed_but_metadata_writes_are_denied(
        self,
    ) -> None:
        if not is_seatbelt_usable():
            self.skipTest("macOS Seatbelt is unavailable")
        git = shutil.which("git") or "/usr/bin/git"
        with tempfile.TemporaryDirectory(prefix="eidos-linked-worktree-") as temporary:
            root = Path(temporary)
            repository = root / "repository"
            worktree = root / "managed-worktree"
            home = root / "home"
            sandbox_tmp = root / "tmp"
            repository.mkdir()
            home.mkdir()
            sandbox_tmp.mkdir()

            def run_git(*args: str, cwd: Path = repository) -> str:
                completed = subprocess.run(
                    [git, *args],
                    cwd=cwd,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                return completed.stdout.strip()

            run_git("init", "-q", "-b", "main")
            run_git("config", "user.email", "eidos-tests@example.com")
            run_git("config", "user.name", "Eidos Tests")
            (repository / "README.md").write_text("base\n", encoding="utf-8")
            run_git("add", "README.md")
            run_git("commit", "-qm", "initial")
            run_git("worktree", "add", "-q", "-b", "eidos/linked", str(worktree))

            def resolve_git_path(value: str, cwd: Path) -> Path:
                path = Path(value)
                return (path if path.is_absolute() else cwd / path).resolve()

            git_dir = resolve_git_path(
                run_git("rev-parse", "--git-dir", cwd=worktree),
                worktree,
            )
            git_common_dir = resolve_git_path(
                run_git("rev-parse", "--git-common-dir", cwd=worktree),
                worktree,
            )
            profile = SeatbeltProfile.create(
                workspace_root=worktree,
                sandbox_home=home,
                sandbox_tmp=sandbox_tmp,
                git_worktree_dir=git_dir,
                git_common_dir=git_common_dir,
            )
            environment = {
                "HOME": str(home),
                "TMPDIR": str(sandbox_tmp),
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "GIT_CONFIG_NOSYSTEM": "1",
            }

            for command in (
                ["status", "--porcelain=v2"],
                ["diff", "--no-ext-diff", "--no-textconv"],
                ["log", "-1"],
                ["rev-parse", "HEAD"],
                ["branch", "--show-current"],
            ):
                result = run_sandboxed(
                    profile,
                    [git, *command],
                    timeout_seconds=5,
                    environment=environment,
                )
                self.assertEqual(result.returncode, 0, (command, result.stderr))

            normal_write = worktree / "normal.txt"
            normal = run_sandboxed(
                profile,
                ["/usr/bin/touch", str(normal_write)],
            )
            self.assertEqual(normal.returncode, 0, normal.stderr)
            self.assertTrue(normal_write.exists())

            (worktree / "change.txt").write_text("change\n", encoding="utf-8")
            for command in (
                ["add", "change.txt"],
                ["commit", "-m", "test"],
                ["branch", "eidos/blocked"],
                ["switch", "main"],
                ["checkout", "main"],
                ["reset", "--hard", "HEAD"],
            ):
                result = run_sandboxed(
                    profile,
                    [git, *command],
                    timeout_seconds=5,
                    environment=environment,
                )
                self.assertNotEqual(result.returncode, 0, (command, result.stdout))

            for path in (
                git_dir / "direct-write",
                git_common_dir / "direct-write",
                repository / "original-write",
            ):
                result = run_sandboxed(
                    profile,
                    ["/usr/bin/touch", str(path)],
                )
                self.assertNotEqual(result.returncode, 0, str(path))
                self.assertFalse(path.exists())

            cleanup = subprocess.run(
                [git, "worktree", "remove", "--force", str(worktree)],
                cwd=repository,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(cleanup.returncode, 0, cleanup.stderr)

    def test_dynamic_profile_grants_only_approved_external_paths(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-dynamic-paths-") as temporary:
            root = Path(temporary)
            allowed = root / "allowed"
            sibling = root / "sibling"
            executable = root / "executable"
            for directory in (allowed, sibling, executable):
                directory.mkdir()
            (allowed / "read.txt").write_text("approved", encoding="utf-8")
            (sibling / "read.txt").write_text("private", encoding="utf-8")
            script = executable / "hello"
            script.write_text("#!/bin/sh\nprintf executable-ok\n", encoding="utf-8")
            script.chmod(0o755)

            default_read = run_sandboxed(
                self.dynamic_profile(root),
                ["/bin/cat", str(allowed / "read.txt")],
            )
            expanded = self.dynamic_profile(
                root,
                AdditionalPermissionProfile(file_system=(
                    FileSystemPermissionEntry(
                        path=str(allowed),
                        access=FileSystemAccessMode.WRITE,
                    ),
                    FileSystemPermissionEntry(
                        path=str(executable),
                        access=FileSystemAccessMode.EXECUTE,
                    ),
                )),
            )
            read = run_sandboxed(
                expanded, ["/bin/cat", str(allowed / "read.txt")]
            )
            write = run_sandboxed(
                expanded,
                [
                    "/bin/sh",
                    "-c",
                    'printf ok > "$1"; printf denied > "$2"',
                    "sh",
                    str(allowed / "written.txt"),
                    str(sibling / "written.txt"),
                ],
            )
            execute = run_sandboxed(expanded, [str(script)])

            self.assertEqual(default_read.returncode, 0, default_read.stderr)
            self.assertEqual(default_read.stdout, "approved")
            self.assertEqual(
                read.stdout,
                "approved",
                f"returncode={read.returncode} stderr={read.stderr}",
            )
            self.assertEqual((allowed / "written.txt").read_text(), "ok")
            self.assertFalse((sibling / "written.txt").exists())
            self.assertNotEqual(write.returncode, 0)
            self.assertEqual(execute.stdout, "executable-ok")

    def test_dynamic_profile_keeps_runtime_write_and_git_denies(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-dynamic-denies-") as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            runtime = workspace / "runtime"
            git = workspace / ".git"
            runtime.mkdir(parents=True)
            git.mkdir()
            protected = runtime / "policy.sbpl"
            protected.write_text("original", encoding="utf-8")
            profile = self.dynamic_profile(
                root, protected_write_paths=(runtime,)
            )

            result = run_sandboxed(
                profile,
                [
                    "/bin/sh",
                    "-c",
                    'printf changed > "$1"; printf changed > "$2"',
                    "sh",
                    str(protected),
                    str(git / "config"),
                ],
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(protected.read_text(), "original")
            self.assertFalse((git / "config").exists())

    def test_managed_workspace_inside_data_keeps_data_state_denied(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-managed-data-") as temporary:
            root = Path(temporary)
            data = root / "data"
            workspace = data / ".eidos-worktrees" / "wt_1"
            home = root / "home"
            sandbox_tmp = root / "tmp"
            workspace.mkdir(parents=True)
            home.mkdir()
            sandbox_tmp.mkdir()
            state = data / "state.sqlite"
            state.write_text("protected", encoding="utf-8")
            target = workspace / "created.txt"
            permissions = materialize_effective_profile(
                BasePermissionProfile.for_workspace(
                    workspace_root=workspace,
                    protected_paths=(data,),
                )
            )
            profile = SeatbeltProfile.create(
                workspace_root=workspace,
                sandbox_home=home,
                sandbox_tmp=sandbox_tmp,
                effective_permissions=permissions,
            )

            write = run_sandboxed(
                profile,
                ["/bin/sh", "-c", "printf created > \"$1\"", "sh", str(target)],
            )
            denied = run_sandboxed(profile, ["/bin/cat", str(state)])

            self.assertEqual(write.returncode, 0, write.stderr)
            self.assertEqual(target.read_text(encoding="utf-8"), "created")
            self.assertNotEqual(denied.returncode, 0)
            self.assertEqual(state.read_text(encoding="utf-8"), "protected")

    def test_dynamic_network_grant_reaches_only_when_enabled(self) -> None:
        class Handler(socketserver.BaseRequestHandler):
            def handle(self) -> None:
                self.request.recv(1)

        with (
            tempfile.TemporaryDirectory(prefix="eidos-dynamic-network-") as temporary,
            socketserver.TCPServer(("127.0.0.1", 0), Handler) as server,
        ):
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = server.server_address[1]
            command = [
                "/usr/bin/nc", "-z", "-w", "1", "127.0.0.1", str(port)
            ]

            denied = run_sandboxed(
                self.dynamic_profile(Path(temporary)), command
            )
            allowed = run_sandboxed(
                self.dynamic_profile(
                    Path(temporary),
                    AdditionalPermissionProfile(
                        network=NetworkPermissions(enabled=True)
                    ),
                ),
                command,
            )
            server.shutdown()
            thread.join(timeout=2)

            self.assertNotEqual(denied.returncode, 0)
            self.assertEqual(allowed.returncode, 0, allowed.stderr)

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

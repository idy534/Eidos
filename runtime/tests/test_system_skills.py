from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import io
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
from contextlib import redirect_stderr
from unittest.mock import patch


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.db.storage import SessionStore, WorkspaceIdentity  # noqa: E402
from eidos_runtime.extensions.plugins import PluginCatalog  # noqa: E402
from eidos_runtime.extensions.skill_manifest import (  # noqa: E402
    load_skill_agent_metadata,
)
from eidos_runtime.extensions.skills import SkillCatalog  # noqa: E402
from eidos_runtime.models.skill_runtime import ExecutableRequirement  # noqa: E402
from eidos_runtime.sandbox.dependency_environment import (  # noqa: E402
    DependencyShellEnvironment,
)
from eidos_runtime.sandbox.host_shell import (  # noqa: E402
    HostShell,
    ShellEnvironmentSnapshot,
)
from eidos_runtime.sandbox.seatbelt import runtime_python_executable  # noqa: E402
from eidos_runtime.sandbox.shell import run_shell  # noqa: E402


SYSTEM = (
    RUNTIME_ROOT
    / "eidos_runtime" / "resources" / "skills" / ".system"
)


class _PassthroughSeatbeltProfile:
    @staticmethod
    def command(command: list[str]) -> list[str]:
        return list(command)


class _FixedHostShellResolver:
    def __init__(self, shell: HostShell) -> None:
        self._shell = shell

    def resolve(self) -> HostShell:
        return self._shell


class _FixedShellEnvironmentProvider:
    def __init__(self, snapshot: ShellEnvironmentSnapshot) -> None:
        self._snapshot = snapshot

    def get(
        self,
        shell: HostShell,
        cwd: Path,
        *,
        command_wrapper: object | None = None,
    ) -> ShellEnvironmentSnapshot:
        del shell, cwd, command_wrapper
        return self._snapshot

    def fallback_environment(self, shell: HostShell) -> dict[str, str]:
        del shell
        return dict(self._snapshot.environment)


def _workspace_identity(path: Path) -> WorkspaceIdentity:
    metadata = path.stat()
    return WorkspaceIdentity(
        path=path.resolve(),
        device=metadata.st_dev,
        inode=metadata.st_ino,
        owner=metadata.st_uid,
    )


class SystemSkillScriptTests(unittest.TestCase):
    def test_plugin_creator_loads_bundled_python_runtime_declaration(self) -> None:
        metadata = load_skill_agent_metadata(SYSTEM / "plugin-creator")

        self.assertIsNotNone(metadata.runtime_dependencies)
        self.assertIsNone(metadata.runtime_dependency_error)
        assert metadata.runtime_dependencies is not None
        self.assertEqual(metadata.runtime_dependencies.schema_version, 1)
        self.assertEqual(len(metadata.runtime_dependencies.dependencies), 1)
        dependency = metadata.runtime_dependencies.dependencies[0]
        self.assertIsInstance(dependency, ExecutableRequirement)
        self.assertEqual(dependency.kind, "executable")
        self.assertEqual(dependency.name, "python3")
        self.assertEqual(dependency.version, ">=3.11,<3.13")
        self.assertTrue(dependency.required)

    def test_plugin_creator_instructions_use_bound_runtime_and_workspace_only(self) -> None:
        instructions = (
            SYSTEM / "plugin-creator" / "SKILL.md"
        ).read_text(encoding="utf-8")

        self.assertIn("workspace_dependencies", instructions)
        self.assertIn("dependencyBindingId", instructions)
        self.assertIn("$RUNTIME_PYTHON", instructions)
        self.assertIn("<absolute skill root>/scripts/", instructions)
        self.assertIn("data.activeSkillDependencyBindings", instructions)
        self.assertIn("system:plugin-creator", instructions)
        self.assertIn('status` is `"ready"`', instructions)
        self.assertIn("canonical absolute Skill root", instructions)
        self.assertIn('"cwd": "."', instructions)
        self.assertIn("never write", instructions.lower())
        self.assertNotIn("$SKILL_DIR", instructions)
        self.assertNotIn("Run from this skill directory", instructions)
        self.assertNotRegex(instructions, r"(?m)^python3\s")

    def test_plugin_creator_bound_command_runs_from_workspace_without_writing_skill(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-plugin-shell-") as directory:
            root = Path(directory)
            workspace = root / "workspace"
            home = root / "home"
            tmpdir = root / "tmp"
            workspace.mkdir()
            home.mkdir()
            tmpdir.mkdir()
            workspace_identity = _workspace_identity(workspace)
            skill_root = (SYSTEM / "plugin-creator").resolve()
            before = {
                path.relative_to(skill_root).as_posix(): path.read_bytes()
                for path in skill_root.rglob("*")
                if path.is_file()
            }
            shell = HostShell(Path("/bin/sh"), "sh")
            shell_snapshot = ShellEnvironmentSnapshot(
                shell=shell,
                environment={
                    "HOME": str(home),
                    "TMPDIR": str(tmpdir),
                    "PATH": "/usr/bin:/bin",
                    "SHELL": str(shell.executable),
                    "USER": "test-user",
                    "LOGNAME": "test-user",
                    "LANG": "en_US.UTF-8",
                },
                captured_at=datetime(2026, 8, 31, tzinfo=timezone.utc),
                source="captured",
                diagnostic="captured",
            )
            command = (
                '"$RUNTIME_PYTHON" '
                f"{shlex.quote(str(skill_root / 'scripts' / 'create_basic_plugin.py'))} "
                "smoke-plugin --path generated --skill review"
            )
            binding = DependencyShellEnvironment(
                binding_id="a" * 64,
                python_executable=str(runtime_python_executable()),
            )
            with (
                patch(
                    "eidos_runtime.sandbox.shell.HOST_SHELL_RESOLVER",
                    _FixedHostShellResolver(shell),
                ),
                patch(
                    "eidos_runtime.sandbox.shell.SHELL_ENVIRONMENT_PROVIDER",
                    _FixedShellEnvironmentProvider(shell_snapshot),
                ),
                patch(
                    "eidos_runtime.sandbox.shell.SeatbeltProfile.create",
                    return_value=_PassthroughSeatbeltProfile(),
                ),
            ):
                result = run_shell(
                    workspace_identity,
                    command,
                    workspace_identity,
                    10,
                    threading.Event(),
                    lambda _delta: None,
                    active_skill_roots=(skill_root,),
                    dependency_environment=binding,
                )

            self.assertEqual(result["outcome"], "success", result)
            plugin = workspace / "generated" / "smoke-plugin"
            self.assertTrue((plugin / "plugin.json").is_file())
            after = {
                path.relative_to(skill_root).as_posix(): path.read_bytes()
                for path in skill_root.rglob("*")
                if path.is_file()
            }
            self.assertEqual(after, before)

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

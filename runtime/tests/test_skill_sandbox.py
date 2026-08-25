from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch


RUNTIME_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RUNTIME_ROOT))

from eidos_runtime.extensions.skill_access import (  # noqa: E402
    SkillAccess,
    SkillAccessRecord,
    SkillActivationKind,
)
from eidos_runtime.extensions.skills import (  # noqa: E402
    SkillCatalogEntry,
    SkillCatalogSnapshot,
)
from eidos_runtime.sandbox.permissions import (  # noqa: E402
    BasePermissionProfile,
    materialize_effective_profile,
)
from eidos_runtime.sandbox.seatbelt import (  # noqa: E402
    SeatbeltProfile,
    is_seatbelt_usable,
    run_sandboxed,
    runtime_python_executable,
)
from eidos_runtime.sandbox.seatbelt_policy import SeatbeltPolicyCompiler  # noqa: E402
from eidos_runtime.sandbox.shell import run_shell  # noqa: E402
from eidos_runtime.db.storage import WorkspaceIdentity  # noqa: E402


class SkillSandboxUnitTests(unittest.TestCase):
    def test_active_skill_root_is_readable_executable_and_write_denied_in_compiled_policy(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-skill-policy-") as directory:
            root = Path(directory)
            workspace = root / "workspace"
            data = root / "data"
            skill = data / "skills" / "review"
            workspace.mkdir()
            skill.mkdir(parents=True)
            base = BasePermissionProfile.for_workspace(
                workspace_root=workspace,
                protected_paths=(data,),
            ).model_copy(update={"active_skill_roots": (str(skill),)})

            effective = materialize_effective_profile(base)
            compiled = SeatbeltPolicyCompiler().compile(effective)

            self.assertEqual(effective.active_skill_roots, (str(skill.resolve()),))
            self.assertIn(str(skill.resolve()), compiled.parameters.values())
            self.assertIn("file-map-executable", compiled.policy)
            self.assertIn("SKILL_ROOT_0", compiled.policy)
            self.assertIn("file-write*", compiled.policy)
            self.assertIn(
                '(require-not (subpath (param "SKILL_ROOT_0")))',
                compiled.policy,
            )

    def test_shell_path_puts_current_runtime_before_system_and_bundled_tool(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-skill-path-") as directory:
            root = Path(directory)
            for child in (root / "workspace", root / "home", root / "tmp"):
                child.mkdir()
            profile = SeatbeltProfile.create(
                workspace_root=root / "workspace",
                sandbox_home=root / "home",
                sandbox_tmp=root / "tmp",
                sensitive_path=root / "workspace" / ".env",
            )
            path_entries = profile.environment()["PATH"].split(os.pathsep)

            self.assertEqual(path_entries[0], str(Path(sys.executable).parent))
            self.assertLess(path_entries.index("/opt/homebrew/bin"), path_entries.index("/usr/bin"))
            self.assertEqual(path_entries[-1], "/sbin")

    def test_missing_host_command_is_reported_as_command_not_found(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-skill-shell-") as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            metadata = workspace.stat()
            identity = WorkspaceIdentity(
                path=workspace.resolve(),
                device=metadata.st_dev,
                inode=metadata.st_ino,
                owner=metadata.st_uid,
            )

            class PassthroughProfile:
                @staticmethod
                def command(command):
                    return list(command)

                @staticmethod
                def environment():
                    return {"PATH": "/usr/bin:/bin"}

            with patch(
                "eidos_runtime.sandbox.shell.SeatbeltProfile.create",
                return_value=PassthroughProfile(),
            ):
                result = run_shell(
                    identity,
                    "host-command-that-is-not-installed scripts/run.py",
                    identity,
                    2,
                    threading.Event(),
                    lambda _delta: None,
                )

            self.assertEqual(result["code"], "nonzero_exit")
            self.assertIn("not found", result["data"]["stderr"])

    def test_shell_result_observes_implicit_invocation_in_existing_data_dict(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-skill-shell-") as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            metadata = workspace.stat()
            identity = WorkspaceIdentity(
                path=workspace.resolve(),
                device=metadata.st_dev,
                inode=metadata.st_ino,
                owner=metadata.st_uid,
            )
            record = SkillAccessRecord(
                qualified_id="user:review",
                canonical_root=workspace.resolve(),
                source="eidos-user",
                provenance={"version": "local"},
                content_hash="0" * 64,
                activation_kind=SkillActivationKind.IMPLICIT,
            )

            class PassthroughProfile:
                @staticmethod
                def command(command):
                    return list(command)

                @staticmethod
                def environment():
                    return {"PATH": "/usr/bin:/bin"}

            with patch(
                "eidos_runtime.sandbox.shell.SeatbeltProfile.create",
                return_value=PassthroughProfile(),
            ):
                result = run_shell(
                    identity,
                    "true",
                    identity,
                    2,
                    threading.Event(),
                    lambda _delta: None,
                    skill_invocation=record,
                )

            self.assertEqual(result["data"]["qualifiedId"], "user:review")
            self.assertEqual(result["data"]["invocationType"], "implicit")
            self.assertEqual(result["data"]["source"], "eidos-user")


@unittest.skipUnless(
    sys.platform == "darwin" and is_seatbelt_usable(),
    "Skill Seatbelt integration requires macOS Seatbelt",
)
class SkillSeatbeltIntegrationTests(unittest.TestCase):
    def test_active_skill_root_and_workspace_have_the_required_distinct_access(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eidos-skill-seatbelt-") as directory:
            root = Path(directory)
            workspace = root / "workspace"
            data = root / "data"
            skill = data / "skills" / "review"
            outside = root / "outside"
            inactive_skill = data / "skills" / "inactive"
            for path in (
                workspace,
                skill / "scripts",
                skill / "assets",
                inactive_skill,
                outside,
            ):
                path.mkdir(parents=True)
            script = skill / "scripts" / "run.py"
            script.write_text(
                f"#!{runtime_python_executable()}\n"
                "from pathlib import Path\n"
                "Path('workspace-output.txt').write_text('ok')\n"
                "print((Path(__file__).parent.parent / 'assets' / 'asset.txt').read_text())\n",
                encoding="utf-8",
            )
            script.chmod(0o700)
            (skill / "SKILL.md").write_text("skill\n", encoding="utf-8")
            (skill / "assets" / "asset.txt").write_text("asset", encoding="utf-8")
            (workspace / ".env").write_text("secret", encoding="utf-8")
            (workspace / ".git").mkdir()
            (workspace / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
            (outside / "secret.txt").write_text("outside", encoding="utf-8")
            (inactive_skill / "secret.txt").write_text(
                "inactive", encoding="utf-8"
            )
            (root / "home").mkdir()
            (root / "tmp").mkdir()

            document = (skill / "SKILL.md").read_bytes()
            snapshot = SkillCatalogSnapshot(
                catalog_hash="0" * 64,
                entries=(SkillCatalogEntry(
                    qualified_id="user:review",
                    name="review",
                    description="Review files.",
                    source_identity="eidos-user",
                    source_version="local",
                    source_hash="source",
                    content_hash=hashlib.sha256(document).hexdigest(),
                    main_resource_locator=(skill / "SKILL.md").resolve().as_uri(),
                ),),
            )
            snapshot = snapshot.model_copy(update={"catalog_hash": snapshot.canonical_hash()})
            access = SkillAccess.from_snapshot(snapshot)
            access.activate_explicit("user:review")
            base = BasePermissionProfile.for_workspace(
                workspace_root=workspace,
                protected_paths=(data,),
            ).model_copy(update={"active_skill_roots": tuple(
                str(path) for path in access.active_roots()
            )})
            effective = materialize_effective_profile(base)
            profile = SeatbeltProfile.create(
                workspace_root=workspace,
                sandbox_home=root / "home",
                sandbox_tmp=root / "tmp",
                sensitive_path=workspace / ".env",
                effective_permissions=effective,
                active_skill_roots=access.active_roots(),
            )
            executed = run_sandboxed(profile, [str(script)], timeout_seconds=5)
            asset = run_sandboxed(profile, ["/bin/cat", str(skill / "assets" / "asset.txt")])
            root_write = run_sandboxed(profile, ["/usr/bin/touch", str(skill / "blocked.txt")])
            workspace_write = run_sandboxed(profile, ["/usr/bin/touch", str(workspace / "out.txt")])
            external_read = run_sandboxed(profile, ["/bin/cat", str(outside / "secret.txt")])
            inactive_read = run_sandboxed(
                profile, ["/bin/cat", str(inactive_skill / "secret.txt")]
            )
            env_read = run_sandboxed(profile, ["/bin/cat", str(workspace / ".env")])
            git_read = run_sandboxed(profile, ["/bin/cat", str(workspace / ".git" / "HEAD")])
            git_write = run_sandboxed(profile, ["/usr/bin/touch", str(workspace / ".git" / "blocked")])

            self.assertEqual(executed.returncode, 0, executed.stderr)
            self.assertEqual(asset.stdout, "asset")
            self.assertNotEqual(root_write.returncode, 0)
            self.assertEqual(workspace_write.returncode, 0, workspace_write.stderr)
            self.assertNotEqual(external_read.returncode, 0)
            self.assertNotEqual(inactive_read.returncode, 0)
            self.assertNotEqual(env_read.returncode, 0)
            self.assertEqual(git_read.returncode, 0, git_read.stderr)
            self.assertNotEqual(git_write.returncode, 0)
            self.assertFalse((skill / "blocked.txt").exists())
            self.assertFalse((workspace / ".git" / "blocked").exists())


if __name__ == "__main__":
    unittest.main()

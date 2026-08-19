from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from eidos_runtime.sandbox.permissions import (
    AdditionalPermissionProfile,
    BasePermissionProfile,
    FileSystemAccessMode,
    FileSystemPermissionEntry,
    NetworkPermissions,
    SandboxPermissions,
    base_permission_profile_for_workspace,
    materialize_effective_profile,
)
from eidos_runtime.sandbox.denial import (  # noqa: E402
    SandboxDenialCategory,
    detect_sandbox_denial,
)
from eidos_runtime.sandbox.seatbelt_policy import SeatbeltPolicyCompiler


class SandboxPermissionTests(unittest.TestCase):
    def test_materialization_is_canonical_and_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside"
            outside.mkdir()
            alias = root / "alias"
            alias.symlink_to(outside, target_is_directory=True)
            base = BasePermissionProfile.for_workspace(
                workspace_root=root,
                protected_paths=(root / "state",),
            )
            overlay = AdditionalPermissionProfile(
                file_system=(
                    FileSystemPermissionEntry(
                        path=str(alias),
                        access=FileSystemAccessMode.READ,
                    ),
                    FileSystemPermissionEntry(
                        path=str(outside),
                        access=FileSystemAccessMode.READ,
                    ),
                ),
                network=NetworkPermissions(enabled=True),
            )

            first = materialize_effective_profile(base, overlay)
            second = materialize_effective_profile(base, overlay)

            added = [
                entry
                for entry in first.entries
                if entry.requested_path in {str(alias), str(outside)}
            ]
            self.assertEqual(len(added), 1)
            self.assertEqual(added[0].resolved_path, str(outside.resolve()))
            self.assertTrue(first.network_enabled)
            self.assertEqual(first.profile_hash, second.profile_hash)

    def test_overlay_cannot_grant_protected_eidos_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state"
            state.mkdir()
            base = BasePermissionProfile.for_workspace(
                workspace_root=root,
                protected_paths=(state,),
            )
            overlay = AdditionalPermissionProfile(
                file_system=(
                    FileSystemPermissionEntry(
                        path=str(state),
                        access=FileSystemAccessMode.WRITE,
                    ),
                )
            )

            with self.assertRaisesRegex(ValueError, "protected"):
                materialize_effective_profile(base, overlay)

    def test_overlay_cannot_write_protected_runtime_but_can_execute_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            runtime.mkdir()
            base = BasePermissionProfile.for_workspace(
                workspace_root=root,
                protected_write_paths=(runtime,),
            )

            with self.assertRaisesRegex(ValueError, "runtime"):
                materialize_effective_profile(
                    base,
                    AdditionalPermissionProfile(file_system=(
                        FileSystemPermissionEntry(
                            path=str(runtime),
                            access=FileSystemAccessMode.WRITE,
                        ),
                    )),
                )
            materialize_effective_profile(
                base,
                AdditionalPermissionProfile(file_system=(
                    FileSystemPermissionEntry(
                        path=str(runtime),
                        access=FileSystemAccessMode.EXECUTE,
                    ),
                )),
            )

    def test_permission_mode_requires_exact_overlay_shape(self) -> None:
        empty = AdditionalPermissionProfile()
        with self.assertRaisesRegex(ValueError, "additional_permissions"):
            empty.validate_for(SandboxPermissions.WITH_ADDITIONAL_PERMISSIONS)
        with self.assertRaisesRegex(ValueError, "additional_permissions"):
            AdditionalPermissionProfile(
                network=NetworkPermissions(enabled=True)
            ).validate_for(SandboxPermissions.USE_DEFAULT)
        with self.assertRaisesRegex(ValueError, "additional_permissions"):
            empty.validate_for(SandboxPermissions.USE_DEFAULT)
        AdditionalPermissionProfile(
            network=NetworkPermissions(enabled=True)
        ).validate_for(SandboxPermissions.WITH_ADDITIONAL_PERMISSIONS)

    def test_network_none_inherits_and_false_explicitly_disables(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = BasePermissionProfile.for_workspace(
                workspace_root=Path(directory)
            ).model_copy(update={"network_enabled": True})

            inherited = materialize_effective_profile(
                base,
                AdditionalPermissionProfile(
                    network=NetworkPermissions(enabled=None)
                ),
            )
            disabled = materialize_effective_profile(
                base,
                AdditionalPermissionProfile(
                    network=NetworkPermissions(enabled=False)
                ),
            )

            self.assertTrue(inherited.network_enabled)
            self.assertFalse(disabled.network_enabled)

    def test_permission_paths_must_be_absolute(self) -> None:
        with self.assertRaises(ValidationError):
            FileSystemPermissionEntry(
                path="relative",
                access=FileSystemAccessMode.READ,
            )

    def test_dynamic_policy_uses_parameters_and_keeps_denies(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside"
            state = root / "state"
            outside.mkdir()
            state.mkdir()
            profile = materialize_effective_profile(
                BasePermissionProfile.for_workspace(
                    workspace_root=root,
                    protected_paths=(state,),
                    protected_write_paths=(root / "runtime",),
                    runtime_roots=(outside,),
                ),
                AdditionalPermissionProfile(
                    file_system=(
                        FileSystemPermissionEntry(
                            path=str(outside),
                            access=FileSystemAccessMode.EXECUTE,
                        ),
                    ),
                    network=NetworkPermissions(enabled=True),
                ),
            )

            compiled = SeatbeltPolicyCompiler().compile(profile)

            self.assertNotIn(str(outside), compiled.policy)
            self.assertIn("file-map-executable", compiled.policy)
            self.assertIn("(allow network-outbound)", compiled.policy)
            self.assertIn("PERMANENT_DENY_0", compiled.policy)
            self.assertIn("PROTECTED_WRITE_0", compiled.policy)
            self.assertIn("RUNTIME_ROOT_0", compiled.policy)
            self.assertIn(str(outside.resolve()), compiled.parameters.values())

    def test_dynamic_policy_keeps_data_protected_outside_managed_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / "data"
            workspace = data / ".eidos-worktrees" / "wt_1"
            workspace.mkdir(parents=True)
            profile = materialize_effective_profile(
                base_permission_profile_for_workspace(workspace, data)
            )

            compiled = SeatbeltPolicyCompiler().compile(profile)

            self.assertIn("WORKSPACE_ROOT_0", compiled.parameters)
            self.assertIn(
                '(require-not (subpath (param "WORKSPACE_ROOT_0")))',
                compiled.policy,
            )

    def test_denial_detection_does_not_escalate_generic_permission_failure(self) -> None:
        ordinary = detect_sandbox_denial(
            sandboxed=True,
            exit_code=1,
            stdout="",
            stderr="application data: Permission denied",
        )
        network = detect_sandbox_denial(
            sandboxed=True,
            exit_code=1,
            stdout="",
            stderr="connect socket: Operation not permitted",
        )

        self.assertIsNone(ordinary)
        self.assertEqual(network.category, SandboxDenialCategory.NETWORK)


if __name__ == "__main__":
    unittest.main()

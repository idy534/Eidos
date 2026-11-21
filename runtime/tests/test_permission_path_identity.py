"""Permission paths are bound to one on-disk location from request to Seatbelt."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from eidos_runtime.infrastructure import local_files
from eidos_runtime.runtime.permission_policy import PermissionPolicyEvaluator
from eidos_runtime.sandbox.permissions import (
    AdditionalPermissionProfile,
    base_permission_profile_for_workspace,
    canonical_permissions,
    current_grants,
    materialize_effective_profile,
)
from eidos_runtime.tools.workspace import (
    WorkspacePathError,
    reject_protected_metadata_spelling,
    require_canonical_write_target,
)


def _case_insensitive(path: Path) -> bool:
    probe = path / "CaseProbe"
    probe.mkdir()
    try:
        return (path / "caseprobe").exists()
    finally:
        probe.rmdir()


def _layout(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path.resolve()
    data, workspace = root / "home" / ".eidos", root / "home" / "proj"
    (data / "skills" / ".system").mkdir(parents=True)
    workspace.mkdir()
    (data / "state.db").write_text("private\n")
    return data, workspace


def _write(path: str) -> AdditionalPermissionProfile:
    return AdditionalPermissionProfile.model_validate_json(json.dumps(
        {"fileSystem": [{"path": path, "access": "write"}]}
    ))


def test_case_variant_of_private_data_keeps_human_confirmation(tmp_path):
    data, workspace = _layout(tmp_path)
    if not _case_insensitive(data.parent):
        pytest.skip("case-sensitive volume has no case aliases")
    base = base_permission_profile_for_workspace(workspace, data)
    variant = str(data).replace("/.eidos", "/.EIDOS")

    canonical = canonical_permissions(_write(variant))

    assert canonical is not None and canonical.file_system[0].path == str(data)
    decision = PermissionPolicyEvaluator().evaluate(base, None, canonical)
    assert decision.requires_human_confirmation
    # A caller that skips canonicalization cannot reach Seatbelt either.
    with pytest.raises(ValueError, match="changed after approval"):
        materialize_effective_profile(base, _write(variant), allow_protected=True)


def test_grant_directory_replaced_by_link_no_longer_materializes(tmp_path):
    data, workspace = _layout(tmp_path)
    scratch = data.parent / "scratch"
    scratch.mkdir()
    base = base_permission_profile_for_workspace(workspace, data)
    grant = canonical_permissions(_write(str(scratch)))
    assert grant is not None
    assert not PermissionPolicyEvaluator().evaluate(base, None, grant).requires_human_confirmation
    assert materialize_effective_profile(base, grant, allow_protected=True)

    scratch.rmdir()
    scratch.symlink_to(data, target_is_directory=True)

    with pytest.raises(ValueError, match="changed after approval"):
        materialize_effective_profile(base, grant, allow_protected=True)
    # Run-time readers drop the stale grant instead of failing every command.
    current = current_grants(grant)
    assert current is not None and current.file_system == ()
    effective = materialize_effective_profile(base, current, allow_protected=True)
    assert not any(entry.source == "additional" for entry in effective.entries)
    kept = current_grants(canonical_permissions(_write(str(workspace))))
    assert kept is not None and kept.file_system
    assert current_grants(None) is None


def test_link_to_private_data_is_evaluated_at_its_target(tmp_path):
    data, workspace = _layout(tmp_path)
    alias = tmp_path.resolve() / "alias"
    alias.symlink_to(data, target_is_directory=True)
    base = base_permission_profile_for_workspace(workspace, data)

    canonical = canonical_permissions(_write(str(alias)))

    assert canonical is not None
    assert PermissionPolicyEvaluator().evaluate(base, None, canonical).requires_human_confirmation


def test_canonical_path_lists_unreadable_directories(tmp_path):
    hidden = tmp_path.resolve() / "Hidden"
    hidden.mkdir(mode=0o000)
    try:
        spelled = tmp_path.resolve() / ("hidden" if _case_insensitive(tmp_path.resolve()) else "Hidden")
        assert local_files.canonical_path(spelled) == hidden
    finally:
        hidden.chmod(0o700)


def test_external_write_target_requires_disk_spelling(tmp_path):
    data, _workspace = _layout(tmp_path)
    require_canonical_write_target(data / "notes" / "new.md")
    if not _case_insensitive(data.parent):
        return
    with pytest.raises(WorkspacePathError, match="workspace_boundary_violation"):
        require_canonical_write_target(Path(str(data).replace("/.eidos", "/.EIDOS")) / "state.db")


@pytest.mark.parametrize("value", [".GIT/hooks/pre-commit", "src/.Eidos/hooks.json", ".AGENTS/x"])
def test_metadata_aliases_are_rejected_before_authorization(value):
    with pytest.raises(WorkspacePathError, match="workspace_boundary_violation"):
        reject_protected_metadata_spelling(Path(value).parts)


def test_exact_metadata_and_ordinary_names_pass_spelling_check():
    reject_protected_metadata_spelling(Path(".git/hooks/pre-commit").parts)
    reject_protected_metadata_spelling(Path("docs/Git/notes.md").parts)

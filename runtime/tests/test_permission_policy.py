import pytest

from eidos_runtime.runtime.permission_policy import PermissionPolicyEvaluator
from eidos_runtime.sandbox.permissions import (
    AdditionalPermissionProfile, BasePermissionProfile, FileSystemAccessMode,
    FileSystemPermissionEntry, NetworkPermissions, base_permission_profile_for_workspace,
    materialize_effective_profile,
    unsandboxed_execution_allowed,
)
from eidos_runtime.sandbox.seatbelt_policy import SeatbeltPolicyCompiler


def test_network_policy_and_existing_grant(tmp_path):
    base = BasePermissionProfile.for_workspace(workspace_root=tmp_path)
    network = AdditionalPermissionProfile(network=NetworkPermissions(enabled=True))
    policy = PermissionPolicyEvaluator()
    assert policy.evaluate(base, None, network).disposition == 'ask'
    assert policy.evaluate(base, network, network).disposition == 'allow'


def test_protected_paths_and_unsandboxed_constraints(tmp_path):
    secret = tmp_path / 'secret'
    secret.mkdir()
    base = BasePermissionProfile.for_workspace(
        workspace_root=tmp_path, hard_confidentiality_paths=(secret,),
    )
    requested = AdditionalPermissionProfile(fileSystem=(FileSystemPermissionEntry(
        path=str(secret), access=FileSystemAccessMode.READ,
    ),))
    policy = PermissionPolicyEvaluator()
    assert policy.evaluate(base, None, requested).disposition == 'deny'
    assert policy.evaluate(base, None, None, unsandboxed=True).disposition == 'deny'
    assert policy.evaluate(base, None, None).disposition == 'allow'


def test_permission_tool_contract_rejects_empty_requests():
    from eidos_runtime.tools.request_permissions import request_permissions_entry
    entry = request_permissions_entry()
    assert not entry.validate_arguments({'permissions': {}}).valid
    assert not entry.validate_arguments({'permissions': {'network': {'enabled': False}}}).valid
    assert entry.validate_arguments({'permissions': {'network': {'enabled': True}}}).valid


def test_filesystem_policy_respects_path_and_recursion(tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    base = BasePermissionProfile.for_workspace(workspace_root=workspace)
    requested = AdditionalPermissionProfile(fileSystem=(FileSystemPermissionEntry(
        path=str(outside), access=FileSystemAccessMode.WRITE,
    ),))
    policy = PermissionPolicyEvaluator()
    assert policy.evaluate(base, None, requested).disposition == 'ask'
    assert policy.evaluate(base, requested, requested).disposition == 'allow'


@pytest.mark.parametrize('active', [False, True])
def test_user_skill_write_requires_approval_then_accepts_run_grant(tmp_path, active):
    data = tmp_path / '.eidos'
    workspace = data / '..eidos-projectless' / 'session'
    skill = data / 'skills' / 'review'
    workspace.mkdir(parents=True)
    skill.mkdir(parents=True)
    target = skill / 'SKILL.md'
    target.write_text('old skill\n')
    base = base_permission_profile_for_workspace(workspace, data)
    if active:
        base = base.model_copy(update={'active_skill_roots': (str(skill.resolve()),)})
    requested = AdditionalPermissionProfile(fileSystem=(FileSystemPermissionEntry(
        path=str(target.resolve()), access=FileSystemAccessMode.WRITE, recursive=False,
    ),))
    policy = PermissionPolicyEvaluator()

    assert not materialize_effective_profile(base).allows_file_write(target.resolve())
    assert policy.evaluate(base, None, requested).disposition == 'ask'
    assert policy.evaluate(base, requested, requested).disposition == 'allow'
    effective = materialize_effective_profile(base, requested)
    assert effective.allows_file_write(target.resolve())
    assert not effective.allows_file_write((skill / 'sibling.md').resolve())
    assert not effective.allows_file_write((data / 'state.sqlite').resolve())


@pytest.mark.parametrize('workspace_is_system', [False, True])
@pytest.mark.parametrize('unsandboxed', [False, True])
def test_system_skill_is_denied_even_with_grant_or_workspace_identity(
    tmp_path, workspace_is_system, unsandboxed,
):
    data = tmp_path / '.eidos'
    system = data / 'skills' / '.system'
    workspace = system if workspace_is_system else data / '..eidos-projectless' / 'session'
    system.mkdir(parents=True)
    workspace.mkdir(parents=True, exist_ok=True)
    target = system / 'SKILL.md'
    target.write_text('system skill\n')
    base = base_permission_profile_for_workspace(workspace, data)
    requested = AdditionalPermissionProfile(fileSystem=(FileSystemPermissionEntry(
        path=str(target.resolve()), access=FileSystemAccessMode.WRITE, recursive=False,
    ),))

    assert PermissionPolicyEvaluator().evaluate(
        base, requested, requested, unsandboxed=unsandboxed,
    ).disposition == 'deny'
    assert not materialize_effective_profile(base).allows_file_write(target.resolve())
    with pytest.raises(ValueError):
        materialize_effective_profile(base, requested)


@pytest.mark.parametrize('relative', ['state.sqlite', 'skills', 'skills/.system'])
def test_data_root_and_skill_container_cannot_be_broadly_granted(tmp_path, relative):
    data = tmp_path / '.eidos'
    workspace = data / '..eidos-projectless' / 'session'
    workspace.mkdir(parents=True)
    target = data / relative
    if relative == 'state.sqlite':
        target.write_text('protected state\n')
    else:
        target.mkdir(parents=True, exist_ok=True)
    base = base_permission_profile_for_workspace(workspace, data)
    requested = AdditionalPermissionProfile(fileSystem=(FileSystemPermissionEntry(
        path=str(target.resolve()), access=FileSystemAccessMode.WRITE,
    ),))

    assert PermissionPolicyEvaluator().evaluate(base, requested, requested).disposition == 'deny'
    with pytest.raises(ValueError):
        materialize_effective_profile(base, requested)


def test_unsandboxed_process_cannot_bypass_system_write_protection(tmp_path):
    data = tmp_path / '.eidos'
    workspace = data / '..eidos-projectless' / 'session'
    workspace.mkdir(parents=True)
    base = base_permission_profile_for_workspace(workspace, data)
    effective = materialize_effective_profile(base)

    assert not unsandboxed_execution_allowed(effective)
    assert unsandboxed_execution_allowed(effective, controlled_file_write=True)
    assert not effective.allows_file_write(data / 'skills' / '.system' / 'SKILL.md')
    confidential = workspace / 'secret'
    confidential.mkdir()
    hard_base = BasePermissionProfile.for_workspace(
        workspace_root=workspace, hard_confidentiality_paths=(confidential,),
    )
    assert not unsandboxed_execution_allowed(
        materialize_effective_profile(hard_base), controlled_file_write=True,
    )


def test_hard_deny_has_no_workspace_or_active_skill_exception(tmp_path):
    data = tmp_path / 'confidential'
    skill = data / 'skills' / 'review'
    skill.mkdir(parents=True)
    target = skill / 'SKILL.md'
    target.write_text('confidential\n')
    base = BasePermissionProfile.for_workspace(
        workspace_root=skill, hard_confidentiality_paths=(data,), active_skill_roots=(skill,),
    )
    effective = materialize_effective_profile(base)
    assert not effective.allows_file_write(target)
    compiled = SeatbeltPolicyCompiler().compile(effective)
    assert '(deny file-read* file-write* file-map-executable file-test-existence (subpath (param "PERMANENT_DENY_0")))' in compiled.policy
    assert 'WORKSPACE_ROOT_0' not in compiled.parameters
    requested = AdditionalPermissionProfile(fileSystem=(FileSystemPermissionEntry(
        path=str(target), access=FileSystemAccessMode.WRITE, recursive=False,
    ),))
    assert PermissionPolicyEvaluator().evaluate(base, requested, requested).disposition == 'deny'
    with pytest.raises(ValueError):
        materialize_effective_profile(base, requested)

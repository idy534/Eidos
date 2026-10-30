import pytest

from eidos_runtime.runtime.permission_policy import PermissionPolicyEvaluator
from eidos_runtime.sandbox.permissions import (
    AdditionalPermissionProfile, BasePermissionProfile, FileSystemAccessMode,
    FileSystemPermissionEntry, NetworkPermissions, base_permission_profile_for_workspace,
    materialize_effective_profile,
    unsandboxed_execution_allowed,
    with_project_metadata_paths,
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
    assert policy.evaluate(base, None, requested).disposition == 'ask'
    assert policy.evaluate(base, None, requested).requires_human_confirmation
    assert policy.evaluate(base, None, None, unsandboxed=True).disposition == 'ask'
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


@pytest.mark.parametrize('recursive', [False, True])
def test_ordinary_host_read_is_already_allowed(tmp_path, recursive):
    workspace = tmp_path / 'workspace'
    outside = tmp_path / 'outside'
    workspace.mkdir()
    outside.mkdir()
    base = BasePermissionProfile.for_workspace(workspace_root=workspace)
    requested = AdditionalPermissionProfile(fileSystem=(FileSystemPermissionEntry(
        path=str(outside), access=FileSystemAccessMode.READ, recursive=recursive,
    ),))
    decision = PermissionPolicyEvaluator().evaluate(base, None, requested)
    assert decision.disposition == 'allow'
    assert decision.reason_code == 'already_granted'


@pytest.mark.parametrize('protection', ['protected_paths', 'hard_confidentiality_paths'])
@pytest.mark.parametrize('target', ['protected', 'ancestor'])
def test_host_read_exception_keeps_protected_scopes_requiring_confirmation(
    tmp_path, protection, target,
):
    workspace = tmp_path / 'workspace'
    private = tmp_path / 'private'
    workspace.mkdir()
    private.mkdir()
    base = BasePermissionProfile.for_workspace(
        workspace_root=workspace, **{protection: (private,)},
    )
    requested = AdditionalPermissionProfile(fileSystem=(FileSystemPermissionEntry(
        path=str(private if target == 'protected' else tmp_path),
        access=FileSystemAccessMode.READ,
    ),))
    decision = PermissionPolicyEvaluator().evaluate(base, None, requested)
    assert decision.disposition == 'ask'
    assert decision.requires_human_confirmation


@pytest.mark.parametrize('extra', ['network', 'write', 'unsandboxed'])
def test_host_read_does_not_authorize_other_permissions(tmp_path, extra):
    workspace = tmp_path / 'workspace'
    outside = tmp_path / 'outside'
    workspace.mkdir()
    outside.mkdir()
    base = BasePermissionProfile.for_workspace(workspace_root=workspace)
    entries = [FileSystemPermissionEntry(path=str(outside), access=FileSystemAccessMode.READ)]
    if extra == 'write':
        entries.append(FileSystemPermissionEntry(path=str(outside), access=FileSystemAccessMode.WRITE))
    requested = AdditionalPermissionProfile(
        fileSystem=tuple(entries),
        network=NetworkPermissions(enabled=True) if extra == 'network' else None,
    )
    decision = PermissionPolicyEvaluator().evaluate(
        base, None, requested, unsandboxed=extra == 'unsandboxed',
    )
    assert decision.disposition == 'ask'
    assert not decision.requires_human_confirmation


def test_verified_git_metadata_write_requires_approval_then_stays_scoped(tmp_path):
    workspace = tmp_path / 'workspace'
    git = workspace / '.git'
    runtime = workspace / '.eidos'
    git.mkdir(parents=True)
    runtime.mkdir()
    base = BasePermissionProfile.for_workspace(
        workspace_root=workspace,
        protected_write_paths=(git, runtime),
        approval_write_roots=(git,),
    )
    requested = AdditionalPermissionProfile(fileSystem=(FileSystemPermissionEntry(
        path=str(git), access=FileSystemAccessMode.WRITE,
    ),))
    policy = PermissionPolicyEvaluator()

    assert policy.evaluate(base, None, requested).disposition == 'ask'
    assert policy.evaluate(base, requested, requested).disposition == 'allow'
    effective = materialize_effective_profile(base, requested)
    assert effective.allows_file_write(git / 'index.lock')
    assert not effective.allows_file_write(runtime / 'state.json')


def test_arbitrary_git_metadata_write_uses_ordinary_approval(tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    outside_git = tmp_path / 'other' / '.git'
    outside_git.mkdir(parents=True)
    requested = AdditionalPermissionProfile(fileSystem=(FileSystemPermissionEntry(
        path=str(outside_git), access=FileSystemAccessMode.WRITE,
    ),))

    decision = PermissionPolicyEvaluator().evaluate(
        BasePermissionProfile.for_workspace(workspace_root=workspace),
        None,
        requested,
    )
    assert decision.disposition == 'ask'
    assert not decision.requires_human_confirmation


@pytest.mark.parametrize('name', ['.git', '.agents', '.eidos'])
def test_project_metadata_uses_scoped_ordinary_approval_and_preserves_data(tmp_path, name):
    workspace, data = tmp_path / 'workspace', tmp_path / 'data'
    target = workspace / name
    target.mkdir(parents=True)
    data.mkdir()
    state = data / 'state.sqlite'
    state.write_text('private')
    base = base_permission_profile_for_workspace(workspace, data)
    requested = AdditionalPermissionProfile(fileSystem=(FileSystemPermissionEntry(
        path=str(target), access=FileSystemAccessMode.WRITE,
    ),))
    policy = PermissionPolicyEvaluator()
    decision = policy.evaluate(base, None, requested)
    assert decision.disposition == 'ask'
    assert not decision.requires_human_confirmation
    assert policy.evaluate(base, requested, requested).disposition == 'allow'
    assert not materialize_effective_profile(base).allows_file_write(target / 'config')
    effective = materialize_effective_profile(base, requested, allow_protected=True)
    assert effective.allows_file_write(target / 'config')
    assert not effective.allows_file_write(state)
    broad = AdditionalPermissionProfile(fileSystem=(FileSystemPermissionEntry(
        path=str(tmp_path), access=FileSystemAccessMode.WRITE,
    ),))
    assert policy.evaluate(base, None, broad).requires_human_confirmation


def test_unsandboxed_mode_alone_uses_ordinary_approval(tmp_path):
    base = base_permission_profile_for_workspace(tmp_path, None)
    decision = PermissionPolicyEvaluator().evaluate(base, None, None, unsandboxed=True)
    assert decision.disposition == 'ask'
    assert not decision.requires_human_confirmation


def test_home_workspace_does_not_downgrade_the_eidos_data_directory(tmp_path):
    data = tmp_path / '.eidos'
    data.mkdir()
    target = data / 'state.sqlite'
    target.touch()
    base = base_permission_profile_for_workspace(tmp_path, data)
    assert str(data) not in base.approval_write_roots
    requested = AdditionalPermissionProfile(fileSystem=(FileSystemPermissionEntry(
        path=str(target), access=FileSystemAccessMode.WRITE, recursive=False,
    ),))
    assert PermissionPolicyEvaluator().evaluate(base, None, requested).requires_human_confirmation
    assert not materialize_effective_profile(base).allows_file_write(target)


def test_metadata_binding_cannot_downgrade_runtime_protection(tmp_path):
    workspace, runtime = tmp_path / 'workspace', tmp_path / 'runtime'
    workspace.mkdir()
    runtime.mkdir()
    base = BasePermissionProfile.for_workspace(workspace_root=workspace, protected_write_paths=(runtime,))
    updated = with_project_metadata_paths(base, (str(runtime), str(tmp_path)))
    assert updated.approval_write_roots == ()


def test_run_snapshot_keeps_linked_git_metadata_on_the_ordinary_approval_path(tmp_path):
    from eidos_runtime.model.config import default_profile_snapshot
    from eidos_runtime.runtime.resolution import WorkspaceIdentitySnapshot, create_run_resolution_snapshot

    workspace, git = tmp_path / 'workspace', tmp_path / 'repository' / '.git'
    workspace.mkdir()
    git.mkdir(parents=True)
    metadata = workspace.stat()
    snapshot = create_run_resolution_snapshot(
        run_id='run', model_profile=default_profile_snapshot('deepseek-v4-flash'),
        extension_snapshot={}, created_at=1, data_directory=tmp_path / 'data',
        workspace_identity=WorkspaceIdentitySnapshot(
            path=str(workspace), device=metadata.st_dev, inode=metadata.st_ino,
            owner=metadata.st_uid, git_dir=str(git), git_common_dir=str(git),
        ),
    )
    base = BasePermissionProfile.model_validate_json(snapshot.permission_profile_json)
    request = AdditionalPermissionProfile(fileSystem=(FileSystemPermissionEntry(
        path=str(git), access=FileSystemAccessMode.WRITE,
    ),))
    decision = PermissionPolicyEvaluator().evaluate(base, None, request)
    assert decision.disposition == 'ask'
    assert not decision.requires_human_confirmation


def test_git_approval_cannot_override_an_overlapping_unapproved_protection(tmp_path):
    workspace = tmp_path / 'workspace'
    git = workspace / '.git'
    git.mkdir(parents=True)
    base = BasePermissionProfile.for_workspace(
        workspace_root=workspace,
        protected_write_paths=(workspace, git),
        approval_write_roots=(git,),
    )
    requested = AdditionalPermissionProfile(fileSystem=(FileSystemPermissionEntry(
        path=str(git), access=FileSystemAccessMode.WRITE,
    ),))

    assert PermissionPolicyEvaluator().evaluate(
        base, None, requested,
    ).disposition == 'ask'
    with pytest.raises(ValueError):
        materialize_effective_profile(base, requested)


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
def test_system_skill_requires_confirmation_even_with_workspace_identity(
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

    decision = PermissionPolicyEvaluator().evaluate(
        base, None, requested, unsandboxed=unsandboxed,
    )
    assert decision.disposition == 'ask'
    assert decision.requires_human_confirmation
    assert not materialize_effective_profile(base).allows_file_write(target.resolve())
    with pytest.raises(ValueError):
        materialize_effective_profile(base, requested)


@pytest.mark.parametrize('relative', ['state.sqlite', 'skills', 'skills/.system'])
def test_data_root_and_skill_container_require_human_confirmation(tmp_path, relative):
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

    decision = PermissionPolicyEvaluator().evaluate(base, None, requested)
    assert decision.disposition == 'ask'
    assert decision.requires_human_confirmation
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
    assert PermissionPolicyEvaluator().evaluate(base, None, requested).disposition == 'ask'
    with pytest.raises(ValueError):
        materialize_effective_profile(base, requested)

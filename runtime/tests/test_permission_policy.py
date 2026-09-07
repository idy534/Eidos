from eidos_runtime.runtime.permission_policy import PermissionPolicyEvaluator
from eidos_runtime.sandbox.permissions import (
    AdditionalPermissionProfile, BasePermissionProfile, FileSystemAccessMode,
    FileSystemPermissionEntry, NetworkPermissions,
)


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

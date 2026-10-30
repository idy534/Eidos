from __future__ import annotations

import json
from pathlib import Path
import threading
import subprocess
import sys

import pytest

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel
from eidos_runtime.runtime.approval import ApprovalDecision
from eidos_runtime.runtime.engine import RuntimeEngine
from eidos_runtime.runtime.run_resources import RunResources
from eidos_runtime.sandbox.permissions import (
    AdditionalPermissionProfile, BasePermissionProfile, FileSystemAccessMode,
    FileSystemPermissionEntry, materialize_effective_profile,
    base_permission_profile_for_workspace,
)
from eidos_runtime.sandbox.seatbelt_policy import SeatbeltPolicyCompiler
from eidos_runtime.sandbox.seatbelt import SeatbeltProfile, is_seatbelt_usable


def fixture(tmp_path: Path, mode: str):
    data, workspace = tmp_path / 'data', tmp_path / 'workspace'
    data.mkdir(mode=0o700)
    workspace.mkdir()
    store = SessionStore(data)
    store.initialize()
    session = store.create_session(str(workspace))
    run, _ = store.create_run(session['id'], 'Update the system skill', approval_mode=mode)
    target = data / 'skills' / '.system' / 'SKILL.md'
    target.parent.mkdir(parents=True)
    target.write_text('old\n')
    return store, run, target


def patch_model(target: Path):
    return ScriptedModel([
        ModelResponse(tool_calls=(ModelToolCall('edit', 'apply_patch', {'patch':
            f'*** Begin Patch\n*** Update File: {target}\n@@\n-old\n+new\n*** End Patch'}),)),
        ModelResponse(text='done'),
    ])


@pytest.mark.parametrize('mode', ['manual', 'auto_review'])
@pytest.mark.parametrize('decision', ['approve', 'reject'])
def test_system_skill_uses_human_confirmation_and_executes_only_after_approval(tmp_path, monkeypatch, mode, decision):
    store, run, target = fixture(tmp_path, mode)
    monkeypatch.setattr('eidos_runtime.runtime.tool_runtime.is_seatbelt_ready', lambda: False)
    requests = []

    def confirm(request, cancel):
        requests.append(request)
        assert request['requiresHumanConfirmation'] is True
        assert request['riskWarning']
        assert target.read_text() == 'old\n'
        assert store.connection.execute('SELECT count(*) FROM durable_intents').fetchone()[0] == 0
        return ApprovalDecision(decision)

    try:
        RuntimeEngine(store, patch_model(target), lambda _: None, request_approval=confirm).run(run['id'], threading.Event())
        assert len(requests) == 1
        assert target.read_text() == ('new\n' if decision == 'approve' else 'old\n')
        row = store.connection.execute('SELECT status,review_json FROM approvals').fetchone()
        assert row['status'] == ('approved' if decision == 'approve' else 'rejected')
        if mode == 'auto_review':
            assert json.loads(row['review_json'])['source'] == 'manual'
        assert store.read_run(run['id'])['status'] == 'succeeded'
    finally:
        store.close()


def test_dangerous_approval_rechecks_file_version(tmp_path, monkeypatch):
    store, run, target = fixture(tmp_path, 'auto_review')
    monkeypatch.setattr('eidos_runtime.runtime.tool_runtime.is_seatbelt_ready', lambda: False)

    def confirm(request, cancel):
        target.write_text('concurrent edit\n')
        return ApprovalDecision('approve')

    try:
        RuntimeEngine(store, patch_model(target), lambda _: None, request_approval=confirm).run(run['id'], threading.Event())
        result = json.loads(store.connection.execute('SELECT result_json FROM tool_calls').fetchone()[0])
        assert result['code'] == 'file_version_conflict'
        assert result['sideEffectsMayExist'] is False
        assert target.read_text() == 'concurrent edit\n'
    finally:
        store.close()


def test_dangerous_approval_recovers_with_a_human_decision(tmp_path, monkeypatch):
    class Crash(BaseException):
        pass

    store, run, target = fixture(tmp_path, 'auto_review')
    monkeypatch.setattr('eidos_runtime.runtime.tool_runtime.is_seatbelt_ready', lambda: False)
    def crash(request, cancel):
        raise Crash()
    try:
        with pytest.raises(Crash):
            RuntimeEngine(store, patch_model(target), lambda _: None, request_approval=crash).run(run['id'], threading.Event())
        assert target.read_text() == 'old\n'
        confirmations = []
        def confirm(request, cancel):
            confirmations.append(request)
            return ApprovalDecision('approve')
        RuntimeEngine(store, ScriptedModel([ModelResponse(text='done')]), lambda _: None, request_approval=confirm).run(run['id'], threading.Event())
        assert len(confirmations) == 1
        assert target.read_text() == 'new\n'
        assert store.connection.execute('SELECT count(*) FROM approvals').fetchone()[0] == 1
    finally:
        store.close()


def test_full_access_file_effect_creates_intent_without_any_approval(tmp_path):
    store, run, target = fixture(tmp_path, 'full_access')
    def unexpected(request, cancel):
        raise AssertionError('Full access must execute directly')
    try:
        RuntimeEngine(store, patch_model(target), lambda _: None, request_approval=unexpected).run(run['id'], threading.Event())
        assert target.read_text() == 'new\n'
        assert store.connection.execute('SELECT count(*) FROM approvals').fetchone()[0] == 0
        assert store.connection.execute('SELECT count(*) FROM durable_intents').fetchone()[0] == 1
        preconditions = json.loads(store.connection.execute('SELECT preconditions_json FROM durable_intents').fetchone()[0])
        assert 'authorization' not in preconditions
        assert store.read_run(run['id'])['status'] == 'succeeded'
    finally:
        store.close()


@pytest.mark.parametrize('mode', ['manual', 'full_access'])
def test_permission_request_tool_is_hidden_in_full_access(tmp_path, mode):
    store, run, _target = fixture(tmp_path, mode)
    try:
        with RunResources(store, run['id'], store.read_run_extension_snapshot(run['id']), '') as resources:
            names = {entry.spec.name for entry in resources.registry.entries}
            assert ('request_permissions' in names) == (mode == 'manual')
    finally:
        store.close()


@pytest.mark.parametrize('permissions', [{'networkAccess': 'request'}, {'gitWriteAccess': 'request'}, {'sandboxPermissions': 'require_escalated'}])
def test_full_access_shell_ignores_legacy_permission_requests(tmp_path, monkeypatch, permissions):
    store, run, _target = fixture(tmp_path, 'full_access')
    attempts = []
    def shell(_manager, launch, **kwargs):
        attempts.append(launch)
        return {'schemaVersion': 1, 'toolName': 'run_shell', 'outcome': 'success', 'code': 'ok', 'summary': 'done',
                'data': {'exitCode': 0, 'stdout': '', 'stderr': '', 'truncated': False, 'termination': 'exit',
                         'executionStatus': 'exited', 'workspaceChanged': False},
                'sideEffectsMayExist': False, 'reconciliationRequired': False}
    monkeypatch.setattr('eidos_runtime.runtime.shell_process_manager.ShellProcessManager.start', shell)
    model = ScriptedModel([ModelResponse(tool_calls=(ModelToolCall('shell', 'run_shell', {
        'command': 'true', 'justification': 'legacy request', **permissions}),)), ModelResponse(text='done')])
    try:
        RuntimeEngine(store, model, lambda _: None, shell_available=True).run(run['id'], threading.Event())
        assert len(attempts) == 1
        assert store.connection.execute('SELECT count(*) FROM approvals').fetchone()[0] == 0
        assert store.connection.execute('SELECT sandbox_type FROM tool_attempts').fetchone()[0] == 'none'
        assert store.read_run(run['id'])['status'] == 'succeeded'
    finally:
        store.close()


def test_protected_override_is_scoped_and_changes_the_profile_hash(tmp_path):
    secret = tmp_path / 'secret'
    secret.mkdir()
    target, sibling = secret / 'target', secret / 'sibling'
    target.touch()
    sibling.touch()
    base = BasePermissionProfile.for_workspace(workspace_root=tmp_path, hard_confidentiality_paths=(secret,))
    grant = AdditionalPermissionProfile(fileSystem=(FileSystemPermissionEntry(
        path=str(target), access=FileSystemAccessMode.WRITE, recursive=False),))
    original = materialize_effective_profile(base)
    override = materialize_effective_profile(base, grant, allow_protected=True)
    assert override.allows_file_write(target)
    assert not override.allows_file_write(sibling)
    assert override.profile_hash != original.profile_hash
    assert 'require-not (literal (param "ADDITIONAL_WRITE_0"))' in SeatbeltPolicyCompiler().compile(override).policy


@pytest.mark.skipif(sys.platform != 'darwin', reason='Native macOS Seatbelt required')
def test_native_protected_override_allows_only_the_confirmed_file(tmp_path):
    assert is_seatbelt_usable(), 'Native Seatbelt could not start'
    workspace, data, home, scratch = (tmp_path / name for name in ('workspace', 'data', 'home', 'scratch'))
    for root in (workspace, data, home, scratch):
        root.mkdir()
    target, sibling = data / 'target', data / 'sibling'
    target.write_text('old')
    sibling.write_text('old')
    base = BasePermissionProfile.for_workspace(workspace_root=workspace, protected_paths=(data,))
    grant = AdditionalPermissionProfile(fileSystem=(FileSystemPermissionEntry(
        path=str(target), access=FileSystemAccessMode.WRITE, recursive=False),))
    def execute(effective, path):
        profile = SeatbeltProfile.create(workspace_root=workspace, sandbox_home=home,
                                        sandbox_tmp=scratch, effective_permissions=effective)
        return subprocess.run(profile.command(['/bin/sh', '-c', 'printf changed > "$1"', 'sh', str(path)]),
                              capture_output=True, text=True, timeout=10)
    assert execute(materialize_effective_profile(base), target).returncode != 0
    effective = materialize_effective_profile(base, grant, allow_protected=True)
    assert execute(effective, target).returncode == 0
    assert target.read_text() == 'changed'
    assert execute(effective, sibling).returncode != 0
    assert sibling.read_text() == 'old'


def test_cancel_during_human_confirmation_does_not_commit(tmp_path, monkeypatch):
    store, run, target = fixture(tmp_path, 'auto_review')
    monkeypatch.setattr('eidos_runtime.runtime.tool_runtime.is_seatbelt_ready', lambda: False)
    cancel = threading.Event()
    def confirm(request, _cancel):
        cancel.set()
        return ApprovalDecision('approve')
    try:
        RuntimeEngine(store, patch_model(target), lambda _: None, request_approval=confirm).run(run['id'], cancel)
        assert target.read_text() == 'old\n'
        assert store.connection.execute('SELECT count(*) FROM durable_intents').fetchone()[0] == 0
        assert store.read_run(run['id'])['status'] == 'canceled'
    finally:
        store.close()


@pytest.mark.skipif(sys.platform != 'darwin', reason='Native macOS Seatbelt required')
def test_native_ordinary_git_grant_keeps_eidos_data_denied(tmp_path):
    assert is_seatbelt_usable(), 'Native Seatbelt could not start'
    workspace, data, home, scratch = (tmp_path / name for name in ('workspace', 'data', 'home', 'scratch'))
    for root in (workspace, data, home, scratch):
        root.mkdir()
    git = workspace / '.git'
    git.mkdir()
    state = data / 'state.sqlite'
    state.write_text('private')
    base = base_permission_profile_for_workspace(workspace, data)
    grant = AdditionalPermissionProfile(fileSystem=(FileSystemPermissionEntry(
        path=str(git), access=FileSystemAccessMode.WRITE,
    ),))
    from eidos_runtime.runtime.permission_policy import PermissionPolicyEvaluator
    assert not PermissionPolicyEvaluator().evaluate(base, None, grant).requires_human_confirmation

    def execute(effective, command, path):
        profile = SeatbeltProfile.create(workspace_root=workspace, sandbox_home=home,
                                        sandbox_tmp=scratch, effective_permissions=effective)
        return subprocess.run(profile.command(['/bin/sh', '-c', command, 'sh', str(path)]),
                              capture_output=True, text=True, timeout=10)

    target = git / 'index.lock'
    assert execute(materialize_effective_profile(base), 'printf ready > "$1"', target).returncode != 0
    effective = materialize_effective_profile(base, grant, allow_protected=True)
    assert execute(effective, 'printf ready > "$1"', target).returncode == 0
    assert target.read_text() == 'ready'
    assert execute(effective, 'cat "$1"', state).returncode != 0
    assert execute(effective, 'printf broken > "$1"', state).returncode != 0
    assert state.read_text() == 'private'

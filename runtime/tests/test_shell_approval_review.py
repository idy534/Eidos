from __future__ import annotations

import json
import threading

import pytest

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel
from eidos_runtime.model.config import default_profile_snapshot
from eidos_runtime.runtime.engine import RuntimeEngine
from eidos_runtime.runtime.approval import ApprovalDecision
from eidos_runtime.runtime.permission_policy import PermissionPolicyEvaluator
from eidos_runtime.runtime.shell_orchestration import collect_shell_review_scripts
from eidos_runtime.workspace.reader import capture_workspace_identity


@pytest.mark.parametrize('change', ['none', 'script', 'cancel', 'reject'])
def test_unsandboxed_script_uses_auto_review_before_launch(tmp_path, monkeypatch, change):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    script = workspace / 'render.sh'
    script.write_text('printf ready\n')
    store = SessionStore(tmp_path / 'data')
    store.initialize()
    cancel = threading.Event()
    launches = []

    def start(_manager, launch, **_kwargs):
        launches.append(launch)
        return {'schemaVersion': 1, 'toolName': 'run_shell', 'outcome': 'success', 'code': 'ok',
                'summary': 'done', 'data': {'exitCode': 0, 'stdout': '', 'stderr': '',
                'truncated': False, 'termination': 'exit', 'executionStatus': 'exited',
                'workspaceChanged': False}, 'sideEffectsMayExist': False, 'reconciliationRequired': False}

    monkeypatch.setattr('eidos_runtime.runtime.shell_process_manager.ShellProcessManager.start', start)
    model = ScriptedModel([
        ModelResponse(tool_calls=(ModelToolCall('shell', 'run_shell', {
            'command': 'sh ./render.sh', 'sandboxPermissions': 'require_escalated',
            'justification': 'Render the requested document',
        }),)),
        ModelResponse(tool_calls=(ModelToolCall('assessment', 'submit_approval_assessment', {
            'outcome': 'deny' if change == 'reject' else 'allow', 'risk': 'medium',
            'rationale': 'The script only generates local output.' if change != 'reject' else 'The action was not authorized.',
        }),)),
        ModelResponse(text='done'),
    ])
    model.profile_snapshot = default_profile_snapshot('deepseek-v4-flash')
    original = model.complete

    def complete(*args, **kwargs):
        response = original(*args, **kwargs)
        if any(tool.name == 'submit_approval_assessment' for tool in kwargs.get('tool_definitions', ())):
            if change == 'script':
                script.write_text('printf changed\n')
            if change == 'cancel':
                cancel.set()
        return response

    model.complete = complete

    def unexpected(*_args):
        pytest.fail('Ordinary auto review must not ask the user')

    try:
        session = store.create_session(str(workspace))
        run, _ = store.create_run(session['id'], 'Render my document', approval_mode='auto_review')
        RuntimeEngine(store, model, lambda _: None, request_approval=unexpected, shell_available=True).run(run['id'], cancel)
        assert len(launches) == (1 if change == 'none' else 0)
        request = json.loads(store.connection.execute('SELECT request_json FROM approvals').fetchone()[0])
        assert not request.get('requiresHumanConfirmation')
        assert request['sandboxEnforced'] is False
        assert request['reviewScriptEvidence'][0]['content'] == 'printf ready\n'
        assert request['protectedEidosPaths']['privateData'] == [str(tmp_path / 'data')]
        assert str(workspace / '.git') not in request['protectedEidosPaths']['writeProtected']
        if change == 'cancel':
            assert store.read_run(run['id'])['status'] == 'canceled'
            assert store.connection.execute('SELECT count(*) FROM durable_intents').fetchone()[0] == 0
        else:
            review = json.loads(store.connection.execute('SELECT review_json FROM approvals').fetchone()[0])
            assert review['source'] == 'model'
            assert review['decision'] == ('reject' if change == 'reject' else 'approve')
            if change == 'script':
                result = json.loads(store.connection.execute('SELECT result_json FROM tool_calls').fetchone()[0])
                assert result['code'] == 'file_version_conflict'
            assert store.read_run(run['id'])['status'] == 'succeeded'
    finally:
        store.close()


def test_script_evidence_is_bounded_and_cannot_follow_links_or_read_other_roots(tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    outside = tmp_path / 'secret.py'
    outside.write_text('private data')
    (workspace / 'link.py').symlink_to(outside)
    (workspace / 'large.py').write_text('x' * 9000)
    (workspace / 'fourth.py').write_text('not included')
    scripts = collect_shell_review_scripts(
        f'python link.py; python {outside}; python large.py; python fourth.py',
        workspace, (capture_workspace_identity(workspace),),
    )
    assert len(scripts) == 3
    assert scripts[0].content is None
    assert scripts[1].content is None and scripts[1].error == 'outside_review_roots'
    assert len(scripts[2].content.encode()) == 8 * 1024
    assert scripts[2].truncated
    assert all('private data' not in (script.content or '') for script in scripts)


@pytest.mark.parametrize('decision', ['approve', 'reject'])
def test_pending_legacy_unsandboxed_approval_keeps_the_human_decision_after_restart(tmp_path, monkeypatch, decision):
    class Crash(BaseException):
        pass

    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    (workspace / 'render.sh').write_text('printf ready\n')
    data = tmp_path / 'data'
    store = SessionStore(data)
    store.initialize()
    original = PermissionPolicyEvaluator.evaluate
    launches = []

    def legacy(policy, *args, **kwargs):
        result = original(policy, *args, **kwargs)
        return result.model_copy(update={'reason_code': 'dangerous_permission_requires_confirmation'}) if kwargs.get('unsandboxed') else result

    def start(_manager, launch, **_kwargs):
        launches.append(launch)
        return {'outcome': 'success', 'code': 'ok', 'summary': 'done', 'data': {
            'exitCode': 0, 'stdout': '', 'stderr': '', 'executionStatus': 'exited'},
            'sideEffectsMayExist': False, 'reconciliationRequired': False}

    monkeypatch.setattr('eidos_runtime.runtime.shell_process_manager.ShellProcessManager.start', start)
    try:
        session = store.create_session(str(workspace))
        run, _ = store.create_run(session['id'], 'Render my document', approval_mode='auto_review')
        with monkeypatch.context() as old_policy:
            old_policy.setattr(PermissionPolicyEvaluator, 'evaluate', legacy)
            model = ScriptedModel([ModelResponse(tool_calls=(ModelToolCall('shell', 'run_shell', {
                'command': 'sh ./render.sh', 'sandboxPermissions': 'require_escalated',
                'justification': 'Render the document',
            }),))])
            with pytest.raises(Crash):
                RuntimeEngine(store, model, lambda _: None, shell_available=True,
                              request_approval=lambda *_: (_ for _ in ()).throw(Crash())).run(run['id'], threading.Event())
        pending = store.typed_runtime_repository().read_pending_approval(run['id'])
        store.close()
        store = SessionStore(data)
        store.initialize()
        requests = []
        RuntimeEngine(
            store, ScriptedModel([ModelResponse(text='done')]), lambda _: None, shell_available=True,
            request_approval=lambda request, _cancel: requests.append(request) or ApprovalDecision(decision),
        ).run(run['id'], threading.Event())
        assert len(requests) == 1 and requests[0]['requiresHumanConfirmation']
        assert len(launches) == (1 if decision == 'approve' else 0)
        row = store.connection.execute('SELECT id,decision,review_json FROM approvals').fetchone()
        assert (row['id'], row['decision']) == (pending.id, decision)
        assert json.loads(row['review_json'])['source'] == 'manual'
        assert store.read_run(run['id'])['status'] == 'succeeded'
    finally:
        store.close()

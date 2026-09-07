import json
import threading

import pytest

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel
from eidos_runtime.runtime.approval import ApprovalDecision
from eidos_runtime.runtime.engine import RuntimeEngine


@pytest.mark.parametrize('decision', ['approve', 'reject'])
def test_permission_request_round_trip(tmp_path, decision):
    data = tmp_path / 'data'
    data.mkdir(mode=0o700)
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    store = SessionStore(data)
    store.initialize()
    try:
        session = store.create_session(str(workspace))
        run, _ = store.create_run(session['id'], 'request network')
        requests = []
        def approve(request, cancel):
            requests.append(request)
            assert store.read_run(run['id'])['status'] == 'waiting_approval'
            assert store.read_session_snapshot(session['id'])['session']['activeRunStatus'] == 'waiting_approval'
            return ApprovalDecision(decision)
        model = ScriptedModel([
            ModelResponse(tool_calls=(ModelToolCall('p1', 'request_permissions', {'permissions': {'network': {'enabled': True}}}),)),
            ModelResponse(tool_calls=(ModelToolCall('p2', 'request_permissions', {'reason': 'Continue task', 'permissions': {'network': {'enabled': True}}}),)),
            ModelResponse(text='done'),
        ])
        RuntimeEngine(store, model, lambda message: None, request_approval=approve).run(run['id'], threading.Event())
        assert len(requests) == 1
        assert store.read_run(run['id'])['status'] == 'succeeded'
        rows = store.connection.execute("SELECT result_json FROM tool_calls ORDER BY creation_seq").fetchall()
        assert [json.loads(row[0])['code'] for row in rows] == (
            ['permission_granted', 'already_granted'] if decision == 'approve' else ['user_rejected', 'user_rejected']
        )
    finally:
        store.close()


@pytest.mark.parametrize('decision', ['approve', 'reject'])
def test_network_denial_does_not_replay_shell_and_persists_raw_arguments(tmp_path, decision, monkeypatch):
    data = tmp_path / 'data'
    data.mkdir(mode=0o700)
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    store = SessionStore(data)
    store.initialize()
    attempts = []
    approvals = []
    def shell(_manager, launch, **_kwargs):
        enabled = any("network-outbound" in argument for argument in launch.argv)
        attempts.append(enabled)
        return {'schemaVersion': 1, 'toolName': 'run_shell',
                'outcome': 'success' if enabled else 'error',
                'code': 'ok' if enabled else 'shell_exit_nonzero', 'summary': 'fixture',
                'data': {'exitCode': 0 if enabled else 1, 'stdout': '',
                         'stderr': '' if enabled else 'connect: network operation not permitted',
                         'truncated': False, 'termination': 'exit',
                         'executionStatus': 'exited', 'workspaceChanged': False},
                'sideEffectsMayExist': False, 'reconciliationRequired': False}
    monkeypatch.setattr('eidos_runtime.runtime.shell_process_manager.ShellProcessManager.start', shell)
    monkeypatch.setattr('eidos_runtime.runtime.tool_runtime.is_seatbelt_ready', lambda: True)
    try:
        session = store.create_session(str(workspace))
        run, _ = store.create_run(session['id'], 'install dependency')
        def approve(request, cancel):
            approvals.append(request)
            assert len(attempts) == 1
            return ApprovalDecision(decision)
        model = ScriptedModel([
            ModelResponse(tool_calls=(ModelToolCall('s1', 'run_shell', {'command': 'npm install docx'}),)),
            ModelResponse(tool_calls=(ModelToolCall('s2', 'run_shell', {'command': 'npm install docx', 'networkAccess': 'default', **({'yieldTimeMs': 30_000} if decision == 'reject' else {})}),)),
            ModelResponse(text='done'),
        ])
        RuntimeEngine(store, model, lambda message: None, request_approval=approve, shell_available=True).run(run['id'], threading.Event())
        assert len(approvals) == 1
        assert attempts == ([False, True] if decision == 'approve' else [False, False])
        rows = store.connection.execute('SELECT raw_arguments_json, arguments_json, result_json FROM tool_calls ORDER BY creation_seq').fetchall()
        assert 'networkAccess' not in json.loads(rows[0][0])
        assert json.loads(rows[1][0])['networkAccess'] == 'default'
        assert json.loads(rows[0][1])['networkAccess'] == 'default'
        assert json.loads(rows[0][2])['code'] == ('permission_granted_retry_required' if decision == 'approve' else 'user_rejected_network')
    finally:
        store.close()


@pytest.mark.parametrize('kind', ['permission', 'denial', 'action'])
@pytest.mark.parametrize('decision', ['approve', 'reject'])
def test_pending_approval_recovers_without_replaying_completed_shell(tmp_path, monkeypatch, kind, decision):
    class ProcessCrash(BaseException):
        pass
    data = tmp_path / 'data'
    data.mkdir(mode=0o700)
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    store = SessionStore(data)
    store.initialize()
    attempts = []
    def shell(_manager, launch, **_kwargs):
        enabled = any("network-outbound" in argument for argument in launch.argv)
        attempts.append(enabled)
        return {'schemaVersion': 1, 'toolName': 'run_shell',
                'outcome': 'success' if enabled else 'error',
                'code': 'ok' if enabled else 'shell_exit_nonzero', 'summary': 'fixture',
                'data': {'exitCode': 0 if enabled else 1, 'stdout': '',
                         'stderr': '' if enabled else 'connect: network operation not permitted',
                         'truncated': False, 'termination': 'exit',
                         'executionStatus': 'exited', 'workspaceChanged': False},
                'sideEffectsMayExist': False, 'reconciliationRequired': False}
    monkeypatch.setattr('eidos_runtime.runtime.shell_process_manager.ShellProcessManager.start', shell)
    monkeypatch.setattr('eidos_runtime.runtime.tool_runtime.is_seatbelt_ready', lambda: True)
    try:
        session = store.create_session(str(workspace))
        run, _ = store.create_run(session['id'], 'recover permission')
        call = ModelToolCall('p', 'request_permissions', {'permissions': {'network': {'enabled': True}}}) if kind == 'permission' else ModelToolCall(
            's', 'run_shell', {'command': 'npm install docx', **({'networkAccess': 'request', 'justification': 'Install dependency'} if kind == 'action' else {})})
        def crash(request, cancel):
            raise ProcessCrash()
        with pytest.raises(ProcessCrash):
            RuntimeEngine(store, ScriptedModel([ModelResponse(tool_calls=(call,))]), lambda message: None,
                          request_approval=crash, shell_available=True).run(run['id'], threading.Event())
        assert store.read_run(run['id'])['status'] == 'waiting_approval'
        original = store.typed_runtime_repository().read_pending_approval(run['id'])
        store.close()
        store = SessionStore(data)
        store.initialize()
        assert store.health_state == 'ready'
        assert store.read_run(run['id'])['status'] == 'waiting_approval'
        assert store.typed_runtime_repository().read_pending_approval(run['id']).id == original.id
        assert store.read_session_snapshot(session['id'])['session']['activeRunStatus'] == 'waiting_approval'
        requests = []
        def approve(request, cancel):
            requests.append(request)
            return ApprovalDecision(decision)
        RuntimeEngine(store, ScriptedModel([ModelResponse(text='done')]), lambda message: None,
                      request_approval=approve, shell_available=True).run(run['id'], threading.Event())
        assert len(requests) == 1
        assert attempts == ([False] if kind == 'denial' else [True] if kind == 'action' and decision == 'approve' else [])
        assert store.read_run(run['id'])['status'] == 'succeeded'
        assert store.connection.execute('SELECT COUNT(*) FROM approvals').fetchone()[0] == 1
    finally:
        store.close()


def test_schema_v8_upgrade_keeps_original_arguments_unknown(tmp_path):
    import sqlite3
    from eidos_runtime.db.schema import V8_SCHEMA_SQL, SCHEMA_VERSION
    data = tmp_path / 'data'
    data.mkdir(mode=0o700)
    database = data / 'state.sqlite'
    with sqlite3.connect(database) as connection:
        connection.executescript(V8_SCHEMA_SQL)
        connection.execute('PRAGMA user_version = 8')
    database.chmod(0o600)
    store = SessionStore(data)
    store.initialize()
    try:
        assert store.health_state == 'ready'
        assert store.connection.execute('PRAGMA user_version').fetchone()[0] == SCHEMA_VERSION
        column = next(row for row in store.connection.execute('PRAGMA table_info(tool_calls)') if row[1] == 'raw_arguments_json')
        assert column[3] == 0
        assert column[4] is None
    finally:
        store.close()

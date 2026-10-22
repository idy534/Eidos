import json
import threading

import pytest

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.extensions.plugins import PluginCatalog
from eidos_runtime.extensions.skills import SkillCatalog
from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel
from eidos_runtime.runtime.approval import ApprovalDecision
from eidos_runtime.runtime.engine import RuntimeEngine


@pytest.mark.parametrize('decision', ['approve', 'reject'])
def test_network_run_grant_skips_download_approval_but_keeps_install_approval(
    tmp_path, monkeypatch, decision,
):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    store = SessionStore(tmp_path / 'data')
    store.initialize()
    try:
        skills = SkillCatalog(PluginCatalog(store))
        session = store.create_session(str(workspace))
        run, _ = store.create_run(
            session['id'], 'Allow network access and install this skill',
            extension_snapshot=skills.extension_snapshot(),
        )
        requests = []

        def approve(request, _cancel):
            requests.append(request)
            return ApprovalDecision('approve' if request['kind'] == 'permission_request' else decision)

        def download(_url, _cancel):
            assert [request['kind'] for request in requests] == ['permission_request']
            assert store.run_permission_grants(run['id']).network.enabled is True
            assert store.connection.execute(
                'SELECT status FROM durable_intents WHERE run_id=?', (run['id'],),
            ).fetchone()[0] == 'running'
            return 'example', {'SKILL.md': b'---\nname: example\ndescription: Example skill.\n---\nRead data.\n'}

        monkeypatch.setattr('eidos_runtime.extensions.skills._download_github_skill', download)
        model = ScriptedModel([
            ModelResponse(tool_calls=(ModelToolCall('network', 'request_permissions', {
                'permissions': {'network': {'enabled': True}},
            }),)),
            ModelResponse(tool_calls=(ModelToolCall('install', 'skill_install', {
                'url': 'https://github.com/example/skills/tree/main/example',
            }),)),
            ModelResponse(text='Done.'),
        ])
        RuntimeEngine(
            store, model, lambda _message: None, request_approval=approve,
        ).run(run['id'], threading.Event())
        assert store.read_run(run['id'])['status'] == 'succeeded'
        assert [request['kind'] for request in requests] == ['permission_request', 'file_change']
        assert (store.data_directory / 'skills' / 'example' / 'SKILL.md').exists() == (decision == 'approve')
        assert store.connection.execute(
            "SELECT count(*) FROM approvals WHERE json_extract(request_json,'$.kind')='network_access'",
        ).fetchone()[0] == 0
        intent = store.connection.execute(
            'SELECT preconditions_json FROM durable_intents WHERE run_id=?', (run['id'],),
        ).fetchone()
        assert json.loads(intent[0])['authorization'] == 'existing_network_permission'
    finally:
        store.close()


def test_unfinished_skill_download_intent_is_not_replayed_after_restart(tmp_path, monkeypatch):
    class ProcessCrash(BaseException):
        pass

    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    data = tmp_path / 'data'
    store = SessionStore(data)
    store.initialize()
    try:
        skills = SkillCatalog(PluginCatalog(store))
        session = store.create_session(str(workspace))
        run, _ = store.create_run(
            session['id'], 'Allow network and install example',
            extension_snapshot=skills.extension_snapshot(),
        )
        downloads = []

        def download(_url, _cancel):
            downloads.append('download')
            return 'example', {'SKILL.md': b'---\nname: example\ndescription: Example skill.\n---\nRead data.\n'}

        def crash_during_install(request, _cancel):
            if request['kind'] == 'permission_request':
                return ApprovalDecision('approve')
            assert request['kind'] == 'file_change'
            raise ProcessCrash()

        monkeypatch.setattr('eidos_runtime.extensions.skills._download_github_skill', download)
        model = ScriptedModel([
            ModelResponse(tool_calls=(ModelToolCall('network', 'request_permissions', {
                'permissions': {'network': {'enabled': True}},
            }),)),
            ModelResponse(tool_calls=(ModelToolCall('install', 'skill_install', {
                'url': 'https://github.com/example/skills/tree/main/example',
            }),)),
        ])
        with pytest.raises(ProcessCrash):
            RuntimeEngine(store, model, lambda _message: None,
                request_approval=crash_during_install).run(run['id'], threading.Event())
        assert downloads == ['download']
        pending = store.typed_runtime_repository().read_pending_approval(run['id'])
        store.close()
        store = SessionStore(data)
        store.initialize()
        def unexpected_approval(*_args):
            pytest.fail('An interrupted intent must not restart an approval or download')

        RuntimeEngine(store, ScriptedModel([ModelResponse(text='Done.')]), lambda _message: None,
            request_approval=unexpected_approval,
        ).run(run['id'], threading.Event())
        assert store.read_run(run['id'])['status'] == 'interrupted'
        assert downloads == ['download']
        assert not (data / 'skills' / 'example').exists()
        assert store.connection.execute('SELECT status FROM approvals WHERE id=?', (pending.id,)).fetchone()[0] == 'invalidated'
        assert store.connection.execute('SELECT status FROM durable_intents').fetchone()[0] == 'interrupted'
        assert store.connection.execute('SELECT count(*) FROM approvals').fetchone()[0] == 2
    finally:
        store.close()

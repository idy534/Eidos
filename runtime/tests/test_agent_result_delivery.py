from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from eidos_runtime.application.collaboration import CollaborationApplication
from eidos_runtime.context.builder import ContextBuilder
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.domain.collaboration import (
    AgentMessageRequest, AgentTextPage, CollaborationRejected, ReadAgentResult, SpawnAgent,
)
from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel
from eidos_runtime.model.context_projection import project_context, recovery_context
from eidos_runtime.persistence.context_snapshots import ContextSnapshotRepository
from eidos_runtime.persistence.collaboration import CollaborationRepository
from eidos_runtime.runtime.engine import RuntimeEngine
from eidos_runtime.tools.collaboration import AgentResultData, CollaborationProjector, collaboration_entries


@pytest.fixture
def collaboration(tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    store = SessionStore(tmp_path / 'data')
    store.initialize()
    session = store.create_session(str(workspace))
    parent, _ = store.create_run(session['id'], 'Coordinate the investigation')
    application = CollaborationApplication(store, lambda: None, store.cancel_run, lambda: None)
    yield store, parent, application
    store.close()


def spawn(store, parent, application, index=0):
    item = store.create_tool_item(parent['id'], 1, index, f'spawn-{index}', 'spawn_agent', '{}')
    return application.repository.spawn(parent['id'], item['id'], SpawnAgent(
        task_name=f'task-{index}', message='Investigate the assigned files and report the final result.'))


def finish_child(store, child, answer):
    assert store.claim_next_run_committed().value['id'] == child.run_id
    progress = store.create_assistant_item(child.run_id, 1)
    store.append_item_content(progress['id'], 'PROCESS_ONLY: intermediate investigation details')
    store.complete_assistant_item_committed(progress['id'])
    item = store.create_assistant_item(child.run_id, 2)
    store.append_item_content(item['id'], answer)
    store.complete_assistant_and_run(item['id'], child.run_id)
    return item['id']


def evidence(store, run_id):
    context = ContextBuilder(store).build(run_id).model_context
    section = next(item for item in context if item.get('sectionId') == 'agent-evidence')
    return section, json.loads(section['content'].split('\n', 1)[1])


def test_long_chinese_assignments_and_escaped_messages_cross_the_argument_boundary(collaboration):
    _store, _parent, application = collaboration
    entries = {entry.spec.name: entry for entry in collaboration_entries(application, child=False)}
    assignment = '中' * 20_000
    assert entries['spawn_agent'].validate_arguments({'taskName': 'inspect', 'message': assignment}).valid
    assert entries['send_message'].validate_arguments({'agentId': 'parent', 'message': assignment}).valid
    # JSON control-character escaping must not turn an accepted body into a rejected call.
    assert entries['send_message'].validate_arguments({'agentId': 'parent', 'message': '\x00' * 65536}).valid
    assert not entries['followup_task'].validate_arguments({'agentId': 'child', 'message': '中' * 21846}).valid
    with pytest.raises(ValidationError):
        AgentMessageRequest(agent_id='parent', message='   ')


def test_parent_receives_the_long_final_answer_without_child_process_messages(collaboration):
    store, parent, application = collaboration
    child = spawn(store, parent, application)
    answer = '结论\n' + '中' * 5_000 + '\nVERIFIED_END'
    item_id = finish_child(store, child, answer)
    section, state = evidence(store, parent['id'])
    result = state['agents'][0]
    assert result['result'] == answer
    assert result['resultItemId'] == item_id
    assert result['resultTruncated'] is False
    assert result['resultBytes'] == len(answer.encode())
    assert state['messages'] == []
    assert 'PROCESS_ONLY' not in section['content']
    assert 'modelId' not in result and 'task' not in result


def test_large_result_has_head_tail_and_pinned_utf8_pages_across_followup(collaboration):
    store, parent, application = collaboration
    child = spawn(store, parent, application)
    answer = 'HEAD\n' + '中🙂\n' * 12_000 + '\nVERIFIED_TAIL'
    item_id = finish_child(store, child, answer)
    _, state = evidence(store, parent['id'])
    result = state['agents'][0]
    assert result['resultTruncated'] is True
    assert result['result'].startswith('HEAD') and result['result'].endswith('VERIFIED_TAIL')
    offset, chunks = 0, []
    while True:
        page = application.repository.read_result(parent['id'], ReadAgentResult(
            agent_id=child.id, item_id=item_id, offset_bytes=offset, max_bytes=4097))
        chunks.append(page.content)
        if not page.has_more:
            break
        assert page.next_offset > offset
        offset = page.next_offset
    assert ''.join(chunks) == answer
    followup = store.create_tool_item(parent['id'], 2, 0, 'followup', 'followup_task', '{}')
    application.repository.followup(parent['id'], followup['id'], child.id, 'Check one more fact.')
    pinned = application.repository.read_result(parent['id'], ReadAgentResult(agent_id=child.id, item_id=item_id))
    assert pinned.source_id == item_id and pinned.content.startswith('HEAD')
    with pytest.raises(CollaborationRejected, match='agent_result_not_available'):
        application.repository.read_result(parent['id'], ReadAgentResult(agent_id=child.id))


def test_result_reader_rejects_other_parents_items_and_invalid_utf8_offsets(collaboration):
    store, parent, application = collaboration
    child = spawn(store, parent, application)
    item_id = finish_child(store, child, '中🙂结果')
    other_session = store.create_session(str(store.workspace_for_run(parent['id']).path))
    other, _ = store.create_run(other_session['id'], 'unrelated task')
    with pytest.raises(CollaborationRejected, match='agent_not_owned_by_run'):
        application.repository.read_result(other['id'], ReadAgentResult(agent_id=child.id, item_id=item_id))
    with pytest.raises(CollaborationRejected, match='agent_result_not_available'):
        application.repository.read_result(parent['id'], ReadAgentResult(agent_id=child.id, item_id=store.get_user_item(parent['id'])['id']))
    with pytest.raises(CollaborationRejected, match='invalid_agent_output_range'):
        application.repository.read_result(parent['id'], ReadAgentResult(agent_id=child.id, item_id=item_id, offset_bytes=1))


def test_status_projection_keeps_sixteen_agents_and_does_not_duplicate_answers(collaboration):
    store, parent, application = collaboration
    for index in range(16):
        child = spawn(store, parent, application, index)
        finish_child(store, child, '中' * 3_000)
    state = application.repository.state(parent['id'])
    raw = {'toolName': 'list_agents', 'outcome': 'success', 'code': 'ok', 'summary': 'ok',
        'data': AgentResultData(state=state).model_dump(mode='json', by_alias=True, exclude_none=True)}
    projected = CollaborationProjector().project(None, raw).model_result
    assert len(projected['data']['agents']) == 16
    assert all(agent.get('resultItemId') and 'result' not in agent for agent in projected['data']['agents'])
    assert len(evidence(store, parent['id'])[1]['agents']) == 16


def test_recovery_keeps_valid_json_and_only_full_messages_receive_receipts(collaboration, monkeypatch):
    store, parent, application = collaboration
    child = spawn(store, parent, application)
    assert store.claim_next_run_committed().value['id'] == child.run_id
    for index in range(16):
        application.repository.send(child.run_id, f'message-{index}', 'parent', '中' * 2000)
    section, _ = evidence(store, parent['id'])
    projected = project_context((section,), text_bytes=64 * 1024)
    recovered = recovery_context(projected, 16 * 1024)[0]
    parsed = json.loads(recovered['content'].split('\n', 1)[1])
    full = {message['id'] for message in parsed['messages'] if not message['contentTruncated']}
    assert set(recovered['agentMessageIds']) == full
    assert any(message['contentTruncated'] for message in parsed['messages'])
    # Catalog/selection layers can put collaboration after the first two
    # messages. Recovery must retain it even with a long conversation tail.
    history = tuple({'type': 'user', 'content': f'History {index}'} for index in range(20))
    reordered = recovery_context((*history[:2], section, *history[2:]), 16 * 1024)
    assert any(item.get('sectionId') == 'agent-evidence' for item in reordered)
    monkeypatch.setattr(ContextSnapshotRepository, 'read_for_model_attempt', lambda *args: SimpleNamespace(model_context=(recovered,)))
    application.repository.record_model_delivery(parent['id'], 'unaccepted-attempt')
    assert application.repository.has_unread_messages(parent['id'])


def test_long_message_readback_is_delivered_and_not_reinjected(collaboration):
    store, parent, application = collaboration
    child = spawn(store, parent, application)
    assert store.claim_next_run_committed().value['id'] == child.run_id
    message = '中' * 20_000
    application.repository.send(child.run_id, 'long-message', 'parent', message)
    store.fail_run(child.run_id, 'fixture_complete')
    assert application.repository.read_message(parent['id'], 'long-message').content == message
    with pytest.raises(CollaborationRejected, match='agent_message_not_available'):
        application.repository.read_message(child.run_id, 'long-message')
    model = ScriptedModel([ModelResponse(tool_calls=(ModelToolCall('read-message', 'read_agent_message', {'messageId': 'long-message'}),)), ModelResponse(text='Received the evidence.')])
    RuntimeEngine(store, model, lambda _: None, collaboration=application).run(parent['id'], threading.Event())
    assert store.read_run(parent['id'])['status'] == 'succeeded'
    assert not application.repository.model_state(parent['id']).messages
    returned = next(item for item in model.contexts[-1] if item.get('name') == 'read_agent_message' and item.get('type') == 'tool_result')
    assert json.loads(returned['result'])['data']['page']['content'] == message


def test_result_tool_runs_through_the_controller_with_a_large_page(collaboration):
    store, parent, application = collaboration
    child = spawn(store, parent, application)
    answer = '中' * 9_000
    item_id = finish_child(store, child, answer)
    model = ScriptedModel([ModelResponse(tool_calls=(ModelToolCall('read-result', 'read_agent_result', {'agentId': child.id, 'itemId': item_id, 'maxBytes': 65536}),)), ModelResponse(text='Reviewed the result.')])
    RuntimeEngine(store, model, lambda _: None, collaboration=application).run(parent['id'], threading.Event())
    assert store.read_run(parent['id'])['status'] == 'succeeded'
    returned = next(item for item in model.contexts[-1] if item.get('name') == 'read_agent_result' and item.get('type') == 'tool_result')
    page = AgentTextPage.model_validate(json.loads(returned['result'])['data']['page'])
    assert page.content == answer and not page.has_more


def test_cancel_after_model_acceptance_does_not_consume_the_message(collaboration, monkeypatch):
    store, parent, application = collaboration
    child = spawn(store, parent, application)
    assert store.claim_next_run_committed().value['id'] == child.run_id
    application.repository.send(child.run_id, 'cancel-message', 'parent', 'Evidence remains pending after cancellation.')
    store.fail_run(child.run_id, 'fixture_complete')
    original = CollaborationRepository.record_model_delivery

    def cancel_before_receipt(repository, run_id, attempt_id):
        store.cancel_run(run_id)
        return original(repository, run_id, attempt_id)

    monkeypatch.setattr(CollaborationRepository, 'record_model_delivery', cancel_before_receipt)
    RuntimeEngine(store, ScriptedModel([ModelResponse(text='Received.')]), lambda _: None,
        collaboration=application).run(parent['id'], threading.Event())
    assert store.read_run(parent['id'])['status'] == 'canceled'
    assert application.repository.has_unread_messages(parent['id'])
    assert store.connection.execute('SELECT COUNT(*) FROM agent_message_receipts').fetchone()[0] == 0


def test_saved_results_and_unread_messages_survive_restart(collaboration):
    store, parent, application = collaboration
    child = spawn(store, parent, application)
    assert store.claim_next_run_committed().value['id'] == child.run_id
    application.repository.send(child.run_id, 'saved-message', 'parent', 'A decision is required.')
    item = store.create_assistant_item(child.run_id, 1)
    answer = '中' * 5_000 + 'SAVED_END'
    store.append_item_content(item['id'], answer)
    store.complete_assistant_and_run(item['id'], child.run_id)
    data_directory = store.data_directory
    store.close()
    reopened = SessionStore(data_directory)
    reopened.initialize()
    try:
        repository = CollaborationRepository(reopened.database)
        page = repository.read_result(parent['id'], ReadAgentResult(agent_id=child.id, item_id=item['id'], max_bytes=65536))
        assert page.content == answer and not page.has_more
        assert [message.id for message in repository.model_state(parent['id']).messages] == ['saved-message']
    finally:
        reopened.close()

from __future__ import annotations

import sqlite3
import threading

import pytest

from eidos_runtime.application.collaboration import CollaborationApplication
from eidos_runtime.db.gate_refinements_migration import migrate_gate_refinements
from eidos_runtime.db.schema import V16_SCHEMA_SQL
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.domain.collaboration import SpawnAgent
from eidos_runtime.domain.planning import RequestUserInput
from eidos_runtime.extensions.plugins import PluginCatalog
from eidos_runtime.extensions.skill_management import SkillManagement
from eidos_runtime.extensions.skills import SkillCatalog, SkillReadError
from eidos_runtime.model.client import ModelResponse, ScriptedModel
from eidos_runtime.persistence.collaboration import CollaborationRepository
from eidos_runtime.persistence.planning import PlanningRepository
from eidos_runtime.runtime.engine import RuntimeEngine


@pytest.fixture
def state(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    session = store.create_session(str(workspace))
    run, _ = store.create_run(session['id'], 'Coordinate the investigation')
    yield store, session, run
    store.close()


def spawn(store, run, index, required=True):
    repository = CollaborationRepository(store.database)
    item = store.create_tool_item(run['id'], 1, index, f'spawn-{index}', 'spawn_agent', '{}')
    return repository.spawn(run['id'], item['id'], SpawnAgent(
        task_name=f'task-{index}', message='Investigate the assigned files.', role='explorer',
        required_for_completion=required,
    ))


def application(store):
    return CollaborationApplication(store, schedule=lambda: None, cancel=store.cancel_run, publish=lambda: None)


def test_optional_cleanup_preserves_required_child_and_owned_cancellation(state):
    store, _session, run = state
    required = spawn(store, run, 0)
    optional = spawn(store, run, 1, False)
    application(store).stop_optional(run['id'])
    assert store.read_run(optional.run_id)['status'] == 'canceled'
    assert store.read_run(required.run_id)['status'] == 'queued'
    assert CollaborationRepository(store.database).state(run['id']).agents[1].required_for_completion is False


def test_messages_over_one_page_are_delivered_before_completion(state, monkeypatch):
    store, _session, run = state
    child = spawn(store, run, 0)
    repository = CollaborationRepository(store.database)
    assert store.claim_next_run()['id'] == child.run_id
    for index in range(33):
        item = store.create_tool_item(child.run_id, 1, index, f'message-{index}', 'send_message', '{}')
        repository.send(child.run_id, item['id'], 'parent', f'Evidence number {index}')
    store.fail_run(child.run_id, 'fixture_complete')
    assert len(repository.model_state(run['id']).messages) == 16
    # Preparing or previewing the context must not consume messages.
    repository.model_state(run['id'])
    repository.record_model_delivery(run['id'], 'unaccepted-attempt')
    assert repository.has_unread_messages(run['id'])
    model = ScriptedModel([ModelResponse(text='Summary') for _ in range(3)])
    original = model.complete
    def before_accept(*args, **kwargs):
        attempt = store.connection.execute("SELECT id FROM model_attempts WHERE status='running' ORDER BY creation_seq DESC LIMIT 1").fetchone()
        assert attempt is not None
        repository.record_model_delivery(run['id'], attempt['id'])
        assert repository.has_unread_messages(run['id'])
        return original(*args, **kwargs)
    monkeypatch.setattr(model, 'complete', before_accept)
    RuntimeEngine(store, model, lambda _event: None, collaboration=application(store)).run(run['id'], threading.Event())
    assert store.read_run(run['id'])['status'] == 'succeeded'
    assert not repository.has_unread_messages(run['id'])
    pages = [next(value['content'] for value in context if value.get('sectionId') == 'agent-evidence') for context in model.contexts]
    assert len(pages) == 3
    import json
    assert [[message['content'] for message in json.loads(page.split('\n', 1)[1])['messages']] for page in pages] == [
        [f'Evidence number {index}' for index in range(start, end)] for start, end in ((0, 16), (16, 32), (32, 33))
    ]
    assert store.connection.execute('SELECT COUNT(*) FROM agent_message_receipts').fetchone()[0] == 33


def test_v16_migration_preserves_delegation_and_defaults_to_required(state):
    store, _session, run = state
    child = spawn(store, run, 0)
    connection = store.connection
    connection.executescript(
        'DROP TABLE IF EXISTS memory_fts_word; '
        'DROP TABLE IF EXISTS memory_usage; DROP TABLE IF EXISTS memory_snapshot_refs; '
        'DROP TABLE IF EXISTS memory_generations; DROP TABLE IF EXISTS memory_model_attempts; '
        'DROP TABLE IF EXISTS memory_jobs; DROP TABLE IF EXISTS memory_suppressions; '
        'DROP TABLE IF EXISTS memory_actions; DROP TABLE IF EXISTS memory_evidence; '
        'DROP TABLE IF EXISTS memory_revisions; DROP TABLE IF EXISTS memory_entries; '
        'DROP TABLE IF EXISTS memory_source_items; DROP TABLE IF EXISTS memory_sources; '
        'DROP TABLE IF EXISTS memory_scopes; DROP TABLE IF EXISTS memory_tool_budget; '
        'DROP TABLE IF EXISTS memory_tool_reads; '
        'DROP TRIGGER IF EXISTS memory_source_insert; DROP TRIGGER IF EXISTS memory_source_change; '
        'DROP TRIGGER IF EXISTS memory_source_delete; DROP TRIGGER IF EXISTS memory_scope_revoke; '
        'DROP TRIGGER IF EXISTS memory_scope_changed; DROP TRIGGER IF EXISTS memory_session_use_revoke; '
        'ALTER TABLE context_snapshots DROP COLUMN memory_revoked; '
        'DROP TABLE agent_message_receipts; DROP TABLE run_skill_leases; '
        'ALTER TABLE agent_delegations DROP COLUMN required_for_completion; '
        'PRAGMA user_version=16;'
    )
    migrate_gate_refinements(connection)
    assert connection.execute('PRAGMA user_version').fetchone()[0] == 17
    assert connection.execute('SELECT child_session_id, required_for_completion FROM agent_delegations').fetchone()[:] == (child.session_id, 1)
    assert connection.execute('PRAGMA foreign_key_check').fetchall() == []
    # Reopen through the real database initialization path.
    directory = store.data_directory
    store.close()
    reopened = SessionStore(directory)
    reopened.initialize()
    assert reopened.read_run(run['id'])['id'] == run['id']
    reopened.close()


def test_v16_migration_rolls_back_invalid_foreign_keys(tmp_path):
    connection = sqlite3.connect(tmp_path / 'bad.sqlite')
    connection.executescript(V16_SCHEMA_SQL)
    connection.execute("INSERT INTO runs(id,session_id,user_input,model_profile_json,status,created_at,updated_at) VALUES('bad','absent','task','{}','queued',1,1)")
    connection.execute('PRAGMA user_version=16')
    connection.commit()
    with pytest.raises(sqlite3.IntegrityError, match='foreign key violation'):
        migrate_gate_refinements(connection)
    assert connection.execute('PRAGMA user_version').fetchone()[0] == 16
    assert connection.execute("SELECT 1 FROM sqlite_master WHERE name='run_skill_leases'").fetchone() is None
    assert 'required_for_completion' not in [row[1] for row in connection.execute('PRAGMA table_info(agent_delegations)')]
    connection.close()


def test_unused_skill_removal_survives_restart_without_changing_frozen_catalog(state):
    store, session, old_run = state
    store.fail_run(old_run['id'], 'fixture_complete')
    root = store.data_directory / 'skills' / 'example'
    root.mkdir(parents=True, mode=0o700)
    (root / 'SKILL.md').write_text('---\nname: example\ndescription: Example skill.\n---\nEvidence.\n')
    catalog = SkillCatalog(PluginCatalog(store))
    snapshot = catalog.extension_snapshot()
    run, _ = store.create_run(session['id'], 'May use a skill', extension_snapshot=snapshot, queued=True)
    frozen = catalog.catalog_snapshot(snapshot)
    assert SkillManagement(catalog).remove('user:example').cleanup_pending is False
    assert not root.exists()
    assert 'skillCatalogSnapshotJson' not in store.read_run(run['id'])['extensionSnapshot']
    assert 'skillCatalogSnapshotJson' in store.read_run_extension_snapshot(run['id'])
    fresh = SkillCatalog(PluginCatalog(store))
    restored = fresh.catalog_snapshot(store.read_run_extension_snapshot(run['id']))
    assert restored == frozen
    with pytest.raises(SkillReadError):
        fresh.read_skill(restored, 'user:example')
    with pytest.raises(ValueError, match='skill_unavailable'):
        store.acquire_skill_lease(run['id'], 'user:example')
    directory = store.data_directory
    store.close()
    reopened = SessionStore(directory)
    reopened.initialize()
    restored = SkillCatalog(PluginCatalog(reopened)).catalog_snapshot(reopened.read_run_extension_snapshot(run['id']))
    assert restored == frozen
    assert reopened.claim_next_run()['id'] == run['id']
    model = ScriptedModel([ModelResponse(text='Proceed with other evidence')])
    RuntimeEngine(reopened, model, lambda _event: None).run(run['id'], threading.Event())
    assert reopened.read_run(run['id'])['status'] == 'succeeded'
    reopened.close()


def test_tool_activation_keeps_latest_request_and_reactivation(state):
    store, _session, run = state
    store.activate_tools(run['id'], tuple(f'a-{index:02}' for index in range(32)))
    names = store.activate_tools(run['id'], ('z-new',))
    assert names[0] == 'z-new'
    assert len(names) == 32
    assert 'a-31' not in names
    assert store.activate_tools(run['id'], ('a-20',))[0] == 'a-20'
    assert 'z-new' in store.activated_tools(run['id'])


def test_execute_run_can_ask_but_terminal_run_cannot(state):
    store, _session, run = state
    repository = PlanningRepository(store.database)
    item = store.create_tool_item(run['id'], 1, 0, 'question', 'request_user_input', '{}')
    question = RequestUserInput(questions=[{'id': 'scope', 'question': 'Which scope?', 'type': 'text'}])
    assert repository.ask(run['id'], item['id'], question, suspend=False).status == 'pending'
    store.fail_run(run['id'], 'fixture_complete')
    with pytest.raises(ValueError, match='user_input_run_not_active'):
        repository.ask(run['id'], item['id'], question, suspend=False)


def test_search_queue_owns_no_waiting_thread_and_starts_when_a_slot_is_free(tmp_path, monkeypatch):
    from contextlib import contextmanager
    import os
    from types import SimpleNamespace
    from eidos_runtime.tools.workspace import ToolExecutor, ResolvedAuthorizedPath, ToolCancelled
    from eidos_runtime.workspace.search_driver import WorkspaceSearchResult

    started = {str(index): threading.Event() for index in range(6)}
    release = {key: threading.Event() for key in started}
    class Driver:
        def search(self, request, cancel):
            started[request.query].set()
            while not release[request.query].wait(0.01):
                if cancel.is_set():
                    break
            return WorkspaceSearchResult(matches=(), scanned_bytes=0, truncated=False, truncation_reason=None)

    @contextmanager
    def reader(_resolved):
        descriptor = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            yield SimpleNamespace(root_fd=descriptor, _verify_root=lambda: None)
        finally:
            os.close(descriptor)

    with ToolExecutor(tmp_path, search_driver=Driver()) as executor:
        monkeypatch.setattr(executor, '_resolve_read_path', lambda _path: ResolvedAuthorizedPath(tmp_path, '.', 'workspace', False))
        monkeypatch.setattr(executor, '_authorized_reader', reader)
        monkeypatch.setattr(executor, '_verify_root', lambda: None)
        def submit(query):
            return executor._search_text({'query': query, 'path': '.', 'regex': False, 'includeGlobs': [], 'maxResults': 5, 'yieldTimeMs': 0}, threading.Event())
        for index in range(4):
            submit(str(index))
            assert started[str(index)].wait(2)
        queued = submit('4')
        canceled = submit('5')
        queued_id = queued['data']['sessionId']
        assert queued['summary'] == 'Search is queued'
        assert executor._search_sessions[queued_id].thread is None
        cancel = threading.Event()
        cancel.set()
        with pytest.raises(ToolCancelled):
            executor._search_text_wait({'sessionId': canceled['data']['sessionId'], 'yieldTimeMs': 0}, cancel)
        assert not started['5'].is_set()
        release['0'].set()
        assert started['4'].wait(2)
        for event in release.values():
            event.set()
    assert not started['5'].is_set()
    assert executor._search_sessions == {}


def test_mixed_batch_parallel_reads_keep_persisted_order_and_skip_recovery(state, monkeypatch):
    from types import SimpleNamespace
    from eidos_runtime.model.client import ModelToolCall, ModelResponse
    from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel
    from eidos_runtime.runtime.events import RuntimeEvents
    from eidos_runtime.runtime.run_resources import RunResources
    from eidos_runtime.runtime.state_machine import RuntimePhaseTracker
    from eidos_runtime.runtime.tool_runtime import ToolCallRuntime, ReadOnlyToolHandler
    from eidos_runtime.sandbox.permissions import BasePermissionProfile
    from eidos_runtime.sandbox.sensitive import default_scanner
    from eidos_runtime.runtime.tool_execution import HandlerOutcome
    from eidos_runtime.runtime.errors import tool_error

    store, _session, run = state
    # The pairs must overlap; the intervening plan write must finish first.
    barriers = [threading.Barrier(2), threading.Barrier(2)]
    observed = []
    def read(_handler, _run_id, _item, call, _cancel, _runtime):
        pair = 0 if call.provider_call_id.startswith('before') else 1
        if pair:
            assert store.connection.execute('SELECT 1 FROM plans WHERE run_id=?', (run['id'],)).fetchone()
        observed.append(call.provider_call_id)
        barriers[pair].wait(timeout=3)
        return HandlerOutcome(tool_error('list_files', 'fixture_read', 'Fixture read result'), 'completed')
    monkeypatch.setattr(ReadOnlyToolHandler, 'execute', read)
    kernel = RuntimeAsyncKernel()
    kernel.start()
    try:
        with RunResources(store, run['id'], store.read_run_extension_snapshot(run['id']), async_kernel=kernel) as resources:
            # Test a real Plan write between otherwise parallel read segments.
            store.connection.execute("UPDATE runs SET work_mode='plan' WHERE id=?", (run['id'],))
            resources._set_registry()
            runtime = ToolCallRuntime(store, resources.dispatcher, None, RuntimeEvents(lambda _: None), default_scanner(), RuntimePhaseTracker(), async_kernel=kernel, shell_available=False, base_permissions=BasePermissionProfile.for_workspace(workspace_root=store.workspace_for_run(run['id']).path))
            def unexpected(**_kwargs):
                raise AssertionError('ordinary reads or a local Plan write must not scan the workspace')
            monkeypatch.setattr(runtime, '_refresh_reconciliation', unexpected)
            snapshot = resources.dispatcher.snapshot()
            index = store.increment_model_step(run['id'], tool_snapshot=snapshot.as_dict())
            step = SimpleNamespace(run_id=run['id'], step_index=index, tool_snapshot=snapshot)
            calls = (ModelToolCall('before-1', 'list_files', {}), ModelToolCall('before-2', 'list_files', {}),
                ModelToolCall('write', 'write_plan', {'title': 'Draft', 'markdown': '# Draft'}),
                ModelToolCall('after-1', 'list_files', {}), ModelToolCall('after-2', 'list_files', {}))
            validated = resources.dispatcher.validate(ModelResponse(tool_calls=calls))
            assert validated.error_code is None
            assert runtime.execute(step, validated.tool_calls, threading.Event()).status == 'completed'
            rows = store.connection.execute('SELECT t.provider_call_id,t.batch_order FROM tool_calls t JOIN items i ON i.id=t.item_id WHERE i.run_id=? ORDER BY t.batch_order', (run['id'],)).fetchall()
            assert [tuple(row) for row in rows] == [(call.provider_call_id, index) for index, call in enumerate(calls)]
            assert set(observed) == {'before-1', 'before-2', 'after-1', 'after-2'}
    finally:
        kernel.close()

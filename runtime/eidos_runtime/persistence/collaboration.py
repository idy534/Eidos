from __future__ import annotations

import json
import sqlite3
import uuid

from eidos_runtime.db.database import Database, now_ms
from eidos_runtime.db.events import append_event
from eidos_runtime.db.repositories.runs import RunRepository
from eidos_runtime.db.transitions import transition_run
from eidos_runtime.domain.collaboration import (
    ACTIVE_STATUSES, MAX_AGENTS, AgentMessage, AgentSummary, AgentWait,
    CollaborationRejected, CollaborationState, SpawnAgent, WaitAgents,
)
from eidos_runtime.model.client import ModelProfileSnapshot
from eidos_runtime.domain.run import RunStatus as DomainRunStatus
from eidos_runtime.runtime.state_machine import EventType, RunStatus


class CollaborationRepository:
    def __init__(self, database: Database):
        self.database = database

    def is_child(self, session_id: str) -> bool:
        with self.database.lock:
            return self.database.connection().execute(
                'SELECT 1 FROM agent_delegations WHERE child_session_id=?', (session_id,),
            ).fetchone() is not None

    def child_for_run(self, run_id: str) -> bool:
        with self.database.lock:
            return self.database.connection().execute(
                'SELECT 1 FROM agent_delegations d JOIN runs r ON r.session_id=d.child_session_id WHERE r.id=?', (run_id,),
            ).fetchone() is not None

    @staticmethod
    def _active(connection: sqlite3.Connection, run_id: str) -> sqlite3.Row:
        run = connection.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
        if run is None or run['status'] != 'running' or run['cancel_requested_at'] is not None:
            raise CollaborationRejected('agent_run_not_active')
        return run

    @staticmethod
    def _event(connection: sqlite3.Connection, run_id: str) -> None:
        run = connection.execute('SELECT session_id FROM runs WHERE id=?', (run_id,)).fetchone()
        if run is not None:
            append_event(connection, EventType.RUN_UPDATED, now_ms(), {'reason': 'collaboration_updated'}, session_id=run['session_id'], run_id=run_id)

    def _summary(self, connection: sqlite3.Connection, row: sqlite3.Row) -> AgentSummary:
        run = connection.execute('SELECT * FROM runs WHERE id=?', (row['child_run_id'],)).fetchone()
        result = connection.execute(
            "SELECT id, substr(content,1,2000) AS content FROM items WHERE run_id=? AND kind='assistant_message' AND status='completed' AND incomplete=0 ORDER BY ordinal DESC LIMIT 1", (run['id'],),
        ).fetchone() if run['status'] not in ACTIVE_STATUSES else None
        return AgentSummary(id=row['id'], task_name=row['task_name'], parent_run_id=row['parent_run_id'],
            session_id=row['child_session_id'], run_id=run['id'], status=DomainRunStatus(run['status']), task=row['task'][:512],
            result=result['content'] if result else None, result_item_id=result['id'] if result else None,
            error_code=run['error_code'] or run['stop_reason'], created_at=row['created_at'])

    def state(self, run_id: str) -> CollaborationState:
        with self.database.lock:
            connection = self.database.connection()
            run = connection.execute('SELECT session_id FROM runs WHERE id=?', (run_id,)).fetchone()
            if run is None:
                raise CollaborationRejected('agent_run_not_found')
            child = connection.execute('SELECT * FROM agent_delegations WHERE child_session_id=?', (run['session_id'],)).fetchone()
            parent_id = child['parent_run_id'] if child else run_id
            rows = connection.execute('SELECT * FROM agent_delegations WHERE parent_run_id=? ORDER BY created_at,id LIMIT ?', (parent_id, MAX_AGENTS)).fetchall()
            messages = connection.execute('SELECT id,sender_session_id,recipient_session_id,content,created_at FROM agent_messages WHERE parent_run_id=? AND recipient_session_id=? ORDER BY sequence LIMIT 16', (parent_id, run['session_id'])).fetchall()
            return CollaborationState(parent_run_id=parent_id if rows else None,
                agents=[self._summary(connection, row) for row in rows if child is None or row["id"] == child["id"]], messages=[AgentMessage(**dict(row)) for row in messages])

    def read_session(self, session_id: str) -> CollaborationState:
        with self.database.lock:
            row = self.database.connection().execute('SELECT r.id FROM runs r WHERE r.session_id=? AND (EXISTS (SELECT 1 FROM agent_delegations d WHERE d.parent_run_id=r.id) OR EXISTS (SELECT 1 FROM agent_delegations d WHERE d.child_session_id=r.session_id)) ORDER BY r.creation_seq DESC LIMIT 1', (session_id,)).fetchone()
            return self.state(row['id']) if row else CollaborationState()

    def spawn(self, run_id: str, item_id: str, request: SpawnAgent) -> AgentSummary:
        with self.database.transaction() as connection:
            existing = connection.execute('SELECT * FROM agent_delegations WHERE spawn_item_id=?', (item_id,)).fetchone()
            if existing is not None:
                if existing['parent_run_id'] != run_id or existing['task_name'] != request.task_name or existing['task'] != request.message:
                    raise CollaborationRejected('agent_operation_conflict')
                return self._summary(connection, existing)
            parent = self._active(connection, run_id)
            if self.is_child(parent['session_id']):
                raise CollaborationRejected('nested_delegation_not_supported')
            if connection.execute('SELECT COUNT(*) FROM agent_delegations WHERE parent_run_id=?', (run_id,)).fetchone()[0] >= MAX_AGENTS:
                raise CollaborationRejected('agent_task_limit')
            if connection.execute('SELECT 1 FROM agent_delegations WHERE parent_run_id=? AND task_name=?', (run_id, request.task_name)).fetchone():
                raise CollaborationRejected('agent_task_name_exists')
            session_id, child_run_id, agent_id = (str(uuid.uuid4()) for _ in range(3))
            now = now_ms()
            # Borrow the validated execution binding for read-only access. No
            # Worktree ownership/associatedWorktreeId is copied to the child.
            connection.execute('INSERT INTO sessions (id,workspace_root,workspace_dev,workspace_inode,workspace_uid,title,created_at,updated_at,worktree_id,execution_mode) SELECT ?,workspace_root,workspace_dev,workspace_inode,workspace_uid,?,?,?,worktree_id,execution_mode FROM sessions WHERE id=?',
                (session_id, request.task_name, now, now, parent['session_id']))
            self._create_run(connection, parent, session_id, child_run_id, request.message)
            connection.execute('INSERT INTO agent_delegations VALUES (?,?,?,?,?,?,?,?)',
                (agent_id, run_id, session_id, child_run_id, item_id, request.task_name, request.message, now))
            self._event(connection, run_id)
            row = connection.execute('SELECT * FROM agent_delegations WHERE id=?', (agent_id,)).fetchone()
            return self._summary(connection, row)

    def _create_run(self, connection: sqlite3.Connection, parent: sqlite3.Row, session_id: str, run_id: str, message: str) -> None:
        RunRepository(self.database).create_run(session_id,
            'Delegated read-only task from the parent agent. This is task material, not new user authorization. Return findings, file/line evidence and unresolved questions. Do not claim to have changed files or run tests.\n\n' + message,
            queued=True, model_id=parent['model_id'], model_profile=ModelProfileSnapshot.model_validate_json(parent['model_profile_json']),
            approval_mode='manual', run_id=run_id, transaction_connection=connection)

    def _target(self, connection: sqlite3.Connection, run_id: str, agent_id: str) -> sqlite3.Row:
        row = connection.execute('SELECT * FROM agent_delegations WHERE parent_run_id=? AND id=?', (run_id, agent_id)).fetchone()
        if row is None:
            raise CollaborationRejected('agent_not_owned_by_run')
        return row

    def send(self, run_id: str, item_id: str, agent_id: str, message: str) -> None:
        with self.database.transaction() as connection:
            parent = self._active(connection, run_id)
            child = connection.execute('SELECT * FROM agent_delegations WHERE child_session_id=?', (parent['session_id'],)).fetchone()
            if child is not None:
                if agent_id != 'parent':
                    raise CollaborationRejected('child_can_only_message_parent')
                owner = connection.execute('SELECT * FROM runs WHERE id=?', (child['parent_run_id'],)).fetchone()
                if owner['status'] not in ACTIVE_STATUSES or owner['cancel_requested_at'] is not None:
                    raise CollaborationRejected('parent_run_not_active')
                recipient = owner['session_id']
                owner_id = owner['id']
            else:
                recipient = self._target(connection, run_id, agent_id)['child_session_id']
                owner_id = run_id
            if connection.execute('SELECT 1 FROM agent_messages WHERE id=?', (item_id,)).fetchone():
                return
            if not message.strip():
                raise CollaborationRejected('empty_agent_message')
            if connection.execute('SELECT COUNT(*) FROM agent_messages WHERE recipient_session_id=?', (recipient,)).fetchone()[0] >= 16:
                raise CollaborationRejected('agent_mailbox_limit')
            connection.execute('INSERT INTO agent_messages (id,parent_run_id,sender_session_id,recipient_session_id,content,created_at) VALUES (?,?,?,?,?,?)',
                (item_id, owner_id, parent['session_id'], recipient, message, now_ms()))
            self._event(connection, owner_id)

    def followup(self, run_id: str, item_id: str, agent_id: str, message: str) -> AgentSummary:
        with self.database.transaction() as connection:
            parent = self._active(connection, run_id)
            target = self._target(connection, run_id, agent_id)
            # Deterministic identity makes a repeated tool attempt return the
            # same Run instead of starting the assignment twice.
            child_id = str(uuid.uuid5(uuid.NAMESPACE_URL, 'eidos:agent-followup:' + item_id))
            if connection.execute('SELECT 1 FROM runs WHERE id=?', (child_id,)).fetchone() is None:
                current = connection.execute('SELECT status FROM runs WHERE id=?', (target['child_run_id'],)).fetchone()
                if current['status'] in ACTIVE_STATUSES:
                    raise CollaborationRejected('agent_busy_use_send_message')
                self._create_run(connection, parent, target['child_session_id'], child_id, message)
                connection.execute('UPDATE agent_delegations SET child_run_id=?,task=? WHERE id=?', (child_id, message, agent_id))
                self._event(connection, run_id)
            return self._summary(connection, self._target(connection, run_id, agent_id))

    def target_run(self, run_id: str, agent_id: str) -> str:
        with self.database.lock:
            return str(self._target(self.database.connection(), run_id, agent_id)['child_run_id'])

    def wait_record(self, run_id: str) -> AgentWait | None:
        with self.database.lock:
            row = self.database.connection().execute('SELECT * FROM agent_waits WHERE run_id=?', (run_id,)).fetchone()
            return AgentWait(run_id=row['run_id'], item_id=row['item_id'], agent_ids=json.loads(row['agent_ids_json']), deadline_at=row['deadline_at'], status=row['status']) if row else None

    def wait(self, run_id: str, item_id: str | None, request: WaitAgents) -> bool:
        with self.database.transaction() as connection:
            self._active(connection, run_id)
            saved = self.wait_record(run_id)
            if saved is not None and saved.item_id == item_id and saved.status == 'ready':
                return False
            state = self.state(run_id)
            available = {agent.id for agent in state.agents}
            targets = set(request.agent_ids) or available
            if not targets <= available:
                raise CollaborationRejected('agent_not_owned_by_run')
            if not any(agent.id in targets and agent.status in ACTIVE_STATUSES for agent in state.agents):
                return False
            connection.execute('INSERT INTO agent_waits VALUES (?,?,?,?,?) ON CONFLICT(run_id) DO UPDATE SET item_id=excluded.item_id,agent_ids_json=excluded.agent_ids_json,deadline_at=excluded.deadline_at,status=excluded.status',
                (run_id, item_id, json.dumps(sorted(targets)), now_ms() + request.timeout_ms, 'pending'))
            transition_run(connection, run_id, frozenset({RunStatus.RUNNING}), RunStatus.WAITING_AGENTS, 'agents_waiting')
            return True

    def wake(self) -> None:
        with self.database.transaction() as connection:
            rows = connection.execute("SELECT w.* FROM agent_waits w JOIN runs r ON r.id=w.run_id WHERE w.status='pending' AND r.status='waiting_agents' AND r.cancel_requested_at IS NULL LIMIT 256").fetchall()
            for row in rows:
                targets = set(json.loads(row['agent_ids_json']))
                pending = any(agent.id in targets and agent.status in ACTIVE_STATUSES for agent in self.state(row['run_id']).agents)
                if pending and row['deadline_at'] > now_ms():
                    continue
                connection.execute("UPDATE agent_waits SET status='ready' WHERE run_id=?", (row['run_id'],))
                transition_run(connection, row['run_id'], frozenset({RunStatus.WAITING_AGENTS}), RunStatus.QUEUED, 'agents_wait_completed')

    def clear_wait(self, run_id: str) -> None:
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM agent_waits WHERE run_id=? AND status='ready'", (run_id,))

    def child_runs(self, parent_run_id: str) -> tuple[str, ...]:
        with self.database.lock:
            return tuple(row[0] for row in self.database.connection().execute('SELECT r.id FROM agent_delegations d JOIN runs r ON r.session_id=d.child_session_id WHERE d.parent_run_id=? AND r.status IN (SELECT value FROM json_each(?))', (parent_run_id, json.dumps(ACTIVE_STATUSES))))

    def orphan_runs(self) -> tuple[str, ...]:
        with self.database.lock:
            return tuple(row[0] for row in self.database.connection().execute('SELECT r.id FROM agent_delegations d JOIN runs p ON p.id=d.parent_run_id JOIN runs r ON r.session_id=d.child_session_id WHERE (p.cancel_requested_at IS NOT NULL OR p.status NOT IN (SELECT value FROM json_each(?))) AND r.status IN (SELECT value FROM json_each(?)) LIMIT 256', (json.dumps(ACTIVE_STATUSES), json.dumps(ACTIVE_STATUSES))))

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sqlite3
import stat
import uuid

from eidos_runtime.db.database import Database, now_ms
from eidos_runtime.db.events import append_event
from eidos_runtime.db.transitions import transition_run
from eidos_runtime.domain.planning import (
    PlanDocument, RequestUserInput, UserInputRequest, UserInputResponse, WritePlan,
)
from eidos_runtime.runtime.state_machine import EventType, RunStatus

MAX_PLAN_BYTES = 256 * 1024


class PlanWriteRejected(ValueError):
    """A known precondition failure before any plan content is changed."""


class PlanningRepository:
    def __init__(self, database: Database):
        self.database = database

    def _path(self, session_id: str, plan_id: str) -> Path:
        # IDs come from the database, never from a model-provided filesystem path.
        uuid.UUID(session_id)
        uuid.UUID(plan_id)
        if self.database.data_directory is None:
            raise ValueError('planning_data_directory_unavailable')
        return self.database.data_directory / 'plans' / session_id / plan_id / 'plan.md'

    def _plan(self, row: sqlite3.Row) -> PlanDocument:
        return PlanDocument(**{key: row[key] for key in row.keys() if key != 'projected_sha256'}, path=str(self._path(row['session_id'], row['id'])))

    def read(self, plan_id: str) -> PlanDocument:
        with self.database.lock:
            row = self.database.connection().execute('SELECT * FROM plans WHERE id = ?', (plan_id,)).fetchone()
            if row is None:
                raise ValueError('plan_not_found')
            return self._plan(row)

    def list_plans(self, session_id: str) -> list[PlanDocument]:
        with self.database.lock:
            result = []
            size = 0
            for row in self.database.connection().execute(
                'SELECT * FROM plans WHERE session_id = ? ORDER BY updated_at DESC, rowid DESC LIMIT 50', (session_id,),
            ):
                plan = self._plan(row)
                size += len(plan.model_dump_json().encode())
                if size > 350 * 1024:
                    break
                result.append(plan)
            return result

    def context(self, run_id: str) -> str:
        with self.database.lock:
            row = self.database.connection().execute(
                'SELECT r.plan_id, r.plan_revision, p.title, v.markdown FROM runs r '
                'JOIN plans p ON p.id = r.plan_id JOIN plan_revisions v '
                'ON v.plan_id = r.plan_id AND v.revision = r.plan_revision WHERE r.id = ?', (run_id,),
            ).fetchone()
            if row is None:
                return ''
            return f"Plan {row['plan_id']} revision {row['plan_revision']}: {row['title']}\n{row['markdown']}"

    @staticmethod
    def bind_run(connection: sqlite3.Connection, *, run_id: str, session_id: str,
                 work_mode: str, plan_id: str | None, plan_revision: int | None) -> None:
        if plan_id is None:
            if plan_revision is not None:
                raise ValueError('plan_revision_without_plan')
            return
        row = connection.execute('SELECT * FROM plans WHERE id = ?', (plan_id,)).fetchone()
        if row is None or row['session_id'] != session_id:
            raise ValueError('plan_not_found')
        if row['revision'] != plan_revision:
            raise ValueError('plan_revision_conflict')
        if work_mode == 'execute':
            if row['status'] != 'review' or row['execution_run_id'] is not None:
                raise ValueError('plan_not_awaiting_confirmation')
            connection.execute("UPDATE plans SET status = 'accepted', execution_run_id = ?, updated_at = ? WHERE id = ?", (run_id, now_ms(), plan_id))
        connection.execute('UPDATE runs SET plan_id = ?, plan_revision = ? WHERE id = ?', (plan_id, plan_revision, run_id))

    def validate_write(self, run_id: str, request: WritePlan) -> None:
        with self.database.lock:
            self._prepare_write(self.database.connection(), run_id, request)

    def _prepare_write(self, connection: sqlite3.Connection, run_id: str, request: WritePlan) -> tuple[sqlite3.Row, str, sqlite3.Row | None]:
        run = connection.execute('SELECT * FROM runs WHERE id = ?', (run_id,)).fetchone()
        if run is None or run['work_mode'] != 'plan' or run['status'] != 'running' or run['cancel_requested_at'] is not None:
            raise PlanWriteRejected('plan_run_required')
        plan_id = request.plan_id or run['plan_id'] or str(uuid.uuid4())
        old = connection.execute('SELECT * FROM plans WHERE id = ?', (plan_id,)).fetchone()
        if old is not None:
            if old['session_id'] != run['session_id']:
                raise PlanWriteRejected('plan_not_found')
            if old['revision'] != request.expected_revision:
                raise PlanWriteRejected('plan_revision_conflict')
            if old['status'] == 'accepted':
                raise PlanWriteRejected('accepted_plan_is_immutable')
        elif request.plan_id is not None or request.expected_revision is not None:
            raise PlanWriteRejected('plan_not_found')
        if old is not None:
            current = self.file_text(self._plan(old))
            if current is not None and current != old['markdown']:
                raise PlanWriteRejected('plan_file_has_external_changes')
        if not request.markdown.strip() or len(request.markdown.encode()) > MAX_PLAN_BYTES:
            raise PlanWriteRejected('plan_size_invalid')
        return run, plan_id, old

    def write(self, run_id: str, request: WritePlan) -> PlanDocument:
        with self.database.transaction() as connection:
            run, plan_id, old = self._prepare_write(connection, run_id, request)
            revision = old['revision'] + 1 if old else 1
            self._save(connection, plan_id, run['session_id'], run_id, revision, request.title,
                       request.markdown, 'review' if request.ready_for_review else 'draft')
            connection.execute('UPDATE runs SET plan_id = ?, plan_revision = ? WHERE id = ?', (plan_id, revision, run_id))
            self._event(connection, run['session_id'], run_id, 'plan_written')
        plan = self.read(plan_id)
        self.materialize(plan, old['sha256'] if old else None)
        return plan

    def edit(self, plan_id: str, revision: int, markdown: str) -> PlanDocument:
        with self.database.transaction() as connection:
            row = connection.execute('SELECT * FROM plans WHERE id = ?', (plan_id,)).fetchone()
            if row is None:
                raise ValueError('plan_not_found')
            if row['revision'] != revision:
                raise ValueError('plan_revision_conflict')
            if row['status'] == 'accepted':
                raise ValueError('accepted_plan_is_immutable')
            if connection.execute("SELECT 1 FROM runs WHERE session_id = ? AND status IN ('running', 'waiting_input', 'waiting_agents', 'waiting_approval', 'queued', 'finalizing')", (row['session_id'],)).fetchone():
                raise ValueError('plan_run_still_active')
            current = self.file_text(self._plan(row))
            if current is not None and current not in {row['markdown'], markdown}:
                raise ValueError('plan_file_has_external_changes')
            self._save(connection, plan_id, row['session_id'], row['run_id'], revision + 1, row['title'], markdown, 'review')
            self._event(connection, row['session_id'], row['run_id'], 'plan_edited')
        plan = self.read(plan_id)
        self.materialize(plan, hashlib.sha256(current.encode()).hexdigest() if current is not None else row['sha256'])
        return plan

    def _save(self, connection: sqlite3.Connection, plan_id: str, session_id: str, run_id: str,
              revision: int, title: str, markdown: str, status: str) -> None:
        if not markdown.strip() or len(markdown) > 65536 or len(markdown.encode()) > MAX_PLAN_BYTES:
            raise ValueError('plan_size_invalid')
        sha = hashlib.sha256(markdown.encode()).hexdigest()
        timestamp = now_ms()
        connection.execute('INSERT INTO plans (id, session_id, run_id, revision, title, markdown, sha256, status, updated_at) '
                           'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET '
                           'run_id=excluded.run_id, revision=excluded.revision, title=excluded.title, markdown=excluded.markdown, '
                           'sha256=excluded.sha256, status=excluded.status, updated_at=excluded.updated_at',
                           (plan_id, session_id, run_id, revision, title, markdown, sha, status, timestamp))
        connection.execute('INSERT INTO plan_revisions VALUES (?, ?, ?, ?, ?)', (plan_id, revision, markdown, sha, timestamp))

    def _directory(self, plan: PlanDocument) -> int:
        root = self.database.data_directory
        if root is None:
            raise ValueError('planning_data_directory_unavailable')
        descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            for name in ('plans', plan.session_id, plan.id):
                try:
                    os.mkdir(name, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
                os.close(descriptor)
                descriptor = child
            return descriptor
        except BaseException:
            os.close(descriptor)
            raise

    def file_text(self, plan: PlanDocument) -> str | None:
        directory = self._directory(plan)
        try:
            try:
                descriptor = os.open('plan.md', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
            except FileNotFoundError:
                return None
            with os.fdopen(descriptor, 'rb') as stream:
                before = os.fstat(stream.fileno())
                if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > MAX_PLAN_BYTES:
                    raise ValueError('plan_file_invalid')
                data = stream.read(MAX_PLAN_BYTES + 1)
                after = os.fstat(stream.fileno())
                if len(data) > MAX_PLAN_BYTES or (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
                    raise ValueError('plan_file_changed')
                return data.decode('utf-8')
        finally:
            os.close(directory)

    def materialize(self, plan: PlanDocument, previous_sha: str | None = None) -> None:
        # Keep the DB lock across the projection so concurrent edits cannot reorder it.
        with self.database.lock:
            latest = self.read(plan.id)
            if latest.revision != plan.revision:
                raise ValueError('plan_revision_conflict')
            projected = self.database.connection().execute('SELECT projected_sha256 FROM plans WHERE id=?', (plan.id,)).fetchone()[0]
            current = self.file_text(plan)
            if current is not None:
                sha = hashlib.sha256(current.encode()).hexdigest()
                if sha == plan.sha256:
                    with self.database.transaction() as connection:
                        connection.execute('UPDATE plans SET projected_sha256=? WHERE id=? AND revision=?', (plan.sha256, plan.id, plan.revision))
                    return
                if sha not in {previous_sha, projected}:
                    raise ValueError('plan_file_has_external_changes')
            directory = self._directory(plan)
            temporary = f'.{uuid.uuid4()}.tmp'
            try:
                descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
                with os.fdopen(descriptor, 'wb') as stream:
                    stream.write(plan.markdown.encode())
                    stream.flush()
                    os.fsync(stream.fileno())
                if self.file_text(plan) != current:
                    raise ValueError('plan_file_changed')
                os.replace(temporary, 'plan.md', src_dir_fd=directory, dst_dir_fd=directory)
                os.fsync(directory)
                # The Markdown file is a recoverable projection of the committed revision.
                with self.database.transaction() as connection:
                    connection.execute('UPDATE plans SET projected_sha256=? WHERE id=? AND revision=?', (plan.sha256, plan.id, plan.revision))
            finally:
                try:
                    os.unlink(temporary, dir_fd=directory)
                except FileNotFoundError:
                    pass
                os.close(directory)

    def _request(self, row: sqlite3.Row) -> UserInputRequest:
        return UserInputRequest(id=row['id'], session_id=row['session_id'], run_id=row['run_id'],
            item_id=row['item_id'], questions=RequestUserInput.model_validate_json(row['questions_json']).questions,
            status=row['status'], response=UserInputResponse.model_validate_json(row['response_json']) if row['response_json'] else None,
            created_at=row['created_at'])

    def requests(self, session_id: str) -> list[UserInputRequest]:
        with self.database.lock:
            rows = self.database.connection().execute('SELECT q.*, r.status AS run_status FROM user_input_requests q JOIN runs r ON r.id=q.run_id WHERE q.session_id=? ORDER BY q.created_at DESC LIMIT 50', (session_id,)).fetchall()
            result = []
            size = 0
            for row in rows:
                request = self._request(row)
                size += len(request.model_dump_json().encode())
                if size > 350 * 1024:
                    break
                result.append(request)
            return result

    def for_item(self, item_id: str) -> UserInputRequest | None:
        with self.database.lock:
            row = self.database.connection().execute('SELECT * FROM user_input_requests WHERE item_id=?', (item_id,)).fetchone()
            return self._request(row) if row else None

    def unfinished(self, run_id: str) -> UserInputRequest | None:
        with self.database.lock:
            row = self.database.connection().execute("SELECT q.* FROM user_input_requests q JOIN tool_calls t ON t.item_id=q.item_id WHERE q.run_id=? AND t.status='running' ORDER BY q.created_at DESC LIMIT 1", (run_id,)).fetchone()
            return self._request(row) if row else None

    def ask(self, run_id: str, item_id: str, request: RequestUserInput) -> UserInputRequest:
        with self.database.transaction() as connection:
            run = connection.execute('SELECT * FROM runs WHERE id=?', (run_id,)).fetchone()
            if run is None or run['work_mode'] != 'plan' or run['status'] != 'running' or run['cancel_requested_at'] is not None:
                raise ValueError('plan_run_required')
            connection.execute("INSERT INTO user_input_requests VALUES (?, ?, ?, ?, ?, 'pending', NULL, ?)",
                (str(uuid.uuid4()), run['session_id'], run_id, item_id, request.model_dump_json(), now_ms()))
            transition_run(connection, run_id, frozenset({RunStatus.RUNNING}), RunStatus.WAITING_INPUT, 'user_input_requested')
        result = self.for_item(item_id)
        if result is None:
            raise ValueError('user_input_not_found')
        return result

    def answer(self, request_id: str, response: UserInputResponse) -> UserInputRequest:
        with self.database.transaction() as connection:
            row = connection.execute('SELECT * FROM user_input_requests WHERE id=?', (request_id,)).fetchone()
            if row is None:
                raise ValueError('user_input_not_found')
            request = self._request(row)
            if request.status != 'pending':
                if request.response == response:
                    return request
                raise ValueError('user_input_already_resolved')
            run = connection.execute('SELECT * FROM runs WHERE id=?', (request.run_id,)).fetchone()
            if run['status'] != 'waiting_input' or run['cancel_requested_at'] is not None:
                raise ValueError('user_input_no_longer_active')
            answers = {answer.question_id: answer for answer in response.answers}
            if len(answers) != len(response.answers):
                raise ValueError('duplicate_answer')
            if response.status == 'skipped' and answers:
                raise ValueError('skipped_input_has_answers')
            if response.status == 'answered':
                if set(answers) != {q.id for q in request.questions}:
                    raise ValueError('missing_answer')
                for question in request.questions:
                    answer = answers[question.id]
                    if len(set(answer.option_ids)) != len(answer.option_ids) or not set(answer.option_ids) <= {o.id for o in question.options}:
                        raise ValueError('invalid_answer_option')
                    if question.type != 'multi_select' and len(answer.option_ids) > 1:
                        raise ValueError('too_many_options')
                    if not answer.option_ids and not answer.text.strip():
                        raise ValueError('empty_answer')
            connection.execute('UPDATE user_input_requests SET status=?, response_json=? WHERE id=?', (response.status, response.model_dump_json(), request_id))
            transition_run(connection, request.run_id, frozenset({RunStatus.WAITING_INPUT}), RunStatus.QUEUED, 'user_input_answered')
            return request.model_copy(update={'status': response.status, 'response': response})

    @staticmethod
    def _event(connection: sqlite3.Connection, session_id: str, run_id: str, reason: str) -> None:
        append_event(connection, EventType.RUN_UPDATED, now_ms(), {'reason': reason}, session_id=session_id, run_id=run_id)

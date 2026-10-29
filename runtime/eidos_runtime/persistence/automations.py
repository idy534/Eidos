from __future__ import annotations

from collections.abc import Callable
import sqlite3
from uuid import uuid4

from eidos_runtime.db.database import Repository, CommittedMutation, now_ms
from eidos_runtime.db.events import append_event
from eidos_runtime.domain.automation import (
    Automation, AutomationBinding, AutomationError, AutomationOccurrence, AutomationOrigin, AutomationExecution,
)
from eidos_runtime.runtime.state_machine import EventType

ACTIVE_SQL = "('queued','running','waiting_approval','waiting_input','waiting_agents','finalizing')"


class AutomationRepository(Repository):
    @staticmethod
    def session_project_in(connection: sqlite3.Connection, session_id: str) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT COALESCE(s.project_id,w.project_id,p.id) AS project_id FROM sessions s "
            "LEFT JOIN worktrees w ON w.id=COALESCE(s.worktree_id,s.associated_worktree_id) "
            "LEFT JOIN projects p ON p.id=s.project_id OR (s.project_id IS NULL AND p.workspace_root=s.workspace_root) WHERE s.id=?", (session_id,),
        ).fetchone()
    @staticmethod
    def read_in(connection: sqlite3.Connection, task_id: str) -> Automation:
        row = connection.execute("SELECT config_json FROM scheduled_tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise AutomationError("automation_not_found", "未找到这个定时任务。")
        return Automation.model_validate_json(row[0])

    def read(self, task_id: str) -> Automation:
        with self.lock:
            return self.read_in(self._connection(), task_id)

    def replay(self, operation_id: str, request: dict[str, object]) -> Automation | None:
        value = self.database.operation_result(operation_id, "automation/mutate", request)
        return Automation.model_validate(value) if value is not None else None

    def list(self, after: int = 0, limit: int = 20) -> list[tuple[int, Automation]]:
        with self.lock:
            return [(row[0], Automation.model_validate_json(row[1])) for row in self._connection().execute(
                "SELECT sequence, config_json FROM scheduled_tasks WHERE sequence>? ORDER BY sequence LIMIT ?", (after, limit),
            ).fetchall()]

    def binding(self, session_id: str) -> AutomationBinding:
        with self.lock:
            connection = self._connection()
            row = self.session_project_in(connection, session_id)
            if row is None or connection.execute("SELECT 1 FROM agent_delegations WHERE child_session_id=?", (session_id,)).fetchone():
                raise AutomationError("automation_target_unavailable", "目标会话不存在或不能执行定时任务。")
            return AutomationBinding(kind="existing_session", session_id=session_id, project_id=row[0])

    def project_root(self, project_id: str) -> str:
        with self.lock:
            row = self._connection().execute("SELECT workspace_root FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is None:
            raise AutomationError("automation_target_unavailable", "目标项目已不可用。")
        return row[0]

    def validate_execution(self, project_id: str, execution: AutomationExecution) -> None:
        with self.lock:
            row = self._connection().execute("SELECT git_repository_root FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is None or (execution.execution_mode == "worktree" and row[0] is None):
            raise AutomationError("automation_target_unavailable", "工作树执行需要一个可用的 Git 项目。")

    @staticmethod
    def validate_binding_in(connection: sqlite3.Connection, binding: AutomationBinding) -> None:
        if binding.kind == "existing_session":
            row = AutomationRepository.session_project_in(connection, binding.session_id or "")
            if row is None or row[0] != binding.project_id:
                raise AutomationError("automation_target_unavailable", "目标会话或项目归属已改变。")
        elif binding.project_id is not None and connection.execute("SELECT 1 FROM projects WHERE id=?", (binding.project_id,)).fetchone() is None:
            raise AutomationError("automation_target_unavailable", "目标项目已不可用。")

    @staticmethod
    def save_in(connection: sqlite3.Connection, task: Automation) -> dict[str, object]:
        connection.execute(
            "INSERT INTO scheduled_tasks(id,revision,status,config_json,created_at,updated_at) VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,status=excluded.status,config_json=excluded.config_json,updated_at=excluded.updated_at",
            (task.id, task.revision, task.status, task.model_dump_json(by_alias=True), task.created_at, task.updated_at),
        )
        return append_event(connection, EventType.AUTOMATION_CHANGED, task.updated_at, {"id": task.id})

    def save(self, task: Automation, *, expected: int | None, operation_id: str, request: dict[str, object], cancel: Callable[[], bool] = lambda: False) -> CommittedMutation[Automation]:
        def write(connection: sqlite3.Connection) -> CommittedMutation[Automation]:
            if cancel():
                raise AutomationError("automation_canceled", "对话已停止，任务没有修改。")
            if expected is not None:
                current = self.read_in(connection, task.id)
                if current.revision != expected:
                    raise AutomationError("automation_revision_conflict", "任务已被修改，请重新查看后再更新。")
                if current.deleted_at is not None:
                    return CommittedMutation(current, ())
                if current == task:
                    return CommittedMutation(current, ())
            if task.deleted_at is None and task.status == "ACTIVE":
                self.validate_binding_in(connection, task.target)
            event = self.save_in(connection, task)
            return CommittedMutation(task, (event,))
        return self._write_committed(
            write, operation_id=operation_id, operation_scope="automation/mutate", operation_request=request,
            serialize_value=lambda value: value.to_wire_dict(), deserialize_value=Automation.model_validate,
        )

    def origin(self, run_id: str) -> AutomationOrigin | None:
        with self.lock:
            row = self._connection().execute("SELECT origin_json FROM automation_run_origins WHERE run_id=?", (run_id,)).fetchone()
        return AutomationOrigin.model_validate_json(row[0]) if row else None

    def origins_for_runs(self, run_ids: list[str]) -> dict[str, AutomationOrigin]:
        if not run_ids:
            return {}
        with self.lock:
            rows = self._connection().execute(
                "SELECT run_id,origin_json FROM automation_run_origins WHERE run_id IN (" + ",".join("?" for _ in run_ids) + ")", run_ids,
            ).fetchall()
        return {row[0]: AutomationOrigin.model_validate_json(row[1]) for row in rows}

    def logical_request(self, run_id: str) -> str:
        with self.lock:
            connection = self._connection()
            visited: set[str] = set()
            while run_id not in visited:
                visited.add(run_id)
                row = connection.execute("SELECT source_run_id FROM run_revisions WHERE run_id=? AND revision_kind='regenerate'", (run_id,)).fetchone()
                if row is None:
                    return run_id
                run_id = row[0]
        raise AutomationError("automation_source_not_allowed", "请求来源无法核验。")

    @staticmethod
    def active_run_in(connection: sqlite3.Connection, task_id: str) -> bool:
        return connection.execute(
            "SELECT 1 FROM automation_run_origins a JOIN runs r ON r.id=a.run_id "
            f"WHERE json_extract(a.origin_json,'$.automationId')=? AND r.status IN {ACTIVE_SQL} LIMIT 1", (task_id,),
        ).fetchone() is not None

    def history(self, task_id: str, after: int = 0) -> tuple[list[AutomationOccurrence], str | None]:
        with self.lock:
            rows = self._connection().execute("SELECT o.*,r.status AS run_status,r.reconciliation_required FROM scheduled_occurrences o LEFT JOIN runs r ON r.id=o.run_id WHERE o.task_id=? AND (?=0 OR o.sequence<?) ORDER BY o.sequence DESC LIMIT 21", (task_id, after, after)).fetchall()
        return [self._occurrence(row) for row in rows[:20]], str(rows[19]["sequence"]) if len(rows) > 20 else None

    @staticmethod
    def _occurrence(row: sqlite3.Row) -> AutomationOccurrence:
        snapshot = Automation.model_validate_json(row["config_json"])
        return AutomationOccurrence(id=row["id"], automation_id=row["task_id"], revision=snapshot.revision,
            scheduled_for=row["scheduled_for"], status=row["status"], snapshot=snapshot,
            run_id=row["run_id"], session_id=row["session_id"], reason=row["reason"], created_at=row["created_at"],
            run_status=row["run_status"] if "run_status" in row.keys() else None,
            reconciliation_required=bool(row["reconciliation_required"]) if "reconciliation_required" in row.keys() else False)

    def occurrence(self, occurrence_id: str) -> AutomationOccurrence:
        with self.lock:
            row = self._connection().execute("SELECT * FROM scheduled_occurrences WHERE id=?", (occurrence_id,)).fetchone()
        if row is None:
            raise AutomationError("automation_not_found", "执行记录不存在。")
        return self._occurrence(row)

    def claim(self, task_id: str, scheduled_for: str) -> AutomationOccurrence | None:
        with self.lock, self._connection() as connection:
            task = self.read_in(connection, task_id)
            if task.status != "ACTIVE" or task.deleted_at is not None:
                return None
            if connection.execute("SELECT 1 FROM scheduled_occurrences WHERE task_id=? AND scheduled_for=?", (task_id, scheduled_for)).fetchone():
                return None
            reason: str | None = None
            if self.active_run_in(connection, task_id) or connection.execute(
                "SELECT 1 FROM scheduled_occurrences WHERE task_id=? AND status='preparing' LIMIT 1", (task_id,),
            ).fetchone():
                # One preparation can occupy the unique partial index.
                reason = "上次执行未结束"
            if task.target.session_id and connection.execute(f"SELECT 1 FROM runs WHERE session_id=? AND status IN {ACTIVE_SQL}", (task.target.session_id,)).fetchone():
                reason = "会话忙碌"
            occurrence_id = str(uuid4())
            connection.execute(
                "INSERT INTO scheduled_occurrences(id,task_id,scheduled_for,config_json,status,reason,created_at) VALUES(?,?,?,?,?,?,?)",
                (occurrence_id, task_id, scheduled_for, task.model_dump_json(by_alias=True), "skipped" if reason else "preparing", reason, now_ms()),
            )
            append_event(connection, EventType.AUTOMATION_CHANGED, now_ms(), {"id": task_id})
            return self.occurrence(occurrence_id)

    @staticmethod
    def pause_in(connection: sqlite3.Connection, task: Automation, reason: str) -> None:
        if task.deleted_at is None:
            AutomationRepository.save_in(connection, task.model_copy(update={"status": "PAUSED", "pause_reason": reason, "revision": task.revision + 1, "updated_at": now_ms()}))

    def pause_expired(self, task: Automation) -> None:
        with self.lock, self._connection() as connection:
            current = self.read_in(connection, task.id)
            preparing = connection.execute(
                "SELECT 1 FROM scheduled_occurrences WHERE task_id=? AND status='preparing' "
                "AND json_extract(config_json,'$.revision')=? LIMIT 1", (task.id, task.revision),
            ).fetchone()
            if current.revision == task.revision and current.status == "ACTIVE" and preparing is None:
                self.pause_in(connection, current, "执行时间已过去")

    def finish(self, occurrence_id: str, reason: str, *, pause: bool = False, skipped: bool = False) -> None:
        with self.lock, self._connection() as connection:
            occurrence = self.occurrence(occurrence_id)
            connection.execute("UPDATE scheduled_occurrences SET status=?,reason=? WHERE id=? AND status='preparing'", ("skipped" if skipped else "failed", reason[:512], occurrence_id))
            task = self.read_in(connection, occurrence.automation_id)
            if pause and task.revision == occurrence.revision:
                self.pause_in(connection, task, reason[:512])
            else:
                append_event(connection, EventType.AUTOMATION_CHANGED, now_ms(), {"id": task.id})

    def prepared_session(self, occurrence_id: str, session_id: str) -> None:
        with self.lock, self._connection() as connection:
            connection.execute("UPDATE scheduled_occurrences SET session_id=? WHERE id=? AND status='preparing'", (session_id, occurrence_id))
            occurrence = self.occurrence(occurrence_id)
            append_event(connection, EventType.AUTOMATION_CHANGED, now_ms(), {"id": occurrence.automation_id})

    def recover(self) -> None:
        with self.lock, self._connection() as connection:
            ids = [row[0] for row in connection.execute("SELECT DISTINCT task_id FROM scheduled_occurrences WHERE status='preparing'")]
            connection.execute("UPDATE scheduled_occurrences SET status='interrupted',reason='准备过程被中断' WHERE status='preparing'")
            for task_id in ids:
                append_event(connection, EventType.AUTOMATION_CHANGED, now_ms(), {"id": task_id})

    @staticmethod
    def pause_targets(connection: sqlite3.Connection, *, session_id: str | None = None, project_id: str | None = None) -> None:
        for row in connection.execute("SELECT config_json FROM scheduled_tasks").fetchall():
            task = Automation.model_validate_json(row[0])
            if (session_id and task.target.session_id == session_id) or (project_id and task.target.project_id == project_id):
                AutomationRepository.pause_in(connection, task, "目标会话或项目已删除")

    def accept(self, occurrence_id: str, write_run: Callable[[sqlite3.Connection, AutomationOrigin], tuple[dict[str, object], dict[str, object]]], session_id: str) -> tuple[dict[str, object], dict[str, object]]:
        with self.lock, self._connection() as connection:
            occurrence = self.occurrence(occurrence_id)
            task = self.read_in(connection, occurrence.automation_id)
            if occurrence.status != "preparing" or task.revision != occurrence.revision or task.status != "ACTIVE" or task.deleted_at is not None:
                raise AutomationError("automation_revision_conflict", "准备期间任务配置已改变，本次执行已停止。")
            self.validate_binding_in(connection, task.target)
            if self.active_run_in(connection, task.id):
                raise AutomationError("automation_session_busy", "上次执行未结束，本次执行已跳过。")
            if connection.execute(f"SELECT 1 FROM runs WHERE session_id=? AND status IN {ACTIVE_SQL}", (session_id,)).fetchone():
                raise AutomationError("automation_session_busy", "会话忙碌，本次执行已跳过。")
            origin = AutomationOrigin(automation_id=task.id, revision=task.revision, occurrence_id=occurrence.id, scheduled_for=occurrence.scheduled_for)
            run, item = write_run(connection, origin)
            connection.execute("INSERT INTO automation_run_origins(run_id,origin_json) VALUES(?,?)", (run["id"], origin.model_dump_json(by_alias=True)))
            connection.execute("UPDATE scheduled_occurrences SET status='submitted',run_id=?,session_id=? WHERE id=?", (run["id"], session_id, occurrence.id))
            if task.kind == "at":
                self.pause_in(connection, task, "one_shot_fired")
            else:
                append_event(connection, EventType.AUTOMATION_CHANGED, now_ms(), {"id": task.id})
            return run, item

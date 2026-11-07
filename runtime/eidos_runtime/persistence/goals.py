from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import nullcontext
import json
import sqlite3
from uuid import uuid4

from eidos_runtime.db.database import CommittedMutation, Repository, now_ms
from eidos_runtime.db.events import append_event
from eidos_runtime.domain.goal import GoalActivity, GoalDeclaration, GoalError, GoalSample, GoalView, SessionGoal, GoalRunOrigin
from eidos_runtime.protocol.goals import GoalRequest
from eidos_runtime.protocol.methods import RunStartRequestDto
from eidos_runtime.domain.automation import AutomationModel
from eidos_runtime.runtime.state_machine import EventType

ACTIVE_SQL = "('queued','running','waiting_approval','waiting_input','waiting_agents','finalizing')"


def _goal(connection: sqlite3.Connection, goal_id: str) -> SessionGoal:
    row = connection.execute("SELECT goal_json FROM session_goals WHERE id=?", (goal_id,)).fetchone()
    if row is None:
        raise GoalError("goal_not_found", "目标不存在。")
    return SessionGoal.model_validate_json(row[0])


def _current(connection: sqlite3.Connection, session_id: str) -> SessionGoal | None:
    row = connection.execute("SELECT goal_json FROM session_goals WHERE session_id=? ORDER BY sequence DESC LIMIT 1", (session_id,)).fetchone()
    return SessionGoal.model_validate_json(row[0]) if row else None


def _save(connection: sqlite3.Connection, goal: SessionGoal, *, revise: bool = True, run_id: str | None = None, use_latest_run: bool = True) -> SessionGoal:
    previous = _goal(connection, goal.id)
    value = goal.model_copy(update={"revision": goal.revision + int(revise), "updated_at": now_ms()})
    connection.execute("UPDATE session_goals SET status=?,revision=?,goal_json=? WHERE id=?",
                       (value.status, value.revision, value.model_dump_json(), value.id))
    kind = "status" if previous.status != value.status else "edited" if previous.objective_revision != value.objective_revision else "progress"
    latest = connection.execute("SELECT id FROM runs WHERE session_id=? ORDER BY creation_seq DESC LIMIT 1", (value.session_id,)).fetchone()
    append_event(connection, EventType.GOAL_CHANGED, now_ms(), {"sessionId": value.session_id,
        "status": value.status, "kind": kind, "reason": value.status_reason},
        session_id=value.session_id, run_id=run_id or (latest[0] if latest and use_latest_run else None))
    if value.status != "active":
        connection.execute("UPDATE goal_continuations SET status='obsolete' WHERE goal_id=? AND status='pending'", (value.id,))
    return value


def _intend(connection: sqlite3.Connection, goal: SessionGoal, trigger: str, source: str | None = None) -> None:
    if goal.status != "active":
        return
    connection.execute("INSERT OR IGNORE INTO goal_continuations(id,goal_id,objective_revision,activation,trigger,source_run_id,status) VALUES(?,?,?,?,?,?,'pending')",
                       (str(uuid4()), goal.id, goal.objective_revision, goal.activation, trigger, source))


def _attach(connection: sqlite3.Connection, run_id: str, goal: SessionGoal, trigger: str) -> None:
    sequence = connection.execute("SELECT coalesce(max(sequence),0)+1 FROM goal_runs WHERE goal_id=?", (goal.id,)).fetchone()[0]
    changed = connection.execute("INSERT OR IGNORE INTO goal_runs(run_id,goal_id,objective_revision,activation,trigger,sequence,reference_ids_json) VALUES(?,?,?,?,?,?,?)",
                                (run_id, goal.id, goal.objective_revision, goal.activation, trigger, sequence, json.dumps(goal.reference_ids)))
    if changed.rowcount:
        _save(connection, goal.model_copy(update={"audit_runs": goal.audit_runs + 1}), revise=False, run_id=run_id)


def attach_goal_run(connection: sqlite3.Connection, run_id: str) -> None:
    run = connection.execute("SELECT session_id,work_mode FROM runs WHERE id=?", (run_id,)).fetchone()
    if run is None or run["work_mode"] != "execute":
        return
    if connection.execute("SELECT 1 FROM agent_delegations WHERE child_session_id=?", (run["session_id"],)).fetchone():
        return
    if connection.execute("SELECT 1 FROM automation_run_origins WHERE run_id=? UNION ALL SELECT 1 FROM channel_run_origins WHERE run_id=? LIMIT 1", (run_id, run_id)).fetchone():
        return
    goal = _current(connection, run["session_id"])
    if goal and goal.status == "active":
        _attach(connection, run_id, goal, "user_input")


def _view(connection: sqlite3.Connection, goal: SessionGoal | None) -> GoalView:
    if goal is None:
        return GoalView()
    # Child usage belongs to the parent goal. UNION avoids counting a Run twice.
    usage = connection.execute("""WITH owned(id) AS (
        SELECT run_id FROM goal_runs WHERE goal_id=?
        UNION SELECT r.id FROM goal_runs g JOIN agent_delegations d ON d.parent_run_id=g.run_id
        JOIN runs r ON r.session_id=d.child_session_id WHERE g.goal_id=?
    ) SELECT coalesce(sum(coalesce(json_extract(a.usage_json,'$.input_tokens'),0)+coalesce(json_extract(a.usage_json,'$.output_tokens'),0)),0),
        coalesce(max(a.usage_json IS NULL OR json_extract(a.usage_json,'$.input_tokens') IS NULL OR json_extract(a.usage_json,'$.output_tokens') IS NULL),0)
        FROM model_attempts a JOIN steps s ON s.id=a.step_id JOIN owned o ON o.id=s.run_id""", (goal.id, goal.id)).fetchone()
    times = connection.execute("SELECT count(*),coalesce(sum(r.total_effective_ms),0) FROM goal_runs g JOIN runs r ON r.id=g.run_id WHERE g.goal_id=?", (goal.id,)).fetchone()
    activities = []
    for row in connection.execute("SELECT id,run_id,occurred_at,payload_json FROM events WHERE session_id=? AND event_type='goal.changed' "
        "AND json_extract(payload_json,'$.kind') IN ('started','edited','status') ORDER BY id DESC LIMIT 20", (goal.session_id,)):
        payload = json.loads(row["payload_json"])
        activities.append(GoalActivity(event_id=row["id"], run_id=row["run_id"], occurred_at=row["occurred_at"],
            status="paused" if payload["status"] == "budget_limited" else payload["status"], kind=payload["kind"], reason=payload.get("reason")))
    return GoalView(goal=goal, tokens_used=usage[0], usage_incomplete=bool(usage[1]), run_count=times[0], effective_ms=times[1], activities=tuple(reversed(activities)))


def settle_goal_run(connection: sqlite3.Connection, run_id: str, status: str, reason: str | None) -> None:
    # A queued goal can be steered into an older Run whose resources remain
    # frozen. Its durable continuation still follows that owner's cancellation.
    if status != "succeeded":
        for intent in connection.execute("SELECT goal_id,objective_revision,activation FROM goal_continuations WHERE source_run_id=? AND status='pending'", (run_id,)).fetchall():
            pending_goal = _goal(connection, intent["goal_id"])
            if pending_goal.status == "active" and (pending_goal.objective_revision, pending_goal.activation) == (intent["objective_revision"], intent["activation"]):
                _save(connection, pending_goal.model_copy(update={"status": "paused" if status in {"canceled", "interrupted"} else "blocked",
                    "status_reason": {"canceled": "run_canceled", "interrupted": "runtime_restarted", "failed": "run_failed", "stopped": "run_stopped"}[status],
                    "status_detail": reason}), run_id=run_id)
    row = connection.execute("SELECT * FROM goal_runs WHERE run_id=?", (run_id,)).fetchone()
    if row is None:
        return
    goal = _goal(connection, row["goal_id"])
    if goal.status != "active":
        return
    sampled = GoalSample.model_validate_json(row["sample_json"]) if row["sample_json"] else GoalSample(
        goal_id=row["goal_id"], objective_revision=row["objective_revision"], activation=row["activation"])
    if sampled != goal.sample():
        _intend(connection, goal, "continuation")
        return
    if status == "succeeded" and connection.execute(
        "SELECT 1 FROM runs future JOIN items i ON i.run_id=future.id AND i.kind='user_message' "
        "WHERE future.session_id=? AND future.status='queued' AND future.creation_seq>(SELECT creation_seq FROM runs WHERE id=?) LIMIT 1",
        (goal.session_id, run_id),
    ).fetchone():
        _intend(connection, goal, "continuation", run_id)
        return
    if status in {"failed", "stopped"} and row["activation"] < goal.activation:
        # Feedback or resume during this Run started a newer activation. The
        # older Run's failure must not block it; queued plain input does not.
        _intend(connection, goal, "continuation", run_id)
        return
    if status != "succeeded":
        target = "paused" if status in {"canceled", "interrupted"} else "blocked"
        why = {"canceled": "run_canceled", "interrupted": "runtime_restarted", "failed": "run_failed", "stopped": "run_stopped"}[status]
        _save(connection, goal.model_copy(update={"status": target, "status_reason": why, "status_detail": reason}), run_id=run_id)
        return
    if row["candidate_json"] and row["sample_json"]:
        # Any later tool can make a completion claim stale. The model can
        # declare again after it has observed that tool's actual result.
        later_tool = connection.execute("SELECT 1 FROM items a JOIN items b ON b.run_id=a.run_id WHERE a.id=? AND b.kind='tool_call' AND b.ordinal>a.ordinal LIMIT 1", (row["candidate_item_id"],)).fetchone()
        if sampled == goal.sample() and later_tool is None:
            declaration = GoalDeclaration.model_validate_json(row["candidate_json"])
            _save(connection, goal.model_copy(update={"status": declaration.status,
                "status_reason": "model_declared" if declaration.status == "complete" else "model_blocked",
                "status_detail": declaration.summary, "evidence": declaration.evidence}), run_id=run_id)
            return
    known = json.loads(connection.execute("SELECT progress_json FROM session_goals WHERE id=?", (goal.id,)).fetchone()[0])
    observed: list[str] = []
    observed.extend(row[0] for row in connection.execute(
        "SELECT t.progress_fingerprint FROM tool_calls t JOIN items i ON i.id=t.item_id "
        "WHERE i.run_id=? AND t.status='completed' AND t.progress_fingerprint IS NOT NULL "
        "AND json_extract(t.result_json,'$.outcome')='success' AND t.tool_name NOT IN ('update_goal','automation') "
        "ORDER BY t.creation_seq DESC LIMIT 256", (run_id,)))
    # Reuse existing durable progress evidence; no model-supplied counters and
    # no new fingerprints. A repeated read/error does not reset the goal guard.
    fresh = set(observed) - set(known)
    idle = 0 if fresh else goal.consecutive_idle_runs + 1
    connection.execute("UPDATE session_goals SET progress_json=? WHERE id=?", (json.dumps(list(dict.fromkeys([*known, *observed]))[-256:]), goal.id))
    goal = _save(connection, goal.model_copy(update={"consecutive_idle_runs": idle,
        **({"status": "blocked", "status_reason": "idle_runs"} if idle >= 3 else {})}), revise=idle >= 3, run_id=run_id)
    _intend(connection, goal, "continuation", run_id)


def recover_goals(connection: sqlite3.Connection) -> None:
    # Pause all automatic advancement, including the gap between two Runs.
    for row in connection.execute("SELECT id FROM session_goals WHERE status='active'").fetchall():
        goal = _goal(connection, row[0])
        _save(connection, goal.model_copy(update={"status": "paused", "status_reason": "runtime_restarted"}))
    # Never let a pre-restart automatic Run start via normal FIFO recovery.
    connection.execute("UPDATE runs SET cancel_requested_at=coalesce(cancel_requested_at,?) WHERE id IN (SELECT run_id FROM goal_runs WHERE trigger!='user_input') AND status IN " + ACTIVE_SQL, (now_ms(),))
    connection.execute("UPDATE goal_runs SET candidate_json=NULL,candidate_item_id=NULL")


def pause_goal_for_rewind(connection: sqlite3.Connection, session_id: str) -> None:
    goal = _current(connection, session_id)
    if goal is not None and goal.status == "active":
        _save(connection, goal.model_copy(update={"status": "paused", "status_reason": "workspace_rewound"}))


def pause_goal_for_plan(connection: sqlite3.Connection, session_id: str, run_id: str) -> None:
    goal = _current(connection, session_id)
    if goal is not None and goal.status == "active":
        _save(connection, goal.model_copy(update={"status": "paused", "status_reason": "plan_selected"}), run_id=run_id)


def goal_for_queued_input(connection: sqlite3.Connection, run_id: str) -> SessionGoal | None:
    row = connection.execute("SELECT goal_id FROM goal_runs WHERE run_id=? AND trigger='user_input' AND sequence=1", (run_id,)).fetchone()
    if row is None:
        return None
    goal = _goal(connection, row[0])
    return goal if goal.status not in {"complete", "cleared"} else None


def update_queued_goal(connection: sqlite3.Connection, goal: SessionGoal, run_id: str, text: str) -> None:
    if not text.strip() or len(text) > 4000:
        raise ValueError("目标正文需要包含 1 到 4,000 个字符。")
    changed = _save(connection, goal.model_copy(update={"objective": text, "objective_revision": goal.objective_revision + 1,
        "activation": goal.activation + 1, "consecutive_idle_runs": 0, "status_detail": None, "evidence": ()}), run_id=run_id)
    connection.execute("UPDATE goal_runs SET objective_revision=?,activation=?,sample_json=NULL,candidate_json=NULL,candidate_item_id=NULL WHERE run_id=?",
        (changed.objective_revision, changed.activation, run_id))
    connection.execute("UPDATE goal_continuations SET status='obsolete' WHERE goal_id=? AND status='pending'", (goal.id,))


def remove_queued_goal_input(connection: sqlite3.Connection, goal: SessionGoal, *, target_run_id: str | None) -> None:
    if target_run_id is not None:
        # The accepted mailbox input belongs to the active Run's frozen
        # resources. Continue the goal only after that Run has released them.
        goal = _save(connection, goal.model_copy(update={"audit_runs": 0, "consecutive_idle_runs": 0}), use_latest_run=False)
        _intend(connection, goal, "continuation", target_run_id)
    else:
        _save(connection, goal.model_copy(update={"status": "cleared", "status_reason": "queued_input_deleted"}), use_latest_run=False)


class GoalRepository(Repository):
    def origins_for_runs(self, run_ids: list[str]) -> dict[str, GoalRunOrigin]:
        if not run_ids:
            return {}
        with self.lock:
            return {row["run_id"]: GoalRunOrigin(goal_id=row["goal_id"], trigger=row["trigger"], sequence=row["sequence"])
                for row in self._connection().execute("SELECT run_id,goal_id,trigger,sequence FROM goal_runs WHERE run_id IN (" + ",".join("?" for _ in run_ids) + ")", run_ids)}

    def view(self, session_id: str) -> GoalView:
        with self.lock:
            connection = self._connection()
            return _view(connection, _current(connection, session_id))

    def for_run(self, run_id: str) -> GoalView:
        with self.lock:
            connection = self._connection()
            row = connection.execute("SELECT goal_id FROM goal_runs WHERE run_id=?", (run_id,)).fetchone()
            return _view(connection, _goal(connection, row[0]) if row else None)

    def attach(self, run_id: str) -> None:
        with self.lock, self._connection() as connection:
            attach_goal_run(connection, run_id)

    def execute(self, request: GoalRequest) -> GoalView:
        def write(connection: sqlite3.Connection) -> GoalView:
            if connection.execute("SELECT 1 FROM sessions WHERE id=?", (request.session_id,)).fetchone() is None:
                raise GoalError("goal_not_found", "会话不存在。")
            if connection.execute("SELECT 1 FROM agent_delegations WHERE child_session_id=?", (request.session_id,)).fetchone():
                raise GoalError("goal_source_not_allowed", "请在主会话中设置目标。")
            goal = _current(connection, request.session_id)
            if request.action == "get":
                return _view(connection, goal)
            if goal is not None and request.expected_revision != goal.revision:
                raise GoalError("goal_revision_conflict", "目标已变化，请重新查看后操作。")
            if goal is None and request.expected_revision is not None:
                raise GoalError("goal_revision_conflict", "目标已变化，请重新查看后操作。")
            if goal is None or goal.status == "cleared":
                raise GoalError("goal_not_found", "当前没有目标。")
            changes: dict[str, object] = {}
            if request.action == "pause":
                if goal.status != "active":
                    raise GoalError("goal_invalid_state", "只有进行中的目标可以暂停续跑。")
                changes.update(status="paused", status_reason="user_paused")
            elif request.action == "resume":
                if goal.status not in {"paused", "blocked"}:
                    raise GoalError("goal_invalid_state", "这个目标不能继续。")
                changes.update(status="active", status_reason=None, status_detail=None, activation=goal.activation + 1,
                               audit_runs=0, consecutive_idle_runs=0)
                if request.model is not None:
                    changes.update(model=request.model, approval_mode=request.approval_mode,
                                   full_access_confirmation=request.full_access_confirmation)
            elif request.action == "clear":
                changes.update(status="cleared", status_reason="user_cleared")
            connection.execute("UPDATE goal_runs SET candidate_json=NULL,candidate_item_id=NULL WHERE goal_id=?", (goal.id,))
            connection.execute("UPDATE goal_continuations SET status='obsolete' WHERE goal_id=? AND status='pending'", (goal.id,))
            goal = _save(connection, goal.model_copy(update=changes))
            _intend(connection, goal, "resume" if request.action == "resume" else "continuation")
            return _view(connection, goal)
        if request.action == "get":
            with self.lock:
                return write(self._connection())
        return self._write_committed(lambda connection: CommittedMutation(write(connection), ()),
            operation_id=request.operation_id, operation_scope="goal/" + request.action,
            operation_request=request.model_dump(mode="json", by_alias=True),
            serialize_value=lambda value: value.to_wire_dict(),
            deserialize_value=lambda value: GoalView.model_validate_json(json.dumps(value))).value

    def admit_input(self, request: RunStartRequestDto,
                    write_run: Callable[[sqlite3.Connection], tuple[dict[str, object], dict[str, object]]], *,
                    operation_request: dict[str, object]) -> tuple[dict[str, object], dict[str, object]]:
        """Admit goal creation or version-checked feedback with its user Run.

        operation_request must be the run/start replay key, so a retry with
        the same operationId returns the original Run.
        """
        def write(connection: sqlite3.Connection) -> CommittedMutation[tuple[dict[str, object], dict[str, object]]]:
            if (not request.goal_mode and request.goal_revision is None) or request.work_mode != "execute":
                raise GoalError("goal_invalid_state", "目标必须通过普通执行请求创建。")
            if connection.execute("SELECT 1 FROM agent_delegations WHERE child_session_id=?", (request.session_id,)).fetchone():
                raise GoalError("goal_source_not_allowed", "请在主会话中设置目标。")
            previous = _current(connection, request.session_id)
            if (previous.revision if previous else None) != request.goal_revision:
                raise GoalError("goal_revision_conflict", "目标已变化，请重新查看后操作。")
            if not request.goal_mode and (previous is None or previous.status != "active"):
                raise GoalError("goal_revision_conflict", "目标已结束或暂停，请重新查看后操作。")
            run, item = write_run(connection)
            run_id = str(run["id"])
            if not request.goal_mode:
                connection.execute("UPDATE goal_runs SET candidate_json=NULL,candidate_item_id=NULL WHERE goal_id=?", (previous.id,))
                model = AutomationModel(model_id=request.model_id, reasoning_selection=request.reasoning_selection)
                _save(connection, previous.model_copy(update={"model": model, "approval_mode": request.approval_mode,
                    "full_access_confirmation": request.full_access_confirmation, "activation": previous.activation + 1,
                    "audit_runs": 0, "consecutive_idle_runs": 0}), run_id=run_id)
                connection.execute("UPDATE goal_continuations SET status='obsolete' WHERE goal_id=? AND status='pending'", (previous.id,))
                return CommittedMutation((run, item), ())
            if previous and previous.status not in {"complete", "cleared"}:
                _save(connection, previous.model_copy(update={"status": "cleared", "status_reason": "replaced"}), run_id=run_id)
            goal = SessionGoal(id=str(uuid4()), session_id=request.session_id, objective=request.user_input,
                status="active", revision=1, objective_revision=1, activation=1,
                model=AutomationModel(model_id=request.model_id, reasoning_selection=request.reasoning_selection),
                approval_mode=request.approval_mode, full_access_confirmation=request.full_access_confirmation,
                reference_ids=tuple(request.references), created_at=now_ms(), updated_at=now_ms())
            connection.execute("INSERT INTO session_goals(id,session_id,status,revision,goal_json) VALUES(?,?,?,?,?)",
                (goal.id, goal.session_id, goal.status, goal.revision, goal.model_dump_json()))
            _attach(connection, run_id, goal, "user_input")
            run["goalOrigin"] = GoalRunOrigin(goal_id=goal.id, trigger="user_input", sequence=1).to_wire_dict()
            append_event(connection, EventType.GOAL_CHANGED, now_ms(),
                {"sessionId": goal.session_id, "status": goal.status, "kind": "started"}, session_id=goal.session_id, run_id=run_id)
            return CommittedMutation((run, item), ())
        return self._write_committed(write, operation_id=request.operation_id, operation_scope="run/start",
            operation_request=operation_request,
            serialize_value=lambda pair: {"run": pair[0], "item": pair[1]},
            deserialize_value=lambda value: (value["run"], value["item"])).value

    def bind_sample(self, run_id: str, sample: GoalSample | None) -> None:
        with self.lock, self._connection() as connection:
            connection.execute("UPDATE goal_runs SET sample_json=? WHERE run_id=?", (sample.model_dump_json() if sample else None, run_id))

    def invalidate_candidate(self, run_id: str) -> None:
        with self.lock, self._connection() as connection:
            connection.execute("UPDATE goal_runs SET candidate_json=NULL,candidate_item_id=NULL WHERE run_id=?", (run_id,))

    def declare(self, run_id: str, item_id: str, declaration: GoalDeclaration, *,
                prepare_only: bool = False, connection: sqlite3.Connection | None = None) -> GoalView:
        with self.lock, (nullcontext(connection) if connection is not None else self._connection()) as connection:
            if declaration.status == "complete" and not declaration.evidence:
                raise GoalError("goal_evidence_required", "完成声明需要逐条列出需求和证据。")
            row = connection.execute("SELECT * FROM goal_runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None or row["sample_json"] is None:
                raise GoalError("goal_not_active", "当前请求没有活动目标。")
            goal = _goal(connection, row["goal_id"])
            if goal.status != "active":
                raise GoalError("goal_not_active", "目标已暂停或结束。")
            if GoalSample.model_validate_json(row["sample_json"]) != goal.sample():
                raise GoalError("goal_revision_conflict", "目标已修改，请读取新目标后重新审计。")
            run = connection.execute("SELECT status,cancel_requested_at FROM runs WHERE id=?", (run_id,)).fetchone()
            if run is None or run["status"] != "running" or run["cancel_requested_at"] is not None:
                raise GoalError("goal_not_active", "当前执行已停止。")
            if declaration.status == "blocked" and goal.audit_runs < 3:
                raise GoalError("goal_blocked_too_early", "本次激活至少需要三个目标 Run；模型仍须确认同一阻碍连续存在。")
            if not prepare_only:
                connection.execute("UPDATE goal_runs SET candidate_json=?,candidate_item_id=? WHERE run_id=?", (declaration.model_dump_json(), item_id, run_id))
            return _view(connection, goal)

    def ready_continuations(self) -> Iterator[tuple[str, SessionGoal]]:
        after = ""
        while page := self.pending(after):
            yield from page
            after = page[-1][1].id

    def pending(self, after: str = "") -> tuple[tuple[str, SessionGoal], ...]:
        with self.lock:
            connection = self._connection()
            return tuple((row["id"], _goal(connection, row["goal_id"])) for row in connection.execute(
                "SELECT c.id,c.goal_id FROM goal_continuations c JOIN session_goals g ON g.id=c.goal_id "
                "WHERE c.status='pending' AND g.status='active' AND g.id>? AND NOT EXISTS "
                "(SELECT 1 FROM runs r WHERE r.session_id=g.session_id AND r.status IN " + ACTIVE_SQL + ") ORDER BY g.id LIMIT 100", (after,)))

    def accept(self, intent_id: str, write_run: Callable[[sqlite3.Connection], tuple[dict[str, object], dict[str, object]]]) -> tuple[dict[str, object], dict[str, object]]:
        with self.lock, self._connection() as connection:
            intent = connection.execute("SELECT * FROM goal_continuations WHERE id=?", (intent_id,)).fetchone()
            if intent is None:
                raise GoalError("goal_revision_conflict", "续跑请求已失效。")
            goal = _goal(connection, intent["goal_id"])
            if intent["status"] != "pending" or goal.status != "active" or (goal.objective_revision, goal.activation) != (intent["objective_revision"], intent["activation"]):
                raise GoalError("goal_revision_conflict", "目标已变化，本次续跑已取消。")
            if connection.execute("SELECT 1 FROM runs WHERE session_id=? AND status IN " + ACTIVE_SQL, (goal.session_id,)).fetchone():
                raise GoalError("goal_session_busy", "会话正在处理用户输入，目标稍后继续。")
            run, item = write_run(connection)
            _attach(connection, str(run["id"]), goal, intent["trigger"])
            connection.execute("UPDATE goal_continuations SET status='accepted',run_id=? WHERE id=?", (run["id"], intent_id))
            return run, item

    def fail_preparation(self, goal_id: str, sample: GoalSample, code: str) -> None:
        with self.lock, self._connection() as connection:
            try:
                goal = _goal(connection, goal_id)
            except GoalError:
                return
            if goal.status == "active" and goal.sample() == sample:
                detail = {"MODEL_NOT_AVAILABLE": "目标使用的模型已不可用，请检查模型配置。",
                    "WORKSPACE_IDENTITY_CHANGED": "工作区路径已变化，请重新打开会话确认。",
                    "RESOURCE_NOT_FOUND": "目标会话或执行位置已不可用。"}.get(code, "续跑准备失败，请检查模型配置和执行位置。")
                _save(connection, goal.model_copy(update={"status": "blocked", "status_reason": "preparation_failed", "status_detail": detail}))

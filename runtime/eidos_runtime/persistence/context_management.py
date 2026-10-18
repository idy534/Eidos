from __future__ import annotations

import json
import time
import uuid

from eidos_runtime.context.plan import ContextPlan
from eidos_runtime.db.database import Repository
from eidos_runtime.db.errors import ResourceNotFoundError
from eidos_runtime.db.events import append_event
from eidos_runtime.domain.context_management import ContextCompactionRecord, ContextDetails
from eidos_runtime.model.client import ModelProfileSnapshot, ModelUsage
from eidos_runtime.persistence.context_snapshots import ContextSnapshotRepository
from eidos_runtime.runtime.state_machine import EventType


class ContextManagementRepository(Repository):
    """Compaction commands and diagnostics use the existing durable event log."""

    def record(self, run_id: str, record: ContextCompactionRecord) -> None:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT session_id FROM runs WHERE id=?", (run_id,)).fetchone()
            if row is None:
                raise ResourceNotFoundError("run not found")
            append_event(connection, EventType.RUN_UPDATED, int(time.time() * 1000),
                         {"reason": "context_compaction", "compaction": record.to_wire_dict()},
                         session_id=row[0], run_id=run_id)

    def pending(self, run_id: str) -> ContextCompactionRecord | None:
        with self.lock:
            row = self._connection().execute("""
                SELECT e.payload_json FROM events e
                WHERE e.session_id=(SELECT session_id FROM runs WHERE id=?)
                  AND e.event_type='run.updated'
                  AND json_extract(e.payload_json,'$.compaction.stage')='requested'
                  AND NOT EXISTS (
                    SELECT 1 FROM events done WHERE done.session_id=e.session_id
                      AND done.event_type='run.updated' AND done.id>e.id
                      AND json_extract(done.payload_json,'$.compaction.requestId')=json_extract(e.payload_json,'$.compaction.requestId')
                      AND json_extract(done.payload_json,'$.compaction.stage') IN ('started','finished')
                  ) ORDER BY e.id LIMIT 1
            """, (run_id,)).fetchone()
        return ContextCompactionRecord.model_validate_json(json.dumps(json.loads(row[0])["compaction"])) if row else None

    def request(self, run_id: str) -> ContextCompactionRecord:
        # The same transaction deduplicates repeated clicks. No background
        # worker or second run is started by a manual request.
        with self.lock:
            pending = self.pending(run_id)
            if pending is not None:
                return pending
            record = ContextCompactionRecord(request_id=str(uuid.uuid4()), stage="requested", trigger="manual", status="queued")
            self.record(run_id, record)
            return record

    def guard(self, run_id: str) -> tuple[int, ...]:
        with self.lock:
            row = self._connection().execute("""
                SELECT workspace_version,reconciliation_epoch,compaction_count,
                    (SELECT COALESCE(MAX(creation_seq),0) FROM items WHERE session_id=runs.session_id AND kind='user_message'),
                    (SELECT COALESCE(MAX(rowid),0) FROM input_mailbox WHERE run_id=runs.id)
                FROM runs WHERE id=?
            """, (run_id,)).fetchone()
        if row is None:
            raise ResourceNotFoundError("run not found")
        return tuple(row)

    def details(self, run_id: str, profile: ModelProfileSnapshot) -> ContextDetails:
        latest = ContextSnapshotRepository(self.database).read_latest_budget_for_run(run_id)
        with self.lock:
            connection = self._connection()
            record = connection.execute("""
                SELECT e.payload_json,r.status FROM events e JOIN runs r ON r.id=e.run_id
                WHERE e.session_id=(SELECT session_id FROM runs WHERE id=?)
                  AND e.event_type='run.updated' AND json_type(e.payload_json,'$.compaction')='object'
                ORDER BY e.id DESC LIMIT 1
            """, (run_id,)).fetchone()
            rows = connection.execute("""
                SELECT a.usage_json,p.plan_json,c.id FROM model_attempts a
                JOIN context_snapshots c ON c.id=a.context_snapshot_id
                JOIN context_plans p ON p.id=c.plan_id
                WHERE c.run_id=? AND a.status='completed'
                  AND json_extract(a.usage_json,'$.input_tokens')>0
                ORDER BY a.creation_seq DESC LIMIT 20
            """, (run_id,)).fetchall()
        provider_tokens = None
        errors = []
        for row in rows:
            usage = ModelUsage.model_validate_json(row["usage_json"])
            plan = ContextPlan.model_validate_json(row["plan_json"])
            if latest is not None and row["id"] == latest[0]:
                provider_tokens = usage.input_tokens
            if usage.input_tokens:
                errors.append(round((plan.token_budget.projected_input_tokens - usage.input_tokens) / usage.input_tokens * 100, 1))
        budget = latest[1] if latest else None
        compaction = ContextCompactionRecord.model_validate_json(json.dumps(json.loads(record[0])["compaction"])) if record else None
        if compaction is not None and compaction.stage == "started":
            # A crash may occur after the authoritative summary transaction and
            # before the optional post-build metrics event. The atomic event
            # decides success; a missing timing update cannot turn it into failure.
            with self.lock:
                committed = self._connection().execute("""
                    SELECT payload_json FROM events WHERE event_type='context.compacted'
                      AND session_id=(SELECT session_id FROM runs WHERE id=?)
                      AND json_extract(payload_json,'$.compaction.requestId')=?
                    ORDER BY id DESC LIMIT 1
                """, (run_id, compaction.request_id)).fetchone()
            if committed is not None:
                compaction = ContextCompactionRecord.model_validate_json(json.dumps(json.loads(committed[0])["compaction"]))
        if compaction is not None and compaction.stage == "started" and record["status"] not in {"running", "waiting_approval", "waiting_input", "waiting_agents"}:
            compaction = compaction.model_copy(update={"stage": "finished", "status": "failed", "reason": "interrupted"})
        return ContextDetails(
            run_id=run_id, context_window_tokens=profile.context_window_tokens,
            measurement="provider" if provider_tokens is not None else (budget.measurement or "tokenizer_estimate") if budget else "unavailable",
            active_tokens=provider_tokens if provider_tokens is not None else budget.projected_input_tokens if budget else None,
            projected_tokens=budget.projected_input_tokens if budget else None,
            sections=budget.sections or () if budget else (),
            cleared_tool_results=self._cleared_count(run_id),
            calibration_samples=len(errors), last_estimation_error_percent=errors[0] if errors else None,
            compaction=compaction,
            manual_pending=self.pending(run_id) is not None,
        )

    def _cleared_count(self, run_id: str) -> int:
        with self.lock:
            row = self._connection().execute("""
                SELECT p.plan_json FROM context_snapshots c JOIN context_plans p ON p.id=c.plan_id
                WHERE c.run_id=? ORDER BY c.created_at DESC,c.id DESC LIMIT 1
            """, (run_id,)).fetchone()
        if row is None:
            return 0
        return sum(value.startswith("cleared-tool-result:") for value in ContextPlan.model_validate_json(row[0]).omissions)


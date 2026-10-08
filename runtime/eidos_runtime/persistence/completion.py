import json

from eidos_runtime.db.database import CommittedMutation, Database, Repository, now_ms
from eidos_runtime.db.errors import ResourceNotFoundError, StorageError
from eidos_runtime.db.events import append_event, event_from_row
from eidos_runtime.domain.completion import CompletionCheckRecord, CompletionFacts, CompletionOutput
from eidos_runtime.runtime.state_machine import EventType


class CompletionRepository(Repository):
    """Completion decisions are facts in the existing Event/Outbox transaction."""

    def __init__(self, database: Database) -> None:
        super().__init__(database)

    def facts(self, run_id: str) -> CompletionFacts:
        with self.lock:
            connection = self._connection()
            run = connection.execute("SELECT workspace_version,reconciliation_epoch FROM runs WHERE id=?", (run_id,)).fetchone()
            if run is None:
                raise ResourceNotFoundError("run not found")
            frontier = connection.execute(
                """SELECT COALESCE(MAX(id),0) FROM events WHERE run_id=?
                AND event_type IN ('tool_call.started','tool_call.completed','item.updated',
                    'input.queued','input.injected','reconciliation.required','reconciliation.cleared')""", (run_id,),
            ).fetchone()[0]
            user_sequence = connection.execute("SELECT COALESCE(MAX(creation_seq),0) FROM items WHERE run_id=? AND kind='user_message'", (run_id,)).fetchone()[0]
            has_tools = connection.execute("SELECT 1 FROM tool_calls t JOIN items i ON i.id=t.item_id WHERE i.run_id=? LIMIT 1", (run_id,)).fetchone() is not None
            pending_input = connection.execute("SELECT 1 FROM input_mailbox WHERE run_id=? AND status='pending' LIMIT 1", (run_id,)).fetchone() is not None
            rows = connection.execute(
                """SELECT t.result_json FROM tool_calls t JOIN items i ON i.id=t.item_id
                WHERE i.run_id=? AND t.tool_name='declare_outputs' AND t.status='completed'
                AND json_extract(t.result_json,'$.outcome')='success'
                ORDER BY t.creation_seq DESC LIMIT 32""", (run_id,),
            ).fetchall()
        outputs: dict[str, CompletionOutput] = {}
        for row in rows:
            result = json.loads(row[0])
            for output in result.get("data", {}).get("outputs", ()):
                reference = CompletionOutput.model_validate_json(json.dumps({"path": output["path"], "version": output["version"]}))
                outputs.setdefault(reference.path, reference)
        return CompletionFacts(
            workspace_version=run["workspace_version"], reconciliation_epoch=run["reconciliation_epoch"],
            last_tool_event_id=frontier, last_user_sequence=user_sequence,
            has_tools=has_tools, pending_input=pending_input, outputs=tuple(outputs.values()),
        )

    def latest(self, run_id: str, fingerprint: str) -> tuple[CompletionCheckRecord, dict[str, object]] | None:
        with self.lock:
            row = self._connection().execute(
                """SELECT * FROM events WHERE run_id=? AND event_type='run.updated'
                AND json_extract(payload_json,'$.reason')='completion_check'
                AND json_extract(payload_json,'$.completionCheck.fingerprint')=?
                ORDER BY id DESC LIMIT 1""", (run_id, fingerprint),
            ).fetchone()
        if row is None:
            return None
        event = event_from_row(row)
        if event is None:
            raise StorageError("completion_record_invalid")
        record = CompletionCheckRecord.model_validate_json(json.dumps(event["payload"]["completionCheck"]))
        return record, event

    def record(self, run_id: str, record: CompletionCheckRecord) -> CommittedMutation[CompletionCheckRecord]:
        with self.lock, self._connection() as connection:
            run = connection.execute("SELECT session_id FROM runs WHERE id=?", (run_id,)).fetchone()
            if run is None:
                raise ResourceNotFoundError("run not found")
            event = append_event(connection, EventType.RUN_UPDATED, now_ms(), {
                "reason": "completion_check", "completionCheck": record.model_dump(mode="python", by_alias=True),
            }, session_id=run["session_id"], run_id=run_id)
        return CommittedMutation(record, (event,))

    def feedback(self, run_id: str) -> CompletionCheckRecord | None:
        with self.lock:
            row = self._connection().execute(
                """SELECT e.* FROM events e WHERE e.run_id=? AND e.event_type='run.updated'
                AND json_extract(e.payload_json,'$.reason')='completion_check'
                ORDER BY e.id DESC LIMIT 1""", (run_id,),
            ).fetchone()
            if row is None:
                return None
            newer_fact = self._connection().execute(
                """SELECT 1 FROM events WHERE run_id=? AND id>? AND event_type IN (
                'tool_call.started','tool_call.completed','item.updated','input.queued','input.injected',
                'reconciliation.required','reconciliation.cleared') LIMIT 1""", (run_id, row["id"]),
            ).fetchone()
        if newer_fact:
            return None
        event = event_from_row(row)
        if event is None:
            raise StorageError("completion_record_invalid")
        record = CompletionCheckRecord.model_validate_json(json.dumps(event["payload"]["completionCheck"]))
        return record if record.status == "continue" else None

from __future__ import annotations

import json
import sqlite3
import time
import uuid

from pydantic import ValidationError

from eidos_runtime.context.facts import CompactSummary, ContextFacts, ContextItemFact
from eidos_runtime.context.verified_compaction import (
    ContextCompactionVerifier,
    VerifiedCompactSummary,
)
from eidos_runtime.db.database import Repository
from eidos_runtime.db.errors import InvalidRunStateError
from eidos_runtime.db.events import append_event
from eidos_runtime.db.mappers import _json_tuple
from eidos_runtime.db.repositories.context import ContextRepository
from eidos_runtime.persistence.errors import PersistenceCorruptionError
from eidos_runtime.repo_intelligence.retrieval import RetrievalSnapshot
from eidos_runtime.runtime.state_machine import EventType


class VerifiedCompactionRepository(Repository):
    def verify_and_persist(
        self,
        *,
        run_id: str,
        summary: CompactSummary,
        input_range: tuple[int, int],
        source_event_ids: tuple[int, ...] = (),
        source_tool_call_ids: tuple[str, ...] = (),
        source_evidence_ids: tuple[str, ...] = (),
        pending_approval_facts: tuple[str, ...] = (),
        reconciliation_facts: tuple[str, ...] = (),
        phase: str = "mid_turn",
    ) -> VerifiedCompactSummary:
        if phase not in {"pre_turn", "mid_turn"}:
            raise ValueError("invalid compaction phase")
        facts = self.load_facts(
            run_id, required_item_ids=summary.source_item_ids
        )
        verified = ContextCompactionVerifier().verify(
            summary,
            facts,
            input_range=input_range,
            source_event_ids=source_event_ids,
            source_tool_call_ids=source_tool_call_ids,
            source_evidence_ids=source_evidence_ids,
            pending_approval_facts=pending_approval_facts,
            reconciliation_facts=reconciliation_facts,
        )
        identifier = f"compact_{verified.summary_hash}"
        with self.lock, self._connection() as connection:
            existing = connection.execute(
                "SELECT 1 FROM verified_compact_summaries WHERE id = ?",
                (identifier,),
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO verified_compact_summaries (
                        id, run_id, summary_hash, verified_json, input_start,
                        input_end, compaction_version, verification_result,
                        verified_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'verified', ?)
                    """,
                    (
                        identifier, run_id, verified.summary_hash,
                        verified.model_dump_json(), input_range[0], input_range[1],
                        verified.compaction_version, verified.verified_at_ms,
                    ),
                )
                run = connection.execute(
                    "SELECT session_id FROM runs WHERE id = ? AND status = 'running'",
                    (run_id,),
                ).fetchone()
                if run is None:
                    raise InvalidRunStateError("run is not active")
                summary_id = str(uuid.uuid4())
                now = int(time.time() * 1000)
                connection.execute(
                    """
                    INSERT INTO compact_summaries (
                        id, session_id, run_id, task_goal, constraints_json,
                        completed_actions_json, workspace_changes_json,
                        important_facts_json, unresolved_problems_json,
                        next_actions_json, source_item_ids_json,
                        summary_metadata_json, phase, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        summary_id, run["session_id"], run_id,
                        verified.summary.task_goal,
                        _json_tuple(verified.summary.constraints),
                        _json_tuple(verified.summary.completed_actions),
                        _json_tuple(verified.summary.workspace_changes),
                        _json_tuple(verified.summary.important_facts),
                        _json_tuple(verified.summary.unresolved_problems),
                        _json_tuple(verified.summary.next_actions),
                        _json_tuple(verified.summary.source_item_ids),
                        json.dumps(
                            {
                                "important_decisions": verified.summary.important_decisions,
                                "failed_attempts": verified.summary.failed_attempts,
                                "pending_approvals": verified.summary.pending_approvals,
                                "uncertain_side_effects": verified.summary.uncertain_side_effects,
                                "verified_summary_id": identifier,
                            },
                            ensure_ascii=False,
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                        phase,
                        now,
                    ),
                )
                updated = connection.execute(
                    """
                    UPDATE runs
                    SET compaction_count = compaction_count + 1, updated_at = ?
                    WHERE id = ? AND status = 'running'
                    """,
                    (now, run_id),
                )
                if updated.rowcount != 1:
                    raise InvalidRunStateError("compaction could not commit")
                append_event(
                    connection,
                    EventType.CONTEXT_COMPACTED,
                    now,
                    {
                        "summaryId": summary_id,
                        "sourceItemCount": len(verified.summary.source_item_ids),
                        "phase": phase,
                    },
                    session_id=run["session_id"],
                    run_id=run_id,
                )
        return verified

    def latest(self, run_id: str) -> VerifiedCompactSummary | None:
        with self.lock:
            row = self._connection().execute(
                """
                SELECT verified_json FROM verified_compact_summaries
                WHERE run_id = ? ORDER BY verified_at DESC, rowid DESC LIMIT 1
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        try:
            return VerifiedCompactSummary.model_validate_json(row["verified_json"])
        except (TypeError, ValidationError, ValueError):
            raise PersistenceCorruptionError(
                "persistence_record_invalid", record="verified_compact_summary"
            ) from None

    def load_facts(
        self,
        run_id: str,
        *,
        required_item_ids: tuple[str, ...] = (),
    ) -> ContextFacts:
        candidate = ContextRepository(self.database).compaction_candidate_facts(run_id)
        with self.lock:
            connection = self._connection()
            run = connection.execute(
                "SELECT * FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
            if run is None:
                raise ValueError("run not found")
            source_ids = {
                item.item_id for item in candidate.items
            }
            source_ids.update(required_item_ids)
            if candidate.compact_summary is not None:
                source_ids.update(candidate.compact_summary.source_item_ids)
            rows = connection.execute(
                """
                SELECT items.*, tool_calls.provider_call_id, tool_calls.tool_name,
                       tool_calls.arguments_json, tool_calls.result_json,
                       tool_calls.model_result_json
                FROM items LEFT JOIN tool_calls ON tool_calls.item_id = items.id
                WHERE items.session_id = ? ORDER BY items.creation_seq
                """,
                (candidate.session_id,),
            ).fetchall()
            pending = tuple(row[0] for row in connection.execute(
                "SELECT id FROM approvals WHERE run_id = ? AND status = 'pending'",
                (run_id,),
            ).fetchall())
            event_ids = tuple(int(row[0]) for row in connection.execute(
                "SELECT id FROM events WHERE run_id = ?", (run_id,)
            ).fetchall())
            tool_ids = tuple(str(row[0]) for row in connection.execute(
                """
                SELECT tool_calls.id FROM tool_calls JOIN items
                  ON items.id = tool_calls.item_id WHERE items.run_id = ?
                """,
                (run_id,),
            ).fetchall())
            retrieval_rows = connection.execute(
                "SELECT snapshot_json FROM repository_retrieval_snapshots WHERE run_id = ?",
                (run_id,),
            ).fetchall()
        evidence_ids: list[str] = []
        for row in retrieval_rows:
            try:
                retrieval = RetrievalSnapshot.model_validate_json(row[0])
            except (ValidationError, ValueError):
                raise PersistenceCorruptionError(
                    "persistence_record_invalid", record="retrieval_snapshot"
                ) from None
            evidence_ids.extend(
                evidence.id
                for result in retrieval.results for evidence in result.evidence
            )
        selected_rows = [row for row in rows if str(row["id"]) in source_ids]
        changes = _workspace_changes(selected_rows)
        return candidate.model_copy(update={
            "items": tuple(ContextItemFact(
                item_id=str(row["id"]), run_id=str(row["run_id"]), kind=str(row["kind"]),
                status=str(row["status"]), content=row["content"],
                provider_call_id=row["provider_call_id"],
                tool_name=row["tool_name"], arguments_json=row["arguments_json"],
                result_json=row["result_json"],
                model_result_json=row["model_result_json"],
                ordinal=int(row["ordinal"]),
            ) for row in selected_rows),
            "pending_approval_ids": pending,
            "committed_workspace_changes": changes,
            "available_event_ids": event_ids,
            "available_tool_call_ids": tool_ids,
            "available_evidence_ids": tuple(dict.fromkeys(evidence_ids)),
        })


def _workspace_changes(rows: list[sqlite3.Row]) -> tuple[str, ...]:
    changes: list[str] = []
    for row in rows:
        raw = row["result_json"]
        if raw is None:
            continue
        try:
            result = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            continue
        data = result.get("data") if isinstance(result, dict) else None
        if not isinstance(data, dict):
            continue
        if data.get("workspaceChanged") is True and isinstance(data.get("path"), str):
            changes.append(str(data["path"]))
        for key in ("created", "modified", "deleted"):
            values = data.get(key)
            if isinstance(values, list):
                changes.extend(str(value) for value in values if isinstance(value, str))
    return tuple(dict.fromkeys(changes))


__all__ = ["VerifiedCompactionRepository"]

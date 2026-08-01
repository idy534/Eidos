from __future__ import annotations

import json
import sqlite3
import time

from pydantic import ValidationError

from eidos_runtime.context.facts import CompactSummary, ContextFacts, ContextItemFact
from eidos_runtime.context.verified_compaction import (
    ContextCompactionVerifier,
    VerifiedCompactSummary,
)
from eidos_runtime.db.database import Repository
from eidos_runtime.persistence.errors import PersistenceCorruptionError
from eidos_runtime.repo_intelligence.retrieval import RetrievalSnapshot


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
    ) -> VerifiedCompactSummary:
        facts = self.load_facts(run_id)
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
                    "SELECT session_id FROM runs WHERE id = ?", (run_id,)
                ).fetchone()
                if run is None:
                    raise ValueError("run not found")
                event = connection.execute(
                    """
                    INSERT INTO events (
                        event_contract_version, event_type, occurred_at,
                        session_id, run_id, payload_json
                    ) VALUES (1, 'CONTEXT_COMPACTED', ?, ?, ?, ?)
                    """,
                    (
                        int(time.time() * 1000), run["session_id"], run_id,
                        json.dumps({"verifiedSummaryId": identifier}, separators=(",", ":")),
                    ),
                )
                connection.execute(
                    "INSERT INTO event_outbox (event_id, status) VALUES (?, 'pending')",
                    (event.lastrowid,),
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

    def load_facts(self, run_id: str) -> ContextFacts:
        with self.lock:
            connection = self._connection()
            run = connection.execute(
                "SELECT * FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
            if run is None:
                raise ValueError("run not found")
            rows = connection.execute(
                """
                SELECT items.*, tool_calls.provider_call_id, tool_calls.tool_name,
                       tool_calls.arguments_json, tool_calls.result_json,
                       tool_calls.model_result_json
                FROM items LEFT JOIN tool_calls ON tool_calls.item_id = items.id
                WHERE items.run_id = ? ORDER BY items.ordinal
                """,
                (run_id,),
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
        changes = _workspace_changes(rows)
        return ContextFacts(
            run_id=run_id,
            session_id=str(run["session_id"]),
            items=tuple(ContextItemFact(
                item_id=str(row["id"]), run_id=run_id, kind=str(row["kind"]),
                status=str(row["status"]), content=row["content"],
                provider_call_id=row["provider_call_id"],
                tool_name=row["tool_name"], arguments_json=row["arguments_json"],
                result_json=row["result_json"],
                model_result_json=row["model_result_json"], ordinal=int(row["ordinal"]),
            ) for row in rows),
            workspace_version=int(run["workspace_version"]),
            reconciliation_epoch=int(run["reconciliation_epoch"]),
            reconciliation_required=bool(run["reconciliation_required"]),
            side_effects_may_exist=bool(run["side_effects_may_exist"]),
            pending_approval_ids=pending,
            committed_workspace_changes=changes,
            available_event_ids=event_ids,
            available_tool_call_ids=tool_ids,
            available_evidence_ids=tuple(dict.fromkeys(evidence_ids)),
        )


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
        for key in ("created", "modified", "deleted"):
            values = data.get(key)
            if isinstance(values, list):
                changes.extend(str(value) for value in values if isinstance(value, str))
    return tuple(dict.fromkeys(changes))


__all__ = ["VerifiedCompactionRepository"]

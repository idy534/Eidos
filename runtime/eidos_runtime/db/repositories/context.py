from __future__ import annotations

from eidos_runtime.domain.input_reference import InputReference

import json
import sqlite3

from eidos_runtime.context.facts import CompactSummary, ContextFacts, ContextItemFact, ReconciliationOrigin
from eidos_runtime.db.database import Repository
from eidos_runtime.model.context_projection import text_preview, model_result_json as project_result_json
from eidos_runtime.db.errors import (
    ResourceNotFoundError,
    StorageError,
)
from eidos_runtime.db.mappers import _compact_summary_from_row
from eidos_runtime.runtime.contracts import ProgressSignature
from eidos_runtime.model.response_phase import AssistantMessagePhase
from eidos_runtime.model.replay import ModelResponseReplay

# Summary candidates use small batches. Online history uses the separate hard
# resource ceiling; the model's token budget decides when normal compaction is
# due. A summary batch size must not become a small model-independent window.
CONTEXT_PROJECTION_MAX_BYTES = 768 * 1024
CONTEXT_PROJECTION_MAX_ITEMS = 200
# Compaction candidates are never sent to the model. Keep their payloads
# bounded independently of the model projection so an oversized historical
# item can still be represented by its durable source id and summarized.
COMPACTION_FACT_VALUE_MAX_CHARS = 8 * 1024
CONTEXT_PROJECTION_HARD_MAX_BYTES = 8 * 1024 * 1024
CONTEXT_PROJECTION_HARD_MAX_ITEMS = 2_000
RECENT_CONTEXT_STEPS = 3
CONTEXT_PROJECTION_FACT_OVERHEAD_BYTES = 448


class ContextRepository(Repository):
    def context_projection_facts(self, run_id: str) -> ContextFacts:
        from eidos_runtime.persistence.session_history import SessionHistoryRepository
        return SessionHistoryRepository(self.database).with_history(
            self._bounded_context_facts(run_id, newest=True), newest=True,
        )

    def compaction_candidate_facts(self, run_id: str) -> ContextFacts:
        from eidos_runtime.persistence.session_history import SessionHistoryRepository
        return SessionHistoryRepository(self.database).with_history(
            self._bounded_context_facts(run_id, newest=False), newest=False,
        )

    def _bounded_context_facts(
        self, run_id: str, *, newest: bool
    ) -> ContextFacts:
        item_limit = CONTEXT_PROJECTION_HARD_MAX_ITEMS if newest else CONTEXT_PROJECTION_MAX_ITEMS
        byte_limit = CONTEXT_PROJECTION_HARD_MAX_BYTES if newest else CONTEXT_PROJECTION_MAX_BYTES
        with self.lock:
            connection = self._connection()
            run = connection.execute(
                "SELECT * FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
            if run is None:
                raise ResourceNotFoundError("run not found")
            summary_row = connection.execute(
                """
                SELECT * FROM compact_summaries
                WHERE run_id = ?
                  AND EXISTS (
                    SELECT 1 FROM verified_compact_summaries
                    WHERE verified_compact_summaries.id = json_extract(
                        compact_summaries.summary_metadata_json,
                        '$.verified_summary_id'
                    )
                  )
                ORDER BY creation_seq DESC LIMIT 1
                """,
                (run_id,),
            ).fetchone()
            source_ids = set(
                _compact_summary_from_row(summary_row).source_item_ids
                if summary_row is not None else ()
            )
            goal_row = connection.execute(
                """
                SELECT * FROM items
                WHERE run_id = ? AND kind = 'user_message'
                ORDER BY creation_seq ASC LIMIT 1
                """,
                (run_id,),
            ).fetchone()
            latest_user_row = connection.execute(
                """
                SELECT items.* FROM items JOIN runs ON runs.id = items.run_id
                WHERE items.session_id = ? AND kind = 'user_message'
                  AND items.status = 'completed'
                  AND run_id IN (
                    SELECT id FROM runs WHERE creation_seq <= ?
                      AND (id = ? OR started_at IS NOT NULL)
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM run_revisions
                    WHERE run_revisions.source_run_id = items.run_id
                  )
                ORDER BY runs.creation_seq DESC, items.ordinal DESC LIMIT 1
                """,
                (run["session_id"], run["creation_seq"], run_id),
            ).fetchone()
            recent_steps = connection.execute(
                """
                SELECT DISTINCT model_step_index FROM items
                WHERE run_id = ? AND model_step_index IS NOT NULL
                ORDER BY model_step_index DESC LIMIT ?
                """,
                (run_id, RECENT_CONTEXT_STEPS),
            ).fetchall()
            recent_rows: list[sqlite3.Row] = []
            if recent_steps:
                placeholders = ",".join("?" for _ in recent_steps)
                recent_rows = connection.execute(
                    f"""
                    SELECT * FROM items
                    WHERE run_id = ? AND model_step_index IN ({placeholders})
                      AND status IN ('completed', 'failed', 'declined')
                      AND NOT (kind = 'assistant_message' AND incomplete = 1)
                    """,
                    (run_id, *(row[0] for row in recent_steps)),
                ).fetchall()
            reconciliation_rows: list[sqlite3.Row] = []
            if bool(run["reconciliation_required"]):
                reconciliation_rows = connection.execute(
                    """
                    SELECT items.* FROM items
                    JOIN tool_calls ON tool_calls.item_id = items.id
                    WHERE items.run_id = ?
                      AND json_extract(tool_calls.result_json, '$.reconciliationRequired') = 1
                    """,
                    (run_id,),
                ).fetchall()
            protected_rows = {
                str(row["id"]): row
                for row in (
                    goal_row,
                    latest_user_row,
                    *recent_rows,
                    *reconciliation_rows,
                )
                if row is not None
            }
            ordered_protected = sorted(protected_rows.values(), key=lambda row: int(row["creation_seq"]), reverse=True)
            protected_ids = {str(row["id"]) for row in ordered_protected[:max(0, item_limit - 1)]}
            if goal_row is not None:
                protected_ids.add(str(goal_row["id"]))
            excluded = source_ids | protected_ids
            excluded_sql = ""
            excluded_values: tuple[object, ...] = ()
            if excluded:
                placeholders = ",".join("?" for _ in excluded)
                excluded_sql = f" AND items.id NOT IN ({placeholders})"
                excluded_values = tuple(excluded)
            replay_join = """
                LEFT JOIN model_attempts replay_attempt ON replay_attempt.id = (
                    SELECT a.id FROM model_attempts a JOIN steps s ON s.id = a.step_id
                    WHERE s.run_id = items.run_id AND s.ordinal = items.model_step_index
                      AND a.status = 'completed' ORDER BY a.ordinal LIMIT 1
                )
            """ if newest else ""
            base = f"""
                FROM items JOIN runs ON runs.id = items.run_id
                LEFT JOIN tool_calls ON tool_calls.item_id = items.id
                {replay_join}
                WHERE items.session_id = ?
                  AND items.run_id IN (
                    SELECT id FROM runs WHERE creation_seq <= ?
                      AND (id = ? OR started_at IS NOT NULL)
                  )
                  AND items.status IN ('completed', 'failed', 'declined')
                  AND NOT EXISTS (
                    SELECT 1 FROM input_mailbox pending
                    WHERE pending.id = items.id AND pending.status = 'pending'
                  )
                  AND NOT (items.kind = 'assistant_message' AND items.incomplete = 1)
                  AND NOT EXISTS (
                    SELECT 1 FROM run_revisions
                    WHERE run_revisions.source_run_id = items.run_id
                  )
                  {excluded_sql}
            """
            size_expression = f"""
                length(CAST(COALESCE(items.content, '') AS BLOB))
                + length(CAST(COALESCE(tool_calls.arguments_json, '') AS BLOB))
                + length(CAST(COALESCE(
                    tool_calls.model_result_json, tool_calls.result_json, ''
                ) AS BLOB)) + {CONTEXT_PROJECTION_FACT_OVERHEAD_BYTES}
            """
            if newest:
                # Apportion a shared replay record across its Items. Counting
                # the entire reasoning once per ToolCall would reject a valid
                # multi-tool response at the internal hard limit.
                size_expression += """ + COALESCE(
                    json_extract(replay_attempt.response_replay_json, '$."$eidosBlob".uncompressedBytes'),
                    length(replay_attempt.response_replay_json), 0) / MAX(1, (
                        SELECT COUNT(*) FROM items sibling
                        WHERE sibling.run_id = items.run_id
                          AND sibling.model_step_index = items.model_step_index
                          AND sibling.status IN ('completed', 'failed', 'declined')
                          AND NOT (sibling.kind = 'assistant_message' AND sibling.incomplete = 1)
                    ))
                """
            aggregate = connection.execute(
                f"SELECT COUNT(*), COALESCE(SUM({size_expression}), 0) {base}",
                (run["session_id"], run["creation_seq"], run_id, *excluded_values),
            ).fetchone()
            metadata = connection.execute(
                f"""
                SELECT items.id, {size_expression} AS fact_bytes
                {base}
                ORDER BY runs.creation_seq {'DESC' if newest else 'ASC'},
                         items.ordinal {'DESC' if newest else 'ASC'} LIMIT ?
                """,
                (
                    run["session_id"],
                    run["creation_seq"], run_id,
                    *excluded_values,
                    item_limit + 1,
                ),
            ).fetchall()
            selected_ids = list(protected_ids) if newest else []
            protected_bytes = 0
            if newest and protected_ids:
                placeholders = ",".join("?" for _ in protected_ids)
                protected_bytes = int(connection.execute(
                    f"""
                    SELECT COALESCE(SUM({size_expression}), 0)
                    FROM items LEFT JOIN tool_calls ON tool_calls.item_id = items.id
                    {replay_join}
                    WHERE items.id IN ({placeholders})
                    """,
                    tuple(protected_ids),
                ).fetchone()[0])
            if (
                len(protected_ids) > CONTEXT_PROJECTION_HARD_MAX_ITEMS
                or protected_bytes > CONTEXT_PROJECTION_HARD_MAX_BYTES
            ):
                protected_bytes = min(protected_bytes, byte_limit)
            selection_item_limit = max(
                item_limit, len(protected_ids)
            )
            selection_byte_limit = max(
                byte_limit, min(protected_bytes, byte_limit)
            )
            selected_bytes = min(protected_bytes, byte_limit)
            if (
                len(selected_ids) > selection_item_limit
                or selected_bytes > selection_byte_limit
            ):
                protected_bytes = min(protected_bytes, byte_limit)
            base_selected = 0
            for row in metadata:
                fact_bytes = int(row["fact_bytes"])
                if (
                    len(selected_ids) >= selection_item_limit
                    or (
                        newest
                        and selected_bytes + fact_bytes > selection_byte_limit
                    )
                ):
                    break
                selected_ids.append(str(row["id"]))
                selected_bytes += fact_bytes
                base_selected += 1
            item_rows: list[sqlite3.Row] = []
            if selected_ids:
                placeholders = ",".join("?" for _ in selected_ids)
                if newest:
                    item_rows = connection.execute(
                        f"""
                        SELECT items.*, runs.creation_seq AS run_creation_seq
                        FROM items JOIN runs ON runs.id = items.run_id
                        WHERE items.id IN ({placeholders})
                        """,
                        selected_ids,
                    ).fetchall()
                else:
                    item_rows = connection.execute(
                        f"""
                        SELECT items.creation_seq, items.id, items.session_id, run_id, ordinal,
                               runs.creation_seq AS run_creation_seq,
                               model_step_index, kind, items.status, input_references_json,
                               substr(content, 1, ?) AS content,
                               incomplete, items.created_at, items.completed_at
                        FROM items JOIN runs ON runs.id = items.run_id
                        WHERE items.id IN ({placeholders})
                        """,
                        (COMPACTION_FACT_VALUE_MAX_CHARS, *selected_ids),
                    ).fetchall()
            item_rows.sort(key=lambda row: (int(row["run_creation_seq"]), int(row["ordinal"])))
            native_phases = {}
            response_rows = {}
            if selected_ids:
                placeholders = ",".join("?" for _ in selected_ids)
                phase_rows = connection.execute(f"""
                    SELECT i.id, (SELECT CASE WHEN json_extract(a.protocol_diagnostics_json,'$.phaseSource')='provider'
                        THEN a.phase END FROM model_attempts a JOIN steps s ON s.id=a.step_id
                        WHERE s.run_id=i.run_id AND s.ordinal=i.model_step_index AND a.status='completed'
                        ORDER BY a.ordinal LIMIT 1) AS phase
                    FROM items i WHERE i.id IN ({placeholders}) AND i.kind='assistant_message'
                """, selected_ids).fetchall()
                native_phases = {row["id"]: AssistantMessagePhase(row["phase"]) for row in phase_rows if row["phase"] is not None}
                if newest:
                    replay_rows = connection.execute(f"""
                        SELECT items.id AS item_id, replay_attempt.id AS response_id,
                               replay_attempt.response_replay_json
                        FROM items {replay_join} WHERE items.id IN ({placeholders})
                    """, selected_ids).fetchall()
                    response_rows = {row["item_id"]: row for row in replay_rows}
            tool_rows: list[sqlite3.Row] = []
            if selected_ids:
                placeholders = ",".join("?" for _ in selected_ids)
                if newest:
                    tool_rows = connection.execute(
                        f"SELECT * FROM tool_calls WHERE item_id IN ({placeholders})",
                        selected_ids,
                    ).fetchall()
                else:
                    tool_rows = connection.execute(
                        f"""
                        SELECT item_id, provider_call_id, tool_name,
                               payload_kind,
                               substr(arguments_json, 1, ?) AS arguments_json,
                               status,
                               substr(result_json, 1, ?) AS result_json,
                               substr(model_result_json, 1, ?) AS model_result_json
                        FROM tool_calls WHERE item_id IN ({placeholders})
                        """,
                        (
                            COMPACTION_FACT_VALUE_MAX_CHARS,
                            COMPACTION_FACT_VALUE_MAX_CHARS,
                            COMPACTION_FACT_VALUE_MAX_CHARS,
                            *selected_ids,
                        ),
                    ).fetchall()
            candidate_overflow = (
                int(aggregate[0]) > base_selected
                or int(aggregate[1])
                > max(0, byte_limit - protected_bytes)
            )
            latest_signature = connection.execute(
                """
                SELECT progress_signature_json FROM steps
                WHERE run_id = ? AND progress_signature_json IS NOT NULL
                ORDER BY creation_seq DESC LIMIT 1
                """,
                (run_id,),
            ).fetchone()
            active_errors = (
                ProgressSignature.model_validate_json(latest_signature[0]).error_fingerprints
                if latest_signature is not None else ()
            )
            pending_approval_rows = connection.execute(
                """
                SELECT id FROM approvals
                WHERE run_id = ? AND status = 'pending'
                ORDER BY creation_seq
                """,
                (run_id,),
            ).fetchall()
            pending_approval_ids = tuple(str(row["id"]) for row in pending_approval_rows)
            origin_rows = connection.execute(
                """SELECT d.id, t.provider_call_id, t.tool_name,
                          substr(json_extract(t.result_json, '$.code'), 1, 128) AS reason,
                          substr(json_extract(t.result_json, '$.data.outputCaptureError'), 1, 128) AS capture_error
                   FROM durable_intents d JOIN tool_calls t ON t.id = d.tool_call_id
                   WHERE d.run_id = ? AND d.status IN ('running', 'uncertain', 'interrupted')
                   ORDER BY d.created_at, d.id LIMIT 16""",
                (run_id,),
            ).fetchall() if run["reconciliation_required"] else ()
        tools_by_item = {row["item_id"]: row for row in tool_rows}
        # Queued input is persisted before the preceding Run finishes. Its
        # insertion sequence must not split that Run's assistant/tool history.
        item_order = {str(row["id"]): index for index, row in enumerate(item_rows)}
        if newest:
            # Metadata only bounds loading. Reserve the actual serialized bytes
            # of protected facts before older history can consume their space.
            # Prefer newer unprotected history, then restore conversation order.
            item_rows.sort(key=lambda row: (
                str(row["id"]) not in protected_ids,
                item_order[str(row["id"])] if str(row["id"]) in protected_ids else -item_order[str(row["id"])],
            ))
        items: list[ContextItemFact] = []
        serialized_bytes = 2
        projected_candidate_bytes = 0
        projected_candidate_count = 0
        replay_cache: dict[str, ModelResponseReplay] = {}
        replay_attached: set[str] = set()
        for row in item_rows:
            tool = tools_by_item.get(row["id"])
            model_result_json = (
                str(tool["model_result_json"])
                if tool and tool["model_result_json"] is not None
                else None
            )
            response_row = response_rows.get(row["id"])
            response_id = response_row["response_id"] if response_row is not None else None
            replay = None
            if response_id is not None and response_id not in replay_attached and response_row["response_replay_json"] is not None:
                if response_id not in replay_cache:
                    try:
                        replay_cache[response_id] = ModelResponseReplay.model_validate_json(
                            self.database.json_blobs.read_json(response_row["response_replay_json"], expected_kind="model-response-replay")
                        )
                    except ValueError:
                        raise StorageError("model_response_replay_invalid") from None
                replay = replay_cache[response_id]
            fact = ContextItemFact(
                item_id=str(row["id"]),
                run_id=str(row["run_id"]),
                kind=str(row["kind"]),
                status=str(row["status"]),
                content=row["content"],
                phase=native_phases.get(row["id"]),
                model_step_index=row["model_step_index"],
                model_response_id=response_id,
                response_replay=replay,
                input_references=tuple(InputReference.model_validate(value) for value in json.loads(row["input_references_json"])),
                provider_call_id=str(tool["provider_call_id"]) if tool else None,
                tool_name=str(tool["tool_name"]) if tool else None,
                payload_kind=str(tool["payload_kind"]) if tool else None,
                arguments_json=str(tool["arguments_json"]) if tool else None,
                result_json=(
                    str(tool["result_json"])
                    if tool
                    and tool["result_json"] is not None
                    and model_result_json is None
                    else None
                ),
                model_result_json=model_result_json,
            )
            text_limit = max(256, min(2 * 1024 * 1024, byte_limit // 4))
            updates = {}
            if fact.content:
                updates["content"] = text_preview(fact.content, text_limit,
                    continuation=f"Original item {fact.item_id}: read_tool_output(callId='{fact.item_id}', stream='context') supports byte pages.")
            if fact.arguments_json and len(fact.arguments_json.encode("utf-8")) > text_limit:
                updates["arguments_json"] = json.dumps({"historicalArgumentsOmitted": True,
                    "notice": "Historical arguments are omitted; do not replay this operation to recover its result."})
            for field in ("result_json", "model_result_json"):
                raw = getattr(fact, field)
                if raw:
                    raw = project_result_json(raw)
                    if len(raw.encode("utf-8")) > text_limit:
                        parsed = json.loads(raw)
                        raw = json.dumps({"outcome": parsed.get("outcome"), "code": parsed.get("code"),
                            "summary": parsed.get("summary"), "contextCleared": True,
                            "sideEffectsMayExist": parsed.get("sideEffectsMayExist", False),
                            "reconciliationRequired": parsed.get("reconciliationRequired", False),
                            "data": {key: value for key, value in (parsed.get("data") or {}).items()
                                if key in {"sessionId", "executionStatus", "exitCode", "termination", "path", "nextOffset", "nextLine", "outputCallId", "outputComplete", "workspaceChangeState"}},
                            "readBack": {"tool": "read_tool_output", "callId": fact.provider_call_id,
                                "itemId": fact.item_id, "stream": "result"}}, ensure_ascii=False)
                    updates[field] = raw
            fact = fact.model_copy(update=updates)
            size = len(fact.model_dump_json(exclude_none=True).encode("utf-8")) + 1
            if serialized_bytes + size > byte_limit:
                candidate_overflow = True
                continue
            items.append(fact)
            if replay is not None:
                replay_attached.add(response_id)
            serialized_bytes += size
            if str(row["id"]) not in protected_ids:
                projected_candidate_bytes += size
                projected_candidate_count += 1
        items.sort(key=lambda fact: item_order[fact.item_id])
        candidate_count = int(aggregate[0])
        candidate_bytes = int(aggregate[1])
        omitted_count = max(0, candidate_count - projected_candidate_count)
        omitted_bytes = max(0, candidate_bytes - projected_candidate_bytes)
        return ContextFacts(
            run_id=run_id,
            session_id=str(run["session_id"]),
            items=tuple(items),
            compact_summary=_compact_summary_from_row(summary_row),
            compaction_count=int(run["compaction_count"]),
            workspace_version=int(run["workspace_version"]),
            reconciliation_epoch=int(run["reconciliation_epoch"]),
            last_diff_hash=run["last_diff_hash"],
            candidate_overflow=candidate_overflow,
            projection_candidate_count=candidate_count,
            projection_candidate_bytes=candidate_bytes,
            projection_omitted_count=omitted_count,
            projection_omitted_bytes=omitted_bytes,
            current_user_goal_id=(
                str(goal_row["id"]) if goal_row is not None else None
            ),
            reconciliation_required=bool(run["reconciliation_required"]),
            reconciliation_origins=tuple(
                ReconciliationOrigin(
                    intent_id=str(row["id"]), call_id=str(row["provider_call_id"]),
                    tool_name=str(row["tool_name"]), reason=str(row["reason"] or "outcome_unknown"),
                    output_capture_error=row["capture_error"],
                ) for row in origin_rows
            ),
            active_error_fingerprints=tuple(active_errors),
            pending_approval_ids=pending_approval_ids,
            side_effects_may_exist=bool(run["side_effects_may_exist"]),
        )

    def latest_compact_summary(self, run_id: str) -> CompactSummary | None:
        with self.lock:
            row = self._connection().execute(
                """
                SELECT * FROM compact_summaries
                WHERE run_id = ?
                  AND EXISTS (
                    SELECT 1 FROM verified_compact_summaries
                    WHERE verified_compact_summaries.id = json_extract(
                        compact_summaries.summary_metadata_json,
                        '$.verified_summary_id'
                    )
                  )
                ORDER BY creation_seq DESC LIMIT 1
                """,
                (run_id,),
            ).fetchone()
        return _compact_summary_from_row(row)

    def compaction_count(self, run_id: str) -> int:
        with self.lock:
            row = self._connection().execute(
                "SELECT compaction_count FROM runs WHERE id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise ResourceNotFoundError("run not found")
        return int(row[0])

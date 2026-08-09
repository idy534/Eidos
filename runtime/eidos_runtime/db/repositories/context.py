from __future__ import annotations

import json
import sqlite3
import uuid

from eidos_runtime.context.facts import CompactSummary, ContextFacts, ContextItemFact
from eidos_runtime.db.database import CommittedMutation, Repository, now_ms as _now_ms
from eidos_runtime.db.errors import (
    ContextLimitExceeded,
    InvalidRunStateError,
    ResourceNotFoundError,
)
from eidos_runtime.db.events import append_event
from eidos_runtime.db.mappers import _compact_summary_from_row, _json_tuple
from eidos_runtime.runtime.contracts import ProgressSignature
from eidos_runtime.runtime.state_machine import EventType

# The soft limits bound unprotected history selected in one projection. Recent
# facts are protected from compaction and may exceed the soft limit when the
# model-visible projection still fits the separate hard serialization ceiling.
# Neither limit is the provider model context window.
CONTEXT_PROJECTION_MAX_BYTES = 768 * 1024
CONTEXT_PROJECTION_MAX_ITEMS = 200
# Compaction candidates are never sent to the model. Keep their payloads
# bounded independently of the model projection so an oversized historical
# item can still be represented by its durable source id and summarized.
COMPACTION_FACT_VALUE_MAX_CHARS = 8 * 1024
CONTEXT_PROJECTION_HARD_MAX_BYTES = 8 * 1024 * 1024
CONTEXT_PROJECTION_HARD_MAX_ITEMS = 2_000
RECENT_CONTEXT_STEPS = 3


class ContextRepository(Repository):
    def context_projection_facts(self, run_id: str) -> ContextFacts:
        return self._bounded_context_facts(run_id, newest=True)

    def compaction_candidate_facts(self, run_id: str) -> ContextFacts:
        return self._bounded_context_facts(run_id, newest=False)

    def _bounded_context_facts(
        self, run_id: str, *, newest: bool
    ) -> ContextFacts:
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
                WHERE run_id = ? ORDER BY creation_seq DESC LIMIT 1
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
                SELECT * FROM items
                WHERE session_id = ? AND kind = 'user_message'
                  AND status = 'completed'
                  AND NOT EXISTS (
                    SELECT 1 FROM run_revisions
                    WHERE run_revisions.source_run_id = items.run_id
                  )
                ORDER BY creation_seq DESC LIMIT 1
                """,
                (run["session_id"],),
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
            protected_ids = set(protected_rows)
            excluded = source_ids | protected_ids
            excluded_sql = ""
            excluded_values: tuple[object, ...] = ()
            if excluded:
                placeholders = ",".join("?" for _ in excluded)
                excluded_sql = f" AND items.id NOT IN ({placeholders})"
                excluded_values = tuple(excluded)
            base = f"""
                FROM items LEFT JOIN tool_calls ON tool_calls.item_id = items.id
                WHERE items.session_id = ?
                  AND items.status IN ('completed', 'failed', 'declined')
                  AND NOT (items.kind = 'assistant_message' AND items.incomplete = 1)
                  AND NOT EXISTS (
                    SELECT 1 FROM run_revisions
                    WHERE run_revisions.source_run_id = items.run_id
                  )
                  {excluded_sql}
            """
            size_expression = """
                length(CAST(COALESCE(items.content, '') AS BLOB))
                + length(CAST(COALESCE(tool_calls.arguments_json, '') AS BLOB))
                + length(CAST(COALESCE(
                    tool_calls.model_result_json, tool_calls.result_json, ''
                ) AS BLOB)) + 256
            """
            aggregate = connection.execute(
                f"SELECT COUNT(*), COALESCE(SUM({size_expression}), 0) {base}",
                (run["session_id"], *excluded_values),
            ).fetchone()
            metadata = connection.execute(
                f"""
                SELECT items.id, items.creation_seq, {size_expression} AS fact_bytes
                {base}
                ORDER BY items.creation_seq {'DESC' if newest else 'ASC'} LIMIT ?
                """,
                (
                    run["session_id"],
                    *excluded_values,
                    CONTEXT_PROJECTION_MAX_ITEMS + 1,
                ),
            ).fetchall()
            goal_size = (
                len(str(goal_row["content"] or "").encode("utf-8")) + 256
                if goal_row is not None else 0
            )
            if goal_size > CONTEXT_PROJECTION_MAX_BYTES:
                raise ContextLimitExceeded("current_user_goal_too_large")
            selected_ids = list(protected_ids) if newest else []
            protected_bytes = 0
            if newest and protected_ids:
                placeholders = ",".join("?" for _ in protected_ids)
                protected_bytes = int(connection.execute(
                    f"""
                    SELECT COALESCE(SUM({size_expression}), 0)
                    FROM items LEFT JOIN tool_calls ON tool_calls.item_id = items.id
                    WHERE items.id IN ({placeholders})
                    """,
                    tuple(protected_ids),
                ).fetchone()[0])
            if (
                len(protected_ids) > CONTEXT_PROJECTION_HARD_MAX_ITEMS
                or protected_bytes > CONTEXT_PROJECTION_HARD_MAX_BYTES
            ):
                raise ContextLimitExceeded("internal_projection_limit")
            selection_item_limit = max(
                CONTEXT_PROJECTION_MAX_ITEMS, len(protected_ids)
            )
            selection_byte_limit = max(
                CONTEXT_PROJECTION_MAX_BYTES, protected_bytes
            )
            selected_bytes = protected_bytes
            if (
                len(selected_ids) > selection_item_limit
                or selected_bytes > selection_byte_limit
            ):
                raise ContextLimitExceeded("internal_projection_limit")
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
                        f"SELECT * FROM items WHERE id IN ({placeholders})",
                        selected_ids,
                    ).fetchall()
                else:
                    item_rows = connection.execute(
                        f"""
                        SELECT creation_seq, id, session_id, run_id, ordinal,
                               model_step_index, kind, status,
                               substr(content, 1, ?) AS content,
                               incomplete, created_at, completed_at
                        FROM items WHERE id IN ({placeholders})
                        """,
                        (COMPACTION_FACT_VALUE_MAX_CHARS, *selected_ids),
                    ).fetchall()
            item_rows.sort(key=lambda row: int(row["creation_seq"]))
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
                > max(0, CONTEXT_PROJECTION_MAX_BYTES - protected_bytes)
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
        tools_by_item = {row["item_id"]: row for row in tool_rows}
        items: list[ContextItemFact] = []
        serialized_bytes = 2
        serialization_limit = (
            CONTEXT_PROJECTION_HARD_MAX_BYTES
            if protected_bytes > CONTEXT_PROJECTION_MAX_BYTES
            else CONTEXT_PROJECTION_MAX_BYTES
        )
        projected_candidate_bytes = 0
        projected_candidate_count = 0
        for row in item_rows:
            tool = tools_by_item.get(row["id"])
            model_result_json = (
                str(tool["model_result_json"])
                if tool and tool["model_result_json"] is not None
                else None
            )
            fact = ContextItemFact(
                item_id=str(row["id"]),
                run_id=str(row["run_id"]),
                kind=str(row["kind"]),
                status=str(row["status"]),
                content=row["content"],
                provider_call_id=str(tool["provider_call_id"]) if tool else None,
                tool_name=str(tool["tool_name"]) if tool else None,
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
            size = len(json.dumps(
                fact.model_dump(),
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")) + 1
            if serialized_bytes + size > serialization_limit:
                candidate_overflow = True
                if goal_row is not None and row["id"] == goal_row["id"]:
                    raise ContextLimitExceeded("current_user_goal_too_large")
                if str(row["id"]) in protected_ids:
                    raise ContextLimitExceeded("internal_projection_limit")
                continue
            items.append(fact)
            serialized_bytes += size
            if str(row["id"]) not in protected_ids:
                projected_candidate_bytes += size
                projected_candidate_count += 1
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
            active_error_fingerprints=tuple(active_errors),
        )

    def latest_compact_summary(self, run_id: str) -> CompactSummary | None:
        with self.lock:
            row = self._connection().execute(
                """
                SELECT * FROM compact_summaries
                WHERE run_id = ? ORDER BY creation_seq DESC LIMIT 1
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

    def commit_compaction(
        self, run_id: str, phase: str, summary: CompactSummary
    ) -> CommittedMutation[CompactSummary]:
        if phase not in {"pre_turn", "mid_turn"}:
            raise ValueError("invalid compaction phase")
        summary_id = str(uuid.uuid4())
        now = _now_ms()
        with self.lock, self._connection() as connection:
            run = connection.execute(
                "SELECT session_id, compaction_count FROM runs WHERE id = ? AND status = 'running'",
                (run_id,),
            ).fetchone()
            if run is None:
                raise InvalidRunStateError("run is not active")
            connection.execute(
                """
                INSERT INTO compact_summaries (
                    id, session_id, run_id, task_goal, constraints_json,
                    completed_actions_json, workspace_changes_json,
                    important_facts_json, unresolved_problems_json,
                    next_actions_json, source_item_ids_json, phase, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    summary_id, run["session_id"], run_id, summary.task_goal,
                    _json_tuple(summary.constraints),
                    _json_tuple(summary.completed_actions),
                    _json_tuple(summary.workspace_changes),
                    _json_tuple(summary.important_facts),
                    _json_tuple(summary.unresolved_problems),
                    _json_tuple(summary.next_actions),
                    _json_tuple(summary.source_item_ids), phase, now,
                ),
            )
            updated = connection.execute(
                """
                UPDATE runs SET compaction_count = compaction_count + 1, updated_at = ?
                WHERE id = ? AND status = 'running'
                """,
                (now, run_id),
            )
            if updated.rowcount != 1:
                raise InvalidRunStateError("compaction could not commit")
            event = append_event(
                connection,
                EventType.CONTEXT_COMPACTED,
                now,
                {
                    "summaryId": summary_id,
                    "sourceItemCount": len(summary.source_item_ids),
                    "phase": phase,
                },
                session_id=run["session_id"],
                run_id=run_id,
            )
        return CommittedMutation(summary, (event,))

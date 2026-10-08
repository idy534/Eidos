from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from contextlib import ExitStack
from collections.abc import Callable

from eidos_runtime.db.database import now_ms
from eidos_runtime.memory.contracts import (
    MemoryBackfillRequest,
    MemoryActionResult,
    MemoryConsolidation,
    MemoryExtraction,
    MemoryJobRequest,
    MemoryReadRequest,
    MemoryRecord,
    MemoryState,
)
from eidos_runtime.memory.model_executor import MemoryModelExecutor
from eidos_runtime.memory.repository import MemoryRejected
from eidos_runtime.memory.fact_review import require_supported_fact
from eidos_runtime.memory.service import MemoryCancellation, MemoryService
from eidos_runtime.model.client import ModelRequestError
from eidos_runtime.model.config import ModelConfigStore
from eidos_runtime.model.gateway import ModelGateway
from eidos_runtime.sandbox.sensitive import default_scanner


logger = logging.getLogger("eidos.runtime.memory")
LIVE_STATES = "('queued','running','waiting_approval','waiting_input','waiting_agents','finalizing')"


class MemoryJobs:
    """Durable domain jobs executed by the existing managed-task supervisor."""

    def __init__(
        self,
        service: MemoryService,
        configs: ModelConfigStore,
        gateway: ModelGateway,
        foreground_busy: Callable[[], bool],
    ) -> None:
        self.service = service
        self.database = service.database
        self.configs = configs
        self.executor = MemoryModelExecutor(service, gateway)
        self.foreground_busy = foreground_busy

    def run(self, cancel: threading.Event) -> None:
        # Single process-owned service. The supervisor owns cancellation and
        # resource settlement; there is no unregistered thread or in-memory queue.
        self.recover()
        next_collection = 0.0
        while not cancel.is_set():
            try:
                self.service.cleanup()
                with self.database.lock:
                    missing = (
                        self.database.connection()
                        .execute(
                            "SELECT s.id FROM memory_scopes s WHERE s.generation>0 AND NOT EXISTS(SELECT 1 FROM memory_generations g WHERE g.scope_id=s.id AND g.generation=s.generation) LIMIT 1"
                        )
                        .fetchone()
                    )
                if missing:
                    self.service.refresh_generation(missing[0])
                if time.monotonic() >= next_collection:
                    self.service.collect_unreferenced_files()
                    next_collection = time.monotonic() + 3600
                self.enqueue_ready()
                if not self.foreground_busy():
                    job = self.claim()
                    if job is not None:
                        self.process(job, cancel)
            except Exception as error:
                logger.warning(
                    "Memory service iteration failed error_class=%s",
                    type(error).__name__,
                )
            cancel.wait(1.0)

    def recover(self) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE memory_jobs SET state='retry_wait',lease_token=NULL,not_before=? WHERE state='running' AND lease_until<?",
                (now_ms(), now_ms()),
            )
            connection.execute(
                "UPDATE memory_model_attempts SET state='failed',error_code='memory_attempt_interrupted' WHERE state='running'"
            )

    def enqueue_ready(self) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE memory_sources SET backfill_enabled=0 WHERE backfill_enabled=1 "
                "AND processed_frontier>=backfill_until AND processed_offset=0"
            )
            # Only continue explicitly selected history, within its saved upper bound.
            rows = connection.execute(
                "SELECT s.session_id,r.id AS run_id,r.model_id,max(i.creation_seq) AS frontier FROM memory_sources s "
                "JOIN items i ON i.session_id=s.session_id JOIN runs r ON r.id=i.run_id "
                "WHERE s.deleted=0 AND s.temporary=0 AND s.backfill_enabled=1 "
                "AND i.creation_seq<=s.backfill_until AND i.creation_seq>s.processed_frontier "
                "AND i.status='completed' AND i.incomplete=0 AND r.status NOT IN "
                + LIVE_STATES
                + " AND NOT EXISTS(SELECT 1 FROM runs active WHERE active.session_id=s.session_id AND active.status IN "
                + LIVE_STATES
                + ") "
                "AND NOT EXISTS(SELECT 1 FROM agent_delegations WHERE child_session_id=s.session_id) "
                "AND NOT EXISTS(SELECT 1 FROM memory_jobs j WHERE j.session_id=s.session_id AND j.source_revision=s.revision AND j.frontier=s.processed_frontier AND j.start_offset=s.processed_offset AND j.state NOT IN ('succeeded','superseded','canceled')) "
                "GROUP BY s.session_id ORDER BY min(i.creation_seq) LIMIT 100"
            ).fetchall()
            for row in rows:
                scopes = self.service.repository.scopes(
                    connection, row["session_id"], "current"
                )
                scope = scopes[0]
                self._enqueue(connection, row["session_id"], scope.id, row["model_id"])

    def _enqueue(
        self,
        connection: sqlite3.Connection,
        session_id: str,
        scope_id: str,
        model_id: str,
        *,
        backfill: bool = False,
    ) -> None:
        source = connection.execute(
            "SELECT * FROM memory_sources WHERE session_id=?", (session_id,)
        ).fetchone()
        scope = self.service.repository.scope(connection, scope_id)
        if (
            source is None
            or source["temporary"]
            or source["deleted"]
            or not source["backfill_enabled"]
            or not source["backfill_until"]
        ):
            raise MemoryRejected("memory_history_not_authorized")
        connection.execute(
            "UPDATE memory_jobs SET state='superseded',lease_token=NULL WHERE session_id=? AND source_revision<>? AND state IN ('queued','running','retry_wait','paused_budget','blocked_model')",
            (session_id, source["revision"]),
        )
        if connection.execute(
            "SELECT 1 FROM memory_jobs WHERE session_id=? AND state IN ('queued','running','retry_wait','paused_budget','blocked_model')",
            (session_id,),
        ).fetchone():
            return
        earliest = 0
        frontier = 0 if backfill else source["processed_frontier"]
        start_offset = 0 if backfill else source["processed_offset"]
        sources, target, target_offset = self._window(
            connection, session_id, scope_id, frontier, start_offset, earliest, source["backfill_until"]
        )
        if target <= frontier and not target_offset:
            return
        if not sources:
            connection.execute(
                "UPDATE memory_sources SET processed_frontier=?,processed_offset=? WHERE session_id=?",
                (
                    target if not target_offset else target - 1,
                    target_offset,
                    session_id,
                ),
            )
            return
        config = self.configs.get(model_id)
        snapshot = config.model_dump_json(exclude={"api_key"}) if config else "{}"
        connection.execute(
            "INSERT OR IGNORE INTO memory_jobs(id,scope_id,session_id,source_revision,privacy_epoch,base_generation,kind,state,model_id,model_snapshot_json,frontier,target_frontier,start_offset,target_offset,source_since,not_before,created_at,policy_version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                str(uuid.uuid4()),
                scope_id,
                session_id,
                source["revision"],
                scope.privacy_epoch,
                scope.generation,
                "extract",
                "queued" if config else "blocked_model",
                model_id,
                snapshot,
                frontier,
                target,
                start_offset,
                target_offset,
                earliest,
                now_ms(),
                now_ms(),
                2,
            ),
        )

    def backfill(self, request: MemoryBackfillRequest) -> MemoryState:
        with self.database.transaction() as connection:
            self.service.repository.assert_writable(connection, request.session_id)
            scope = self.service.repository.scopes(
                connection, request.session_id, "current"
            )[0]
            replay = self.service.repository.replay(
                connection, request.operation_id, {scope.id}, request.model_dump_json()
            )
            if replay:
                return self.service.read(
                    MemoryReadRequest(session_id=request.session_id)
                )
            latest = connection.execute(
                "SELECT model_id FROM runs WHERE session_id=? ORDER BY creation_seq DESC LIMIT 1",
                (request.session_id,),
            ).fetchone()
            if latest is None:
                raise MemoryRejected("memory_history_empty")
            upper = connection.execute(
                "SELECT COALESCE(max(creation_seq),0) FROM items WHERE session_id=?",
                (request.session_id,),
            ).fetchone()[0]
            # Capture this selection once. Later messages require another explicit selection.
            connection.execute(
                "UPDATE memory_sources SET backfill_enabled=1,backfill_until=?,processed_frontier=0,processed_offset=0 WHERE session_id=?",
                (upper, request.session_id),
            )
            self._enqueue(
                connection, request.session_id, scope.id, latest[0], backfill=True
            )
            self.service.repository.save_action(
                connection,
                request,
                scope.id,
                "backfill",
                MemoryActionResult(
                    operation_id=request.operation_id,
                    status="pending",
                    code="memory_backfill_queued",
                ),
            )
        return self.service.read(MemoryReadRequest(session_id=request.session_id))

    def retry(self, request: MemoryJobRequest) -> MemoryState:
        with self.database.transaction() as connection:
            self.service.repository.assert_writable(connection, request.session_id)
            scopes = {
                s.id
                for s in self.service.repository.scopes(connection, request.session_id)
            }
            row = connection.execute(
                "SELECT * FROM memory_jobs WHERE id=?", (request.job_id,)
            ).fetchone()
            if (
                row is None
                or row["scope_id"] not in scopes
                or row["state"]
                not in {"failed", "blocked_model", "paused_budget", "retry_wait"}
            ):
                raise MemoryRejected("memory_job_not_retryable")
            config = self.configs.get(row["model_id"])
            if config is None:
                raise MemoryRejected("memory_model_unavailable")
            source = connection.execute(
                "SELECT revision,backfill_enabled,backfill_until FROM memory_sources WHERE session_id=?",
                (row["session_id"],),
            ).fetchone()
            scope = self.service.repository.scope(connection, row["scope_id"])
            if (
                source is None
                or source[0] != row["source_revision"]
                or not source["backfill_enabled"]
                or source["backfill_until"] < row["target_frontier"]
                or row["policy_version"] != 2
                or scope.privacy_epoch != row["privacy_epoch"]
            ):
                raise MemoryRejected("memory_job_stale")
            connection.execute(
                "UPDATE memory_jobs SET state='queued',lease_token=NULL,attempts=0,error_code=NULL,not_before=?,model_snapshot_json=? WHERE id=?",
                (now_ms(), config.model_dump_json(exclude={"api_key"}), row["id"]),
            )
        return self.service.read(MemoryReadRequest(session_id=request.session_id))

    def claim(self) -> sqlite3.Row | None:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE memory_jobs SET state='retry_wait',lease_token=NULL WHERE state='running' AND lease_until<?",
                (now_ms(),),
            )
            connection.execute(
                "UPDATE memory_jobs SET state='queued',not_before=? WHERE state='paused_budget' AND not_before<=?",
                (now_ms(), now_ms()),
            )
            if connection.execute(
                "SELECT 1 FROM memory_jobs WHERE state='running'"
            ).fetchone():
                return None
            row = connection.execute(
                "SELECT j.* FROM memory_jobs j WHERE state IN ('queued','retry_wait') AND not_before<=? ORDER BY (SELECT max(a.created_at) FROM memory_model_attempts a JOIN memory_jobs other ON other.id=a.job_id WHERE other.scope_id=j.scope_id),j.created_at LIMIT 1",
                (now_ms(),),
            ).fetchone()
            if row is None:
                return None
            token = str(uuid.uuid4())
            connection.execute(
                "UPDATE memory_jobs SET state='running',lease_token=?,lease_until=?,attempts=attempts+1 WHERE id=? AND state IN ('queued','retry_wait')",
                (token, now_ms() + 180000, row["id"]),
            )
            return connection.execute(
                "SELECT * FROM memory_jobs WHERE id=?", (row["id"],)
            ).fetchone()

    def _validate(self, connection: sqlite3.Connection, job: sqlite3.Row) -> None:
        current = connection.execute(
            "SELECT state,lease_token,lease_until FROM memory_jobs WHERE id=?",
            (job["id"],),
        ).fetchone()
        source = connection.execute(
            "SELECT revision,temporary,deleted,backfill_enabled,backfill_until FROM memory_sources WHERE session_id=?",
            (job["session_id"],),
        ).fetchone()
        scope = self.service.repository.scope(connection, job["scope_id"])
        if (
            current is None
            or current["state"] != "running"
            or current["lease_token"] != job["lease_token"]
            or current["lease_until"] < now_ms()
        ):
            raise MemoryRejected("memory_lease_stale")
        if (
            source is None
            or source[0] != job["source_revision"]
            or source[1]
            or source[2]
            or not source["backfill_enabled"]
            or source["backfill_until"] < job["target_frontier"]
            or job["policy_version"] != 2
        ):
            raise MemoryRejected("memory_source_conflict")
        if scope.privacy_epoch != job["privacy_epoch"]:
            raise MemoryRejected("memory_privacy_conflict")

    def process(self, job: sqlite3.Row, parent_cancel: threading.Event) -> None:
        cancel = JobCancellation(self, job, parent_cancel)
        try:
            config = self.configs.get(job["model_id"])
            if (
                config is None
                or config.model_dump_json(exclude={"api_key"})
                != job["model_snapshot_json"]
            ):
                raise MemoryRejected("memory_model_unavailable")
            with self.database.transaction() as connection:
                self._validate(connection, job)
                connection.execute(
                    "UPDATE memory_jobs SET base_generation=? WHERE id=? AND lease_token=?",
                    (
                        self.service.repository.scope(
                            connection, job["scope_id"]
                        ).generation,
                        job["id"],
                        job["lease_token"],
                    ),
                )
                job = connection.execute(
                    "SELECT * FROM memory_jobs WHERE id=?", (job["id"],)
                ).fetchone()
                sources = self._sources(connection, job)
            if job["extraction_json"]:
                extraction = MemoryExtraction.model_validate_json(
                    job["extraction_json"]
                )
            else:
                extraction = self.executor.execute(
                    job["id"],
                    config,
                    "extract",
                    json.dumps({"sources": sources}, ensure_ascii=False),
                    MemoryExtraction,
                    cancel,
                )
            related = []
            with self.database.transaction() as connection:
                self._validate(connection, job)
                for candidate in extraction.candidates:
                    self._validate_candidate(connection, job, candidate, sources)
                    matches = self.service.related_facts(candidate, job["session_id"])
                    related.extend(
                        e for e in matches if e.id not in {v.id for v in related}
                    )
                connection.execute(
                    "UPDATE memory_jobs SET extraction_json=? WHERE id=? AND lease_token=?",
                    (extraction.model_dump_json(), job["id"], job["lease_token"]),
                )
            if extraction.candidates:
                # Include complete related entries only. Partial old claims must
                # not become update targets simply to fit a request budget.
                retained = []
                for entry in related[:32]:
                    trial = {
                        "extraction": extraction.model_dump(mode="json"),
                        "original_sources": sources,
                        "related_entries": [
                            e.model_dump(mode="json") for e in [*retained, entry]
                        ],
                    }
                    if len(json.dumps(trial, ensure_ascii=False).encode()) <= 90 * 1024:
                        retained.append(entry)
                related = retained
                proposals = self.executor.execute(
                    job["id"],
                    config,
                    "consolidate",
                    json.dumps(
                        {
                            "extraction": extraction.model_dump(mode="json"),
                            "original_sources": sources,
                            "related_entries": [
                                e.model_dump(mode="json") for e in related[:32]
                            ],
                        },
                        ensure_ascii=False,
                    ),
                    MemoryConsolidation,
                    cancel,
                )
            else:
                proposals = MemoryConsolidation()
            self._publish(job, extraction, proposals, related, sources)
            self.service.refresh_generation(job["scope_id"])
            logger.info(
                "Memory job succeeded job_id=%s source_revision=%s",
                job["id"],
                job["source_revision"],
            )
        except Exception as original_error:
            error = original_error
            try:
                with self.database.lock:
                    self._validate(self.database.connection(), job)
            except MemoryRejected as stale:
                error = stale
            code = (
                str(error)
                if isinstance(error, MemoryRejected)
                else error.failure.code
                if isinstance(error, ModelRequestError)
                else "memory_job_failed"
            )
            state = (
                "blocked_model"
                if code == "memory_model_unavailable"
                else "paused_budget"
                if code == "memory_budget_exceeded"
                else "superseded"
                if code
                in {
                    "memory_source_conflict",
                    "memory_privacy_conflict",
                    "memory_lease_stale",
                    "memory_revision_conflict",
                }
                else "retry_wait"
                if isinstance(error, ModelRequestError)
                and error.failure.retryable
                and job["attempts"] < 3
                else "canceled"
                if parent_cancel.is_set()
                else "failed"
            )
            not_before = (
                (now_ms() // 86400000 + 1) * 86400000
                if state == "paused_budget"
                else now_ms() + (60000 if job["attempts"] < 2 else 300000)
            )
            with self.database.transaction() as connection:
                connection.execute(
                    "UPDATE memory_jobs SET state=?,error_code=?,not_before=?,lease_token=NULL WHERE id=? AND lease_token=?",
                    (state, code, not_before, job["id"], job["lease_token"]),
                )
            logger.warning(
                "Memory job ended job_id=%s state=%s error_code=%s",
                job["id"],
                state,
                code,
            )

    def _window(
        self,
        connection: sqlite3.Connection,
        session_id: str,
        scope_id: str,
        frontier: int,
        start_offset: int,
        earliest: int = 0,
        target: int | None = None,
    ) -> tuple[list[dict[str, object]], int, int]:
        rows = connection.execute(
            "SELECT i.id,i.creation_seq,i.kind,i.status,i.incomplete,i.run_id,i.created_at,r.work_mode,t.tool_name, "
            "length(COALESCE(NULLIF(i.content,''),t.result_json,'')) AS body_length "
            "FROM items i JOIN runs r ON r.id=i.run_id LEFT JOIN tool_calls t ON t.item_id=i.id WHERE i.session_id=? AND i.creation_seq>? AND i.creation_seq<=? ORDER BY i.creation_seq LIMIT 32",
            (session_id, frontier, target or 9007199254740991),
        ).fetchall()
        sources = []
        budget = 0
        last = frontier
        offset = 0
        for row in rows:
            if row["status"] == "in_progress":
                break
            if (
                row["kind"] == "assistant_message"
                or row["status"] != "completed"
                or row["incomplete"]
                or (row["tool_name"] or "").startswith("memory_")
                or row["created_at"] < earliest
            ):
                last = row["creation_seq"]
                continue
            if connection.execute(
                "SELECT 1 FROM memory_suppressions WHERE scope_id=? AND item_id=?",
                (scope_id, row["id"]),
            ).fetchone():
                last = row["creation_seq"]
                continue
            eligible = connection.execute(
                "SELECT eligible FROM memory_source_items WHERE item_id=?", (row["id"],)
            ).fetchone()
            if (
                not eligible
                or not eligible[0]
                or connection.execute(
                    "SELECT 1 FROM run_revisions WHERE source_run_id=?",
                    (row["run_id"],),
                ).fetchone()
            ):
                last = row["creation_seq"]
                continue
            begin = start_offset if row == rows[0] else 0
            body = connection.execute(
                "SELECT substr(COALESCE(NULLIF(i.content,''),t.result_json,''),?,6000) FROM items i LEFT JOIN tool_calls t ON t.item_id=i.id WHERE i.id=?",
                (begin + 1, row["id"]),
            ).fetchone()[0]
            if budget + len(body.encode()) > 64000:
                break
            budget += len(body.encode())
            safe = default_scanner().scan_text(body, reject_denied=False).text
            sources.append(
                {
                    "item_id": row["id"],
                    "kind": row["kind"],
                    "work_mode": row["work_mode"],
                    "content": safe,
                    "slice_start": begin,
                    "slice_end": begin + len(body),
                    "source_characters": row["body_length"],
                }
            )
            last = row["creation_seq"]
            if begin + len(body) < row["body_length"]:
                offset = begin + len(body)
                break
        return sources, last, offset

    def _sources(
        self, connection: sqlite3.Connection, job: sqlite3.Row
    ) -> list[dict[str, object]]:
        sources, target, offset = self._window(
            connection,
            job["session_id"],
            job["scope_id"],
            job["frontier"],
            job["start_offset"],
            earliest=job["source_since"],
            target=job["target_frontier"],
        )
        if target != job["target_frontier"] or offset != job["target_offset"]:
            raise MemoryRejected("memory_source_conflict")
        return sources

    def _validate_candidate(self, connection, job, candidate, sources) -> None:
        self.service._safe_record(candidate)
        inputs = {s["item_id"]: s for s in sources}
        if not candidate.source_item_ids or set(candidate.source_quotes) != set(
            candidate.source_item_ids
        ):
            raise MemoryRejected("memory_evidence_required")
        for item_id in candidate.source_item_ids:
            quote = candidate.source_quotes[item_id]
            if (
                item_id not in inputs
                or not quote.strip()
                or len(quote) > 4096
                or quote not in inputs[item_id]["content"]
            ):
                raise MemoryRejected("memory_evidence_quote_invalid")
        self.service.repository.evidence(
            connection,
            job["session_id"],
            candidate.source_item_ids,
            job["scope_id"],
            candidate.evidence_class,
        )

    def _publish(self, job, extraction, proposals, related, sources) -> None:
        if len(proposals.changes) != len(extraction.candidates) or {
            p.candidate_index for p in proposals.changes
        } != set(range(len(extraction.candidates))):
            raise MemoryRejected("memory_proposal_coverage_invalid")
        targets = [p.target_entry_id for p in proposals.changes if p.target_entry_id]
        if len(targets) != len(set(targets)):
            raise MemoryRejected("memory_proposal_target_conflict")
        existing = {e.id: e for e in related}
        with ExitStack() as stack:
            refs = {
                p.candidate_index: stack.enter_context(
                    self.service.prepared(
                        job["scope_id"],
                        existing[p.target_entry_id].content
                        if p.action in {"corroborate", "archive"}
                        and p.target_entry_id in existing
                        else p.content
                        or extraction.candidates[p.candidate_index].content,
                    )
                )
                for p in proposals.changes
                if p.action != "noop"
            }
            with self.database.transaction() as connection:
                self._validate(connection, job)
                if (
                    self.service.repository.scope(
                        connection, job["scope_id"]
                    ).generation
                    != job["base_generation"]
                ):
                    raise MemoryRejected("memory_revision_conflict")
                for proposal in proposals.changes:
                    if proposal.action == "noop":
                        continue
                    require_supported_fact(proposal.grounded, proposal.atomic)
                    candidate = extraction.candidates[proposal.candidate_index]
                    self._validate_candidate(connection, job, candidate, sources)
                    evidence = self.service.repository.evidence(
                        connection,
                        job["session_id"],
                        candidate.source_item_ids,
                        job["scope_id"],
                        candidate.evidence_class,
                    )
                    record = MemoryRecord.model_validate(
                        candidate.model_dump(
                            exclude={"evidence_class", "source_quotes", "requires_confirmation"}
                        )
                    )
                    if proposal.content:
                        record = record.model_copy(update={"content": proposal.content})
                    status = (
                        "candidate"
                        if candidate.evidence_class == "inferred" or candidate.requires_confirmation
                        else "active"
                    )
                    target = None
                    if proposal.target_entry_id:
                        if status == "candidate":
                            raise MemoryRejected("memory_candidate_target_invalid")
                        target = existing.get(proposal.target_entry_id)
                        if (
                            target is None
                            or target.revision != proposal.expected_revision
                        ):
                            raise MemoryRejected("memory_proposal_target_invalid")
                        # Background work must not override any explicit control.
                        self.service.repository.assert_automatic_target(connection, target)
                        if proposal.action in {"corroborate", "archive"}:
                            evidence = list(
                                {
                                    e.item_id: e for e in [*target.evidence, *evidence]
                                }.values()
                            )
                        if proposal.action in {"corroborate", "archive"}:
                            record = record.model_copy(
                                update={
                                    "content": target.content,
                                    "title": target.title,
                                    "aliases": target.aliases,
                                }
                            )
                        if proposal.action == "archive":
                            status = "archived"
                    self.service.repository.publish(
                        connection,
                        job["scope_id"],
                        record,
                        refs[proposal.candidate_index],
                        evidence,
                        status=status,
                        evidence_class=candidate.evidence_class,
                        entry_id=target.id if target else None,
                        expected_revision=proposal.expected_revision,
                    )
                connection.execute(
                    "UPDATE memory_sources SET processed_frontier=?,processed_offset=? WHERE session_id=? AND revision=?",
                    (
                        job["target_frontier"]
                        if not job["target_offset"]
                        else job["target_frontier"] - 1,
                        job["target_offset"],
                        job["session_id"],
                        job["source_revision"],
                    ),
                )
                connection.execute(
                    "UPDATE memory_jobs SET state='succeeded',proposals_json=?,lease_token=NULL WHERE id=? AND lease_token=?",
                    (proposals.model_dump_json(), job["id"], job["lease_token"]),
                )
                if not job["target_offset"]:
                    connection.execute(
                        "UPDATE memory_sources SET backfill_enabled=0 WHERE session_id=? AND backfill_until<=?",
                        (job["session_id"], job["target_frontier"]),
                    )


class JobCancellation(MemoryCancellation):
    def __init__(
        self, jobs: MemoryJobs, job: sqlite3.Row, parent: threading.Event
    ) -> None:
        super().__init__(jobs.service, {job["scope_id"]: job["privacy_epoch"]}, parent)
        self.jobs = jobs
        self.job = job
        self.deadline = time.monotonic() + 120

    def is_set(self) -> bool:
        if super().is_set() or time.monotonic() > self.deadline:
            return True
        with self.service.database.lock:
            try:
                self.jobs._validate(self.service.database.connection(), self.job)
            except MemoryRejected:
                self.set()
        return super().is_set()

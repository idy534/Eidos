"""Immutable conversation prefixes. Historical runs never enter the scheduler."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from collections.abc import Iterator, Callable

from pydantic import ValidationError
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from eidos_runtime.protocol.session_fork import SessionForkRequest

from eidos_runtime.context.facts import ContextFacts, ContextItemFact
from eidos_runtime.db.database import CommittedMutation, Database, Repository, now_ms
from eidos_runtime.db.errors import (
    InvalidRunStateError,
    ResourceNotFoundError,
    StorageError,
)
from eidos_runtime.db.mappers import (
    _item_from_row,
    _run_from_row,
    _remote_model_id_from_profile,
    project_tool_text,
    _truncate_snapshot_text,
    _snapshot_display_arguments,
)
from eidos_runtime.db.repositories.sessions import SessionRepository
from eidos_runtime.domain.session import SessionExecutionMode
from eidos_runtime.domain.session_fork import SessionForkOrigin, SessionForkSource
from eidos_runtime.model.replay import ModelResponseReplay
from eidos_runtime.model.response_phase import AssistantMessagePhase
from eidos_runtime.models import EidosFrozenStrictModel
from eidos_runtime.protocol.schemas import ItemDto, RunDto

MAX_HISTORY_ITEMS = 20_000
MAX_HISTORY_BYTES = 128 * 1024 * 1024
TERMINAL_STATUSES = frozenset(
    {"succeeded", "failed", "stopped", "canceled", "interrupted"}
)


class HistoryRecord(EidosFrozenStrictModel):
    item: ItemDto
    run: RunDto
    origin: SessionForkOrigin
    model_result_json: str | None = None
    model_response_id: str | None = None
    response_replay_json: str | None = None
    phase: AssistantMessagePhase | None = None
    context_eligible: bool = True

    def retarget(self, session_id: str) -> HistoryRecord:
        item_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"history:{session_id}:{self.item.id}")
        )
        run_id = str(
            uuid.uuid5(uuid.NAMESPACE_URL, f"history:{session_id}:{self.run.id}")
        )
        tool = self.item.tool_call
        if tool is not None:
            tool = tool.model_copy(
                update={
                    "id": str(
                        uuid.uuid5(
                            uuid.NAMESPACE_URL, f"history-tool:{session_id}:{tool.id}"
                        )
                    ),
                    "item_id": item_id,
                }
            )
        return self.model_copy(
            update={
                "item": self.item.model_copy(
                    update={
                        "id": item_id,
                        "session_id": session_id,
                        "run_id": run_id,
                        "tool_call": tool,
                    }
                ),
                "run": self.run.model_copy(
                    update={
                        "id": run_id,
                        "session_id": session_id,
                        "allowed_actions": [],
                    }
                ),
            }
        )

    def fact(self, database: Database, *, replay: bool) -> ContextItemFact:
        tool = self.item.tool_call
        return ContextItemFact(
            item_id=self.item.id,
            run_id=self.run.id,
            kind=self.item.kind,
            status=self.item.status,
            content=self.item.content,
            ordinal=self.item.ordinal,
            input_references=tuple(self.item.references),
            model_step_index=self.item.model_step_index,
            phase=self.phase,
            model_response_id=self.model_response_id,
            response_replay=(
                ModelResponseReplay.model_validate_json(
                    database.json_blobs.read_json(
                        self.response_replay_json, expected_kind="model-response-replay"
                    )
                )
                if replay and self.response_replay_json is not None
                else None
            ),
            provider_call_id=tool.provider_call_id if tool else None,
            tool_name=tool.tool_name if tool else None,
            payload_kind=tool.payload_kind if tool else None,
            arguments_json=tool.arguments_json if tool else None,
            result_json=tool.result_json if tool else None,
            model_result_json=self.model_result_json,
        )


class HistoryPage(EidosFrozenStrictModel):
    records: tuple[HistoryRecord, ...] = ()
    previous_item_id: str | None = None


class PreparedEntry(EidosFrozenStrictModel):
    item_id: str
    run_id: str
    record_json: str
    byte_size: int
    context_eligible: bool
    model_step_index: int | None


class PreparedResource(EidosFrozenStrictModel):
    resource_id: str
    stored: str
    input_id: str | None
    kind: str


class PreparedHistory(EidosFrozenStrictModel):
    anchor: HistoryRecord
    entries: tuple[PreparedEntry, ...]
    resources: tuple[PreparedResource, ...]


class SessionHistoryRepository(Repository):

    def _with_legacy_remote_model_id(
        self, record: HistoryRecord, cache: dict[tuple[str, str, str], str | None]
    ) -> HistoryRecord:
        if record.run.remote_model_id is not None or not record.run.model_id.startswith("custom-"):
            return record
        key = (record.origin.session_id, record.origin.run_id, record.run.model_id)
        if key not in cache:
            row = self._connection().execute(
                "SELECT model_profile_json FROM runs WHERE session_id=? AND id=? AND model_id=?",
                key,
            ).fetchone()
            cache[key] = _remote_model_id_from_profile(row[0]) if row is not None else None
        remote_model_id = cache[key]
        if remote_model_id is None:
            return record
        return record.model_copy(update={
            "run": record.run.model_copy(update={"remote_model_id": remote_model_id}),
        })

    def source(self, session_id: str) -> SessionForkSource | None:
        with self.lock:
            row = (
                self._connection()
                .execute(
                    "SELECT source_json FROM session_forks WHERE session_id=?",
                    (session_id,),
                )
                .fetchone()
            )
            return SessionForkSource.model_validate_json(row[0]) if row else None

    def item_record(self, session_id: str, item_id: str) -> HistoryRecord | None:
        with self.lock:
            row = self._connection().execute(
                "SELECT * FROM session_history_entries WHERE session_id=? AND item_id=?",
                (session_id, item_id),
            ).fetchone()
        return self._record(row) if row is not None else None

    def _record(self, row: sqlite3.Row) -> HistoryRecord:
        return self._redact_memory(self._stored_record(row))

    def _stored_record(self, row: sqlite3.Row) -> HistoryRecord:
        try:
            return HistoryRecord.model_validate_json(
                self.database.json_blobs.read_json(
                    row["record_json"],
                    expected_kind="session-history-record",
                )
            )
        except ValidationError:
            raise StorageError("session_history_record_invalid") from None

    def _redact_memory(self, record: HistoryRecord) -> HistoryRecord:
        tool = record.item.tool_call
        if tool is None or not tool.tool_name.startswith("memory_"):
            return record
        connection = self._connection()
        tracked = connection.execute(
            "SELECT epochs_json,revoked FROM memory_tool_reads WHERE item_id=?",
            (record.origin.item_id,),
        ).fetchone()
        from eidos_runtime.memory.service import MemoryService
        from eidos_runtime.runtime.errors import tool_error

        if (
            tracked
            and not tracked["revoked"]
            and MemoryService.valid_epochs(
                connection, json.loads(tracked["epochs_json"])
            )
        ):
            return record
        redacted = json.dumps(
            tool_error(
                tool.tool_name,
                "memory_snapshot_revoked",
                "Historical memory payload revoked.",
            )
        )
        return record.model_copy(
            update={
                "item": record.item.model_copy(
                    update={
                        "content": None,
                        "tool_call": tool.model_copy(
                            update={
                                "arguments_json": "{}",
                                "result_json": redacted,
                                "approval_feedback": None,
                                "change_diff": None,
                                "change_diff_bytes": None,
                                "change_diff_hash": None,
                                "result_bytes": None,
                                "result_hash": None,
                            }
                        ),
                    }
                ),
                "model_result_json": redacted,
                "response_replay_json": None,
            }
        )

    def scrub_revoked_memory(self) -> bool:
        """Bounded storage cleanup; normal reads already redact synchronously."""
        changed = False
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "SELECT value FROM memory_metadata WHERE key='history_cleanup_cursor'"
            ).fetchone()
            if cursor is None or int(cursor[0]) < 0:
                return False
            rows = connection.execute(
                "SELECT rowid AS cleanup_id,* FROM session_history_entries WHERE rowid>? ORDER BY rowid LIMIT 100",
                (int(cursor[0]),),
            ).fetchall()
            used, position = 0, int(cursor[0])
            for row in rows:
                if used >= 1024 * 1024:
                    break
                used += row["byte_size"]
                position = row["cleanup_id"]
                original = self._stored_record(row)
                record = self._redact_memory(original)
                if record == original:
                    continue
                encoded = record.model_dump_json(by_alias=True)
                stored = self.database.json_blobs.put_json(
                    "session-history-record", encoded
                )
                connection.execute(
                    "UPDATE session_history_entries SET record_json=?,byte_size=? WHERE session_id=? AND item_id=?",
                    (stored, len(encoded.encode()), row["session_id"], row["item_id"]),
                )
                connection.execute(
                    "UPDATE session_history_resources SET blob_ref=? WHERE session_id=? AND blob_ref=?",
                    (stored, row["session_id"], row["record_json"]),
                )
                changed = True
            connection.execute(
                "UPDATE memory_metadata SET value=? WHERE key='history_cleanup_cursor'",
                (str(position) if rows else "-1",),
            )
        return changed

    def anchor(self, session_id: str, item_id: str) -> HistoryRecord:
        with self.lock:
            if (
                self._connection()
                .execute(
                    "SELECT 1 FROM agent_delegations WHERE child_session_id=?",
                    (session_id,),
                )
                .fetchone()
            ):
                raise InvalidRunStateError("child sessions cannot be forked")
            inherited = (
                self._connection()
                .execute(
                    "SELECT * FROM session_history_entries WHERE session_id=? AND item_id=?",
                    (session_id, item_id),
                )
                .fetchone()
            )
            if inherited:
                record = self._record(inherited)
                if (
                    self._connection()
                    .execute(
                        "SELECT 1 FROM session_history_entries WHERE session_id=? AND run_id=? AND sequence>? LIMIT 1",
                        (session_id, inherited["run_id"], inherited["sequence"]),
                    )
                    .fetchone()
                ):
                    raise InvalidRunStateError(
                        "only the complete final answer can be forked"
                    )
            else:
                row = (
                    self._connection()
                    .execute(
                        "SELECT * FROM items WHERE session_id=? AND id=?",
                        (session_id, item_id),
                    )
                    .fetchone()
                )
                if row is None:
                    raise ResourceNotFoundError("answer not found in source session")
                record = self._native_record(row)
                later = (
                    self._connection()
                    .execute(
                        "SELECT 1 FROM items WHERE run_id=? AND ordinal>? LIMIT 1",
                        (row["run_id"], row["ordinal"]),
                    )
                    .fetchone()
                )
                if later:
                    raise InvalidRunStateError(
                        "only the complete final answer can be forked"
                    )
            if (
                record.item.kind != "assistant_message"
                or record.item.status != "completed"
                or record.item.incomplete
                or record.run.status not in TERMINAL_STATUSES
            ):
                raise InvalidRunStateError(
                    "only a completed answer of an ended run can be forked"
                )
            return record

    def _native_record(self, row: sqlite3.Row) -> HistoryRecord:
        connection = self._connection()
        run_row = connection.execute(
            "SELECT * FROM runs WHERE id=?", (row["run_id"],)
        ).fetchone()
        tool = connection.execute(
            "SELECT * FROM tool_calls WHERE item_id=?", (row["id"],)
        ).fetchone()
        response = connection.execute(
            "SELECT a.* FROM model_attempts a JOIN steps s ON s.id=a.step_id "
            "WHERE s.run_id=? AND s.ordinal=? AND a.status='completed' ORDER BY a.ordinal LIMIT 1",
            (row["run_id"], row["model_step_index"]),
        ).fetchone()
        phase = None
        if response is not None and response["protocol_diagnostics_json"]:
            if (
                json.loads(response["protocol_diagnostics_json"]).get("phaseSource")
                == "provider"
            ):
                phase = response["phase"]
        incomplete_group = (
            row["model_step_index"] is not None
            and connection.execute(
                "SELECT 1 FROM items WHERE run_id=? AND model_step_index=? AND "
                "(status NOT IN ('completed','failed','declined') OR incomplete=1) LIMIT 1",
                (row["run_id"], row["model_step_index"]),
            ).fetchone()
            is not None
        )
        record = HistoryRecord(
            item=ItemDto.model_validate(_item_from_row(row, tool)),
            run=RunDto.model_validate(
                _run_from_row(run_row, include_user_input=False)
            ).model_copy(update={"extension_snapshot": None, "activated_tools": []}),
            origin=SessionForkOrigin(
                session_id=row["session_id"], run_id=row["run_id"], item_id=row["id"]
            ),
            model_result_json=(tool["model_result_json"] or tool["result_json"])
            if tool
            else None,
            model_response_id=response["id"] if response else None,
            response_replay_json=response["response_replay_json"] if response else None,
            phase=AssistantMessagePhase(phase) if phase is not None else None,
            context_eligible=not incomplete_group
            and row["status"] in {"completed", "failed", "declined"}
            and not (row["kind"] == "assistant_message" and row["incomplete"]),
        )
        return self._redact_memory(record)

    def _prefix(
        self, session_id: str, anchor: HistoryRecord
    ) -> Iterator[HistoryRecord]:
        connection = self._connection()
        inherited_anchor = connection.execute(
            "SELECT sequence FROM session_history_entries WHERE session_id=? AND item_id=?",
            (session_id, anchor.item.id),
        ).fetchone()
        rows = connection.execute(
            "SELECT * FROM session_history_entries WHERE session_id=? AND sequence<=? ORDER BY sequence",
            (
                session_id,
                inherited_anchor[0] if inherited_anchor else MAX_HISTORY_ITEMS,
            ),
        )
        model_ids: dict[tuple[str, str, str], str | None] = {}
        for row in rows:
            yield self._with_legacy_remote_model_id(self._record(row), model_ids)
        if inherited_anchor:
            return
        end = connection.execute(
            "SELECT creation_seq FROM runs WHERE id=?", (anchor.run.id,)
        ).fetchone()[0]
        rows = connection.execute(
            "SELECT i.* FROM items i JOIN runs r ON r.id=i.run_id "
            "WHERE i.session_id=? AND r.creation_seq<=? AND NOT EXISTS ("
            "SELECT 1 FROM run_revisions v JOIN runs replacement ON replacement.id=v.run_id "
            "WHERE v.source_run_id=r.id AND replacement.creation_seq<=?) "
            "ORDER BY r.creation_seq, i.ordinal",
            (session_id, end, end),
        )
        for row in rows:
            yield self._native_record(row)

    @contextmanager
    def _capture_reader(self):
        # A standard SQLite WAL read view keeps the whole prefix consistent
        # without occupying the Runtime connection during Blob IO.
        from eidos_runtime.db.layout import STATE_DATABASE_NAME

        path = self.database.data_directory / STATE_DATABASE_NAME
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("BEGIN")
            yield _HistoryCaptureReader(self.database, connection)
        finally:
            connection.close()

    def _prepare_history(
        self,
        request: SessionForkRequest,
        session_id: str,
        cancelled: Callable[[], bool],
    ) -> PreparedHistory:
        entries: list[PreparedEntry] = []
        resources: dict[str, PreparedResource] = {}
        counted_blobs: set[str] = set()
        total = 0
        with self._capture_reader() as reader:
            anchor = reader.anchor(request.source_session_id, request.source_item_id)
            for record in reader._prefix(request.source_session_id, anchor):
                if cancelled():
                    raise HistoryCaptureCancelled("fork capture cancelled")
                if record.run.status not in TERMINAL_STATUSES:
                    raise InvalidRunStateError(
                        "history contains an unfinished earlier run"
                    )
                record = record.retarget(session_id)
                encoded = record.model_dump_json(by_alias=True)
                size = len(encoded.encode())
                total += size
                if len(entries) >= MAX_HISTORY_ITEMS or total > MAX_HISTORY_BYTES:
                    raise HistoryLimitExceeded(
                        "conversation history exceeds fork storage limits"
                    )
                stored = self.database.json_blobs.put_json(
                    "session-history-record", encoded
                )
                counted_blobs.add(stored)
                entries.append(
                    PreparedEntry(
                        item_id=record.item.id,
                        run_id=record.run.id,
                        record_json=stored,
                        byte_size=size,
                        context_eligible=record.context_eligible,
                        model_step_index=record.item.model_step_index,
                    )
                )
                for resource in reader._resources(record, stored):
                    if resource.stored not in counted_blobs:
                        total += len(
                            self.database.json_blobs.read_json(
                                resource.stored, expected_kind=resource.kind
                            ).encode()
                        )
                        counted_blobs.add(resource.stored)
                        if total > MAX_HISTORY_BYTES:
                            raise HistoryLimitExceeded(
                                "conversation history exceeds fork storage limits"
                            )
                    resources[resource.resource_id] = resource
        return PreparedHistory(
            anchor=anchor, entries=tuple(entries), resources=tuple(resources.values())
        )

    def fork(
        self,
        request: SessionForkRequest,
        *,
        session_id: str,
        worktree_id: str | None = None,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> CommittedMutation[str]:
        replay = self.database.operation_result(
            request.operation_id,
            "session/fork",
            request.model_dump(mode="json", by_alias=True),
        )
        if replay is not None:
            target = str(replay["sessionId"])
            if SessionRepository(self.database).read_session(target) is None:
                raise ResourceNotFoundError("forked session was deleted")
            return CommittedMutation(target, ())
        prepared = self._prepare_history(request, session_id, cancelled)

        def write(connection: sqlite3.Connection) -> CommittedMutation[str]:
            if cancelled():
                raise HistoryCaptureCancelled("fork capture cancelled")
            current_anchor = self.anchor(
                request.source_session_id, request.source_item_id
            )
            if current_anchor.item != prepared.anchor.item:
                raise InvalidRunStateError("source answer changed during capture")
            sessions = SessionRepository(self.database)
            projection = sessions.read_session_projection(request.source_session_id)
            if projection is None:
                raise ResourceNotFoundError("source session not found")
            if projection.worktree is not None and worktree_id is None:
                raise InvalidRunStateError(
                    "managed sessions require an independent worktree"
                )
            mutation = sessions.create_session_committed(
                projection.session.workspace_root,
                session_id=session_id,
                worktree_id=worktree_id,
                execution_mode=SessionExecutionMode.WORKTREE
                if worktree_id
                else SessionExecutionMode.LOCAL,
                project_id=projection.project.id if projection.project else None,
                workspace_roots=projection.session.roots or None,
                projectless=projection.project is None,
                title=projection.session.title or "新会话",
                transaction_connection=connection,
            )
            connection.executemany(
                "INSERT INTO session_history_entries VALUES (?,?,?,?,?,?,?,?)",
                (
                    (
                        session_id,
                        i + 1,
                        entry.item_id,
                        entry.run_id,
                        entry.record_json,
                        entry.byte_size,
                        int(entry.context_eligible),
                        entry.model_step_index,
                    )
                    for i, entry in enumerate(prepared.entries)
                ),
            )
            connection.executemany(
                "INSERT INTO session_history_resources VALUES (?,?,?,?)",
                (
                    (
                        session_id,
                        resource.resource_id,
                        resource.stored,
                        resource.input_id,
                    )
                    for resource in prepared.resources
                ),
            )
            source = SessionForkSource(
                session_id=request.source_session_id,
                run_id=prepared.anchor.run.id,
                item_id=request.source_item_id,
                item_count=len(prepared.entries),
                created_at=now_ms(),
            )
            connection.execute(
                "INSERT INTO session_forks VALUES (?,?)",
                (session_id, source.model_dump_json(by_alias=True)),
            )
            from eidos_runtime.memory.lifecycle import fork_source

            fork_source(connection, request.source_session_id, session_id)
            return CommittedMutation(session_id, mutation.events)

        with self.lock, self.database.json_blobs.lock:
            # Recheck staged content while GC is excluded, before the short
            # metadata transaction. Missing or corrupted content fails closed.
            for entry in prepared.entries:
                self.database.json_blobs.read_json(
                    entry.record_json, expected_kind="session-history-record"
                )
            for resource in prepared.resources:
                self.database.json_blobs.read_json(
                    resource.stored, expected_kind=resource.kind
                )
                if (
                    resource.input_id
                    and self._connection()
                    .execute(
                        "SELECT 1 FROM input_references WHERE id=?",
                        (resource.input_id,),
                    )
                    .fetchone()
                    is None
                ):
                    raise ResourceNotFoundError(
                        "input reference was revoked during capture"
                    )
            return self._write_committed(
                write,
                operation_id=request.operation_id,
                operation_scope="session/fork",
                operation_request=request.model_dump(mode="json", by_alias=True),
                serialize_value=lambda value: {"sessionId": value},
                deserialize_value=_replayed_session,
            )

    def _resources(
        self, record: HistoryRecord, stored_record: str
    ) -> Iterator[PreparedResource]:
        connection = self._connection()
        resources: list[tuple[str, str, str | None, str]] = []
        if record.item.tool_call is not None:
            resources.append(
                (
                    "history-tool:" + record.item.tool_call.id,
                    stored_record,
                    None,
                    "session-history-record",
                )
            )
        if record.response_replay_json:
            resources.append(
                (
                    "response:" + str(record.model_response_id),
                    record.response_replay_json,
                    None,
                    "model-response-replay",
                )
            )
        if record.model_result_json:
            content = json.loads(record.model_result_json).get("modelContentSnapshot")
            if isinstance(content, str):
                resources.append(
                    (
                        "tool-content:" + hashlib.sha256(content.encode()).hexdigest(),
                        content,
                        None,
                        "tool-result-content",
                    )
                )
        for reference in record.item.references:
            row = connection.execute(
                "SELECT snapshot_json FROM input_references WHERE id=?", (reference.id,)
            ).fetchone()
            if row is None:
                raise ResourceNotFoundError("input reference snapshot is unavailable")
            resources.append(
                ("input:" + reference.id, row[0], reference.id, "input-reference")
            )
        for identifier, stored, input_id, kind in resources:
            self.database.json_blobs.read_json(stored, expected_kind=kind)
            yield PreparedResource(
                resource_id=identifier, stored=stored, input_id=input_id, kind=kind
            )

    def page(
        self,
        session_id: str,
        *,
        limit: int,
        before_item_id: str | None = None,
        byte_budget: int = 768 * 1024,
    ) -> HistoryPage:
        with self.lock:
            before = MAX_HISTORY_ITEMS + 1
            if before_item_id:
                anchor = (
                    self._connection()
                    .execute(
                        "SELECT sequence FROM session_history_entries WHERE session_id=? AND item_id=?",
                        (session_id, before_item_id),
                    )
                    .fetchone()
                )
                if anchor is None:
                    raise ResourceNotFoundError("history cursor not found in session")
                before = anchor[0]
            rows = self._connection().execute(
                "SELECT * FROM session_history_entries WHERE session_id=? AND sequence<? ORDER BY sequence DESC LIMIT ?",
                (session_id, before, limit + 1),
            )
            selected, used, more = [], 0, False
            model_ids: dict[tuple[str, str, str], str | None] = {}
            for row in rows:
                record = self._with_legacy_remote_model_id(self._record(row), model_ids)
                size = len(json.dumps(self.view(record), ensure_ascii=False).encode())
                if len(selected) >= limit or used + size > byte_budget:
                    more = True
                    break
                selected.append(record)
                used += size
            selected.reverse()
            return HistoryPage(
                records=tuple(selected),
                previous_item_id=selected[0].item.id if more and selected else None,
            )

    @staticmethod
    def view(record: HistoryRecord) -> dict[str, object]:
        item = project_tool_text(record.item.to_json_value())
        if isinstance(item.get("content"), str):
            item["content"] = _truncate_snapshot_text(item["content"])
        tool = item.get("toolCall")
        if isinstance(tool, dict):
            arguments = _snapshot_display_arguments(tool)
            if arguments is None:
                tool.pop("argumentsJson", None)
            else:
                tool["argumentsJson"] = arguments
            if isinstance(tool.get("resultJson"), str):
                tool["resultJson"] = _truncate_snapshot_text(tool["resultJson"])
        return {
            "item": item,
            "run": record.run.to_json_value(),
            "origin": record.origin.to_wire_dict(),
        }

    def with_history(self, facts: ContextFacts, *, newest: bool) -> ContextFacts:
        from eidos_runtime.db.repositories.context import (
            CONTEXT_PROJECTION_HARD_MAX_BYTES,
            CONTEXT_PROJECTION_HARD_MAX_ITEMS,
            CONTEXT_PROJECTION_MAX_BYTES,
            CONTEXT_PROJECTION_MAX_ITEMS,
        )

        if self.source(facts.session_id) is None:
            return facts
        with self.lock:
            excluded = set(
                facts.compact_summary.source_item_ids if facts.compact_summary else ()
            )
            byte_limit = CONTEXT_PROJECTION_HARD_MAX_BYTES if newest else CONTEXT_PROJECTION_MAX_BYTES
            item_limit = CONTEXT_PROJECTION_HARD_MAX_ITEMS if newest else CONTEXT_PROJECTION_MAX_ITEMS
            native_size = 2 + sum(
                len(fact.model_dump_json(exclude_none=True).encode("utf-8")) + 1
                for fact in facts.items
            )
            remaining = max(0, byte_limit - native_size)
            capacity = max(0, item_limit - len(facts.items))
            rows = self._connection().execute(
                "SELECT * FROM session_history_entries WHERE session_id=? AND context_eligible=1 ORDER BY sequence "
                + ("DESC" if newest else "ASC"),
                (facts.session_id,),
            )
            selected: list[ContextItemFact] = []
            total, total_bytes, used = 0, 0, 0
            group: list[sqlite3.Row] = []
            group_key = None

            def consume() -> None:
                nonlocal used
                if len(selected) >= capacity or used >= remaining:
                    return
                ordered = list(reversed(group)) if newest else group
                group_facts: list[ContextItemFact] = []
                seen: set[str | None] = set()
                size = 0
                for row in ordered:
                    record = self._record(row)
                    if not record.context_eligible:
                        continue
                    fact = record.fact(
                        self.database,
                        replay=newest and record.model_response_id not in seen,
                    )
                    seen.add(record.model_response_id)
                    if not newest:
                        fact = fact.model_copy(
                            update={
                                "content": (fact.content or "")[:8192],
                                "arguments_json": fact.arguments_json[:8192]
                                if fact.arguments_json
                                else None,
                                "model_result_json": fact.model_result_json[:8192]
                                if fact.model_result_json
                                else None,
                                "result_json": fact.result_json[:8192]
                                if fact.result_json
                                else None,
                            }
                        )
                    size += len(fact.model_dump_json(exclude_none=True).encode("utf-8")) + 1
                    group_facts.append(fact)
                if (
                    len(selected) + len(group_facts) > capacity
                    or used + size > remaining
                ):
                    return
                if newest:
                    selected[0:0] = group_facts
                else:
                    selected.extend(group_facts)
                used += size

            for row in rows:
                if row["item_id"] in excluded:
                    continue
                total += 1
                total_bytes += row["byte_size"]
                key = (row["run_id"], row["model_step_index"])
                if key != group_key:
                    consume()
                    group, group_key = [], key
                group.append(row)
            consume()
            return facts.model_copy(
                update={
                    "items": (*selected, *facts.items),
                    "candidate_overflow": facts.candidate_overflow
                    or total > len(selected),
                    "projection_candidate_count": facts.projection_candidate_count
                    + total,
                    "projection_candidate_bytes": facts.projection_candidate_bytes
                    + total_bytes,
                    "projection_omitted_count": facts.projection_omitted_count
                    + total
                    - len(selected),
                    "projection_omitted_bytes": facts.projection_omitted_bytes
                    + max(0, total_bytes - used),
                }
            )

    def messages(
        self, session_id: str, item_ids: list[str], *, limit: int = 20
    ) -> tuple[ItemDto, ...]:
        with self.lock:
            selected: list[ItemDto] = []
            requested = set(item_ids)
            for row in self._connection().execute(
                "SELECT * FROM session_history_entries WHERE session_id=? ORDER BY sequence DESC",
                (session_id,),
            ):
                if requested and row["item_id"] not in requested:
                    continue
                item = self._record(row).item
                if item.kind in {"user_message", "assistant_message"} and item.content is not None:
                    selected.append(item)
                    if len(selected) >= limit:
                        break
            return tuple(reversed(selected))

    def verification_records(
        self, session_id: str, item_ids: set[str]
    ) -> tuple[HistoryRecord, ...]:
        with self.lock:
            result: list[HistoryRecord] = []
            for row in self._connection().execute(
                "SELECT * FROM session_history_entries WHERE session_id=? AND context_eligible=1 ORDER BY sequence",
                (session_id,),
            ):
                if row["item_id"] in item_ids:
                    result.append(self._record(row))
            return tuple(result)

    def tool_record(self, session_id: str, tool_call_id: str) -> HistoryRecord | None:
        with self.lock:
            row = (
                self._connection()
                .execute(
                    "SELECT blob_ref AS record_json FROM session_history_resources WHERE session_id=? AND resource_id=?",
                    (session_id, "history-tool:" + tool_call_id),
                )
                .fetchone()
            )
            return self._record(row) if row else None


class _HistoryCaptureReader(SessionHistoryRepository):
    def __init__(self, database: Database, connection: sqlite3.Connection) -> None:
        super().__init__(database)
        self._read_view = connection

    def _connection(self) -> sqlite3.Connection:
        return self._read_view


def _replayed_session(value: object) -> str:
    if not isinstance(value, dict) or not isinstance(value.get("sessionId"), str):
        raise InvalidRunStateError("invalid fork operation result")
    return value["sessionId"]


class HistoryCaptureCancelled(InvalidRunStateError):
    pass


class HistoryLimitExceeded(InvalidRunStateError):
    pass

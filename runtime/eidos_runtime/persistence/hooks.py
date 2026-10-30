from __future__ import annotations

import uuid
import json
import sqlite3
from pydantic import JsonValue

from eidos_runtime.db.database import Repository, now_ms
from eidos_runtime.db.errors import ResourceNotFoundError
from eidos_runtime.db.events import append_event
from eidos_runtime.db.mappers import _plugin_from_row
from eidos_runtime.domain.hooks import (
    HookActivity, HookActivityPage, HookBinding, HookOutput, HookPendingEvent,
    HookResult, HookSourceReview, HookTranscriptItem,
)
from eidos_runtime.runtime.state_machine import EventType


class HookRepository(Repository):
    def child_sessions(self, session_id: str) -> tuple[str, ...]:
        with self.lock:
            return tuple(row[0] for row in self._connection().execute(
                "SELECT child_session_id FROM agent_delegations WHERE parent_run_id IN (SELECT id FROM runs WHERE session_id=?)",
                (session_id,),
            ))

    def session_start_source(self, run_id: str) -> str:
        with self.lock:
            run = self._connection().execute("SELECT session_id FROM runs WHERE id=?", (run_id,)).fetchone()
            fork = self._connection().execute("SELECT 1 FROM session_forks WHERE session_id=?", (run["session_id"],)).fetchone()
            previous = self._connection().execute("SELECT 1 FROM runs WHERE session_id=? AND id!=? AND started_at IS NOT NULL LIMIT 1", (run["session_id"], run_id)).fetchone()
        return "resume" if previous else "fork" if fork else "startup"

    def allowed(self, binding: HookBinding) -> bool:
        with self.lock:
            return self._connection().execute(
                "SELECT 1 FROM plugins p JOIN plugin_hook_trust t ON t.plugin_id=p.id AND t.content_hash=p.content_hash WHERE p.id=? AND p.content_hash=? AND p.enabled=1 AND p.status='installed'"
                if binding.source_kind == "plugin" else
                "SELECT 1 FROM hook_sources WHERE id=? AND content_hash=? AND trusted_hash=content_hash AND enabled=1 AND error_code IS NULL",
                (binding.plugin_id, binding.plugin_hash),
            ).fetchone() is not None

    def trusted(self, plugin_id: str, content_hash: str) -> bool:
        with self.lock:
            return self._connection().execute(
                "SELECT 1 FROM plugin_hook_trust WHERE plugin_id=? AND content_hash=?",
                (plugin_id, content_hash),
            ).fetchone() is not None

    def set_trust(self, plugin_id: str, content_hash: str, trusted: bool) -> None:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT * FROM plugins WHERE id=? AND status='installed' AND content_hash=?", (plugin_id, content_hash)).fetchone()
            if row is None:
                raise ResourceNotFoundError("plugin version changed or is unavailable")
            if trusted:
                connection.execute("INSERT INTO plugin_hook_trust VALUES(?,?,?) ON CONFLICT(plugin_id) DO UPDATE SET content_hash=excluded.content_hash, trusted_at=excluded.trusted_at", (plugin_id, content_hash, now_ms()))
            else:
                connection.execute("DELETE FROM plugin_hook_trust WHERE plugin_id=?", (plugin_id,))
            append_event(connection, EventType.PLUGIN_STATE_CHANGED, now_ms(), {"plugin": _plugin_from_row(row)})

    def reserve(self, run_id: str, binding: HookBinding, event_key: str, input_json: str) -> tuple[str, HookResult | None, bool]:
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT id,status,result_json FROM hook_invocations WHERE run_id=? AND plugin_id=? AND hook_key=? AND event_key=?",
                (run_id, binding.plugin_id, binding.definition.key, event_key),
            ).fetchone()
            if row is not None:
                result = HookResult.model_validate_json(row["result_json"]) if row["result_json"] else HookResult(status="unknown", error_code="hook_outcome_unknown")
                return row["id"], result, False
            # Trust revocation and enablement are checked in the same transaction as Intent.
            allowed = connection.execute(
                "SELECT 1 FROM plugins p JOIN plugin_hook_trust t ON t.plugin_id=p.id AND t.content_hash=p.content_hash WHERE p.id=? AND p.content_hash=? AND p.enabled=1 AND p.status='installed'"
                if binding.source_kind == "plugin" else
                "SELECT 1 FROM hook_sources WHERE id=? AND content_hash=? AND trusted_hash=content_hash AND enabled=1 AND error_code IS NULL",
                (binding.plugin_id, binding.plugin_hash),
            ).fetchone()
            if allowed is None:
                return "", HookResult(status="skipped", error_code="hook_not_trusted"), False
            invocation_id = str(uuid.uuid4())
            connection.execute(
                "INSERT INTO hook_invocations(id,run_id,plugin_id,plugin_hash,hook_key,event,event_key,status,input_json,created_at,binding_json,background) VALUES(?,?,?,?,?,?,?,'intent',?,?,?,?)",
                (invocation_id, run_id, binding.plugin_id, binding.plugin_hash, binding.definition.key, binding.definition.event, event_key, input_json, now_ms(), binding.model_dump_json(by_alias=True), int(binding.definition.background)),
            )
            return invocation_id, None, True

    def started(self, invocation_id: str, permissions_json: str) -> None:
        with self.database.transaction() as connection:
            updated = connection.execute("UPDATE hook_invocations SET status='running', permissions_json=? WHERE id=? AND status='intent'", (permissions_json, invocation_id))
            if updated.rowcount:
                row = connection.execute("SELECT h.run_id,r.session_id FROM hook_invocations h JOIN runs r ON r.id=h.run_id WHERE h.id=?", (invocation_id,)).fetchone()
                append_event(connection, EventType.RUN_UPDATED, now_ms(), {"reason": "hook_started"}, run_id=row["run_id"], session_id=row["session_id"])

    def complete(self, invocation_id: str, result: HookResult, output: HookOutput | None = None) -> None:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT run_id FROM hook_invocations WHERE id=?", (invocation_id,)).fetchone()
            if row is None:
                raise ResourceNotFoundError("hook invocation not found")
            updated = connection.execute("UPDATE hook_invocations SET status=?, result_json=?, output_json=?, completed_at=? WHERE id=? AND status IN ('intent','running')", ("unknown" if result.status == "unknown" else "completed", result.model_dump_json(by_alias=True), output.model_dump_json(by_alias=True) if output else None, now_ms(), invocation_id))
            if updated.rowcount != 1:
                return
            if result.workspace_changed or not result.workspace_observation_complete:
                connection.execute("UPDATE runs SET workspace_version=workspace_version+1,updated_at=? WHERE id=?", (now_ms(), row["run_id"]))
            run = connection.execute("SELECT session_id FROM runs WHERE id=?", (row["run_id"],)).fetchone()
            event = append_event(connection, EventType.RUN_UPDATED, now_ms(), {"reason": "hook_result"}, run_id=row["run_id"], session_id=run["session_id"])
            connection.execute("UPDATE hook_invocations SET completion_event_id=? WHERE id=?", (event["eventId"], invocation_id))
            plugin = connection.execute("SELECT * FROM plugins WHERE id=(SELECT plugin_id FROM hook_invocations WHERE id=?)", (invocation_id,)).fetchone()
            if plugin is not None:
                append_event(connection, EventType.PLUGIN_STATE_CHANGED, now_ms(), {"plugin": _plugin_from_row(plugin)})

    def latest(self, plugin_id: str) -> HookResult | None:
        with self.lock:
            row = self._connection().execute("SELECT result_json FROM hook_invocations WHERE plugin_id=? AND result_json IS NOT NULL ORDER BY created_at DESC, rowid DESC LIMIT 1", (plugin_id,)).fetchone()
        return HookResult.model_validate_json(row["result_json"]) if row else None

    def activities(self, *, source_id: str | None = None, session_id: str | None = None,
                   run_id: str | None = None, before: int | None = None,
                   limit: int = 20, previews: bool = True) -> HookActivityPage:
        clauses, arguments = [], []
        for column, value in (("h.plugin_id", source_id), ("r.session_id", session_id), ("h.run_id", run_id), ("h.rowid <", before)):
            if value is not None:
                clauses.append(f"{column} ?" if column.endswith("<") else f"{column} = ?")
                arguments.append(value)
        limit = min(20, max(1, limit))
        with self.lock:
            rows = self._connection().execute(
                "SELECT h.rowid AS cursor,h.id,h.run_id,h.plugin_id,h.event,h.status,h.background,h.created_at,h.completed_at,r.session_id,"
                "json_extract(h.binding_json,'$.definition.handlerType') AS handler_type,"
                "json_extract(h.binding_json,'$.definition.statusMessage') AS status_message,"
                "json_remove(h.result_json,'$.additionalContext','$.updatedInput','$.workspaceChanges') AS result_json,"
                + ("h.output_json " if previews else "NULL AS output_json ")
                + "FROM hook_invocations h JOIN runs r ON r.id=h.run_id "
                + ("WHERE " + " AND ".join(clauses) if clauses else "")
                + " ORDER BY h.rowid DESC LIMIT ?", (*arguments, limit + 1),
            ).fetchall()
        items = []
        for row in rows[:limit]:
            result = HookResult.model_validate_json(row["result_json"]) if row["result_json"] else None
            output = HookOutput.model_validate_json(row["output_json"]) if previews and row["output_json"] else None
            items.append(HookActivity(id=row["id"], run_id=row["run_id"], session_id=row["session_id"],
                source_id=row["plugin_id"], event=row["event"], handler_type=row["handler_type"] or "command",
                background=bool(row["background"]), status=result.status if result else row["status"],
                status_message=row["status_message"] or "",
                created_at=row["created_at"], completed_at=row["completed_at"], result=result,
                stdout_preview=output.stdout[:4096] if output else "", stderr_preview=output.stderr[:4096] if output else "",
                preview_truncated=bool(output and (output.raw_truncated or len(output.stdout) > 4096 or len(output.stderr) > 4096))))
        return HookActivityPage(items=tuple(items), next_cursor=rows[limit - 1]["cursor"] if len(rows) > limit else None)

    def save_source(self, source: HookSourceReview) -> HookSourceReview:
        with self.database.transaction() as connection:
            previous = connection.execute("SELECT content_hash,error_code FROM hook_sources WHERE id=?", (source.id,)).fetchone()
            connection.execute("""INSERT INTO hook_sources
                (id,scope,paths_json,content_hash,definitions_json,enabled,error_code,updated_at)
                VALUES(?,?,?,?,?,1,?,?) ON CONFLICT(id) DO UPDATE SET
                paths_json=excluded.paths_json,content_hash=excluded.content_hash,
                definitions_json=excluded.definitions_json,error_code=excluded.error_code,
                trusted_hash=CASE WHEN hook_sources.content_hash=excluded.content_hash THEN hook_sources.trusted_hash ELSE NULL END,
                updated_at=excluded.updated_at""",
                (source.id, source.scope, json.dumps(source.paths), source.content_hash,
                 json.dumps([definition.to_wire_dict() for definition in source.definitions]), source.error_code, now_ms()))
            row = connection.execute("SELECT enabled,trusted_hash FROM hook_sources WHERE id=?", (source.id,)).fetchone()
            if previous is None or previous["content_hash"] != source.content_hash or previous["error_code"] != source.error_code:
                append_event(connection, EventType.HOOK_SOURCES_CHANGED, now_ms(), {"id": source.id})
        return source.model_copy(update={"enabled": bool(row["enabled"]), "trusted": row["trusted_hash"] == source.content_hash and source.error_code is None})

    def source(self, source_id: str) -> HookSourceReview:
        with self.lock:
            row = self._connection().execute("SELECT * FROM hook_sources WHERE id=?", (source_id,)).fetchone()
        if row is None:
            raise ResourceNotFoundError("hook source not found")
        from eidos_runtime.domain.hooks import HookDefinition
        return HookSourceReview(id=row["id"], scope=row["scope"], paths=tuple(json.loads(row["paths_json"])),
            content_hash=row["content_hash"], definitions=tuple(HookDefinition.model_validate(value) for value in json.loads(row["definitions_json"])),
            trusted=row["trusted_hash"] == row["content_hash"] and row["error_code"] is None,
            enabled=bool(row["enabled"]), error_code=row["error_code"])

    def set_source_state(self, source_id: str, content_hash: str, *, trusted: bool | None = None,
                         enabled: bool | None = None) -> HookSourceReview:
        with self.database.transaction() as connection:
            if trusted is not None:
                updated = connection.execute("UPDATE hook_sources SET trusted_hash=?,updated_at=? WHERE id=? AND content_hash=? AND (error_code IS NULL OR ?=0)",
                    (content_hash if trusted else None, now_ms(), source_id, content_hash, int(trusted)))
            else:
                updated = connection.execute("UPDATE hook_sources SET enabled=?,updated_at=? WHERE id=? AND content_hash=?",
                    (int(bool(enabled)), now_ms(), source_id, content_hash))
            if updated.rowcount != 1:
                raise ResourceNotFoundError("hook source changed or is unavailable")
            append_event(connection, EventType.HOOK_SOURCES_CHANGED, now_ms(), {"id": source_id})
        return self.source(source_id)

    def discard_background(self, session_id: str) -> None:
        with self.database.transaction() as connection:
            connection.execute("UPDATE hook_invocations SET discarded_at=? WHERE background=1 AND run_id IN (SELECT id FROM runs WHERE session_id=?) AND delivered_run_id IS NULL",
                (now_ms(), session_id))

    def transcript_items(self, session_id: str) -> tuple[HookTranscriptItem, ...]:
        with self.lock:
            rows = self._connection().execute("""SELECT i.id,i.kind,substr(coalesce(i.content,''),1,32768) AS content,
                i.created_at,t.tool_name,t.provider_call_id,substr(t.arguments_json,1,32768) AS arguments,
                substr(t.model_result_json,1,32768) AS output,
                (length(coalesce(i.content,''))>32768 OR length(coalesce(t.arguments_json,''))>32768
                OR length(coalesce(t.model_result_json,''))>32768) AS truncated
                FROM items i LEFT JOIN tool_calls t ON t.item_id=i.id WHERE i.session_id=?
                ORDER BY i.creation_seq DESC LIMIT 1025""", (session_id,)).fetchall()
        return tuple(HookTranscriptItem(id=row["id"], kind=row["kind"], content=row["content"],
            created_at=row["created_at"], tool_name=row["tool_name"], call_id=row["provider_call_id"],
            arguments=row["arguments"], output=row["output"], truncated=bool(row["truncated"])) for row in reversed(rows))

    def frontier(self, run_id: str) -> int:
        with self.lock:
            return self._connection().execute("SELECT coalesce(max(completion_event_id),0) FROM hook_invocations WHERE run_id=? AND background=0", (run_id,)).fetchone()[0]

    @staticmethod
    def record_pending(connection: sqlite3.Connection, run_id: str, event: HookPendingEvent) -> None:
        connection.execute("INSERT OR IGNORE INTO hook_pending_events VALUES(?,?,?)", (run_id, event.event_key, json.dumps(event.fields, ensure_ascii=False)))

    def pending(self, run_id: str) -> tuple[tuple[str, dict[str, JsonValue]], ...]:
        with self.lock:
            rows = self._connection().execute("SELECT event_key,fields_json FROM hook_pending_events WHERE run_id=? ORDER BY rowid LIMIT 64", (run_id,)).fetchall()
        return tuple((row["event_key"], json.loads(row["fields_json"])) for row in rows)

    def delivered(self, run_id: str, event_key: str) -> None:
        with self.database.transaction() as connection:
            connection.execute("DELETE FROM hook_pending_events WHERE run_id=? AND event_key=?", (run_id, event_key))

    def context(self, run_id: str) -> tuple[str, ...]:
        with self.lock:
            rows = self._connection().execute("""SELECT h.id,h.background,h.result_json FROM hook_invocations h
                WHERE h.result_json IS NOT NULL AND h.discarded_at IS NULL AND (
                    h.run_id=? OR (h.background=1 AND h.run_id IN (
                        SELECT id FROM runs WHERE session_id=(SELECT session_id FROM runs WHERE id=?))
                        AND (h.delivered_run_id IS NULL OR h.delivered_run_id=?)))
                ORDER BY h.created_at DESC,h.rowid DESC LIMIT 64""", (run_id, run_id, run_id)).fetchall()
        messages: list[str] = []
        received: list[str] = []
        remaining = 32768
        for row in rows:
            result = HookResult.model_validate_json(row["result_json"])
            text = result.additional_context
            if result.status in {"failed", "unknown"}:
                text += f"\nHook error: {result.error_code}. The check did not pass."
            elif result.action in {"continue", "stop", "deny"}:
                text += "\n" + result.message
            if result.output_call_id and (result.context_truncated or result.status in {"failed", "unknown"}):
                text += f"\nSaved Hook output: read_tool_output(callId='{result.output_call_id}', stream='stdout' or 'stderr'). Read by page; do not execute this Hook again."
            if text.strip() and remaining > 0:
                if len(text) > remaining and result.output_call_id:
                    pointer = f"Truncated Hook feedback. Read saved output with read_tool_output(callId='{result.output_call_id}', stream='result').\n"
                    if remaining <= len(pointer):
                        continue
                    text = pointer + text[:remaining - len(pointer)]
                else:
                    text = text[:remaining]
                messages.append(text)
                remaining -= len(text)
                if row["background"]:
                    received.append(row["id"])
        if received:
            with self.database.transaction() as connection:
                connection.executemany("UPDATE hook_invocations SET delivered_run_id=? WHERE id=? AND delivered_run_id IS NULL",
                    ((run_id, identifier) for identifier in received))
        return tuple(reversed(messages))

    def tool_feedback(self, run_id: str, call_id: str) -> str | None:
        with self.lock:
            rows = self._connection().execute(
                "SELECT result_json FROM hook_invocations WHERE run_id=? AND event='PostToolUse' "
                "AND (json_extract(input_json,'$.tool_use_id')=? OR "
                "json_extract(input_json,'$.tool_response.data.sessionId') IN ("
                "SELECT json_extract(t.arguments_json,'$.sessionId') FROM tool_calls t JOIN items i ON i.id=t.item_id "
                "WHERE i.run_id=? AND t.provider_call_id=? AND t.tool_name IN ('write_stdin','control_shell'))) "
                "AND result_json IS NOT NULL ORDER BY created_at,rowid LIMIT 128",
                (run_id, call_id, run_id, call_id),
            ).fetchall()
        messages = [result.message or "The tool output was withheld by a configured Hook." for row in rows
                    if (result := HookResult.model_validate_json(row["result_json"])).action == "feedback"]
        return "\n".join(messages)[:16384] if messages else None

    def stop_active(self, run_id: str) -> bool:
        with self.lock:
            rows = self._connection().execute("SELECT result_json FROM hook_invocations WHERE run_id=? AND event IN ('Stop','SubagentStop') AND result_json IS NOT NULL ORDER BY created_at DESC LIMIT 128", (run_id,)).fetchall()
        return any(HookResult.model_validate_json(row["result_json"]).action == "continue" for row in rows)

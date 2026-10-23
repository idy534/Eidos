from __future__ import annotations

import uuid
import json
import sqlite3
from pydantic import JsonValue

from eidos_runtime.db.database import Repository, now_ms
from eidos_runtime.db.errors import ResourceNotFoundError
from eidos_runtime.db.events import append_event
from eidos_runtime.db.mappers import _plugin_from_row
from eidos_runtime.domain.hooks import HookBinding, HookOutput, HookPendingEvent, HookResult
from eidos_runtime.runtime.state_machine import EventType


class HookRepository(Repository):
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
                "SELECT 1 FROM plugins p JOIN plugin_hook_trust t ON t.plugin_id=p.id AND t.content_hash=p.content_hash WHERE p.id=? AND p.content_hash=? AND p.enabled=1 AND p.status='installed'",
                (binding.plugin_id, binding.plugin_hash),
            ).fetchone()
            if allowed is None:
                return "", HookResult(status="skipped", error_code="hook_not_trusted"), False
            invocation_id = str(uuid.uuid4())
            connection.execute(
                "INSERT INTO hook_invocations(id,run_id,plugin_id,plugin_hash,hook_key,event,event_key,status,input_json,created_at) VALUES(?,?,?,?,?,?,?,'intent',?,?)",
                (invocation_id, run_id, binding.plugin_id, binding.plugin_hash, binding.definition.key, binding.definition.event, event_key, input_json, now_ms()),
            )
            return invocation_id, None, True

    def started(self, invocation_id: str, permissions_json: str) -> None:
        with self.database.transaction() as connection:
            connection.execute("UPDATE hook_invocations SET status='running', permissions_json=? WHERE id=? AND status='intent'", (permissions_json, invocation_id))

    def complete(self, invocation_id: str, result: HookResult, output: HookOutput | None = None) -> None:
        with self.database.transaction() as connection:
            row = connection.execute("SELECT run_id FROM hook_invocations WHERE id=?", (invocation_id,)).fetchone()
            if row is None:
                raise ResourceNotFoundError("hook invocation not found")
            updated = connection.execute("UPDATE hook_invocations SET status=?, result_json=?, output_json=?, completed_at=? WHERE id=? AND status IN ('intent','running')", ("unknown" if result.status == "unknown" else "completed", result.model_dump_json(by_alias=True), output.model_dump_json(by_alias=True) if output else None, now_ms(), invocation_id))
            if updated.rowcount != 1:
                return
            if result.workspace_changed:
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

    def frontier(self, run_id: str) -> int:
        with self.lock:
            return self._connection().execute("SELECT coalesce(max(completion_event_id),0) FROM hook_invocations WHERE run_id=?", (run_id,)).fetchone()[0]

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
            rows = self._connection().execute("SELECT result_json FROM hook_invocations WHERE run_id=? AND result_json IS NOT NULL ORDER BY created_at DESC,rowid DESC LIMIT 64", (run_id,)).fetchall()
        messages: list[str] = []
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
                text = text[:remaining]
                messages.append(text)
                remaining -= len(text)
        return tuple(reversed(messages))

    def tool_feedback(self, run_id: str, call_id: str) -> str | None:
        with self.lock:
            rows = self._connection().execute(
                "SELECT result_json FROM hook_invocations WHERE run_id=? AND event='PostToolUse' "
                "AND (json_extract(input_json,'$.tool_use_id')=? OR "
                "json_extract(input_json,'$.tool_response.data.sessionId') IN ("
                "SELECT json_extract(t.arguments_json,'$.sessionId') FROM tool_calls t JOIN items i ON i.id=t.item_id "
                "WHERE i.run_id=? AND t.provider_call_id=? AND t.tool_name='write_stdin')) "
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

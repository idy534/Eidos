"""Literal text search over saved history.

Messages use the existing trigram FTS5 message index. Tool calls are not in
that index: their arguments and results are scanned with SQLite ``instr`` over
the newest calls of the Session only, with each body read up to a fixed
prefix, so one query has a bounded cost. Memory payloads are never searched,
because their revocation must keep applying to saved history.

The project scope searches the user and assistant messages of the project's
other Sessions through the same index. It follows the memory reading mode of
the current scope, and excludes child-agent and channel Sessions. Channel and
child Runs cannot use it.
"""

from __future__ import annotations

from datetime import datetime
import json
import re
from typing import Literal

from eidos_runtime.db.database import Repository
from eidos_runtime.db.errors import ResourceNotFoundError
from eidos_runtime.domain.history_search import HistoryMatch, HistoryScopeError, HistorySearchResult
from eidos_runtime.persistence.search import SearchRepository, fold
from eidos_runtime.persistence.session_history import SessionHistoryRepository

TOOL_SCAN_CALLS = 2_000
TOOL_RESULT_SCAN_CHARS = 256 * 1024
TOOL_ARGUMENT_SCAN_CHARS = 16 * 1024
SNIPPET_BEFORE_CHARS = 80
SNIPPET_AFTER_CHARS = 220
# Their results repeat other saved history and would answer every query twice.
_REPEATING_TOOLS = ("search_history", "read_tool_output")
_MESSAGE_KINDS = {"user_message": "user", "assistant_message": "assistant"}


# One project identity for a Session, as the memory scopes resolve it.
_SESSION_JOIN = (
    "FROM sessions s LEFT JOIN worktrees w ON w.id=s.worktree_id "
    "LEFT JOIN projects p ON p.id=s.project_id OR (s.project_id IS NULL AND p.workspace_root=s.workspace_root)"
)
_SESSION_PROJECT = "COALESCE(s.project_id,w.project_id,p.id)"
_OTHER_SESSION = (
    "NOT EXISTS(SELECT 1 FROM agent_delegations a WHERE a.child_session_id=s.id) "
    "AND NOT EXISTS(SELECT 1 FROM channel_sessions c WHERE c.session_id=s.id)"
)


class HistorySearchRepository(Repository):
    def project_scope(self, run_id: str) -> tuple[str, str | None]:
        """The current Session and its project (None when projectless).

        Raises HistoryScopeError when this Run may not look at other Sessions.
        """
        with self.database.transaction() as connection:
            run = connection.execute("SELECT session_id FROM runs WHERE id=?", (run_id,)).fetchone()
            if run is None:
                raise ResourceNotFoundError("run not found")
            session_id = str(run["session_id"])
            if connection.execute(
                "SELECT 1 FROM channel_run_origins WHERE run_id=? UNION ALL "
                "SELECT 1 FROM agent_delegations WHERE child_session_id=?",
                (run_id, session_id),
            ).fetchone():
                raise HistoryScopeError("history_scope_unavailable")
            scope = self.database.memory.repository.scopes(connection, session_id, "current")[0]
            if scope.mode == "off":
                raise HistoryScopeError("history_scope_disabled")
            return session_id, scope.project_id

    def assert_readable(self, run_id: str, session_id: str) -> None:
        """Allow reading a message of another Session found by the project scope."""
        current, project_id = self.project_scope(run_id)
        if session_id == current:
            return
        with self.lock:
            row = self._connection().execute(
                f"SELECT {_SESSION_PROJECT} AS project_id {_SESSION_JOIN} WHERE s.id=? AND {_OTHER_SESSION}",
                (session_id,),
            ).fetchone()
        if row is None or row["project_id"] != project_id:
            raise HistoryScopeError("history_scope_unavailable")

    def search(
        self,
        run_id: str,
        query: str,
        *,
        limit: int,
        scope: Literal["session", "project"] = "session",
    ) -> HistorySearchResult:
        folded = fold(query)
        if not folded or limit < 1:
            raise ValueError("invalid history query")
        if scope == "project":
            return self._search_project(run_id, folded, query.lower(), limit=limit)
        with self.lock:
            run = self._connection().execute("SELECT session_id FROM runs WHERE id=?", (run_id,)).fetchone()
        if run is None:
            raise ResourceNotFoundError("run not found")
        session_id = str(run["session_id"])
        indexing = SearchRepository(self.database).project_batch(session_id)
        candidate = "d.id IN(SELECT rowid FROM search_fts WHERE search_fts MATCH ?) AND " if len(folded) >= 3 else ""
        fts = ['"' + folded.replace('"', '""') + '"'] if len(folded) >= 3 else []
        lowered = query.lower()
        with self.lock:
            connection = self._connection()
            messages = connection.execute(
                f"""
                SELECT d.item_id, d.source_kind, d.position, d.body, i.kind AS item_kind
                FROM search_documents d LEFT JOIN items i ON i.id=d.item_id
                WHERE {candidate}instr(d.folded, ?) > 0 AND d.session_id=?
                ORDER BY CASE d.source_kind WHEN 'item' THEN 0 ELSE 1 END, d.position DESC
                LIMIT ?
                """,
                (*fts, folded, session_id, limit + 1),
            ).fetchall()
            scanned = connection.execute(
                "SELECT COUNT(*) FROM tool_calls t JOIN items i ON i.id=t.item_id WHERE i.session_id=?",
                (session_id,),
            ).fetchone()[0]
            tools = connection.execute(
                """
                SELECT i.id AS item_id, i.creation_seq AS position, t.provider_call_id, t.tool_name,
                       substr(COALESCE(t.result_json, t.model_result_json, ''), 1, ?) AS body,
                       substr(COALESCE(t.arguments_json, ''), 1, ?) AS arguments
                FROM (
                    SELECT t.id FROM tool_calls t JOIN items i ON i.id=t.item_id
                    WHERE i.session_id=? ORDER BY t.creation_seq DESC LIMIT ?
                ) recent
                JOIN tool_calls t ON t.id=recent.id JOIN items i ON i.id=t.item_id
                WHERE t.status IN ('completed', 'failed', 'canceled')
                  AND i.status IN ('completed', 'failed', 'canceled')
                  AND substr(t.tool_name, 1, 7) <> 'memory_'
                  AND t.tool_name NOT IN (?, ?)
                  AND (instr(lower(substr(COALESCE(t.result_json, t.model_result_json, ''), 1, ?)), ?) > 0
                       OR instr(lower(substr(COALESCE(t.arguments_json, ''), 1, ?)), ?) > 0)
                ORDER BY i.creation_seq DESC
                LIMIT ?
                """,
                (TOOL_RESULT_SCAN_CHARS, TOOL_ARGUMENT_SCAN_CHARS, session_id, TOOL_SCAN_CALLS,
                 *_REPEATING_TOOLS, TOOL_RESULT_SCAN_CHARS, lowered, TOOL_ARGUMENT_SCAN_CHARS, lowered,
                 limit + 1),
            ).fetchall()
        found: list[tuple[tuple[int, int], HistoryMatch]] = []
        history = SessionHistoryRepository(self.database)
        for row in messages:
            kind = row["item_kind"]
            if row["source_kind"] == "history":
                record = history.item_record(session_id, row["item_id"])
                kind = record.item.kind if record is not None else None
            if kind not in _MESSAGE_KINDS:
                continue
            found.append(((0 if row["source_kind"] == "item" else 1, -int(row["position"])), HistoryMatch(
                item_id=row["item_id"], kind=_MESSAGE_KINDS[kind], snippet=_snippet(row["body"], lowered),
            )))
        for row in tools:
            text = row["body"] if lowered in row["body"].lower() else row["arguments"]
            found.append(((0, -int(row["position"])), HistoryMatch(
                item_id=row["item_id"], kind="tool", snippet=_snippet(text, lowered),
                call_id=row["provider_call_id"], tool_name=row["tool_name"],
            )))
        found.sort(key=lambda value: value[0])
        more = len(messages) > limit or len(tools) > limit or len(found) > limit
        return HistorySearchResult(
            matches=tuple(match for _, match in found[:limit]),
            complete=not (more or indexing or scanned > TOOL_SCAN_CALLS),
        )


    def _search_project(
        self, run_id: str, folded: str, lowered: str, *, limit: int
    ) -> HistorySearchResult:
        current, project_id = self.project_scope(run_id)
        indexing = SearchRepository(self.database).project_batch()
        candidate = "d.id IN(SELECT rowid FROM search_fts WHERE search_fts MATCH ?) AND " if len(folded) >= 3 else ""
        fts = ['"' + folded.replace('"', '""') + '"'] if len(folded) >= 3 else []
        with self.lock:
            rows = self._connection().execute(
                f"""
                SELECT d.session_id, d.item_id, d.source_kind, d.position, d.body, i.kind AS item_kind,
                       COALESCE(s.title, '') AS title, COALESCE(i.created_at, s.updated_at) AS stamp
                {_SESSION_JOIN}
                JOIN search_documents d ON d.session_id=s.id
                LEFT JOIN items i ON i.id=d.item_id AND d.source_kind='item'
                WHERE {candidate}instr(d.folded, ?) > 0 AND s.id<>? AND {_OTHER_SESSION}
                  AND {_SESSION_PROJECT} IS ?
                ORDER BY stamp DESC, d.position DESC
                LIMIT ?
                """,
                (*fts, folded, current, project_id, limit + 1),
            ).fetchall()
        history = SessionHistoryRepository(self.database)
        matches = []
        for row in rows[:limit]:
            kind = row["item_kind"]
            if row["source_kind"] == "history":
                record = history.item_record(row["session_id"], row["item_id"])
                kind = record.item.kind if record is not None else None
            if kind not in _MESSAGE_KINDS:
                continue
            matches.append(HistoryMatch(
                item_id=row["item_id"], kind=_MESSAGE_KINDS[kind], snippet=_snippet(row["body"], lowered),
                session_id=row["session_id"], session_title=row["title"] or None,
                date=datetime.fromtimestamp(int(row["stamp"]) / 1000).date().isoformat() if row["stamp"] else None,
            ))
        return HistorySearchResult(matches=tuple(matches), complete=not (len(rows) > limit or indexing))


def _snippet(text: str, query: str) -> str:
    """Show the match in readable text: JSON string values rather than escapes."""
    readable = text
    try:
        value = json.loads(text)
    except ValueError:
        pass
    else:
        strings = "\n".join(_strings(value))
        if query in strings.lower():
            readable = strings
    readable = re.sub(r"\s+", " ", readable)
    position = max(0, readable.lower().find(query))
    start = max(0, position - SNIPPET_BEFORE_CHARS)
    end = position + len(query) + SNIPPET_AFTER_CHARS
    return ("…" if start else "") + readable[start:end] + ("…" if end < len(readable) else "")


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _strings(item)]
    if isinstance(value, list):
        return [text for item in value for text in _strings(item)]
    return []

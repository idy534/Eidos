"""Bounded text projection and literal retrieval over authoritative messages."""

from __future__ import annotations

import base64
import hashlib
import json
import re
import time

from markdown_it import MarkdownIt

from eidos_runtime.db.database import Repository
from eidos_runtime.db.errors import InvalidCursorError, ResourceNotFoundError
from eidos_runtime.protocol.search import (
    SearchHit,
    SearchProjectHit,
    SearchRequest,
    SearchResponse,
)
from eidos_runtime.persistence.session_history import SessionHistoryRepository

_PARSER = MarkdownIt("commonmark").enable("table")


def search_text(content: str) -> str:
    words: list[str] = []
    for token in _PARSER.parse(content):
        if token.type in {"fence", "code_block"}:
            words.append(token.content)
        elif token.type == "inline":
            words.append(
                "".join(
                    child.content
                    if child.type in {"text", "code_inline"}
                    else " "
                    if child.type in {"softbreak", "hardbreak"}
                    else ""
                    for child in token.children or []
                )
            )
    return re.sub(r"\s+", " ", " ".join(words)).strip()


def fold(value: str) -> str:
    # Per-codepoint lowercasing agrees with the UI, including expanded mappings.
    return "".join(c.lower() for c in re.sub(r"\s+", " ", value).strip())


class SearchRepository(Repository):
    def project_batch(self, session_id: str | None = None) -> bool:
        """No worker/thread per query. A durable queue resumes on the next request."""
        deadline = time.monotonic() + 0.08
        with self.lock:
            pending = (
                self._connection()
                .execute(
                    "SELECT * FROM search_pending WHERE (? IS NULL OR session_id=?) LIMIT 40",
                    (session_id, session_id),
                )
                .fetchall()
            )
        for job in pending:
            if time.monotonic() > deadline:
                break
            with self.lock:
                source = (
                    self._connection()
                    .execute(
                        "SELECT content AS value,creation_seq AS position FROM items WHERE session_id=? AND id=?"
                        if job["source_kind"] == "item"
                        else "SELECT record_json AS value,sequence AS position FROM session_history_entries WHERE session_id=? AND item_id=?",
                        (job["session_id"], job["item_id"]),
                    )
                    .fetchone()
                )
            value = source["value"] if source else None
            content = value
            if source and job["source_kind"] == "history":
                record = SessionHistoryRepository(self.database).item_record(
                    job["session_id"], job["item_id"]
                )
                content = (
                    record.item.content
                    if record
                    and record.item.kind in {"user_message", "assistant_message"}
                    else None
                )
            body = search_text(content) if content else ""
            with self.database.transaction() as connection:
                current = connection.execute(
                    "SELECT content FROM items WHERE session_id=? AND id=?"
                    if job["source_kind"] == "item"
                    else "SELECT record_json FROM session_history_entries WHERE session_id=? AND item_id=?",
                    (job["session_id"], job["item_id"]),
                ).fetchone()
                if (current[0] if current else None) != value:
                    continue
                connection.execute(
                    "DELETE FROM search_documents WHERE session_id=? AND item_id=?",
                    (job["session_id"], job["item_id"]),
                )
                if body and current:
                    connection.execute(
                        "INSERT INTO search_documents(session_id,item_id,source_kind,position,body,folded) VALUES(?,?,?,?,?,?)",
                        (
                            job["session_id"],
                            job["item_id"],
                            job["source_kind"],
                            source["position"],
                            body,
                            fold(body),
                        ),
                    )
                connection.execute(
                    "DELETE FROM search_pending WHERE session_id=? AND item_id=?",
                    (job["session_id"], job["item_id"]),
                )
        with self.lock:
            return (
                self._connection()
                .execute(
                    "SELECT 1 FROM search_pending WHERE (? IS NULL OR session_id=?) LIMIT 1",
                    (session_id, session_id),
                )
                .fetchone()
                is not None
            )

    def search(self, request: SearchRequest) -> SearchResponse:
        indexing = self.project_batch(request.session_id)
        with self.lock:
            revision = (
                self._connection()
                .execute("SELECT revision FROM search_index_state WHERE id=1")
                .fetchone()[0]
            )
        query = fold(request.query)
        fingerprint = hashlib.sha256(
            json.dumps(
                [query, request.session_id, request.project_id, request.projectless]
            ).encode()
        ).hexdigest()
        offset = 0
        occurrence_offset = 0
        reset = False
        if request.cursor:
            try:
                key, offset, occurrence_offset, previous_revision = json.loads(
                    base64.urlsafe_b64decode(request.cursor)
                )
                if (
                    key != fingerprint
                    or type(offset) is not int
                    or offset < 0
                    or offset > 10_000_000
                ):
                    raise ValueError
                if (
                    type(occurrence_offset) is not int
                    or not 0 <= occurrence_offset <= 10_000_000
                ):
                    raise ValueError
                if previous_revision != revision:
                    offset = occurrence_offset = 0
                    reset = True
            except (ValueError, TypeError, json.JSONDecodeError) as error:
                raise InvalidCursorError("invalid search cursor") from error
        conditions = [
            "NOT EXISTS(SELECT 1 FROM agent_delegations a WHERE a.child_session_id=s.id)"
        ]
        values: list[str | int] = []
        if request.session_id:
            conditions.append("s.id=?")
            values.append(request.session_id)
        if request.project_id:
            conditions.append("p.id=?")
            values.append(request.project_id)
        if request.projectless:
            conditions.append("p.id IS NULL")
        scope = " AND ".join(conditions)
        join = "FROM sessions s LEFT JOIN worktrees w ON w.id=s.worktree_id LEFT JOIN projects p ON p.workspace_root=s.workspace_root"
        # Trigram narrows the normal path. Short terms deliberately search the
        # full projection rather than silently limiting the visible history.
        candidate = (
            "d.id IN(SELECT rowid FROM search_fts WHERE search_fts MATCH ?) AND "
            if len(query) >= 3
            else ""
        )
        params: list[str | int] = (
            ['"' + query.replace('"', '""') + '"'] if len(query) >= 3 else []
        )
        params.append(query)
        with self.lock:
            connection = self._connection()
            connection.create_function(
                "eidos_search_fold",
                1,
                lambda text: fold(text or ""),
                deterministic=True,
            )
            if (
                request.session_id
                and connection.execute(
                    "SELECT 1 FROM sessions WHERE id=?", (request.session_id,)
                ).fetchone()
                is None
            ):
                raise ResourceNotFoundError("session not found")
            # Only IDs and a bounded excerpt cross the SQLite boundary.
            message = f"SELECT s.id AS session_id,COALESCE(s.title,'新会话') AS title,COALESCE(p.name,'无项目') AS project_name,d.item_id,d.id AS document_id,0 AS rank,d.source_kind,d.position,s.updated_at AS activity {join} JOIN search_documents d ON d.session_id=s.id WHERE {candidate}instr(d.folded,?)>0 AND {scope}"
            if request.session_id:
                sql = (
                    message
                    + " ORDER BY CASE source_kind WHEN 'history' THEN 0 ELSE 1 END,position LIMIT ? OFFSET ?"
                )
                rows = connection.execute(
                    sql, [*params, *values, request.limit + 1, offset]
                ).fetchall()
            else:
                titles = f"SELECT s.id AS session_id,COALESCE(s.title,'新会话') AS title,COALESCE(p.name,'无项目') AS project_name,NULL AS item_id,NULL AS document_id,CASE WHEN eidos_search_fold(s.title)=? THEN 3 WHEN instr(eidos_search_fold(s.title),?)=1 THEN 2 ELSE 1 END AS rank,'item' AS source_kind,0 AS position,s.updated_at AS activity {join} WHERE instr(eidos_search_fold(s.title),?)>0 AND {scope}"
                sql = f"WITH hits AS ({titles} UNION ALL {message}), grouped AS (SELECT *,row_number() OVER(PARTITION BY session_id ORDER BY rank DESC,CASE source_kind WHEN 'item' THEN 1 ELSE 0 END DESC,position DESC) AS n FROM hits) SELECT * FROM grouped WHERE n=1 ORDER BY rank DESC,activity DESC,session_id LIMIT ? OFFSET ?"
                rows = connection.execute(
                    sql,
                    [
                        query,
                        query,
                        query,
                        *values,
                        *params,
                        *values,
                        request.limit + 1,
                        offset,
                    ],
                ).fetchall()
        hits = []
        cursor = None
        for row_index, row in enumerate(rows[: request.limit]):
            if row["document_id"] is not None:
                with self.lock:
                    document = (
                        self._connection()
                        .execute(
                            "SELECT body FROM search_documents WHERE id=? AND session_id=?",
                            (row["document_id"], row["session_id"]),
                        )
                        .fetchone()
                    )
                if document is None:
                    continue
                body = document[0]
            else:
                body = row["title"]
            # Case conversion can change length, so snippet placement uses a map.
            mapping = (index for index, c in enumerate(body) for _ in c.lower())
            mapped_position = -1
            original = 0
            folded = fold(body)
            start = 0
            occurrence = 0
            while (start := folded.find(query, start)) >= 0:
                while mapped_position < start:
                    original = next(mapping, original)
                    mapped_position += 1
                if not request.session_id or occurrence >= (
                    occurrence_offset if row_index == 0 else 0
                ):
                    snippet = body[max(0, original - 60) : original + 180]
                    hits.append(
                        SearchHit(
                            session_id=row["session_id"],
                            title=row["title"],
                            project_name=row["project_name"],
                            item_id=row["item_id"],
                            snippet=snippet,
                            occurrence=occurrence,
                            match_kind="title" if row["rank"] else "message",
                        )
                    )
                occurrence += 1
                start += max(1, len(query))
                if len(hits) == request.limit:
                    if request.session_id and folded.find(query, start) >= 0:
                        cursor = base64.urlsafe_b64encode(
                            json.dumps(
                                [fingerprint, offset + row_index, occurrence, revision]
                            ).encode()
                        ).decode()
                    elif len(rows) > row_index + 1:
                        cursor = base64.urlsafe_b64encode(
                            json.dumps(
                                [fingerprint, offset + row_index + 1, 0, revision]
                            ).encode()
                        ).decode()
                    break
                if not request.session_id:
                    break
            if len(hits) == request.limit:
                break
        if not cursor and len(rows) > request.limit:
            cursor = base64.urlsafe_b64encode(
                json.dumps([fingerprint, offset + request.limit, 0, revision]).encode()
            ).decode()
        with self.lock:
            projects = (
                self._connection()
                .execute(
                    "SELECT id,name FROM projects WHERE instr(eidos_search_fold(name),?)>0 ORDER BY name LIMIT 10",
                    (query,),
                )
                .fetchall()
                if not request.session_id and not request.projectless
                else []
            )
        return SearchResponse(
            items=hits,
            next_cursor=cursor,
            indexing=indexing,
            reset=reset,
            projects=[
                SearchProjectHit(id=row["id"], name=row["name"]) for row in projects
            ],
        )

    def around_cursor(self, session_id: str, item_id: str) -> str | None:
        with self.lock:
            rows = (
                self._connection()
                .execute(
                    """
                WITH ordered AS (
                  SELECT item_id,0 AS kind,sequence AS pos FROM session_history_entries WHERE session_id=?
                  UNION ALL SELECT id,1,creation_seq FROM items WHERE session_id=?
                ), anchor AS(SELECT kind,pos FROM ordered WHERE item_id=?)
                SELECT item_id FROM ordered,anchor a WHERE ordered.kind>a.kind OR (ordered.kind=a.kind AND ordered.pos>a.pos)
                ORDER BY ordered.kind,ordered.pos LIMIT 1 OFFSET 15
            """,
                    (session_id, session_id, item_id),
                )
                .fetchone()
            )
            exists = (
                self._connection()
                .execute(
                    "SELECT 1 FROM items WHERE session_id=? AND id=? UNION ALL SELECT 1 FROM session_history_entries WHERE session_id=? AND item_id=?",
                    (session_id, item_id, session_id, item_id),
                )
                .fetchone()
            )
        if not exists:
            raise ResourceNotFoundError("item not found")
        return rows[0] if rows else None

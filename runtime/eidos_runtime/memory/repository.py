from __future__ import annotations

import logging
import sqlite3
import uuid

from rapidfuzz.fuzz import WRatio

from eidos_runtime.db.database import Database
from eidos_runtime.memory.contracts import GLOBAL_SCOPE_ID, MemoryDocument, MemoryEvidence, MemoryScope
from eidos_runtime.memory.publication import MemoryFiles

logger = logging.getLogger("eidos.runtime.memory")


class MemoryRejected(ValueError):
    """Stable content-free rejection for tools and typed IPC."""


class MemoryRepository:
    def __init__(self, database: Database) -> None:
        self.database = database
        if database.data_directory is None:
            raise RuntimeError("memory_storage_unavailable")
        self.files = MemoryFiles(database.data_directory)
        self.trigram_available = False
        with database.transaction() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO memory_scopes(id,kind,mode,enabled_after) VALUES(?,'global','auto',CAST((SELECT value FROM memory_metadata WHERE key='created_after') AS INTEGER))",
                (GLOBAL_SCOPE_ID,),
            )
            try:
                connection.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts_trigram USING fts5(document_id UNINDEXED,scope_id UNINDEXED,path,title,summary,body,tokenize='trigram')"
                )
                self.trigram_available = True
            except sqlite3.OperationalError:
                pass

    def root_session(self, connection: sqlite3.Connection, session_id: str) -> str:
        if (
            connection.execute(
                "SELECT 1 FROM sessions WHERE id=?", (session_id,)
            ).fetchone()
            is None
        ):
            raise MemoryRejected("memory_session_not_found")
        row = connection.execute(
            "SELECT r.session_id FROM agent_delegations a JOIN runs r ON r.id=a.parent_run_id WHERE a.child_session_id=?",
            (session_id,),
        ).fetchone()
        return row[0] if row else session_id

    def scopes(
        self,
        connection: sqlite3.Connection,
        session_id: str | None,
        selection: str = "allowed",
        *,
        project_id: str | None = None,
    ) -> list[MemoryScope]:
        if project_id is not None and session_id is not None:
            raise MemoryRejected("memory_target_ambiguous")
        if project_id is not None and connection.execute(
            "SELECT 1 FROM projects WHERE id=?", (project_id,)
        ).fetchone() is None:
            raise MemoryRejected("memory_project_not_found")
        ids = [GLOBAL_SCOPE_ID]
        if session_id:
            root = self.root_session(connection, session_id)
            project = connection.execute(
                "SELECT COALESCE(s.project_id,w.project_id,p.id) FROM sessions s LEFT JOIN worktrees w ON w.id=s.worktree_id LEFT JOIN projects p ON p.id=s.project_id OR (s.project_id IS NULL AND p.workspace_root=s.workspace_root) WHERE s.id=?",
                (root,),
            ).fetchone()
            project_id = project[0] if project else None
        if project_id is not None:
            row = connection.execute(
                "SELECT id FROM memory_scopes WHERE project_id=?", (project_id,)
            ).fetchone()
            identifier = row[0] if row else str(uuid.uuid4())
            if row is None:
                connection.execute(
                    "INSERT INTO memory_scopes(id,kind,project_id,mode,enabled_after) VALUES(?,'project',?,'auto',CAST((SELECT value FROM memory_metadata WHERE key='created_after') AS INTEGER))",
                    (identifier, project_id),
                )
            ids.append(identifier)
        if selection == "global":
            ids = ids[:1]
        elif selection == "current":
            ids = ids[-1:]
        return [self.scope(connection, identifier) for identifier in ids]

    @staticmethod
    def scope(connection: sqlite3.Connection, identifier: str) -> MemoryScope:
        row = connection.execute(
            "SELECT * FROM memory_scopes WHERE id=?", (identifier,)
        ).fetchone()
        if row is None:
            raise MemoryRejected("memory_scope_not_found")
        return MemoryScope(
            id=row["id"],
            kind=row["kind"],
            project_id=row["project_id"],
            mode=row["mode"],
            privacy_epoch=row["privacy_epoch"],
            generation=row["generation"],
        )

    def assert_writable(
        self, connection: sqlite3.Connection, session_id: str | None
    ) -> None:
        if session_id and self.root_session(connection, session_id) != session_id:
            raise MemoryRejected("memory_root_session_required")
        if (
            session_id
            and connection.execute(
                "SELECT 1 FROM runs r WHERE r.session_id=? AND (EXISTS(SELECT 1 FROM automation_run_origins a WHERE a.run_id=r.id) OR EXISTS(SELECT 1 FROM channel_run_origins c WHERE c.run_id=r.id)) AND r.status IN ('running','finalizing')",
                (session_id,),
            ).fetchone()
        ):
            raise MemoryRejected("memory_automated_input")

    def evidence(
        self,
        connection: sqlite3.Connection,
        session_id: str | None,
        item_ids: list[str],
        scope_id: str,
        *,
        automatic: bool,
    ) -> list[MemoryEvidence]:
        if not item_ids:
            return []
        if not session_id:
            return []
        values = []
        for item_id in dict.fromkeys(item_ids):
            row = connection.execute(
                "SELECT i.*,v.item_revision,v.eligible,s.revision AS source_revision,s.deleted FROM items i JOIN memory_source_items v ON v.item_id=i.id JOIN memory_sources s ON s.session_id=i.session_id WHERE i.id=? AND NOT EXISTS(SELECT 1 FROM memory_suppressions WHERE scope_id=? AND item_id=i.id)",
                (item_id, scope_id),
            ).fetchone()
            if (
                row is None
                or not row["eligible"]
                or row["deleted"]
                or row["status"] not in {"completed", "failed"}
                or row["incomplete"]
            ):
                continue
            if row["kind"] == "user_message":
                evidence_class = "explicit_user"
            else:
                tool = connection.execute(
                    "SELECT tool_name FROM tool_calls WHERE item_id=? AND status IN ('completed','failed')",
                    (item_id,),
                ).fetchone()
                if (
                    not tool
                    or tool["tool_name"].startswith("memory_")
                ):
                    continue
                evidence_class = "observed_verified"
            values.append(
                MemoryEvidence(
                    item_id=item_id,
                    session_id=row["session_id"],
                    run_id=row["run_id"],
                    item_revision=row["item_revision"],
                    source_revision=row["source_revision"],
                    evidence_class=evidence_class,
                )
            )
        return values

    @staticmethod
    def row(
        connection: sqlite3.Connection, scope_id: str, path: str
    ) -> sqlite3.Row | None:
        return connection.execute(
            "SELECT * FROM memory_documents WHERE scope_id=? AND path=? AND status='active'",
            (scope_id, path),
        ).fetchone()

    def document(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        revision: int | None = None,
    ) -> tuple[MemoryDocument, str]:
        revision = row["current_revision"] if revision is None else revision
        saved = connection.execute(
            "SELECT * FROM memory_revisions WHERE document_id=? AND revision=?",
            (row["id"], revision),
        ).fetchone()
        if row["status"] != "active" or not saved or not saved["file_ref"]:
            raise MemoryRejected("memory_document_unavailable")
        evidence = [
            MemoryEvidence(**dict(value))
            for value in connection.execute(
                "SELECT item_id,session_id,run_id,item_revision,source_revision,evidence_class FROM memory_evidence WHERE document_id=? AND revision=?",
                (row["id"], revision),
            )
        ]
        try:
            expected = saved["file_size"], saved["file_mtime_ns"]
            if self.files.signature(saved["file_ref"]) != expected:
                raise ValueError("memory_body_changed")
            body = self.files.read(saved["file_ref"])
            if self.files.signature(saved["file_ref"]) != expected:
                raise ValueError("memory_body_changed")
        except (OSError, ValueError, UnicodeError):
            logger.warning(
                "Memory body unavailable document_id=%s revision=%s",
                row["id"],
                revision,
            )
            raise MemoryRejected("memory_body_unavailable") from None
        return MemoryDocument(
            id=row["id"],
            scope_id=row["scope_id"],
            path=row["path"],
            revision=revision,
            title=saved["title"],
            summary=saved["summary"],
            kind=saved["kind"],
            user_owned=bool(saved["user_owned"]),
            archived=bool(row["archived"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            evidence=evidence,
        ), body

    def list(
        self,
        connection: sqlite3.Connection,
        scope_ids: list[str],
        query: str,
        cursor: int,
        limit: int,
        *, include_archived: bool = False,
    ) -> tuple[list[MemoryDocument], int | None, bool]:
        if not scope_ids:
            return [], None, False
        placeholders = ",".join("?" for _ in scope_ids)
        ids: list[str] | None = None
        limited = False
        visible = "document_id IN (SELECT id FROM memory_documents WHERE status='active' AND (? OR archived=0))"
        if query.strip():
            terms = " OR ".join(
                '"' + word.replace('"', '""') + '"' for word in query.split()
            )
            tables = ["memory_fts_word"] + (
                ["memory_fts_trigram"]
                if self.trigram_available and len(query) >= 3
                else []
            )
            ranked: dict[str, float] = {}
            for table in tables:
                rows = connection.execute(
                    f"SELECT document_id,bm25({table}) AS rank FROM {table} WHERE {table} MATCH ? AND scope_id IN ({placeholders}) AND {visible} ORDER BY rank LIMIT 500",
                    (terms, *scope_ids, include_archived),
                ).fetchall()
                limited |= len(rows) == 500
                for rank, value in enumerate(rows):
                    ranked[value["document_id"]] = ranked.get(
                        value["document_id"], 0
                    ) + 1 / (60 + rank)
            # A bounded LIKE fallback covers one/two-character Chinese and titles.
            escaped = (
                query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            )
            fallback = connection.execute(
                f"SELECT document_id,path,title FROM memory_fts_word WHERE scope_id IN ({placeholders}) AND {visible} AND (path LIKE ? ESCAPE '\\' OR title LIKE ? ESCAPE '\\' OR summary LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\') LIMIT 500",
                (*scope_ids, include_archived, *([f"%{escaped}%"] * 4)),
            ).fetchall()
            limited |= len(fallback) == 500
            for value in fallback:
                ranked[value["document_id"]] = (
                    ranked.get(value["document_id"], 0)
                    + 0.05
                    + WRatio(query, value["title"]) / 10000
                )
            ids = sorted(ranked, key=lambda key: (-ranked[key], key))
            selected = ids[cursor : cursor + limit + 1]
            rows = [
                connection.execute(
                    "SELECT * FROM memory_documents WHERE id=? AND status='active'",
                    (identifier,),
                ).fetchone()
                for identifier in selected
            ]
        else:
            rows = connection.execute(
                f"SELECT * FROM memory_documents WHERE status='active' AND (? OR archived=0) AND scope_id IN ({placeholders}) ORDER BY path,id LIMIT ? OFFSET ?",
                (include_archived, *scope_ids, limit + 1, cursor),
            ).fetchall()
        documents = []
        for row in rows[:limit]:
            if row is None:
                continue
            try:
                documents.append(self.document(connection, row)[0])
            except MemoryRejected:
                continue
        more = len(rows) > limit or (ids is not None and cursor + limit < len(ids))
        return documents, cursor + limit if more else None, limited

    def index(
        self, connection: sqlite3.Connection, document: MemoryDocument, body: str
    ) -> None:
        for table in ["memory_fts_word"] + (
            ["memory_fts_trigram"] if self.trigram_available else []
        ):
            connection.execute(
                f"DELETE FROM {table} WHERE document_id=?", (document.id,)
            )
            connection.execute(
                f"INSERT INTO {table}(document_id,scope_id,path,title,summary,body) VALUES(?,?,?,?,?,?)",
                (
                    document.id,
                    document.scope_id,
                    document.path,
                    document.title,
                    document.summary,
                    body,
                ),
            )

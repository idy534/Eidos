from __future__ import annotations

import json
import logging
import re
import sqlite3
import uuid

from rapidfuzz.fuzz import WRatio

from eidos_runtime.db.database import Database, now_ms
from eidos_runtime.memory.contracts import (
    MemoryActionResult,
    MemoryEntry,
    MemoryEvidence,
    MemoryJob,
    MemoryManageRequest,
    MemoryReadRequest,
    MemoryRecord,
    MemoryScope,
    MemorySettings,
    MemoryWriteRequest,
)
from eidos_runtime.memory.publication import MemoryFiles


logger = logging.getLogger("eidos.runtime.memory")
GLOBAL_SCOPE_ID = "00000000-0000-4000-8000-000000000001"


class MemoryRejected(ValueError):
    """Stable, content-free error suitable for tool/RPC mapping."""


class MemoryRepository:
    def __init__(self, database: Database) -> None:
        self.database = database
        if database.data_directory is None:
            raise RuntimeError("memory_storage_unavailable")
        self.files = MemoryFiles(database.data_directory)
        with database.transaction() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO memory_scopes(id,kind,settings_json) VALUES(?,?,?)",
                (GLOBAL_SCOPE_ID, "global", MemorySettings().model_dump_json()),
            )
            try:
                connection.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts_trigram USING fts5(entry_id UNINDEXED,scope_id UNINDEXED,title,aliases,body,tokenize='trigram')"
                )
                self.trigram_available = True
            except sqlite3.OperationalError:
                self.trigram_available = False

    def root_session(self, connection: sqlite3.Connection, session_id: str) -> str:
        row = connection.execute(
            "SELECT id FROM sessions WHERE id=?", (session_id,)
        ).fetchone()
        if row is None:
            raise MemoryRejected("memory_session_not_found")
        child = connection.execute(
            "SELECT r.session_id FROM agent_delegations a JOIN runs r ON r.id=a.parent_run_id WHERE a.child_session_id=?",
            (session_id,),
        ).fetchone()
        return child[0] if child else session_id

    def scopes(
        self,
        connection: sqlite3.Connection,
        session_id: str | None,
        selection: str = "allowed",
    ) -> list[MemoryScope]:
        scope_ids = [GLOBAL_SCOPE_ID]
        if session_id is not None:
            session_id = self.root_session(connection, session_id)
            project = connection.execute(
                "SELECT COALESCE(w.project_id,p.id) FROM sessions s "
                "LEFT JOIN worktrees w ON w.id=s.worktree_id "
                "LEFT JOIN projects p ON p.workspace_root=s.workspace_root WHERE s.id=?",
                (session_id,),
            ).fetchone()
            if project and project[0]:
                row = connection.execute(
                    "SELECT id FROM memory_scopes WHERE project_id=?", (project[0],)
                ).fetchone()
                if row is None:
                    identifier = str(uuid.uuid4())
                    connection.execute(
                        "INSERT INTO memory_scopes(id,kind,project_id,settings_json) VALUES(?,?,?,?)",
                        (
                            identifier,
                            "project",
                            project[0],
                            MemorySettings().model_dump_json(),
                        ),
                    )
                else:
                    identifier = row[0]
                scope_ids.append(identifier)
        if selection == "global":
            scope_ids = scope_ids[:1]
        elif selection == "current":
            scope_ids = scope_ids[-1:]
        return [self.scope(connection, identifier) for identifier in scope_ids]

    def scope(self, connection: sqlite3.Connection, scope_id: str) -> MemoryScope:
        row = connection.execute(
            "SELECT * FROM memory_scopes WHERE id=?", (scope_id,)
        ).fetchone()
        if row is None:
            raise MemoryRejected("memory_scope_not_found")
        return MemoryScope(
            id=row["id"],
            kind=row["kind"],
            project_id=row["project_id"],
            settings=MemorySettings.model_validate_json(row["settings_json"]),
            privacy_epoch=row["privacy_epoch"],
            generation=row["generation"],
        )

    def temporary(self, connection: sqlite3.Connection, session_id: str | None) -> bool:
        if session_id is None:
            return False
        row = connection.execute(
            "SELECT temporary FROM memory_sources WHERE session_id=?",
            (self.root_session(connection, session_id),),
        ).fetchone()
        return bool(row and row[0])

    def assert_writable(
        self, connection: sqlite3.Connection, session_id: str | None
    ) -> None:
        if (
            session_id is not None
            and self.root_session(connection, session_id) != session_id
        ):
            raise MemoryRejected("memory_root_session_required")
        if self.temporary(connection, session_id):
            raise MemoryRejected("memory_temporary_session")

    def evidence(
        self,
        connection: sqlite3.Connection,
        session_id: str | None,
        item_ids: list[str],
        scope_id: str,
        evidence_class: str,
    ) -> list[MemoryEvidence]:
        if not item_ids:
            return []
        if session_id is None:
            raise MemoryRejected("memory_evidence_session_required")
        values = []
        for item_id in dict.fromkeys(item_ids):
            row = connection.execute(
                "SELECT i.id,i.kind,i.status,i.incomplete,i.run_id,v.item_revision,s.revision "
                "FROM items i JOIN memory_source_items v ON v.item_id=i.id "
                "JOIN memory_sources s ON s.session_id=i.session_id "
                "WHERE i.id=? AND i.session_id=? AND v.eligible=1 AND s.deleted=0 AND s.temporary=0 "
                "AND NOT EXISTS(SELECT 1 FROM memory_suppressions WHERE scope_id=? AND item_id=i.id) "
                "AND NOT EXISTS(SELECT 1 FROM run_revisions WHERE source_run_id=i.run_id)",
                (item_id, session_id, scope_id),
            ).fetchone()
            if row is None or row["status"] != "completed" or row["incomplete"]:
                raise MemoryRejected("memory_evidence_invalid")
            if (
                evidence_class in {"explicit_user", "repeated_user"}
                and row["kind"] != "user_message"
            ):
                raise MemoryRejected("memory_user_evidence_required")
            # Model prose alone is never proof of execution or verification.
            if evidence_class == "observed_verified" and row["kind"] in {
                "assistant_message",
                "user_message",
            }:
                raise MemoryRejected("memory_tool_evidence_required")
            if row["kind"] != "user_message" and evidence_class == "inferred":
                raise MemoryRejected("memory_original_user_evidence_required")
            values.append(
                MemoryEvidence(
                    item_id=item_id,
                    session_id=session_id,
                    run_id=row["run_id"],
                    item_revision=row["item_revision"],
                    source_revision=row["revision"],
                    evidence_class=evidence_class,
                )
            )
        return values

    def _read(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        revision: int | None = None,
    ) -> MemoryEntry:
        revision = revision or row["current_revision"]
        saved = connection.execute(
            "SELECT * FROM memory_revisions WHERE entry_id=? AND revision=?",
            (row["id"], revision),
        ).fetchone()
        if (
            saved is None
            or saved["file_ref"] is None
            or row["status"] in {"forgotten", "quarantined"}
        ):
            raise MemoryRejected("memory_entry_unavailable")
        evidence = [
            MemoryEvidence(
                item_id=v["item_id"],
                session_id=v["session_id"],
                run_id=v["run_id"],
                item_revision=v["item_revision"],
                source_revision=v["source_revision"],
                evidence_class=v["evidence_class"],
            )
            for v in connection.execute(
                "SELECT * FROM memory_evidence WHERE entry_id=? AND revision=?",
                (row["id"], revision),
            )
        ]
        if not row["user_owned"]:
            for value in evidence:
                source = connection.execute(
                    "SELECT v.item_revision,v.eligible,s.temporary,s.deleted FROM memory_source_items v "
                    "JOIN memory_sources s ON s.session_id=v.session_id WHERE v.item_id=?",
                    (value.item_id,),
                ).fetchone()
                suppressed = connection.execute(
                    "SELECT 1 FROM memory_suppressions WHERE scope_id=? AND item_id=?",
                    (row["scope_id"], value.item_id),
                ).fetchone()
                if (
                    source is None
                    or source[0] != value.item_revision
                    or not source[1]
                    or source[2]
                    or source[3]
                    or suppressed
                ):
                    raise MemoryRejected("memory_evidence_revoked")
            if not evidence:
                raise MemoryRejected("memory_evidence_required")
        try:
            content = self.files.read(saved["file_ref"])
        except (OSError, ValueError, UnicodeError):
            logger.warning(
                "Memory body unavailable entry_id=%s revision=%s", row["id"], revision
            )
            raise MemoryRejected("memory_body_unavailable") from None
        return MemoryEntry(
            id=row["id"],
            scope_id=row["scope_id"],
            revision=revision,
            kind=row["kind"],
            status=row["status"]
            if revision == row["current_revision"]
            else "superseded",
            title=saved["title"],
            content=content,
            aliases=json.loads(saved["aliases_json"]),
            evidence_class=saved["evidence_class"],
            evidence=evidence,
            pinned=bool(row["pinned"]),
            user_owned=bool(row["user_owned"]),
            valid_from=saved["valid_from"],
            valid_to=saved["valid_to"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            last_used_at=row["last_used_at"],
            use_count=row["use_count"],
        )

    def get(
        self,
        connection: sqlite3.Connection,
        session_id: str | None,
        entry_id: str,
        revision: int | None = None,
        *,
        for_use: bool = False,
    ) -> MemoryEntry:
        scopes = self.scopes(connection, session_id)
        allowed = {s.id for s in scopes if not for_use or s.settings.use_enabled}
        if for_use and self.temporary(connection, session_id):
            allowed.clear()
        row = connection.execute(
            "SELECT * FROM memory_entries WHERE id=?", (entry_id,)
        ).fetchone()
        if row is None or row["scope_id"] not in allowed:
            raise MemoryRejected("memory_entry_not_found")
        return self._read(connection, row, revision)

    def search(
        self,
        connection: sqlite3.Connection,
        request: MemoryReadRequest,
        *,
        for_use: bool = False,
    ) -> tuple[list[MemoryEntry], bool, int | None]:
        scopes = self.scopes(connection, request.session_id, request.scope)
        identifiers = [s.id for s in scopes if not for_use or s.settings.use_enabled]
        if not identifiers or (
            for_use and self.temporary(connection, request.session_id)
        ):
            return [], False, None
        placeholders = ",".join("?" for _ in identifiers)
        statuses = (
            "('active','superseded','archived')"
            if request.include_history
            else "('active')"
            if for_use
            else "('active','candidate')"
        )
        sql = f"SELECT e.*,e.rowid AS position FROM memory_entries e WHERE e.scope_id IN ({placeholders}) AND e.status IN {statuses}"
        parameters: list[object] = list(identifiers)
        query = request.query.strip()
        # Search bounded pages. Short-word fallback never scans every body.
        sql += " AND e.rowid>? ORDER BY e.rowid LIMIT 501"
        parameters.append(request.cursor)
        rows = connection.execute(sql, parameters).fetchall()
        truncated = len(rows) > 500
        rows = rows[:500]
        ranks: dict[str, float] = {}
        if query:
            terms = re.findall(r"[\w]+", query, flags=re.UNICODE)[:12]
            expression = " OR ".join(
                '"' + word.replace('"', '""') + '"' for word in terms
            )
            for table in [
                "memory_fts_word",
                *(
                    ["memory_fts_trigram"]
                    if self.trigram_available and len(query) >= 3
                    else []
                ),
            ]:
                if not expression:
                    continue
                try:
                    hits = connection.execute(
                        f"SELECT entry_id FROM {table} WHERE {table} MATCH ? AND scope_id IN ({placeholders}) ORDER BY bm25({table}) ASC LIMIT 200",
                        [expression, *identifiers],
                    ).fetchall()
                except sqlite3.OperationalError:
                    logger.warning("Memory search index unavailable index=%s", table)
                    continue
                for rank, hit in enumerate(hits):
                    ranks[hit[0]] = ranks.get(hit[0], 0.0) + 1 / (60 + rank)
        results: list[tuple[float, MemoryEntry, int]] = []
        for row in rows:
            try:
                entry = self._read(connection, row)
            except MemoryRejected:
                continue
            if request.valid_at is not None and (
                (entry.valid_from is not None and entry.valid_from > request.valid_at)
                or (entry.valid_to is not None and entry.valid_to <= request.valid_at)
            ):
                continue
            score = ranks.get(entry.id, 0.0)
            if query:
                q = query.casefold()
                title = " ".join([entry.title, *entry.aliases]).casefold()
                if q in title:
                    score += 1.0
                elif q in entry.content.casefold():
                    score += 0.5
                elif not score and WRatio(q, title) >= 85:
                    score += 0.1
                if not score:
                    continue
            results.append((score, entry, row["position"]))
        results.sort(
            key=lambda v: (
                -v[0],
                not v[1].pinned,
                v[1].evidence_class == "inferred",
                -v[1].updated_at,
                v[1].id,
            )
        )
        if len(results) > request.limit:
            # Keep pagination deterministic in rowid order when continuation is
            # needed; ranking within the page must not skip unseen entries.
            results.sort(key=lambda v: v[2])
            cursor = results[request.limit - 1][2]
            return [v[1] for v in results[: request.limit]], True, cursor
        cursor = rows[-1]["position"] if truncated and rows else None
        return [v[1] for v in results], truncated, cursor

    def index(self, connection: sqlite3.Connection, entry: MemoryEntry) -> None:
        for table in [
            "memory_fts_word",
            *(["memory_fts_trigram"] if self.trigram_available else []),
        ]:
            connection.execute(f"DELETE FROM {table} WHERE entry_id=?", (entry.id,))
            if entry.status == "active":
                connection.execute(
                    f"INSERT INTO {table}(entry_id,scope_id,title,aliases,body) VALUES(?,?,?,?,?)",
                    (
                        entry.id,
                        entry.scope_id,
                        entry.title,
                        " ".join(entry.aliases),
                        entry.content,
                    ),
                )

    def publish(
        self,
        connection: sqlite3.Connection,
        scope_id: str,
        record: MemoryRecord,
        file_ref: str,
        evidence: list[MemoryEvidence],
        *,
        status: str = "active",
        user_owned: bool = False,
        evidence_class: str = "explicit_user",
        entry_id: str | None = None,
        expected_revision: int | None = None,
    ) -> MemoryEntry:
        now = now_ms()
        if entry_id is None:
            entry_id = str(uuid.uuid4())
            revision = 1
            connection.execute(
                "INSERT INTO memory_entries(id,scope_id,kind,status,current_revision,user_owned,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    entry_id,
                    scope_id,
                    record.kind,
                    status,
                    revision,
                    int(user_owned),
                    now,
                    now,
                ),
            )
        else:
            revision = (expected_revision or 0) + 1
            changed = connection.execute(
                "UPDATE memory_entries SET current_revision=?,status=?,updated_at=?,user_owned=? WHERE id=? AND scope_id=? AND current_revision=? AND status NOT IN ('forgotten','quarantined')",
                (
                    revision,
                    status,
                    now,
                    int(user_owned),
                    entry_id,
                    scope_id,
                    expected_revision,
                ),
            )
            if changed.rowcount != 1:
                raise MemoryRejected("memory_revision_conflict")
        connection.execute(
            "INSERT INTO memory_revisions(entry_id,revision,file_ref,title,aliases_json,evidence_class,valid_from,valid_to,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                entry_id,
                revision,
                file_ref,
                record.title or record.content[:80],
                json.dumps(record.aliases, ensure_ascii=False),
                evidence_class,
                record.valid_from,
                record.valid_to,
                now,
            ),
        )
        for value in evidence:
            connection.execute(
                "INSERT INTO memory_evidence(entry_id,revision,item_id,session_id,run_id,item_revision,source_revision,evidence_class) VALUES(?,?,?,?,?,?,?,?)",
                (
                    entry_id,
                    revision,
                    value.item_id,
                    value.session_id,
                    value.run_id,
                    value.item_revision,
                    value.source_revision,
                    value.evidence_class,
                ),
            )
        row = connection.execute(
            "SELECT * FROM memory_entries WHERE id=?", (entry_id,)
        ).fetchone()
        entry = self._read(connection, row)
        self.index(connection, entry)
        connection.execute(
            "UPDATE memory_scopes SET generation=generation+1 WHERE id=?", (scope_id,)
        )
        return entry

    def replay(
        self,
        connection: sqlite3.Connection,
        operation_id: str,
        allowed: set[str],
        request_json: str,
    ) -> MemoryActionResult | None:
        row = connection.execute(
            "SELECT * FROM memory_actions WHERE operation_id=?", (operation_id,)
        ).fetchone()
        if row is None:
            return None
        if row["scope_id"] not in allowed:
            raise MemoryRejected("memory_operation_not_found")
        if row["request_json"] != request_json:
            raise MemoryRejected("memory_operation_conflict")
        return MemoryActionResult.model_validate_json(row["result_json"])

    def save_action(
        self,
        connection: sqlite3.Connection,
        request: MemoryWriteRequest | MemoryManageRequest,
        scope_id: str,
        action: str,
        result: MemoryActionResult,
    ) -> None:
        connection.execute(
            "INSERT INTO memory_actions(operation_id,scope_id,entry_id,action,request_json,result_json,created_at) VALUES(?,?,?,?,?,?,?)",
            (
                request.operation_id,
                scope_id,
                result.entry_id,
                action,
                request.model_dump_json(),
                result.model_dump_json(),
                now_ms(),
            ),
        )

    def revoke(
        self,
        connection: sqlite3.Connection,
        entry: MemoryEntry,
        *,
        forgotten: bool = False,
    ) -> None:
        connection.execute(
            "UPDATE memory_entries SET status=?,updated_at=? WHERE id=?",
            ("forgotten" if forgotten else "quarantined", now_ms(), entry.id),
        )
        for table in [
            "memory_fts_word",
            *(["memory_fts_trigram"] if self.trigram_available else []),
        ]:
            connection.execute(f"DELETE FROM {table} WHERE entry_id=?", (entry.id,))
        connection.execute(
            "UPDATE memory_scopes SET privacy_epoch=privacy_epoch+1,generation=generation+1 WHERE id=?",
            (entry.scope_id,),
        )
        epoch = self.scope(connection, entry.scope_id).privacy_epoch
        # Suppress *all revisions*, not just the currently displayed evidence.
        if forgotten:
            connection.execute(
                "INSERT OR REPLACE INTO memory_suppressions(scope_id,item_id,privacy_epoch) SELECT ?,item_id,? FROM memory_evidence WHERE entry_id=?",
                (entry.scope_id, epoch, entry.id),
            )
            connection.execute(
                "UPDATE memory_revisions SET title='',aliases_json='[]' WHERE entry_id=?",
                (entry.id,),
            )
            connection.execute(
                "UPDATE memory_actions SET request_json='[privacy-revoked]' WHERE entry_id=?",
                (entry.id,),
            )
        connection.execute(
            "UPDATE memory_jobs SET extraction_json=NULL,proposals_json=NULL WHERE scope_id=?",
            (entry.scope_id,),
        )

    def jobs(
        self, connection: sqlite3.Connection, allowed: set[str]
    ) -> list[MemoryJob]:
        if not allowed:
            return []
        rows = connection.execute(
            "SELECT * FROM memory_jobs WHERE scope_id IN ("
            + ",".join("?" for _ in allowed)
            + ") ORDER BY created_at DESC LIMIT 50",
            list(allowed),
        ).fetchall()
        return [
            MemoryJob(
                id=r["id"],
                scope_id=r["scope_id"],
                session_id=r["session_id"],
                source_revision=r["source_revision"],
                kind=r["kind"],
                state=r["state"],
                model_id=r["model_id"],
                attempts=r["attempts"],
                not_before=r["not_before"],
                error_code=r["error_code"],
                tokens=r["tokens"],
                estimated_usage=bool(r["estimated_usage"]),
            )
            for r in rows
        ]

    def usage(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        step_id: str,
        entry: MemoryEntry,
        kind: str,
    ) -> None:
        inserted = connection.execute(
            "INSERT OR IGNORE INTO memory_usage(run_id,step_id,entry_id,revision,kind,created_at) VALUES(?,?,?,?,?,?)",
            (run_id, step_id, entry.id, entry.revision, kind, now_ms()),
        ).rowcount
        if inserted and kind in {"read", "cited", "applied"}:
            connection.execute(
                "UPDATE memory_entries SET use_count=use_count+1,last_used_at=? WHERE id=?",
                (now_ms(), entry.id),
            )

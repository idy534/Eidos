from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager, ExitStack
import hashlib
import json
import logging
import os
import shutil
import stat
import sqlite3
import threading
import time
from urllib.parse import quote
import uuid

from pydantic import Field, TypeAdapter, ValidationError

from eidos_runtime.context.tokens import estimate_text_tokens
from eidos_runtime.db.database import Database, now_ms
from eidos_runtime.memory.contracts import (
    MemoryActionResult,
    MemoryDocument,
    MemoryEditRequest,
    MemoryEvidence,
    MemoryExport,
    MemoryExportFile,
    MemoryExportRequest,
    MemoryListRequest,
    MemoryKind,
    MemoryPolicyRequest,
    MemoryProjection,
    MemoryProjectionRef,
    MemoryReadRequest,
    MemoryReadResult,
    MemoryScope,
    MemoryState,
    NonnegativeInt,
)
from eidos_runtime.memory.repository import MemoryRejected, MemoryRepository
from eidos_runtime.models import EidosFrozenStrictModel
from eidos_runtime.sandbox.sensitive import SensitiveScanError, default_scanner

logger = logging.getLogger("eidos.runtime.memory")
_EPOCHS = TypeAdapter(dict[str, NonnegativeInt])
_REVISION = TypeAdapter(NonnegativeInt)


class MemoryAccess(EidosFrozenStrictModel):
    scopes: tuple[MemoryScope, ...] = Field(min_length=1, max_length=2)
    writable: bool
    available: bool = True

    @property
    def read_scopes(self) -> tuple[str, ...]:
        return tuple(
            scope.kind
            for scope in self.scopes
            if self.available and scope.mode != "off"
        )

    @property
    def read_enabled(self) -> bool:
        return bool(self.read_scopes)

    @property
    def generate_enabled(self) -> bool:
        return self.writable and any(scope.mode == "auto" for scope in self.scopes)


class MemoryService:
    """Document memory interface shared by the foreground tools and Desktop."""

    def __init__(self, database: Database) -> None:
        self.database = database
        self.on_cleanup: Callable[[], None] | None = None
        self._admitted: dict[int, tuple[dict[str, int], threading.Event]] = {}
        self._preparing: set[str] = set()
        self._clear_legacy_files()
        self.repository = MemoryRepository(database)

    def _clear_legacy_files(self) -> None:
        with self.database.lock:
            row = (
                self.database.connection()
                .execute(
                    "SELECT value FROM memory_metadata WHERE key='legacy_cleanup_pending'"
                )
                .fetchone()
            )
            if row is None or row[0] != "1":
                return
            root = self.database.data_directory
            if root is None:
                raise MemoryRejected("memory_storage_unavailable")
            # The user chose a destructive memory reset. Never follow links or
            # touch chat, project, credential or execution-blob directories.
            for name in (
                "memory",
                "memories",
                "memories.sqlite",
                "memories.sqlite-wal",
                "memories.sqlite-shm",
                "hook-transcripts",
            ):
                path = root / name
                try:
                    metadata = path.lstat()
                except FileNotFoundError:
                    continue
                if metadata.st_uid != os.getuid():
                    raise MemoryRejected("memory_legacy_owner_invalid")
                if stat.S_ISDIR(metadata.st_mode):
                    shutil.rmtree(path)
                else:
                    path.unlink()
            with self.database.transaction() as connection:
                connection.execute(
                    "UPDATE memory_metadata SET value='0' WHERE key='legacy_cleanup_pending'"
                )

    def access(self, session_id: str) -> MemoryAccess:
        with self.database.transaction() as connection:
            scopes = tuple(self.repository.scopes(connection, session_id))
            return MemoryAccess(
                scopes=scopes,
                writable=self.repository.root_session(connection, session_id)
                == session_id,
                available=self.repository.root_session(connection, session_id)
                == session_id,
            )

    def list(self, request: MemoryListRequest, *, for_use: bool = False) -> MemoryState:
        if for_use and request.project_id is not None:
            raise MemoryRejected("memory_model_target_forbidden")
        with self.database.transaction() as connection:
            if (
                for_use
                and request.session_id
                and not self.access(request.session_id).available
            ):
                raise MemoryRejected("memory_child_disabled")
            scopes = self.repository.scopes(connection, request.session_id, project_id=request.project_id)
            selected = self.repository.scopes(
                connection, request.session_id, request.scope, project_id=request.project_id
            )
            ids = [scope.id for scope in selected if not for_use or scope.mode != "off"]
            if for_use and not ids:
                raise MemoryRejected("memory_read_disabled")
            documents, cursor, limited = self.repository.list(
                connection,
                ids,
                request.query,
                request.cursor,
                request.limit,
                include_archived=request.include_archived,
            )
            return MemoryState(
                scopes=scopes,
                documents=documents,
                next_cursor=cursor,
                search_limited=limited,
            )

    @staticmethod
    def _render_index(documents: list[MemoryDocument], *, more: bool = False) -> str:
        lines = [
            "# Memory",
            "",
            "Read the linked note when its scope or conditions matter.",
            "",
        ]
        for document in sorted(documents, key=lambda value: value.path):
            summary = " ".join(document.summary.split())
            path = quote(document.path, safe="/")
            label = document.path.replace("[", "\\[").replace("]", "\\]")
            line = f"- [{label}]({path}): {summary}"
            if len(lines) >= 198 or len(("\n".join(lines) + line).encode()) > 24 * 1024:
                more = True
                break
            lines.append(line)
        if more:
            lines.extend(
                [
                    "",
                    "More notes are available through memory_search and paginated memory_read.",
                ]
            )
        if not documents:
            lines.append("No notes yet.")
        return "\n".join(lines) + "\n"

    def _index_documents(
        self, connection: sqlite3.Connection, scope_id: str
    ) -> tuple[list[MemoryDocument], bool]:
        documents = []
        rows = connection.execute(
            "SELECT * FROM memory_documents WHERE scope_id=? AND status='active' AND archived=0 ORDER BY path LIMIT 201",
            (scope_id,),
        ).fetchall()
        for row in rows[:200]:
            try:
                documents.append(self.repository.document(connection, row)[0])
            except MemoryRejected:
                continue
        return documents, len(rows) > 200

    def read(
        self, request: MemoryReadRequest, *, for_use: bool = False
    ) -> MemoryReadResult:
        if for_use and request.project_id is not None:
            raise MemoryRejected("memory_model_target_forbidden")
        with self.database.transaction() as connection:
            if (
                for_use
                and request.session_id
                and not self.access(request.session_id).available
            ):
                raise MemoryRejected("memory_child_disabled")
            scope = self.repository.scopes(
                connection, request.session_id, request.scope, project_id=request.project_id
            )[0]
            if for_use and scope.mode == "off":
                raise MemoryRejected("memory_read_disabled")
            document = None
            revisions: list[int] = []
            if request.path == "MEMORY.md":
                documents, more = self._index_documents(connection, scope.id)
                body, revision = (
                    self._render_index(documents, more=more),
                    scope.generation,
                )
            else:
                row = self.repository.row(connection, scope.id, request.path)
                if row is None:
                    raise MemoryRejected("memory_document_not_found")
                document, body = self.repository.document(
                    connection, row, request.revision
                )
                revision = document.revision
                revisions = [
                    value[0]
                    for value in connection.execute(
                        "SELECT revision FROM memory_revisions WHERE document_id=? AND file_ref IS NOT NULL ORDER BY revision DESC LIMIT 50",
                        (document.id,),
                    )
                ]
            if request.revision is not None and request.revision != revision:
                raise MemoryRejected("memory_revision_conflict")
            end = min(len(body), request.offset + request.limit)
            epochs = (
                self.use_epochs(
                    connection, request.session_id, {scope.id: scope.privacy_epoch}
                )
                if for_use
                else {}
            )
            return MemoryReadResult(
                path=request.path,
                scope_id=scope.id,
                revision=revision,
                document=document,
                current_revision=row["current_revision"] if document else revision,
                revisions=revisions,
                content=body[request.offset : end],
                offset=request.offset,
                next_offset=end if end < len(body) else None,
                epochs=epochs,
            )

    def _state(
        self,
        connection: sqlite3.Connection,
        request: MemoryEditRequest,
        automatic: bool,
    ) -> tuple[MemoryScope, list[MemoryEvidence], MemoryDocument | None, str]:
        if automatic and request.project_id is not None:
            raise MemoryRejected("memory_model_target_forbidden")
        self.repository.assert_writable(connection, request.session_id)
        scope = self.repository.scopes(connection, request.session_id, request.scope, project_id=request.project_id)[0]
        if automatic and scope.mode != "auto":
            raise MemoryRejected("memory_automatic_disabled")
        if automatic and any(
            connection.execute(
                "SELECT 1 FROM memory_suppressions WHERE scope_id=? AND item_id=?",
                (scope.id, item_id),
            ).fetchone()
            for item_id in request.source_item_ids
        ):
            raise MemoryRejected("memory_forgotten_source")
        evidence = self.repository.evidence(
            connection,
            request.session_id,
            request.source_item_ids,
            scope.id,
            automatic=automatic,
        )
        row = self.repository.row(connection, scope.id, request.path)
        document, body = (
            self.repository.document(connection, row) if row else (None, "")
        )
        if (document.revision if document else 0) != request.expected_revision:
            raise MemoryRejected("memory_revision_conflict")
        if request.action == "delete" and document is None:
            raise MemoryRejected("memory_document_not_found")
        return scope, evidence, document, body

    def validate_edit(
        self, request: MemoryEditRequest, *, automatic: bool = False
    ) -> str:
        try:
            value = request.model_dump(mode="json")
            if default_scanner().scan_json(value) != value:
                raise MemoryRejected("memory_sensitive_content")
        except SensitiveScanError:
            raise MemoryRejected("memory_sensitive_content") from None
        with self.database.transaction() as connection:
            return self._state(connection, request, automatic)[3]

    def edit_base(
        self, run_id: str, scope_id: str, path: str, *, item_id: str
    ) -> tuple[int, MemoryKind]:
        """Use Run read/write evidence, or a private CAS base for explicit off-mode replacement."""
        with self.database.lock:
            connection = self.database.connection()
            current = self.repository.row(connection, scope_id, path)
            pending = connection.execute(
                "SELECT json_extract(request_json,'$.expectedRevision') FROM approvals WHERE run_id=? AND item_id=? AND status='pending'",
                (run_id, item_id),
            ).fetchone()
            if pending is not None:
                kind = (
                    self.repository.document(connection, current)[0].kind
                    if current is not None
                    else "feedback"
                )
                return _REVISION.validate_python(pending[0]), kind
            if current is None:
                return 0, "feedback"
            if self.repository.scope(connection, scope_id).mode == "off":
                # Off mode does not expose the existing body to the model.
                # Its explicit replacement still captures CAS before approval.
                document, _ = self.repository.document(connection, current)
                return document.revision, document.kind
            read = connection.execute(
                """SELECT COALESCE(json_extract(t.result_json,'$.data.read.revision'),
                                   json_extract(t.result_json,'$.data.action.revision')) AS revision
                   FROM tool_calls t JOIN items i ON i.id=t.item_id
                   WHERE i.run_id=? AND t.status='completed'
                     AND json_extract(t.result_json,'$.outcome')='success'
                     AND ((t.tool_name='memory_read'
                       AND json_extract(t.result_json,'$.data.read.path')=?
                       AND json_extract(t.result_json,'$.data.read.scopeId')=?)
                      OR (t.tool_name='memory_edit'
                       AND json_extract(t.result_json,'$.data.action.documentId')=?))
                   ORDER BY t.creation_seq DESC LIMIT 1""",
                (run_id, path, scope_id, current["id"]),
            ).fetchone()
            if read is None:
                raise MemoryRejected("memory_read_required")
            document, _ = self.repository.document(connection, current)
            return int(read["revision"]), document.kind

    def audit_sources(self, run_id: str) -> list[str]:
        with self.database.lock:
            connection = self.database.connection()
            user = connection.execute(
                "SELECT id FROM items WHERE run_id=? AND kind='user_message' AND status='completed' AND incomplete=0 ORDER BY creation_seq DESC LIMIT 1",
                (run_id,),
            ).fetchone()
            tools = connection.execute(
                """SELECT i.id FROM items i JOIN tool_calls t ON t.item_id=i.id
                   WHERE i.run_id=? AND i.incomplete=0 AND t.status IN ('completed','failed')
                     AND t.tool_name NOT LIKE 'memory_%'
                   ORDER BY t.creation_seq DESC LIMIT 15""",
                (run_id,),
            ).fetchall()
            return ([user[0]] if user else []) + [row[0] for row in tools]

    @contextmanager
    def prepared(self, scope_id: str, body: str | None) -> Iterator[str]:
        # Register preparation under the same lock as the orphan collector.
        with self.database.lock:
            reference = (
                self.repository.files.prepare(scope_id, body)
                if body is not None
                else ""
            )
            if reference:
                self._preparing.add(reference)
        try:
            yield reference
        finally:
            if reference:
                with self.database.lock:
                    self._preparing.discard(reference)
                    connection = self.database.connection()
                    linked = connection.execute(
                        "SELECT 1 FROM memory_revisions WHERE file_ref=? UNION ALL SELECT 1 FROM memory_indexes WHERE file_ref=?",
                        (reference, reference),
                    ).fetchone()
                    if linked is None:
                        self.repository.files.remove(reference)

    def edit(
        self,
        request: MemoryEditRequest,
        *,
        automatic: bool = False,
        archive: bool = False,
        cancel: threading.Event | None = None,
        access_epochs: dict[str, int] | None = None,
    ) -> MemoryActionResult:
        archive = archive and request.action == "delete"
        fingerprint = hashlib.sha256(
            (
                request.model_dump_json(
                    exclude={"project_id"} if request.project_id is None else None
                ) + (":archive" if archive else "")
            ).encode()
        ).hexdigest()
        with self.database.transaction() as connection:
            replay = connection.execute(
                "SELECT request_hash,result_json FROM memory_actions WHERE operation_id=?",
                (request.operation_id,),
            ).fetchone()
            if replay:
                if replay[0] != fingerprint:
                    raise MemoryRejected("memory_operation_conflict")
                return MemoryActionResult.model_validate_json(replay[1])
        self.validate_edit(request, automatic=automatic)
        with self.database.transaction() as connection:
            scope, _, old, old_body = self._state(connection, request, automatic)
            title = request.title.strip() or (
                old.title if old else request.path.removesuffix(".md")
            )
            summary = request.summary.strip() or (
                old.summary
                if old
                else next(
                    (
                        line.lstrip("# ").strip()[:240]
                        for line in (request.content or "").splitlines()
                        if line.lstrip("# ").strip()
                    ),
                    title[:240],
                )
            )
            unchanged = bool(
                old
                and not old.archived
                and request.action == "write"
                and old_body == request.content
                and old.title == title
                and old.summary == summary
                and old.kind == request.kind
            )
            if archive and old is not None and old.archived:
                unchanged = True
            identifier = old.id if old else str(uuid.uuid4())
            revision = request.expected_revision + (0 if unchanged else 1)
            code = (
                "memory_unchanged"
                if unchanged
                else "memory_archived"
                if archive
                else "memory_deleted"
                if request.action == "delete"
                else "memory_saved"
            )
            result = MemoryActionResult(
                operation_id=request.operation_id,
                document_id=identifier,
                path=request.path,
                revision=revision,
                code=code,
            )
        # Only the note body is prepared before publication. The small derived
        # index is rebuilt from the latest committed notes inside publication,
        # so another note's write does not conflict or overwrite its index entry.
        with ExitStack() as prepared:
            reference = prepared.enter_context(
                self.prepared(
                    scope.id,
                    request.content
                    if request.action == "write" and not unchanged
                    else None,
                )
            )
            with self.database.transaction() as connection:
                if cancel is not None and cancel.is_set():
                    raise MemoryRejected("memory_canceled")
                if access_epochs is not None and not self.valid_epochs(
                    connection, access_epochs
                ):
                    raise MemoryRejected("memory_snapshot_revoked")
                checked, evidence, current, _ = self._state(
                    connection, request, automatic
                )
                stamp = now_ms()
                if not unchanged:
                    if request.action == "delete" and not archive:
                        connection.execute(
                            "UPDATE memory_documents SET status='forgotten',updated_at=? WHERE id=?",
                            (stamp, identifier),
                        )
                        connection.execute(
                            "INSERT OR IGNORE INTO memory_suppressions(scope_id,item_id,privacy_epoch) SELECT ?,item_id,? FROM memory_evidence WHERE document_id=?",
                            (scope.id, checked.privacy_epoch + 1, identifier),
                        )
                    else:
                        if current is None:
                            connection.execute(
                                "INSERT INTO memory_documents(id,scope_id,path,current_revision,status,user_owned,created_at,updated_at,archived) VALUES(?,?,?,?,'active',?,?,?,?)",
                                (
                                    identifier,
                                    scope.id,
                                    request.path,
                                    revision,
                                    int(not automatic),
                                    stamp,
                                    stamp,
                                    int(archive),
                                ),
                            )
                        else:
                            connection.execute(
                                "UPDATE memory_documents SET current_revision=?,user_owned=?,updated_at=?,archived=? WHERE id=?",
                                (
                                    revision,
                                    int(not automatic),
                                    stamp,
                                    int(archive),
                                    identifier,
                                ),
                            )
                        if archive:
                            # A normal tool deletion is reversible. Keep an exact
                            # content version, and reserve its path for restoration.
                            saved = connection.execute(
                                "SELECT * FROM memory_revisions WHERE document_id=? AND revision=?",
                                (identifier, request.expected_revision),
                            ).fetchone()
                            connection.execute(
                                "INSERT INTO memory_revisions(document_id,revision,file_ref,file_size,file_mtime_ns,title,summary,kind,user_owned,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                                (
                                    identifier,
                                    revision,
                                    saved["file_ref"],
                                    saved["file_size"],
                                    saved["file_mtime_ns"],
                                    saved["title"],
                                    saved["summary"],
                                    saved["kind"],
                                    int(not automatic),
                                    stamp,
                                ),
                            )
                        else:
                            size, mtime = self.repository.files.signature(reference)
                            connection.execute(
                                "INSERT INTO memory_revisions(document_id,revision,file_ref,file_size,file_mtime_ns,title,summary,kind,user_owned,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                                (
                                    identifier,
                                    revision,
                                    reference,
                                    size,
                                    mtime,
                                    title,
                                    summary,
                                    request.kind,
                                    int(not automatic),
                                    stamp,
                                ),
                            )
                        for value in evidence:
                            connection.execute(
                                "INSERT INTO memory_evidence(document_id,revision,item_id,session_id,run_id,item_revision,source_revision,evidence_class) VALUES(?,?,?,?,?,?,?,?)",
                                (
                                    identifier,
                                    revision,
                                    value.item_id,
                                    value.session_id,
                                    value.run_id,
                                    value.item_revision,
                                    value.source_revision,
                                    value.evidence_class,
                                ),
                            )
                        if not archive:
                            row = self.repository.row(
                                connection, scope.id, request.path
                            )
                            document, body = self.repository.document(connection, row)
                            self.repository.index(connection, document, body)
                    epoch = checked.privacy_epoch + int(
                        request.action == "delete" and not archive
                    )
                    connection.execute(
                        "UPDATE memory_scopes SET generation=generation+1,privacy_epoch=? WHERE id=?",
                        (epoch, scope.id),
                    )
                    documents, more = self._index_documents(connection, scope.id)
                    index_ref = prepared.enter_context(
                        self.prepared(
                            scope.id, self._render_index(documents, more=more)
                        )
                    )
                    connection.execute(
                        "INSERT OR REPLACE INTO memory_indexes(scope_id,generation,privacy_epoch,file_ref) VALUES(?,?,?,?)",
                        (scope.id, checked.generation + 1, epoch, index_ref),
                    )
                connection.execute(
                    "INSERT INTO memory_actions(operation_id,scope_id,document_id,request_hash,result_json,created_at) VALUES(?,?,?,?,?,?)",
                    (
                        request.operation_id,
                        scope.id,
                        identifier,
                        fingerprint,
                        result.model_dump_json(),
                        stamp,
                    ),
                )
                self._cancel_revoked(connection)
        self.cleanup()
        return result

    def policy(self, request: MemoryPolicyRequest) -> MemoryState:
        with self.database.transaction() as connection:
            self.repository.assert_writable(connection, request.session_id)
            scope = self.repository.scopes(
                connection, request.session_id, request.scope, project_id=request.project_id
            )[0]
            if scope.mode != request.mode:
                connection.execute(
                    "UPDATE memory_scopes SET mode=?,enabled_after=?,privacy_epoch=privacy_epoch+?,generation=generation+1 WHERE id=?",
                    (request.mode, now_ms(), int(request.mode == "off"), scope.id),
                )
                self._cancel_revoked(connection)
        self.cleanup()
        return self.list(
            MemoryListRequest(session_id=request.session_id, project_id=request.project_id, scope=request.scope)
        )

    def export(self, request: MemoryExportRequest) -> MemoryExport:
        # Export at most 16 documents per page; callers consume every page.
        state = self.list(
            MemoryListRequest(
                session_id=request.session_id,
                project_id=request.project_id,
                scope=request.scope,
                cursor=request.cursor,
                limit=16,
            )
        )
        files = []
        with self.database.transaction() as connection:
            if request.cursor == 0:
                selected = self.repository.scopes(
                    connection, request.session_id, request.scope, project_id=request.project_id
                )[0]
                documents, more = self._index_documents(connection, selected.id)
                files.append(
                    MemoryExportFile(
                        path="MEMORY.md",
                        content=self._render_index(documents, more=more),
                    )
                )
            for document in state.documents:
                row = self.repository.row(connection, document.scope_id, document.path)
                if row is None or row["current_revision"] != document.revision:
                    raise MemoryRejected("memory_revision_conflict")
                _, body = self.repository.document(connection, row)
                files.append(MemoryExportFile(path=document.path, content=body))
            generation = self.repository.scopes(
                connection, request.session_id, request.scope, project_id=request.project_id
            )[0].generation
        return MemoryExport(
            files=files, generation=generation, next_cursor=state.next_cursor
        )

    def project(
        self, session_id: str, available_tokens: int, *, run_id: str
    ) -> MemoryProjection:
        del run_id
        budget = max(0, min(2000, int(available_tokens * 0.05)))
        with self.database.transaction() as connection:
            scopes = [
                scope
                for scope in self.repository.scopes(connection, session_id)
                if self.access(session_id).available and scope.mode != "off"
            ]
            text, refs, generations, epochs = "", [], {}, {}
            for scope in reversed(scopes):
                documents, more = self._index_documents(connection, scope.id)
                if not documents:
                    continue
                prefix = f"\n{scope.kind}/MEMORY.md (historical context):\n"
                block = self._render_index(documents, more=more)
                selected = []
                truncated = False
                footer = "More notes are available through memory_search and paginated memory_read."
                for line in block.splitlines():
                    if (
                        estimate_text_tokens(
                            text + prefix + "\n".join([*selected, line, footer])
                        )
                        > budget
                    ):
                        truncated = True
                        break
                    selected.append(line)
                if not selected:
                    continue
                if truncated:
                    selected.extend(["", footer])
                text += prefix + "\n".join(selected) + "\n"
                epochs[scope.id], generations[scope.id] = (
                    scope.privacy_epoch,
                    scope.generation,
                )
                refs.extend(
                    MemoryProjectionRef(
                        entry_id=document.id, revision=document.revision
                    )
                    for document in documents
                )
            return MemoryProjection(
                epochs=self.use_epochs(connection, session_id, epochs) if text else {},
                generations=generations,
                entries=refs,
                rendered_payload=text,
                token_estimate=estimate_text_tokens(text) if text else 0,
            )

    def use_epochs(
        self,
        connection: sqlite3.Connection,
        session_id: str | None,
        epochs: dict[str, int],
    ) -> dict[str, int]:
        return epochs

    @staticmethod
    def valid_epochs(connection: sqlite3.Connection, epochs: dict[str, int]) -> bool:
        for identifier, epoch in epochs.items():
            if identifier.startswith("session:"):
                # Older snapshots retain use epochs. Ordinary chat deletion no
                # longer withdraws notes; prior explicit epoch revocations stay invalid.
                row = connection.execute(
                    "SELECT use_epoch FROM memory_sources WHERE session_id=?",
                    (identifier.removeprefix("session:"),),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT privacy_epoch FROM memory_scopes WHERE id=?", (identifier,)
                ).fetchone()
            if row is None or row[0] != epoch:
                return False
        return True

    def track_tool(
        self,
        run_id: str,
        item_id: str,
        epochs: dict[str, int],
        result: dict[str, object],
        *,
        retrieval: bool,
    ) -> None:
        del result, retrieval
        with self.database.transaction() as connection:
            if not self.valid_epochs(connection, epochs):
                raise MemoryRejected("memory_snapshot_revoked")
            old = connection.execute(
                "SELECT revoked FROM memory_tool_reads WHERE item_id=?", (item_id,)
            ).fetchone()
            if old and old[0]:
                raise MemoryRejected("memory_snapshot_revoked")
            connection.execute(
                "INSERT OR IGNORE INTO memory_tool_reads(item_id,run_id,epochs_json) VALUES(?,?,?)",
                (item_id, run_id, json.dumps(epochs)),
            )

    def tool_epochs(self, item_id: str, *, session_id: str) -> dict[str, int] | None:
        """Get the durable retrieval guard without exposing it in tool text."""
        with self.database.lock:
            connection = self.database.connection()
            row = connection.execute(
                "SELECT epochs_json,revoked FROM memory_tool_reads WHERE item_id=?",
                (item_id,),
            ).fetchone()
            if row is None:
                from eidos_runtime.persistence.session_history import (
                    SessionHistoryRepository,
                )

                history = SessionHistoryRepository(self.database).item_record(
                    session_id, item_id
                )
                if history is not None:
                    row = connection.execute(
                        "SELECT epochs_json,revoked FROM memory_tool_reads WHERE item_id=?",
                        (history.origin.item_id,),
                    ).fetchone()
            if row is None or row["revoked"]:
                return None
            try:
                return _EPOCHS.validate_json(row["epochs_json"]) or None
            except ValidationError:
                return None

    @contextmanager
    def admit(
        self, epochs: dict[str, int], parent_cancel: threading.Event
    ) -> Iterator[threading.Event]:
        guard = MemoryCancellation(self, epochs, parent_cancel)
        with self.database.lock:
            if not self.valid_epochs(self.database.connection(), epochs):
                raise MemoryRejected("memory_snapshot_revoked")
            self._admitted[id(guard)] = epochs, guard
        try:
            yield guard
            if guard.is_set() and not parent_cancel.is_set():
                raise MemoryRejected("memory_snapshot_revoked")
        finally:
            with self.database.lock:
                self._admitted.pop(id(guard), None)

    def _cancel_revoked(self, connection: sqlite3.Connection) -> None:
        for epochs, cancel in self._admitted.values():
            if not self.valid_epochs(connection, epochs):
                cancel.set()

    def collect_unreferenced_files(self) -> int:
        with self.database.lock:
            connection = self.database.connection()
            references = {
                row[0]
                for row in connection.execute(
                    "SELECT file_ref FROM memory_revisions WHERE file_ref IS NOT NULL UNION SELECT file_ref FROM memory_indexes"
                )
            }
            return self.repository.files.collect_unreferenced(
                references | self._preparing
            )

    def cleanup(self) -> None:
        from eidos_runtime.runtime.errors import tool_error

        changed = False
        with self.database.transaction() as connection:
            self._cancel_revoked(connection)
            rows = connection.execute(
                "SELECT r.document_id,r.revision,r.file_ref FROM memory_revisions r JOIN memory_documents d ON d.id=r.document_id WHERE r.file_ref IS NOT NULL AND d.status<>'active' LIMIT 500"
            ).fetchall()
            for row in rows:
                self.repository.files.remove(row["file_ref"])
                connection.execute(
                    "UPDATE memory_revisions SET file_ref=NULL,title='',summary='' WHERE document_id=? AND revision=?",
                    (row["document_id"], row["revision"]),
                )
                changed = True
            for row in connection.execute(
                "SELECT i.file_ref,i.scope_id FROM memory_indexes i JOIN memory_scopes s ON s.id=i.scope_id WHERE i.privacy_epoch<>s.privacy_epoch OR i.generation<>s.generation"
            ).fetchall():
                self.repository.files.remove(row[0])
                connection.execute(
                    "DELETE FROM memory_indexes WHERE scope_id=?", (row[1],)
                )
                changed = True
            changed |= (
                connection.execute(
                    "UPDATE context_snapshots SET snapshot_json='{}' WHERE memory_revoked=1 AND snapshot_json<>'{}'"
                ).rowcount
                > 0
            )
            changed |= (
                connection.execute(
                    "UPDATE step_resolution_snapshots SET snapshot_json='{}' WHERE memory_revoked=1 AND snapshot_json<>'{}'"
                ).rowcount
                > 0
            )
            for row in connection.execute(
                "SELECT * FROM memory_tool_reads WHERE revoked=0 LIMIT 10000"
            ).fetchall():
                if self.valid_epochs(connection, json.loads(row["epochs_json"])):
                    continue
                tool = connection.execute(
                    "SELECT tool_name FROM tool_calls WHERE item_id=?",
                    (row["item_id"],),
                ).fetchone()
                if tool:
                    redacted = json.dumps(
                        tool_error(
                            tool[0],
                            "memory_snapshot_revoked",
                            "Historical memory payload revoked.",
                        )
                    )
                    connection.execute(
                        "UPDATE tool_calls SET arguments_json='{}',raw_arguments_json=NULL,result_json=?,model_result_json=?,ui_result_json=?,approval_diff=NULL,approval_feedback=NULL WHERE item_id=?",
                        (redacted, redacted, redacted, row["item_id"]),
                    )
                    connection.execute(
                        "UPDATE approvals SET request_json='{}',feedback=NULL WHERE item_id=?",
                        (row["item_id"],),
                    )
                connection.execute(
                    "UPDATE memory_tool_reads SET revoked=1 WHERE item_id=?",
                    (row["item_id"],),
                )
                changed = True
            for table in ["memory_fts_word"] + (
                ["memory_fts_trigram"] if self.repository.trigram_available else []
            ):
                connection.execute(
                    f"DELETE FROM {table} WHERE document_id IN (SELECT id FROM memory_documents WHERE status<>'active')"
                )
        from eidos_runtime.persistence.session_history import SessionHistoryRepository

        changed |= SessionHistoryRepository(self.database).scrub_revoked_memory()
        if changed and self.on_cleanup:
            self.on_cleanup()


class MemoryCancellation(threading.Event):
    def __init__(
        self, service: MemoryService, epochs: dict[str, int], parent: threading.Event
    ) -> None:
        super().__init__()
        self.service, self.epochs, self.parent = service, epochs, parent

    def is_set(self) -> bool:
        if super().is_set() or self.parent.is_set():
            return True
        with self.service.database.lock:
            if not self.service.valid_epochs(
                self.service.database.connection(), self.epochs
            ):
                self.set()
        return super().is_set()

    def wait(self, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        while not self.is_set():
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                break
            super().wait(0.05 if remaining is None else min(0.05, remaining))
        return self.is_set()

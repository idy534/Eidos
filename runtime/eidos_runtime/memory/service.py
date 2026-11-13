from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager, ExitStack
from dataclasses import dataclass
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
from typing import Literal
import unicodedata
import uuid

from pydantic import Field, TypeAdapter, ValidationError
from rapidfuzz.fuzz import WRatio
from rapidfuzz.utils import default_process

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
    MemorySimilarNote,
    MemoryState,
    NonnegativeInt,
)
from eidos_runtime.memory.repository import MemoryRejected, MemoryRepository
from eidos_runtime.models import EidosFrozenStrictModel
from eidos_runtime.sandbox.sensitive import SensitiveScanError, default_scanner

logger = logging.getLogger("eidos.runtime.memory")
_EPOCHS = TypeAdapter(dict[str, NonnegativeInt])
_REVISION = TypeAdapter(NonnegativeInt)

# MEMORY.md and its request projection share one limit: the first 200 lines or
# 25 KB, whichever comes first. Both readable scopes share that request budget.
INDEX_MAX_LINES = 200
INDEX_MAX_BYTES = 25 * 1024
INDEX_SUMMARY_CHARS = 120
# When both scopes have notes, the global scope keeps this share of the budget.
INDEX_GLOBAL_SHARE = 0.4
# A small model window must not be filled by the index.
INDEX_WINDOW_SHARE = 0.10
_INDEX_KINDS = ("feedback", "user", "project", "reference")
# New notes are compared with this many recent notes of the same scope.
SIMILAR_CANDIDATES = 500
SIMILAR_THRESHOLD = 85
_INDEX_INTRO = (
    "# Memory",
    "",
    "Read a linked note when its scope or conditions matter.",
    "",
)
_INDEX_FOOTER_LINES = 2
# Each scope block opens with a blank line and a "Scope ... MEMORY.md:" line.
_HEADING_LINES = 2
_INDEX_FOOTER_BYTES = 192
_INDEX_FOOTER_TOKENS = 48


def _index_overflow(hidden: int) -> str:
    noun = "note is" if hidden == 1 else "notes are"
    return (
        f"{hidden} more {noun} not listed. Use memory_search to find them, "
        "or merge and archive notes to keep this index short."
    )


def _index_line(document: MemoryDocument) -> str:
    summary = " ".join(document.summary.split())
    if len(summary) > INDEX_SUMMARY_CHARS:
        summary = summary[: INDEX_SUMMARY_CHARS - 1].rstrip() + "…"
    path = quote(document.path, safe="/")
    label = document.path.replace("[", "\\[").replace("]", "\\]")
    return f"- [{label}]({path}): {summary}"


@dataclass(frozen=True)
class _IndexBudget:
    lines: int
    bytes: int
    tokens: int | None = None


@dataclass(frozen=True)
class _ScopeIndex:
    scope: MemoryScope
    documents: list[MemoryDocument]
    total: int
    text: str
    shown: int


def _line_tokens(text: str) -> int:
    """Token estimate summed per line, as the index fitting counts them."""
    return sum(estimate_text_tokens(line) + 1 for line in text.splitlines())


def _render_index_text(
    documents: list[MemoryDocument], total: int, budget: _IndexBudget
) -> tuple[str, int]:
    """Render ordered notes within a line, byte and optional token budget.

    Returns the text and the number of listed notes. A footer states how many
    notes were left out; its room is reserved only when some notes do not fit.
    """

    def fit(lines_limit: int, bytes_limit: int, tokens_limit: int | None) -> tuple[list[str], int]:
        lines = list(_INDEX_INTRO)
        size = sum(len(line.encode()) + 1 for line in lines)
        tokens = sum(estimate_text_tokens(line) + 1 for line in lines)
        shown, kind = 0, None
        for document in documents:
            block = ([] if document.kind == kind else [f"## {document.kind}"]) + [
                _index_line(document)
            ]
            block_bytes = sum(len(line.encode()) + 1 for line in block)
            block_tokens = (
                sum(estimate_text_tokens(line) + 1 for line in block)
                if tokens_limit is not None
                else 0
            )
            if (
                len(lines) + len(block) > lines_limit
                or size + block_bytes > bytes_limit
                or (tokens_limit is not None and tokens + block_tokens > tokens_limit)
            ):
                break
            lines.extend(block)
            size += block_bytes
            tokens += block_tokens
            shown, kind = shown + 1, document.kind
        return lines, shown

    lines, shown = fit(budget.lines, budget.bytes, budget.tokens)
    if shown < total:
        lines, shown = fit(
            max(0, budget.lines - _INDEX_FOOTER_LINES),
            max(0, budget.bytes - _INDEX_FOOTER_BYTES),
            None if budget.tokens is None else max(0, budget.tokens - _INDEX_FOOTER_TOKENS),
        )
        lines.extend(["", _index_overflow(total - shown)])
    if total == 0:
        lines.append("No notes yet.")
    return "\n".join(lines) + "\n", shown


# Bidirectional controls and tag characters can hide text from a reader while
# the model still receives it. Zero-width joiners stay allowed in note bodies
# because emoji and some scripts need them.
_HIDDEN_BODY_CHARACTERS = frozenset(
    [*range(0x202A, 0x202F), *range(0x2066, 0x206A), *range(0xE0000, 0xE0080)]
)


def _unsafe_text(request: MemoryEditRequest) -> bool:
    """Whether a note carries characters that render invisibly.

    Path, title and summary reach the request index, so they reject every
    format character (Unicode category Cf). The body rejects only the hidden
    controls above.
    """
    for value in (request.path, request.title, request.summary):
        if any(unicodedata.category(character) == "Cf" for character in value):
            return True
    return any(ord(character) in _HIDDEN_BODY_CHARACTERS for character in request.content or "")


def _split_budget(total: int, needs: list[int], shares: list[float]) -> list[int]:
    """Give each scope its share, then pass unused room to scopes that need more."""
    allocated = [min(need, int(total * share)) for need, share in zip(needs, shares)]
    spare = total - sum(allocated)
    for index, need in enumerate(needs):
        extra = min(spare, need - allocated[index])
        allocated[index] += extra
        spare -= extra
    return allocated


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

    def list(
        self,
        request: MemoryListRequest,
        *,
        for_use: bool = False,
        order: Literal["path", "oldest"] = "path",
    ) -> MemoryState:
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
                order=order,
            )
            return MemoryState(
                scopes=scopes,
                documents=documents,
                next_cursor=cursor,
                search_limited=limited,
            )

    @staticmethod
    def _render_index(documents: list[MemoryDocument], total: int) -> str:
        """The full MEMORY.md: the first 200 lines or 25 KB of ordered notes."""
        return _render_index_text(
            documents, total, _IndexBudget(lines=INDEX_MAX_LINES, bytes=INDEX_MAX_BYTES)
        )[0]

    def _index_documents(
        self, connection: sqlite3.Connection, scope_id: str
    ) -> tuple[list[MemoryDocument], int]:
        """Notes in index order and the scope's active note count.

        Corrections and personal preferences come first because they apply to
        most tasks; within a type the most recently changed note comes first.
        The order only uses write-time fields, so reading notes never changes
        the index text or the request prefix built from it.
        """
        total = connection.execute(
            "SELECT COUNT(*) FROM memory_documents WHERE scope_id=? AND status='active' AND archived=0",
            (scope_id,),
        ).fetchone()[0]
        order = " ".join(
            f"WHEN '{kind}' THEN {index}" for index, kind in enumerate(_INDEX_KINDS)
        )
        rows = connection.execute(
            f"""SELECT d.* FROM memory_documents d
                JOIN memory_revisions r ON r.document_id=d.id AND r.revision=d.current_revision
                WHERE d.scope_id=? AND d.status='active' AND d.archived=0
                ORDER BY CASE r.kind {order} ELSE {len(_INDEX_KINDS)} END, d.updated_at DESC, d.path
                LIMIT ?""",
            (scope_id, INDEX_MAX_LINES),
        ).fetchall()
        documents = []
        for row in rows:
            try:
                documents.append(self.repository.document(connection, row)[0])
            except MemoryRejected:
                total -= 1
                continue
        return documents, total

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
                documents, total = self._index_documents(connection, scope.id)
                body, revision = (
                    self._render_index(documents, total),
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
        if _unsafe_text(request):
            raise MemoryRejected("memory_unsafe_text")
        try:
            value = request.model_dump(mode="json")
            if default_scanner().scan_json(value) != value:
                raise MemoryRejected("memory_sensitive_content")
        except SensitiveScanError:
            raise MemoryRejected("memory_sensitive_content") from None
        with self.database.transaction() as connection:
            return self._state(connection, request, automatic)[3]

    def edit_base(
        self,
        run_id: str,
        scope_id: str,
        path: str,
        *,
        item_id: str,
        require_read: bool = True,
    ) -> tuple[int, MemoryKind]:
        """Use Run read/write evidence, or a private CAS base for explicit off-mode replacement.

        Appends and exact replacements are merged by Runtime against the
        current version, so they take that version without a prior read.
        """
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
            if not require_read or self.repository.scope(connection, scope_id).mode == "off":
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

    def current_body(self, scope_id: str, path: str) -> str | None:
        """The current note body for a Runtime-side merge; never sent to the model."""
        with self.database.lock:
            connection = self.database.connection()
            row = self.repository.row(connection, scope_id, path)
            return None if row is None else self.repository.document(connection, row)[1]

    def replayed(self, operation_id: str) -> MemoryActionResult | None:
        """The stored result of an already published operation."""
        with self.database.lock:
            row = (
                self.database.connection()
                .execute(
                    "SELECT result_json FROM memory_actions WHERE operation_id=?",
                    (operation_id,),
                )
                .fetchone()
            )
        return None if row is None else MemoryActionResult.model_validate_json(row[0])

    def similar_notes(
        self, scope_id: str, path: str, *, limit: int = 3
    ) -> list[MemorySimilarNote]:
        """Active notes whose path, title and summary resemble one note.

        A new note is saved first and the model is shown these candidates, so
        it can merge a duplicate and archive the extra note. RapidFuzz compares
        characters, which also works for unspaced Chinese summaries.
        """
        with self.database.lock:
            rows = (
                self.database.connection()
                .execute(
                    """SELECT d.path,r.title,r.summary FROM memory_documents d
                       JOIN memory_revisions r ON r.document_id=d.id AND r.revision=d.current_revision
                       WHERE d.scope_id=? AND d.status='active' AND d.archived=0
                       ORDER BY d.updated_at DESC LIMIT ?""",
                    (scope_id, SIMILAR_CANDIDATES + 1),
                )
                .fetchall()
            )
        target = next((row for row in rows if row["path"] == path), None)
        if target is None:
            return []

        def text(row: sqlite3.Row) -> str:
            return " ".join((row["path"].removesuffix(".md"), row["title"], row["summary"]))

        scored = sorted(
            (
                (WRatio(text(target), text(row), processor=default_process), row)
                for row in rows
                if row["path"] != path
            ),
            key=lambda value: (-value[0], value[1]["path"]),
        )
        return [
            MemorySimilarNote(path=row["path"], summary=row["summary"])
            for score, row in scored[:limit]
            if score >= SIMILAR_THRESHOLD
        ]

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
                    documents, total = self._index_documents(connection, scope.id)
                    index_ref = prepared.enter_context(
                        self.prepared(scope.id, self._render_index(documents, total))
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
                documents, total = self._index_documents(connection, selected.id)
                files.append(
                    MemoryExportFile(
                        path="MEMORY.md",
                        content=self._render_index(documents, total),
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

    @staticmethod
    def _scope_heading(scope: MemoryScope) -> str:
        label = "global" if scope.kind == "global" else "current"
        return f'\nScope "{label}" — MEMORY.md:\n'

    def _scope_indexes(
        self,
        connection: sqlite3.Connection,
        session_id: str,
        *,
        available_tokens: int | None,
    ) -> list[_ScopeIndex]:
        """Fit each readable scope's MEMORY.md into one shared request budget.

        The current project comes first. With both scopes present the global
        scope keeps 40% of the lines, bytes and tokens, so project notes cannot
        push personal preferences out; room one scope leaves unused goes to the
        other. A token cap of 10% of the usable input applies only when the
        line and byte limits would exceed it on a small model window.
        """
        access = self.access(session_id)
        loaded = []
        for scope in reversed(self.repository.scopes(connection, session_id)):
            if not access.available or scope.mode == "off":
                continue
            documents, total = self._index_documents(connection, scope.id)
            if total:
                loaded.append((scope, documents, total))
        if not loaded:
            return []
        full = [
            _render_index_text(
                documents, total, _IndexBudget(lines=INDEX_MAX_LINES, bytes=INDEX_MAX_BYTES)
            )[0]
            for _, documents, total in loaded
        ]
        headings = [self._scope_heading(scope) for scope, _, _ in loaded]
        shares = (
            [1.0]
            if len(loaded) == 1
            else [
                INDEX_GLOBAL_SHARE if scope.kind == "global" else 1 - INDEX_GLOBAL_SHARE
                for scope, _, _ in loaded
            ]
        )
        lines = _split_budget(
            INDEX_MAX_LINES,
            [text.count("\n") + _HEADING_LINES for text in full],
            shares,
        )
        sizes = _split_budget(
            INDEX_MAX_BYTES,
            [len((heading + text).encode()) for heading, text in zip(headings, full)],
            shares,
        )
        tokens: list[int | None] = [None] * len(loaded)
        if available_tokens is not None:
            cap = max(0, int(available_tokens * INDEX_WINDOW_SHARE))
            needs = [_line_tokens(heading + text) for heading, text in zip(headings, full)]
            if sum(needs) > cap:
                if cap < 2 * _INDEX_FOOTER_TOKENS:
                    return []
                tokens = list(_split_budget(cap, needs, shares))
        indexes = []
        for (scope, documents, total), heading, line_limit, byte_limit, token_limit in zip(
            loaded, headings, lines, sizes, tokens
        ):
            text, shown = _render_index_text(
                documents,
                total,
                _IndexBudget(
                    lines=max(0, line_limit - _HEADING_LINES),
                    bytes=max(0, byte_limit - len(heading.encode())),
                    tokens=None
                    if token_limit is None
                    else max(0, token_limit - _line_tokens(heading)),
                ),
            )
            indexes.append(_ScopeIndex(scope, documents, total, text, shown))
        return indexes

    def index_hidden(self, session_id: str, scope_id: str) -> int:
        """Notes of one scope that the request index no longer lists."""
        with self.database.transaction() as connection:
            for index in self._scope_indexes(connection, session_id, available_tokens=None):
                if index.scope.id == scope_id:
                    return index.total - index.shown
        return 0

    def project(
        self, session_id: str, available_tokens: int, *, run_id: str
    ) -> MemoryProjection:
        del run_id
        with self.database.transaction() as connection:
            text, refs, generations, epochs = "", [], {}, {}
            for index in self._scope_indexes(
                connection, session_id, available_tokens=available_tokens
            ):
                scope = index.scope
                text += self._scope_heading(scope) + index.text
                epochs[scope.id], generations[scope.id] = (
                    scope.privacy_epoch,
                    scope.generation,
                )
                refs.extend(
                    MemoryProjectionRef(
                        entry_id=document.id, revision=document.revision
                    )
                    for document in index.documents
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
        read_document_id: str | None = None,
    ) -> None:
        del result, retrieval
        with self.database.transaction() as connection:
            if read_document_id is not None:
                # Usage only orders the oldest-first listing; it never changes
                # the note version, generation or the rendered index.
                connection.execute(
                    "UPDATE memory_documents SET last_read_at=?,read_count=read_count+1 WHERE id=?",
                    (now_ms(), read_document_id),
                )
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

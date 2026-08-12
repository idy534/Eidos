"""Typed SQLite persistence for immutable repository-intelligence generations.

Repository Inventory and Tree-sitter output are derived facts, but they must
survive a Runtime restart so Context construction can select one complete,
identity-bound generation.  This module deliberately owns only those facts;
it neither scans a workspace nor ranks retrieval results.
"""

from __future__ import annotations

from enum import StrEnum
import hashlib
import json
from pathlib import Path
import sqlite3
import stat
import threading
import time
from typing import TypeVar

from pydantic import Field, ValidationError, model_validator

from eidos_runtime.db.database import Database, Repository
from eidos_runtime.models import EidosFrozenStrictModel, JsonSafeInt
from eidos_runtime.persistence.conversion import RowReader, RowValues
from eidos_runtime.persistence.errors import PersistenceCorruptionError
from eidos_runtime.repo_intelligence.index import (
    CodeChunk,
    Import,
    ParseDiagnostic,
    ParsedFile,
    Reference,
    RepositoryIndexSnapshot,
    Symbol,
    SymbolKind,
)
from eidos_runtime.repo_intelligence.inventory import (
    DirectoryRecord,
    FileRecord,
    FileType,
    GitStatusClassification,
    InventoryDiagnostic,
    RepositoryInventory,
    VerificationState,
)
from eidos_runtime.repo_intelligence.map import RepositoryMap


ModelT = TypeVar("ModelT", bound=EidosFrozenStrictModel)


class RepositorySnapshotStatus(StrEnum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"


class RepositoryWorkspaceIdentity(EidosFrozenStrictModel):
    """The workspace identity a repository generation was verified against."""

    root: str = Field(min_length=1)
    device: int
    inode: int
    owner: int

    @classmethod
    def from_root(cls, root: Path) -> "RepositoryWorkspaceIdentity":
        canonical = root.resolve(strict=True)
        metadata = canonical.stat()
        if not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("repository root must be a directory")
        return cls(
            root=str(canonical),
            device=metadata.st_dev,
            inode=metadata.st_ino,
            owner=metadata.st_uid,
        )

    @property
    def repository_id(self) -> str:
        """Keep the established repository-id derivation wire-compatible.

        The identity itself remains a separate persisted equality boundary: a
        different directory mounted at the same path cannot reuse a previous
        complete generation.
        """

        return hashlib.sha256(self.root.encode("utf-8")).hexdigest()


class RepositoryGrammarVersion(EidosFrozenStrictModel):
    language: str = Field(min_length=1)
    parser_version: str = Field(min_length=1)


class RepositoryFtsDocument(EidosFrozenStrictModel):
    """One persisted FTS5 row, returned without leaking SQLite rows."""

    index_snapshot_id: str = Field(min_length=1)
    record_id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    symbol: str | None = None
    body: str
    start_line: int = Field(ge=0)
    end_line: int = Field(ge=0)
    file_hash: str | None = None


class RepositoryFtsMatch(EidosFrozenStrictModel):
    document: RepositoryFtsDocument
    bm25: float


class RepositoryIntelligenceSnapshot(EidosFrozenStrictModel):
    """One normalized, immutable Inventory/Index/Map generation."""

    snapshot_id: str = Field(min_length=1)
    repository_id: str = Field(min_length=1)
    workspace_identity: RepositoryWorkspaceIdentity
    inventory_generation: int = Field(ge=0)
    index_generation: int | None = Field(default=None, ge=0)
    inventory_snapshot_id: str = Field(min_length=1)
    inventory_snapshot_hash: str = Field(min_length=64, max_length=64)
    index_snapshot_id: str | None = None
    index_snapshot_hash: str | None = Field(default=None, min_length=64, max_length=64)
    grammar_versions: tuple[RepositoryGrammarVersion, ...] = ()
    status: RepositorySnapshotStatus
    complete: bool
    created_at_ms: JsonSafeInt
    inventory: RepositoryInventory
    index: RepositoryIndexSnapshot | None = None
    repository_map: RepositoryMap | None = None

    @model_validator(mode="after")
    def verify_generation(self) -> "RepositoryIntelligenceSnapshot":
        if self.repository_id != self.workspace_identity.repository_id:
            raise ValueError("repository identity does not match repository id")
        if (
            self.inventory.repository_id != self.repository_id
            or self.inventory.generation != self.inventory_generation
            or self.inventory.snapshot_id != self.inventory_snapshot_id
            or self.inventory.snapshot_hash != self.inventory_snapshot_hash
        ):
            raise ValueError("inventory does not match repository generation")
        if self.complete:
            if (
                self.status is not RepositorySnapshotStatus.COMPLETE
                or self.index is None
                or self.repository_map is None
            ):
                raise ValueError(
                    "complete repository generation requires index and map"
                )
            if (
                self.index_generation != self.index.index_generation
                or self.index_snapshot_id != self.index.snapshot_id
                or self.index_snapshot_hash != self.index.snapshot_hash
                or self.index.inventory_snapshot_id != self.inventory_snapshot_id
            ):
                raise ValueError("index does not match repository generation")
            if (
                self.repository_map.repository_id != self.repository_id
                or self.repository_map.inventory_snapshot_id
                != self.inventory_snapshot_id
            ):
                raise ValueError("map does not match repository generation")
        elif self.status is not RepositorySnapshotStatus.INCOMPLETE:
            raise ValueError("incomplete repository generation has invalid status")
        return self


class RepositoryIndexStatus(EidosFrozenStrictModel):
    """Typed startup/reconciliation status without a second mutable authority."""

    repository_id: str = Field(min_length=1)
    workspace_identity: RepositoryWorkspaceIdentity
    snapshot_id: str | None = None
    inventory_generation: int | None = Field(default=None, ge=0)
    index_generation: int | None = Field(default=None, ge=0)
    complete: bool
    reconciliation_required: bool


class RepositoryIntelligenceRepository(Repository):
    """Persists complete generations and keeps incomplete candidates non-authoritative."""

    def __init__(self, database: Database) -> None:
        super().__init__(database)

    def commit_complete(
        self,
        inventory: RepositoryInventory,
        index: RepositoryIndexSnapshot,
        repository_map: RepositoryMap,
        workspace_identity: RepositoryWorkspaceIdentity,
    ) -> RepositoryIntelligenceSnapshot:
        if not inventory.complete or not index.complete:
            raise ValueError("complete inventory and index are required")
        _validate_matching_generation(
            inventory, index, repository_map, workspace_identity
        )
        snapshot = _snapshot_from_parts(
            inventory,
            index,
            repository_map,
            workspace_identity,
            complete=True,
        )
        return self._write(
            lambda connection: self._persist_snapshot(connection, snapshot)
        )

    def record_incomplete(
        self,
        inventory: RepositoryInventory,
        index: RepositoryIndexSnapshot | None,
        workspace_identity: RepositoryWorkspaceIdentity,
    ) -> RepositoryIntelligenceSnapshot:
        if inventory.complete and (index is None or index.complete):
            raise ValueError("complete generation must use commit_complete")
        if inventory.repository_id != workspace_identity.repository_id:
            raise ValueError("inventory workspace identity does not match")
        if index is not None:
            if index.repository_id != inventory.repository_id:
                raise ValueError("index repository does not match inventory")
            if index.inventory_snapshot_id != inventory.snapshot_id:
                raise ValueError("index inventory does not match")
        snapshot = _snapshot_from_parts(
            inventory,
            index,
            None,
            workspace_identity,
            complete=False,
        )
        return self._write(
            lambda connection: self._persist_snapshot(connection, snapshot)
        )

    def read_latest_complete(
        self,
        repository_id: str,
        workspace_identity: RepositoryWorkspaceIdentity,
    ) -> RepositoryIntelligenceSnapshot | None:
        if repository_id != workspace_identity.repository_id:
            raise ValueError("repository id does not match workspace identity")
        with self.lock:
            connection = self._connection()
            row = connection.execute(
                """
                SELECT * FROM repository_snapshots
                WHERE repository_id = ?
                  AND workspace_root = ?
                  AND workspace_dev = ?
                  AND workspace_inode = ?
                  AND workspace_uid = ?
                  AND complete = 1
                  AND repository_map_json IS NOT NULL
                ORDER BY creation_seq DESC
                LIMIT 1
                """,
                (
                    repository_id,
                    workspace_identity.root,
                    workspace_identity.device,
                    workspace_identity.inode,
                    workspace_identity.owner,
                ),
            ).fetchone()
            return (
                _snapshot_from_row(connection, row)
                if row is not None
                else None
            )

    def read_status(
        self, workspace_identity: RepositoryWorkspaceIdentity
    ) -> RepositoryIndexStatus:
        with self.lock:
            connection = self._connection()
            complete_row = connection.execute(
                """
                SELECT * FROM repository_snapshots
                WHERE repository_id = ?
                  AND workspace_root = ?
                  AND workspace_dev = ?
                  AND workspace_inode = ?
                  AND workspace_uid = ?
                  AND complete = 1
                  AND repository_map_json IS NOT NULL
                ORDER BY creation_seq DESC LIMIT 1
                """,
                _identity_parameters(workspace_identity),
            ).fetchone()
            latest_row = connection.execute(
                """
                SELECT * FROM repository_snapshots
                WHERE repository_id = ?
                  AND workspace_root = ?
                  AND workspace_dev = ?
                  AND workspace_inode = ?
                  AND workspace_uid = ?
                ORDER BY creation_seq DESC LIMIT 1
                """,
                _identity_parameters(workspace_identity),
            ).fetchone()
        if complete_row is None:
            return RepositoryIndexStatus(
                repository_id=workspace_identity.repository_id,
                workspace_identity=workspace_identity,
                complete=False,
                reconciliation_required=True,
            )
        complete = _header_from_row(complete_row)
        return RepositoryIndexStatus(
            repository_id=workspace_identity.repository_id,
            workspace_identity=workspace_identity,
            snapshot_id=complete.snapshot_id,
            inventory_generation=complete.inventory_generation,
            index_generation=complete.index_generation,
            complete=True,
            reconciliation_required=(
                latest_row is not None
                and _header_from_row(latest_row).snapshot_id != complete.snapshot_id
            ),
        )

    def list_fts_documents(
        self, index_snapshot_id: str
    ) -> tuple[RepositoryFtsDocument, ...]:
        with self.lock:
            rows = self._connection().execute(
                """
                SELECT index_snapshot_id, record_id, path, kind, symbol, body,
                       start_line, end_line, file_hash
                FROM repository_fts
                WHERE index_snapshot_id = ?
                ORDER BY path COLLATE BINARY, kind COLLATE BINARY,
                         start_line, record_id COLLATE BINARY
                """,
                (index_snapshot_id,),
            ).fetchall()
        return tuple(_fts_document_from_row(row) for row in rows)

    def query_fts_bm25(
        self,
        index_snapshot_id: str,
        text: str,
        *,
        deadline_ms: int,
        cancel: threading.Event | None = None,
        limit: int = 500,
    ) -> tuple[RepositoryFtsMatch, ...]:
        tokens = [token for token in text.replace("/", " ").split() if token]
        if not tokens:
            return ()
        match = " OR ".join(f'"{token.replace(chr(34), "")}"' for token in tokens)
        deadline = time.monotonic() + deadline_ms / 1000
        cancel = cancel or threading.Event()
        with self.lock:
            connection = self._connection()

            def interrupted() -> int:
                return int(cancel.is_set() or time.monotonic() >= deadline)

            connection.set_progress_handler(interrupted, 1_000)
            try:
                rows = connection.execute(
                    """
                    SELECT index_snapshot_id, record_id, path, kind, symbol,
                           body, start_line, end_line, file_hash,
                           bm25(repository_fts) AS rank
                    FROM repository_fts
                    WHERE repository_fts MATCH ? AND index_snapshot_id = ?
                    ORDER BY rank, path COLLATE BINARY, record_id COLLATE BINARY
                    LIMIT ?
                    """,
                    (match, index_snapshot_id, limit),
                ).fetchall()
            finally:
                connection.set_progress_handler(None, 0)
        return tuple(RepositoryFtsMatch(
            document=_fts_document_from_row(row),
            bm25=float(row["rank"]),
        ) for row in rows)

    def exact_symbol_lookup(
        self, index_snapshot_id: str, symbol: str
    ) -> tuple[RepositoryFtsDocument, ...]:
        return self._query_documents(
            "index_snapshot_id = ? AND kind = 'symbol' AND symbol = ?",
            (index_snapshot_id, symbol),
        )

    def definition_lookup(
        self, index_snapshot_id: str, symbol: str
    ) -> tuple[RepositoryFtsDocument, ...]:
        return self.exact_symbol_lookup(index_snapshot_id, symbol)

    def path_lookup(
        self, index_snapshot_id: str, path: str
    ) -> tuple[RepositoryFtsDocument, ...]:
        return self._query_documents(
            "index_snapshot_id = ? AND path = ?", (index_snapshot_id, path)
        )

    def import_relationship_lookup(
        self, index_snapshot_id: str, name: str
    ) -> tuple[RepositoryFtsDocument, ...]:
        return self._relationship_documents(
            index_snapshot_id, "repository_imports", "imported_name", name
        )

    def reference_relationship_lookup(
        self, index_snapshot_id: str, name: str
    ) -> tuple[RepositoryFtsDocument, ...]:
        return self._relationship_documents(
            index_snapshot_id, "repository_references", "name", name
        )

    def test_source_relationship_lookup(
        self, index_snapshot_id: str, path: str
    ) -> tuple[RepositoryFtsDocument, ...]:
        stem = Path(path).stem.removeprefix("test_").removesuffix("_test")
        with self.lock:
            rows = self._connection().execute(
                """
                SELECT index_snapshot_id, record_id, path, kind, symbol, body,
                       start_line, end_line, file_hash
                FROM repository_fts
                WHERE index_snapshot_id = ? AND path != ?
                  AND (path LIKE ? OR path LIKE ?)
                ORDER BY path COLLATE BINARY, record_id COLLATE BINARY
                """,
                (index_snapshot_id, path, f"%/{stem}_test.%", f"%/test_{stem}.%"),
            ).fetchall()
        return tuple(_fts_document_from_row(row) for row in rows)

    def _query_documents(
        self, where: str, parameters: tuple[object, ...]
    ) -> tuple[RepositoryFtsDocument, ...]:
        with self.lock:
            rows = self._connection().execute(
                "SELECT index_snapshot_id, record_id, path, kind, symbol, body, "
                "start_line, end_line, file_hash FROM repository_fts WHERE "
                + where
                + " ORDER BY path COLLATE BINARY, record_id COLLATE BINARY",
                parameters,
            ).fetchall()
        return tuple(_fts_document_from_row(row) for row in rows)

    def _relationship_documents(
        self, index_snapshot_id: str, table: str, column: str, value: str
    ) -> tuple[RepositoryFtsDocument, ...]:
        if table not in {"repository_imports", "repository_references"}:
            raise ValueError("unsupported relationship table")
        if column not in {"imported_name", "name"}:
            raise ValueError("unsupported relationship column")
        with self.lock:
            rows = self._connection().execute(
                f"""
                SELECT f.index_snapshot_id, f.record_id, f.path, f.kind,
                       f.symbol, f.body, f.start_line, f.end_line, f.file_hash
                FROM repository_fts AS f
                JOIN {table} AS relationship ON relationship.path = f.path
                WHERE f.index_snapshot_id = ?
                  AND relationship.repository_index_generation_id = ?
                  AND relationship.{column} LIKE ?
                ORDER BY f.path COLLATE BINARY, f.record_id COLLATE BINARY
                """,
                (index_snapshot_id, index_snapshot_id, f"%{value}%"),
            ).fetchall()
        return tuple(_fts_document_from_row(row) for row in rows)

    def _persist_snapshot(
        self,
        connection: sqlite3.Connection,
        snapshot: RepositoryIntelligenceSnapshot,
    ) -> RepositoryIntelligenceSnapshot:
        existing = connection.execute(
            "SELECT * FROM repository_snapshots WHERE id = ?",
            (snapshot.snapshot_id,),
        ).fetchone()
        if existing is not None:
            return _snapshot_from_row(connection, existing)
        connection.execute(
            """
            INSERT INTO repository_snapshots (
                id, repository_id, workspace_root, workspace_dev, workspace_inode,
                workspace_uid, inventory_generation, index_generation,
                inventory_snapshot_id, inventory_snapshot_hash, index_snapshot_id,
                index_snapshot_hash, repository_map_json, grammar_versions_json,
                status, complete, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot.snapshot_id,
                snapshot.repository_id,
                snapshot.workspace_identity.root,
                snapshot.workspace_identity.device,
                snapshot.workspace_identity.inode,
                snapshot.workspace_identity.owner,
                snapshot.inventory_generation,
                snapshot.index_generation,
                snapshot.inventory_snapshot_id,
                snapshot.inventory_snapshot_hash,
                snapshot.index_snapshot_id,
                snapshot.index_snapshot_hash,
                (
                    snapshot.repository_map.model_dump_json()
                    if snapshot.repository_map is not None
                    else None
                ),
                _canonical_json([
                    version.model_dump(mode="json")
                    for version in snapshot.grammar_versions
                ]),
                snapshot.status.value,
                int(snapshot.complete),
                snapshot.created_at_ms,
            ),
        )
        _insert_inventory(connection, snapshot.snapshot_id, snapshot.inventory)
        _insert_inventory_diagnostics(
            connection, snapshot.snapshot_id, snapshot.inventory.diagnostics
        )
        if snapshot.index is not None:
            _insert_index_generation(connection, snapshot)
            if snapshot.complete:
                _insert_fts_documents(
                    connection, _fts_documents(snapshot.index, snapshot.inventory)
                )
            _insert_index_facts(connection, snapshot.snapshot_id, snapshot.index)
        return snapshot


def _validate_matching_generation(
    inventory: RepositoryInventory,
    index: RepositoryIndexSnapshot,
    repository_map: RepositoryMap,
    workspace_identity: RepositoryWorkspaceIdentity,
) -> None:
    if (
        inventory.repository_id != workspace_identity.repository_id
        or inventory.repository_id != index.repository_id
        or inventory.snapshot_id != index.inventory_snapshot_id
        or inventory.generation != index.inventory_generation
        or repository_map.repository_id != inventory.repository_id
        or repository_map.inventory_snapshot_id != inventory.snapshot_id
    ):
        raise ValueError("repository generations do not match workspace identity")


def _snapshot_from_parts(
    inventory: RepositoryInventory,
    index: RepositoryIndexSnapshot | None,
    repository_map: RepositoryMap | None,
    workspace_identity: RepositoryWorkspaceIdentity,
    *,
    complete: bool,
) -> RepositoryIntelligenceSnapshot:
    grammar_versions = _grammar_versions(index)
    payload = {
        "repository_id": inventory.repository_id,
        "workspace_identity": workspace_identity.model_dump(mode="json"),
        "inventory_snapshot_id": inventory.snapshot_id,
        "inventory_snapshot_hash": inventory.snapshot_hash,
        "index_snapshot_id": index.snapshot_id if index is not None else None,
        "index_snapshot_hash": index.snapshot_hash if index is not None else None,
        "repository_map_snapshot_id": (
            repository_map.snapshot_id if repository_map is not None else None
        ),
        "repository_map_snapshot_hash": (
            repository_map.snapshot_hash if repository_map is not None else None
        ),
        "grammar_versions": [item.model_dump(mode="json") for item in grammar_versions],
        "complete": complete,
    }
    digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    return RepositoryIntelligenceSnapshot(
        snapshot_id=f"repository_{digest}",
        repository_id=inventory.repository_id,
        workspace_identity=workspace_identity,
        inventory_generation=inventory.generation,
        index_generation=index.index_generation if index is not None else None,
        inventory_snapshot_id=inventory.snapshot_id,
        inventory_snapshot_hash=inventory.snapshot_hash,
        index_snapshot_id=index.snapshot_id if index is not None else None,
        index_snapshot_hash=index.snapshot_hash if index is not None else None,
        grammar_versions=grammar_versions,
        status=(
            RepositorySnapshotStatus.COMPLETE
            if complete else RepositorySnapshotStatus.INCOMPLETE
        ),
        complete=complete,
        created_at_ms=inventory.created_at_ms,
        inventory=inventory,
        index=index,
        repository_map=repository_map,
    )


def _identity_parameters(
    identity: RepositoryWorkspaceIdentity,
) -> tuple[object, ...]:
    return (
        identity.repository_id,
        identity.root,
        identity.device,
        identity.inode,
        identity.owner,
    )


def _grammar_versions(
    index: RepositoryIndexSnapshot | None,
) -> tuple[RepositoryGrammarVersion, ...]:
    if index is None:
        return ()
    versions = {
        (record.language, record.parser_version)
        for record in index.parsed_files
    }
    return tuple(
        RepositoryGrammarVersion(language=language, parser_version=parser_version)
        for language, parser_version in sorted(versions)
    )


def _insert_inventory(
    connection: sqlite3.Connection,
    snapshot_id: str,
    inventory: RepositoryInventory,
) -> None:
    connection.executemany(
        """
        INSERT INTO repository_files (
            repository_snapshot_id, path, file_type, language, size_bytes,
            mtime_ns, ctime_ns, device, inode, content_hash, encoding,
            generated, vendor, ignored, git_status, generation, verification_state
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                snapshot_id,
                record.path,
                record.file_type.value,
                record.language,
                record.size_bytes,
                record.mtime_ns,
                record.ctime_ns,
                record.device,
                record.inode,
                record.content_hash,
                record.encoding,
                int(record.generated),
                int(record.vendor),
                int(record.ignored),
                record.git_status.value,
                record.generation,
                record.verification_state.value,
            )
            for record in inventory.files
        ],
    )
    connection.executemany(
        """
        INSERT INTO repository_directories (
            repository_snapshot_id, path, device, inode, ignored, generation
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            (
                snapshot_id,
                record.path,
                record.device,
                record.inode,
                int(record.ignored),
                record.generation,
            )
            for record in inventory.directories
        ],
    )


def _insert_inventory_diagnostics(
    connection: sqlite3.Connection,
    snapshot_id: str,
    diagnostics: tuple[InventoryDiagnostic, ...],
) -> None:
    connection.executemany(
        """
        INSERT INTO repository_diagnostics (
            repository_snapshot_id, repository_index_generation_id, source, path,
            code, message, start_line, inventory_generation, index_generation
        ) VALUES (?, NULL, 'inventory', ?, ?, ?, 0, ?, 0)
        """,
        [
            (snapshot_id, item.path, item.code, item.message, 0)
            for item in diagnostics
        ],
    )


def _insert_index_generation(
    connection: sqlite3.Connection,
    snapshot: RepositoryIntelligenceSnapshot,
) -> None:
    index = snapshot.index
    assert index is not None
    connection.execute(
        """
        INSERT INTO repository_index_generations (
            id, repository_snapshot_id, repository_id, inventory_snapshot_id,
            inventory_generation, index_generation, snapshot_hash,
            parser_versions_json, complete, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            index.snapshot_id,
            snapshot.snapshot_id,
            index.repository_id,
            index.inventory_snapshot_id,
            index.inventory_generation,
            index.index_generation,
            index.snapshot_hash,
            _canonical_json([
                version.model_dump(mode="json")
                for version in snapshot.grammar_versions
            ]),
            int(index.complete),
            index.created_at_ms,
        ),
    )


def _insert_index_facts(
    connection: sqlite3.Connection,
    snapshot_id: str,
    index: RepositoryIndexSnapshot,
) -> None:
    index_id = index.snapshot_id
    connection.executemany(
        """
        INSERT INTO repository_parsed_files (
            repository_index_generation_id, path, file_content_hash,
            inventory_generation, index_generation, language, parser_version,
            byte_length, has_errors
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                index_id,
                item.path,
                item.file_content_hash,
                item.inventory_generation,
                item.index_generation,
                item.language,
                item.parser_version,
                item.byte_length,
                int(item.has_errors),
            )
            for item in index.parsed_files
        ],
    )
    connection.executemany(
        """
        INSERT INTO repository_symbols (
            repository_index_generation_id, id, path, name, kind, scope,
            start_line, start_column, end_line, end_column, file_content_hash,
            inventory_generation, index_generation
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                index_id,
                item.id,
                item.path,
                item.name,
                item.kind.value,
                item.scope,
                item.start_line,
                item.start_column,
                item.end_line,
                item.end_column,
                item.file_content_hash,
                item.inventory_generation,
                item.index_generation,
            )
            for item in index.symbols
        ],
    )
    connection.executemany(
        """
        INSERT INTO repository_imports (
            repository_index_generation_id, id, path, imported_name, source,
            start_line, file_content_hash, inventory_generation, index_generation
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                index_id,
                item.id,
                item.path,
                item.imported_name,
                item.source,
                item.start_line,
                item.file_content_hash,
                item.inventory_generation,
                item.index_generation,
            )
            for item in index.imports
        ],
    )
    connection.executemany(
        """
        INSERT INTO repository_references (
            repository_index_generation_id, id, path, name, start_line,
            start_column, file_content_hash, inventory_generation, index_generation
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                index_id,
                item.id,
                item.path,
                item.name,
                item.start_line,
                item.start_column,
                item.file_content_hash,
                item.inventory_generation,
                item.index_generation,
            )
            for item in index.references
        ],
    )
    # FTS insertion intentionally precedes chunks.  All writes share the same
    # transaction; a later index-fact failure proves the FTS row is rolled back.
    connection.executemany(
        """
        INSERT INTO repository_chunks (
            repository_index_generation_id, id, path, start_line, end_line,
            byte_start, byte_end, text, file_content_hash,
            inventory_generation, index_generation
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                index_id,
                item.id,
                item.path,
                item.start_line,
                item.end_line,
                item.byte_start,
                item.byte_end,
                item.text,
                item.file_content_hash,
                item.inventory_generation,
                item.index_generation,
            )
            for item in index.chunks
        ],
    )
    connection.executemany(
        """
        INSERT INTO repository_diagnostics (
            repository_snapshot_id, repository_index_generation_id, source, path,
            code, message, start_line, inventory_generation, index_generation
        ) VALUES (?, ?, 'index', ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                snapshot_id,
                index_id,
                item.path,
                item.code,
                item.message,
                item.start_line,
                item.inventory_generation,
                item.index_generation,
            )
            for item in index.diagnostics
        ],
    )


def _fts_documents(
    index: RepositoryIndexSnapshot,
    inventory: RepositoryInventory,
) -> tuple[RepositoryFtsDocument, ...]:
    documents: list[RepositoryFtsDocument] = []
    for record in inventory.files:
        documents.append(RepositoryFtsDocument(
            index_snapshot_id=index.snapshot_id,
            record_id=f"file_{record.path}",
            path=record.path,
            kind="file",
            body=record.path,
            start_line=0,
            end_line=0,
            file_hash=record.content_hash,
        ))
    for record in index.symbols:
        documents.append(RepositoryFtsDocument(
            index_snapshot_id=index.snapshot_id,
            record_id=record.id,
            path=record.path,
            kind="symbol",
            symbol=record.name,
            body=record.name,
            start_line=record.start_line,
            end_line=record.end_line,
            file_hash=record.file_content_hash,
        ))
    for record in index.chunks:
        documents.append(RepositoryFtsDocument(
            index_snapshot_id=index.snapshot_id,
            record_id=record.id,
            path=record.path,
            kind="chunk",
            body=record.text,
            start_line=record.start_line,
            end_line=record.end_line,
            file_hash=record.file_content_hash,
        ))
    return tuple(sorted(
        documents,
        key=lambda item: (
            item.path.encode("utf-8"),
            item.kind,
            item.start_line,
            item.record_id,
        ),
    ))


def _insert_fts_documents(
    connection: sqlite3.Connection,
    documents: tuple[RepositoryFtsDocument, ...],
) -> None:
    connection.executemany(
        """
        INSERT INTO repository_fts (
            index_snapshot_id, record_id, path, symbol, body, kind, start_line,
            end_line, file_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                document.index_snapshot_id,
                document.record_id,
                document.path,
                document.symbol or "",
                document.body,
                document.kind,
                document.start_line,
                document.end_line,
                document.file_hash or "",
            )
            for document in documents
        ],
    )


def _snapshot_from_row(
    connection: sqlite3.Connection,
    row: RowValues,
) -> RepositoryIntelligenceSnapshot:
    header = _header_from_row(row)
    inventory = _inventory_from_rows(connection, header)
    index = _index_from_rows(connection, header)
    repository_map: RepositoryMap | None = None
    if header.repository_map_json is not None:
        try:
            repository_map = RepositoryMap.model_validate_json(
                header.repository_map_json
            )
        except ValidationError as error:
            location = error.errors(include_url=False)[0].get("loc", ())
            field = str(location[0]) if location else None
            raise PersistenceCorruptionError(
                "persistence_record_invalid",
                record="repository_map",
                field=field,
            ) from None
    return _build_model("repository_snapshot", RepositoryIntelligenceSnapshot, {
        "snapshot_id": header.snapshot_id,
        "repository_id": header.repository_id,
        "workspace_identity": header.workspace_identity,
        "inventory_generation": header.inventory_generation,
        "index_generation": header.index_generation,
        "inventory_snapshot_id": header.inventory_snapshot_id,
        "inventory_snapshot_hash": header.inventory_snapshot_hash,
        "index_snapshot_id": header.index_snapshot_id,
        "index_snapshot_hash": header.index_snapshot_hash,
        "grammar_versions": header.grammar_versions,
        "status": header.status,
        "complete": header.complete,
        "created_at_ms": header.created_at_ms,
        "inventory": inventory,
        "index": index,
        "repository_map": repository_map,
    })


class _SnapshotHeader(EidosFrozenStrictModel):
    snapshot_id: str
    repository_id: str
    workspace_identity: RepositoryWorkspaceIdentity
    inventory_generation: int
    index_generation: int | None
    inventory_snapshot_id: str
    inventory_snapshot_hash: str
    index_snapshot_id: str | None
    index_snapshot_hash: str | None
    repository_map_json: str | None
    grammar_versions: tuple[RepositoryGrammarVersion, ...]
    status: RepositorySnapshotStatus
    complete: bool
    created_at_ms: int


def _header_from_row(row: RowValues) -> _SnapshotHeader:
    values = RowReader(row, record="repository_snapshot")
    grammar_versions = _grammar_versions_from_json(
        values.json_text("grammar_versions_json")
    )
    try:
        status = RepositorySnapshotStatus(values.text("status"))
    except ValueError:
        raise PersistenceCorruptionError(
            "persistence_value_invalid",
            record="repository_snapshot",
            field="status",
        ) from None
    return _build_model("repository_snapshot", _SnapshotHeader, {
        "snapshot_id": values.text("id"),
        "repository_id": values.text("repository_id"),
        "workspace_identity": RepositoryWorkspaceIdentity(
            root=values.text("workspace_root"),
            device=values.integer("workspace_dev"),
            inode=values.integer("workspace_inode"),
            owner=values.integer("workspace_uid"),
        ),
        "inventory_generation": values.integer("inventory_generation"),
        "index_generation": values.optional_integer("index_generation"),
        "inventory_snapshot_id": values.text("inventory_snapshot_id"),
        "inventory_snapshot_hash": values.text("inventory_snapshot_hash"),
        "index_snapshot_id": values.optional_text("index_snapshot_id"),
        "index_snapshot_hash": values.optional_text("index_snapshot_hash"),
        "repository_map_json": values.optional_json_text("repository_map_json"),
        "grammar_versions": grammar_versions,
        "status": status,
        "complete": values.boolean("complete"),
        "created_at_ms": values.integer("created_at"),
    })


def _inventory_from_rows(
    connection: sqlite3.Connection,
    header: _SnapshotHeader,
) -> RepositoryInventory:
    rows = connection.execute(
        """
        SELECT * FROM repository_files
        WHERE repository_snapshot_id = ?
        ORDER BY path COLLATE BINARY
        """,
        (header.snapshot_id,),
    ).fetchall()
    files = tuple(_file_from_row(row) for row in rows)
    directory_rows = connection.execute(
        """
        SELECT * FROM repository_directories
        WHERE repository_snapshot_id = ?
        ORDER BY path COLLATE BINARY
        """,
        (header.snapshot_id,),
    ).fetchall()
    directories = tuple(_directory_from_row(row) for row in directory_rows)
    diagnostic_rows = connection.execute(
        """
        SELECT * FROM repository_diagnostics
        WHERE repository_snapshot_id = ? AND source = 'inventory'
        ORDER BY creation_seq ASC
        """,
        (header.snapshot_id,),
    ).fetchall()
    diagnostics = tuple(_inventory_diagnostic_from_row(row) for row in diagnostic_rows)
    return _build_model("repository_inventory", RepositoryInventory, {
        "schema_version": 1,
        "repository_id": header.repository_id,
        "root": header.workspace_identity.root,
        "generation": header.inventory_generation,
        "complete": header.complete,
        "files": files,
        "directories": directories,
        "diagnostics": diagnostics,
        "created_at_ms": header.created_at_ms,
        "snapshot_id": header.inventory_snapshot_id,
        "snapshot_hash": header.inventory_snapshot_hash,
    })


def _index_from_rows(
    connection: sqlite3.Connection,
    header: _SnapshotHeader,
) -> RepositoryIndexSnapshot | None:
    if header.index_snapshot_id is None:
        return None
    row = connection.execute(
        "SELECT * FROM repository_index_generations WHERE id = ?",
        (header.index_snapshot_id,),
    ).fetchone()
    if row is None:
        raise PersistenceCorruptionError(
            "persistence_record_missing",
            record="repository_index_generation",
            field="id",
        )
    values = RowReader(row, record="repository_index_generation")
    index_id = values.text("id")
    if values.text("repository_snapshot_id") != header.snapshot_id:
        raise PersistenceCorruptionError(
            "persistence_value_invalid",
            record="repository_index_generation",
            field="repository_snapshot_id",
        )
    parsed_files = tuple(_parsed_file_from_row(row) for row in connection.execute(
        """
        SELECT * FROM repository_parsed_files
        WHERE repository_index_generation_id = ?
        ORDER BY path COLLATE BINARY
        """,
        (index_id,),
    ).fetchall())
    symbols = tuple(_symbol_from_row(row) for row in connection.execute(
        """
        SELECT * FROM repository_symbols
        WHERE repository_index_generation_id = ?
        ORDER BY id COLLATE BINARY
        """,
        (index_id,),
    ).fetchall())
    imports = tuple(_import_from_row(row) for row in connection.execute(
        """
        SELECT * FROM repository_imports
        WHERE repository_index_generation_id = ?
        ORDER BY id COLLATE BINARY
        """,
        (index_id,),
    ).fetchall())
    references = tuple(_reference_from_row(row) for row in connection.execute(
        """
        SELECT * FROM repository_references
        WHERE repository_index_generation_id = ?
        ORDER BY id COLLATE BINARY
        """,
        (index_id,),
    ).fetchall())
    chunks = tuple(_chunk_from_row(row) for row in connection.execute(
        """
        SELECT * FROM repository_chunks
        WHERE repository_index_generation_id = ?
        ORDER BY id COLLATE BINARY
        """,
        (index_id,),
    ).fetchall())
    diagnostics = tuple(_parse_diagnostic_from_row(row) for row in connection.execute(
        """
        SELECT * FROM repository_diagnostics
        WHERE repository_index_generation_id = ? AND source = 'index'
        ORDER BY path COLLATE BINARY, start_line, code COLLATE BINARY, creation_seq
        """,
        (index_id,),
    ).fetchall())
    return _build_model("repository_index_snapshot", RepositoryIndexSnapshot, {
        "schema_version": 1,
        "repository_id": values.text("repository_id"),
        "inventory_snapshot_id": values.text("inventory_snapshot_id"),
        "inventory_generation": values.integer("inventory_generation"),
        "index_generation": values.integer("index_generation"),
        "complete": values.boolean("complete"),
        "parsed_files": parsed_files,
        "symbols": symbols,
        "imports": imports,
        "references": references,
        "chunks": chunks,
        "diagnostics": diagnostics,
        "created_at_ms": values.integer("created_at"),
        "snapshot_id": index_id,
        "snapshot_hash": values.text("snapshot_hash"),
    })


def _file_from_row(row: RowValues) -> FileRecord:
    values = RowReader(row, record="repository_file")
    return _build_model("repository_file", FileRecord, {
        "path": values.text("path"),
        "file_type": _enum(FileType, values.text("file_type"), "repository_file", "file_type"),
        "language": values.optional_text("language"),
        "size_bytes": values.integer("size_bytes"),
        "mtime_ns": values.integer("mtime_ns"),
        "ctime_ns": values.optional_integer("ctime_ns"),
        "device": values.optional_integer("device"),
        "inode": values.optional_integer("inode"),
        "content_hash": values.optional_text("content_hash"),
        "encoding": values.text("encoding"),
        "generated": values.boolean("generated"),
        "vendor": values.boolean("vendor"),
        "ignored": values.boolean("ignored"),
        "git_status": _enum(GitStatusClassification, values.text("git_status"), "repository_file", "git_status"),
        "generation": values.integer("generation"),
        "verification_state": _enum(VerificationState, values.text("verification_state"), "repository_file", "verification_state"),
    })


def _directory_from_row(row: RowValues) -> DirectoryRecord:
    values = RowReader(row, record="repository_directory")
    return _build_model("repository_directory", DirectoryRecord, {
        "path": values.text("path"),
        "device": values.optional_integer("device"),
        "inode": values.optional_integer("inode"),
        "ignored": values.boolean("ignored"),
        "generation": values.integer("generation"),
    })


def _inventory_diagnostic_from_row(row: RowValues) -> InventoryDiagnostic:
    values = RowReader(row, record="repository_inventory_diagnostic")
    return _build_model("repository_inventory_diagnostic", InventoryDiagnostic, {
        "code": values.text("code"),
        "path": values.text("path"),
        "message": values.text("message"),
        "recoverable": True,
    })


def _parsed_file_from_row(row: RowValues) -> ParsedFile:
    values = RowReader(row, record="repository_parsed_file")
    return _build_model("repository_parsed_file", ParsedFile, {
        "path": values.text("path"),
        "file_content_hash": values.text("file_content_hash"),
        "inventory_generation": values.integer("inventory_generation"),
        "index_generation": values.integer("index_generation"),
        "language": values.text("language"),
        "parser_version": values.text("parser_version"),
        "byte_length": values.integer("byte_length"),
        "has_errors": values.boolean("has_errors"),
    })


def _symbol_from_row(row: RowValues) -> Symbol:
    values = RowReader(row, record="repository_symbol")
    return _build_model("repository_symbol", Symbol, {
        "id": values.text("id"),
        "path": values.text("path"),
        "name": values.text("name"),
        "kind": _enum(SymbolKind, values.text("kind"), "repository_symbol", "kind"),
        "scope": values.text("scope"),
        "start_line": values.integer("start_line"),
        "start_column": values.integer("start_column"),
        "end_line": values.integer("end_line"),
        "end_column": values.integer("end_column"),
        "file_content_hash": values.text("file_content_hash"),
        "inventory_generation": values.integer("inventory_generation"),
        "index_generation": values.integer("index_generation"),
    })


def _import_from_row(row: RowValues) -> Import:
    values = RowReader(row, record="repository_import")
    return _build_model("repository_import", Import, {
        "id": values.text("id"),
        "path": values.text("path"),
        "imported_name": values.text("imported_name"),
        "source": values.optional_text("source"),
        "start_line": values.integer("start_line"),
        "file_content_hash": values.text("file_content_hash"),
        "inventory_generation": values.integer("inventory_generation"),
        "index_generation": values.integer("index_generation"),
    })


def _reference_from_row(row: RowValues) -> Reference:
    values = RowReader(row, record="repository_reference")
    return _build_model("repository_reference", Reference, {
        "id": values.text("id"),
        "path": values.text("path"),
        "name": values.text("name"),
        "start_line": values.integer("start_line"),
        "start_column": values.integer("start_column"),
        "file_content_hash": values.text("file_content_hash"),
        "inventory_generation": values.integer("inventory_generation"),
        "index_generation": values.integer("index_generation"),
    })


def _chunk_from_row(row: RowValues) -> CodeChunk:
    values = RowReader(row, record="repository_chunk")
    return _build_model("repository_chunk", CodeChunk, {
        "id": values.text("id"),
        "path": values.text("path"),
        "start_line": values.integer("start_line"),
        "end_line": values.integer("end_line"),
        "byte_start": values.integer("byte_start"),
        "byte_end": values.integer("byte_end"),
        "text": values.text("text"),
        "file_content_hash": values.text("file_content_hash"),
        "inventory_generation": values.integer("inventory_generation"),
        "index_generation": values.integer("index_generation"),
    })


def _parse_diagnostic_from_row(row: RowValues) -> ParseDiagnostic:
    values = RowReader(row, record="repository_parse_diagnostic")
    return _build_model("repository_parse_diagnostic", ParseDiagnostic, {
        "path": values.text("path"),
        "code": values.text("code"),
        "message": values.text("message"),
        "start_line": values.integer("start_line"),
        "inventory_generation": values.integer("inventory_generation"),
        "index_generation": values.integer("index_generation"),
    })


def _fts_document_from_row(row: RowValues) -> RepositoryFtsDocument:
    values = RowReader(row, record="repository_fts")
    file_hash = values.optional_text("file_hash")
    return _build_model("repository_fts", RepositoryFtsDocument, {
        "index_snapshot_id": values.text("index_snapshot_id"),
        "record_id": values.text("record_id"),
        "path": values.text("path"),
        "kind": values.text("kind"),
        "symbol": values.optional_text("symbol") or None,
        "body": values.text("body"),
        "start_line": values.integer("start_line"),
        "end_line": values.integer("end_line"),
        "file_hash": file_hash or None,
    })


def _grammar_versions_from_json(value: str) -> tuple[RepositoryGrammarVersion, ...]:
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        raise PersistenceCorruptionError(
            "persistence_json_invalid",
            record="repository_snapshot",
            field="grammar_versions_json",
        ) from None
    if not isinstance(decoded, list):
        raise PersistenceCorruptionError(
            "persistence_json_invalid",
            record="repository_snapshot",
            field="grammar_versions_json",
        )
    versions: list[RepositoryGrammarVersion] = []
    for item in decoded:
        versions.append(_build_model(
            "repository_grammar_version", RepositoryGrammarVersion, item
        ))
    return tuple(versions)


def _enum(
    enum_type: type[StrEnum],
    value: str,
    record: str,
    field: str,
) -> StrEnum:
    try:
        return enum_type(value)
    except ValueError:
        raise PersistenceCorruptionError(
            "persistence_value_invalid", record=record, field=field
        ) from None


def _build_model(
    record: str,
    factory: type[ModelT],
    values: object,
) -> ModelT:
    try:
        return factory.model_validate(values)
    except ValidationError as error:
        location = error.errors(include_url=False)[0].get("loc", ())
        field = str(location[0]) if location else None
        raise PersistenceCorruptionError(
            "persistence_record_invalid", record=record, field=field
        ) from None


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


__all__ = [
    "RepositoryFtsDocument",
    "RepositoryGrammarVersion",
    "RepositoryIndexStatus",
    "RepositoryIntelligenceRepository",
    "RepositoryIntelligenceSnapshot",
    "RepositorySnapshotStatus",
    "RepositoryWorkspaceIdentity",
]

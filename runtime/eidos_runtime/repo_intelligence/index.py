from __future__ import annotations

from enum import StrEnum
import hashlib
import json
import os
from pathlib import Path
import threading
import time
from importlib.metadata import version
from typing import Final, TypeVar

from pydantic import Field, model_validator
from tree_sitter import Language, Parser
from tree_sitter_go import language as go_language
from tree_sitter_javascript import language as javascript_language
from tree_sitter_python import language as python_language
from tree_sitter_typescript import language_tsx, language_typescript

from eidos_runtime.models import EidosFrozenStrictModel, JsonSafeInt
from eidos_runtime.repo_intelligence.inventory import (
    FileRecord,
    RepositoryInventory,
    read_verified_file,
)


MAX_DIAGNOSTICS: Final = 128
MAX_CHUNKS_PER_FILE: Final = 128
MAX_REFERENCES_PER_FILE: Final = 512
FactT = TypeVar("FactT", bound=EidosFrozenStrictModel)


class IndexCanceled(RuntimeError):
    pass


class IndexError(RuntimeError):
    pass


class SymbolKind(StrEnum):
    FUNCTION = "function"
    CLASS = "class"
    METHOD = "method"
    VARIABLE = "variable"
    TYPE = "type"
    UNKNOWN = "unknown"


class IndexGeneration(EidosFrozenStrictModel):
    repository_id: str
    inventory_generation: int = Field(ge=0)
    index_generation: int = Field(ge=0)
    complete: bool
    created_at_ms: JsonSafeInt


class ParsedFile(EidosFrozenStrictModel):
    path: str
    file_content_hash: str
    inventory_generation: int = Field(ge=0)
    index_generation: int = Field(ge=0)
    language: str
    parser_version: str
    byte_length: int = Field(ge=0)
    has_errors: bool


class Symbol(EidosFrozenStrictModel):
    id: str
    path: str
    name: str
    kind: SymbolKind
    scope: str
    start_line: int = Field(ge=0)
    start_column: int = Field(ge=0)
    end_line: int = Field(ge=0)
    end_column: int = Field(ge=0)
    file_content_hash: str
    inventory_generation: int = Field(ge=0)
    index_generation: int = Field(ge=0)


class Import(EidosFrozenStrictModel):
    id: str
    path: str
    imported_name: str
    source: str | None = None
    start_line: int = Field(ge=0)
    file_content_hash: str
    inventory_generation: int = Field(ge=0)
    index_generation: int = Field(ge=0)


class Reference(EidosFrozenStrictModel):
    id: str
    path: str
    name: str
    start_line: int = Field(ge=0)
    start_column: int = Field(ge=0)
    file_content_hash: str
    inventory_generation: int = Field(ge=0)
    index_generation: int = Field(ge=0)


class CodeChunk(EidosFrozenStrictModel):
    id: str
    path: str
    start_line: int = Field(ge=0)
    end_line: int = Field(ge=0)
    byte_start: int = Field(ge=0)
    byte_end: int = Field(ge=0)
    text: str
    file_content_hash: str
    inventory_generation: int = Field(ge=0)
    index_generation: int = Field(ge=0)


class ParseDiagnostic(EidosFrozenStrictModel):
    path: str
    code: str
    message: str
    start_line: int = Field(ge=0)
    inventory_generation: int = Field(ge=0)
    index_generation: int = Field(ge=0)


class RepositoryIndexSnapshot(EidosFrozenStrictModel):
    schema_version: int = 1
    repository_id: str
    inventory_snapshot_id: str
    inventory_generation: int = Field(ge=0)
    index_generation: int = Field(ge=0)
    complete: bool
    parsed_files: tuple[ParsedFile, ...]
    symbols: tuple[Symbol, ...]
    imports: tuple[Import, ...]
    references: tuple[Reference, ...]
    chunks: tuple[CodeChunk, ...]
    diagnostics: tuple[ParseDiagnostic, ...] = ()
    created_at_ms: JsonSafeInt
    snapshot_id: str
    snapshot_hash: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def verify_hash(self) -> RepositoryIndexSnapshot:
        payload = self.model_dump(
            mode="json",
            exclude={"snapshot_id", "snapshot_hash", "created_at_ms"},
        )
        digest = _hash(payload)
        if digest != self.snapshot_hash or self.snapshot_id != f"index_{digest}":
            raise ValueError("repository index snapshot hash mismatch")
        return self


class RepositoryIndexer:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("index root must be a directory")
        self._generation = 0
        self._last_complete: RepositoryIndexSnapshot | None = None

    @property
    def last_complete(self) -> RepositoryIndexSnapshot | None:
        return self._last_complete

    def restore_generation(self, snapshot: RepositoryIndexSnapshot) -> None:
        if not snapshot.complete or snapshot.repository_id != _repository_id(self.root):
            raise IndexError("index restore snapshot is incompatible")
        if snapshot.index_generation > self._generation:
            self._generation = snapshot.index_generation
            self._last_complete = snapshot

    def restore_generation_floor(self, generation: int) -> None:
        """Advance the counter without accepting a mapless legacy generation."""

        if generation < 0:
            raise ValueError("index generation floor must be non-negative")
        self._generation = max(self._generation, generation)

    def build(
        self,
        inventory: RepositoryInventory,
        *,
        cancel: threading.Event | None = None,
        previous: RepositoryIndexSnapshot | None = None,
    ) -> RepositoryIndexSnapshot:
        if not inventory.complete:
            raise IndexError("complete inventory is required")
        if Path(inventory.root).resolve() != self.root:
            raise IndexError("inventory root does not match index root")
        cancel = cancel or threading.Event()
        next_generation = self._generation + 1
        parsed_files: list[ParsedFile] = []
        symbols: list[Symbol] = []
        imports: list[Import] = []
        references: list[Reference] = []
        chunks: list[CodeChunk] = []
        diagnostics: list[ParseDiagnostic] = []
        complete = True
        reusable_paths: set[str] = set()
        current_files = {record.path: record for record in inventory.files}
        if previous is not None:
            if not previous.complete or previous.repository_id != inventory.repository_id:
                raise IndexError("previous index generation is incompatible")
            reusable_paths = {
                parsed.path
                for parsed in previous.parsed_files
                if (
                    (record := current_files.get(parsed.path)) is not None
                    and record.content_hash == parsed.file_content_hash
                    and record.language == parsed.language
                )
            }
            parsed_files.extend(
                _advance_fact(item, inventory.generation, next_generation)
                for item in previous.parsed_files if item.path in reusable_paths
            )
            symbols.extend(
                _advance_fact(item, inventory.generation, next_generation)
                for item in previous.symbols if item.path in reusable_paths
            )
            imports.extend(
                _advance_fact(item, inventory.generation, next_generation)
                for item in previous.imports if item.path in reusable_paths
            )
            references.extend(
                _advance_fact(item, inventory.generation, next_generation)
                for item in previous.references if item.path in reusable_paths
            )
            chunks.extend(
                _advance_fact(item, inventory.generation, next_generation)
                for item in previous.chunks if item.path in reusable_paths
            )
            diagnostics.extend(
                _advance_fact(item, inventory.generation, next_generation)
                for item in previous.diagnostics if item.path in reusable_paths
            )
        for file_record in inventory.files:
            if cancel.is_set():
                raise IndexCanceled("index_canceled")
            if file_record.language not in _LANGUAGE_FACTORIES:
                continue
            if file_record.path in reusable_paths:
                continue
            try:
                content = self._read_verified(file_record)
            except (OSError, IndexError) as error:
                complete = False
                if len(diagnostics) < MAX_DIAGNOSTICS:
                    diagnostics.append(_diagnostic(
                        file_record, "STALE_FILE", str(error), next_generation
                    ))
                continue
            try:
                parsed = self._parse_file(
                    file_record,
                    content,
                    inventory.generation,
                    next_generation,
                    cancel,
                )
            except IndexCanceled:
                raise
            except Exception as error:
                complete = False
                if len(diagnostics) < MAX_DIAGNOSTICS:
                    diagnostics.append(_diagnostic(
                        file_record, "PARSE_FAILED", type(error).__name__, next_generation
                    ))
                continue
            parsed_files.append(parsed[0])
            symbols.extend(parsed[1])
            imports.extend(parsed[2])
            references.extend(parsed[3])
            chunks.extend(parsed[4])
            diagnostics.extend(parsed[5][:MAX_DIAGNOSTICS - len(diagnostics)])
            # A syntax-error file is a bounded diagnostic.  The generation is
            # complete when every eligible file reached a consistent outcome.
        if cancel.is_set():
            raise IndexCanceled("index_canceled")
        parsed_files.sort(key=lambda record: record.path.encode())
        symbols.sort(key=lambda record: record.id)
        imports.sort(key=lambda record: record.id)
        references.sort(key=lambda record: record.id)
        chunks.sort(key=lambda record: record.id)
        diagnostics.sort(key=lambda record: (record.path.encode(), record.start_line, record.code))
        payload = {
            "schema_version": 1,
            "repository_id": inventory.repository_id,
            "inventory_snapshot_id": inventory.snapshot_id,
            "inventory_generation": inventory.generation,
            "index_generation": next_generation,
            "complete": complete,
            "parsed_files": [item.model_dump(mode="json") for item in parsed_files],
            "symbols": [item.model_dump(mode="json") for item in symbols],
            "imports": [item.model_dump(mode="json") for item in imports],
            "references": [item.model_dump(mode="json") for item in references],
            "chunks": [item.model_dump(mode="json") for item in chunks],
            "diagnostics": [item.model_dump(mode="json") for item in diagnostics],
        }
        digest = _hash(payload)
        snapshot = RepositoryIndexSnapshot(
            schema_version=1,
            repository_id=inventory.repository_id,
            inventory_snapshot_id=inventory.snapshot_id,
            inventory_generation=inventory.generation,
            index_generation=next_generation,
            complete=complete,
            parsed_files=tuple(parsed_files),
            symbols=tuple(symbols),
            imports=tuple(imports),
            references=tuple(references),
            chunks=tuple(chunks),
            diagnostics=tuple(diagnostics),
            created_at_ms=int(time.time() * 1000),
            snapshot_id=f"index_{digest}",
            snapshot_hash=digest,
        )
        self._generation = next_generation
        if complete:
            self._last_complete = snapshot
        return snapshot

    def _read_verified(self, record: FileRecord) -> bytes:
        if record.device is None or record.inode is None:
            raise IndexError("inventory file identity is incomplete")
        root_fd = os.open(
            self.root,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            # Recreate the exact nanosecond identity fields used by the
            # inventory instead of trusting path-based metadata.
            current = os.stat(record.path, dir_fd=root_fd, follow_symlinks=False)
            if (
                current.st_dev != record.device
                or current.st_ino != record.inode
                or current.st_size != record.size_bytes
                or current.st_mtime_ns != record.mtime_ns
            ):
                raise IndexError("file changed after inventory")
            content = read_verified_file(
                root_fd, record.path, current, record.size_bytes
            )
        finally:
            os.close(root_fd)
        digest = hashlib.sha256(content).hexdigest()
        if record.content_hash is None or digest != record.content_hash:
            raise IndexError("file content hash does not match inventory")
        return content

    def _parse_file(self, record: FileRecord, content: bytes, inventory_generation: int, index_generation: int, cancel: threading.Event):
        language = record.language
        assert language is not None
        parser = Parser(Language(_LANGUAGE_FACTORIES[language]()))
        tree = parser.parse(content)
        parsed_file = ParsedFile(
            path=record.path,
            file_content_hash=record.content_hash or "",
            inventory_generation=inventory_generation,
            index_generation=index_generation,
            language=language,
            parser_version=_parser_version(language),
            byte_length=len(content),
            has_errors=tree.root_node.has_error,
        )
        symbols: list[Symbol] = []
        imports: list[Import] = []
        references: list[Reference] = []
        chunks: list[CodeChunk] = []
        diagnostics: list[ParseDiagnostic] = []
        definitions: set[tuple[int, int]] = set()
        nodes = list(_walk(tree.root_node))
        for node in nodes:
            if cancel.is_set():
                raise IndexCanceled("index_canceled")
            if node.type == "ERROR" and len(diagnostics) < MAX_DIAGNOSTICS:
                diagnostics.append(ParseDiagnostic(
                    path=record.path,
                    code="TREE_SITTER_ERROR",
                    message="syntax error",
                    start_line=node.start_point[0],
                    inventory_generation=inventory_generation,
                    index_generation=index_generation,
                ))
            name_node = _definition_name_node(node, language)
            if name_node is not None:
                name = _node_text(name_node, content)
                kind = _symbol_kind(node.type)
                symbol_id = _stable_id("symbol", record.path, name, kind.value, node.start_byte)
                definitions.add((name_node.start_byte, name_node.end_byte))
                symbols.append(Symbol(
                    id=symbol_id,
                    path=record.path,
                    name=name,
                    kind=kind,
                    scope=_scope_for(node, content),
                    start_line=node.start_point[0],
                    start_column=node.start_point[1],
                    end_line=node.end_point[0],
                    end_column=node.end_point[1],
                    file_content_hash=record.content_hash or "",
                    inventory_generation=inventory_generation,
                    index_generation=index_generation,
                ))
            if node.type in {
                "import_statement",
                "import_from_statement",
                "import_declaration",
                "import_clause",
            }:
                imports.append(Import(
                    id=_stable_id("import", record.path, node.start_byte),
                    path=record.path,
                    imported_name=_node_text(node, content)[:512],
                    source=_import_source(node, content),
                    start_line=node.start_point[0],
                    file_content_hash=record.content_hash or "",
                    inventory_generation=inventory_generation,
                    index_generation=index_generation,
                ))
            if (
                node.type == "identifier"
                and (node.start_byte, node.end_byte) not in definitions
                and len(references) < MAX_REFERENCES_PER_FILE
            ):
                references.append(Reference(
                    id=_stable_id("reference", record.path, node.start_byte),
                    path=record.path,
                    name=_node_text(node, content),
                    start_line=node.start_point[0],
                    start_column=node.start_point[1],
                    file_content_hash=record.content_hash or "",
                    inventory_generation=inventory_generation,
                    index_generation=index_generation,
                ))
        if parsed_file.has_errors and not diagnostics:
            diagnostics.append(ParseDiagnostic(
                path=record.path,
                code="TREE_SITTER_ERROR",
                message="syntax error",
                start_line=tree.root_node.start_point[0],
                inventory_generation=inventory_generation,
                index_generation=index_generation,
            ))
        for node in nodes:
            if node.parent is not None and node.parent.type != "module":
                continue
            if node.type in {"function_definition", "class_definition", "function_declaration", "class_declaration", "method_declaration", "type_declaration"}:
                if len(chunks) >= MAX_CHUNKS_PER_FILE:
                    break
                chunks.append(CodeChunk(
                    id=_stable_id("chunk", record.path, node.start_byte, node.end_byte),
                    path=record.path,
                    start_line=node.start_point[0],
                    end_line=node.end_point[0],
                    byte_start=node.start_byte,
                    byte_end=node.end_byte,
                    text=_node_text(node, content)[:64 * 1024],
                    file_content_hash=record.content_hash or "",
                    inventory_generation=inventory_generation,
                    index_generation=index_generation,
                ))
        return parsed_file, symbols, imports, references, chunks, diagnostics


def _walk(node):
    yield node
    for child in node.children:
        yield from _walk(child)


def _node_text(node, content: bytes) -> str:
    return content[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _definition_name_node(node, language: str):
    definition_types = {
        "function_definition", "class_definition", "function_declaration",
        "class_declaration", "method_definition", "method_declaration",
        "type_declaration", "variable_declarator",
    }
    if node.type not in definition_types:
        return None
    field = node.child_by_field_name("name")
    if field is not None:
        return field
    for child in node.children:
        if child.type in {"identifier", "type_identifier"}:
            return child
    return None


def _symbol_kind(node_type: str) -> SymbolKind:
    if "class" in node_type:
        return SymbolKind.CLASS
    if "function" in node_type:
        return SymbolKind.FUNCTION
    if "method" in node_type:
        return SymbolKind.METHOD
    if "type" in node_type:
        return SymbolKind.TYPE
    if "variable" in node_type:
        return SymbolKind.VARIABLE
    return SymbolKind.UNKNOWN


def _scope_for(node, content: bytes) -> str:
    parent = node.parent
    while parent is not None and parent.type != "module":
        if parent.type in {"class_definition", "class_declaration", "class_body"}:
            return _node_text(parent, content).splitlines()[0][:256]
        parent = parent.parent
    return "module"


def _import_source(node, content: bytes) -> str | None:
    source = node.child_by_field_name("source")
    if source is None:
        return None
    return _node_text(source, content)[:512]


def _diagnostic(record: FileRecord, code: str, message: str, generation: int) -> ParseDiagnostic:
    return ParseDiagnostic(
        path=record.path,
        code=code,
        message=message,
        start_line=0,
        inventory_generation=generation,
        index_generation=generation,
    )


def _stable_id(kind: str, *values: object) -> str:
    return f"{kind}_{_hash([kind, *values])}"


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")).hexdigest()


def _repository_id(root: Path) -> str:
    return hashlib.sha256(str(root).encode("utf-8")).hexdigest()


def _parser_version(language: str) -> str:
    grammar_package = {
        "python": "tree-sitter-python",
        "typescript": "tree-sitter-typescript",
        "tsx": "tree-sitter-typescript",
        "javascript": "tree-sitter-javascript",
        "go": "tree-sitter-go",
    }[language]
    return f"tree-sitter={version('tree-sitter')};{grammar_package}={version(grammar_package)}"


def _advance_fact(
    item: FactT, inventory_generation: int, index_generation: int
) -> FactT:
    return item.model_copy(update={
        "inventory_generation": inventory_generation,
        "index_generation": index_generation,
    })


_LANGUAGE_FACTORIES = {
    "python": python_language,
    "typescript": language_typescript,
    "tsx": language_tsx,
    "javascript": javascript_language,
    "go": go_language,
}


__all__ = [
    "CodeChunk",
    "Import",
    "IndexCanceled",
    "IndexGeneration",
    "IndexError",
    "ParseDiagnostic",
    "ParsedFile",
    "Reference",
    "RepositoryIndexSnapshot",
    "RepositoryIndexer",
    "Symbol",
    "SymbolKind",
]

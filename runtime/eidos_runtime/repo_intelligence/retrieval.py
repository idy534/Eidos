from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import threading
import time
from typing import Final

from pydantic import Field, model_validator
from rapidfuzz import fuzz, process

from eidos_runtime.models import EidosFrozenStrictModel, JsonSafeInt
from eidos_runtime.repo_intelligence.index import (
    RepositoryIndexSnapshot,
)
from eidos_runtime.repo_intelligence.inventory import RepositoryInventory
from eidos_runtime.persistence.repository_intelligence import (
    RepositoryFtsDocument,
    RepositoryIntelligenceRepository,
)


RANKING_VERSION: Final = 1
MAX_QUERY_BYTES: Final = 16 * 1024
MAX_FTS_CANDIDATES: Final = 64
MAX_EXACT_CANDIDATES: Final = 32
MAX_RELATION_CANDIDATES: Final = 64
MAX_FUZZY_CANDIDATES: Final = 32
MAX_TOTAL_CANDIDATES: Final = 256


class RepositoryRetrievalQuery(EidosFrozenStrictModel):
    text: str = Field(max_length=MAX_QUERY_BYTES)
    mentioned_paths: tuple[str, ...] = ()
    mentioned_symbols: tuple[str, ...] = ()
    current_diff_paths: tuple[str, ...] = ()
    recently_modified_paths: tuple[str, ...] = ()
    previous_read_paths: tuple[str, ...] = ()
    recent_tool_result_paths: tuple[str, ...] = ()
    rule_paths: tuple[str, ...] = ()
    max_results: int = Field(default=12, ge=1, le=100)
    max_evidence_bytes: int = Field(default=128 * 1024, ge=1, le=1024 * 1024)
    deadline_ms: int = Field(default=500, ge=1, le=10_000)


class RetrievalReason(EidosFrozenStrictModel):
    signal: str
    value: float
    explanation: str


class RetrievalScoreBreakdown(EidosFrozenStrictModel):
    exact_symbol: float = 0.0
    definition_match: float = 0.0
    import_relationship: float = 0.0
    reference_relationship: float = 0.0
    fts_bm25: float = 0.0
    exact_path: float = 0.0
    fuzzy_path: float = 0.0
    fuzzy_symbol: float = 0.0
    current_diff: float = 0.0
    recently_modified: float = 0.0
    previous_read: float = 0.0
    recent_tool_result: float = 0.0
    path_rule: float = 0.0
    language_affinity: float = 0.0
    test_source: float = 0.0
    generated_penalty: float = 0.0
    vendor_penalty: float = 0.0

    @property
    def total(self) -> float:
        return sum(self.model_dump(mode="python").values())


class RepositoryEvidence(EidosFrozenStrictModel):
    id: str
    kind: str
    path: str
    file_hash: str | None
    start_line: int = Field(ge=0)
    end_line: int = Field(ge=0)
    inventory_generation: int = Field(ge=0)
    index_generation: int = Field(ge=0)
    text: str
    retrieval_reasons: tuple[RetrievalReason, ...] = ()


class RepositoryRetrievalCandidate(EidosFrozenStrictModel):
    record_id: str
    path: str
    score: float
    score_breakdown: RetrievalScoreBreakdown
    reasons: tuple[RetrievalReason, ...]
    evidence: tuple[RepositoryEvidence, ...]


class RetrievalSnapshot(EidosFrozenStrictModel):
    ranking_version: int = RANKING_VERSION
    inventory_snapshot_id: str
    index_snapshot_id: str
    query: RepositoryRetrievalQuery
    results: tuple[RepositoryRetrievalCandidate, ...]
    created_at_ms: JsonSafeInt
    snapshot_id: str
    snapshot_hash: str = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def verify_hash(self) -> RetrievalSnapshot:
        payload = self.model_dump(
            mode="json",
            exclude={"snapshot_id", "snapshot_hash", "created_at_ms"},
        )
        digest = _hash(payload)
        if digest != self.snapshot_hash or self.snapshot_id != f"retrieval_{digest}":
            raise ValueError("retrieval snapshot hash mismatch")
        return self


@dataclass(frozen=True)
class _Document:
    record_id: str
    path: str
    kind: str
    text: str
    file_hash: str | None
    start_line: int
    end_line: int


@dataclass
class _Candidate:
    document: _Document
    signals: dict[str, float]


class RepositoryRetriever:
    def __init__(
        self,
        inventory: RepositoryInventory,
        index: RepositoryIndexSnapshot,
        repository: RepositoryIntelligenceRepository,
    ) -> None:
        if not inventory.complete or not index.complete:
            raise ValueError("complete inventory and index generations are required")
        if (
            inventory.repository_id != index.repository_id
            or inventory.generation != index.inventory_generation
            or inventory.snapshot_id != index.inventory_snapshot_id
        ):
            raise ValueError("inventory and index generation mismatch")
        self.inventory = inventory
        self.index = index
        self.repository = repository
        self._file_by_path = {item.path: item for item in inventory.files}
        self._paths = tuple(sorted(self._file_by_path, key=str.encode))
        self._symbol_names = tuple(sorted({item.name for item in index.symbols}))

    def retrieve(
        self,
        query: RepositoryRetrievalQuery,
        *,
        cancel: threading.Event | None = None,
    ) -> RetrievalSnapshot:
        if len(query.text.encode("utf-8")) > MAX_QUERY_BYTES:
            raise ValueError("retrieval query is too large")
        deadline = time.monotonic() + query.deadline_ms / 1000
        cancel = cancel or threading.Event()
        candidates: dict[str, _Candidate] = {}

        def add(
            document: RepositoryFtsDocument,
            signal: str,
            value: float = 1.0,
        ) -> None:
            if len(candidates) >= MAX_TOTAL_CANDIDATES and document.record_id not in candidates:
                return
            candidate = candidates.setdefault(
                document.record_id,
                _Candidate(_document(document), {}),
            )
            candidate.signals[signal] = max(candidate.signals.get(signal, 0.0), value)

        def remaining_ms() -> int:
            return max(0, int((deadline - time.monotonic()) * 1000))

        def active() -> bool:
            return not cancel.is_set() and remaining_ms() > 0

        def add_paths(paths: tuple[str, ...], signal: str) -> None:
            for path in paths[:MAX_EXACT_CANDIDATES]:
                if not active():
                    return
                for document in self.repository.path_lookup(
                    self.index.snapshot_id,
                    path,
                    limit=MAX_EXACT_CANDIDATES,
                    deadline_ms=remaining_ms(),
                    cancel=cancel,
                ):
                    add(document, signal)

        for symbol in query.mentioned_symbols[:MAX_EXACT_CANDIDATES]:
            if not active():
                break
            for document in self.repository.definition_lookup(
                self.index.snapshot_id,
                symbol,
                limit=MAX_EXACT_CANDIDATES,
                deadline_ms=remaining_ms(),
                cancel=cancel,
            ):
                add(document, "exact_symbol")
                add(document, "definition_match")

        add_paths(query.mentioned_paths, "exact_path")
        add_paths(query.current_diff_paths, "current_diff")
        add_paths(query.recently_modified_paths, "recently_modified")
        add_paths(query.previous_read_paths, "previous_read")
        add_paths(query.recent_tool_result_paths, "recent_tool_result")
        add_paths(query.rule_paths, "path_rule")

        for symbol in query.mentioned_symbols[:MAX_EXACT_CANDIDATES]:
            if not active():
                break
            for document in self.repository.import_relationship_lookup(
                self.index.snapshot_id,
                symbol,
                limit=MAX_RELATION_CANDIDATES,
                deadline_ms=remaining_ms(),
                cancel=cancel,
            ):
                add(document, "import_relationship")
            if not active():
                break
            for document in self.repository.reference_relationship_lookup(
                self.index.snapshot_id,
                symbol,
                limit=MAX_RELATION_CANDIDATES,
                deadline_ms=remaining_ms(),
                cancel=cancel,
            ):
                add(document, "reference_relationship")

        for path in query.mentioned_paths[:MAX_EXACT_CANDIDATES]:
            if not active():
                break
            for document in self.repository.test_source_relationship_lookup(
                self.index.snapshot_id,
                path,
                limit=MAX_RELATION_CANDIDATES,
                deadline_ms=remaining_ms(),
                cancel=cancel,
            ):
                add(document, "test_source")

        if active():
            for match in self.repository.query_fts_bm25(
                self.index.snapshot_id,
                query.text,
                deadline_ms=remaining_ms(),
                cancel=cancel,
                limit=MAX_FTS_CANDIDATES,
            ):
                add(match.document, "fts_bm25", 1.0 / (1.0 + abs(match.bm25)))

        if active():
            for selected_path, score, _ in process.extract(
                query.text,
                self._paths,
                scorer=fuzz.WRatio,
                score_cutoff=70,
                limit=MAX_FUZZY_CANDIDATES,
            ):
                if not active():
                    break
                for document in self.repository.path_lookup(
                    self.index.snapshot_id,
                    selected_path,
                    limit=MAX_EXACT_CANDIDATES,
                    deadline_ms=remaining_ms(),
                    cancel=cancel,
                ):
                    add(document, "fuzzy_path", float(score) / 100 * 2)

        if active():
            for selected_symbol, score, _ in process.extract(
                query.text,
                self._symbol_names,
                scorer=fuzz.WRatio,
                score_cutoff=70,
                limit=MAX_FUZZY_CANDIDATES,
            ):
                if not active():
                    break
                for document in self.repository.exact_symbol_lookup(
                    self.index.snapshot_id,
                    selected_symbol,
                    limit=MAX_EXACT_CANDIDATES,
                    deadline_ms=remaining_ms(),
                    cancel=cancel,
                ):
                    add(document, "fuzzy_symbol", float(score) / 100 * 3)

        mentioned_languages = {
            self._file_by_path[path].language
            for path in query.mentioned_paths
            if path in self._file_by_path
            and self._file_by_path[path].language is not None
        }
        ranked: list[RepositoryRetrievalCandidate] = []
        for candidate in candidates.values():
            breakdown = self._score(candidate, query, mentioned_languages)
            if breakdown.total <= 0:
                continue
            reasons = _reasons(breakdown)
            ranked.append(RepositoryRetrievalCandidate(
                record_id=candidate.document.record_id,
                path=candidate.document.path,
                score=breakdown.total,
                score_breakdown=breakdown,
                reasons=reasons,
                evidence=(),
            ))
        ranked.sort(key=lambda item: (-item.score, item.path.encode(), item.record_id.encode()))
        selected: list[RepositoryRetrievalCandidate] = []
        used_bytes = 0
        for candidate in ranked[:query.max_results]:
            document = candidates[candidate.record_id].document
            evidence = RepositoryEvidence(
                id=document.record_id,
                kind=document.kind,
                path=document.path,
                file_hash=document.file_hash,
                start_line=document.start_line,
                end_line=document.end_line,
                inventory_generation=self.inventory.generation,
                index_generation=self.index.index_generation,
                text=document.text,
                retrieval_reasons=candidate.reasons,
            )
            evidence_bytes = len(evidence.text.encode("utf-8"))
            if used_bytes + evidence_bytes > query.max_evidence_bytes:
                remaining = query.max_evidence_bytes - used_bytes
                if remaining <= 0:
                    continue
                evidence = evidence.model_copy(update={
                    "text": _utf8_prefix(evidence.text, remaining),
                })
                evidence_bytes = len(evidence.text.encode("utf-8"))
            used_bytes += evidence_bytes
            selected.append(candidate.model_copy(update={"evidence": (evidence,)}))
        payload = {
            "ranking_version": RANKING_VERSION,
            "inventory_snapshot_id": self.inventory.snapshot_id,
            "index_snapshot_id": self.index.snapshot_id,
            "query": query.model_dump(mode="json"),
            "results": [item.model_dump(mode="json") for item in selected],
        }
        digest = _hash(payload)
        return RetrievalSnapshot(
            ranking_version=RANKING_VERSION,
            inventory_snapshot_id=self.inventory.snapshot_id,
            index_snapshot_id=self.index.snapshot_id,
            query=query,
            results=tuple(selected),
            created_at_ms=int(time.time() * 1000),
            snapshot_id=f"retrieval_{digest}",
            snapshot_hash=digest,
        )

    def _score(
        self,
        candidate: _Candidate,
        query: RepositoryRetrievalQuery,
        mentioned_languages: set[str | None],
    ) -> RetrievalScoreBreakdown:
        document = candidate.document
        signals = candidate.signals
        exact_symbol = 8.0 if (
            signals.get("exact_symbol", 0.0) > 0
        ) else 0.0
        exact_path = 7.0 if signals.get("exact_path", 0.0) else 0.0
        fuzzy_path = signals.get("fuzzy_path", 0.0)
        fuzzy_symbol = signals.get("fuzzy_symbol", 0.0)
        current_diff = 6.0 if signals.get("current_diff", 0.0) else 0.0
        recently_modified = 4.0 if signals.get("recently_modified", 0.0) else 0.0
        previous_read = 3.0 if signals.get("previous_read", 0.0) else 0.0
        recent_tool_result = 3.0 if signals.get("recent_tool_result", 0.0) else 0.0
        path_rule = 2.0 if signals.get("path_rule", 0.0) else 0.0
        fts = signals.get("fts_bm25", 0.0)
        definition = 3.0 if signals.get("definition_match", 0.0) else 0.0
        import_relationship = 2.0 if signals.get("import_relationship", 0.0) else 0.0
        reference_relationship = 1.5 if signals.get("reference_relationship", 0.0) else 0.0
        file_record = self._file_by_path.get(document.path)
        language_affinity = 1.0 if (
            file_record is not None
            and file_record.language in mentioned_languages
        ) else 0.0
        test_source = 1.5 if signals.get("test_source", 0.0) else 0.0
        generated_penalty = -2.0 if file_record is not None and file_record.generated else 0.0
        vendor_penalty = -3.0 if file_record is not None and file_record.vendor else 0.0
        return RetrievalScoreBreakdown(
            exact_symbol=exact_symbol,
            definition_match=definition,
            import_relationship=import_relationship,
            reference_relationship=reference_relationship,
            fts_bm25=fts,
            exact_path=exact_path,
            fuzzy_path=fuzzy_path,
            fuzzy_symbol=fuzzy_symbol,
            current_diff=current_diff,
            recently_modified=recently_modified,
            previous_read=previous_read,
            recent_tool_result=recent_tool_result,
            path_rule=path_rule,
            language_affinity=language_affinity,
            test_source=test_source,
            generated_penalty=generated_penalty,
            vendor_penalty=vendor_penalty,
        )


def _reasons(breakdown: RetrievalScoreBreakdown) -> tuple[RetrievalReason, ...]:
    labels = {
        "exact_symbol": "exact symbol match",
        "definition_match": "definition record",
        "import_relationship": "import relationship",
        "reference_relationship": "reference relationship",
        "fts_bm25": "SQLite FTS5 lexical match",
        "exact_path": "explicit path match",
        "fuzzy_path": "bounded fuzzy path match",
        "fuzzy_symbol": "bounded fuzzy symbol match",
        "current_diff": "current diff path",
        "recently_modified": "recently modified path",
        "previous_read": "previous read evidence",
        "recent_tool_result": "recent tool result",
        "path_rule": "path-scoped rule relevance",
        "language_affinity": "language affinity",
        "test_source": "test/source promotion",
        "generated_penalty": "generated-file penalty",
        "vendor_penalty": "vendor-file penalty",
    }
    values = breakdown.model_dump(mode="python")
    return tuple(
        RetrievalReason(signal=name, value=float(value), explanation=labels[name])
        for name, value in values.items()
        if value != 0
    )


def _hash(value: object) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")).hexdigest()


def _document(item: RepositoryFtsDocument) -> _Document:
    return _Document(
        record_id=item.record_id,
        path=item.path,
        kind=item.kind,
        text=item.symbol or item.body,
        file_hash=item.file_hash,
        start_line=item.start_line,
        end_line=item.end_line,
    )


def _utf8_prefix(value: str, byte_limit: int) -> str:
    prefix = value.encode("utf-8")[:byte_limit]
    while True:
        try:
            return prefix.decode("utf-8")
        except UnicodeDecodeError as error:
            prefix = prefix[:error.start]


__all__ = [
    "RepositoryEvidence",
    "RepositoryRetrievalCandidate",
    "RepositoryRetrievalQuery",
    "RepositoryRetriever",
    "RetrievalReason",
    "RetrievalScoreBreakdown",
    "RetrievalSnapshot",
]

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import threading
import time
from typing import Final

from pydantic import Field, model_validator
from rapidfuzz.fuzz import ratio

from eidos_runtime.models import EidosFrozenStrictModel, JsonSafeInt
from eidos_runtime.repo_intelligence.index import (
    RepositoryIndexSnapshot,
)
from eidos_runtime.repo_intelligence.inventory import RepositoryInventory
from eidos_runtime.persistence.repository_intelligence import (
    RepositoryIntelligenceRepository,
)


RANKING_VERSION: Final = 1
MAX_QUERY_BYTES: Final = 16 * 1024


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
        self._documents = {
            item.record_id: _Document(
                record_id=item.record_id,
                path=item.path,
                kind=item.kind,
                text=item.symbol or item.body,
                file_hash=item.file_hash,
                start_line=item.start_line,
                end_line=item.end_line,
            )
            for item in repository.list_fts_documents(index.snapshot_id)
        }

    def retrieve(
        self,
        query: RepositoryRetrievalQuery,
        *,
        cancel: threading.Event | None = None,
    ) -> RetrievalSnapshot:
        if len(query.text.encode("utf-8")) > MAX_QUERY_BYTES:
            raise ValueError("retrieval query is too large")
        deadline = time.monotonic() + query.deadline_ms / 1000
        fts_scores = self._fts_scores(query, cancel=cancel)
        candidates: list[RepositoryRetrievalCandidate] = []
        for document in self._documents.values():
            if time.monotonic() >= deadline:
                break
            breakdown = self._score(document, query, fts_scores)
            if breakdown.total <= 0:
                continue
            reasons = _reasons(breakdown)
            candidates.append(RepositoryRetrievalCandidate(
                record_id=document.record_id,
                path=document.path,
                score=breakdown.total,
                score_breakdown=breakdown,
                reasons=reasons,
                evidence=(),
            ))
        candidates.sort(key=lambda item: (-item.score, item.path.encode()))
        selected: list[RepositoryRetrievalCandidate] = []
        used_bytes = 0
        for candidate in candidates[:query.max_results]:
            document = self._documents[candidate.record_id]
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

    def _fts_scores(
        self,
        query: RepositoryRetrievalQuery,
        *,
        cancel: threading.Event | None,
    ) -> dict[str, float]:
        rows = self.repository.query_fts_bm25(
            self.index.snapshot_id,
            query.text,
            deadline_ms=query.deadline_ms,
            cancel=cancel,
        )
        return {
            row.document.record_id: 1.0 / (1.0 + abs(row.bm25))
            for row in rows
        }

    def _score(
        self,
        document: _Document,
        query: RepositoryRetrievalQuery,
        fts_scores: dict[str, float],
    ) -> RetrievalScoreBreakdown:
        exact_symbol = 8.0 if (
            document.kind == "symbol" and document.text in query.mentioned_symbols
        ) else 0.0
        exact_path = 7.0 if document.path in query.mentioned_paths else 0.0
        fuzzy_path = max((_fuzzy(document.path, value, 2) for value in query.mentioned_paths), default=0.0)
        fuzzy_symbol = max((_fuzzy(document.text, value, 3) for value in query.mentioned_symbols), default=0.0)
        current_diff = 6.0 if document.path in query.current_diff_paths else 0.0
        recently_modified = 4.0 if document.path in query.recently_modified_paths else 0.0
        previous_read = 3.0 if document.path in query.previous_read_paths else 0.0
        recent_tool_result = 3.0 if document.path in query.recent_tool_result_paths else 0.0
        path_rule = 2.0 if document.path in query.rule_paths else 0.0
        fts = fts_scores.get(document.record_id, 0.0)
        definition = 3.0 if exact_symbol else 0.0
        import_relationship = 2.0 if any(
            item.path == document.path
            and any(name in item.imported_name for name in query.mentioned_symbols)
            for item in self.index.imports
        ) else 0.0
        reference_relationship = 1.5 if any(
            item.path == document.path and item.name in query.mentioned_symbols
            for item in self.index.references
        ) else 0.0
        file_record = next(
            (item for item in self.inventory.files if item.path == document.path), None
        )
        mentioned_languages = {
            item.language for item in self.inventory.files
            if item.path in query.mentioned_paths and item.language is not None
        }
        language_affinity = 1.0 if (
            file_record is not None and file_record.language in mentioned_languages
        ) else 0.0
        test_source = 1.5 if _related_test_path(
            document.path, query.mentioned_paths
        ) else 0.0
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


def _utf8_prefix(value: str, byte_limit: int) -> str:
    prefix = value.encode("utf-8")[:byte_limit]
    while True:
        try:
            return prefix.decode("utf-8")
        except UnicodeDecodeError as error:
            prefix = prefix[:error.start]


def _fuzzy(left: str, right: str, weight: float) -> float:
    similarity = ratio(left, right)
    return similarity / 100 * weight if similarity >= 70 else 0.0


def _related_test_path(path: str, selected_sources: tuple[str, ...]) -> bool:
    name = path.rsplit("/", 1)[-1]
    is_test = name.startswith("test_") or "_test." in name
    if not is_test:
        return False
    normalized = name.removeprefix("test_").replace("_test.", ".")
    return any(source.rsplit("/", 1)[-1] == normalized for source in selected_sources)


__all__ = [
    "RepositoryEvidence",
    "RepositoryRetrievalCandidate",
    "RepositoryRetrievalQuery",
    "RepositoryRetriever",
    "RetrievalReason",
    "RetrievalScoreBreakdown",
    "RetrievalSnapshot",
]

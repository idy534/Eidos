from eidos_runtime.repo_intelligence.inventory import (
    DirectoryRecord,
    FileRecord,
    InventoryCanceled,
    InventoryDiagnostic,
    RepositoryInventory,
    RepositoryInventoryBuilder,
)
from eidos_runtime.repo_intelligence.index import (
    CodeChunk,
    Import,
    IndexCanceled,
    ParseDiagnostic,
    ParsedFile,
    Reference,
    RepositoryIndexSnapshot,
    RepositoryIndexer,
    Symbol,
    SymbolKind,
)
from eidos_runtime.repo_intelligence.map import (
    DiscoveredCommand,
    RepositoryMap,
    RepositoryMapBuilder,
)
from eidos_runtime.repo_intelligence.watcher import (
    RepositoryChange,
    RepositoryWatchController,
    coalesce_changes,
)


_RETRIEVAL_EXPORTS = frozenset({
    "RepositoryEvidence",
    "RepositoryRetrievalCandidate",
    "RepositoryRetrievalQuery",
    "RepositoryRetriever",
    "RetrievalReason",
    "RetrievalScoreBreakdown",
    "RetrievalSnapshot",
})


def __getattr__(name: str) -> object:
    if name not in _RETRIEVAL_EXPORTS:
        raise AttributeError(name)
    from eidos_runtime.repo_intelligence import retrieval

    return getattr(retrieval, name)

__all__ = [
    "DirectoryRecord",
    "DiscoveredCommand",
    "CodeChunk",
    "FileRecord",
    "InventoryCanceled",
    "InventoryDiagnostic",
    "Import",
    "IndexCanceled",
    "ParseDiagnostic",
    "ParsedFile",
    "Reference",
    "RepositoryEvidence",
    "RepositoryIndexSnapshot",
    "RepositoryIndexer",
    "RepositoryInventory",
    "RepositoryInventoryBuilder",
    "RepositoryMap",
    "RepositoryMapBuilder",
    "RepositoryChange",
    "RepositoryWatchController",
    "RepositoryRetrievalCandidate",
    "RepositoryRetrievalQuery",
    "RepositoryRetriever",
    "RetrievalReason",
    "RetrievalScoreBreakdown",
    "RetrievalSnapshot",
    "Symbol",
    "SymbolKind",
    "coalesce_changes",
]

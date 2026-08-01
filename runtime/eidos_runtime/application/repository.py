from __future__ import annotations

from pathlib import Path
import threading

from eidos_runtime.models import EidosFrozenStrictModel
from eidos_runtime.repo_intelligence.index import (
    RepositoryIndexSnapshot,
    RepositoryIndexer,
)
from eidos_runtime.repo_intelligence.inventory import (
    RepositoryInventory,
    RepositoryInventoryBuilder,
)
from eidos_runtime.repo_intelligence.map import RepositoryMap, RepositoryMapBuilder
from eidos_runtime.repo_intelligence.retrieval import (
    RepositoryRetrievalQuery,
    RepositoryRetriever,
    RetrievalSnapshot,
)


class RepositoryAnalysisSnapshot(EidosFrozenStrictModel):
    """One immutable repository analysis result owned by the application port."""

    inventory: RepositoryInventory
    index: RepositoryIndexSnapshot | None
    repository_map: RepositoryMap | None
    complete: bool


class RepositoryApplication:
    """Coordinates inventory, index and map builders without owning their policy."""

    def __init__(self, root: Path) -> None:
        self.inventory_builder = RepositoryInventoryBuilder(root)
        self.indexer = RepositoryIndexer(root)
        self.map_builder = RepositoryMapBuilder(root)

    def build(
        self, *, cancel: threading.Event | None = None
    ) -> RepositoryAnalysisSnapshot:
        inventory = self.inventory_builder.build(cancel=cancel)
        if not inventory.complete:
            return RepositoryAnalysisSnapshot(
                inventory=inventory, index=None, repository_map=None, complete=False
            )
        index = self.indexer.build(inventory, cancel=cancel)
        repository_map = (
            self.map_builder.build(inventory) if index.complete else None
        )
        return RepositoryAnalysisSnapshot(
            inventory=inventory,
            index=index,
            repository_map=repository_map,
            complete=index.complete and repository_map is not None,
        )

    def retrieve(
        self,
        snapshot: RepositoryAnalysisSnapshot,
        query: RepositoryRetrievalQuery,
    ) -> RetrievalSnapshot:
        if not snapshot.complete or snapshot.index is None:
            raise ValueError("complete repository analysis is required")
        return RepositoryRetriever(snapshot.inventory, snapshot.index).retrieve(query)


__all__ = ["RepositoryAnalysisSnapshot", "RepositoryApplication"]

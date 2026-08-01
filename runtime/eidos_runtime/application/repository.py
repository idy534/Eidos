from __future__ import annotations

from pathlib import Path
import threading
from collections.abc import Callable

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
from eidos_runtime.persistence.repository_intelligence import (
    RepositoryIndexStatus,
    RepositoryIntelligenceRepository,
    RepositoryIntelligenceSnapshot,
    RepositoryWorkspaceIdentity,
)


class RepositoryAnalysisSnapshot(EidosFrozenStrictModel):
    """One immutable repository analysis result owned by the application port."""

    inventory: RepositoryInventory
    index: RepositoryIndexSnapshot | None
    repository_map: RepositoryMap | None
    complete: bool
    persisted_snapshot: RepositoryIntelligenceSnapshot | None = None


class RepositoryApplication:
    """Coordinates persisted repository generations without owning scan policy."""

    def __init__(
        self,
        root: Path,
        *,
        repository: RepositoryIntelligenceRepository,
    ) -> None:
        self.inventory_builder = RepositoryInventoryBuilder(root)
        self.indexer = RepositoryIndexer(root)
        self.map_builder = RepositoryMapBuilder(root)
        self.repository = repository
        self.workspace_identity = RepositoryWorkspaceIdentity.from_root(root)

    def build(
        self, *, cancel: threading.Event | None = None
    ) -> RepositoryAnalysisSnapshot:
        workspace_identity = RepositoryWorkspaceIdentity.from_root(
            self.inventory_builder.root
        )
        restored = self.repository.read_latest_complete(
            workspace_identity.repository_id, workspace_identity
        )
        if restored is not None:
            self.inventory_builder.restore_generation(restored.inventory)
            if restored.index is not None:
                self.indexer.restore_generation(restored.index)
        inventory = self.inventory_builder.build(cancel=cancel)
        if not inventory.complete:
            persisted = self.repository.record_incomplete(
                inventory, None, workspace_identity
            )
            return RepositoryAnalysisSnapshot(
                inventory=inventory,
                index=None,
                repository_map=None,
                complete=False,
                persisted_snapshot=persisted,
            )
        index = self.indexer.build(
            inventory,
            cancel=cancel,
            previous=restored.index if restored is not None else None,
        )
        persisted = (
            self.repository.commit_complete(inventory, index, workspace_identity)
            if index.complete
            else self.repository.record_incomplete(
                inventory, index, workspace_identity
            )
        )
        repository_map = (
            self.map_builder.build(inventory) if index.complete else None
        )
        return RepositoryAnalysisSnapshot(
            inventory=inventory,
            index=index,
            repository_map=repository_map,
            complete=index.complete and repository_map is not None,
            persisted_snapshot=persisted,
        )

    def initialize_recovery(self) -> RepositoryIndexStatus:
        """Read one complete persisted generation without blocking startup.

        The later watcher/reconciliation stage owns discovery of new paths. This
        basis only compares every previously verified record to its current
        metadata and reports whether a bounded reconciliation is needed.
        """

        identity = RepositoryWorkspaceIdentity.from_root(self.inventory_builder.root)
        status = self.repository.read_status(identity)
        restored = self.repository.read_latest_complete(
            identity.repository_id, identity
        )
        if restored is None:
            return status
        return status.model_copy(update={
            "reconciliation_required": (
                status.reconciliation_required
                or _inventory_metadata_changed(self.inventory_builder.root, restored.inventory)
            ),
        })

    def restore_latest_complete(self) -> RepositoryIntelligenceSnapshot | None:
        identity = RepositoryWorkspaceIdentity.from_root(self.inventory_builder.root)
        return self.repository.read_latest_complete(identity.repository_id, identity)

    def retrieve(
        self,
        snapshot: RepositoryAnalysisSnapshot,
        query: RepositoryRetrievalQuery,
    ) -> RetrievalSnapshot:
        if not snapshot.complete or snapshot.index is None:
            raise ValueError("complete repository analysis is required")
        return RepositoryRetriever(snapshot.inventory, snapshot.index).retrieve(query)


class RepositoryApplicationFactory:
    """Own one repository service per verified Workspace identity."""

    def __init__(
        self,
        repository_provider: Callable[[], RepositoryIntelligenceRepository],
    ) -> None:
        self._repository_provider = repository_provider
        self._applications: dict[
            tuple[str, int, int, int], RepositoryApplication
        ] = {}
        self._lock = threading.RLock()

    def for_workspace(self, root: Path) -> RepositoryApplication:
        identity = RepositoryWorkspaceIdentity.from_root(root)
        key = (identity.root, identity.device, identity.inode, identity.owner)
        with self._lock:
            application = self._applications.get(key)
            if application is None:
                application = RepositoryApplication(
                    Path(identity.root),
                    repository=self._repository_provider(),
                )
                self._applications[key] = application
            return application


def _inventory_metadata_changed(root: Path, inventory: RepositoryInventory) -> bool:
    for record in inventory.files:
        try:
            metadata = (root / record.path).lstat()
        except OSError:
            return True
        if (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mtime_ns,
        ) != (
            record.device,
            record.inode,
            record.size_bytes,
            record.mtime_ns,
        ):
            return True
    return False


__all__ = [
    "RepositoryAnalysisSnapshot",
    "RepositoryApplication",
    "RepositoryApplicationFactory",
]

from __future__ import annotations

from collections.abc import Callable
import logging
from pathlib import Path
import threading
from typing import Protocol

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
from eidos_runtime.repo_intelligence.watcher import (
    RepositoryChange,
    RepositoryWatchController,
    coalesce_changes,
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


class RepositoryRunCapture(EidosFrozenStrictModel):
    """One atomic Workspace view frozen for a Run."""

    snapshot: RepositoryAnalysisSnapshot | None
    dirty_paths: tuple[str, ...]
    invalidation_epoch: int


logger = logging.getLogger("eidos.runtime.repository")


class RepositoryWatcherShutdownError(RuntimeError):
    """The Runtime could not stop a workspace watcher within its deadline."""


class RepositoryWatchPort(Protocol):
    def run(
        self,
        stop: threading.Event,
        on_invalidate: Callable[[tuple[RepositoryChange, ...]], None],
    ) -> None: ...


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
        if workspace_identity != self.workspace_identity:
            raise OSError("repository workspace identity changed before build")
        restored = self.repository.read_latest_complete(
            workspace_identity.repository_id, workspace_identity
        )
        watermark = self.repository.read_generation_watermark(workspace_identity)
        self.inventory_builder.restore_generation_floor(
            watermark.max_inventory_generation
        )
        self.indexer.restore_generation_floor(watermark.max_index_generation)
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
        repository_map = self.map_builder.build(inventory) if index.complete else None
        if repository_map is not None:
            self.map_builder.verify_git_state(repository_map)
            if RepositoryWorkspaceIdentity.from_root(
                self.inventory_builder.root
            ) != workspace_identity:
                raise OSError("repository workspace identity changed before commit")
        persisted = (
            self.repository.commit_complete(
                inventory,
                index,
                repository_map,
                workspace_identity,
            )
            if repository_map is not None
            else self.repository.record_incomplete(
                inventory, index, workspace_identity
            )
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

    def restore_analysis_snapshot(self) -> RepositoryAnalysisSnapshot | None:
        """Restore one complete generation without running inventory or indexing."""

        restored = self.restore_latest_complete()
        if restored is None:
            return None
        if restored.repository_map is None:
            return None
        return RepositoryAnalysisSnapshot(
            inventory=restored.inventory,
            index=restored.index,
            repository_map=restored.repository_map,
            complete=(
                restored.complete
                and restored.index is not None
                and restored.repository_map is not None
            ),
            persisted_snapshot=restored,
        )

    def retrieve(
        self,
        snapshot: RepositoryAnalysisSnapshot,
        query: RepositoryRetrievalQuery,
        *,
        cancel: threading.Event | None = None,
    ) -> RetrievalSnapshot:
        if not snapshot.complete or snapshot.index is None:
            raise ValueError("complete repository analysis is required")
        return RepositoryRetriever(
            snapshot.inventory, snapshot.index, self.repository
        ).retrieve(query, cancel=cancel)


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


class ActiveRepositoryState:
    """Thread-safe mutable lifecycle around one immutable analysis snapshot."""

    def __init__(
        self,
        *,
        workspace_identity: RepositoryWorkspaceIdentity,
        application: RepositoryApplication,
        snapshot: RepositoryAnalysisSnapshot | None,
        recovery_status: RepositoryIndexStatus,
        watcher: RepositoryWatchPort,
        watcher_stop: threading.Event,
    ) -> None:
        self.workspace_identity = workspace_identity
        self.application = application
        self._snapshot = snapshot
        self._recovery_status = recovery_status
        self._dirty_paths: set[str] = set()
        self._invalidation_epoch = 0
        self.watcher = watcher
        self.watcher_stop = watcher_stop
        self.watcher_thread: threading.Thread | None = None
        self._closing = False
        self._closed = False
        self._lock = threading.RLock()
        self._readiness = threading.Condition(self._lock)
        self._build_in_progress = False
        self._readiness_attempt = 0

    @property
    def snapshot(self) -> RepositoryAnalysisSnapshot | None:
        with self._lock:
            return self._snapshot

    @property
    def recovery_status(self) -> RepositoryIndexStatus:
        with self._lock:
            return self._recovery_status

    @property
    def reconciliation_required(self) -> bool:
        with self._lock:
            return self._recovery_status.reconciliation_required

    @property
    def dirty_paths(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._dirty_paths)

    @property
    def invalidation_epoch(self) -> int:
        with self._lock:
            return self._invalidation_epoch

    def capture_for_run(self) -> RepositoryRunCapture:
        with self._lock:
            return RepositoryRunCapture(
                snapshot=self._snapshot,
                dirty_paths=tuple(sorted(self._dirty_paths, key=str.encode)),
                invalidation_epoch=self._invalidation_epoch,
            )

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def invalidate(self, changes: tuple[RepositoryChange, ...]) -> None:
        normalized = coalesce_changes(
            ((change.change, change.path) for change in changes)
        )
        if not normalized:
            return
        with self._lock:
            if self._closing or self._closed:
                return
            self._dirty_paths.update(change.path for change in normalized)
            self._invalidation_epoch += 1
            if not self._recovery_status.reconciliation_required:
                self._recovery_status = self._recovery_status.model_copy(
                    update={"reconciliation_required": True}
                )
            dirty_count = len(self._dirty_paths)
            epoch = self._invalidation_epoch
        logger.info(
            "repository_invalidated",
            extra={
                "workspace_root": self.workspace_identity.root,
                "dirty_path_count": dirty_count,
                "invalidation_epoch": epoch,
            },
        )

    def begin_generation_build(
        self, cancel: threading.Event | None
    ) -> int | None:
        """Claim one build or wait for the in-flight attempt to finish."""

        with self._readiness:
            if self._closing or self._closed or (cancel is not None and cancel.is_set()):
                return None
            if self._snapshot is not None and not self._recovery_status.reconciliation_required:
                return None
            if self._build_in_progress:
                observed_attempt = self._readiness_attempt
                while (
                    self._build_in_progress
                    and self._readiness_attempt == observed_attempt
                    and not self._closing
                    and not self._closed
                    and not (cancel is not None and cancel.is_set())
                ):
                    self._readiness.wait(timeout=0.05)
                return None
            self._build_in_progress = True
            return self._invalidation_epoch

    def finish_generation_build(self) -> None:
        with self._readiness:
            self._build_in_progress = False
            self._readiness_attempt += 1
            self._readiness.notify_all()

    def publish_generation(
        self,
        snapshot: RepositoryAnalysisSnapshot,
        *,
        start_invalidation_epoch: int,
    ) -> bool:
        """Atomically publish a complete candidate and its recovery status."""

        persisted = snapshot.persisted_snapshot
        if not snapshot.complete or persisted is None or not persisted.complete:
            raise ValueError("only a persisted complete generation can be published")
        if persisted.workspace_identity != self.workspace_identity:
            raise ValueError("repository generation workspace identity changed")
        with self._lock:
            if self._closing or self._closed:
                return False
            changed_during_build = self._invalidation_epoch != start_invalidation_epoch
            self._snapshot = snapshot
            self._recovery_status = RepositoryIndexStatus(
                repository_id=self.workspace_identity.repository_id,
                workspace_identity=self.workspace_identity,
                snapshot_id=persisted.snapshot_id,
                inventory_generation=persisted.inventory_generation,
                index_generation=persisted.index_generation,
                complete=True,
                reconciliation_required=changed_during_build,
            )
            if not changed_during_build:
                self._dirty_paths.clear()
            return not changed_during_build

    def close(self, *, timeout: float) -> None:
        with self._lock:
            if self._closed:
                return
            self._closing = True
            self.watcher_stop.set()
            watcher_thread = self.watcher_thread
        if watcher_thread is not None:
            watcher_thread.join(timeout=timeout)
            if watcher_thread.is_alive():
                with self._lock:
                    self._closing = False
                raise RepositoryWatcherShutdownError(
                    f"repository watcher did not stop: {self.workspace_identity.root}"
                )
        with self._lock:
            self._closed = True
            self._closing = False


class RepositoryWorkspaceRuntime:
    """Owns one active Repository Generation lifecycle per Workspace identity."""

    def __init__(
        self,
        application_factory: RepositoryApplicationFactory,
        *,
        watcher_factory: Callable[[Path], RepositoryWatchPort] = (
            RepositoryWatchController
        ),
        watcher_shutdown_timeout: float = 5.0,
    ) -> None:
        self.application_factory = application_factory
        self._watcher_factory = watcher_factory
        self._watcher_shutdown_timeout = watcher_shutdown_timeout
        self._active_by_root: dict[str, ActiveRepositoryState] = {}
        self._lock = threading.RLock()

    def activate_workspace(self, root: Path) -> ActiveRepositoryState:
        identity = RepositoryWorkspaceIdentity.from_root(root)
        with self._lock:
            current = self._active_by_root.get(identity.root)
            if current is not None and current.workspace_identity == identity:
                return current
            if current is not None:
                current.close(timeout=self._watcher_shutdown_timeout)

            application = self.application_factory.for_workspace(Path(identity.root))
            snapshot = application.restore_analysis_snapshot()
            recovery_status = application.initialize_recovery()
            # Persisted inventory can detect changed or deleted known files, but it
            # cannot prove that no new path appeared while the Runtime was offline.
            # Activation therefore never publishes a false cold-start clean state.
            if not recovery_status.reconciliation_required:
                recovery_status = recovery_status.model_copy(
                    update={"reconciliation_required": True}
                )
            stop = threading.Event()
            watcher = self._watcher_factory(Path(identity.root))
            active = ActiveRepositoryState(
                workspace_identity=identity,
                application=application,
                snapshot=snapshot,
                recovery_status=recovery_status,
                watcher=watcher,
                watcher_stop=stop,
            )
            worker = threading.Thread(
                target=self._run_watcher,
                args=(active,),
                name=f"repository-watch-{identity.repository_id[:12]}",
                daemon=True,
            )
            active.watcher_thread = worker
            self._active_by_root[identity.root] = active
            worker.start()

        logger.info(
            "repository_workspace_activated",
            extra={
                "workspace_root": identity.root,
                "repository_id": identity.repository_id,
                "generation": recovery_status.inventory_generation,
            },
        )
        if snapshot is not None:
            logger.info(
                "repository_generation_restored",
                extra={
                    "workspace_root": identity.root,
                    "repository_id": identity.repository_id,
                    "generation": snapshot.inventory.generation,
                },
            )
        if recovery_status.reconciliation_required:
            logger.info(
                "repository_reconciliation_required",
                extra={
                    "workspace_root": identity.root,
                    "repository_id": identity.repository_id,
                    "dirty_path_count": 0,
                },
            )
        return active

    def ensure_ready(
        self,
        root: Path,
        *,
        cancel: threading.Event | None = None,
    ) -> ActiveRepositoryState:
        active = self.activate_workspace(root)
        if cancel is not None and cancel.is_set():
            return active
        if active.snapshot is not None and not active.reconciliation_required:
            return active
        reason = "first_generation" if active.snapshot is None else "reconciliation"
        start_epoch = active.begin_generation_build(cancel)
        if start_epoch is None:
            return active
        repository_id = active.workspace_identity.repository_id
        if reason == "reconciliation":
            logger.info(
                "repository_generation_reconciliation_started",
                extra={
                    "repository_id": repository_id,
                    "generation": active.recovery_status.inventory_generation,
                    "dirty_path_count": len(active.dirty_paths),
                    "invalidation_epoch": start_epoch,
                    "reason": reason,
                },
            )
        logger.info(
            "repository_generation_build_started",
            extra={
                "repository_id": repository_id,
                "generation": active.recovery_status.inventory_generation,
                "dirty_path_count": len(active.dirty_paths),
                "invalidation_epoch": start_epoch,
                "reason": reason,
            },
        )
        try:
            candidate = active.application.build(cancel=cancel)
            if not candidate.complete:
                logger.info(
                    "repository_generation_build_incomplete",
                    extra={
                        "repository_id": repository_id,
                        "generation": candidate.inventory.generation,
                        "dirty_path_count": len(active.dirty_paths),
                        "invalidation_epoch": active.invalidation_epoch,
                        "reason": reason,
                    },
                )
                return active
            clean = active.publish_generation(
                candidate, start_invalidation_epoch=start_epoch
            )
            event = (
                "repository_generation_ready"
                if clean
                else "repository_generation_reconciliation_deferred"
            )
            logger.info(
                event,
                extra={
                    "repository_id": repository_id,
                    "generation": candidate.inventory.generation,
                    "dirty_path_count": len(active.dirty_paths),
                    "invalidation_epoch": active.invalidation_epoch,
                    "reason": reason,
                },
            )
            return active
        except Exception as error:
            logger.warning(
                "repository_generation_build_incomplete",
                extra={
                    "repository_id": repository_id,
                    "generation": active.recovery_status.inventory_generation,
                    "dirty_path_count": len(active.dirty_paths),
                    "invalidation_epoch": active.invalidation_epoch,
                    "reason": type(error).__name__,
                },
                exc_info=logger.isEnabledFor(logging.DEBUG),
            )
            return active
        finally:
            active.finish_generation_build()

    def get_active(self, root: Path) -> ActiveRepositoryState | None:
        canonical = str(root.resolve(strict=False))
        with self._lock:
            active = self._active_by_root.get(canonical)
            if active is None or active.closed:
                return None
            try:
                identity = RepositoryWorkspaceIdentity.from_root(root)
            except (OSError, ValueError):
                return None
            return active if active.workspace_identity == identity else None

    def invalidate(
        self,
        root: Path,
        changes: tuple[RepositoryChange, ...],
    ) -> None:
        active = self.get_active(root)
        if active is not None:
            active.invalidate(changes)

    def shutdown_workspace(self, root: Path) -> None:
        canonical = str(root.resolve(strict=False))
        with self._lock:
            active = self._active_by_root.get(canonical)
            if active is None:
                return
            active.close(timeout=self._watcher_shutdown_timeout)
            self._active_by_root.pop(canonical, None)
        logger.info(
            "repository_workspace_shutdown",
            extra={
                "workspace_root": active.workspace_identity.root,
                "repository_id": active.workspace_identity.repository_id,
            },
        )

    def shutdown_all(self) -> None:
        with self._lock:
            active_states = tuple(self._active_by_root.values())
            for active in active_states:
                active.close(timeout=self._watcher_shutdown_timeout)
            self._active_by_root.clear()
        for active in active_states:
            logger.info(
                "repository_workspace_shutdown",
                extra={
                    "workspace_root": active.workspace_identity.root,
                    "repository_id": active.workspace_identity.repository_id,
                },
            )

    @staticmethod
    def _run_watcher(active: ActiveRepositoryState) -> None:
        try:
            active.watcher.run(active.watcher_stop, active.invalidate)
        except Exception:
            if not active.watcher_stop.is_set():
                logger.exception(
                    "repository_watcher_failed",
                    extra={"workspace_root": active.workspace_identity.root},
                )


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
    "ActiveRepositoryState",
    "RepositoryAnalysisSnapshot",
    "RepositoryApplication",
    "RepositoryApplicationFactory",
    "RepositoryWatcherShutdownError",
    "RepositoryWorkspaceRuntime",
]

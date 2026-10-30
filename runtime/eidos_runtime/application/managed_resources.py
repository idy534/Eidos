"""Resource preparation use cases on the existing owned-task/SQLite boundary."""
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
import logging
import threading
import uuid

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.infrastructure.managed_resources import ManagedResourceManager, ResourceError
from eidos_runtime.models.managed_resources import ResourcePack, ResourceRetryRequest, ResourceRetryResponse, ResourceStatus
from eidos_runtime.resource_paths import managed_resource_directory

logger = logging.getLogger(__name__)


def installation_operation(store: SessionStore, manager: ManagedResourceManager) -> Callable[[ResourcePack], AbstractContextManager[None]]:
    @contextmanager
    def operation(pack: ResourcePack) -> Iterator[None]:
        record, _ = store.accept_async_operation(
            request_id=None, operation_id=str(uuid.uuid4()), scope="resource/install",
            request={"packId": pack.id, "version": pack.version, "release": manager.index.version},
        )
        store.start_async_operation(record.id)
        try:
            yield
        except Exception as error:
            if isinstance(error, ResourceError) and error.code == "resource_preparation_canceled":
                store.cancel_async_operation(record.id)
            else:
                store.fail_async_operation(record.id, error.code if isinstance(error, ResourceError) else "resource_install_failed")
            raise
        else:
            store.complete_async_operation(record.id, {
                "packId": pack.id, "version": pack.version, "directory": str(manager.location(pack.id)),
            })
    return operation


class ManagedResourceApplication:
    def __init__(self, start_task: Callable[[str, Callable[[threading.Event], None]], bool]) -> None:
        self.manager: ManagedResourceManager | None = None
        self._start_task = start_task
        self._lock = threading.Lock()
        self._pending: set[str] = set()

    def status(self) -> ResourceStatus:
        if self.manager is None:
            return ResourceStatus(available=False, directory=str(managed_resource_directory()))
        return self.manager.status()

    def retry(self, request: ResourceRetryRequest) -> ResourceRetryResponse:
        manager = self.manager
        if manager is None:
            return ResourceRetryResponse(accepted=False)
        try:
            manager.pack(request.pack_id)
        except ResourceError as error:
            raise ApplicationError("RESOURCE_UNKNOWN") from error
        with self._lock:
            if request.pack_id in self._pending:
                return ResourceRetryResponse(accepted=True)
            self._pending.add(request.pack_id)

        def prepare(cancel: threading.Event) -> None:
            try:
                manager.ensure((request.pack_id,), cancel)
            except ResourceError as error:
                logger.info("Document resource preparation is retryable: %s", error.code)
            finally:
                with self._lock:
                    self._pending.discard(request.pack_id)

        accepted = self._start_task("document-resource-prepare", prepare)
        if not accepted:
            with self._lock:
                self._pending.discard(request.pack_id)
        return ResourceRetryResponse(accepted=accepted)

    def warmup(self) -> None:
        if self.manager is not None and any(pack.id == "document-python" for pack in self.manager.index.packs):
            self.retry(ResourceRetryRequest(pack_id="document-python"))

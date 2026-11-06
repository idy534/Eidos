"""User-triggered login/catalog tasks on the existing Runtime owner boundary."""
from collections.abc import Callable, Mapping
from pathlib import Path
import threading
import uuid

from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.infrastructure.managed_resources import ManagedResourceManager, ResourceError
from eidos_runtime.infrastructure.qoder_model_driver import QoderModelDriver, QoderWorkerBinding, has_saved_qoder_login
from eidos_runtime.model.client import ModelRequestError
from eidos_runtime.model.config import ModelReasoningConfig
from eidos_runtime.model.qoder_protocol import PACK_ID, WorkerCatalog, WorkerLogin, WorkerRequest
from eidos_runtime.models.qoder import QoderCatalogEntry, QoderConnectionRequest, QoderConnectionState
from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel


class QoderApplication:
    def __init__(self, resources: Callable[[], ManagedResourceManager | None],
                 kernel: Callable[[], RuntimeAsyncKernel], directory: Callable[[], Path],
                 start_task: Callable[[str, Callable[[threading.Event], None]], bool],
                 on_catalog: Callable[[Mapping[str, str]], None] | None = None) -> None:
        self._resources, self._kernel, self._directory, self._start_task = resources, kernel, directory, start_task
        self._on_catalog = on_catalog or (lambda _names: None)
        self._lock = threading.RLock()
        # This is connection progress/catalog cache, never Run or billing state.
        self._state = QoderConnectionState(resource_ready=False)

    def model_name(self, model_id: str) -> str | None:
        with self._lock:
            return next((model.name for model in self._state.models if model.id == model_id), None)

    def connection(self, request: QoderConnectionRequest) -> QoderConnectionState:
        manager = self._resources()
        progress = manager.pack_status(PACK_ID) if manager is not None and any(p.id == PACK_ID for p in manager.index.packs) else None
        ready = progress is not None and progress.phase == "ready"
        with self._lock:
            self._state = self._state.model_copy(update={"resource_ready": ready,
                "resource_preparing": progress is not None and progress.phase in {"checking", "downloading", "verifying", "extracting"},
                "resource_error": progress.error_code if progress is not None else None})
            if request.action == "status" or self._state.phase in {"checking", "logging_in", "refreshing"}:
                return self._state
            if not ready:
                return self._state if request.action == "check" else QoderConnectionState(resource_ready=False, phase="error", error_code="resource_not_ready")
            if request.action == "check":
                try:
                    saved = has_saved_qoder_login(self._directory() / "qoder/default")
                except ModelRequestError as error:
                    self._state = QoderConnectionState(resource_ready=True, phase="error", error_code=error.failure.code)
                    return self._state
                if not saved:
                    self._state = QoderConnectionState(resource_ready=True)
                    return self._state
            self._state = QoderConnectionState(resource_ready=True,
                phase="logging_in" if request.action == "login" else "checking" if request.action == "check" else "refreshing")

            def run(cancel: threading.Event) -> None:
                driver = None
                try:
                    assert manager is not None
                    binding = QoderWorkerBinding(manager, self._directory() / "qoder/default")
                    driver = QoderModelDriver(binding)
                    kernel = self._kernel()
                    if request.action == "login":
                        result = kernel.call(driver.request, WorkerRequest(request_id=str(uuid.uuid4()), operation="login"),
                                             cancel, lambda _text: None, 190)
                        if not isinstance(result.payload, WorkerLogin) or not result.payload.succeeded:
                            raise ApplicationError("authentication_failed")
                    result = kernel.call(driver.request, WorkerRequest(request_id=str(uuid.uuid4()), operation="catalog"),
                                         cancel, lambda _text: None, 60)
                    if not isinstance(result.payload, WorkerCatalog):
                        raise ApplicationError("protocol_error")
                    models = tuple(QoderCatalogEntry(id=m.id, name=m.name, enabled=m.enabled, context_windows=m.context_windows,
                        default_context_window=m.default_context_window, max_output_tokens=m.max_output_tokens,
                        supports_images=m.supports_images, reasoning=ModelReasoningConfig(selections=m.reasoning_selections,
                        default_selection=m.default_reasoning) if m.reasoning_selections else None) for m in result.payload.models)
                    self._on_catalog({model.id: model.name for model in models})
                    state = QoderConnectionState(resource_ready=True, phase="ready", models=models)
                except (ModelRequestError, ResourceError) as error:
                    state = QoderConnectionState(resource_ready=ready, phase="error",
                        error_code=error.failure.code if isinstance(error, ModelRequestError) else error.code)
                except Exception:
                    state = QoderConnectionState(resource_ready=ready, phase="error", error_code="qoder_connection_failed")
                finally:
                    if driver is not None:
                        self._kernel().call(driver.close)
                with self._lock:
                    self._state = state

            if not self._start_task("qoder-connection", run):
                self._state = QoderConnectionState(resource_ready=ready, phase="error", error_code="runtime_draining")
            return self._state

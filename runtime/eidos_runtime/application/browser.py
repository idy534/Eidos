"""Browser status, grant revocation and Chrome shutdown for Desktop."""
from __future__ import annotations

from collections.abc import Callable

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.extensions.browser_plugin import BROWSER_PLUGIN_ID
from eidos_runtime.infrastructure.browser_host import BrowserHost
from eidos_runtime.infrastructure.browser_runtime import BROWSER_PACK_ID
from eidos_runtime.infrastructure.managed_resources import ManagedResourceManager
from eidos_runtime.models.browser import (
    BrowserCloseResponse,
    BrowserGrantRevokeRequest,
    BrowserGrantRevokeResponse,
    BrowserStatus,
    BrowserStatusRequest,
)
from eidos_runtime.persistence.browser_grants import BrowserGrantRepository
from eidos_runtime.runtime.events import RuntimeEvents

_RESOURCE_PHASES = {
    "ready": "ready", "missing": "missing", "failed": "failed",
    "checking": "preparing", "downloading": "preparing", "verifying": "preparing", "extracting": "preparing",
}


class BrowserApplication:
    def __init__(
        self,
        store: SessionStore,
        events: RuntimeEvents,
        host: Callable[[], BrowserHost | None],
        resources: Callable[[], ManagedResourceManager | None],
    ) -> None:
        self.store = store
        self.events = events
        self.host = host
        self.resources = resources
        self.grants = BrowserGrantRepository(store.database)

    def status(self, request: BrowserStatusRequest) -> BrowserStatus:
        host = self.host()
        host_status = host.status() if host is not None else None
        record = self.store.plugin_record(BROWSER_PLUGIN_ID)
        installed = record is not None and record.get("status") == "installed"
        return BrowserStatus(
            chrome_installed=host_status.chrome_installed if host_status is not None else False,
            chrome_version=host_status.chrome_version if host_status is not None else None,
            running=host_status.running if host_status is not None else False,
            plugin_installed=installed,
            plugin_enabled=installed and record.get("enabled") is True,
            resource=self._resource_phase(),
            granted=request.session_id is not None and self.grants.active(request.session_id) is not None,
        )

    def revoke(self, request: BrowserGrantRevokeRequest) -> BrowserGrantRevokeResponse:
        mutation = self.grants.revoke(request.session_id, "user")
        if mutation.events:
            self.events.publish(mutation)
        return BrowserGrantRevokeResponse(revoked=mutation.value)

    def close(self) -> BrowserCloseResponse:
        host = self.host()
        if host is None or not host.status().running:
            return BrowserCloseResponse(closed=False)
        host.close_browser()
        return BrowserCloseResponse(closed=True)

    def _resource_phase(self) -> str:
        manager = self.resources()
        if manager is None:
            return "unavailable"
        progress = next((pack for pack in manager.status().packs if pack.id == BROWSER_PACK_ID), None)
        if progress is None:
            return "unavailable"
        return _RESOURCE_PHASES.get(progress.phase, "missing")

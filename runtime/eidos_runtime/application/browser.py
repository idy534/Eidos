"""Browser status, grant revocation and Chrome shutdown for Desktop."""
from __future__ import annotations

from collections.abc import Callable

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.extensions.browser_plugin import BROWSER_PLUGIN_ID
import threading

from eidos_runtime.infrastructure.browser_host import BrowserHost, BrowserUnavailable
from eidos_runtime.infrastructure.browser_runtime import BROWSER_PACK_ID
from eidos_runtime.infrastructure.managed_resources import ManagedResourceManager, ResourceError
from eidos_runtime.models.browser import (
    BrowserClearResponse,
    BrowserCloseResponse,
    BrowserExtensionTokenClearRequest,
    BrowserExtensionTokenRequest,
    BrowserExtensionTokenResponse,
    BrowserOpenResponse,
    BrowserGrantRevokeRequest,
    BrowserGrantRevokeResponse,
    BrowserStatus,
    BrowserStatusRequest,
    BrowserTargetRequest,
    BrowserTargetResponse,
)
from eidos_runtime.application.errors import ApplicationInvalidParamsError
from eidos_runtime.infrastructure.chrome_extension import (
    ExtensionPresence, ExtensionTokenInvalid, extension_presence, normalize_extension_token,
)
from eidos_runtime.infrastructure.user_chrome import user_chrome_endpoint
from eidos_runtime.persistence.browser_grants import BrowserGrantRepository
from eidos_runtime.runtime.browser_launch import browser_secrets, extension_token
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
        presence: Callable[[], ExtensionPresence] = extension_presence,
    ) -> None:
        self.presence = presence
        self.store = store
        self.events = events
        self.host = host
        self.resources = resources
        self.grants = BrowserGrantRepository(store.database)

    def status(self, request: BrowserStatusRequest) -> BrowserStatus:
        host = self.host()
        host_status = host.status() if host is not None else None
        target = self.grants.target()
        configured = extension_token(self.store) is not None
        presence = self.presence() if target == "chrome_extension" else "unknown"
        effective = (
            "eidos" if target == "chrome_extension" and (not configured or presence == "missing") else target
        )
        record = self.store.plugin_record(BROWSER_PLUGIN_ID)
        installed = record is not None and record.get("status") == "installed"
        return BrowserStatus(
            chrome_installed=host_status.chrome_installed if host_status is not None else False,
            chrome_version=host_status.chrome_version if host_status is not None else None,
            running=host_status.running if host_status is not None else False,
            plugin_installed=installed,
            plugin_enabled=installed and record.get("enabled") is True,
            resource=self._resource_phase(),
            target=target,
            effective_target=effective,
            attach_available=user_chrome_endpoint() is not None,
            extension_configured=configured,
            extension_presence=presence,
            granted=request.session_id is not None and self.grants.active(
                request.session_id, "default" if effective == "eidos" else "user",
            ) is not None,
        )

    def set_target(self, request: BrowserTargetRequest) -> BrowserTargetResponse:
        previous = self.grants.target()
        mutation = self.grants.set_target(request.target)
        self.events.publish(mutation)
        # Every way of reaching the user's Chrome asks each Session again.
        revoked = self._revoke_user_grants() if previous != request.target else 0
        return BrowserTargetResponse(target=request.target, revoked_grants=revoked)

    def set_extension_token(self, request: BrowserExtensionTokenRequest) -> BrowserExtensionTokenResponse:
        try:
            token = normalize_extension_token(request.token)
        except ExtensionTokenInvalid:
            raise ApplicationInvalidParamsError("BROWSER_EXTENSION_TOKEN_INVALID") from None
        secrets = browser_secrets(self.store)
        if secrets is None:
            raise ApplicationInvalidParamsError("BROWSER_EXTENSION_TOKEN_INVALID")
        reference = secrets.write(token)
        try:
            mutation = self.grants.set_extension_credential(reference)
        except BaseException:
            secrets.prune(lambda identifier: identifier != reference)
            raise
        self.events.publish(mutation)
        secrets.prune(lambda identifier: identifier == reference)
        # A new pairing with the user's Chrome is confirmed again per Session.
        return BrowserExtensionTokenResponse(configured=True, revoked_grants=self._revoke_user_grants())

    def clear_extension_token(self, _request: BrowserExtensionTokenClearRequest) -> BrowserExtensionTokenResponse:
        mutation = self.grants.set_extension_credential(None)
        self.events.publish(mutation)
        secrets = browser_secrets(self.store)
        if secrets is not None:
            secrets.prune(lambda _identifier: False)
        return BrowserExtensionTokenResponse(configured=False, revoked_grants=self._revoke_user_grants())

    def _revoke_user_grants(self) -> int:
        revocation = self.grants.revoke_all("user", profile="user")
        if revocation.events:
            self.events.publish(revocation)
        return revocation.value

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

    def open(self) -> BrowserOpenResponse:
        """Start the Eidos Chrome for the user to sign in; no Agent is connected."""

        host = self.host()
        if host is None:
            return BrowserOpenResponse(opened=False, error_code="browser_unavailable")
        try:
            host.ensure(threading.Event())
        except BrowserUnavailable as error:
            return BrowserOpenResponse(opened=False, error_code=error.code)
        return BrowserOpenResponse(opened=True)

    def clear_profile(self) -> BrowserClearResponse:
        """Sign out of every site by deleting the profile, then revoke all grants."""

        host = self.host()
        if host is None:
            return BrowserClearResponse(cleared=False, revoked_grants=0, error_code="browser_unavailable")
        try:
            cleared = host.clear_profile()
        except BrowserUnavailable as error:
            return BrowserClearResponse(cleared=False, revoked_grants=0, error_code=error.code)
        mutation = self.grants.revoke_all("profile_cleared")
        if mutation.events:
            self.events.publish(mutation)
        return BrowserClearResponse(cleared=cleared, revoked_grants=mutation.value)

    def _resource_phase(self) -> str:
        manager = self.resources()
        if manager is None:
            return "unavailable"
        try:
            progress = manager.pack_status(BROWSER_PACK_ID)
        except ResourceError:
            return "unavailable"
        return _RESOURCE_PHASES.get(progress.phase, "missing")

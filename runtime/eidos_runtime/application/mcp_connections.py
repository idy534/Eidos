"""Remote connection lifecycle. SQLite owns state; secret files own token material."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING
from urllib.parse import urlsplit
import os
import time
import uuid

import anyio
import httpx2
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.db.database import canonical_hash, now_ms
from eidos_runtime.infrastructure.mcp_auth import (
    Credentials, McpAuthError, OAuthStorage, client_kwargs, github_device_login,
    oauth_callback, oauth_provider, refresh_github, verify_remote,
    refresh_oauth,
    credential_refresh_lock,
)
from eidos_runtime.infrastructure.secret_files import SecretFiles
from eidos_runtime.models.mcp_connections import McpConnectionRequest, McpConnectionState, RemoteMcpAuth, StoredMcpConnection
from eidos_runtime.persistence.mcp_connections import McpConnectionRepository
from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel, RuntimeAsyncTask

if TYPE_CHECKING:
    from eidos_runtime.extensions.contracts import McpServerConfigV1
    from eidos_runtime.extensions.plugins import PluginCatalog


class _Bearer(httpx2.Auth):
    def __init__(self, token: Callable[[], Awaitable[str]]) -> None:
        self.token = token

    async def async_auth_flow(self, request: httpx2.Request):
        request.headers["Authorization"] = f"Bearer {await self.token()}"
        yield request


class McpConnections:
    def __init__(self, plugins: "PluginCatalog") -> None:
        self.plugins = plugins
        self.repository = McpConnectionRepository(plugins.store.database)
        self.secrets = SecretFiles(Path(plugins.store.data_directory) / "plugin-credentials", max_bytes=32768)
        self._lock = RLock()
        self._tasks: dict[tuple[str, str], RuntimeAsyncTask[None]] = {}
        self._presentations: dict[str, tuple[str, str | None]] = {}

    def recover(self) -> None:
        """Called once by Runtime startup, never by a Run-scoped catalog view."""
        self.repository.recover()
        self.secrets.prune(self.repository.retained)

    def binding(self, plugin_id: str, server_id: str) -> tuple["McpServerConfigV1", str]:
        record = self.plugins.store.plugin_record(plugin_id)
        if record is None or record["status"] != "installed":
            raise ApplicationError("RESOURCE_NOT_FOUND")
        config = next((item for item in self.plugins.manifest(plugin_id).mcp_servers if item.id == server_id), None)
        if config is None or config.transport != "streamable-http":
            raise ApplicationError("MCP_REMOTE_REQUIRED")
        return config, canonical_hash({"pluginHash": record["contentHash"], "config": config.model_dump(mode="json", by_alias=True)})

    def read(self, plugin_id: str, server_id: str) -> McpConnectionState:
        config, identity = self.binding(plugin_id, server_id)
        auth = config.auth or RemoteMcpAuth()
        record = self.repository.get(plugin_id, server_id)
        base = McpConnectionState(plugin_id=plugin_id, server_id=server_id, auth_type=auth.type,
                                  browser_available=auth.type == "oauth" or bool(self._github_client(auth)))
        if record is None or record.config_hash != identity:
            return base
        presentation = self._presentations.get(record.generation)
        return base.model_copy(update={
            "status": record.status, "account_label": record.account_label, "expires_at": record.expires_at,
            "error_code": record.error_code, "updated_at": record.updated_at,
            "authorization_url": presentation[0] if presentation and record.status == "connecting" else None,
            "user_code": presentation[1] if presentation and record.status == "connecting" else None,
        })

    def revision(self, plugin_id: str, server_id: str) -> str:
        record = self.repository.get(plugin_id, server_id)
        return f"{record.generation}:{record.status}" if record else "disconnected"

    @staticmethod
    def _github_client(auth: RemoteMcpAuth) -> str | None:
        if auth.type != "github-device":
            return None
        return auth.client_id or os.environ.get("EIDOS_GITHUB_CLIENT_ID") or None

    def execute(self, request: McpConnectionRequest, kernel: RuntimeAsyncKernel) -> McpConnectionState:
        with self._lock:
            config, identity = self.binding(request.plugin_id, request.server_id)
            if request.action == "read":
                return self.read(request.plugin_id, request.server_id)
            key = request.plugin_id, request.server_id
            current = self.repository.get(*key)
            if request.action == "connect" and current and current.generation == request.operation_id:
                return self.read(*key)
            if request.action in {"cancel", "disconnect"}:
                record = StoredMcpConnection(plugin_id=key[0], server_id=key[1], config_hash=identity,
                    generation=str(uuid.uuid4()), status="disconnected", updated_at=now_ms())
                self.repository.save(record)
                task = self._tasks.pop(key, None)
                if task:
                    task.cancel()
                if current:
                    self._presentations.pop(current.generation, None)
                self.plugins.set_mcp_enabled(*key, False)
                self.secrets.prune(self.repository.retained)
                return self.read(*key)
            auth = config.auth or RemoteMcpAuth()
            if not config.enabled or not self.plugins.store.plugin_record(key[0])["enabled"]:
                raise ApplicationError("MCP_NOT_AUTHORIZED")
            if request.method == "browser" and auth.type not in {"oauth", "github-device", "none"}:
                raise ApplicationError("MCP_TOKEN_REQUIRED")
            if request.method == "browser" and auth.type == "github-device" and not self._github_client(auth):
                raise ApplicationError("GITHUB_CLIENT_ID_REQUIRED")
            if request.method == "token" and auth.type == "none":
                raise ApplicationError("MCP_AUTH_INVALID")
            record = StoredMcpConnection(plugin_id=key[0], server_id=key[1], config_hash=identity,
                generation=str(request.operation_id), status="connecting", updated_at=now_ms())
            self.repository.save(record)
            try:
                self.plugins.set_mcp_enabled(*key, True)
                previous = self._tasks.pop(key, None)
                if previous:
                    previous.cancel()
                token = request.token.get_secret_value() if request.token else None
                self._tasks[key] = kernel.start_task(self._login, record, config, token,
                    owner_id=f"mcp-login:{key[0]}:{key[1]}", deadline=time.monotonic() + 600)
            except Exception:
                self.repository.save(record.model_copy(update={"status": "error", "error_code": "mcp_connection_failed",
                    "updated_at": now_ms()}), expected_generation=record.generation)
                raise ApplicationError("RUNTIME_DRAINING") from None
            return self.read(*key)

    async def _login(self, record: StoredMcpConnection, config: "McpServerConfigV1", token: str | None) -> None:
        auth = config.auth or RemoteMcpAuth()
        credentials = Credentials(token=OAuthToken(access_token=token)) if token else Credentials()

        async def present(url: str, code: str | None = None) -> None:
            parsed = urlsplit(url)
            if parsed.scheme != "https" or parsed.username or parsed.password or len(url) > 8192:
                raise McpAuthError("mcp_login_url_invalid")
            with self._lock:
                self._assert_current(record)
                self._presentations[record.generation] = (url, code)

        async def load() -> Credentials:
            return credentials

        async def save(value: Credentials) -> None:
            nonlocal credentials
            credentials = value

        try:
            if not token and auth.type == "github-device":
                credentials = await github_device_login(str(self._github_client(auth)), auth.scopes, present)
            if not token and auth.type == "oauth":
                async with oauth_callback() as (redirect_uri, callback):
                    if auth.client_id:
                        credentials = credentials.model_copy(update={"client_info": OAuthClientInformationFull(
                            client_id=auth.client_id, redirect_uris=[redirect_uri], token_endpoint_auth_method="none")})
                    provider = oauth_provider(str(config.url), auth, OAuthStorage(load, save),
                                              redirect_uri=redirect_uri, redirect=present, callback=callback)
                    await verify_remote(str(config.url), auth=provider)
                    metadata = provider.context.oauth_metadata
                    credentials = credentials.model_copy(update={"token_endpoint": str(metadata.token_endpoint) if metadata and metadata.token_endpoint else None})
            else:
                account = await verify_remote(str(config.url), token=credentials.token.access_token if credentials.token else None)
                credentials = credentials.model_copy(update={"account_label": account})
            self._publish(record, credentials, status="connected")
        except BaseException as error:
            if isinstance(error, anyio.get_cancelled_exc_class()):
                code = "mcp_login_canceled"
            elif isinstance(error, McpAuthError):
                code = str(error)
            elif isinstance(error, TimeoutError):
                code = "mcp_login_timeout"
            elif isinstance(error, httpx2.HTTPStatusError) and error.response.status_code in {401, 403}:
                code = "mcp_login_required" if error.response.status_code == 401 else "mcp_access_denied"
            else:
                code = "mcp_connection_failed"
            self.repository.save(record.model_copy(update={"status": "error", "error_code": code, "updated_at": now_ms()}),
                                 expected_generation=record.generation)
        finally:
            with self._lock:
                self._presentations.pop(record.generation, None)
                self.secrets.prune(self.repository.retained)

    def _assert_current(self, record: StoredMcpConnection) -> StoredMcpConnection:
        current = self.repository.get(record.plugin_id, record.server_id)
        _, identity = self.binding(record.plugin_id, record.server_id)
        plugin = self.plugins.store.plugin_record(record.plugin_id)
        consent = self.plugins.store.mcp_server_state(record.plugin_id, record.server_id)["consented"]
        if (not current or current.generation != record.generation or current.config_hash != identity
                or not plugin or not plugin["enabled"] or not consent):
            raise McpAuthError("mcp_not_authorized")
        return current

    def _publish(self, record: StoredMcpConnection, credentials: Credentials, *, status: str = "connected") -> None:
        encoded = credentials.model_dump_json()
        if len(encoded.encode()) > 32768:
            raise McpAuthError("mcp_credential_invalid")
        with self._lock, self.plugins.store.lock:
            self._assert_current(record)
            reference = self.secrets.write(encoded)
            try:
                saved = self.repository.save(record.model_copy(update={"status": status, "credential_ref": reference,
                    "account_label": credentials.account_label, "expires_at": credentials.expires_at,
                    "updated_at": now_ms()}), expected_generation=record.generation)
                if not saved:
                    raise McpAuthError("mcp_not_authorized")
            finally:
                self.secrets.prune(self.repository.retained)

    def _load(self, record: StoredMcpConnection) -> Credentials:
        with self._lock, self.plugins.store.lock:
            current = self._assert_current(record)
            if current.status != "connected" or not current.credential_ref:
                raise McpAuthError("mcp_login_required")
            try:
                return Credentials.model_validate_json(self.secrets.read(current.credential_ref))
            except (ValueError, OSError):
                self.repository.save(current.model_copy(update={"status": "expired", "error_code": "mcp_login_required",
                    "credential_ref": None, "updated_at": now_ms()}), expected_generation=current.generation)
                raise McpAuthError("mcp_login_required") from None

    def checker(self, plugin_id: str, server_id: str) -> Callable[[], None]:
        record = self.repository.get(plugin_id, server_id)

        def check() -> None:
            if record is None:
                raise McpAuthError("mcp_login_required")
            self._load(record)
        return check

    def http_client(self, plugin_id: str, config: "McpServerConfigV1") -> httpx2.AsyncClient:
        record = self.repository.get(plugin_id, config.id)
        if record is None:
            raise McpAuthError("mcp_login_required")
        self._load(record)
        auth = config.auth or RemoteMcpAuth()

        async def load() -> Credentials:
            return await anyio.to_thread.run_sync(self._load, record)

        async def save(value: Credentials) -> None:
            await anyio.to_thread.run_sync(lambda: self._publish(record, value))

        async def token_value() -> str:
            key = str(self.plugins.store.data_directory), record.plugin_id, record.server_id, record.generation
            lock = credential_refresh_lock(key)
            async with lock:
                credentials = await load()
                if credentials.expires_at is not None and credentials.expires_at <= now_ms() + 30000:
                    try:
                        if auth.type == "github-device":
                            credentials = await refresh_github(credentials)
                        elif auth.type == "oauth":
                            credentials = await refresh_oauth(credentials)
                        if credentials.expires_at is not None and credentials.expires_at <= now_ms():
                            raise McpAuthError("mcp_login_required")
                        await save(credentials)
                    except McpAuthError:
                        self.repository.save(record.model_copy(update={"status": "expired", "credential_ref": None,
                            "error_code": "mcp_login_required", "updated_at": now_ms()}), expected_generation=record.generation)
                        raise McpAuthError("mcp_login_required") from None
                    except Exception:
                        raise McpAuthError("mcp_connection_failed") from None
                if not credentials.token:
                    raise McpAuthError("mcp_login_required")
                return credentials.token.access_token

        async def check(request: httpx2.Request) -> None:
            # Never forward a credential to a different origin, including a redirect.
            if request.url.copy_with(path="/", query=None) != httpx2.URL(str(config.url)).copy_with(path="/", query=None):
                raise McpAuthError("mcp_origin_mismatch")
            await anyio.to_thread.run_sync(self._assert_current, record)

        async def check_response(response: httpx2.Response) -> None:
            if response.status_code in {401, 403}:
                current = self.repository.get(record.plugin_id, record.server_id)
                if current:
                    self.repository.save(current.model_copy(update={"status": "expired" if response.status_code == 401 else "error",
                        "error_code": "mcp_login_required" if response.status_code == 401 else "mcp_access_denied",
                        "updated_at": now_ms()}), expected_generation=record.generation)

        provider = _Bearer(token_value) if auth.type != "none" else None
        options = client_kwargs()
        # Eidos owns startup/tool deadlines. An idle GET stream must not expire a ready session.
        options["timeout"] = httpx2.Timeout(15, read=None)
        return httpx2.AsyncClient(**options, auth=provider, event_hooks={"request": [check], "response": [check_response]})

    def close(self) -> None:
        with self._lock:
            tasks = tuple(self._tasks.items())
            self._tasks.clear()
            for key, task in tasks:
                if not task.done():
                    record = self.repository.get(*key)
                    if record and record.status == "connecting":
                        self.repository.save(record.model_copy(update={"generation": str(uuid.uuid4()), "status": "disconnected",
                            "error_code": "mcp_login_interrupted", "updated_at": now_ms()}))
                    task.cancel()
        for _key, task in tasks:
            if not task.done():
                task.wait(3)

    def revoke_plugin(self, plugin_id: str) -> None:
        with self._lock:
            for record in self.repository.for_plugin(plugin_id):
                self.repository.save(record.model_copy(update={"generation": str(uuid.uuid4()), "status": "disconnected",
                    "credential_ref": None, "account_label": None, "expires_at": None, "error_code": None, "updated_at": now_ms()}),
                    expected_generation=record.generation)
                task = self._tasks.pop((record.plugin_id, record.server_id), None)
                if task and not task.done():
                    task.cancel()
                self._presentations.pop(record.generation, None)
            self.secrets.prune(self.repository.retained)

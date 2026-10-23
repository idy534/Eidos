"""Provider mechanics; connection consent and durable generations belong to Eidos."""
from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit
import time
import logging
from weakref import WeakValueDictionary

import anyio
from anyio.lowlevel import RunVar
import httpx2
from authlib.integrations.httpx_client import AsyncOAuth2Client
from authlib.integrations.base_client.errors import OAuthError
from mcp import ClientSession
from mcp.client.auth import OAuthClientProvider
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import AuthorizationCodeResult, OAuthClientInformationFull, OAuthClientMetadata, OAuthToken
from pydantic import Field

from eidos_runtime.models import EidosFrozenModel
from eidos_runtime.models.mcp_connections import RemoteMcpAuth

logging.getLogger("mcp.client.auth").setLevel(logging.CRITICAL)
logging.getLogger("mcp.client.streamable_http").setLevel(logging.CRITICAL)

MAX_HTTP_BYTES = 2 * 1024 * 1024
_REFRESH_LOCKS: RunVar[WeakValueDictionary[tuple[str, ...], anyio.Lock]] = RunVar("eidos_mcp_refresh_locks")


def credential_refresh_lock(key: tuple[str, ...]) -> anyio.Lock:
    """All catalog views on the owner loop share rotation locks, never tool execution locks."""
    try:
        locks = _REFRESH_LOCKS.get()
    except LookupError:
        locks = WeakValueDictionary()
        _REFRESH_LOCKS.set(locks)
    lock = locks.get(key)
    if lock is None:
        lock = anyio.Lock()
        locks[key] = lock
    return lock


class McpAuthError(RuntimeError):
    pass


class Credentials(EidosFrozenModel):
    token: OAuthToken | None = Field(default=None, repr=False)
    client_info: OAuthClientInformationFull | None = Field(default=None, repr=False)
    expires_at: int | None = None
    account_label: str | None = None
    client_id: str | None = None
    token_endpoint: str | None = None


class _BoundedStream(httpx2.AsyncByteStream):
    def __init__(self, stream: httpx2.AsyncByteStream) -> None:
        self.stream = stream

    async def __aiter__(self):
        received = 0
        async for chunk in self.stream:
            received += len(chunk)
            if received > MAX_HTTP_BYTES:
                raise httpx2.StreamError("mcp_response_too_large")
            yield chunk

    async def aclose(self) -> None:
        await self.stream.aclose()


class BoundedMcpTransport(httpx2.AsyncBaseTransport):
    """Bound HTTP response bytes. The official SDK still parses JSON and SSE."""
    def __init__(self, transport: httpx2.AsyncBaseTransport | None = None) -> None:
        self.transport = transport or httpx2.AsyncHTTPTransport(retries=0, trust_env=False)

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        if request.url.scheme != "https" and not (
            request.url.scheme == "http" and request.url.host in {"127.0.0.1", "::1", "localhost"}
        ):
            raise McpAuthError("mcp_url_invalid")
        response = await self.transport.handle_async_request(request)
        if response.headers.get("Content-Encoding", "identity").lower() != "identity":
            await response.aclose()
            raise httpx2.StreamError("mcp_compressed_response_unsupported")
        return httpx2.Response(response.status_code, headers=response.headers,
                               stream=_BoundedStream(response.stream), extensions=response.extensions)

    async def aclose(self) -> None:
        await self.transport.aclose()


class DeviceCode(EidosFrozenModel):
    device_code: str = Field(min_length=1, max_length=4096, repr=False)
    user_code: str = Field(min_length=1, max_length=128)
    verification_uri: str
    expires_in: int = Field(gt=0, le=3600)
    interval: int = Field(default=5, ge=1, le=60)


def expires_at(token: OAuthToken) -> int | None:
    return int(time.time() * 1000) + token.expires_in * 1000 if token.expires_in is not None else None


def client_kwargs() -> dict[str, object]:
    return {"timeout": httpx2.Timeout(15), "follow_redirects": False, "trust_env": False,
            "transport": BoundedMcpTransport(),
            "headers": {"Accept": "application/json", "Accept-Encoding": "identity", "User-Agent": "Eidos-Desktop"}}


async def github_device_login(client_id: str, scopes: list[str],
                              present: Callable[[str, str | None], Awaitable[None]]) -> Credentials:
    async with AsyncOAuth2Client(client_id, token_endpoint_auth_method="none", **client_kwargs()) as client:
        response = await client.request("POST", "https://github.com/login/device/code", withhold_token=True,
                                     data={"client_id": client_id, "scope": " ".join(scopes)})
        if response.status_code != 200 or len(response.content) > 16384:
            raise McpAuthError("mcp_login_failed")
        device = DeviceCode.model_validate(response.json())
        if device.verification_uri != "https://github.com/login/device":
            raise McpAuthError("mcp_login_url_invalid")
        await present(device.verification_uri, device.user_code)
        interval = device.interval
        with anyio.fail_after(device.expires_in):
            while True:
                await anyio.sleep(interval)
                try:
                    raw = await client.fetch_token(
                        "https://github.com/login/oauth/access_token",
                        grant_type="urn:ietf:params:oauth:grant-type:device_code", device_code=device.device_code,
                    )
                    token = OAuthToken.model_validate(raw)
                    return Credentials(token=token, expires_at=expires_at(token), client_id=client_id)
                except OAuthError as error:
                    if error.error == "authorization_pending":
                        continue
                    if error.error == "slow_down":
                        interval += 5
                        continue
                    raise McpAuthError("mcp_login_denied" if error.error == "access_denied" else "mcp_login_failed") from None


async def refresh_github(credentials: Credentials) -> Credentials:
    if not credentials.token or not credentials.token.refresh_token or not credentials.client_id:
        raise McpAuthError("mcp_login_required")
    async with AsyncOAuth2Client(credentials.client_id, token_endpoint_auth_method="none", **client_kwargs()) as client:
        try:
            raw = await client.refresh_token("https://github.com/login/oauth/access_token",
                                             refresh_token=credentials.token.refresh_token)
        except OAuthError:
            raise McpAuthError("mcp_login_required") from None
    token = OAuthToken.model_validate(raw)
    return credentials.model_copy(update={"token": token, "expires_at": expires_at(token)})


async def refresh_oauth(credentials: Credentials) -> Credentials:
    client_info = credentials.client_info
    if not client_info or not credentials.token or not credentials.token.refresh_token or not credentials.token_endpoint:
        raise McpAuthError("mcp_login_required")
    async with AsyncOAuth2Client(client_info.client_id, client_secret=client_info.client_secret,
            token_endpoint_auth_method=client_info.token_endpoint_auth_method or "none", **client_kwargs()) as client:
        try:
            raw = await client.refresh_token(credentials.token_endpoint, refresh_token=credentials.token.refresh_token)
        except OAuthError:
            raise McpAuthError("mcp_login_required") from None
    token = OAuthToken.model_validate(raw)
    return credentials.model_copy(update={"token": token, "expires_at": expires_at(token)})


class OAuthStorage:
    def __init__(self, load: Callable[[], Awaitable[Credentials]],
                 save: Callable[[Credentials], Awaitable[None]]) -> None:
        self.load, self.save = load, save

    async def get_tokens(self) -> OAuthToken | None:
        credentials = await self.load()
        token = credentials.token
        if token and credentials.expires_at is not None:
            token = token.model_copy(update={"expires_in": max(0, (credentials.expires_at - int(time.time() * 1000)) // 1000)})
        return token

    async def set_tokens(self, tokens: OAuthToken) -> None:
        credentials = await self.load()
        await self.save(credentials.model_copy(update={"token": tokens, "expires_at": expires_at(tokens)}))

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return (await self.load()).client_info

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        await self.save((await self.load()).model_copy(update={"client_info": client_info}))


def oauth_provider(url: str, auth: RemoteMcpAuth, storage: OAuthStorage, *, redirect_uri: str,
                   redirect: Callable[[str], Awaitable[None]] | None = None,
                   callback: Callable[[], Awaitable[AuthorizationCodeResult]] | None = None) -> OAuthClientProvider:
    metadata = OAuthClientMetadata(redirect_uris=[redirect_uri], client_name="Eidos",
                                   token_endpoint_auth_method="none", scope=" ".join(auth.scopes) or None)
    return OAuthClientProvider(server_url=url, client_metadata=metadata, storage=storage,
                               redirect_handler=redirect, callback_handler=callback,
                               client_metadata_url=auth.client_metadata_url)


async def verify_remote(url: str, *, auth: httpx2.Auth | None = None,
                        token: str | None = None) -> str | None:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx2.AsyncClient(**client_kwargs(), auth=auth) as client:
        # Explicit token headers are scoped to the configured endpoint by the SDK's redirect policy.
        client.headers.update(headers)
        async with streamable_http_client(url, http_client=client) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                from eidos_runtime.extensions.mcp import _discover_tools
                await _discover_tools(session)
        if urlsplit(url).hostname == "api.githubcopilot.com" and token:
            response = await client.get("https://api.github.com/user")
            if response.status_code == 200 and len(response.content) <= 65536:
                login = response.json().get("login")
                if isinstance(login, str):
                    return login[:128]
    return None


@asynccontextmanager
async def oauth_callback() -> AsyncIterator[tuple[str, Callable[[], Awaitable[AuthorizationCodeResult]]]]:
    received = anyio.Event()
    result: AuthorizationCodeResult | None = None

    class Callback(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            nonlocal result
            parsed = urlsplit(self.path)
            try:
                query = parse_qs(parsed.query, max_num_fields=8)
            except ValueError:
                self.send_error(400)
                return
            if (parsed.path != "/callback" or len(self.path) > 8192
                    or any(len(values) != 1 for values in query.values())
                    or not query.get("code") or not query.get("state")):
                self.send_error(400)
                return
            result = AuthorizationCodeResult(code=query["code"][0], state=query["state"][0],
                                               iss=query.get("iss", [None])[0])
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write("授权已收到，请返回 Eidos。".encode())
            anyio.from_thread.run_sync(received.set)

        def log_message(self, format: str, *args: object) -> None:
            pass

    class CallbackServer(HTTPServer):
        def get_request(self):
            connection, address = super().get_request()
            connection.settimeout(1)
            return connection, address

    server = CallbackServer(("127.0.0.1", 0), Callback)
    server.timeout = 0.25

    async def serve() -> None:
        while not received.is_set():
            await anyio.to_thread.run_sync(server.handle_request)

    async def callback() -> AuthorizationCodeResult:
        await received.wait()
        assert result is not None
        return result

    try:
        async with anyio.create_task_group() as group:
            group.start_soon(serve)
            yield f"http://127.0.0.1:{server.server_port}/callback", callback
            group.cancel_scope.cancel()
    finally:
        server.server_close()

from __future__ import annotations

import json
import base64
import hashlib
from urllib.parse import parse_qs, urlsplit

import anyio
import httpx2
import pytest
from mcp.shared.auth import OAuthToken

from eidos_runtime.infrastructure import mcp_auth
from eidos_runtime.infrastructure.mcp_auth import Credentials, McpAuthError, github_device_login, refresh_github, oauth_callback
from eidos_runtime.models.mcp_connections import McpConnectionRequest, RemoteMcpAuth


def test_device_flow_pending_slow_down_refresh_and_no_client_secret(monkeypatch):
    requests = []
    delays = []
    presentations = []
    original_kwargs = mcp_auth.client_kwargs
    original_sleep = anyio.sleep

    async def short_sleep(delay):
        delays.append(delay)
        await original_sleep(0)

    def handler(request):
        parameters = parse_qs(request.content.decode())
        requests.append((request.url.path, parameters))
        assert "client_secret" not in parameters
        if request.url.path == "/login/device/code":
            return httpx2.Response(200, json={"device_code": "private-device", "user_code": "ABCD-EFGH",
                "verification_uri": "https://github.com/login/device", "expires_in": 600, "interval": 1})
        if parameters.get("grant_type") == ["refresh_token"]:
            return httpx2.Response(200, json={"access_token": "refreshed", "token_type": "bearer", "expires_in": 3600, "refresh_token": "rotated"})
        count = sum(path == "/login/oauth/access_token" for path, _ in requests)
        if count < 3:
            return httpx2.Response(200, json={"error": "authorization_pending" if count == 1 else "slow_down"})
        return httpx2.Response(200, json={"access_token": "access", "token_type": "bearer", "expires_in": 3600, "refresh_token": "refresh"})

    monkeypatch.setattr(mcp_auth, "client_kwargs", lambda: {**original_kwargs(), "transport": httpx2.MockTransport(handler)})
    monkeypatch.setattr(anyio, "sleep", short_sleep)

    async def scenario():
        async def present(url, code):
            presentations.append((url, code))
        result = await github_device_login("eidos-client", ["repo"], present)
        assert result.token.access_token == "access"
        updated = await refresh_github(result)
        assert updated.token.refresh_token == "rotated"
    anyio.run(scenario)
    assert delays == [1, 1, 6]
    assert presentations == [("https://github.com/login/device", "ABCD-EFGH")]
    assert requests[1][1]["client_id"] == ["eidos-client"]


def test_device_login_refuses_provider_supplied_external_verification_url(monkeypatch):
    original = mcp_auth.client_kwargs
    monkeypatch.setattr(mcp_auth, "client_kwargs", lambda: {**original(), "transport": httpx2.MockTransport(
        lambda _: httpx2.Response(200, json={"device_code": "secret", "user_code": "code",
            "verification_uri": "https://attacker.invalid/", "expires_in": 60})
    )})
    async def scenario():
        async def present(_url, _code):
            raise AssertionError("untrusted URL reached the Desktop")
        with pytest.raises(McpAuthError, match="mcp_login_url_invalid"):
            await github_device_login("client", [], present)
    anyio.run(scenario)


def test_token_request_repr_and_public_contract_do_not_reveal_credentials():
    request = McpConnectionRequest(plugin_id="demo", server_id="demo", action="connect", method="token",
                                   token="private-token", operation_id="attempt")
    assert "private-token" not in repr(request)
    assert "private-token" not in request.model_dump_json()
    assert "private-token" not in repr(Credentials(token=OAuthToken(access_token="private-token")))


def test_oauth_loopback_callback_and_close():
    async def scenario():
        async with oauth_callback() as (redirect, callback):
            async with httpx2.AsyncClient(timeout=3, trust_env=False) as client:
                bad = await client.get(redirect + "?state=state")
                assert bad.status_code == 400
                good = await client.get(redirect + "?code=authorization-code&state=state&iss=https%3A%2F%2Fissuer.example")
                assert good.status_code == 200
            result = await callback()
            assert result.code == "authorization-code" and result.iss == "https://issuer.example"
        with pytest.raises(httpx2.ConnectError):
            async with httpx2.AsyncClient(timeout=1, trust_env=False) as client:
                await client.get(redirect)
    anyio.run(scenario)


def test_official_sdk_oauth_discovery_pkce_callback_and_refresh(monkeypatch):
    original = mcp_auth.client_kwargs
    challenge = []
    credentials = Credentials()

    def handler(request):
        if request.url.host == "auth.example":
            if ".well-known" in request.url.path:
                return httpx2.Response(200, json={"issuer": "https://auth.example", "authorization_endpoint": "https://auth.example/authorize",
                    "token_endpoint": "https://auth.example/token", "registration_endpoint": "https://auth.example/register",
                    "grant_types_supported": ["authorization_code", "refresh_token"], "token_endpoint_auth_methods_supported": ["none"],
                    "code_challenge_methods_supported": ["S256"], "response_types_supported": ["code"]})
            if request.url.path == "/register":
                return httpx2.Response(201, json={**json.loads(request.content), "client_id": "registered-client"})
            params = parse_qs(request.content.decode())
            if params.get("grant_type") == ["refresh_token"]:
                return httpx2.Response(200, json={"access_token": "new-access", "refresh_token": "new-refresh", "token_type": "Bearer", "expires_in": 3600})
            assert params["code"] == ["callback-code"]
            actual = base64.urlsafe_b64encode(hashlib.sha256(params["code_verifier"][0].encode()).digest()).decode().rstrip("=")
            assert actual == challenge[0]
            return httpx2.Response(200, json={"access_token": "access", "refresh_token": "refresh", "token_type": "Bearer", "expires_in": 3600})
        if ".well-known" in request.url.path:
            return httpx2.Response(200, json={"resource": "https://mcp.example/mcp", "authorization_servers": ["https://auth.example"]})
        if request.headers.get("Authorization") != "Bearer access":
            return httpx2.Response(401, headers={"WWW-Authenticate": 'Bearer resource_metadata="https://mcp.example/.well-known/oauth-protected-resource/mcp"'})
        if request.method != "POST":
            return httpx2.Response(405)
        payload = json.loads(request.content)
        if "id" not in payload:
            return httpx2.Response(202)
        result = {"protocolVersion": payload["params"]["protocolVersion"], "capabilities": {"tools": {}}, "serverInfo": {"name": "oauth", "version": "1"}} if payload["method"] == "initialize" else {"tools": []}
        return httpx2.Response(200, json={"jsonrpc": "2.0", "id": payload["id"], "result": result})

    monkeypatch.setattr(mcp_auth, "client_kwargs", lambda: {**original(), "transport": httpx2.MockTransport(handler)})

    async def scenario():
        nonlocal credentials
        async def load():
            return credentials
        async def save(value):
            nonlocal credentials
            credentials = value
        async def present(url):
            parameters = parse_qs(urlsplit(url).query)
            challenge.append(parameters["code_challenge"][0])
            async with httpx2.AsyncClient(timeout=3, trust_env=False) as client:
                response = await client.get(parameters["redirect_uri"][0], params={"code": "callback-code", "state": parameters["state"][0], "iss": "https://auth.example"})
                assert response.status_code == 200
        async with oauth_callback() as (uri, callback):
            provider = mcp_auth.oauth_provider("https://mcp.example/mcp", RemoteMcpAuth(type="oauth"),
                mcp_auth.OAuthStorage(load, save), redirect_uri=uri, redirect=present, callback=callback)
            await mcp_auth.verify_remote("https://mcp.example/mcp", auth=provider)
        assert credentials.client_info.client_id == "registered-client"
        assert credentials.token.access_token == "access"
        credentials = credentials.model_copy(update={"token_endpoint": "https://auth.example/token"})
        assert (await mcp_auth.refresh_oauth(credentials)).token.refresh_token == "new-refresh"
    anyio.run(scenario)


def test_remote_http_response_bytes_are_bounded():
    class LargeStream(httpx2.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(3):
                yield b"x" * (1024 * 1024)
    async def scenario():
        transport = mcp_auth.BoundedMcpTransport(httpx2.MockTransport(lambda _: httpx2.Response(200, stream=LargeStream())))
        async with httpx2.AsyncClient(transport=transport) as client:
            with pytest.raises(httpx2.StreamError, match="mcp_response_too_large"):
                await client.get("https://fixture.example")
    anyio.run(scenario)


def test_oauth_discovery_cannot_downgrade_token_requests_to_plain_http():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx2.Response(200, json={})

    async def scenario():
        transport = mcp_auth.BoundedMcpTransport(httpx2.MockTransport(handler))
        async with httpx2.AsyncClient(transport=transport) as client:
            with pytest.raises(McpAuthError, match="mcp_url_invalid"):
                await client.post("http://provider.example/token", data={"refresh_token": "fixture-refresh"})
            assert requests == []
            assert (await client.get("http://127.0.0.1/metadata")).status_code == 200

    anyio.run(scenario)


def test_python_connection_fixtures_match_the_generated_contract():
    from pathlib import Path
    from eidos_runtime.models.mcp_connections import McpConnectionState, BuiltinPluginsResponse
    from pydantic import ValidationError
    fixture = json.loads((Path(__file__).resolve().parents[2] / "protocol/fixtures/mcp-connections-v1.json").read_text())
    McpConnectionState.model_validate(fixture["connected"])
    McpConnectionRequest.model_validate(fixture["connect"])
    BuiltinPluginsResponse.model_validate(fixture["builtins"])
    with pytest.raises(ValidationError):
        McpConnectionState.model_validate({**fixture["connected"], "accessToken": "forbidden"})

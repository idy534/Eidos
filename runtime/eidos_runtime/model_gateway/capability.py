from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import logging
import threading
import time
import uuid

import httpx
from pydantic import BaseModel, ConfigDict

from eidos_runtime.model_gateway.errors import EidosModelError, normalize_http_error
from eidos_runtime.model_gateway.models import (
    CapabilityProbeSource,
    CapabilitySnapshot,
    ModelProfile,
    WireAPI,
)
from eidos_runtime.model_gateway.registry import AdapterRegistry


logger = logging.getLogger("eidos.runtime.model_gateway")


class CapabilityProbeError(RuntimeError):
    def __init__(self, error: EidosModelError) -> None:
        self.error = error
        super().__init__(error.code)


class TestConnectionResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    success: bool
    profile_valid: bool
    endpoint_identity: str
    capability_snapshot: CapabilitySnapshot | None = None
    warnings: tuple[dict[str, object], ...] = ()
    error: EidosModelError | None = None
    probe_duration_ms: int


class CapabilityProbe:
    def __init__(
        self,
        *,
        registry: AdapterRegistry | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.registry = registry or AdapterRegistry.default()
        self.transport = transport

    def probe(
        self,
        profile: ModelProfile,
        api_key: str,
        cancel: threading.Event,
    ) -> CapabilitySnapshot:
        if cancel.is_set():
            raise RuntimeError("MODEL_PROBE_CANCELLED")
        logger.info(
            "Capability probe started profile_id=%s provider=%s wire_api=%s model_id=%s",
            profile.id,
            profile.provider,
            profile.wire_api.value,
            profile.model_id,
        )
        snapshot = asyncio.run(self._probe(profile, api_key, cancel))
        logger.info(
            "Capability probe succeeded profile_id=%s provider=%s wire_api=%s model_id=%s",
            profile.id,
            profile.provider,
            profile.wire_api.value,
            profile.model_id,
        )
        return snapshot

    def test_connection(
        self,
        profile: ModelProfile,
        api_key: str,
        cancel: threading.Event,
    ) -> TestConnectionResult:
        started = time.monotonic()
        try:
            snapshot = self.probe(profile, api_key, cancel)
        except CapabilityProbeError as failure:
            return TestConnectionResult(
                success=False,
                profile_valid=True,
                endpoint_identity=profile.base_url or "",
                error=failure.error,
                probe_duration_ms=int((time.monotonic() - started) * 1000),
            )
        return TestConnectionResult(
            success=True,
            profile_valid=True,
            endpoint_identity=profile.base_url or "",
            capability_snapshot=snapshot,
            warnings=tuple(warning.model_dump(mode="json") for warning in snapshot.warnings),
            probe_duration_ms=int((time.monotonic() - started) * 1000),
        )

    async def _probe(
        self,
        profile: ModelProfile,
        api_key: str,
        cancel: threading.Event,
    ) -> CapabilitySnapshot:
        if profile.base_url is None:
            raise ValueError("model profile base URL is required")
        provider = self.registry.provider(profile.provider)
        self.registry.wire(profile.wire_api)
        timeout = httpx.Timeout(min(profile.request_timeout, 15.0))
        verified: dict[str, bool | int | None] = {}
        async with httpx.AsyncClient(
            timeout=timeout,
            transport=self.transport,
            follow_redirects=False,
        ) as client:
            headers = {
                **provider.auth_headers(api_key),
                "Content-Type": "application/json",
            }
            response = await _post_cancellable(
                client,
                *_probe_request(profile, "basic"),
                headers,
                cancel,
                profile,
            )
            _ensure_success(response, profile)
            for capability, kind in (
                ("supports_tools", "tools"),
                ("supports_structured_output", "structured"),
            ):
                if getattr(profile, capability) is not True:
                    continue
                probe_response = await _post_cancellable(
                    client,
                    *_probe_request(profile, kind),
                    headers,
                    cancel,
                    profile,
                )
                if probe_response.status_code < 400:
                    verified[capability] = True
        return CapabilitySnapshot.conservative(
            profile,
            snapshot_id=str(uuid.uuid4()),
            probe_source=CapabilityProbeSource.ACTIVE_PROBE,
            probe_version="r2-v1",
            probed_at=datetime.now(UTC),
            verified=verified,
        )


async def _wait_for_cancel(cancel: threading.Event) -> None:
    while not cancel.is_set():
        await asyncio.sleep(0.025)


async def _post_cancellable(
    client: httpx.AsyncClient,
    endpoint: str,
    body: dict[str, object],
    headers: dict[str, str],
    cancel: threading.Event,
    profile: ModelProfile,
) -> httpx.Response:
    request = asyncio.create_task(client.post(endpoint, headers=headers, json=body))
    cancelled = asyncio.create_task(_wait_for_cancel(cancel))
    done, _ = await asyncio.wait(
        {request, cancelled},
        return_when=asyncio.FIRST_COMPLETED,
    )
    if cancelled in done:
        request.cancel()
        try:
            await request
        except asyncio.CancelledError:
            pass
        raise RuntimeError("MODEL_PROBE_CANCELLED")
    cancelled.cancel()
    try:
        await cancelled
    except asyncio.CancelledError:
        pass
    try:
        return request.result()
    except (httpx.TimeoutException, httpx.NetworkError) as failure:
        raise CapabilityProbeError(normalize_http_error(
            504 if isinstance(failure, httpx.TimeoutException) else 503,
            provider=profile.provider,
            wire_api=profile.wire_api,
            model_id=profile.model_id,
            attempt_id="probe",
        )) from None


def _ensure_success(response: httpx.Response, profile: ModelProfile) -> None:
    if response.status_code < 400:
        return
    raise CapabilityProbeError(normalize_http_error(
        response.status_code,
        provider=profile.provider,
        wire_api=profile.wire_api,
        model_id=profile.model_id,
        attempt_id="probe",
        provider_request_id=response.headers.get("x-request-id"),
        retry_after=_retry_after(response.headers.get("retry-after")),
        diagnostic=response.text[:2_000],
    ))


def _probe_request(
    profile: ModelProfile,
    kind: str,
) -> tuple[str, dict[str, object]]:
    base = profile.base_url or ""
    common: dict[str, object] = {
        "model": profile.model_id,
        "max_tokens": 1,
        "stream": False,
    }
    if profile.wire_api is WireAPI.OPENAI_RESPONSES:
        body: dict[str, object] = {
            "model": profile.model_id,
            "input": "Reply with OK.",
            "max_output_tokens": 1,
            "stream": False,
        }
        if kind == "tools":
            body["tools"] = [_openai_tool()]
            body["tool_choice"] = "none"
        elif kind == "structured":
            body["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": "probe",
                    "strict": True,
                    "schema": _probe_schema(),
                }
            }
        return f"{base}/responses", body
    if profile.wire_api is WireAPI.ANTHROPIC_MESSAGES:
        path = "" if base.endswith("/v1") else "/v1"
        body = {
            **common,
            "messages": [{"role": "user", "content": "Reply with OK."}],
        }
        if kind == "tools":
            body["tools"] = [{
                "name": "capability_probe",
                "description": "A no-op schema acceptance probe.",
                "input_schema": _probe_schema(),
            }]
            body["tool_choice"] = {"type": "none"}
        elif kind == "structured":
            body["output_config"] = {
                "format": {"type": "json_schema", "schema": _probe_schema()}
            }
        return f"{base}{path}/messages", body
    body = {
        **common,
        "messages": [{"role": "user", "content": "Reply with OK."}],
    }
    if kind == "tools":
        body["tools"] = [{"type": "function", "function": _openai_tool()}]
        body["tool_choice"] = "none"
    elif kind == "structured":
        body["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "probe",
                "strict": True,
                "schema": _probe_schema(),
            },
        }
    return f"{base}/chat/completions", body


def _openai_tool() -> dict[str, object]:
    return {
        "name": "capability_probe",
        "description": "A no-op schema acceptance probe.",
        "parameters": _probe_schema(),
    }


def _probe_schema() -> dict[str, object]:
    return {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    }


def _retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except ValueError:
        return None
    return result if result >= 0 else None

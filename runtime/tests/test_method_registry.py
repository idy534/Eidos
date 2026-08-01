from __future__ import annotations

from io import StringIO

import pytest
from pydantic import Field, RootModel

from eidos_runtime.protocol.registry import (
    JsonObjectParams,
    JsonObjectResult,
    MethodRegistration,
    MethodRegistry,
    MethodRegistryError,
    MethodValidationError,
)
from eidos_runtime.protocol.schemas import ClosedModel
from eidos_runtime.protocol.server import RuntimeServer


class Params(RootModel[dict[str, object]]):
    pass


class Result(RootModel[dict[str, object]]):
    pass


class SessionReadResult(ClosedModel):
    session_id: str = Field(alias="sessionId")


def test_method_registry_validates_params_and_dispatches_typed_handler() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def handler(request_id: str, params: Params) -> Result:
        calls.append((request_id, params.root))
        return Result({"sessionId": "s-1"})

    registry = MethodRegistry()
    registry.register(
        MethodRegistration(
            name="session/read",
            request_type=Params,
            response_type=Result,
            handler=handler,
            requires_initialized=True,
        )
    )

    assert registry.dispatch("session/read", "request-1", {"sessionId": "s-1"}) == {
        "sessionId": "s-1"
    }
    assert calls == [("request-1", {"sessionId": "s-1"})]
    assert registry.spec("session/read").requires_initialized is True

    with pytest.raises(MethodValidationError):
        registry.dispatch("session/read", "request-2", ["not-an-object"])


def test_method_registry_rejects_duplicates_and_preserves_unknown_methods() -> None:
    registry = MethodRegistry()
    registration = MethodRegistration(
        name="runtime/health",
        request_type=Params,
        response_type=Result,
        handler=lambda _request_id, _params: Result({}),
    )
    registry.register(registration)

    with pytest.raises(MethodRegistryError, match="duplicate"):
        registry.register(registration)
    assert registry.dispatch("unknown/method", "request-1", {}) is False


def test_method_registry_serializes_a_validated_typed_handler_response() -> None:
    registry = MethodRegistry()
    registry.register(
        MethodRegistration(
            name="session/read",
            request_type=Params,
            response_type=SessionReadResult,
            handler=lambda *_args: SessionReadResult(sessionId="session-1"),
        )
    )

    assert registry.dispatch(
        "session/read", "request-1", {"sessionId": "session-1"}
    ) == {"sessionId": "session-1"}


def test_method_registry_rejects_an_invalid_handler_response() -> None:
    registry = MethodRegistry()
    registry.register(
        MethodRegistration(
            name="session/read",
            request_type=Params,
            response_type=SessionReadResult,
            handler=lambda *_args: object(),
        )
    )

    with pytest.raises(ValueError, match="invalid result for session/read"):
        registry.dispatch("session/read", "request-1", {"sessionId": "session-1"})


def test_production_method_registrations_do_not_use_generic_object_models(
    tmp_path,
) -> None:
    server = RuntimeServer(StringIO(), data_directory=tmp_path / "data")

    generic_methods = [
        registration.name
        for registration in server.method_registry
        if registration.request_type is JsonObjectParams
        or registration.response_type is JsonObjectResult
    ]

    assert generic_methods == []

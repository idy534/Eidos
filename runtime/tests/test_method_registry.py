from __future__ import annotations

from io import StringIO

import pytest
from pydantic import Field, RootModel

import eidos_runtime.protocol.registry as registry_module
from eidos_runtime.protocol.registry import (
    DeferredMethodResult,
    MethodApplicationError,
    MethodErrorMapping,
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


def test_method_registry_exposes_the_typed_response_for_post_send_lifecycle() -> None:
    result = SessionReadResult(sessionId="session-1")
    registry = MethodRegistry()
    registry.register(MethodRegistration(
        name="session/read",
        request_type=Params,
        response_type=SessionReadResult,
        handler=lambda *_args: result,
    ))

    invocation = registry.invoke(
        "session/read", "request-1", {"sessionId": "session-1"}
    )

    assert invocation is not False
    assert invocation.response is result
    assert invocation.json_result == {"sessionId": "session-1"}


def test_method_registry_preserves_delivery_settlement_and_stable_error_mapping() -> None:
    deliveries: list[bool] = []

    class Handler:
        def __call__(self, _request_id: str, _request: Params) -> SessionReadResult:
            return SessionReadResult(sessionId="session-1")

        def settle_response(self, _response: object, delivered: bool) -> None:
            deliveries.append(delivered)

    registry = MethodRegistry()
    registry.register(MethodRegistration(
        name="session/read",
        request_type=Params,
        response_type=SessionReadResult,
        handler=Handler(),
    ))
    invocation = registry.invoke("session/read", "request-1", {"sessionId": "s-1"})
    assert invocation is not False
    invocation.settle_response(delivered=True)
    assert deliveries == [True]

    registry = MethodRegistry()
    registry.register(MethodRegistration(
        name="session/read",
        request_type=Params,
        response_type=SessionReadResult,
        handler=lambda *_args: (_ for _ in ()).throw(RuntimeError("failure")),
        error_mapper=lambda error: (
            MethodErrorMapping("STABLE_FAILURE")
            if isinstance(error, RuntimeError)
            else None
        ),
    ))
    with pytest.raises(MethodApplicationError) as error:
        registry.invoke("session/read", "request-1", {"sessionId": "s-1"})
    assert error.value.mapping == MethodErrorMapping("STABLE_FAILURE")


def test_method_registry_allows_an_explicitly_typed_deferred_result() -> None:
    registry = MethodRegistry()
    registry.register(MethodRegistration(
        name="plugin/import",
        request_type=Params,
        response_type=Result,
        handler=lambda *_args: DeferredMethodResult(),
    ))

    invocation = registry.invoke("plugin/import", "request-1", {})

    assert invocation is not False
    assert invocation.deferred is True
    assert invocation.response is None
    assert invocation.json_result is None


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

    assert not hasattr(registry_module, "JsonObjectParams")
    assert not hasattr(registry_module, "JsonObjectResult")
    assert len({registration.request_type for registration in server.method_registry}) == len(
        server.method_registry
    )
    assert len({registration.response_type for registration in server.method_registry}) == len(
        server.method_registry
    )

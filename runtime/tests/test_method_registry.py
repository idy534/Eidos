from __future__ import annotations

import pytest
from pydantic import RootModel

from eidos_runtime.protocol.registry import (
    MethodRegistration,
    MethodRegistry,
    MethodRegistryError,
    MethodValidationError,
)


class Params(RootModel[dict[str, object]]):
    pass


class Result(RootModel[dict[str, object]]):
    pass


def test_method_registry_validates_params_and_dispatches_typed_handler() -> None:
    calls: list[tuple[str, dict[str, object]]] = []
    registry = MethodRegistry()
    registry.register(MethodRegistration(
        name="session/read",
        request_type=Params,
        response_type=Result,
        handler=lambda request_id, params: calls.append((request_id, params.root)),
        requires_initialized=True,
    ))

    assert registry.dispatch("session/read", "request-1", {"sessionId": "s-1"}) is True
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
        handler=lambda _request_id, _params: None,
    )
    registry.register(registration)

    with pytest.raises(MethodRegistryError, match="duplicate"):
        registry.register(registration)
    assert registry.dispatch("unknown/method", "request-1", {}) is False

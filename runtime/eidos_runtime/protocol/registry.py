from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ValidationError
from pydantic import JsonValue


RequestModelT = TypeVar("RequestModelT", bound=BaseModel)
ResponseModelT = TypeVar("ResponseModelT", bound=BaseModel)


@dataclass(frozen=True)
class DeferredMethodResult:
    """A typed method result whose managed operation sends its response later.

    Plugin import is the only current use: acceptance and durable operation
    creation are synchronous, but filesystem import remains under the
    Runtime-managed task.  The later task still validates its declared
    response DTO before protocol serialization.
    """


Handler = Callable[[str, Any], BaseModel | DeferredMethodResult]


@dataclass(frozen=True)
class MethodErrorMapping:
    """Stable protocol presentation for an application/domain failure."""

    code: str
    invalid_params: bool = False


ErrorMapper = Callable[[Exception], MethodErrorMapping | None]


class MethodRegistryError(RuntimeError):
    pass


class MethodValidationError(ValueError):
    def __init__(self, method: str, error: ValidationError) -> None:
        self.method = method
        self.error = error
        super().__init__(f"invalid params for {method}")


class MethodResultValidationError(ValueError):
    """A registered handler failed to return its declared response DTO."""

    def __init__(self, method: str, error: ValidationError | None = None) -> None:
        self.method = method
        self.error = error
        super().__init__(f"invalid result for {method}")


class MethodApplicationError(RuntimeError):
    """A registered error mapper recognized an application/domain failure."""

    def __init__(self, method: str, mapping: MethodErrorMapping) -> None:
        self.method = method
        self.mapping = mapping
        super().__init__(f"application error for {method}: {mapping.code}")


@dataclass(frozen=True)
class MethodInvocation(Generic[ResponseModelT]):
    """One validated method response before its JSON-RPC envelope is sent."""

    response: ResponseModelT | None
    json_result: dict[str, JsonValue] | None
    _settle: Callable[[ResponseModelT, bool], None] | None = None

    def settle_response(self, *, delivered: bool) -> None:
        """Settle optional post-send work owned by an application adapter."""

        if self._settle is not None and self.response is not None:
            self._settle(self.response, delivered)

    @property
    def deferred(self) -> bool:
        """Whether the typed handler accepted work that answers later."""

        return self.response is None


@dataclass(frozen=True)
class MethodRegistration(Generic[RequestModelT, ResponseModelT]):
    name: str
    request_type: type[RequestModelT]
    response_type: type[ResponseModelT]
    handler: Handler
    requires_initialized: bool = True
    allowed_when_draining: bool = False
    allowed_during_reconfiguration: bool = True
    error_mapper: ErrorMapper | None = None


class MethodRegistry:
    def __init__(self) -> None:
        self._methods: dict[str, MethodRegistration[Any, Any]] = {}

    def register(self, registration: MethodRegistration[Any, Any]) -> None:
        if registration.name in self._methods:
            raise MethodRegistryError(
                f"duplicate method registration: {registration.name}"
            )
        if not registration.name or not registration.name.strip():
            raise MethodRegistryError("method name is empty")
        self._methods[registration.name] = registration

    def spec(self, name: str) -> MethodRegistration[Any, Any]:
        try:
            return self._methods[name]
        except KeyError:
            raise MethodRegistryError(f"method is not registered: {name}") from None

    def get(self, name: str) -> MethodRegistration[Any, Any] | None:
        return self._methods.get(name)

    def dispatch(
        self, name: str, request_id: str, params: object
    ) -> dict[str, JsonValue] | None | bool:
        """Validate and invoke one typed business method.

        ``False`` continues to mean that no method was registered.  A known
        method always either returns a JSON-safe result or raises a stable
        validation error, so callers cannot accidentally treat response DTOs
        as unused registration metadata.
        """
        invocation = self.invoke(name, request_id, params)
        if invocation is False:
            return False
        return invocation.json_result

    def invoke(
        self, name: str, request_id: str, params: object
    ) -> MethodInvocation[BaseModel] | bool:
        """Return both the wire result and its typed response instance.

        RuntimeServer uses this form when a use case must settle durable work
        only after the response has physically crossed the stdout boundary.
        ``dispatch`` remains a compact compatibility facade for direct callers.
        """
        registration = self._methods.get(name)
        if registration is None:
            return False
        try:
            request = registration.request_type.model_validate(params)
        except ValidationError as error:
            raise MethodValidationError(name, error) from error
        try:
            result = registration.handler(request_id, request)
        except Exception as error:
            mapping = (
                registration.error_mapper(error)
                if registration.error_mapper is not None
                else None
            )
            if mapping is not None:
                raise MethodApplicationError(name, mapping) from error
            raise
        if isinstance(result, DeferredMethodResult):
            return MethodInvocation(response=None, json_result=None)
        if not isinstance(result, registration.response_type):
            try:
                registration.response_type.model_validate(result)
            except ValidationError as error:
                raise MethodResultValidationError(name, error) from error
            raise MethodResultValidationError(name)
        settle = getattr(registration.handler, "settle_response", None)
        return MethodInvocation(
            response=result,
            json_result=_json_result(result),
            _settle=settle if callable(settle) else None,
        )

    def names(self) -> tuple[str, ...]:
        return tuple(self._methods)

    def __iter__(self) -> Iterator[MethodRegistration[Any, Any]]:
        return iter(self._methods.values())

    def __len__(self) -> int:
        return len(self._methods)


def _json_result(result: BaseModel) -> dict[str, JsonValue]:
    serializer = getattr(result, "to_json_value", None)
    if callable(serializer):
        value = serializer()
    else:
        value = result.model_dump(mode="json", by_alias=True, exclude_none=True)
    if not isinstance(value, dict):
        raise ValueError("method response must serialize to an object")
    return value


__all__ = [
    "DeferredMethodResult",
    "MethodApplicationError",
    "MethodErrorMapping",
    "MethodRegistration",
    "MethodRegistry",
    "MethodRegistryError",
    "MethodInvocation",
    "MethodResultValidationError",
    "MethodValidationError",
]

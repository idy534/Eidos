from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, RootModel, ValidationError
from pydantic import JsonValue


RequestModelT = TypeVar("RequestModelT", bound=BaseModel)
ResponseModelT = TypeVar("ResponseModelT", bound=BaseModel)
Handler = Callable[[str, Any], BaseModel]
ErrorMapper = Callable[[Exception], str | None]


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


class JsonObjectParams(RootModel[dict[str, JsonValue]]):
    """Compatibility request model for existing method-specific validators.

    The registry owns the envelope boundary and guarantees an object-shaped
    JSON value. Existing handlers retain their deliberately narrow business
    validation until each method is migrated to a dedicated DTO.
    """


class JsonObjectResult(RootModel[dict[str, JsonValue]]):
    pass


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
    ) -> dict[str, JsonValue] | bool:
        """Validate and invoke one typed business method.

        ``False`` continues to mean that no method was registered.  A known
        method always either returns a JSON-safe result or raises a stable
        validation error, so callers cannot accidentally treat response DTOs
        as unused registration metadata.
        """
        registration = self._methods.get(name)
        if registration is None:
            return False
        try:
            request = registration.request_type.model_validate(params)
        except ValidationError as error:
            raise MethodValidationError(name, error) from error
        result = registration.handler(request_id, request)
        if not isinstance(result, registration.response_type):
            try:
                registration.response_type.model_validate(result)
            except ValidationError as error:
                raise MethodResultValidationError(name, error) from error
            raise MethodResultValidationError(name)
        return _json_result(result)

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
    "JsonObjectParams",
    "JsonObjectResult",
    "MethodRegistration",
    "MethodRegistry",
    "MethodRegistryError",
    "MethodResultValidationError",
    "MethodValidationError",
]

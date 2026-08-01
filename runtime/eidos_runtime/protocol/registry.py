from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, RootModel, ValidationError
from pydantic import JsonValue


RequestModelT = TypeVar("RequestModelT", bound=BaseModel)
Handler = Callable[[str, Any], None]


class MethodRegistryError(RuntimeError):
    pass


class MethodValidationError(ValueError):
    def __init__(self, method: str, error: ValidationError) -> None:
        self.method = method
        self.error = error
        super().__init__(f"invalid params for {method}")


class JsonObjectParams(RootModel[dict[str, JsonValue]]):
    """Compatibility request model for existing method-specific validators.

    The registry owns the envelope boundary and guarantees an object-shaped
    JSON value. Existing handlers retain their deliberately narrow business
    validation until each method is migrated to a dedicated DTO.
    """


class JsonObjectResult(RootModel[dict[str, JsonValue]]):
    pass


@dataclass(frozen=True)
class MethodRegistration(Generic[RequestModelT]):
    name: str
    request_type: type[RequestModelT]
    response_type: type[BaseModel]
    handler: Handler
    requires_initialized: bool = True
    allowed_when_draining: bool = False
    allowed_during_reconfiguration: bool = True


class MethodRegistry:
    def __init__(self) -> None:
        self._methods: dict[str, MethodRegistration[Any]] = {}

    def register(self, registration: MethodRegistration[Any]) -> None:
        if registration.name in self._methods:
            raise MethodRegistryError(
                f"duplicate method registration: {registration.name}"
            )
        if not registration.name or not registration.name.strip():
            raise MethodRegistryError("method name is empty")
        self._methods[registration.name] = registration

    def spec(self, name: str) -> MethodRegistration[Any]:
        try:
            return self._methods[name]
        except KeyError:
            raise MethodRegistryError(f"method is not registered: {name}") from None

    def get(self, name: str) -> MethodRegistration[Any] | None:
        return self._methods.get(name)

    def dispatch(self, name: str, request_id: str, params: object) -> bool:
        registration = self._methods.get(name)
        if registration is None:
            return False
        try:
            request = registration.request_type.model_validate(params)
        except ValidationError as error:
            raise MethodValidationError(name, error) from error
        registration.handler(request_id, request)
        return True

    def names(self) -> tuple[str, ...]:
        return tuple(self._methods)

    def __iter__(self) -> Iterator[MethodRegistration[Any]]:
        return iter(self._methods.values())

    def __len__(self) -> int:
        return len(self._methods)


__all__ = [
    "JsonObjectParams",
    "JsonObjectResult",
    "MethodRegistration",
    "MethodRegistry",
    "MethodRegistryError",
    "MethodValidationError",
]

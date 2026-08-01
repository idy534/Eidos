"""Local multi-model configuration use cases."""

from __future__ import annotations

from collections.abc import Callable

from eidos_runtime.application.errors import (
    ApplicationError,
    ApplicationInvalidParamsError,
)
from eidos_runtime.model.config import (
    ModelConfig,
    ModelConfigError,
    ModelConfigStore,
    model_presets,
    public_model_config,
)


class ModelApplication:
    """Owns the five public model methods over ``~/.eidos/models.json``."""

    def __init__(self, store: ModelConfigStore) -> None:
        self._store = store

    def presets(self) -> dict[str, object]:
        return model_presets()

    def list_models(self) -> dict[str, object]:
        models = self._store.public_list()
        return {
            "models": [model.to_wire_dict(exclude_none=True) for model in models],
            "defaultModelId": models[0].id if models else None,
        }

    def create(
        self, *, provider: str, model_id: str, api_key: str
    ) -> dict[str, object]:
        return self._public(
            self._call(
                lambda: self._store.create(
                    provider_id=provider,
                    model_id=model_id,
                    api_key=api_key,
                )
            )
        )

    def update(
        self,
        existing_id: str,
        *,
        provider: str,
        model_id: str,
        api_key: str | None,
    ) -> dict[str, object]:
        return self._public(
            self._call(
                lambda: self._store.update(
                    existing_id,
                    provider_id=provider,
                    model_id=model_id,
                    api_key=api_key,
                )
            )
        )

    def delete(self, model_id: str) -> dict[str, object]:
        deleted = self._call(lambda: self._store.delete(model_id))
        return {"deletedModelId": deleted.id}

    @staticmethod
    def _public(config: ModelConfig) -> dict[str, object]:
        return public_model_config(config).to_wire_dict(exclude_none=True)

    @staticmethod
    def _call(operation: Callable[[], ModelConfig]) -> ModelConfig:
        try:
            return operation()
        except ModelConfigError as error:
            if "not found" in str(error):
                raise ApplicationError("RESOURCE_NOT_FOUND", str(error)) from error
            raise ApplicationInvalidParamsError("INVALID_PARAMS", str(error)) from error


__all__ = ["ModelApplication"]

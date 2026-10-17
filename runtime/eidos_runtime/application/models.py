"""Local multi-model configuration use cases."""

from __future__ import annotations

from collections.abc import Callable

from eidos_runtime.application.errors import (
    ApplicationError,
    ApplicationInvalidParamsError,
)
from eidos_runtime.model.config import (
    CustomModelSettings,
    ModelConfig,
    ModelConfigError,
    ModelConfigStore,
    model_presets,
    public_model_config,
    normalize_model_base_url,
)
from eidos_runtime.model.discovery import CustomProviderRequest


class ModelApplication:
    """Owns the five public model methods over ``~/.eidos/models.json``."""

    def __init__(self, store: ModelConfigStore) -> None:
        self._store = store

    def presets(self) -> dict[str, object]:
        return model_presets()

    def discovery_credentials(self, request: CustomProviderRequest) -> tuple[str, str]:
        if request.api_key is not None:
            return request.base_url, request.api_key
        if request.config_id is None:
            return request.base_url, ""
        config = self._store.get(request.config_id)
        if config is None or config.custom is None:
            raise ApplicationInvalidParamsError("INVALID_PARAMS", "只能测试自定义提供商。")
        if normalize_model_base_url(config.url) != request.base_url:
            raise ApplicationInvalidParamsError("INVALID_PARAMS", "修改接口地址后，请重新填写 API Key 或选择无需 API Key。")
        return request.base_url, config.api_key

    def list_models(self) -> dict[str, object]:
        models = self._store.public_list()
        return {
            "models": [model.to_wire_dict(exclude_none=True) for model in models],
            "defaultModelId": models[0].id if models else None,
        }

    def create(
        self, *, provider: str, model_id: str, api_key: str,
        custom: CustomModelSettings | None = None,
    ) -> dict[str, object]:
        return self._public(
            self._call(
                lambda: self._store.create(
                    provider_id=provider,
                    model_id=model_id,
                    api_key=api_key,
                    custom=custom,
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
        custom: CustomModelSettings | None = None,
    ) -> dict[str, object]:
        return self._public(
            self._call(
                lambda: self._store.update(
                    existing_id,
                    provider_id=provider,
                    model_id=model_id,
                    api_key=api_key,
                    custom=custom,
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

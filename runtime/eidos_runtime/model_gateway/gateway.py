from __future__ import annotations

import logging
import uuid

from eidos_runtime.model.config import (
    MODEL_CATALOG,
    ModelConfig,
    ModelConfigStore,
)
from eidos_runtime.model.pydantic_ai_client import (
    ModelClientLease,
    PydanticAIModelClient,
)
from eidos_runtime.model_gateway.pydantic_factory import build_pydantic_model
from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel
from eidos_runtime.runtime.resource_registry import ResourceRegistry


logger = logging.getLogger("eidos.runtime.model_gateway")


class ModelGatewayLease(ModelClientLease):
    def __init__(
        self,
        client: PydanticAIModelClient,
        config: ModelConfig,
        *,
        resource_registry: ResourceRegistry | None,
    ) -> None:
        self.lease_id = str(uuid.uuid4())
        self.model_id = config.id
        self.provider = MODEL_CATALOG.provider_id_for(config.id)
        super().__init__(
            client,
            client.close,
            resource_registry=resource_registry,
            owner_id=self.lease_id,
        )

    def close(self) -> None:
        if not self.closed:
            logger.info(
                "Model lease released lease_id=%s provider=%s model_id=%s",
                self.lease_id,
                self.provider,
                self.model_id,
            )
        super().close()


class ModelGateway:
    def __init__(
        self,
        configs: ModelConfigStore,
        *,
        async_kernel: RuntimeAsyncKernel,
        resource_registry: ResourceRegistry | None = None,
    ) -> None:
        self.configs = configs
        self.resources = resource_registry
        self.async_kernel = async_kernel

    def acquire_lease(self, config: ModelConfig) -> ModelGatewayLease:
        spec = MODEL_CATALOG.profile(config.id)
        built = build_pydantic_model(config)
        client = PydanticAIModelClient(
            built.model,
            spec,
            openai_client=built.provider_client,
            provider_client=built.provider_client,
            retry_transport=built.retry_client,
            profile_snapshot=spec.snapshot(config),
            parallel_tool_calls=config.supports_tool_call,
            reasoning_effort=None,
            async_kernel=self.async_kernel,
        )
        lease = ModelGatewayLease(
            client,
            config,
            resource_registry=self.resources,
        )
        logger.info(
            "Model lease acquired lease_id=%s provider=%s model_id=%s",
            lease.lease_id,
            lease.provider,
            lease.model_id,
        )
        return lease

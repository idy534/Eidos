from __future__ import annotations

import logging
import uuid
from pathlib import Path

from eidos_runtime.infrastructure.managed_resources import ManagedResourceManager, ResourceError
from eidos_runtime.infrastructure.qoder_model_driver import QoderModelDriver, QoderWorkerBinding
from eidos_runtime.model.qoder_client import QoderModelClient

from eidos_runtime.model.config import (
    MODEL_CATALOG,
    ModelConfig,
    ModelConfigStore,
    ModelConfigError,
    ModelReasoningSelection,
)
from eidos_runtime.model.client import ModelClient, ModelProfileSnapshot
from eidos_runtime.model.gateway_types import RetryPolicy
from eidos_runtime.model.pydantic_ai_client import (
    ModelClientLease,
    PydanticAIModelClient,
)
from eidos_runtime.model.native_custom import OpenAIResponsesModelClient
from eidos_runtime.model.pydantic_factory import build_pydantic_model
from eidos_runtime.runtime.async_kernel import RuntimeAsyncKernel
from eidos_runtime.runtime.resource_registry import ResourceRegistry


logger = logging.getLogger("eidos.runtime.model_gateway")


class ModelGatewayLease(ModelClientLease):
    def __init__(
        self,
        client: ModelClient,
        config: ModelConfig,
        *,
        resource_registry: ResourceRegistry | None,
    ) -> None:
        self.lease_id = str(uuid.uuid4())
        self.model_id = config.id
        self.provider = "qoder" if config.qoder else "custom" if config.custom else MODEL_CATALOG.provider_id_for(config.id, vendor=config.vendor)
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
        managed_resources: ManagedResourceManager | None = None,
    ) -> None:
        self.managed_resources = managed_resources
        self.configs = configs
        self.resources = resource_registry
        self.async_kernel = async_kernel

    def acquire_lease(
        self,
        config: ModelConfig,
        *,
        reasoning_selection: ModelReasoningSelection | None = None,
        max_output_tokens: int | None = None,
        retry_policy: RetryPolicy | None = None,
        frozen_profile: ModelProfileSnapshot | None = None,
    ) -> ModelGatewayLease:
        spec = MODEL_CATALOG.profile(config.id, config=config)
        if max_output_tokens is not None:
            if not 1 <= max_output_tokens <= spec.max_output_tokens:
                raise ValueError("model_output_limit_invalid")
            spec = spec.model_copy(update={"max_output_tokens": max_output_tokens})
        if config.qoder is not None:
            if self.managed_resources is None:
                raise ResourceError("resource_not_ready", "请先在运行环境设置中准备 Qoder 资源。")
            if self.configs.path is None:
                self.configs.initialize()
            assert self.configs.path is not None
            binding = QoderWorkerBinding(self.managed_resources, Path(self.configs.path.parent) / "qoder/default")
            profile = spec.snapshot(config, reasoning_selection=reasoning_selection).model_copy(update={"qoder_resource_version": binding.version})
            if frozen_profile is not None:
                fields = ("wire_api", "remote_model_id", "connection_fingerprint", "qoder_sdk_version",
                          "qoder_cli_version", "qoder_resource_version", "context_encoding", "account_ref")
                if any(getattr(frozen_profile, name) != getattr(profile, name) for name in fields):
                    raise ModelConfigError("Qoder model snapshot no longer matches this configuration or resource release")
                profile = frozen_profile
            return ModelGatewayLease(QoderModelClient(profile, QoderModelDriver(binding), self.async_kernel),
                                     config, resource_registry=self.resources)
        built = (build_pydantic_model(config, wire_api=spec.wire_api, retry_policy=retry_policy)
                 if retry_policy is not None else build_pydantic_model(config, wire_api=spec.wire_api))
        profile_snapshot = spec.snapshot(
            config, reasoning_selection=reasoning_selection
        )
        if profile_snapshot.wire_api == "openai_responses":
            client: ModelClient = OpenAIResponsesModelClient(
                spec,
                openai_client=built.provider_client,
                retry_transport=built.retry_client,
                profile_snapshot=profile_snapshot,
                parallel_tool_calls=config.supports_tool_call,
                reasoning_effort=reasoning_selection if config.custom else None,
                async_kernel=self.async_kernel,
            )
        else:
            client = PydanticAIModelClient(
                built.model,
                spec,
                openai_client=built.provider_client,
                provider_client=built.provider_client,
                retry_transport=built.retry_client,
                profile_snapshot=profile_snapshot,
                parallel_tool_calls=config.supports_tool_call,
                reasoning_selection=reasoning_selection,
                reasoning_format=config.custom.reasoning_format if config.custom else None,
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

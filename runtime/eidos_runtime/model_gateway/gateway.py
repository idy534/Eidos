from __future__ import annotations

from datetime import UTC, datetime
import logging
import uuid

from anthropic import AsyncAnthropic
import httpx
from openai import AsyncOpenAI
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openai import OpenAIProvider

from eidos_runtime.model.client import ModelProfileSnapshot
from eidos_runtime.model.config import ModelProfileSpec
from eidos_runtime.model.pydantic_ai_client import (
    ModelClientLease,
    PydanticAIModelClient,
)
from eidos_runtime.model_gateway.auth import ModelSecretStore
from eidos_runtime.model_gateway.models import RunModelSnapshot, WireAPI
from eidos_runtime.model_gateway.registry import AdapterRegistry
from eidos_runtime.runtime.resource_registry import ResourceRegistry


logger = logging.getLogger("eidos.runtime.model_gateway")


class ModelGatewayLease(ModelClientLease):
    def __init__(
        self,
        client: PydanticAIModelClient,
        snapshot: RunModelSnapshot,
        *,
        registry: AdapterRegistry,
        resource_registry: ResourceRegistry | None,
    ) -> None:
        self.lease_id = str(uuid.uuid4())
        self.profile_snapshot = snapshot.profile
        self.capability_snapshot = snapshot.capability
        self.provider_adapter = registry.provider(snapshot.profile.provider)
        self.wire_adapter = registry.wire(snapshot.profile.wire_api)
        self.auth_reference = snapshot.profile.auth_reference
        self.request_timeout = snapshot.profile.request_timeout
        self.retry_policy = snapshot.profile.retry_policy
        self.created_at = datetime.now(UTC)
        super().__init__(
            client,
            client.close,
            resource_registry=resource_registry,
            owner_id=self.lease_id,
        )

    def close(self) -> None:
        if not self.closed:
            logger.info(
                "Model lease released lease_id=%s profile_id=%s provider=%s wire_api=%s model_id=%s",
                self.lease_id,
                self.profile_snapshot.id,
                self.profile_snapshot.provider,
                self.profile_snapshot.wire_api.value,
                self.profile_snapshot.model_id,
            )
        super().close()


class ModelGateway:
    def __init__(
        self,
        secrets: ModelSecretStore,
        *,
        registry: AdapterRegistry | None = None,
        resource_registry: ResourceRegistry | None = None,
    ) -> None:
        self.secrets = secrets
        self.registry = registry or AdapterRegistry.default()
        self.resources = resource_registry

    def acquire_lease(self, snapshot: RunModelSnapshot) -> ModelGatewayLease:
        profile = snapshot.profile
        context_window = (
            snapshot.capability.context_window or profile.context_window
        )
        max_output_tokens = (
            snapshot.capability.max_output_tokens or profile.max_output_tokens
        )
        if context_window is None:
            raise ValueError("effective context window is required")
        if max_output_tokens is None:
            raise ValueError("effective max output tokens is required")
        if profile.base_url is None:
            raise ValueError("model base URL is required")
        self.registry.provider(profile.provider)
        self.registry.wire(profile.wire_api)
        secret = self.secrets.resolve(profile.auth_reference)
        timeout = httpx.Timeout(
            connect=min(10.0, profile.request_timeout),
            read=profile.request_timeout,
            write=min(30.0, profile.request_timeout),
            pool=min(10.0, profile.request_timeout),
        )
        provider_client: AsyncOpenAI | AsyncAnthropic
        if profile.wire_api is WireAPI.ANTHROPIC_MESSAGES:
            provider_client = AsyncAnthropic(
                api_key=secret,
                base_url=profile.base_url,
                max_retries=0,
                timeout=profile.request_timeout,
            )
            model = AnthropicModel(
                profile.model_id,
                provider=AnthropicProvider(anthropic_client=provider_client),
            )
        else:
            provider_client = AsyncOpenAI(
                api_key=secret,
                base_url=profile.base_url,
                max_retries=0,
                timeout=timeout,
            )
            provider = OpenAIProvider(openai_client=provider_client)
            model = (
                OpenAIResponsesModel(profile.model_id, provider=provider)
                if profile.wire_api is WireAPI.OPENAI_RESPONSES
                else OpenAIChatModel(profile.model_id, provider=provider)
            )
        legacy = ModelProfileSnapshot(
            provider_id=profile.provider,
            model_id=profile.model_id,
            wire_api={
                WireAPI.OPENAI_RESPONSES: "openai_responses",
                WireAPI.ANTHROPIC_MESSAGES: "anthropic_messages",
                WireAPI.OPENAI_CHAT_COMPLETIONS: "chat_completions",
            }[profile.wire_api],
            context_window_tokens=context_window,
            max_output_tokens=max_output_tokens,
            request_timeout_seconds=profile.request_timeout,
            supports_tools=snapshot.capability.supports_tools,
            supports_json_schema_output=(
                snapshot.capability.supports_structured_output
            ),
            supports_reasoning=profile.reasoning_mode.value != "none",
            retry_max_attempts=profile.retry_policy.max_attempts,
            retry_initial_backoff_seconds=(
                profile.retry_policy.initial_backoff_seconds
            ),
            retry_max_backoff_seconds=profile.retry_policy.max_backoff_seconds,
        )
        spec = ModelProfileSpec(
            provider_id=profile.provider,
            model_id=profile.model_id,
            wire_api="chat_completions",
            context_window_tokens=context_window,
            max_output_tokens=max_output_tokens,
            request_timeout_seconds=profile.request_timeout,
        )
        client = PydanticAIModelClient(
            model,
            spec,
            openai_client=(
                provider_client if isinstance(provider_client, AsyncOpenAI) else None
            ),
            provider_client=provider_client,
            profile_snapshot=legacy,
            settings_extra_body=(
                {"thinking": {"type": "disabled"}}
                if profile.provider == "deepseek"
                else None
            ),
            parallel_tool_calls=(
                snapshot.capability.supports_parallel_tools
                if profile.wire_api is not WireAPI.ANTHROPIC_MESSAGES
                else None
            ),
            reasoning_effort=(
                profile.reasoning_effort.value
                if profile.reasoning_effort is not None
                and profile.provider != "deepseek"
                else None
            ),
            resource_registry=self.resources,
        )
        lease = ModelGatewayLease(
            client,
            snapshot,
            registry=self.registry,
            resource_registry=self.resources,
        )
        logger.info(
            "Model lease acquired lease_id=%s profile_id=%s provider=%s wire_api=%s model_id=%s",
            lease.lease_id,
            profile.id,
            profile.provider,
            profile.wire_api.value,
            profile.model_id,
        )
        return lease


def legacy_profile_snapshot(snapshot: RunModelSnapshot) -> ModelProfileSnapshot:
    profile = snapshot.profile
    context_window = snapshot.capability.context_window or profile.context_window
    max_output_tokens = (
        snapshot.capability.max_output_tokens or profile.max_output_tokens
    )
    if context_window is None or max_output_tokens is None:
        raise ValueError("effective model limits are required")
    return ModelProfileSnapshot(
        provider_id=profile.provider,
        model_id=profile.model_id,
        wire_api={
            WireAPI.OPENAI_RESPONSES: "openai_responses",
            WireAPI.ANTHROPIC_MESSAGES: "anthropic_messages",
            WireAPI.OPENAI_CHAT_COMPLETIONS: "chat_completions",
        }[profile.wire_api],
        context_window_tokens=context_window,
        max_output_tokens=max_output_tokens,
        request_timeout_seconds=profile.request_timeout,
        supports_tools=snapshot.capability.supports_tools,
        supports_json_schema_output=(
            snapshot.capability.supports_structured_output
        ),
        supports_reasoning=profile.reasoning_mode.value != "none",
        retry_max_attempts=profile.retry_policy.max_attempts,
        retry_initial_backoff_seconds=(
            profile.retry_policy.initial_backoff_seconds
        ),
        retry_max_backoff_seconds=profile.retry_policy.max_backoff_seconds,
    )

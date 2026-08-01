"""Typed model configuration and profile use cases.

This module deliberately owns model-profile validation and declared-capability
resolution without knowing anything about JSON-RPC envelopes.  Runtime client
replacement remains behind ``ModelRuntimePort`` because the server owns the
active factory, leases, and reconfiguration lifecycle.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, Mapping, Protocol
import uuid

from pydantic import ValidationError

from eidos_runtime.application.errors import (
    ApplicationError,
    ApplicationInvalidParamsError,
)
from eidos_runtime.db.storage import ResourceNotFoundError
from eidos_runtime.model.config import (
    ModelConfigError,
    ModelConfigStore,
    model_catalog,
)
from eidos_runtime.model_gateway.auth import ModelSecretError, ModelSecretStore
from eidos_runtime.model_gateway.capabilities import resolve_model_capabilities
from eidos_runtime.model_gateway.models import (
    ModelProfile,
    ReasoningEffort,
    ReasoningMode,
    RetryPolicy,
    WireAPI,
)
from eidos_runtime.model_gateway.presets import PRESETS, ProviderPreset
from eidos_runtime.models import EidosFrozenStrictModel


_PROFILE_DRAFT_KEYS = frozenset({
    "name",
    "provider",
    "baseUrl",
    "authReference",
    "wireApi",
    "modelId",
    "contextWindow",
    "maxOutputTokens",
    "reasoningMode",
    "reasoningEffort",
    "supportsTools",
    "supportsParallelTools",
    "supportsImages",
    "supportsStructuredOutput",
    "supportsPromptCache",
    "requestTimeout",
    "retryPolicy",
})
_RETRY_POLICY_KEYS = frozenset({
    "maxAttempts",
    "initialBackoffSeconds",
    "maxBackoffSeconds",
})


class ModelProfileStore(Protocol):
    """The minimal durable profile repository used by this application."""

    def create_model_profile(self, profile: ModelProfile) -> ModelProfile: ...

    def update_model_profile(self, profile: ModelProfile) -> ModelProfile: ...

    def get_model_profile(self, profile_id: str) -> ModelProfile | None: ...

    def list_model_profiles(self) -> list[ModelProfile]: ...

    def delete_model_profile(self, profile_id: str) -> None: ...


class ModelRuntimePort(Protocol):
    """Runtime-owned legacy client lifecycle port.

    The port's ``configure_legacy_model`` operation is intentionally atomic:
    its implementation owns factory close/replace, config rollback, resource
    ownership and supervisor reconfiguration gates.  This application only
    coordinates the top-level use case and returns a typed business result.
    """

    def has_configured_legacy_model(self) -> bool: ...

    def configure_legacy_model(self, api_key: str) -> None: ...


class ModelStatus(EidosFrozenStrictModel):
    provider: str
    model: str
    configured: bool


class ModelOption(EidosFrozenStrictModel):
    id: str
    provider: str
    display_name: str
    configured: bool
    selectable: bool


class ModelList(EidosFrozenStrictModel):
    models: tuple[ModelOption, ...]
    default_model_id: str


class ModelProfileList(EidosFrozenStrictModel):
    schema_version: Literal[1] = 1
    profiles: tuple[ModelProfile, ...]


class DeletedModelProfile(EidosFrozenStrictModel):
    deleted_profile_id: str


class ModelPresetList(EidosFrozenStrictModel):
    schema_version: Literal[1] = 1
    presets: tuple[ProviderPreset, ...]


@dataclass(frozen=True)
class ModelProfileDraft:
    """Strictly decoded user declaration before it becomes a profile record."""

    name: str
    provider: str
    model_id: str
    base_url: str | None
    auth_reference: str | None
    wire_api: WireAPI | None
    context_window: int | None
    max_output_tokens: int | None
    reasoning_mode: ReasoningMode
    reasoning_effort: ReasoningEffort | None
    supports_tools: bool | None
    supports_parallel_tools: bool | None
    supports_images: bool | None
    supports_structured_output: bool | None
    supports_prompt_cache: bool | None
    request_timeout: float
    retry_policy: RetryPolicy

    @classmethod
    def from_wire(cls, value: Mapping[str, object]) -> "ModelProfileDraft":
        """Decode the established wire draft without a protocol dependency."""

        if set(value) - _PROFILE_DRAFT_KEYS:
            raise _invalid_params("unknown model profile field")
        try:
            provider = _required_string(value, "provider")
            return cls(
                name=_required_string(value, "name"),
                provider=provider,
                model_id=_required_string(value, "modelId"),
                base_url=_optional_string(value, "baseUrl"),
                auth_reference=_optional_string(value, "authReference"),
                wire_api=_optional_enum(value, "wireApi", WireAPI),
                context_window=_optional_int(value, "contextWindow"),
                max_output_tokens=_optional_int(value, "maxOutputTokens"),
                reasoning_mode=_enum_or_default(
                    value, "reasoningMode", ReasoningMode, ReasoningMode.NONE
                ),
                reasoning_effort=_optional_enum(
                    value, "reasoningEffort", ReasoningEffort
                ),
                supports_tools=_optional_bool(value, "supportsTools"),
                supports_parallel_tools=_optional_bool(
                    value, "supportsParallelTools"
                ),
                supports_images=_optional_bool(value, "supportsImages"),
                supports_structured_output=_optional_bool(
                    value, "supportsStructuredOutput"
                ),
                supports_prompt_cache=_optional_bool(
                    value, "supportsPromptCache"
                ),
                request_timeout=_optional_float(
                    value, "requestTimeout", default=120.0
                ),
                retry_policy=_retry_policy(value.get("retryPolicy", {})),
            )
        except (TypeError, ValueError, ValidationError) as error:
            raise _invalid_params("invalid model profile") from error

    def materialize(
        self,
        auth_reference: str,
        *,
        existing: ModelProfile | None = None,
    ) -> ModelProfile:
        """Build an immutable profile and validate its declared capability."""

        preset = PRESETS.get(self.provider)
        if preset is None:
            raise _invalid_params("provider preset is invalid")
        now = datetime.now(UTC)
        try:
            profile = ModelProfile.model_validate({
                "id": existing.id if existing is not None else str(uuid.uuid4()),
                "name": self.name,
                "provider": self.provider,
                "baseUrl": (
                    self.base_url
                    if self.base_url is not None
                    else preset.default_base_url
                ),
                "authReference": auth_reference,
                "wireApi": (
                    self.wire_api
                    if self.wire_api is not None
                    else preset.default_wire_api
                ),
                "modelId": self.model_id,
                "contextWindow": self.context_window,
                "maxOutputTokens": self.max_output_tokens,
                "reasoningMode": self.reasoning_mode,
                "reasoningEffort": self.reasoning_effort,
                "supportsTools": self.supports_tools,
                "supportsParallelTools": self.supports_parallel_tools,
                "supportsImages": self.supports_images,
                "supportsStructuredOutput": self.supports_structured_output,
                "supportsPromptCache": self.supports_prompt_cache,
                "requestTimeout": self.request_timeout,
                "retryPolicy": self.retry_policy,
                "createdAt": existing.created_at if existing is not None else now,
                "updatedAt": now,
            })
            resolve_model_capabilities(profile, preset)
        except (ValueError, ValidationError) as error:
            raise _invalid_params("invalid model profile") from error
        return profile


class ModelProfileApplication:
    """Coordinates model configuration, profile CRUD and capability resolution."""

    def __init__(
        self,
        *,
        store: ModelProfileStore,
        secret_store: ModelSecretStore,
        model_config: ModelConfigStore,
        runtime: ModelRuntimePort,
    ) -> None:
        self._store = store
        self._secret_store = secret_store
        self._model_config = model_config
        self._runtime = runtime

    def status(self) -> ModelStatus:
        try:
            status = self._model_config.public_status()
            configured = bool(status["configured"])
        except (KeyError, TypeError, ModelConfigError) as error:
            raise ApplicationError("INTERNAL_ERROR", "model status is unavailable") from error
        return ModelStatus(
            provider=str(status["provider"]),
            model=str(status["model"]),
            configured=configured or self._runtime.has_configured_legacy_model(),
        )

    def configure(self, api_key: str) -> ModelStatus:
        if not isinstance(api_key, str) or not api_key:
            raise _invalid_params("api key is required")
        try:
            self._runtime.configure_legacy_model(api_key)
        except ApplicationError:
            raise
        except ValueError as error:
            raise _invalid_params("invalid API key") from error
        return self.status()

    def list_models(self) -> ModelList:
        legacy = model_catalog(
            configured=self._runtime.has_configured_legacy_model()
        )
        profiles = tuple(self._model_option(profile) for profile in self._store.list_model_profiles())
        legacy_options = tuple(
            ModelOption.model_validate(option)
            for option in _model_options(legacy)
        )
        selectable = next(
            (option.id for option in profiles if option.selectable),
            str(legacy["defaultModelId"]),
        )
        return ModelList(
            models=profiles + legacy_options,
            default_model_id=selectable,
        )

    def list_profiles(self) -> ModelProfileList:
        return ModelProfileList(profiles=tuple(self._store.list_model_profiles()))

    def get_profile(self, profile_id: str) -> ModelProfile:
        profile = self._store.get_model_profile(profile_id)
        if profile is None:
            raise ApplicationError("RESOURCE_NOT_FOUND", "model profile was not found")
        return profile

    def create_profile(
        self, draft: ModelProfileDraft, *, api_key: str | None = None
    ) -> ModelProfile:
        reference, saved_secret = self._profile_secret_reference(draft, api_key)
        try:
            profile = draft.materialize(reference)
            self._secret_store.resolve(profile.auth_reference)
            self._store.create_model_profile(profile)
        except (ApplicationInvalidParamsError, ModelSecretError, ValueError) as error:
            if saved_secret:
                self._secret_store.delete(reference)
            if isinstance(error, ApplicationInvalidParamsError):
                raise
            raise _invalid_params("invalid model profile") from error
        return profile

    def update_profile(
        self,
        profile_id: str,
        draft: ModelProfileDraft,
        *,
        api_key: str | None = None,
    ) -> ModelProfile:
        existing = self.get_profile(profile_id)
        reference, saved_secret = self._profile_secret_reference(
            draft, api_key, fallback=existing.auth_reference
        )
        try:
            profile = draft.materialize(reference, existing=existing)
            self._secret_store.resolve(profile.auth_reference)
            self._store.update_model_profile(profile)
        except (ApplicationInvalidParamsError, ModelSecretError, ValueError) as error:
            if saved_secret:
                self._secret_store.delete(reference)
            if isinstance(error, ApplicationInvalidParamsError):
                raise
            raise _invalid_params("invalid model profile") from error
        return profile

    def delete_profile(self, profile_id: str) -> DeletedModelProfile:
        try:
            self._store.delete_model_profile(profile_id)
        except ResourceNotFoundError as error:
            raise ApplicationError("RESOURCE_NOT_FOUND", "model profile was not found") from error
        return DeletedModelProfile(deleted_profile_id=profile_id)

    def list_presets(self) -> ModelPresetList:
        return ModelPresetList(presets=tuple(PRESETS.values()))

    def _model_option(self, profile: ModelProfile) -> ModelOption:
        configured = self._profile_is_configured(profile)
        return ModelOption(
            id=profile.id,
            provider=profile.provider,
            display_name=profile.name,
            configured=configured,
            selectable=configured and self._profile_is_selectable(profile),
        )

    def _profile_is_configured(self, profile: ModelProfile) -> bool:
        try:
            self._secret_store.resolve(profile.auth_reference)
        except (ValueError, ModelSecretError):
            return False
        return True

    def _profile_is_selectable(self, profile: ModelProfile) -> bool:
        if not self._profile_is_configured(profile):
            return False
        preset = PRESETS.get(profile.provider)
        if preset is None or not isinstance(profile.wire_api, WireAPI):
            return False
        capability = resolve_model_capabilities(profile, preset)
        return (
            capability.context_window is not None
            and capability.max_output_tokens is not None
        )

    def _profile_secret_reference(
        self,
        draft: ModelProfileDraft,
        api_key: str | None,
        *,
        fallback: str | None = None,
    ) -> tuple[str, bool]:
        if api_key is not None:
            try:
                return self._secret_store.save(api_key), True
            except ValueError as error:
                raise _invalid_params("invalid API key") from error
        reference = draft.auth_reference or fallback
        if reference is None:
            raise _invalid_params("auth reference is required")
        return reference, False


def _required_string(value: Mapping[str, object], field: str) -> str:
    candidate = value.get(field)
    if not isinstance(candidate, str):
        raise ValueError(f"{field} must be a string")
    return candidate


def _optional_string(value: Mapping[str, object], field: str) -> str | None:
    candidate = value.get(field)
    if candidate is None:
        return None
    if not isinstance(candidate, str):
        raise ValueError(f"{field} must be a string")
    return candidate


def _optional_int(value: Mapping[str, object], field: str) -> int | None:
    candidate = value.get(field)
    if candidate is None:
        return None
    if not isinstance(candidate, int) or isinstance(candidate, bool):
        raise ValueError(f"{field} must be an integer")
    return candidate


def _optional_bool(value: Mapping[str, object], field: str) -> bool | None:
    candidate = value.get(field)
    if candidate is None:
        return None
    if not isinstance(candidate, bool):
        raise ValueError(f"{field} must be a boolean")
    return candidate


def _optional_float(
    value: Mapping[str, object], field: str, *, default: float
) -> float:
    candidate = value.get(field, default)
    if not isinstance(candidate, float):
        raise ValueError(f"{field} must be a float")
    return candidate


def _optional_enum(
    value: Mapping[str, object], field: str, enum: type[WireAPI]
) -> WireAPI | None:
    candidate = value.get(field)
    if candidate is None:
        return None
    if not isinstance(candidate, str):
        raise ValueError(f"{field} must be a string")
    return enum(candidate)


def _enum_or_default(
    value: Mapping[str, object],
    field: str,
    enum: type[ReasoningMode],
    default: ReasoningMode,
) -> ReasoningMode:
    candidate = value.get(field, default.value)
    if not isinstance(candidate, str):
        raise ValueError(f"{field} must be a string")
    return enum(candidate)


def _retry_policy(value: object) -> RetryPolicy:
    if not isinstance(value, Mapping) or set(value) - _RETRY_POLICY_KEYS:
        raise ValueError("retry policy is invalid")
    return RetryPolicy(
        max_attempts=value.get("maxAttempts", 3),
        initial_backoff_seconds=value.get("initialBackoffSeconds", 0.2),
        max_backoff_seconds=value.get("maxBackoffSeconds", 2.0),
    )


def _model_options(value: object) -> tuple[Mapping[str, object], ...]:
    if not isinstance(value, Mapping):
        raise ApplicationError("INTERNAL_ERROR", "model catalog is invalid")
    options = value.get("models")
    if not isinstance(options, list) or not all(isinstance(option, Mapping) for option in options):
        raise ApplicationError("INTERNAL_ERROR", "model catalog is invalid")
    return tuple(options)


def _invalid_params(message: str) -> ApplicationInvalidParamsError:
    return ApplicationInvalidParamsError("INVALID_PARAMS", message)


__all__ = [
    "DeletedModelProfile",
    "ModelList",
    "ModelOption",
    "ModelPresetList",
    "ModelProfileApplication",
    "ModelProfileDraft",
    "ModelProfileList",
    "ModelProfileStore",
    "ModelRuntimePort",
    "ModelStatus",
]

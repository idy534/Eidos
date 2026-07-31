"""Pure resolution of declared model capabilities.

This module deliberately has no network or credential dependencies.  Its
snapshots use legacy field names for persisted-data compatibility only:
``reachable`` and ``authenticated`` are always false because no network
verification has occurred.
"""

from __future__ import annotations

from eidos_runtime.model_gateway.models import (
    CapabilityProbeSource,
    CapabilitySnapshot,
    CapabilityWarning,
    ModelProfile,
)
from eidos_runtime.model_gateway.presets import ProviderPreset


_BOOLEAN_CAPABILITIES = (
    "supports_tools",
    "supports_parallel_tools",
    "supports_images",
    "supports_structured_output",
    "supports_prompt_cache",
)


def resolve_model_capabilities(
    profile: ModelProfile,
    preset: ProviderPreset,
) -> CapabilitySnapshot:
    """Resolve effective capabilities from declaration, preset, then defaults."""
    resolved: dict[str, bool] = {}
    sources: dict[str, CapabilityProbeSource] = {}
    for name in _BOOLEAN_CAPABILITIES:
        declared = getattr(profile, name)
        if declared is not None:
            resolved[name] = declared
            sources[name] = CapabilityProbeSource.USER_DECLARATION
            continue
        hinted = preset.capability_hints.get(name)
        if isinstance(hinted, bool):
            resolved[name] = hinted
            sources[name] = CapabilityProbeSource.BUILT_IN_PRESET
            continue
        resolved[name] = False
        sources[name] = CapabilityProbeSource.CONSERVATIVE_DEFAULT

    warnings: tuple[CapabilityWarning, ...] = ()
    if not resolved["supports_tools"] and resolved["supports_parallel_tools"]:
        resolved["supports_parallel_tools"] = False
        sources["supports_parallel_tools"] = CapabilityProbeSource.CONSERVATIVE_DEFAULT
        warnings = (
            CapabilityWarning(
                code="PARALLEL_TOOLS_REQUIRES_TOOLS",
                capability="supports_parallel_tools",
                message="parallel tools are disabled because tools are disabled",
                source=CapabilityProbeSource.CONSERVATIVE_DEFAULT,
            ),
        )

    context_window, sources["context_window"] = _resolve_limit(
        profile.context_window, preset.context_window
    )
    max_output_tokens, sources["max_output_tokens"] = _resolve_limit(
        profile.max_output_tokens, preset.max_output_tokens
    )
    source = _overall_source(sources)
    return CapabilitySnapshot(
        id=f"declared:{profile.id}",
        profile_id=profile.id,
        provider=profile.provider,
        wire_api=profile.wire_api,
        model_id=profile.model_id,
        reachable=False,
        authenticated=False,
        context_window=context_window,
        max_output_tokens=max_output_tokens,
        **resolved,
        reasoning_mode=profile.reasoning_mode,
        probe_source=source,
        probe_version="declared-capabilities-v1",
        probed_at=profile.updated_at,
        warnings=warnings,
        sources=sources,
    )


def _resolve_limit(
    declared: int | None,
    preset: int | None,
) -> tuple[int | None, CapabilityProbeSource]:
    if declared is not None:
        return declared, CapabilityProbeSource.USER_DECLARATION
    if preset is not None:
        return preset, CapabilityProbeSource.BUILT_IN_PRESET
    return None, CapabilityProbeSource.CONSERVATIVE_DEFAULT


def _overall_source(
    sources: dict[str, CapabilityProbeSource],
) -> CapabilityProbeSource:
    if CapabilityProbeSource.USER_DECLARATION in sources.values():
        return CapabilityProbeSource.USER_DECLARATION
    if CapabilityProbeSource.BUILT_IN_PRESET in sources.values():
        return CapabilityProbeSource.BUILT_IN_PRESET
    return CapabilityProbeSource.CONSERVATIVE_DEFAULT

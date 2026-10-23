"""Plugin package identity and presentation, independent of installation state."""
from __future__ import annotations

from pydantic import Field

from eidos_runtime.models import EidosFrozenModel


class PluginVersion(EidosFrozenModel):
    plugin_id: str
    version: str
    content_hash: str


class PluginPresentation(EidosFrozenModel):
    developer_name: str | None = Field(default=None, max_length=128)
    website_url: str | None = Field(default=None, max_length=2048)
    license: str | None = Field(default=None, max_length=128)
    icon: str | None = Field(default=None, max_length=131072)
    onboarding_skill_id: str | None = Field(default=None, max_length=256)

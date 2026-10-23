"""Validated installation data exchanged with the extension repository."""
from __future__ import annotations

from typing import Literal

from eidos_runtime.domain.plugins import PluginVersion
from eidos_runtime.models import EidosFrozenModel


class PluginInstallation(EidosFrozenModel):
    id: str
    name: str
    version: str
    description: str
    manifest_json: str
    content_hash: str


class PluginRevision(PluginVersion):
    status: Literal["installed", "removed"]

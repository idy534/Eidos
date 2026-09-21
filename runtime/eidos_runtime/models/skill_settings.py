"""Persisted skill settings and Desktop management projections."""
from typing import Literal

from eidos_runtime.models import EidosFrozenStrictModel


class SkillState(EidosFrozenStrictModel):
    qualified_id: str
    enabled: bool
    removed: bool = False
    source_kind: Literal["system", "user", "plugin"]
    directory_name: str | None = None
    directory_device: int | None = None
    directory_inode: int | None = None
    content_hash: str
    cleanup_pending: bool = False


class ManagedSkill(EidosFrozenStrictModel):
    schema_version: Literal[1] = 1
    qualified_id: str
    name: str
    description: str
    plugin_id: str
    plugin_version: str
    plugin_hash: str
    content_hash: str
    source_kind: Literal["system", "user", "plugin"]
    enabled: bool
    available: bool
    icon: str | None = None


class SkillDetail(EidosFrozenStrictModel):
    qualified_id: str
    content: str
    body: str
    directory: str


class SkillRemoval(EidosFrozenStrictModel):
    qualified_id: str
    removed: bool = True
    cleanup_pending: bool = False

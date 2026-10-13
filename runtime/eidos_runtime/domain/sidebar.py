from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_validator

from eidos_runtime.models.base import EidosFrozenStrictModel


MAX_SIDEBAR_PINS = 1000
SIDEBAR_ID_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"


def canonical_sidebar_id(value: str) -> str:
    if str(UUID(value)) != value:
        raise ValueError("sidebar ID must be a canonical UUID")
    return value


class ProjectSidebarTarget(EidosFrozenStrictModel):
    kind: Literal["project"]
    id: str = Field(min_length=1, max_length=256)


class SessionSidebarTarget(EidosFrozenStrictModel):
    kind: Literal["session"]
    id: str = Field(min_length=36, max_length=36, pattern=SIDEBAR_ID_PATTERN)

    _canonical_id = field_validator("id")(canonical_sidebar_id)


SidebarTarget = Annotated[
    ProjectSidebarTarget | SessionSidebarTarget, Field(discriminator="kind")
]


class SidebarPinsSnapshot(EidosFrozenStrictModel):
    items: tuple[SidebarTarget, ...] = Field(max_length=MAX_SIDEBAR_PINS)
    through_event_id: int = Field(ge=0, le=9_007_199_254_740_991)

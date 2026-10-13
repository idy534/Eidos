from __future__ import annotations

from pydantic import Field, field_validator
from pydantic.alias_generators import to_camel

from eidos_runtime.domain.sidebar import (
    MAX_SIDEBAR_PINS,
    SIDEBAR_ID_PATTERN,
    SidebarTarget,
    canonical_sidebar_id,
)
from eidos_runtime.protocol.methods import MethodRequestDto, MethodResultDto


class SidebarListPinsRequest(MethodRequestDto):
    pass


class SidebarSetPinnedRequest(MethodRequestDto):
    model_config = {"alias_generator": to_camel}
    target: SidebarTarget
    pinned: bool
    operation_id: str = Field(min_length=36, max_length=36, pattern=SIDEBAR_ID_PATTERN)

    _canonical_operation_id = field_validator("operation_id")(canonical_sidebar_id)


class SidebarPinsResponse(MethodResultDto):
    model_config = {"alias_generator": to_camel}
    items: list[SidebarTarget] = Field(max_length=MAX_SIDEBAR_PINS)
    through_event_id: int = Field(ge=0, le=9_007_199_254_740_991)

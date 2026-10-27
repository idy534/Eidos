from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field

from eidos_runtime.domain.channels import ChannelConfig, ChannelDelivery, ChannelKind
from eidos_runtime.models import EidosFrozenStrictModel, JsonSafeInt


class ChannelPublicConnection(EidosFrozenStrictModel):
    channel: ChannelKind = "dingtalk"
    id: str
    revision: JsonSafeInt
    config: ChannelConfig
    has_secret: bool
    client_secret: str = Field(default="", alias="clientSecret", repr=False)


class ChannelResponse(EidosFrozenStrictModel):
    connection: ChannelPublicConnection | None = None
    state: Literal["disabled", "connecting", "connected", "error"] = "disabled"
    reason: str | None = None
    deliveries: list[ChannelDelivery] = Field(default_factory=list, max_length=20)


class ChannelReadRequest(EidosFrozenStrictModel):
    channel: ChannelKind = "dingtalk"


class ChannelSaveRequest(EidosFrozenStrictModel):
    model_config = ConfigDict(hide_input_in_errors=True)
    channel: ChannelKind = "dingtalk"
    config: ChannelConfig
    expected_revision: JsonSafeInt = Field(ge=0)
    client_secret: str | None = Field(default=None, min_length=1, max_length=1024, repr=False)
    full_access_confirmation: Literal["full-access-v1"] | None = None

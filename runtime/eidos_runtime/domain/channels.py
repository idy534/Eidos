from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from eidos_runtime.domain.approval_policy import ApprovalMode, FULL_ACCESS_WARNING_VERSION
from eidos_runtime.models import EidosFrozenStrictModel, JsonSafeInt

ChannelKind = Literal["dingtalk", "feishu", "wecom"]
CHANNEL_NAMES: dict[ChannelKind, str] = {"dingtalk": "钉钉", "feishu": "飞书", "wecom": "企业微信"}


class ChannelError(ValueError):
    def __init__(self, message: str) -> None:
        super().__init__(message)


class ChannelConfig(EidosFrozenStrictModel):
    enabled: bool = False
    client_id: str = Field(min_length=1, max_length=256)
    model_id: str = Field(min_length=1, max_length=256)
    reasoning_selection: Literal["none", "thinking", "low", "medium", "high", "max"] | None = None
    project_id: str | None = Field(default=None, max_length=256)
    approval_mode: ApprovalMode = "manual"

    @field_validator("client_id", "model_id")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("配置不能包含首尾空白。")
        return value


class ChannelConnection(EidosFrozenStrictModel):
    channel: ChannelKind = "dingtalk"
    id: str
    revision: JsonSafeInt = Field(ge=1)
    config: ChannelConfig
    secret_ref: str
    full_access_confirmation: Literal["full-access-v1"] | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def validate_full_access(self) -> ChannelConnection:
        if (self.config.approval_mode == "full_access") != (self.full_access_confirmation == FULL_ACCESS_WARNING_VERSION):
            raise ValueError("完全访问渠道需要保存用户确认。")
        return self


class ChannelMessage(EidosFrozenStrictModel):
    msg_id: str = Field(min_length=1, max_length=512)
    corp_id: str = Field(min_length=1, max_length=256)
    robot_code: str = Field(min_length=1, max_length=256)
    conversation_id: str = Field(min_length=1, max_length=512)
    conversation_type: Literal["1", "2"]
    sender_id: str = Field(min_length=1, max_length=256)
    sender_name: str = Field(default="钉钉用户", max_length=128)
    mentioned: bool = False
    text: str = Field(default="", max_length=16_000)
    supported: bool = True


class ChannelInbound(EidosFrozenStrictModel):
    id: str
    connection: ChannelConnection
    message: ChannelMessage
    status: Literal["pending", "submitted", "rejected"]
    session_id: str | None = None
    run_id: str | None = None


class ChannelOrigin(EidosFrozenStrictModel):
    channel: ChannelKind = "dingtalk"
    message_id: str
    sender_id: str
    conversation_id: str


class ChannelDelivery(EidosFrozenStrictModel):
    id: str
    inbound_id: str
    kind: str
    text: str
    status: Literal["pending", "sending", "sent", "failed", "unknown"]
    reason: str | None = None
    created_at: JsonSafeInt

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from eidos_runtime.models import EidosFrozenStrictModel, JsonSafeInt


class ChannelError(ValueError):
    def __init__(self, message: str) -> None:
        super().__init__(message)


class ChannelConfig(EidosFrozenStrictModel):
    enabled: bool = False
    client_id: str = Field(min_length=1, max_length=256)
    robot_code: str = Field(min_length=1, max_length=256)
    corp_id: str = Field(min_length=1, max_length=256)
    model_id: str = Field(min_length=1, max_length=256)
    project_id: str | None = Field(default=None, max_length=256)
    approval_mode: Literal["manual", "auto_review"] = "manual"
    allowed_user_ids: list[str] = Field(min_length=1, max_length=100)
    allowed_conversation_ids: list[str] = Field(default_factory=list, max_length=100)
    allow_direct_messages: bool = False

    @field_validator("client_id", "robot_code", "corp_id", "model_id")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError("配置不能包含首尾空白。")
        return value

    @field_validator("allowed_user_ids", "allowed_conversation_ids")
    @classmethod
    def identifiers(cls, values: list[str]) -> list[str]:
        if any(not v.strip() or len(v) > 256 or v != v.strip() for v in values):
            raise ValueError("身份 ID 无效。")
        return list(dict.fromkeys(values))

    @model_validator(mode="after")
    def has_destination(self) -> ChannelConfig:
        if self.enabled and not self.allow_direct_messages and not self.allowed_conversation_ids:
            raise ValueError("请允许至少一个群或开启单聊。")
        return self


class ChannelConnection(EidosFrozenStrictModel):
    id: str
    revision: JsonSafeInt = Field(ge=1)
    config: ChannelConfig
    secret_ref: str


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
    channel: Literal["dingtalk"] = "dingtalk"
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

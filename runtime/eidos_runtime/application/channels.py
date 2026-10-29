from __future__ import annotations

import json
import logging
from collections.abc import Callable
from uuid import uuid4

from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.application.runs import RunApplication
from eidos_runtime.application.sessions import SessionApplication
from eidos_runtime.domain.approval_policy import FULL_ACCESS_WARNING_VERSION
from eidos_runtime.domain.channels import (
    CHANNEL_NAMES, ChannelConnection, ChannelError, ChannelKind, ChannelMessage,
)
from eidos_runtime.infrastructure.channel_secrets import ChannelSecrets
from eidos_runtime.protocol.channels import ChannelPublicConnection, ChannelResponse, ChannelSaveRequest
from eidos_runtime.model.config import ModelConfigStore
from eidos_runtime.persistence.channels import ChannelRepository, stable_id
from eidos_runtime.protocol.methods import RunStartRequestDto, SessionCreateRequestDto

_LOGGER = logging.getLogger("eidos.channels")


def accepts_message(connection: ChannelConnection, message: ChannelMessage) -> bool:
    return connection.config.enabled and (message.conversation_type == "1" or message.mentioned)


class ChannelApplication:
    def __init__(self, repository: ChannelRepository, secrets: ChannelSecrets,
                 sessions: SessionApplication, runs: RunApplication, models: ModelConfigStore) -> None:
        self.repository = repository
        self.secrets = secrets
        self.sessions = sessions
        self.runs = runs
        self.models = models

    def read(self, channel: ChannelKind = "dingtalk") -> ChannelResponse:
        connection = self.repository.connection(channel)
        has_secret = False
        secret_val = ""
        if connection:
            try:
                secret_val = self.secrets.read(connection.secret_ref)
                has_secret = bool(secret_val)
            except (OSError, ValueError):
                has_secret = False
                secret_val = ""
        return ChannelResponse(connection=ChannelPublicConnection(channel=channel, id=connection.id, revision=connection.revision,
            config=connection.config, has_secret=has_secret, client_secret=secret_val) if connection else None,
            deliveries=self.repository.deliveries(channel))

    def save(self, request: ChannelSaveRequest) -> ChannelResponse:
        old = self.repository.connection(request.channel)
        if (old.revision if old else 0) != request.expected_revision:
            raise ChannelError("渠道配置已经改变，请刷新后重试。")
        confirmation = None
        if request.config.approval_mode == "full_access":
            confirmation = request.full_access_confirmation or (old.full_access_confirmation if old else None)
            if confirmation != FULL_ACCESS_WARNING_VERSION:
                raise ChannelError("开启完全访问需要在渠道设置中由用户确认。")
        elif request.full_access_confirmation is not None:
            raise ChannelError("完全访问确认与审批模式不一致。")
        if request.config.enabled and self.models.get(request.config.model_id) is None:
            raise ChannelError("请先配置渠道使用的模型。")
        if request.config.project_id:
            self.repository.project_root(request.config.project_id)
        if request.client_secret:
            if len(request.client_secret.encode()) > 1024 or request.client_secret.strip() != request.client_secret:
                raise ChannelError("Client Secret 无效。")
            reference = self.secrets.write(request.client_secret)
        else:
            raise ChannelError("请填写 Client Secret。")
        value = ChannelConnection(channel=request.channel, id=old.id if old else str(uuid4()), revision=request.expected_revision + 1,
                                  config=request.config, secret_ref=reference, full_access_confirmation=confirmation)
        self.repository.save(value, request.expected_revision)
        return self.read(request.channel)

    def receive(self, connection: ChannelConnection, message: ChannelMessage) -> None:
        if accepts_message(connection, message):
            identifier = self.repository.receive(connection, message)
            _LOGGER.info("Channel inbound persisted channel=%s inbound_id=%s", connection.channel, identifier)
        else:
            _LOGGER.info("Channel callback ignored channel=%s reason=%s", connection.channel, "not_mentioned" if connection.config.enabled else "disabled")

    def process_pending(self, canceled: Callable[[], bool] = lambda: False) -> None:
        for identifier in self.repository.pending():
            if canceled():
                break
            try:
                self._process(identifier, canceled)
            except (ChannelError, ApplicationError, ValueError, OSError):
                # Raw third-party or model errors may contain credentials or content.
                self.repository.reject(identifier, "消息未能启动任务，请在本机 Eidos 检查渠道模型和执行位置。")

    def _process(self, identifier: str, canceled: Callable[[], bool]) -> None:
        incoming = self.repository.inbound(identifier)
        self.repository.require_current(incoming.connection)
        if not incoming.message.supported or not incoming.message.text.strip():
            self.repository.reject(identifier, "目前支持文字消息，请发送文字后重试。")
            return
        config = incoming.connection.config
        route = stable_id(json.dumps([incoming.connection.id, config.client_id, incoming.message.corp_id,
                                     incoming.message.conversation_id, incoming.message.sender_id, config.project_id]))
        session_id = self.repository.session(route)
        if session_id is None:
            root = self.repository.project_root(config.project_id) if config.project_id else None
            session = self.sessions.create(SessionCreateRequestDto(operationId=stable_id("session/" + route + "/" + identifier),
                workspaceRoot=root if config.project_id is None else None,
                projectId=config.project_id,
                workspaceNameHint=CHANNEL_NAMES[incoming.connection.channel] + "对话" if config.project_id is None else None))
            session_id = session.id
            self.repository.bind(route, session_id)
        if canceled():
            raise ChannelError("渠道任务准备已停止。")
        outcome = self.runs.start(RunStartRequestDto(sessionId=session_id, userInput=incoming.message.text,
                                 modelId=config.model_id, reasoningSelection=config.reasoning_selection, approvalMode=config.approval_mode,
                                 fullAccessConfirmation=incoming.connection.full_access_confirmation), channel_message_id=identifier)
        outcome.release_after_commit()

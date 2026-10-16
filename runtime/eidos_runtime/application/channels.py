from __future__ import annotations

import json
from collections.abc import Callable
from uuid import uuid4

from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.application.runs import RunApplication
from eidos_runtime.application.sessions import SessionApplication
from eidos_runtime.domain.channels import (
    ChannelConnection, ChannelError, ChannelMessage,
)
from eidos_runtime.infrastructure.channel_secrets import ChannelSecrets
from eidos_runtime.protocol.channels import ChannelPublicConnection, ChannelResponse, ChannelSaveRequest
from eidos_runtime.model.config import ModelConfigStore
from eidos_runtime.persistence.channels import ChannelRepository, stable_id
from eidos_runtime.protocol.methods import RunStartRequestDto, SessionCreateRequestDto


def allowed(connection: ChannelConnection, message: ChannelMessage) -> bool:
    config = connection.config
    return (config.enabled and config.corp_id == message.corp_id and config.robot_code == message.robot_code
            and message.sender_id in config.allowed_user_ids
            and ((message.conversation_type == "1" and config.allow_direct_messages)
                 or (message.conversation_type == "2" and message.mentioned
                     and message.conversation_id in config.allowed_conversation_ids)))


class ChannelApplication:
    def __init__(self, repository: ChannelRepository, secrets: ChannelSecrets,
                 sessions: SessionApplication, runs: RunApplication, models: ModelConfigStore) -> None:
        self.repository = repository
        self.secrets = secrets
        self.sessions = sessions
        self.runs = runs
        self.models = models

    def read(self) -> ChannelResponse:
        connection = self.repository.connection()
        return ChannelResponse(connection=ChannelPublicConnection(id=connection.id, revision=connection.revision,
            config=connection.config, has_secret=True) if connection else None,
            deliveries=self.repository.deliveries())

    def save(self, request: ChannelSaveRequest) -> ChannelResponse:
        old = self.repository.connection()
        if (old.revision if old else 0) != request.expected_revision:
            raise ChannelError("渠道配置已经改变，请刷新后重试。")
        if request.config.enabled and self.models.get(request.config.model_id) is None:
            raise ChannelError("请先配置渠道使用的模型。")
        if request.config.project_id:
            self.repository.project_root(request.config.project_id)
        if request.client_secret is not None:
            if len(request.client_secret.encode()) > 1024 or request.client_secret.strip() != request.client_secret:
                raise ChannelError("Client Secret 无效。")
            reference = self.secrets.write(request.client_secret)
        elif old and old.config.client_id == request.config.client_id:
            self.secrets.read(old.secret_ref)
            reference = old.secret_ref
        else:
            raise ChannelError("请填写 Client Secret。")
        value = ChannelConnection(id=old.id if old else str(uuid4()), revision=request.expected_revision + 1,
                                  config=request.config, secret_ref=reference)
        self.repository.save(value, request.expected_revision)
        return self.read()

    def receive(self, connection: ChannelConnection, message: ChannelMessage) -> None:
        if allowed(connection, message):
            self.repository.receive(connection, message)

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
            self.repository.reject(identifier, "目前支持文字消息，请输入文字后再次 @ Eidos。")
            return
        config = incoming.connection.config
        route = stable_id(json.dumps([incoming.connection.id, config.client_id, config.corp_id,
                                     incoming.message.conversation_id, incoming.message.sender_id, config.project_id]))
        session_id = self.repository.session(route)
        if session_id is None:
            root = self.repository.project_root(config.project_id) if config.project_id else None
            session = self.sessions.create(SessionCreateRequestDto(operationId=stable_id("session/" + route + "/" + identifier),
                workspaceRoot=root, workspaceNameHint="钉钉对话" if root is None else None))
            session_id = session.id
            self.repository.bind(route, session_id)
        if canceled():
            raise ChannelError("渠道任务准备已停止。")
        outcome = self.runs.start(RunStartRequestDto(sessionId=session_id, userInput=incoming.message.text,
                                 modelId=config.model_id, approvalMode=config.approval_mode), channel_message_id=identifier)
        outcome.release_after_commit()

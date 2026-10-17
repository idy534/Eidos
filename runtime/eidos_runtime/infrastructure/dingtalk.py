from __future__ import annotations

from collections.abc import Callable
import hashlib
import json
import logging
import time

import anyio
import dingtalk_stream
import httpx2 as httpx
from pydantic import JsonValue, TypeAdapter, ValidationError

from eidos_runtime.domain.channels import ChannelConnection, ChannelError, ChannelMessage
from eidos_runtime.infrastructure.channel_adapter import ChannelSendError

_JSON_RESPONSE = TypeAdapter(dict[str, JsonValue])
_LOGGER = logging.getLogger("eidos.dingtalk")


def _fingerprint(value: JsonValue) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:12] if isinstance(value, str) else "missing"


class _SdkLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        # Never forward upstream args, formatted messages or exception values:
        # those can contain callback bodies, connection tickets and credentials.
        events = {
            "connecting to endpoint %s": "connecting",
            "received disconnect topic=%s, message=%s": "disconnected",
            "error processing message": "callback_failed",
            "invalid message, content=%r": "invalid_frame",
            "unknown callback message topic, topic=%s, message=%s": "unknown_topic",
        }
        event = events.get(record.msg) if isinstance(record.msg, str) else None
        if event is not None or record.levelno >= logging.WARNING:
            exception = record.exc_info[0].__name__ if record.exc_info and record.exc_info[0] else "none"
            _LOGGER.log(max(record.levelno, logging.INFO), "DingTalk Stream event=%s exception=%s",
                        event or "connection_error", exception)


DingTalkSendError = ChannelSendError


class _StreamClient(dingtalk_stream.DingTalkStreamClient):
    # The official SDK probes an unrelated public IP only to report localIp.
    # This optional diagnostic is not needed by Eidos.
    HTTP_TIMEOUT_SECONDS = 3

    def get_host_ip(self) -> str:
        return ""

    async def background_task(self, json_message: dict[str, JsonValue], websocket: object | None = None) -> None:
        # Observe before SDK deduplication; callback-level logs alone cannot
        # distinguish platform non-delivery from an upstream cached ACK.
        if json_message.get("type") == "CALLBACK":
            headers = json_message.get("headers")
            if isinstance(headers, dict):
                _LOGGER.info("DingTalk Stream callback frame frame=%s topic=%s",
                             _fingerprint(headers.get("messageId")),
                             "robot" if headers.get("topic") == dingtalk_stream.ChatbotMessage.TOPIC else "other")
        await super().background_task(json_message, websocket)


class DingTalkAdapter:
    def __init__(self) -> None:
        self.client: _StreamClient | None = None
        self._token: str | None = None
        self._token_until = 0.0
        self._token_key: tuple[str, str] | None = None
        # Upstream logs can include complete callbacks and connection tickets.
        self._logger = logging.Logger("eidos.dingtalk.sdk", level=logging.INFO)
        self._logger.propagate = False
        self._logger.addHandler(_SdkLogHandler())

    async def listen(self, connection: ChannelConnection, secret: str,
                     receive: Callable[[ChannelConnection, ChannelMessage], None]) -> None:
        class Handler(dingtalk_stream.ChatbotHandler):
            async def process(self, callback: dingtalk_stream.CallbackMessage) -> tuple[int, str]:
                data = callback.data
                if not isinstance(data, dict):
                    _LOGGER.warning("DingTalk callback ignored reason=invalid_body")
                    return dingtalk_stream.AckMessage.STATUS_OK, "ignored"
                fingerprint = _fingerprint(data.get("msgId"))
                _LOGGER.info("DingTalk callback received message=%s revision=%s", fingerprint, connection.revision)
                try:
                    content = data.get("text", {})
                    message = ChannelMessage(
                        msg_id=data.get("msgId"), corp_id=data.get("senderCorpId"), robot_code=data.get("robotCode"),
                        conversation_id=data.get("conversationId"), conversation_type=data.get("conversationType"),
                        sender_id=data.get("senderStaffId"), sender_name=data.get("senderNick", "钉钉用户"),
                        mentioned=data.get("isInAtList", False),
                        text=content.get("content", "") if isinstance(content, dict) else "",
                        supported=data.get("msgtype") == "text")
                    _LOGGER.info("DingTalk callback parsed message=%s conversation_type=%s mentioned=%s supported=%s text_length=%s",
                                 fingerprint, message.conversation_type, message.mentioned, message.supported, len(message.text))
                    await anyio.to_thread.run_sync(receive, connection, message)
                except ValidationError as error:
                    fields = ",".join(str(entry["loc"][0]) for entry in error.errors(include_input=False, include_context=False, include_url=False))
                    _LOGGER.warning("DingTalk callback ignored message=%s reason=invalid_fields fields=%s", fingerprint, fields)
                    return dingtalk_stream.AckMessage.STATUS_OK, "ignored"
                except ChannelError:
                    _LOGGER.warning("DingTalk callback ignored message=%s reason=configuration_changed", fingerprint)
                    return dingtalk_stream.AckMessage.STATUS_OK, "ignored"
                return dingtalk_stream.AckMessage.STATUS_OK, "OK"

        client = _StreamClient(dingtalk_stream.Credential(connection.config.client_id, secret), logger=self._logger,
                               websocket_connect_options={"open_timeout": 10, "ping_interval": 20, "ping_timeout": 20,
                                                          "max_size": 128 * 1024, "max_queue": 16})
        handler = Handler()
        handler.logger = self._logger
        client.register_callback_handler(dingtalk_stream.ChatbotMessage.TOPIC, handler)
        self.client = client
        try:
            await client.start()
        finally:
            with anyio.CancelScope(shield=True):
                with anyio.move_on_after(4):
                    await client.stop()
            self.client = None

    @property
    def connected(self) -> bool:
        return self.client is not None and self.client.websocket is not None

    @staticmethod
    async def _post(client: httpx.AsyncClient, path: str, payload: dict[str, JsonValue], token: str | None = None) -> dict[str, JsonValue]:
        headers = {"x-acs-dingtalk-access-token": token} if token else {}
        try:
            async with client.stream("POST", "https://api.dingtalk.com" + path, json=payload, headers=headers) as response:
                if response.status_code == 429:
                    raise DingTalkSendError("钉钉发送限流，系统将稍后重试。", retry=True)
                if response.status_code == 401:
                    raise DingTalkSendError("钉钉访问凭据已失效，系统将刷新后重试。", retry=True)
                if response.status_code >= 500:
                    raise DingTalkSendError("钉钉服务响应异常，发送结果未知。", uncertain=True)
                if response.status_code >= 400:
                    raise DingTalkSendError("钉钉拒绝发送，请检查应用权限和会话。")
                raw = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=8192):
                    raw.extend(chunk)
                    if len(raw) > 64 * 1024:
                        raise DingTalkSendError("钉钉响应过大，发送结果未知。", uncertain=True)
            return _JSON_RESPONSE.validate_json(raw)
        except httpx.TransportError as error:
            raise DingTalkSendError("网络中断，发送结果未知。", uncertain=True) from error
        except (ValueError, UnicodeError) as error:
            raise DingTalkSendError("钉钉响应无效，发送结果未知。", uncertain=True) from error

    async def send(self, connection: ChannelConnection, secret: str, message: ChannelMessage, text: str) -> None:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False,
                                     limits=httpx.Limits(max_connections=2)) as client:
            key = (connection.config.client_id, connection.secret_ref)
            if key != self._token_key or time.monotonic() >= self._token_until:
                try:
                    result = await self._post(client, "/v1.0/oauth2/accessToken",
                        {"appKey": connection.config.client_id, "appSecret": secret})
                except DingTalkSendError as error:
                    # Token acquisition has no IM side effect, so timeout is retryable.
                    raise DingTalkSendError("无法取得钉钉访问凭据，请检查凭证、权限和网络。", retry=True) from error
                token, expiry = result.get("accessToken"), result.get("expireIn")
                if not isinstance(token, str) or not isinstance(expiry, (int, float)) or expiry <= 0:
                    raise DingTalkSendError("钉钉应用凭证无效，请重新配置。")
                self._token, self._token_key = token, key
                self._token_until = time.monotonic() + max(0, expiry - 60)
            payload: dict[str, JsonValue] = {"robotCode": message.robot_code, "msgKey": "sampleMarkdown",
                                          "msgParam": json.dumps({"title": "Eidos", "text": text}, ensure_ascii=False)}
            if message.conversation_type == "2":
                path = "/v1.0/robot/groupMessages/send"
                payload["openConversationId"] = message.conversation_id
            else:
                path = "/v1.0/robot/oToMessages/batchSend"
                payload["userIds"] = [message.sender_id]
            try:
                result = await self._post(client, path, payload, self._token)
            except DingTalkSendError as error:
                if error.retry:
                    self._token_until = 0
                raise
            if result.get("code") or result.get("errcode"):
                raise DingTalkSendError("钉钉未接受回复，请检查应用权限和会话。")
            if result.get("invalidStaffIdList") or result.get("flowControlledStaffIdList"):
                raise DingTalkSendError("钉钉未接受目标用户的回复，请检查用户 ID 和发送限制。")
            if not isinstance(result.get("processQueryKey"), str) or not result["processQueryKey"]:
                raise DingTalkSendError("钉钉未返回发送回执，发送结果未知。", uncertain=True)

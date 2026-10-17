from __future__ import annotations

from collections.abc import Callable
import json
import logging
import time
from typing import TYPE_CHECKING
from urllib.parse import quote

import httpx2 as httpx
from pydantic import JsonValue, TypeAdapter, ValidationError

from eidos_runtime.domain.channels import ChannelConnection, ChannelError, ChannelMessage
from eidos_runtime.infrastructure.channel_adapter import ChannelSendError

if TYPE_CHECKING:
    from eidos_runtime.infrastructure.feishu_stream import FeishuStream

_JSON = TypeAdapter(dict[str, JsonValue])
_LOGGER = logging.getLogger("eidos.feishu")


def normalize_feishu(payload: dict[str, JsonValue], bot_id: str, app_id: str) -> ChannelMessage | None:
    header, event = payload.get("header"), payload.get("event")
    if not isinstance(header, dict) or not isinstance(event, dict) or header.get("event_type") != "im.message.receive_v1":
        return None
    sender, message = event.get("sender"), event.get("message")
    if not isinstance(sender, dict) or not isinstance(message, dict) or sender.get("sender_type") != "user":
        return None
    identity = sender.get("sender_id")
    chat_type = message.get("chat_type")
    if not isinstance(identity, dict) or chat_type not in {"p2p", "group"}:
        return None
    content = message.get("content")
    text = ""
    if message.get("message_type") == "text" and isinstance(content, str):
        body = _JSON.validate_json(content)
        if isinstance(body.get("text"), str):
            text = body["text"]
    mentioned = False
    mentions = message.get("mentions")
    if isinstance(mentions, list):
        for mention in mentions:
            if not isinstance(mention, dict):
                continue
            target = mention.get("id")
            if isinstance(target, dict) and target.get("open_id") == bot_id:
                mentioned = True
                key = mention.get("key")
                if isinstance(key, str):
                    text = text.replace(key, "")
    return ChannelMessage(msg_id=message.get("message_id"), corp_id=header.get("tenant_key"), robot_code=app_id,
        conversation_id=message.get("chat_id"), conversation_type="1" if chat_type == "p2p" else "2",
        sender_id=identity.get("open_id"), sender_name="飞书用户", mentioned=mentioned,
        text=text.strip(), supported=message.get("message_type") == "text")


class FeishuAdapter:
    def __init__(self) -> None:
        self.client: FeishuStream | None = None
        self._token: str | None = None
        self._token_until = 0.0
        self._token_key: tuple[str, str] | None = None

    @property
    def connected(self) -> bool:
        return self.client is not None and self.client.connected

    @staticmethod
    async def request(method: str, path: str, payload: dict[str, JsonValue] | None = None,
                      token: str | None = None) -> dict[str, JsonValue]:
        headers = {"Authorization": "Bearer " + token} if token else {}
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False) as client:
                async with client.stream(method, "https://open.feishu.cn" + path, json=payload, headers=headers) as response:
                    if response.status_code == 429:
                        raise ChannelSendError("飞书发送限流，系统将稍后重试。", retry=True)
                    if response.status_code >= 500:
                        raise ChannelSendError("飞书服务响应异常，发送结果未知。", uncertain=True)
                    if response.status_code >= 400:
                        raise ChannelSendError("飞书拒绝请求，请检查应用权限和会话。")
                    raw = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=8192):
                        raw.extend(chunk)
                        if len(raw) > 128 * 1024:
                            raise ChannelSendError("飞书响应过大，发送结果未知。", uncertain=True)
            result = _JSON.validate_json(raw)
            code = result.get("code")
            if type(code) is not int:
                raise ValueError("invalid response status")
            if code != 0:
                if code in {99991661, 99991663, 99991664, 99991668}:
                    raise ChannelSendError("飞书访问凭据已失效，系统将刷新后重试。", retry=True)
                if code == 99991400:
                    raise ChannelSendError("飞书发送限流，系统将稍后重试。", retry=True)
                raise ChannelSendError("飞书拒绝请求，请检查应用权限和会话。")
            return result
        except (httpx.TransportError, ValueError, UnicodeError) as error:
            raise ChannelSendError("飞书请求中断或响应无效，发送结果未知。", uncertain=True) from error

    async def token(self, connection: ChannelConnection, secret: str) -> str:
        key = (connection.config.client_id, connection.secret_ref)
        if key != self._token_key or time.monotonic() >= self._token_until:
            try:
                result = await self.request("POST", "/open-apis/auth/v3/tenant_access_token/internal",
                    {"app_id": connection.config.client_id, "app_secret": secret})
                token, expiry = result.get("tenant_access_token"), result.get("expire")
                if not isinstance(token, str) or not token or type(expiry) is not int or expiry <= 0:
                    raise ValueError("invalid token response")
            except (ChannelSendError, ValueError) as error:
                raise ChannelSendError("无法取得飞书访问凭据，请检查凭证和网络。", retry=True) from error
            self._token, self._token_key = token, key
            self._token_until = time.monotonic() + max(0, expiry - 60)
        assert self._token is not None
        return self._token

    async def listen(self, connection: ChannelConnection, secret: str,
                     receive: Callable[[ChannelConnection, ChannelMessage], None]) -> None:
        # Import inside the existing kernel. The upstream ws module captures
        # asyncio.get_event_loop() at import; Eidos never starts that loop.
        from eidos_runtime.infrastructure.feishu_stream import FeishuStream
        token = await self.token(connection, secret)
        identity = await self.request("GET", "/open-apis/bot/v3/info", token=token)
        bot = identity.get("bot")
        bot_id = bot.get("open_id") if isinstance(bot, dict) else None
        if not isinstance(bot_id, str) or not bot_id:
            raise ChannelError("无法读取飞书机器人身份，请检查应用是否已启用机器人。")

        def handle(payload: dict[str, JsonValue]) -> None:
            try:
                message = normalize_feishu(payload, bot_id, connection.config.client_id)
                if message is not None:
                    # SQLite admission is a short transaction. Returning from
                    # this callback lets the SDK ACK only after persistence.
                    receive(connection, message)
                    _LOGGER.info("Feishu callback persisted revision=%s", connection.revision)
            except (ValidationError, ChannelError):
                _LOGGER.warning("Feishu callback ignored reason=invalid_or_stale")

        self.client = FeishuStream(connection.config.client_id, secret, handle, self.request)
        try:
            await self.client.listen()
        finally:
            self.client = None

    async def send(self, connection: ChannelConnection, secret: str, message: ChannelMessage, text: str) -> None:
        token = await self.token(connection, secret)
        try:
            result = await self.request("POST", "/open-apis/im/v1/messages/" + quote(message.msg_id, safe="") + "/reply",
                {"msg_type": "text", "content": json.dumps({"text": text}, ensure_ascii=False)}, token)
        except ChannelSendError as error:
            if error.retry:
                self._token_until = 0
            raise
        data = result.get("data")
        if not isinstance(data, dict) or not isinstance(data.get("message_id"), str) or not data["message_id"]:
            raise ChannelSendError("飞书未返回发送凭据，发送结果未知。", uncertain=True)

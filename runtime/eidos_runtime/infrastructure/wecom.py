from __future__ import annotations

import asyncio
from collections.abc import Callable
import logging

from aibot import generate_req_id
from aibot.types import WsCmd
from aibot.ws import WsConnectionManager
import anyio
from pydantic import JsonValue, TypeAdapter, ValidationError
import websockets

from eidos_runtime.domain.channels import ChannelConnection, ChannelError, ChannelMessage
from eidos_runtime.infrastructure.channel_adapter import ChannelSendError

_JSON = TypeAdapter(dict[str, JsonValue])
_LOGGER = logging.getLogger("eidos.wecom")


class _SafeLogger:
    def debug(self, *args: object) -> None: pass
    def info(self, *args: object) -> None: pass
    def warn(self, *args: object) -> None:
        _LOGGER.warning("WeCom SDK event=connection_warning")
    def error(self, *args: object) -> None:
        _LOGGER.warning("WeCom SDK event=connection_or_callback_error")


def normalize_wecom(frame: dict[str, JsonValue], bot_id: str) -> ChannelMessage | None:
    if frame.get("cmd") != WsCmd.CALLBACK:
        return None
    body = frame.get("body")
    if not isinstance(body, dict) or body.get("aibotid") != bot_id or body.get("chattype") not in {"single", "group"}:
        return None
    sender = body.get("from")
    if not isinstance(sender, dict):
        return None
    direct = body["chattype"] == "single"
    content = body.get("text")
    return ChannelMessage(msg_id=body.get("msgid"), corp_id=bot_id, robot_code=bot_id,
        conversation_id=sender.get("userid") if direct else body.get("chatid"),
        conversation_type="1" if direct else "2", sender_id=sender.get("userid"), sender_name="企业微信用户",
        # WeCom delivers group bot callbacks after platform mention routing.
        # The protocol has no separate mention flag; never require DingTalk's
        # isInAtList or parse a visible @name as proof of a mention.
        mentioned=not direct, text=content.get("content", "") if isinstance(content, dict) else "",
        supported=body.get("msgtype") == "text")


class _ManagedConnection(WsConnectionManager):
    """Keep the official auth/heartbeat/ACK codec inside an owned task group."""
    def __init__(self) -> None:
        super().__init__(_SafeLogger())
        self.group: anyio.abc.TaskGroup | None = None
        self.authenticated = False
        self.reply_tasks: set[asyncio.Task[None]] = set()
        self.rejected: set[str] = set()

    def _start_heartbeat(self) -> None:
        if not self.authenticated and self.group is not None:
            self.authenticated = True
            self.group.start_soon(self._heartbeat_loop)

    async def _process_reply_queue(self, req_id: str) -> None:
        task = asyncio.current_task()
        assert task is not None
        self.reply_tasks.add(task)
        try:
            await super()._process_reply_queue(req_id)
        finally:
            self.reply_tasks.discard(task)

    def _handle_reply_ack(self, req_id: str, frame: dict[str, JsonValue]) -> None:
        code = frame.get("errcode")
        if req_id in self._pending_acks and type(code) is int and code != 0:
            self.rejected.add(req_id)
        super()._handle_reply_ack(req_id, frame)

    async def _authentication_deadline(self) -> None:
        await anyio.sleep(10)
        if not self.authenticated:
            raise ChannelError("企业微信鉴权失败或超时，请检查 Bot ID 和 Secret。")

    async def listen(self) -> None:
        # Do not call SDK connect()/disconnect(): those schedule receive and
        # close tasks without an awaitable shutdown. Only the lifecycle bridge
        # touches these pinned SDK internals; wire behavior stays upstream.
        async with websockets.connect("wss://openws.work.weixin.qq.com", open_timeout=10, close_timeout=3,
                                      ping_interval=None, max_size=128 * 1024, max_queue=16, proxy=None) as socket:
            self._ws = socket
            try:
                async with anyio.create_task_group() as group:
                    self.group = group
                    group.start_soon(self._authentication_deadline)
                    await self._send_auth()
                    try:
                        async for raw in socket:
                            frame = _JSON.validate_json(raw)
                            if self.authenticated or frame.get("cmd") is None:
                                self._handle_frame(frame)
                    finally:
                        group.cancel_scope.cancel()
            finally:
                self.authenticated = False
                self.group = None
                self._ws = None
                with anyio.CancelScope(shield=True):
                    # Let queued SDK sends enter their tracked wrapper before
                    # canceling and awaiting every owned reply task.
                    await anyio.sleep(0)
                    tasks = tuple(self.reply_tasks)
                    for task in tasks:
                        task.cancel()
                    if tasks:
                        await asyncio.gather(*tasks, return_exceptions=True)
                    self._clear_pending_messages("Connection closed")
                    with anyio.move_on_after(4):
                        await socket.close()
        raise ChannelError("企业微信连接已关闭，系统将重新连接。")

    async def send_message(self, target: str, text: str) -> None:
        req_id = generate_req_id(WsCmd.SEND_MSG)
        try:
            with anyio.fail_after(10):
                receipt = await self.send_reply(req_id, {"chatid": target, "msgtype": "markdown", "markdown": {"content": text}}, WsCmd.SEND_MSG)
                if type(receipt.get("errcode")) is not int or receipt["errcode"] != 0:
                    raise ChannelSendError("企业微信回执无效，发送结果未知。", uncertain=True)
        except Exception as error:
            if req_id in self.rejected:
                raise ChannelSendError("企业微信拒绝发送，请检查机器人可用范围和发送权限。") from error
            raise ChannelSendError("企业微信发送中断或回执超时，发送结果未知。", uncertain=True) from error
        finally:
            self.rejected.discard(req_id)


class WeComAdapter:
    def __init__(self) -> None:
        self.client: _ManagedConnection | None = None
        self._connection: ChannelConnection | None = None

    @property
    def connected(self) -> bool:
        return self.client is not None and self.client.authenticated

    async def listen(self, connection: ChannelConnection, secret: str,
                     receive: Callable[[ChannelConnection, ChannelMessage], None]) -> None:
        client = _ManagedConnection()
        client.set_credentials(connection.config.client_id, secret)

        def handle(frame: dict[str, JsonValue]) -> None:
            try:
                message = normalize_wecom(frame, connection.config.client_id)
                if message is not None:
                    receive(connection, message)
                    _LOGGER.info("WeCom callback persisted revision=%s", connection.revision)
            except (ValidationError, ChannelError):
                _LOGGER.warning("WeCom callback ignored reason=invalid_or_stale")

        client.on_message = handle
        self.client, self._connection = client, connection
        try:
            await client.listen()
        finally:
            self.client, self._connection = None, None

    async def send(self, connection: ChannelConnection, secret: str, message: ChannelMessage, text: str) -> None:
        if not self.connected or self._connection != connection:
            raise ChannelSendError("企业微信尚未连接，系统将稍后重试。", retry=True)
        target = message.sender_id if message.conversation_type == "1" else message.conversation_id
        await self.client.send_message(target, text)

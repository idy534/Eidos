"""Lifecycle bridge for the pinned official SDK's WebSocket codec.

The SDK owns binary frames, fragment assembly, heartbeat payloads and ACKs.
Eidos owns the socket and task group instead of calling the SDK's blocking
start(), which starts additional event loops and unowned background tasks.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
import logging
from urllib.parse import parse_qs, urlparse

import anyio
from lark_channel import JSON, LogLevel, SecurityConfig
from lark_channel.core.log import logger as sdk_logger
from lark_channel.ws.client import Client
from lark_channel.ws.model import ClientConfig
from pydantic import JsonValue, TypeAdapter
import websockets

from eidos_runtime.domain.channels import ChannelError

_JSON = TypeAdapter(dict[str, JsonValue])
_LOGGER = logging.getLogger("eidos.feishu")
Request = Callable[..., Awaitable[dict[str, JsonValue]]]


class _SafeLog(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        # The SDK logs ticket URLs and raw callback exceptions. Never forward
        # the formatted record, args or exception text into Runtime stdout.
        _LOGGER.warning("Feishu SDK event=transport_or_callback_error")


class _Handler:
    def __init__(self, receive: Callable[[dict[str, JsonValue]], None]) -> None:
        self.receive = receive

    def _do_without_validation(self, payload: bytes) -> None:
        self.receive(_JSON.validate_json(payload))


class FeishuStream(Client):
    def __init__(self, app_id: str, secret: str, receive: Callable[[dict[str, JsonValue]], None], request: Request) -> None:
        sdk_logger.handlers = [_SafeLog()]
        sdk_logger.propagate = False
        super().__init__(app_id, secret, log_level=LogLevel.WARNING, event_handler=_Handler(receive), auto_reconnect=False,
            security=SecurityConfig(mode="strict", max_ws_fragment_parts=32, max_ws_fragment_bytes=128 * 1024))
        self.request = request

    @property
    def connected(self) -> bool:
        return self._conn is not None

    async def listen(self) -> None:
        endpoint = await self.request("POST", "/callback/ws/endpoint", {"AppID": self._app_id, "AppSecret": self._app_secret})
        data = endpoint.get("data")
        url = data.get("URL") if isinstance(data, dict) else None
        if not isinstance(url, str):
            raise ChannelError("飞书没有返回长连接地址，请检查长连接配置。")
        parsed = urlparse(url)
        self._validate_conn_url(parsed, url)
        # The discovery result is a signed connection capability. Permit only
        # TLS on Feishu-owned hosts; do not persist or log its query string.
        if parsed.scheme != "wss" or not parsed.hostname or not parsed.hostname.endswith((".feishu.cn", ".larksuite.com")) or parsed.username or parsed.password:
            raise ChannelError("飞书返回的长连接地址无效。")
        query = parse_qs(parsed.query)
        self._service_id = query.get("service_id", [""])[0]
        if not self._service_id.isdigit():
            raise ChannelError("飞书返回的长连接身份无效。")
        config = data.get("ClientConfig")
        if isinstance(config, dict):
            self._configure(JSON.unmarshal(JSON.marshal(config), ClientConfig))
        async with websockets.connect(url, open_timeout=10, close_timeout=3, ping_interval=20, ping_timeout=20,
                                      max_size=128 * 1024, max_queue=16, proxy=None) as socket:
            self._conn = socket
            try:
                async with anyio.create_task_group() as group:
                    group.start_soon(self._ping_loop)
                    try:
                        async for frame in socket:
                            if not isinstance(frame, bytes):
                                raise ChannelError("飞书消息帧格式无效。")
                            await self._handle_message(frame)
                    finally:
                        group.cancel_scope.cancel()
            finally:
                self._conn = None
                with anyio.CancelScope(shield=True):
                    with anyio.move_on_after(4):
                        await socket.close()
        raise ChannelError("飞书连接已关闭，系统将重新连接。")

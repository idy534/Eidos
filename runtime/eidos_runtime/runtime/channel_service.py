from __future__ import annotations

from collections.abc import Callable
from functools import partial
import time
import threading

import anyio

from eidos_runtime.application.channels import ChannelApplication, allowed
from eidos_runtime.domain.channels import ChannelError
from eidos_runtime.protocol.channels import ChannelResponse
from eidos_runtime.infrastructure.dingtalk import DingTalkAdapter, DingTalkSendError


class ChannelService:
    def __init__(self, application: ChannelApplication, active: Callable[[], bool], publish: Callable[[], object],
                 launch: Callable[[Callable[[threading.Event], None]], bool]) -> None:
        self.application = application
        self.active = active
        self.publish = publish
        self.launch = launch
        self._preparing = threading.Event()
        self.adapter = DingTalkAdapter()
        self.state = "disabled"
        self.reason: str | None = None

    def read(self) -> ChannelResponse:
        response = self.application.read()
        if response.connection is None or not response.connection.config.enabled:
            return response.model_copy(update={"state": "disabled", "reason": None})
        return response.model_copy(update={"state": "connecting" if self.state == "disabled" else self.state, "reason": self.reason})

    async def run(self) -> None:
        recovered = False
        try:
            while True:
                try:
                    if not recovered:
                        await anyio.to_thread.run_sync(self.application.repository.recover)
                        recovered = True
                    connection = await anyio.to_thread.run_sync(self.application.repository.connection)
                    if not self.active() or connection is None or not connection.config.enabled:
                        self.state, self.reason = "disabled", None
                        await anyio.sleep(1)
                        continue
                    secret = await anyio.to_thread.run_sync(self.application.secrets.read, connection.secret_ref)
                    self.state, self.reason = "connecting", None
                    started = time.monotonic()
                    async with anyio.create_task_group() as group:
                        group.start_soon(self.adapter.listen, connection, secret, self.application.receive)
                        group.start_soon(self._work)
                        while self.active() and await anyio.to_thread.run_sync(self.application.repository.connection) == connection:
                            if self.adapter.connected:
                                self.state, self.reason = "connected", None
                            elif time.monotonic() - started > 15:
                                self.state, self.reason = "error", "钉钉连接尚未建立，系统正在重试，请检查凭证和网络。"
                            else:
                                self.state = "connecting"
                            await anyio.sleep(1)
                        group.cancel_scope.cancel()
                except Exception:
                    self.state, self.reason = "error", "渠道连接失败，请检查凭证、模型和网络。"
                    await anyio.sleep(3)
        finally:
            self.state = "disabled"

    async def _work(self) -> None:
        while True:
            if not self.active():
                return
            await anyio.to_thread.run_sync(self._prepare)
            await anyio.to_thread.run_sync(self.application.repository.collect_replies)
            delivery = await anyio.to_thread.run_sync(self.application.repository.claim_delivery)
            if delivery is not None:
                try:
                    incoming = await anyio.to_thread.run_sync(self.application.repository.inbound, delivery.inbound_id)
                    current = await anyio.to_thread.run_sync(self.application.repository.connection)
                    if current is None or current.config.client_id != incoming.connection.config.client_id or not allowed(current, incoming.message):
                        raise ChannelError("回复目标已经停用或不再被允许。")
                    secret = await anyio.to_thread.run_sync(self.application.secrets.read, current.secret_ref)
                    await self.adapter.send(current, secret, incoming.message, delivery.text)
                    await anyio.to_thread.run_sync(self.application.repository.settle, delivery.id, "sent")
                except DingTalkSendError as error:
                    await anyio.to_thread.run_sync(partial(self.application.repository.settle, delivery.id,
                        "unknown" if error.uncertain else "failed", str(error), retry=error.retry))
                except ChannelError as error:
                    await anyio.to_thread.run_sync(self.application.repository.settle, delivery.id, "failed", str(error))
                except BaseException:
                    with anyio.CancelScope(shield=True):
                        await anyio.to_thread.run_sync(self.application.repository.settle, delivery.id, "unknown", "发送被中断，结果未知。")
                    raise
            await anyio.to_thread.run_sync(self.publish)
            await anyio.sleep(0.5)

    def _prepare(self) -> None:
        if self._preparing.is_set() or not self.active() or not self.application.repository.pending():
            return
        self._preparing.set()

        def prepare(cancel: threading.Event) -> None:
            try:
                self.application.process_pending(lambda: cancel.is_set() or not self.active())
            finally:
                self._preparing.clear()

        try:
            accepted = self.launch(prepare)
        except BaseException:
            self._preparing.clear()
            raise
        if not accepted:
            self._preparing.clear()

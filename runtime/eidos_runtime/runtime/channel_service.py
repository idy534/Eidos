from __future__ import annotations

from collections.abc import Callable
from functools import partial
import logging
import time
import threading

import anyio

from eidos_runtime.application.channels import ChannelApplication, accepts_message
from eidos_runtime.domain.channels import CHANNEL_NAMES, ChannelError, ChannelKind
from eidos_runtime.protocol.channels import ChannelResponse
from eidos_runtime.infrastructure.channel_adapter import ChannelAdapter, ChannelSendError
from eidos_runtime.infrastructure.dingtalk import DingTalkAdapter
from eidos_runtime.infrastructure.feishu import FeishuAdapter
from eidos_runtime.infrastructure.wecom import WeComAdapter

_LOGGER = logging.getLogger("eidos.channels")


class ChannelService:
    def __init__(self, application: ChannelApplication, active: Callable[[], bool], publish: Callable[[], object],
                 launch: Callable[[Callable[[threading.Event], None]], bool],
                 on_change: Callable[[ChannelKind], None] | None = None) -> None:
        self.application = application
        self.active = active
        self.publish = publish
        self.launch = launch
        self.on_change = on_change
        self._preparing = threading.Event()
        self.adapter = DingTalkAdapter()
        self.adapters: dict[ChannelKind, ChannelAdapter] = {"feishu": FeishuAdapter(), "wecom": WeComAdapter()}
        self.states: dict[ChannelKind, str] = {}
        self.reasons: dict[ChannelKind, str | None] = {}

    def _notify_change(self, channel: ChannelKind) -> None:
        if self.on_change is None:
            return
        try:
            self.on_change(channel)
        except Exception:
            _LOGGER.warning("Channel update notification failed channel=%s", channel, exc_info=True)

    def _set_state(self, channel: ChannelKind, state: str, reason: str | None = None) -> None:
        previous_state = self.states.get(channel)
        previous_reason = self.reasons.get(channel)
        if previous_state == state and previous_reason == reason:
            return
        self.states[channel] = state
        self.reasons[channel] = reason
        if previous_state is None and state == "disabled" and reason is None:
            return
        self._notify_change(channel)

    def read(self, channel: ChannelKind = "dingtalk") -> ChannelResponse:
        response = self.application.read(channel)
        if response.connection is None or not response.connection.config.enabled:
            return response.model_copy(update={"state": "disabled", "reason": None})
        return response.model_copy(update={"state": self.states.get(channel, "connecting"), "reason": self.reasons.get(channel)})

    def _adapter(self, channel: ChannelKind) -> ChannelAdapter:
        return self.adapter if channel == "dingtalk" else self.adapters[channel]

    async def run(self) -> None:
        # Recovery runs once, before any provider can claim a delivery.
        recovered = False
        while True:
            try:
                if not recovered:
                    await anyio.to_thread.run_sync(self.application.repository.recover)
                    recovered = True
                async with anyio.create_task_group() as group:
                    group.start_soon(self._work)
                    for channel in CHANNEL_NAMES:
                        group.start_soon(self._listen, channel)
            except Exception as error:
                _LOGGER.warning("Channel worker restarting exception=%s", type(error).__name__)
                await anyio.sleep(3)

    async def _listen(self, channel: ChannelKind) -> None:
        adapter = self._adapter(channel)
        try:
            while True:
                try:
                    connection = await anyio.to_thread.run_sync(self.application.repository.connection, channel)
                    if not self.active() or connection is None or not connection.config.enabled:
                        self._set_state(channel, "disabled", None)
                        await anyio.sleep(1)
                        continue
                    secret = await anyio.to_thread.run_sync(self.application.secrets.read, connection.secret_ref)
                    self._set_state(channel, "connecting", None)
                    started = time.monotonic()
                    async with anyio.create_task_group() as group:
                        group.start_soon(adapter.listen, connection, secret, self.application.receive)
                        while self.active() and await anyio.to_thread.run_sync(self.application.repository.connection, channel) == connection:
                            if adapter.connected:
                                self._set_state(channel, "connected", None)
                            elif time.monotonic() - started > 15:
                                self._set_state(channel, "error", CHANNEL_NAMES[channel] + "连接尚未建立，请检查凭证和网络。")
                            else:
                                self._set_state(channel, "connecting", self.reasons.get(channel))
                            await anyio.sleep(1)
                        group.cancel_scope.cancel()
                except Exception as error:
                    _LOGGER.warning("Channel service restarting channel=%s exception=%s", channel, type(error).__name__)
                    self._set_state(channel, "error", "渠道连接失败，请检查凭证、模型和网络。")
                    await anyio.sleep(3)
        finally:
            self._set_state(channel, "disabled", None)

    async def _work(self) -> None:
        while True:
            if not self.active():
                await anyio.sleep(0.5)
                continue
            await anyio.to_thread.run_sync(self._prepare)
            await anyio.to_thread.run_sync(self.application.repository.collect_replies)
            delivery = await anyio.to_thread.run_sync(self.application.repository.claim_delivery)
            if delivery is not None:
                incoming = None
                try:
                    incoming = await anyio.to_thread.run_sync(self.application.repository.inbound, delivery.inbound_id)
                    current = await anyio.to_thread.run_sync(self.application.repository.connection, incoming.connection.channel)
                    if current is None or current.config.client_id != incoming.connection.config.client_id or not accepts_message(current, incoming.message):
                        raise ChannelError("渠道已经停用或应用已经改变。")
                    secret = await anyio.to_thread.run_sync(self.application.secrets.read, current.secret_ref)
                    await self._adapter(current.channel).send(current, secret, incoming.message, delivery.text)
                    await anyio.to_thread.run_sync(self.application.repository.settle, delivery.id, "sent")
                    self._notify_change(incoming.connection.channel)
                except ChannelSendError as error:
                    await anyio.to_thread.run_sync(partial(self.application.repository.settle, delivery.id,
                        "unknown" if error.uncertain else "failed", str(error), retry=error.retry))
                    if incoming is not None:
                        self._notify_change(incoming.connection.channel)
                except ChannelError as error:
                    await anyio.to_thread.run_sync(self.application.repository.settle, delivery.id, "failed", str(error))
                    if incoming is not None:
                        self._notify_change(incoming.connection.channel)
                except BaseException:
                    with anyio.CancelScope(shield=True):
                        await anyio.to_thread.run_sync(self.application.repository.settle, delivery.id, "unknown", "发送被中断，结果未知。")
                        if incoming is not None:
                            self._notify_change(incoming.connection.channel)
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

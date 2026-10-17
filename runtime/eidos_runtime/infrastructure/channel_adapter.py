from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from eidos_runtime.domain.channels import ChannelConnection, ChannelMessage


class ChannelSendError(Exception):
    def __init__(self, reason: str, *, uncertain: bool = False, retry: bool = False) -> None:
        super().__init__(reason)
        self.uncertain = uncertain
        self.retry = retry


class ChannelAdapter(Protocol):
    @property
    def connected(self) -> bool: ...

    async def listen(self, connection: ChannelConnection, secret: str,
                     receive: Callable[[ChannelConnection, ChannelMessage], None]) -> None: ...

    async def send(self, connection: ChannelConnection, secret: str, message: ChannelMessage, text: str) -> None: ...

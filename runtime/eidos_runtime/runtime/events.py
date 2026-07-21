from __future__ import annotations

from typing import Callable


class RuntimeEvents:
    """The compatibility seam for existing runtime notifications.

    It intentionally preserves the current JSON-RPC notification names and
    payloads; committed-event projection remains a later, separate change.
    """

    def __init__(self, notify: Callable[[dict[str, object]], None]) -> None:
        self._notify = notify

    def emit(self, method: str, params: dict[str, object]) -> None:
        self._notify({"jsonrpc": "2.0", "method": method, "params": params})

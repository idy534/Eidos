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

    def item_completed(self, item: dict[str, object]) -> None:
        notification_item = item
        if item["kind"] == "assistant_message" and "content" in item:
            notification_item = {
                key: value for key, value in item.items() if key != "content"
            }
        elif item["kind"] == "file_change" and isinstance(item.get("toolCall"), dict):
            tool_call = {
                key: value
                for key, value in item["toolCall"].items()
                if key not in {"argumentsJson", "approvalDiff"}
            }
            notification_item = {**item, "toolCall": tool_call}
        self.emit(
            "item/completed",
            {
                "sessionId": item["sessionId"],
                "runId": item["runId"],
                "item": notification_item,
            },
        )

    def run_completed(self, run: dict[str, object]) -> None:
        self.emit(
            "run/completed",
            {"sessionId": run["sessionId"], "run": run},
        )

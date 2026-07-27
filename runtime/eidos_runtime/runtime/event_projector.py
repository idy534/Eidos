from __future__ import annotations


class EventProjector:
    """Pure projection from committed Event envelopes to protocol v1 notifications."""

    def project(
        self,
        event: dict[str, object],
        *,
        run: dict[str, object] | None = None,
        item: dict[str, object] | None = None,
    ) -> tuple[dict[str, object], ...]:
        event_type = event.get("eventType")
        payload = event.get("payload")
        if (
            event_type == "session.title_updated"
            and isinstance(payload, dict)
            and isinstance(payload.get("title"), str)
            and isinstance(event.get("sessionId"), str)
        ):
            return (self._notification("session/titleUpdated", {
                "sessionId": event["sessionId"],
                "title": payload["title"],
            }),)
        if (
            event_type in {"run.created", "run.status_changed"}
            and run is not None
            and run.get("status") == "running"
            and run.get("modelStepCount") == 0
        ):
            if (
                event_type == "run.created"
                and isinstance(payload, dict)
                and isinstance(payload.get("run"), dict)
                and payload["run"].get("status") != "running"
            ):
                return ()
            notifications = [self._notification("run/started", {
                "sessionId": run["sessionId"], "run": run,
            })]
            if item is not None:
                notifications.extend((
                    self._notification("item/started", {
                        "sessionId": item["sessionId"],
                        "runId": item["runId"],
                        "item": {
                            **{
                                key: value for key, value in item.items()
                                if key != "completedAt"
                            },
                            "status": "in_progress",
                        },
                    }),
                    self._notification("item/completed", {
                        "sessionId": item["sessionId"],
                        "runId": item["runId"],
                        "item": item,
                    }),
                ))
            return tuple(notifications)
        if (
            event_type == "run.updated"
            and run is not None
            and run.get("status") in {
                "queued", "running", "waiting_approval",
                "waiting_user_input", "finalizing",
            }
        ):
            return (self._notification("run/updated", {
                "sessionId": run["sessionId"], "run": run,
            }),)
        if event_type == "run.status_changed" and run is not None and isinstance(payload, dict):
            if payload.get("current") != run.get("status"):
                return ()
            method = (
                "run/completed"
                if payload.get("current") in {
                    "succeeded", "failed", "stopped", "canceled", "interrupted"
                }
                else "run/updated"
            )
            return (self._notification(method, {
                "sessionId": run["sessionId"], "run": run,
            }),)
        if event_type == "item.started" and item is not None:
            started_item = (
                item
                if item.get("status") == "in_progress"
                else {
                    **{
                        key: value
                        for key, value in item.items()
                        if key != "completedAt"
                    },
                    "status": "in_progress",
                }
            )
            return (self._notification("item/started", {
                "sessionId": item["sessionId"],
                "runId": item["runId"],
                "item": started_item,
            }),)
        if event_type == "item.completed" and item is not None:
            return (self._notification("item/completed", {
                "sessionId": item["sessionId"],
                "runId": item["runId"],
                "item": self._safe_completed_item(item),
            }),)
        if event_type == "item.delta" and isinstance(payload, dict):
            return (self._notification("item/delta", {
                "sessionId": event.get("sessionId"),
                "runId": event.get("runId"),
                "itemId": payload["itemId"],
                "sequence": payload["sequence"],
                "delta": payload["delta"],
            }),)
        if event_type == "approval.status_changed" and isinstance(payload, dict):
            current = payload.get("current")
            method = (
                "approval/requested"
                if current == "pending"
                else "approval/canceled"
                if current in {"canceled", "invalidated"}
                else "approval/resolved"
            )
            return (self._notification(method, {
                "sessionId": event.get("sessionId"),
                "runId": event.get("runId"),
                "approvalId": payload.get("entity_id"),
                "status": current,
            }),)
        return ()

    @staticmethod
    def _notification(method: str, params: dict[str, object]) -> dict[str, object]:
        return {"jsonrpc": "2.0", "method": method, "params": params}

    @staticmethod
    def _safe_completed_item(item: dict[str, object]) -> dict[str, object]:
        if item["kind"] == "assistant_message" and "content" in item:
            return {key: value for key, value in item.items() if key != "content"}
        if item["kind"] == "file_change" and isinstance(item.get("toolCall"), dict):
            tool_call = {
                key: value
                for key, value in item["toolCall"].items()
                if key not in {"argumentsJson", "approvalDiff"}
            }
            return {**item, "toolCall": tool_call}
        return item

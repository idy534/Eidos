from __future__ import annotations

from eidos_runtime.protocol.tool_text import project_tool_text
from eidos_runtime.domain.context_management import ContextCompactionActivity, ContextCompactionRecord


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
        compaction_notifications: tuple[dict[str, object], ...] = ()
        if event_type in {"run.updated", "context.compacted"} and isinstance(payload, dict):
            raw = payload.get("compaction")
            if isinstance(raw, dict) and raw.get("stage") in {"started", "finished"}:
                record = ContextCompactionRecord.model_validate(raw)
                activity = ContextCompactionActivity(
                    request_id=record.request_id, run_id=event["runId"], event_id=event["eventId"],
                    started_at=payload.get("compactionStartedAt", event["occurredAt"]),
                    updated_at=event["occurredAt"], status=record.status,
                    trigger=record.trigger, reason=record.reason,
                )
                compaction_notifications = (self._notification("context/compactionUpdated", {
                    "sessionId": event["sessionId"], "activity": activity.to_wire_dict(),
                }),)
        if event_type == "run.queue_changed" and isinstance(payload, dict):
            return (self._notification("run/queueChanged", payload),)
        if event_type in {"channel.changed", "web_search.settings_changed", "shell_environment.settings_changed"}:
            return ()
        if event_type == "automation.changed" and isinstance(payload, dict) and isinstance(payload.get("id"), str):
            return (self._notification("automation/changed", {"id": payload["id"]}),)
        if event_type == "sidebar.pins_changed" and isinstance(event.get("eventId"), int):
            return (self._notification("sidebar/pinsChanged", {
                "eventId": event["eventId"],
            }),)
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
            event_type == "context.compacted"
            and isinstance(payload, dict)
            and isinstance(event.get("sessionId"), str)
            and isinstance(event.get("runId"), str)
            and isinstance(payload.get("summaryId"), str)
            and isinstance(payload.get("sourceItemCount"), int)
            and isinstance(payload.get("phase"), str)
        ):
            return (*compaction_notifications, self._notification("context/compacted", {
                "sessionId": event["sessionId"],
                "runId": event["runId"],
                "summaryId": payload["summaryId"],
                "sourceItemCount": payload["sourceItemCount"],
                "phase": payload["phase"],
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
            and (run.get("status") in {
                "queued", "running", "waiting_approval", "waiting_input", "waiting_agents", "finalizing",
            } or isinstance(payload, dict) and str(payload.get("reason", "")).startswith("hook_"))
        ):
            return (*compaction_notifications, self._notification("run/updated", {
                "sessionId": run["sessionId"], "run": run,
            }),)
        if compaction_notifications:
            return compaction_notifications
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
        if event_type == "item.updated" and item is not None:
            return (self._notification("item/updated", {
                "sessionId": item["sessionId"], "runId": item["runId"], "item": item,
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
                **({"offset": payload["offset"]} if payload.get("offset") is not None else {}),
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
        item = params.get("item")
        if isinstance(item, dict):
            params = {**params, "item": project_tool_text(item)}
        return {"jsonrpc": "2.0", "method": method, "params": params}

    @staticmethod
    def _safe_completed_item(item: dict[str, object]) -> dict[str, object]:
        if item["kind"] == "file_change" and isinstance(item.get("toolCall"), dict):
            tool_call = {
                key: value
                for key, value in item["toolCall"].items()
                if key != "argumentsJson"
            }
            return {**item, "toolCall": tool_call}
        return item

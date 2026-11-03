from __future__ import annotations

import json

from eidos_runtime.db.database import Repository
from eidos_runtime.domain.model_retry import ModelRetryActivity


class ModelRetryRepository(Repository):
    """Reads the display projection of provider retries from the durable event log."""

    def activities(
        self, session_id: str, run_ids: tuple[str, ...], *, through_event_id: int,
        since: int, before: int | None = None, byte_budget: int,
    ) -> tuple[ModelRetryActivity, ...]:
        if not run_ids or byte_budget <= 0:
            return ()
        marks = ",".join("?" for _ in run_ids)
        with self.lock:
            rows = self._connection().execute(f"""
                SELECT id, run_id, occurred_at, payload_json FROM events
                WHERE session_id=? AND run_id IN ({marks}) AND id<=?
                  AND event_type='model.retry_changed'
                ORDER BY id DESC LIMIT 2000
            """, (session_id, *run_ids, through_event_id)).fetchall()
        latest: dict[str, ModelRetryActivity] = {}
        for row in rows:
            payload = json.loads(row["payload_json"])
            key = row["run_id"] + ":" + payload["requestGroupId"]
            if key in latest:
                continue
            latest[key] = ModelRetryActivity.model_validate({
                **payload, "runId": row["run_id"], "eventId": row["id"], "updatedAt": row["occurred_at"],
            })
        selected: list[ModelRetryActivity] = []
        used = 0
        for activity in sorted(latest.values(), key=lambda value: (value.started_at, value.event_id), reverse=True):
            if activity.started_at < since or (before is not None and activity.started_at >= before):
                continue
            size = len(activity.model_dump_json().encode("utf-8")) + 1
            if len(selected) >= 200 or used + size > byte_budget:
                break
            selected.append(activity)
            used += size
        return tuple(reversed(selected))

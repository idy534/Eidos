from __future__ import annotations

import json
import sqlite3
from uuid import uuid4

from pydantic import TypeAdapter

from eidos_runtime.db.database import CommittedMutation, Repository, now_ms
from eidos_runtime.db.errors import InvalidRunStateError, ResourceNotFoundError
from eidos_runtime.db.events import append_event
from eidos_runtime.domain.queued_runs import QueuedMessage, QueuedMessagePage, QueuedRunChange, QueuedRunDeleted, QueuedRunSteered, QueuedRunUpdated
from eidos_runtime.domain.input_reference import InputReference
from eidos_runtime.model.client import ModelProfileSnapshot
from eidos_runtime.runtime.state_machine import EventType


class QueuedRunInputConflict(ValueError):
    pass


class SteerTargetUnavailable(ValueError):
    pass


class QueuedSteerUnsupported(ValueError):
    pass


class QueuedRunRepository(Repository):
    """Edits only never-started user Runs under the same lock as FIFO claiming."""

    def list(self, session_id: str, after_sequence: int = 0) -> QueuedMessagePage:
        with self.lock:
            connection = self._connection()
            if connection.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone() is None:
                raise ResourceNotFoundError("session not found")
            rows = connection.execute(
                "SELECT r.creation_seq, r.id, r.user_input, i.input_references_json FROM runs r "
                "JOIN items i ON i.run_id = r.id AND i.kind = 'user_message' AND i.ordinal = 1 "
                "WHERE r.session_id = ? AND r.status = 'queued' AND r.started_at IS NULL "
                "AND r.creation_seq > ? ORDER BY r.enqueued_at, r.creation_seq LIMIT 101",
                (session_id, after_sequence),
            ).fetchall()
            items: list[QueuedMessage] = []
            size = 0
            for row in rows[:100]:
                message = QueuedMessage(
                    session_id=session_id, run_id=row["id"], user_input=row["user_input"],
                    references=tuple(InputReference.model_validate(value) for value in json.loads(row["input_references_json"])),
                )
                message_size = len(message.model_dump_json().encode())
                if items and size + message_size > 512 * 1024:
                    break
                items.append(message)
                size += message_size
            return QueuedMessagePage(
                items=tuple(items),
                next_sequence=rows[len(items) - 1]["creation_seq"] if len(rows) > len(items) else None,
            )

    def change(
        self, session_id: str, run_id: str, expected_user_input: str,
        user_input: str | None, *, operation_id: str | None,
        target_run_id: str | None = None,
    ) -> CommittedMutation[QueuedRunChange]:
        def write(connection: sqlite3.Connection) -> CommittedMutation[QueuedRunChange]:
            row = connection.execute(
                "SELECT * FROM runs WHERE id = ? AND session_id = ?", (run_id, session_id),
            ).fetchone()
            if row is None:
                raise ResourceNotFoundError("queued run not found")
            if (
                row["status"] != "queued" or row["started_at"] is not None
                or row["model_step_count"] != 0 or row["cancel_requested_at"] is not None
                or row["reconciliation_required"] or row["side_effects_may_exist"]
                or connection.execute(
                    "SELECT 1 FROM execution_segments WHERE run_id = ?", (run_id,),
                ).fetchone() is not None
                or connection.execute(
                    "SELECT 1 FROM agent_delegations WHERE child_session_id = ?", (session_id,),
                ).fetchone() is not None
            ):
                raise InvalidRunStateError("queued message has already started")
            if row["user_input"] != expected_user_input:
                raise QueuedRunInputConflict("queued message changed")
            item = connection.execute(
                "SELECT * FROM items WHERE run_id = ? AND kind = 'user_message' AND ordinal = 1",
                (run_id,),
            ).fetchone()
            if item is None:
                raise InvalidRunStateError("queued message is unavailable")
            now = max(now_ms(), row["updated_at"] + 1)
            change: QueuedRunChange
            events = []
            if target_run_id is not None:
                target = connection.execute(
                    "SELECT * FROM runs WHERE id = ? AND session_id = ? "
                    "AND status IN ('running', 'waiting_approval', 'waiting_input', 'waiting_agents') "
                    "AND cancel_requested_at IS NULL", (target_run_id, session_id),
                ).fetchone()
                if target is None:
                    raise SteerTargetUnavailable("the selected run is no longer active")
                references = tuple(InputReference.model_validate(value) for value in json.loads(item["input_references_json"]))
                # A steering message cannot replace the active Run's frozen
                # agent/extension catalog or an accepted plan execution.
                if row["plan_id"] is not None or any(value.status == "selection" for value in references):
                    raise QueuedSteerUnsupported("selection messages must remain queued")
                if any(value.kind == "image" for value in references) and not ModelProfileSnapshot.model_validate_json(target["model_profile_json"]).supports_images:
                    raise QueuedSteerUnsupported("the active model does not support this image")
                input_id = str(uuid4())
                connection.execute(
                    "INSERT INTO input_mailbox (id, run_id, content, status, created_at) "
                    "VALUES (?, ?, ?, 'pending', ?)",
                    (input_id, target_run_id, row["user_input"], now),
                )
                # The mailbox and its visible Item share one identity. Pending
                # Items remain out of model context until a sampling boundary.
                connection.execute(
                    "INSERT INTO items (id, session_id, run_id, ordinal, kind, status, content, created_at, input_references_json) "
                    "VALUES (?, ?, ?, ?, 'user_message', 'in_progress', ?, ?, ?)",
                    (input_id, session_id, target_run_id, self._next_ordinal(connection, target_run_id),
                     row["user_input"], now, item["input_references_json"]),
                )
                for event_type, payload in (
                    (EventType.INPUT_QUEUED, {"inputId": input_id}),
                    (EventType.ITEM_STARTED, {"itemId": input_id}),
                ):
                    events.append(append_event(connection, event_type, now, payload,
                                               session_id=session_id, run_id=target_run_id))
            if user_input is None:
                # These Runs have no execution resources. Remove their input and snapshots,
                # so a deleted message cannot enter the following Run's context.
                connection.execute(
                    "DELETE FROM event_outbox WHERE event_id IN (SELECT id FROM events WHERE run_id = ?)",
                    (run_id,),
                )
                connection.execute("DELETE FROM events WHERE run_id = ?", (run_id,))
                connection.execute("DELETE FROM items WHERE run_id = ?", (run_id,))
                connection.execute("DELETE FROM run_resolution_snapshots WHERE run_id = ?", (run_id,))
                connection.execute(
                    "UPDATE plans SET execution_run_id = NULL, status = 'review', updated_at = ? "
                    "WHERE execution_run_id = ? AND status = 'accepted'", (now, run_id),
                )
                connection.execute("DELETE FROM runs WHERE id = ?", (run_id,))
                change = (
                    QueuedRunSteered(kind="steered", session_id=session_id, run_id=run_id,
                                     target_run_id=target_run_id, item_id=input_id)
                    if target_run_id is not None else
                    QueuedRunDeleted(kind="deleted", session_id=session_id, run_id=run_id)
                )
            else:
                if not user_input.strip() and not json.loads(item["input_references_json"]):
                    raise ValueError("queued message must not be blank")
                connection.execute(
                    "UPDATE runs SET user_input = ?, updated_at = ? WHERE id = ?",
                    (user_input, now, run_id),
                )
                connection.execute("UPDATE items SET content = ? WHERE id = ?", (user_input, item["id"]))
                change = QueuedRunUpdated(
                    kind="updated",
                    session_id=session_id, run_id=run_id, user_input=user_input, updated_at=now,
                )
            connection.execute("UPDATE sessions SET updated_at = ? WHERE id = ?", (now, session_id))
            event = append_event(
                connection, EventType.RUN_QUEUE_CHANGED, now,
                {"change": change.to_wire_dict()}, session_id=session_id,
            )
            return CommittedMutation(change, (*events, event))

        return self._write_committed(
            write, operation_id=operation_id,
            operation_scope="run/steerQueued" if target_run_id is not None else "run/deleteQueued" if user_input is None else "run/updateQueued",
            operation_request={
                "sessionId": session_id, "runId": run_id,
                "expectedUserInput": expected_user_input, "userInput": user_input,
                **({"targetRunId": target_run_id} if target_run_id is not None else {}),
            },
            serialize_value=lambda change: change.to_wire_dict(),
            deserialize_value=lambda value: TypeAdapter(QueuedRunChange).validate_python(value),
        )

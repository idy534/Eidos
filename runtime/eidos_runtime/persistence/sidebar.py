from __future__ import annotations

import json
import sqlite3
from typing import Literal

from eidos_runtime.db.database import CommittedMutation, Repository, now_ms
from eidos_runtime.db.errors import ResourceNotFoundError
from eidos_runtime.db.events import append_event
from eidos_runtime.domain.sidebar import (
    MAX_SIDEBAR_PINS,
    ProjectSidebarTarget,
    SessionSidebarTarget,
    SidebarPinsSnapshot,
    SidebarTarget,
)
from eidos_runtime.runtime.state_machine import EventType


class SidebarPinLimitReachedError(ValueError):
    pass


def remove_sidebar_pin(
    connection: sqlite3.Connection, kind: Literal["project", "session"], target_id: str
) -> tuple[dict[str, object], ...]:
    column = "project_id" if kind == "project" else "session_id"
    changed = connection.execute(
        f"DELETE FROM sidebar_pins WHERE {column} = ?", (target_id,)
    ).rowcount
    if not changed:
        return ()
    return (append_event(connection, EventType.SIDEBAR_PINS_CHANGED, now_ms(), {}),)


class SidebarRepository(Repository):
    """Owns the ordered sidebar references, independently of task activity."""

    def list_pins(self) -> SidebarPinsSnapshot:
        with self.lock:
            return self._snapshot(self._connection())

    @staticmethod
    def _snapshot(connection: sqlite3.Connection) -> SidebarPinsSnapshot:
        rows = connection.execute(
            "SELECT project_id, session_id FROM sidebar_pins ORDER BY pin_seq LIMIT ?",
            (MAX_SIDEBAR_PINS,),
        ).fetchall()
        revision = connection.execute(
            "SELECT COALESCE(MAX(id), 0) FROM events "
            "WHERE event_type = 'sidebar.pins_changed'"
        ).fetchone()[0]
        return SidebarPinsSnapshot(
            items=tuple(
                ProjectSidebarTarget(kind="project", id=row["project_id"])
                if row["project_id"] is not None
                else SessionSidebarTarget(kind="session", id=row["session_id"])
                for row in rows
            ),
            through_event_id=revision,
        )

    def set_pinned(
        self, target: SidebarTarget, pinned: bool, *, operation_id: str
    ) -> CommittedMutation[SidebarPinsSnapshot]:
        def write(connection: sqlite3.Connection) -> CommittedMutation[SidebarPinsSnapshot]:
            table = "projects" if target.kind == "project" else "sessions"
            if connection.execute(
                f"SELECT 1 FROM {table} WHERE id = ?", (target.id,)
            ).fetchone() is None:
                raise ResourceNotFoundError("sidebar target not found")
            if target.kind == "session" and connection.execute(
                "SELECT 1 FROM agent_delegations WHERE child_session_id = ?",
                (target.id,),
            ).fetchone() is not None:
                raise ResourceNotFoundError("sidebar target not found")
            column = "project_id" if target.kind == "project" else "session_id"
            events: tuple[dict[str, object], ...] = ()
            if pinned:
                exists = connection.execute(
                    f"SELECT 1 FROM sidebar_pins WHERE {column} = ?", (target.id,)
                ).fetchone()
                if exists is None:
                    if connection.execute(
                        "SELECT COUNT(*) FROM sidebar_pins"
                    ).fetchone()[0] >= MAX_SIDEBAR_PINS:
                        raise SidebarPinLimitReachedError("sidebar pin limit reached")
                    connection.execute(
                        f"INSERT INTO sidebar_pins ({column}) VALUES (?)", (target.id,)
                    )
                    events = (
                        append_event(connection, EventType.SIDEBAR_PINS_CHANGED, now_ms(), {}),
                    )
            else:
                events = remove_sidebar_pin(connection, target.kind, target.id)
            return CommittedMutation(self._snapshot(connection), events)

        return self._write_committed(
            write,
            operation_id=operation_id,
            operation_scope="sidebar/setPinned",
            operation_request={"target": target.to_wire_dict(), "pinned": pinned},
            serialize_value=lambda snapshot: snapshot.to_wire_dict(),
            deserialize_value=lambda value: SidebarPinsSnapshot.model_validate_json(
                json.dumps(value)
            ),
        )

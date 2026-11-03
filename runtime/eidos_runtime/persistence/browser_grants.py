from __future__ import annotations

import sqlite3
import uuid

from eidos_runtime.db.database import CommittedMutation, Repository, now_ms
from eidos_runtime.db.events import append_event
from eidos_runtime.domain.browser import BrowserGrant, BrowserGrantRevokeReason
from eidos_runtime.runtime.state_machine import EventType


_TERMINAL_RUN_STATUSES = frozenset({"succeeded", "failed", "stopped", "canceled", "interrupted"})


class BrowserGrantRepository(Repository):
    """Session browser grants; creation and revocation commit with their Event."""

    def active(self, session_id: str) -> BrowserGrant | None:
        with self.lock:
            row = self._connection().execute(
                "SELECT * FROM session_browser_grants WHERE session_id = ? AND profile = 'default' "
                "AND status = 'active'",
                (session_id,),
            ).fetchone()
        return _grant(row) if row is not None else None

    def grant_session_for_run(self, run_id: str) -> str:
        """A child task uses its parent Session's grant while the parent Run is active."""

        with self.lock:
            row = self._connection().execute(
                """
                SELECT r.session_id AS own, parent.session_id AS parent, parent.status AS parent_status
                FROM runs AS r
                LEFT JOIN agent_delegations AS d ON d.child_session_id = r.session_id
                LEFT JOIN runs AS parent ON parent.id = d.parent_run_id
                WHERE r.id = ?
                """,
                (run_id,),
            ).fetchone()
        if row is None:
            raise ValueError("run_not_found")
        if row["parent"] is not None and row["parent_status"] not in _TERMINAL_RUN_STATUSES:
            return str(row["parent"])
        return str(row["own"])

    def approved_source(self, item_id: str) -> str | None:
        """The approval that just authorized this tool item becomes the grant source."""

        with self.lock:
            row = self._connection().execute(
                "SELECT id FROM approvals WHERE item_id = ? AND status = 'approved' "
                "ORDER BY creation_seq DESC LIMIT 1",
                (item_id,),
            ).fetchone()
        return str(row["id"]) if row is not None else None

    def grant(
        self,
        *,
        session_id: str,
        plugin_id: str,
        plugin_version: str,
        plugin_hash: str,
        source_approval_id: str | None,
    ) -> CommittedMutation[BrowserGrant]:
        """Activate a grant; replaying the same approval returns the same grant."""

        def write(connection: sqlite3.Connection) -> CommittedMutation[BrowserGrant]:
            if source_approval_id is not None:
                existing = connection.execute(
                    "SELECT * FROM session_browser_grants WHERE source_approval_id = ?",
                    (source_approval_id,),
                ).fetchone()
                if existing is not None:
                    return CommittedMutation(_grant(existing), ())
            current = connection.execute(
                "SELECT * FROM session_browser_grants WHERE session_id = ? AND profile = 'default' "
                "AND status = 'active'",
                (session_id,),
            ).fetchone()
            if current is not None and current["plugin_hash"] == plugin_hash:
                return CommittedMutation(_grant(current), ())
            now = now_ms()
            if current is not None:
                _revoke(connection, current["id"], "plugin_changed", now)
            grant_id = str(uuid.uuid4())
            connection.execute(
                """
                INSERT INTO session_browser_grants (
                    id, session_id, profile, access, plugin_id, plugin_version, plugin_hash,
                    source_approval_id, status, created_at
                ) VALUES (?, ?, 'default', 'interact', ?, ?, ?, ?, 'active', ?)
                """,
                (grant_id, session_id, plugin_id, plugin_version, plugin_hash, source_approval_id, now),
            )
            event = append_event(
                connection, EventType.BROWSER_GRANT_CHANGED, now,
                {"sessionId": session_id, "active": True}, session_id=session_id,
            )
            row = connection.execute("SELECT * FROM session_browser_grants WHERE id = ?", (grant_id,)).fetchone()
            return CommittedMutation(_grant(row), (event,))

        with self.database.transaction() as connection:
            return write(connection)

    def revoke(self, session_id: str, reason: BrowserGrantRevokeReason) -> CommittedMutation[bool]:
        def write(connection: sqlite3.Connection) -> CommittedMutation[bool]:
            current = connection.execute(
                "SELECT id FROM session_browser_grants WHERE session_id = ? AND profile = 'default' "
                "AND status = 'active'",
                (session_id,),
            ).fetchone()
            if current is None:
                return CommittedMutation(False, ())
            now = now_ms()
            _revoke(connection, current["id"], reason, now)
            event = append_event(
                connection, EventType.BROWSER_GRANT_CHANGED, now,
                {"sessionId": session_id, "active": False}, session_id=session_id,
            )
            return CommittedMutation(True, (event,))

        with self.database.transaction() as connection:
            return write(connection)

    def revoke_all(self, reason: BrowserGrantRevokeReason) -> CommittedMutation[int]:
        """Clearing the shared profile revokes every Session's grant."""

        def write(connection: sqlite3.Connection) -> CommittedMutation[int]:
            rows = connection.execute(
                "SELECT id, session_id FROM session_browser_grants WHERE status = 'active'"
            ).fetchall()
            now = now_ms()
            events = []
            for row in rows:
                _revoke(connection, row["id"], reason, now)
                events.append(append_event(
                    connection, EventType.BROWSER_GRANT_CHANGED, now,
                    {"sessionId": row["session_id"], "active": False}, session_id=row["session_id"],
                ))
            return CommittedMutation(len(rows), tuple(events))

        with self.database.transaction() as connection:
            return write(connection)


def delete_session_browser_grants(connection: sqlite3.Connection, session_id: str) -> None:
    """Session deletion removes its grant history before its approvals."""

    connection.execute("DELETE FROM session_browser_grants WHERE session_id = ?", (session_id,))


def _revoke(connection: sqlite3.Connection, grant_id: str, reason: str, now: int) -> None:
    connection.execute(
        "UPDATE session_browser_grants SET status = 'revoked', revoked_at = ?, revoke_reason = ? "
        "WHERE id = ? AND status = 'active'",
        (now, reason, grant_id),
    )


def _grant(row: sqlite3.Row) -> BrowserGrant:
    return BrowserGrant(
        id=row["id"], session_id=row["session_id"], profile=row["profile"], access=row["access"],
        plugin_id=row["plugin_id"], plugin_version=row["plugin_version"], plugin_hash=row["plugin_hash"],
        source_approval_id=row["source_approval_id"], status=row["status"], created_at=row["created_at"],
        revoked_at=row["revoked_at"], revoke_reason=row["revoke_reason"],
    )

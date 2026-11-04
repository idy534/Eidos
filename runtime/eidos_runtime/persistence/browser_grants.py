from __future__ import annotations

import sqlite3
import uuid

from eidos_runtime.db.database import CommittedMutation, Repository, now_ms
from eidos_runtime.db.events import append_event
from eidos_runtime.domain.browser import (
    DEFAULT_BROWSER_TARGET, BrowserGrant, BrowserGrantRevokeReason, BrowserProfile, BrowserTarget,
)
from eidos_runtime.runtime.state_machine import EventType


_TERMINAL_RUN_STATUSES = frozenset({"succeeded", "failed", "stopped", "canceled", "interrupted"})


class BrowserGrantRepository(Repository):
    """Session browser grants; creation and revocation commit with their Event."""

    def active(self, session_id: str, profile: BrowserProfile = "default") -> BrowserGrant | None:
        with self.lock:
            row = self._connection().execute(
                "SELECT * FROM session_browser_grants WHERE session_id = ? AND profile = ? "
                "AND status = 'active'",
                (session_id, profile),
            ).fetchone()
        return _grant(row) if row is not None else None

    def target(self) -> BrowserTarget:
        with self.lock:
            row = self._connection().execute("SELECT target FROM browser_settings WHERE id = 1").fetchone()
        if row is not None and row["target"] in ("eidos", "user_chrome", "chrome_extension"):
            return row["target"]
        return DEFAULT_BROWSER_TARGET

    def extension_credential(self) -> str | None:
        """Reference to the saved Playwright Extension token; never the token."""

        with self.lock:
            row = self._connection().execute(
                "SELECT extension_credential FROM browser_settings WHERE id = 1",
            ).fetchone()
        return row["extension_credential"] if row is not None else None

    def set_target(self, target: BrowserTarget) -> CommittedMutation[BrowserTarget]:
        """Switching Chrome targets is a user decision recorded with its Event."""

        def write(connection: sqlite3.Connection) -> CommittedMutation[BrowserTarget]:
            now = now_ms()
            connection.execute(
                "INSERT INTO browser_settings (id, target, updated_at) VALUES (1, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET target = excluded.target, updated_at = excluded.updated_at",
                (target, now),
            )
            event = append_event(connection, EventType.BROWSER_SETTINGS_CHANGED, now, {"id": "browser"})
            return CommittedMutation(target, (event,))

        with self.database.transaction() as connection:
            return write(connection)

    def set_extension_credential(self, credential: str | None) -> CommittedMutation[str | None]:
        """Save or forget the extension token reference; the previous one is returned."""

        def write(connection: sqlite3.Connection) -> CommittedMutation[str | None]:
            now = now_ms()
            row = connection.execute(
                "SELECT target, extension_credential FROM browser_settings WHERE id = 1",
            ).fetchone()
            previous = row["extension_credential"] if row is not None else None
            connection.execute(
                "INSERT INTO browser_settings (id, target, extension_credential, updated_at) VALUES (1, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET extension_credential = excluded.extension_credential, "
                "updated_at = excluded.updated_at",
                (row["target"] if row is not None else DEFAULT_BROWSER_TARGET, credential, now),
            )
            event = append_event(connection, EventType.BROWSER_SETTINGS_CHANGED, now, {"id": "browser"})
            return CommittedMutation(previous, (event,))

        with self.database.transaction() as connection:
            return write(connection)

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
        profile: BrowserProfile = "default",
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
                "SELECT * FROM session_browser_grants WHERE session_id = ? AND profile = ? "
                "AND status = 'active'",
                (session_id, profile),
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
                ) VALUES (?, ?, ?, 'interact', ?, ?, ?, ?, 'active', ?)
                """,
                (grant_id, session_id, profile, plugin_id, plugin_version, plugin_hash, source_approval_id, now),
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
            rows = connection.execute(
                "SELECT id FROM session_browser_grants WHERE session_id = ? AND status = 'active'",
                (session_id,),
            ).fetchall()
            if not rows:
                return CommittedMutation(False, ())
            now = now_ms()
            for row in rows:
                _revoke(connection, row["id"], reason, now)
            event = append_event(
                connection, EventType.BROWSER_GRANT_CHANGED, now,
                {"sessionId": session_id, "active": False}, session_id=session_id,
            )
            return CommittedMutation(True, (event,))

        with self.database.transaction() as connection:
            return write(connection)

    def revoke_all(
        self, reason: BrowserGrantRevokeReason, profile: BrowserProfile = "default",
    ) -> CommittedMutation[int]:
        """Clearing a profile, or leaving a Chrome target, revokes its grants in every Session."""

        def write(connection: sqlite3.Connection) -> CommittedMutation[int]:
            rows = connection.execute(
                "SELECT id, session_id FROM session_browser_grants WHERE status = 'active' AND profile = ?",
                (profile,),
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

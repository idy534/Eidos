from __future__ import annotations

import sqlite3


# Session-scoped permission for the bundled browser tools, per Chrome profile. The approval that
# created a grant stays referenced; revocation keeps the row for history.
BROWSER_GRANTS_SCHEMA_SQL = """
CREATE TABLE session_browser_grants (
    creation_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE RESTRICT,
    profile TEXT NOT NULL CHECK (profile IN ('default', 'user')),
    access TEXT NOT NULL CHECK (access = 'interact'),
    plugin_id TEXT NOT NULL,
    plugin_version TEXT NOT NULL,
    plugin_hash TEXT NOT NULL,
    source_approval_id TEXT REFERENCES approvals(id) ON DELETE RESTRICT,
    status TEXT NOT NULL CHECK (status IN ('active', 'revoked')),
    created_at INTEGER NOT NULL,
    revoked_at INTEGER,
    revoke_reason TEXT CHECK (
        revoke_reason IS NULL OR revoke_reason IN ('user', 'plugin_changed', 'profile_cleared')
    ),
    CHECK (
        (status = 'active' AND revoked_at IS NULL AND revoke_reason IS NULL)
        OR (status = 'revoked' AND revoked_at IS NOT NULL AND revoke_reason IS NOT NULL)
    )
);

CREATE UNIQUE INDEX one_active_browser_grant_per_session
ON session_browser_grants (session_id, profile)
WHERE status = 'active';

CREATE INDEX session_browser_grants_by_session
ON session_browser_grants (session_id, creation_seq);

-- Which Chrome the browser tools drive: the Eidos profile or, after the
-- user opts in, their own everyday Chrome.
CREATE TABLE browser_settings (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    target TEXT NOT NULL CHECK (target IN ('eidos', 'user_chrome')),
    updated_at INTEGER NOT NULL
);

CREATE UNIQUE INDEX session_browser_grants_source_approval
ON session_browser_grants (source_approval_id)
WHERE source_approval_id IS NOT NULL;
"""


def migrate_browser_grants(connection: sqlite3.Connection) -> None:
    try:
        connection.executescript("BEGIN IMMEDIATE;\n" + BROWSER_GRANTS_SCHEMA_SQL)
        connection.execute("PRAGMA user_version=48")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise

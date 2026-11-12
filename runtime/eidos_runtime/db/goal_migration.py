from __future__ import annotations

import sqlite3

GOAL_V51_SCHEMA_SQL = """
CREATE TABLE session_goals (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK(status IN ('active','paused','blocked','budget_limited','complete','cleared')),
    revision INTEGER NOT NULL CHECK(revision > 0),
    goal_json TEXT NOT NULL CHECK(json_valid(goal_json)),
    progress_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(progress_json)),
    CHECK(status=json_extract(goal_json,'$.status')),
    CHECK(revision=json_extract(goal_json,'$.revision'))
);
CREATE UNIQUE INDEX one_open_goal_per_session ON session_goals(session_id)
WHERE status IN ('active','paused','blocked','budget_limited');
CREATE TABLE goal_runs (
    run_id TEXT PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
    goal_id TEXT NOT NULL REFERENCES session_goals(id) ON DELETE CASCADE,
    objective_revision INTEGER NOT NULL,
    activation INTEGER NOT NULL,
    trigger TEXT NOT NULL CHECK(trigger IN ('goal_set','continuation','user_input','resume')),
    sequence INTEGER NOT NULL,
    sample_json TEXT,
    candidate_json TEXT,
    candidate_item_id TEXT,
    UNIQUE(goal_id, sequence)
);
CREATE TABLE goal_continuations (
    id TEXT PRIMARY KEY,
    goal_id TEXT NOT NULL REFERENCES session_goals(id) ON DELETE CASCADE,
    objective_revision INTEGER NOT NULL,
    activation INTEGER NOT NULL,
    trigger TEXT NOT NULL CHECK(trigger IN ('goal_set','continuation','resume')),
    source_run_id TEXT REFERENCES runs(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK(status IN ('pending','accepted','obsolete')),
    run_id TEXT UNIQUE REFERENCES runs(id) ON DELETE SET NULL,
    UNIQUE(goal_id, source_run_id)
);
CREATE UNIQUE INDEX one_pending_goal_continuation ON goal_continuations(goal_id) WHERE status='pending';
CREATE INDEX goal_runs_by_goal ON goal_runs(goal_id);
CREATE TRIGGER goal_run_no_learning AFTER INSERT ON goal_runs
WHEN NEW.trigger != 'user_input'
BEGIN
    UPDATE memory_source_items SET eligible=0 WHERE run_id=NEW.run_id;
END;
CREATE TRIGGER goal_item_no_learning AFTER INSERT ON memory_source_items
WHEN EXISTS(SELECT 1 FROM goal_runs WHERE run_id=NEW.run_id AND trigger!='user_input')
BEGIN
    UPDATE memory_source_items SET eligible=0 WHERE item_id=NEW.item_id;
END;
"""

GOAL_SCHEMA_SQL = GOAL_V51_SCHEMA_SQL.replace(",'budget_limited'", "").replace(
    "    candidate_item_id TEXT,", "    candidate_item_id TEXT,\n    reference_ids_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(reference_ids_json)),"
)


def migrate_goals(connection: sqlite3.Connection) -> None:
    try:
        connection.executescript("BEGIN IMMEDIATE;\n" + GOAL_V51_SCHEMA_SQL + "\nPRAGMA user_version=51;\nCOMMIT;")
    except sqlite3.Error:
        connection.rollback()
        raise


def migrate_goal_composer(connection: sqlite3.Connection) -> None:
    """Remove old goal budgets without resuming a goal the budget had stopped."""
    try:
        connection.executescript("""BEGIN IMMEDIATE;
            ALTER TABLE goal_runs ADD COLUMN reference_ids_json TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(reference_ids_json));
            UPDATE session_goals SET
                status=CASE WHEN status='budget_limited' THEN 'paused' ELSE status END,
                goal_json=json_set(json_remove(goal_json,'$.token_budget'),
                    '$.status',CASE WHEN status='budget_limited' THEN 'paused' ELSE status END,
                    '$.status_reason',CASE WHEN status='budget_limited' THEN 'legacy_budget_removed'
                        ELSE json_extract(goal_json,'$.status_reason') END);
            UPDATE goal_continuations SET status='obsolete'
                WHERE status='pending' AND goal_id IN (SELECT id FROM session_goals WHERE status!='active');
            PRAGMA user_version=52;
            COMMIT;""")
    except sqlite3.Error:
        connection.rollback()
        raise

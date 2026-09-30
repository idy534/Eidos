"""v17 state schema. Indexes are disposable; evidence and actions are not."""

import sqlite3


MEMORY_SCHEMA_SQL = """
CREATE TABLE memory_scopes (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('global','project')),
    project_id TEXT UNIQUE, settings_json TEXT NOT NULL,
    privacy_epoch INTEGER NOT NULL DEFAULT 0, generation INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE memory_sources (
    session_id TEXT PRIMARY KEY, revision INTEGER NOT NULL DEFAULT 1,
    processed_frontier INTEGER NOT NULL DEFAULT 0,
    temporary INTEGER NOT NULL DEFAULT 0, deleted INTEGER NOT NULL DEFAULT 0,
    enabled_after INTEGER NOT NULL DEFAULT 0, lineage_session_id TEXT
);
CREATE TABLE memory_source_items (
    item_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, run_id TEXT NOT NULL,
    item_revision INTEGER NOT NULL DEFAULT 1, sequence INTEGER NOT NULL,
    eligible INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX memory_source_items_session ON memory_source_items(session_id, sequence);
CREATE TABLE memory_entries (
    id TEXT PRIMARY KEY, scope_id TEXT NOT NULL REFERENCES memory_scopes(id),
    kind TEXT NOT NULL, status TEXT NOT NULL,
    current_revision INTEGER NOT NULL, pinned INTEGER NOT NULL DEFAULT 0,
    user_owned INTEGER NOT NULL DEFAULT 0, created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL, last_used_at INTEGER, use_count INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX memory_entries_scope ON memory_entries(scope_id,status,updated_at);
CREATE TABLE memory_revisions (
    entry_id TEXT NOT NULL REFERENCES memory_entries(id), revision INTEGER NOT NULL,
    file_ref TEXT, title TEXT NOT NULL, aliases_json TEXT NOT NULL,
    evidence_class TEXT NOT NULL, valid_from INTEGER, valid_to INTEGER,
    created_at INTEGER NOT NULL, PRIMARY KEY(entry_id,revision)
);
CREATE TABLE memory_evidence (
    entry_id TEXT NOT NULL, revision INTEGER NOT NULL, item_id TEXT NOT NULL,
    session_id TEXT NOT NULL, run_id TEXT NOT NULL, item_revision INTEGER NOT NULL,
    source_revision INTEGER NOT NULL, evidence_class TEXT NOT NULL,
    PRIMARY KEY(entry_id,revision,item_id),
    FOREIGN KEY(entry_id,revision) REFERENCES memory_revisions(entry_id,revision)
);
CREATE INDEX memory_evidence_source ON memory_evidence(session_id,item_id);
CREATE TABLE memory_actions (
    operation_id TEXT PRIMARY KEY, scope_id TEXT NOT NULL, entry_id TEXT,
    action TEXT NOT NULL, request_json TEXT NOT NULL, result_json TEXT NOT NULL, created_at INTEGER NOT NULL
);
CREATE TABLE memory_suppressions (
    scope_id TEXT NOT NULL, item_id TEXT NOT NULL, privacy_epoch INTEGER NOT NULL,
    PRIMARY KEY(scope_id,item_id)
);
CREATE TABLE memory_jobs (
    id TEXT PRIMARY KEY, scope_id TEXT NOT NULL, session_id TEXT NOT NULL,
    source_revision INTEGER NOT NULL, privacy_epoch INTEGER NOT NULL,
    base_generation INTEGER NOT NULL, kind TEXT NOT NULL, state TEXT NOT NULL,
    policy_version INTEGER NOT NULL DEFAULT 1, model_id TEXT NOT NULL, model_snapshot_json TEXT NOT NULL,
    frontier INTEGER NOT NULL, target_frontier INTEGER NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0, lease_token TEXT, lease_until INTEGER,
    not_before INTEGER NOT NULL, created_at INTEGER NOT NULL, error_code TEXT,
    tokens INTEGER NOT NULL DEFAULT 0, estimated_usage INTEGER NOT NULL DEFAULT 0,
    extraction_json TEXT, proposals_json TEXT,
    UNIQUE(kind,session_id,source_revision,frontier,policy_version)
);
CREATE INDEX memory_jobs_queue ON memory_jobs(state,not_before,created_at);
CREATE TABLE memory_model_attempts (
    id TEXT PRIMARY KEY, job_id TEXT NOT NULL, model_id TEXT NOT NULL,
    phase TEXT NOT NULL, state TEXT NOT NULL, tokens INTEGER NOT NULL,
    estimated INTEGER NOT NULL, created_at INTEGER NOT NULL, error_code TEXT
);
CREATE INDEX memory_model_attempts_time ON memory_model_attempts(created_at);
CREATE TABLE memory_generations (
    scope_id TEXT NOT NULL, generation INTEGER NOT NULL, manifest_json TEXT NOT NULL,
    summary_ref TEXT NOT NULL, catalog_ref TEXT NOT NULL, created_at INTEGER NOT NULL,
    PRIMARY KEY(scope_id,generation)
);
CREATE TABLE memory_snapshot_refs (
    snapshot_id TEXT NOT NULL, scope_id TEXT NOT NULL, privacy_epoch INTEGER NOT NULL,
    PRIMARY KEY(snapshot_id,scope_id)
);
CREATE TABLE memory_usage (
    run_id TEXT NOT NULL, step_id TEXT NOT NULL, entry_id TEXT NOT NULL,
    revision INTEGER NOT NULL, kind TEXT NOT NULL, created_at INTEGER NOT NULL,
    PRIMARY KEY(run_id,step_id,entry_id,revision,kind)
);
CREATE VIRTUAL TABLE memory_fts_word USING fts5(entry_id UNINDEXED,scope_id UNINDEXED,title,aliases,body,tokenize='unicode61');
ALTER TABLE context_snapshots ADD COLUMN memory_revoked INTEGER NOT NULL DEFAULT 0;

INSERT INTO memory_sources(session_id) SELECT id FROM sessions;
INSERT INTO memory_source_items(item_id,session_id,run_id,sequence)
SELECT id,session_id,run_id,creation_seq FROM items;

CREATE TRIGGER memory_source_insert AFTER INSERT ON items BEGIN
    INSERT OR IGNORE INTO memory_sources(session_id) VALUES(NEW.session_id);
    UPDATE memory_sources SET revision=revision+1 WHERE session_id=NEW.session_id;
    INSERT INTO memory_source_items(item_id,session_id,run_id,sequence)
    VALUES(NEW.id,NEW.session_id,NEW.run_id,NEW.creation_seq);
END;
CREATE TRIGGER memory_source_change AFTER UPDATE OF content,status,incomplete ON items
WHEN OLD.content IS NOT NEW.content OR OLD.status IS NOT NEW.status OR OLD.incomplete IS NOT NEW.incomplete BEGIN
    UPDATE memory_sources SET revision=revision+1 WHERE session_id=NEW.session_id;
    UPDATE memory_source_items SET item_revision=item_revision+1 WHERE item_id=NEW.id;
    UPDATE memory_entries SET status='quarantined' WHERE user_owned=0
      AND id IN (SELECT entry_id FROM memory_evidence WHERE item_id=NEW.id);
    UPDATE memory_scopes SET privacy_epoch=privacy_epoch+1 WHERE id IN
      (SELECT e.scope_id FROM memory_entries e JOIN memory_evidence v ON v.entry_id=e.id WHERE v.item_id=NEW.id);
END;
CREATE TRIGGER memory_source_delete BEFORE DELETE ON items BEGIN
    UPDATE memory_sources SET revision=revision+1 WHERE session_id=OLD.session_id;
    UPDATE memory_source_items SET eligible=0,item_revision=item_revision+1 WHERE item_id=OLD.id;
    UPDATE memory_entries SET status='quarantined' WHERE user_owned=0
      AND id IN (SELECT entry_id FROM memory_evidence WHERE item_id=OLD.id);
    UPDATE memory_scopes SET privacy_epoch=privacy_epoch+1 WHERE id IN
      (SELECT e.scope_id FROM memory_entries e JOIN memory_evidence v ON v.entry_id=e.id WHERE v.item_id=OLD.id);
END;
CREATE TRIGGER memory_scope_revoke AFTER UPDATE OF privacy_epoch ON memory_scopes
WHEN NEW.privacy_epoch <> OLD.privacy_epoch BEGIN
    UPDATE context_snapshots SET memory_revoked=1 WHERE id IN
      (SELECT snapshot_id FROM memory_snapshot_refs WHERE scope_id=NEW.id AND privacy_epoch<>NEW.privacy_epoch);
    DELETE FROM memory_fts_word WHERE scope_id=NEW.id AND entry_id IN
      (SELECT id FROM memory_entries WHERE status IN ('forgotten','quarantined'));
    UPDATE memory_jobs SET state='superseded',lease_token=NULL WHERE scope_id=NEW.id
      AND state IN ('queued','running','retry_wait','paused_budget','blocked_model');
END;
CREATE TRIGGER memory_scope_changed AFTER UPDATE OF generation,privacy_epoch,settings_json ON memory_scopes
WHEN NEW.generation<>OLD.generation OR NEW.privacy_epoch<>OLD.privacy_epoch OR NEW.settings_json<>OLD.settings_json BEGIN
    INSERT INTO events(event_contract_version,event_type,occurred_at,payload_json)
    VALUES(1,'memory.changed',CAST(strftime('%s','now') AS INTEGER)*1000,
      json_object('scopeId',NEW.id,'generation',NEW.generation,'privacyEpoch',NEW.privacy_epoch,'reason','memory_state_changed'));
    INSERT INTO event_outbox(event_id,status) VALUES(last_insert_rowid(),'pending');
END;
"""


def migrate_memory(connection: sqlite3.Connection) -> None:
    try:
        connection.executescript("BEGIN IMMEDIATE;\n" + MEMORY_SCHEMA_SQL)
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise sqlite3.IntegrityError("memory migration foreign key violation")
        connection.execute("PRAGMA user_version=17")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise

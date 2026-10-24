"""Document memory baseline. The v40 transition deliberately discards old memories."""

from __future__ import annotations

import sqlite3

DOCUMENT_MEMORY_SQL = """
CREATE TABLE memory_scopes (
 id TEXT PRIMARY KEY,kind TEXT NOT NULL CHECK(kind IN ('global','project')),
 project_id TEXT UNIQUE,mode TEXT NOT NULL CHECK(mode IN ('off','read','auto')),
 enabled_after INTEGER NOT NULL,privacy_epoch INTEGER NOT NULL DEFAULT 0,generation INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE memory_sources (
 session_id TEXT PRIMARY KEY,revision INTEGER NOT NULL DEFAULT 1,use_epoch INTEGER NOT NULL DEFAULT 0,
 temporary INTEGER NOT NULL DEFAULT 0,deleted INTEGER NOT NULL DEFAULT 0,lineage_session_id TEXT
);
CREATE TABLE memory_source_items (
 item_id TEXT PRIMARY KEY,session_id TEXT NOT NULL,run_id TEXT NOT NULL,
 item_revision INTEGER NOT NULL DEFAULT 1,sequence INTEGER NOT NULL,eligible INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX memory_source_items_session ON memory_source_items(session_id,sequence);
CREATE TABLE memory_documents (
 id TEXT PRIMARY KEY,scope_id TEXT NOT NULL REFERENCES memory_scopes(id),path TEXT NOT NULL,
 current_revision INTEGER NOT NULL,status TEXT NOT NULL CHECK(status IN ('active','quarantined','forgotten')),
 user_owned INTEGER NOT NULL DEFAULT 0,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL
);
CREATE UNIQUE INDEX memory_document_path ON memory_documents(scope_id,path) WHERE status='active';
CREATE TABLE memory_revisions (
 document_id TEXT NOT NULL REFERENCES memory_documents(id),revision INTEGER NOT NULL,
 file_ref TEXT,file_size INTEGER,file_mtime_ns INTEGER,title TEXT NOT NULL,summary TEXT NOT NULL,
 kind TEXT NOT NULL,user_owned INTEGER NOT NULL,created_at INTEGER NOT NULL,
 PRIMARY KEY(document_id,revision)
);
CREATE TABLE memory_evidence (
 document_id TEXT NOT NULL,revision INTEGER NOT NULL,item_id TEXT NOT NULL,session_id TEXT NOT NULL,
 run_id TEXT NOT NULL,item_revision INTEGER NOT NULL,source_revision INTEGER NOT NULL,evidence_class TEXT NOT NULL,
 PRIMARY KEY(document_id,revision,item_id),
 FOREIGN KEY(document_id,revision) REFERENCES memory_revisions(document_id,revision)
);
CREATE INDEX memory_evidence_source ON memory_evidence(session_id,item_id);
CREATE TABLE memory_actions (
 operation_id TEXT PRIMARY KEY,scope_id TEXT NOT NULL,document_id TEXT NOT NULL,
 request_hash TEXT NOT NULL,result_json TEXT NOT NULL,created_at INTEGER NOT NULL
);
CREATE TABLE memory_suppressions (
 scope_id TEXT NOT NULL,item_id TEXT NOT NULL,privacy_epoch INTEGER NOT NULL,PRIMARY KEY(scope_id,item_id)
);
CREATE TABLE memory_indexes (
 scope_id TEXT PRIMARY KEY REFERENCES memory_scopes(id),generation INTEGER NOT NULL,
 privacy_epoch INTEGER NOT NULL,file_ref TEXT NOT NULL
);
CREATE TABLE memory_snapshot_refs (
 snapshot_id TEXT NOT NULL,scope_id TEXT NOT NULL,privacy_epoch INTEGER NOT NULL,PRIMARY KEY(snapshot_id,scope_id)
);
CREATE TABLE memory_tool_reads (
 item_id TEXT PRIMARY KEY,run_id TEXT NOT NULL,epochs_json TEXT NOT NULL,revoked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE memory_metadata (key TEXT PRIMARY KEY,value TEXT NOT NULL);
INSERT INTO memory_metadata VALUES('legacy_cleanup_pending','1');
INSERT INTO memory_metadata VALUES('history_cleanup_cursor','0');
INSERT INTO memory_metadata VALUES('created_after',CAST(CAST(strftime('%s','now') AS INTEGER)*1000 AS TEXT));
CREATE VIRTUAL TABLE memory_fts_word USING fts5(document_id UNINDEXED,scope_id UNINDEXED,path,title,summary,body,tokenize='unicode61');
INSERT INTO memory_sources(session_id) SELECT id FROM sessions;
INSERT INTO memory_source_items(item_id,session_id,run_id,sequence)
 SELECT id,session_id,run_id,creation_seq FROM items;
CREATE TRIGGER memory_source_insert AFTER INSERT ON items BEGIN
 INSERT OR IGNORE INTO memory_sources(session_id) VALUES(NEW.session_id);
 UPDATE memory_sources SET revision=revision+1 WHERE session_id=NEW.session_id;
 INSERT INTO memory_source_items(item_id,session_id,run_id,sequence) VALUES(NEW.id,NEW.session_id,NEW.run_id,NEW.creation_seq);
END;
CREATE TRIGGER memory_source_change AFTER UPDATE OF content,status,incomplete ON items
 WHEN OLD.content IS NOT NEW.content OR OLD.status IS NOT NEW.status OR OLD.incomplete IS NOT NEW.incomplete BEGIN
 UPDATE memory_sources SET revision=revision+1 WHERE session_id=NEW.session_id;
 UPDATE memory_source_items SET item_revision=item_revision+1 WHERE item_id=NEW.id;
 UPDATE memory_documents SET status='quarantined' WHERE user_owned=0 AND status='active'
  AND id IN (SELECT document_id FROM memory_evidence WHERE item_id=NEW.id);
 UPDATE memory_scopes SET privacy_epoch=privacy_epoch+1,generation=generation+1 WHERE id IN
  (SELECT d.scope_id FROM memory_documents d JOIN memory_evidence e ON e.document_id=d.id WHERE e.item_id=NEW.id AND d.user_owned=0);
END;
CREATE TRIGGER memory_source_delete BEFORE DELETE ON items BEGIN
 UPDATE memory_sources SET revision=revision+1 WHERE session_id=OLD.session_id;
 UPDATE memory_source_items SET eligible=0,item_revision=item_revision+1 WHERE item_id=OLD.id;
 UPDATE memory_documents SET status='quarantined' WHERE user_owned=0 AND status='active'
  AND id IN (SELECT document_id FROM memory_evidence WHERE item_id=OLD.id);
 UPDATE memory_scopes SET privacy_epoch=privacy_epoch+1,generation=generation+1 WHERE id IN
  (SELECT d.scope_id FROM memory_documents d JOIN memory_evidence e ON e.document_id=d.id WHERE e.item_id=OLD.id AND d.user_owned=0);
END;
CREATE TRIGGER memory_scope_revoke AFTER UPDATE OF privacy_epoch ON memory_scopes
 WHEN NEW.privacy_epoch<>OLD.privacy_epoch BEGIN
 UPDATE context_snapshots SET memory_revoked=1 WHERE id IN
  (SELECT snapshot_id FROM memory_snapshot_refs WHERE scope_id=NEW.id AND privacy_epoch<>NEW.privacy_epoch);
 UPDATE step_resolution_snapshots SET memory_revoked=1 WHERE id IN
  (SELECT s.resolution_snapshot_id FROM steps s JOIN model_attempts a ON a.step_id=s.id
   JOIN context_snapshots c ON c.model_attempt_id=a.id WHERE c.memory_revoked=1);
 DELETE FROM memory_fts_word WHERE document_id IN (SELECT id FROM memory_documents WHERE status<>'active');
 UPDATE memory_metadata SET value='0' WHERE key='history_cleanup_cursor';
END;
CREATE TRIGGER memory_session_use_revoke AFTER UPDATE OF use_epoch ON memory_sources
 WHEN NEW.use_epoch<>OLD.use_epoch BEGIN
 UPDATE context_snapshots SET memory_revoked=1 WHERE id IN
  (SELECT snapshot_id FROM memory_snapshot_refs WHERE scope_id='session:'||NEW.session_id AND privacy_epoch<>NEW.use_epoch);
 UPDATE step_resolution_snapshots SET memory_revoked=1 WHERE id IN
  (SELECT s.resolution_snapshot_id FROM steps s JOIN model_attempts a ON a.step_id=s.id
   JOIN context_snapshots c ON c.model_attempt_id=a.id WHERE c.memory_revoked=1);
 UPDATE memory_metadata SET value='0' WHERE key='history_cleanup_cursor';
END;
CREATE TRIGGER memory_scope_changed AFTER UPDATE OF generation,privacy_epoch,mode ON memory_scopes
 WHEN NEW.generation<>OLD.generation OR NEW.privacy_epoch<>OLD.privacy_epoch OR NEW.mode<>OLD.mode BEGIN
 INSERT INTO events(event_contract_version,event_type,occurred_at,payload_json)
 VALUES(1,'memory.changed',CAST(strftime('%s','now') AS INTEGER)*1000,
  json_object('scopeId',NEW.id,'generation',NEW.generation,'privacyEpoch',NEW.privacy_epoch,'reason','memory_state_changed'));
 INSERT INTO event_outbox(event_id,status) VALUES(last_insert_rowid(),'pending');
END;
CREATE TRIGGER automation_memory_insert AFTER INSERT ON memory_source_items
 WHEN EXISTS(SELECT 1 FROM automation_run_origins WHERE run_id=NEW.run_id)
 BEGIN UPDATE memory_source_items SET eligible=0 WHERE item_id=NEW.item_id; END;
CREATE TRIGGER automation_origin_memory AFTER INSERT ON automation_run_origins
 BEGIN UPDATE memory_source_items SET eligible=0 WHERE item_id IN (SELECT id FROM items WHERE run_id=NEW.run_id); END;
CREATE TRIGGER channel_memory_insert AFTER INSERT ON memory_source_items
 WHEN EXISTS(SELECT 1 FROM channel_run_origins WHERE run_id=NEW.run_id)
 BEGIN UPDATE memory_source_items SET eligible=0 WHERE item_id=NEW.item_id; END;
CREATE TRIGGER channel_origin_memory AFTER INSERT ON channel_run_origins
 BEGIN UPDATE memory_source_items SET eligible=0 WHERE item_id IN (SELECT id FROM items WHERE run_id=NEW.run_id); END;
UPDATE memory_source_items SET eligible=0 WHERE run_id IN (SELECT run_id FROM automation_run_origins UNION SELECT run_id FROM channel_run_origins);
"""

_OLD_TABLES = (
    "memory_fts_trigram",
    "memory_fts_word",
    "memory_evidence",
    "memory_revisions",
    "memory_entries",
    "memory_actions",
    "memory_suppressions",
    "memory_jobs",
    "memory_model_attempts",
    "memory_generations",
    "memory_snapshot_refs",
    "memory_usage",
    "memory_tool_budget",
    "memory_tool_reads",
    "memory_source_items",
    "memory_sources",
    "memory_scopes",
)

RESET_DOCUMENT_MEMORY_SQL = (
    """
CREATE TEMP TABLE memory_permission_reset AS SELECT id,kind,project_id,privacy_epoch+1 AS privacy_epoch,
 CASE WHEN json_valid(settings_json) AND json_extract(settings_json,'$.useEnabled')=1
 THEN CASE WHEN json_extract(settings_json,'$.generateEnabled')=1 THEN 'auto' ELSE 'read' END ELSE 'off' END AS mode
 FROM memory_scopes;
ALTER TABLE step_resolution_snapshots ADD COLUMN memory_revoked INTEGER NOT NULL DEFAULT 0;
UPDATE context_snapshots SET memory_revoked=1,snapshot_json='{}' WHERE id IN (SELECT snapshot_id FROM memory_snapshot_refs);
UPDATE step_resolution_snapshots SET memory_revoked=1,snapshot_json='{}' WHERE id IN
 (SELECT s.resolution_snapshot_id FROM steps s JOIN model_attempts a ON a.step_id=s.id
  JOIN context_snapshots c ON c.model_attempt_id=a.id WHERE c.memory_revoked=1);
UPDATE approvals SET request_json='{}',feedback=NULL WHERE item_id IN (SELECT item_id FROM tool_calls WHERE tool_name LIKE 'memory_%');
UPDATE tool_calls SET arguments_json='{}',raw_arguments_json=NULL,result_json=NULL,model_result_json=NULL,
 ui_result_json=NULL,approval_diff=NULL,approval_feedback=NULL WHERE tool_name LIKE 'memory_%';
UPDATE hook_invocations SET input_json='{}',result_json=NULL,output_json=NULL,
 discarded_at=CAST(strftime('%s','now') AS INTEGER)*1000
 WHERE json_valid(input_json) AND json_extract(input_json,'$.tool_name') LIKE 'memory_%';
DELETE FROM hook_pending_events WHERE json_valid(fields_json) AND json_extract(fields_json,'$.tool_name') LIKE 'memory_%';
"""
    + "\n".join(
        f"DROP TRIGGER IF EXISTS {name};"
        for name in (
            "memory_source_insert",
            "memory_source_change",
            "memory_source_delete",
            "memory_scope_revoke",
            "memory_session_use_revoke",
            "memory_scope_changed",
            "automation_memory_insert",
            "automation_origin_memory",
            "channel_memory_insert",
            "channel_origin_memory",
        )
    )
    + "\n"
    + "\n".join(f"DROP TABLE IF EXISTS {name};" for name in _OLD_TABLES)
    + "\n"
    + DOCUMENT_MEMORY_SQL
    + """
INSERT INTO memory_scopes(id,kind,project_id,mode,enabled_after,privacy_epoch)
 SELECT id,kind,project_id,mode,CAST(strftime('%s','now') AS INTEGER)*1000,privacy_epoch FROM memory_permission_reset;
DROP TABLE memory_permission_reset;
INSERT INTO memory_tool_reads(item_id,run_id,epochs_json,revoked)
 SELECT t.item_id,i.run_id,'{}',1 FROM tool_calls t JOIN items i ON i.id=t.item_id WHERE t.tool_name LIKE 'memory_%';
"""
)


def reset_document_memory(connection: sqlite3.Connection) -> None:
    try:
        connection.executescript(
            "BEGIN IMMEDIATE;\n"
            + RESET_DOCUMENT_MEMORY_SQL
            + "\nPRAGMA user_version=40;\nCOMMIT;"
        )
    except BaseException:
        connection.rollback()
        raise

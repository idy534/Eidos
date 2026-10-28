from __future__ import annotations

import sqlite3


PROJECT_WORKSPACES_SCHEMA_SQL = """
CREATE TABLE projects_multi_root (
    id TEXT PRIMARY KEY,
    name TEXT,
    workspace_root TEXT NOT NULL,
    git_repository_root TEXT,
    git_common_dir TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    revision INTEGER NOT NULL DEFAULT 1 CHECK(revision >= 1),
    primary_root_id TEXT,
    CHECK ((git_repository_root IS NULL) = (git_common_dir IS NULL))
);
INSERT INTO projects_multi_root
    (id,name,workspace_root,git_repository_root,git_common_dir,created_at,updated_at)
SELECT id,name,workspace_root,git_repository_root,git_common_dir,created_at,updated_at
FROM projects;
DROP TABLE projects;
ALTER TABLE projects_multi_root RENAME TO projects;
CREATE INDEX projects_workspace ON projects(workspace_root);
CREATE TABLE project_roots (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    path TEXT NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    repository_root TEXT,
    git_dir TEXT,
    git_common_dir TEXT,
    UNIQUE(project_id,path), UNIQUE(project_id,position)
);
INSERT INTO project_roots
    (id,project_id,path,position,repository_root,git_common_dir)
SELECT 'root_'||id,id,workspace_root,0,git_repository_root,git_common_dir FROM projects;
UPDATE projects SET primary_root_id='root_'||id;
ALTER TABLE sessions ADD COLUMN project_id TEXT REFERENCES projects(id) ON DELETE RESTRICT;
ALTER TABLE sessions ADD COLUMN workspace_roots_json TEXT NOT NULL DEFAULT '[]';
UPDATE sessions SET project_id=COALESCE(
    (SELECT project_id FROM worktrees WHERE id=sessions.worktree_id),
    (SELECT id FROM projects WHERE workspace_root=sessions.workspace_root)
);
UPDATE sessions SET workspace_roots_json=json_array(json_object(
    'id','root_'||project_id,'path',workspace_root,
    'device',workspace_dev,'inode',workspace_inode,'owner',workspace_uid,
    'repository_root',(SELECT git_repository_root FROM projects WHERE id=sessions.project_id),
    'git_dir',NULL,'git_common_dir',(SELECT git_common_dir FROM projects WHERE id=sessions.project_id)
)) WHERE project_id IS NOT NULL AND workspace_dev IS NOT NULL;
CREATE INDEX sessions_project ON sessions(project_id);
ALTER TABLE review_comments ADD COLUMN root_id TEXT NOT NULL DEFAULT '';
CREATE INDEX review_comments_workspace ON review_comments(session_id,root_id,path,scope);
ALTER TABLE worktrees ADD COLUMN repository_root TEXT;
ALTER TABLE worktrees ADD COLUMN git_common_dir TEXT;
UPDATE worktrees SET
    repository_root=(SELECT git_repository_root FROM projects WHERE id=worktrees.project_id),
    git_common_dir=(SELECT git_common_dir FROM projects WHERE id=worktrees.project_id);
ALTER TABLE worktree_snapshots ADD COLUMN repository_root TEXT;
ALTER TABLE worktree_snapshots ADD COLUMN git_common_dir TEXT;
UPDATE worktree_snapshots SET
    repository_root=COALESCE((SELECT repository_root FROM worktrees WHERE id=worktree_snapshots.worktree_id),(SELECT git_repository_root FROM projects WHERE id=worktree_snapshots.project_id)),
    git_common_dir=COALESCE((SELECT git_common_dir FROM worktrees WHERE id=worktree_snapshots.worktree_id),(SELECT git_common_dir FROM projects WHERE id=worktree_snapshots.project_id));
"""


def migrate_project_workspaces(connection: sqlite3.Connection) -> None:
    # Rebuild the unique single-root catalog without changing any durable IDs.
    connection.execute("PRAGMA foreign_keys=OFF")
    try:
        connection.executescript("BEGIN IMMEDIATE;\n" + PROJECT_WORKSPACES_SCHEMA_SQL)
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise sqlite3.IntegrityError("workspace migration foreign key mismatch")
        connection.execute("PRAGMA user_version=45")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.execute("PRAGMA foreign_keys=ON")


from __future__ import annotations

from pathlib import Path

from eidos_runtime.application.checkpoints import CheckpointApplication
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.protocol.methods import (
    CheckpointCreateRequestDto,
    CheckpointForkRequestDto,
    CheckpointListRequestDto,
    CheckpointRewindRequestDto,
)


def test_checkpoint_freezes_snapshot_lineage_and_records_rewind_fork(
    tmp_path: Path,
) -> None:
    data = tmp_path / "data"
    workspace = tmp_path / "workspace"
    fork_workspace = tmp_path / "fork-workspace"
    data.mkdir(mode=0o700)
    workspace.mkdir()
    fork_workspace.mkdir()
    store = SessionStore(data)
    store.initialize()
    try:
        session = store.create_session(str(workspace))
        run, _item = store.enqueue_run(str(session["id"]), "inspect repository")
        run_id = str(run["id"])
        identity = store.workspace_for_run(run_id)
        store.long_task_repository().initialize(
            run_id=run_id,
            workspace_path=str(identity.path),
            workspace_device=identity.device,
            workspace_inode=identity.inode,
            workspace_owner=identity.owner,
            rule_snapshot_id="rule-1",
            inventory_snapshot_id="inventory-1",
            index_snapshot_id="index-1",
            permission_snapshot_hash="permission-1",
        )
        application = CheckpointApplication(
            store, store.checkpoint_repository()
        )

        created = application.create(CheckpointCreateRequestDto(runId=run_id))
        listed = application.list(CheckpointListRequestDto(runId=run_id))
        rewound = application.rewind(
            CheckpointRewindRequestDto(checkpointId=created.checkpoint.id)
        )
        forked = application.fork(CheckpointForkRequestDto(
            checkpointId=created.checkpoint.id,
            workspaceRoot=str(fork_workspace),
        ))

        assert created.checkpoint.rule_snapshot_id == "rule-1"
        assert created.checkpoint.repository_snapshot_id == "index-1"
        assert listed.checkpoints == [created.checkpoint]
        assert rewound.run.id == run_id
        assert forked.parent_run_id == run_id
        assert forked.run.id != run_id
        rows = store.connection.execute(
            "SELECT action, target_run_id FROM checkpoint_actions ORDER BY created_at, id"
        ).fetchall()
        assert [(row["action"], row["target_run_id"]) for row in rows] == [
            ("rewind", run_id),
            ("fork", forked.run.id),
        ]
    finally:
        store.close()

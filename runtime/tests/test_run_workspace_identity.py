from __future__ import annotations

from pathlib import Path
import subprocess

import pytest

from eidos_runtime.db.database import WorkspaceIdentity
from eidos_runtime.db.errors import StorageError, WorkspaceIdentityChangedError
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.git import WorktreeManager
from eidos_runtime.domain.long_task import ResumeOutcome, SafePoint
from eidos_runtime.runtime.supervisor import RunSupervisor
from eidos_runtime.tools.runtime_workspace import ToolExecutor
from eidos_runtime.tools.workspace import WorkspacePathError


def _git_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(
        ["git", "init", "-q", "-b", "main"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "eidos-tests@example.com"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Eidos Tests"],
        cwd=repository,
        check=True,
    )
    (repository / "README.md").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repository, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "initial"],
        cwd=repository,
        check=True,
    )
    return repository


def test_workspace_for_run_reads_the_frozen_resolution_snapshot(
    tmp_path: Path,
) -> None:
    repository = _git_repository(tmp_path)
    store = SessionStore(tmp_path / "data")
    store.initialize()
    manager = WorktreeManager(
        store.database,
        managed_root=tmp_path / "managed-worktrees",
    )
    try:
        worktree = manager.create(repository)
        session = store.typed_runtime_repository().create_session(
            str(repository.resolve()),
            worktree_id=worktree.id,
        ).value
        run, _item = store.create_run(session.id, "inspect")
        frozen = store.read_run_resolution_snapshot(
            str(run["id"])
        ).workspace_identity

        worktree_root = Path(worktree.worktree_root)
        displaced_root = tmp_path / "displaced-worktree"
        worktree_root.rename(displaced_root)
        worktree_root.mkdir()
        replacement = worktree_root.stat()
        assert replacement.st_ino != frozen.inode

        identity = store.workspace_for_run(str(run["id"]))

        assert identity == WorkspaceIdentity(
            path=Path(frozen.path),
            device=frozen.device,
            inode=frozen.inode,
            owner=frozen.owner,
        )
        with pytest.raises(
            WorkspacePathError, match="^workspace_identity_changed$"
        ):
            with ToolExecutor(identity):
                pass
    finally:
        store.close()


def test_workspace_for_run_rejects_a_corrupt_resolution_snapshot(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    try:
        session = store.create_session(str(workspace))
        run, _item = store.create_run(str(session["id"]), "inspect")
        with store.connection:
            store.connection.execute(
                """
                UPDATE run_resolution_snapshots
                SET snapshot_json = '{}'
                WHERE run_id = ?
                """,
                (run["id"],),
            )

        with pytest.raises(
            StorageError, match="^run_resolution_snapshot_invalid$"
        ):
            store.workspace_for_run(str(run["id"]))
    finally:
        store.close()


def test_resume_observes_current_path_against_the_frozen_run_identity(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    try:
        session = store.create_session(str(workspace))
        run, _item = store.create_run(str(session["id"]), "inspect")
        run_id = str(run["id"])
        frozen = store.workspace_for_run(run_id)
        tasks = store.long_task_repository()
        tasks.initialize(
            run_id=run_id,
            workspace_path=str(frozen.path),
            workspace_device=frozen.device,
            workspace_inode=frozen.inode,
            workspace_owner=frozen.owner,
        )
        tasks.request_pause(run_id)
        tasks.mark_paused(run_id, SafePoint.BEFORE_MODEL)
        workspace.rename(tmp_path / "displaced-workspace")
        workspace.mkdir()
        supervisor = RunSupervisor(
            store,
            lambda _run_id: None,  # type: ignore[arg-type,return-value]
            lambda _event: None,
            lambda value: value,
            lambda: True,
            lambda: True,
            lambda: None,
        )

        resumed = supervisor.resume_run(run_id)

        assert resumed.last_verification is not None
        assert resumed.last_verification.outcome is ResumeOutcome.WORKSPACE_CHANGED
        assert "workspace_identity_changed" in resumed.last_verification.reasons
    finally:
        store.close()


@pytest.mark.parametrize("changed_field", ["path", "device", "inode", "owner"])
def test_enqueue_run_compares_expected_workspace_identity_before_inserting(
    tmp_path: Path,
    changed_field: str,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    metadata = workspace.resolve().stat()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    try:
        session = store.create_session(str(workspace))
        expected_values: dict[str, object] = {
            "path": workspace.resolve(),
            "device": metadata.st_dev,
            "inode": metadata.st_ino,
            "owner": metadata.st_uid,
        }
        if changed_field == "path":
            expected_values[changed_field] = tmp_path / "different-workspace"
        else:
            expected_values[changed_field] = int(expected_values[changed_field]) + 1
        expected = WorkspaceIdentity(**expected_values)  # type: ignore[arg-type]

        with pytest.raises(
            WorkspaceIdentityChangedError, match="^workspace_identity_changed$"
        ):
            store.enqueue_run(
                str(session["id"]),
                "inspect",
                operation_id="workspace-compare",
                session_title="must not persist",
                expected_workspace_identity=expected,
            )

        assert store.connection.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0
        assert store.connection.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0
        assert store.connection.execute(
            "SELECT COUNT(*) FROM run_resolution_snapshots"
        ).fetchone()[0] == 0
        row = store.connection.execute(
            "SELECT title FROM sessions WHERE id = ?", (session["id"],)
        ).fetchone()
        assert row is not None
        assert row["title"] is None
        assert store.connection.execute(
            "SELECT COUNT(*) FROM operations WHERE id = 'workspace-compare'"
        ).fetchone()[0] == 0
    finally:
        store.close()

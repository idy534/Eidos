"""Directories recorded before a restart that renumbered their volume.

macOS assigns a volume's st_dev when it mounts, so a Session, Worktree or Run
recorded in an earlier boot can carry a device number that no longer matches
the same directory. Tests fake that by shifting the recorded number.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from eidos_runtime.application.sessions import SessionApplication
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.domain.project_workspace import DirectoryIdentity
from eidos_runtime.git import WorktreeManager
from eidos_runtime.persistence.project_workspaces import verify_session_roots
from eidos_runtime.protocol.methods import SessionCreateRequestDto
from eidos_runtime.runtime.long_task import ResumeVerifier
from eidos_runtime.runtime.resolution import RunResolutionSnapshot, canonical_sha256
from eidos_runtime.workspace import recorded_identity
from eidos_runtime.workspace.reader import WorkspacePathError, WorkspaceReader
from eidos_runtime.workspace.recorded_identity import (
    current_device,
    current_directory_identity,
    same_recorded_directory,
)

from test_multi_workspace_execution import _repository
from test_project_workspaces import _request, _server


@pytest.fixture
def renumbering_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(recorded_identity, "_DEVICE_NUMBERS_CHANGE_ON_REMOUNT", True)


@pytest.fixture
def stable_device_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(recorded_identity, "_DEVICE_NUMBERS_CHANGE_ON_REMOUNT", False)


def _identity(path: Path) -> tuple[int, int, int]:
    metadata = os.stat(path, follow_symlinks=False)
    return metadata.st_dev, metadata.st_ino, metadata.st_uid


def _earlier_boot(identity: tuple[int, int, int]) -> tuple[int, int, int]:
    return identity[0] + 1, identity[1], identity[2]


@pytest.mark.usefixtures("renumbering_platform")
def test_renumbered_volume_keeps_inode_and_owner_checks(tmp_path: Path) -> None:
    directory = tmp_path / "workspace"
    directory.mkdir()
    observed = _identity(directory)
    recorded = _earlier_boot(observed)

    assert same_recorded_directory(recorded, observed)
    assert current_device(directory, recorded) == observed[0]
    assert not same_recorded_directory(recorded, (observed[0], observed[1] + 1, observed[2]))
    assert not same_recorded_directory(recorded, (observed[0], observed[1], observed[2] + 1))

    replaced_inode = (recorded[0], recorded[1] + 1, recorded[2])
    assert current_device(directory, replaced_inode) == replaced_inode[0]
    assert current_device(tmp_path / "missing", recorded) == recorded[0]
    assert current_device(f"{directory}\0", recorded) == recorded[0]

    alias = tmp_path / "alias"
    alias.symlink_to(directory, target_is_directory=True)
    assert current_device(alias, recorded) == recorded[0]

    git_identity = DirectoryIdentity(device=recorded[0], inode=recorded[1], owner=recorded[2])
    assert current_directory_identity(directory, git_identity) == DirectoryIdentity(
        device=observed[0], inode=observed[1], owner=observed[2]
    )
    assert current_directory_identity(None, git_identity) is git_identity


@pytest.mark.usefixtures("stable_device_platform")
def test_stable_device_platforms_still_require_the_recorded_device(tmp_path: Path) -> None:
    directory = tmp_path / "workspace"
    directory.mkdir()
    observed = _identity(directory)
    recorded = _earlier_boot(observed)

    assert not same_recorded_directory(recorded, observed)
    assert current_device(directory, recorded) == recorded[0]


def _shift_session_identity(
    server, session_id: str, *, device: int = 1, inode: int = 0
) -> None:
    connection = server.store.connection
    row = connection.execute(
        "SELECT workspace_dev, workspace_inode, workspace_roots_json FROM sessions WHERE id=?",
        (session_id,),
    ).fetchone()
    roots = json.loads(row["workspace_roots_json"])
    for root in roots:
        root["device"] += device
        root["inode"] += inode
        for key in ("git_dir_identity", "git_common_identity"):
            if root[key] is not None:
                root[key]["device"] += device
    with connection:
        connection.execute(
            "UPDATE sessions SET workspace_dev=?, workspace_inode=?, workspace_roots_json=? WHERE id=?",
            (row["workspace_dev"] + device, row["workspace_inode"] + inode, json.dumps(roots), session_id),
        )


def _project_session(server, output, primary: Path, secondary: Path) -> str:
    project = _request(
        server,
        output,
        str(uuid4()),
        "project/createFolders",
        {
            "operationId": str(uuid4()),
            "name": "Renumbered volume",
            "roots": [
                {"id": "primary", "path": str(primary)},
                {"id": "secondary", "path": str(secondary)},
            ],
            "primaryRootId": "primary",
        },
    )
    assert "result" in project, project
    created = _request(
        server,
        output,
        str(uuid4()),
        "session/create",
        {"projectId": project["result"]["id"], "workspaceRoot": str(primary)},
    )
    assert "result" in created, created
    return created["result"]["id"]


def _session_reads(server, output, session_id: str, secondary: Path) -> list[dict[str, object]]:
    return [
        _request(server, output, str(uuid4()), "workspace/listDirectory", {
            "sessionId": session_id, "path": ".",
        }),
        _request(server, output, str(uuid4()), "workspace/listDirectory", {
            "sessionId": session_id, "workspaceRoot": str(secondary), "path": ".",
        }),
        _request(server, output, str(uuid4()), "session/gitStatus", {
            "sessionId": session_id, "rootId": "secondary",
        }),
    ]


@pytest.mark.integration
@pytest.mark.usefixtures("renumbering_platform")
def test_session_recorded_before_restart_still_opens_its_folders(tmp_path: Path) -> None:
    primary = _repository(tmp_path / "primary", "primary\n")
    secondary = _repository(tmp_path / "secondary", "secondary\n")
    (secondary / "repository-marker.txt").write_text("changed\n", encoding="utf-8")
    server, output = _server(tmp_path)
    try:
        session_id = _project_session(server, output, primary, secondary)
        _shift_session_identity(server, session_id)

        listing, secondary_listing, status = _session_reads(server, output, session_id, secondary)

        assert "result" in listing, listing
        assert "repository-marker.txt" in {entry["name"] for entry in listing["result"]["entries"]}
        assert "result" in secondary_listing, secondary_listing
        assert "result" in status, status
        assert status["result"]["unstagedFiles"] == ["repository-marker.txt"]

        workspace = server.store.workspace_for_session(session_id)
        assert (workspace.device, workspace.inode) == _identity(primary)[:2]
        assert workspace.git_common_identity is not None
        assert workspace.git_common_identity.device == _identity(primary / ".git")[0]
        for root in (workspace, *workspace.additional_roots):
            with WorkspaceReader(root):
                pass
    finally:
        server.close()


@pytest.mark.integration
@pytest.mark.parametrize(
    ("platform", "inode"),
    [("stable_device_platform", 0), ("renumbering_platform", 1)],
)
def test_changed_session_folder_identity_is_still_rejected(
    tmp_path: Path, request: pytest.FixtureRequest, platform: str, inode: int,
) -> None:
    request.getfixturevalue(platform)
    primary = _repository(tmp_path / "primary", "primary\n")
    secondary = _repository(tmp_path / "secondary", "secondary\n")
    server, output = _server(tmp_path)
    try:
        session_id = _project_session(server, output, primary, secondary)
        _shift_session_identity(server, session_id, inode=inode)

        for response in _session_reads(server, output, session_id, secondary):
            assert response["error"]["data"]["code"] == "WORKSPACE_IDENTITY_CHANGED", response
    finally:
        server.close()


def _shift_snapshot(identity: dict[str, object]) -> None:
    identity["device"] += 1
    for key in ("git_dir_identity", "git_common_identity"):
        if identity.get(key) is not None:
            identity[key]["device"] += 1
    for root in identity.get("additional_roots") or ():
        _shift_snapshot(root)


@pytest.mark.integration
@pytest.mark.usefixtures("renumbering_platform")
def test_run_snapshot_recorded_before_restart_reopens_the_same_folders(tmp_path: Path) -> None:
    primary = _repository(tmp_path / "primary", "primary\n")
    secondary = _repository(tmp_path / "secondary", "secondary\n")
    server, output = _server(tmp_path)
    try:
        session_id = _project_session(server, output, primary, secondary)
        run, _item = server.store.create_run(session_id, "inspect")
        connection = server.store.connection
        row = connection.execute(
            "SELECT snapshot_json FROM run_resolution_snapshots WHERE run_id=?", (run["id"],)
        ).fetchone()
        snapshot = RunResolutionSnapshot.model_validate_json(row["snapshot_json"]).model_dump(
            mode="json", exclude={"id", "snapshot_hash"}, exclude_none=True,
        )
        _shift_snapshot(snapshot["workspace_identity"])
        digest = canonical_sha256(snapshot)
        snapshot = {"id": f"run_{digest}", "snapshot_hash": digest, **snapshot}
        with connection:
            connection.execute(
                "UPDATE run_resolution_snapshots SET snapshot_json=? WHERE run_id=?",
                (json.dumps(snapshot), run["id"]),
            )

        workspace = server.store.workspace_for_run(run["id"])

        assert [root.device for root in (workspace, *workspace.additional_roots)] == [
            _identity(primary)[0], _identity(secondary)[0],
        ]
        assert workspace.git_dir_identity is not None
        assert workspace.git_dir_identity.device == _identity(primary / ".git")[0]
        for root in (workspace, *workspace.additional_roots):
            with WorkspaceReader(root):
                pass
    finally:
        server.close()


@pytest.mark.integration
@pytest.mark.parametrize(("platform", "accepted"), [
    ("renumbering_platform", True), ("stable_device_platform", False),
])
def test_copied_session_roots_from_before_restart(
    tmp_path: Path, request: pytest.FixtureRequest, platform: str, accepted: bool,
) -> None:
    request.getfixturevalue(platform)
    primary = _repository(tmp_path / "primary", "primary\n")
    secondary = _repository(tmp_path / "secondary", "secondary\n")
    server, output = _server(tmp_path)
    try:
        session_id = _project_session(server, output, primary, secondary)
        _shift_session_identity(server, session_id)
        roots = server.store.typed_runtime_repository().read_session(session_id).roots

        if accepted:
            verify_session_roots(roots)
        else:
            with pytest.raises(WorkspacePathError):
                verify_session_roots(roots)
    finally:
        server.close()


def _managed_worktree(tmp_path: Path):
    repository = _repository(tmp_path / "repository", "base\n")
    store = SessionStore(tmp_path / "data")
    store.initialize()
    manager = WorktreeManager(store.database, managed_root=tmp_path / "managed")
    sessions = SessionApplication(store, scan_text=lambda value: value, worktree_manager=manager)
    created = sessions.create(
        SessionCreateRequestDto(workspaceRoot=str(repository), executionMode="worktree")
    ).root
    worktree_id = created["worktree"]["worktreeId"]
    with store.connection:
        store.connection.execute(
            "UPDATE worktrees SET git_dir_dev = git_dir_dev + 1 WHERE id=?", (worktree_id,)
        )
    return store, manager, created["id"], worktree_id


@pytest.mark.integration
@pytest.mark.usefixtures("renumbering_platform")
def test_worktree_recorded_before_restart_stays_valid(tmp_path: Path) -> None:
    store, manager, session_id, worktree_id = _managed_worktree(tmp_path)
    try:
        identity = manager.execution_identity(worktree_id)

        assert identity.git_dir is not None and identity.git_dir_identity is not None
        assert identity.git_dir_identity.device == _identity(identity.git_dir)[0]
        assert store.workspace_for_session(session_id).git_dir_identity == identity.git_dir_identity
        assert manager.validate(worktree_id).valid
    finally:
        store.close()


@pytest.mark.integration
@pytest.mark.usefixtures("stable_device_platform")
def test_worktree_device_change_is_rejected_where_devices_are_stable(tmp_path: Path) -> None:
    store, manager, _, worktree_id = _managed_worktree(tmp_path)
    try:
        validation = manager.validate(worktree_id)

        assert not validation.valid
        assert validation.code == "workspace_identity_changed"
    finally:
        store.close()


@pytest.mark.parametrize(
    ("platform", "current", "changed"),
    [
        ("renumbering_platform", ("/w", 2, 7, 501), False),
        ("stable_device_platform", ("/w", 2, 7, 501), True),
        ("renumbering_platform", ("/w", 2, 8, 501), True),
        ("renumbering_platform", ("/other", 1, 7, 501), True),
        ("renumbering_platform", ("/w", -1, -1, -1), True),
    ],
)
def test_resume_treats_only_volume_renumbering_as_the_same_workspace(
    request: pytest.FixtureRequest,
    platform: str,
    current: tuple[str, int, int, int],
    changed: bool,
) -> None:
    request.getfixturevalue(platform)
    verification = ResumeVerifier.verify(
        run_id="run",
        expected_workspace=("/w", 1, 7, 501),
        current_workspace=current,
        expected_git_head=None,
        current_git_head=None,
        expected_rule_snapshot_id=None,
        current_rule_snapshot_id=None,
        expected_index_snapshot_id=None,
        current_index_snapshot_id=None,
        side_effects_may_exist=False,
    )

    assert ("workspace_identity_changed" in verification.reasons) is changed

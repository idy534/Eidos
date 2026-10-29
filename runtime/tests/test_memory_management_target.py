from __future__ import annotations

import hashlib
import uuid

import pytest

from eidos_runtime.db.storage import SessionStore
from eidos_runtime.memory.contracts import (
    MemoryEditRequest,
    MemoryExportRequest,
    MemoryListRequest,
    MemoryPolicyRequest,
    MemoryReadRequest,
)
from eidos_runtime.memory.repository import MemoryRejected
from eidos_runtime.persistence.worktrees import ProjectWorktreeRepository

pytestmark = pytest.mark.integration


@pytest.fixture
def setup(tmp_path):
    store = SessionStore(tmp_path / "data")
    store.initialize()
    repository = ProjectWorktreeRepository(store.database)
    projects = []
    for name in ("first", "second"):
        workspace = tmp_path / name
        workspace.mkdir()
        projects.append(repository.get_or_create_project(workspace))
    yield store, repository, projects
    store.close()


def edit(project_id, **changes):
    return MemoryEditRequest.model_validate({
        "project_id": project_id,
        "scope": "current",
        "path": "preferences.md",
        "operation_id": str(uuid.uuid4()),
        "content": "用户使用中文。",
        **changes,
    })


def test_project_management_without_sessions_survives_restart(setup):
    store, _, (first, second) = setup
    service = store.database.memory
    assert store.database.connection().execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
    saved = service.edit(edit(first.id))
    state = service.list(MemoryListRequest(project_id=first.id, scope="current"))
    assert [note.id for note in state.documents] == [saved.document_id]
    assert not service.list(MemoryListRequest(project_id=second.id, scope="current")).documents
    service.policy(MemoryPolicyRequest(project_id=first.id, scope="current", mode="read"))
    assert service.list(MemoryListRequest(project_id=first.id)).scopes[-1].mode == "read"
    assert service.list(MemoryListRequest(project_id=second.id)).scopes[-1].mode == "auto"
    exported = service.export(MemoryExportRequest(project_id=first.id))
    assert [file.path for file in exported.files] == ["MEMORY.md", "preferences.md"]
    assert "用户使用中文。" in exported.files[-1].content
    store.close()
    reopened = SessionStore(store.database.data_directory)
    reopened.initialize()
    try:
        service = reopened.database.memory
        read = service.read(MemoryReadRequest(project_id=first.id, path="preferences.md"))
        assert read.content == "用户使用中文。"
        assert read.revision == 1
        assert service.list(MemoryListRequest(project_id=first.id)).scopes[-1].mode == "read"
    finally:
        reopened.close()


def test_management_uses_existing_session_scope_and_cas(setup):
    store, _, (first, _) = setup
    session = store.create_session(str(first.workspace_root))
    service = store.database.memory
    initial = edit(first.id)
    saved = service.edit(initial)
    assert service.read(MemoryReadRequest(session_id=session["id"], path=initial.path)).scope_id == service.read(
        MemoryReadRequest(project_id=first.id, path=initial.path)
    ).scope_id
    service.edit(edit(first.id, expected_revision=saved.revision, content="用户使用中文和短句。"))
    with pytest.raises(MemoryRejected, match="revision_conflict"):
        service.edit(edit(first.id, expected_revision=saved.revision))
    archived = service.edit(edit(first.id, expected_revision=2, action="delete", content=None), archive=True)
    assert not service.list(MemoryListRequest(project_id=first.id, scope="current")).documents
    assert service.list(MemoryListRequest(project_id=first.id, scope="current", include_archived=True)).documents[0].archived
    service.edit(edit(first.id, expected_revision=archived.revision))
    service.edit(edit(first.id, expected_revision=4, action="delete", content=None))
    with pytest.raises(MemoryRejected, match="not_found"):
        service.read(MemoryReadRequest(project_id=first.id, path=initial.path))


def test_global_memory_is_shared_and_project_modes_are_independent(setup):
    store, _, (first, second) = setup
    service = store.database.memory
    service.edit(MemoryEditRequest(project_id=first.id, scope="global", path="shared.md", operation_id="shared", content="用户偏好短句。"))
    for project_id in (first.id, second.id, None):
        assert service.read(MemoryReadRequest(project_id=project_id, scope="global", path="shared.md")).content == "用户偏好短句。"
    service.policy(MemoryPolicyRequest(project_id=first.id, scope="global", mode="off"))
    state = service.list(MemoryListRequest(project_id=second.id))
    assert state.scopes[0].mode == "off"
    assert state.scopes[-1].mode == "auto"


def test_invalid_and_ambiguous_targets_fail_without_falling_back(setup):
    store, repository, (first, _) = setup
    service = store.database.memory
    repository.delete_project(first.id)
    for request, handler in (
        (MemoryListRequest(project_id=first.id), service.list),
        (MemoryReadRequest(project_id=first.id), service.read),
        (MemoryPolicyRequest(project_id=first.id, mode="off"), service.policy),
        (MemoryExportRequest(project_id=first.id), service.export),
        (edit(first.id), service.edit),
    ):
        with pytest.raises(MemoryRejected, match="memory_project_not_found"):
            handler(request)
    with pytest.raises(MemoryRejected, match="memory_target_ambiguous"):
        service.list(MemoryListRequest(project_id="missing", session_id="missing"))
    assert service.list(MemoryListRequest()).scopes[0].mode == "auto"


def test_model_use_cannot_supply_a_management_project(setup):
    store, _, (first, _) = setup
    service = store.database.memory
    with pytest.raises(MemoryRejected, match="memory_model_target_forbidden"):
        service.list(MemoryListRequest(project_id=first.id), for_use=True)
    with pytest.raises(MemoryRejected, match="memory_model_target_forbidden"):
        service.read(MemoryReadRequest(project_id=first.id), for_use=True)
    with pytest.raises(MemoryRejected, match="memory_model_target_forbidden"):
        service.edit(edit(first.id), automatic=True)


def test_legacy_operation_replay_keeps_its_original_fingerprint(setup):
    store, _, _ = setup
    service = store.database.memory
    request = MemoryEditRequest(scope="global", path="old.md", operation_id="legacy", content="旧笔记。")
    saved = service.edit(request)
    legacy_json = request.model_dump_json(exclude={"project_id"})
    with store.database.transaction() as connection:
        connection.execute("UPDATE memory_actions SET request_hash=? WHERE operation_id=?", (hashlib.sha256(legacy_json.encode()).hexdigest(), "legacy"))
    assert service.edit(request) == saved

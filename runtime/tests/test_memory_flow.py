from __future__ import annotations

from contextlib import contextmanager
import json
from itertools import count
import sqlite3
import threading

import pytest

from eidos_runtime.db.schema import SCHEMA_VERSION, V42_SCHEMA_SQL
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.domain.collaboration import SpawnAgent
from eidos_runtime.memory import flow_migration
from eidos_runtime.memory.contracts import (
    MemoryEditRequest,
    MemoryListRequest,
    MemoryPolicyRequest,
    MemoryReadRequest,
)
from eidos_runtime.memory.repository import MemoryRejected
from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel
from eidos_runtime.persistence.collaboration import CollaborationRepository
from eidos_runtime.runtime.engine import RuntimeEngine
from eidos_runtime.tools.memory import memory_entries

pytestmark = pytest.mark.integration


@pytest.fixture
def setup(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    session = store.create_session(str(workspace))
    yield store, session
    store.close()


CALL_IDS = count()


def call(name, **arguments):
    return ModelResponse(
        tool_calls=(ModelToolCall(f"memory-flow-{next(CALL_IDS)}", name, arguments),)
    )


def results(store, run):
    return [
        json.loads(row[0])
        for row in store.connection.execute(
            "SELECT t.model_result_json FROM tool_calls t JOIN items i ON i.id=t.item_id WHERE i.run_id=? ORDER BY t.creation_seq",
            (run["id"],),
        )
    ]


def seed(service, session, **changes):
    return service.edit(
        MemoryEditRequest(
            session_id=session["id"],
            operation_id="seed",
            path="profile.md",
            summary="用户背景",
            kind="user",
            content="用户使用中文。",
            **changes,
        )
    )


def test_automatic_update_of_user_note_needs_no_internal_arguments_or_approval(setup):
    store, session = setup
    service = store.database.memory
    original = seed(service, session)
    run, _ = store.create_run(
        session["id"], "我是一名 agent 后端开发", approval_mode="manual"
    )
    model = ScriptedModel(
        [
            call("memory_read", path="profile.md"),
            call(
                "memory_edit",
                path="profile.md",
                content="用户使用中文，是 agent 后端开发。",
            ),
            ModelResponse(text="已更新原笔记"),
        ]
    )
    RuntimeEngine(store, model, lambda _: None).run(run["id"], threading.Event())
    assert [result["outcome"] for result in results(store, run)] == [
        "success",
        "success",
    ]
    read = service.read(MemoryReadRequest(session_id=session["id"], path="profile.md"))
    assert read.revision == 2 and read.document.id == original.document_id
    assert read.document.kind == "user" and read.document.summary
    assert "后端开发" in read.content
    assert not store.connection.execute("SELECT 1 FROM approvals").fetchone()
    assert store.read_run(run["id"])["status"] == "succeeded"
    entry = next(
        value for value in memory_entries() if value.spec.name == "memory_edit"
    )
    assert (
        not {"sourceItemIds", "userRequested", "expectedRevision"}
        & entry.spec.input_schema["properties"].keys()
    )
    assert (
        store.connection.execute(
            "SELECT item_id FROM memory_evidence WHERE document_id=? AND revision=2",
            (original.document_id,),
        ).fetchone()[0]
        == store.get_user_item(run["id"])["id"]
    )


def test_project_session_can_save_global_preferences_when_project_is_read_only(setup):
    store, session = setup
    service = store.database.memory
    service.policy(
        MemoryPolicyRequest(session_id=session["id"], scope="current", mode="read")
    )
    run, _ = store.create_run(session["id"], "我更喜欢中文回复", approval_mode="manual")
    model = ScriptedModel(
        [
            call(
                "memory_edit",
                scope="global",
                path="language.md",
                content="用户更喜欢中文回复。",
            ),
            ModelResponse(text="完成"),
        ]
    )
    RuntimeEngine(store, model, lambda _: None).run(run["id"], threading.Event())
    assert results(store, run)[0]["code"] == "memory_saved"
    assert (
        service.read(MemoryReadRequest(scope="global", path="language.md")).content
        == "用户更喜欢中文回复。"
    )
    assert not service.list(
        MemoryListRequest(session_id=session["id"], scope="current")
    ).documents


def test_existing_note_requires_a_read_and_stale_read_conflicts_without_losing_work(
    setup,
):
    store, session = setup
    service = store.database.memory
    seed(service, session)
    run, _ = store.create_run(session["id"], "更正笔记")

    class ConcurrentEdit(ScriptedModel):
        changed = False

        def complete(self, context, cancel, on_text_delta, **kwargs):
            if self._index == 2 and not self.changed:
                self.changed = True
                service.edit(
                    MemoryEditRequest(
                        session_id=session["id"],
                        operation_id="other-editor",
                        path="profile.md",
                        expected_revision=1,
                        content="另一个窗口的新内容。",
                    )
                )
            return super().complete(context, cancel, on_text_delta, **kwargs)

    model = ConcurrentEdit(
        [
            call("memory_edit", path="profile.md", content="未读不能覆盖"),
            call("memory_read", path="profile.md"),
            call("memory_edit", path="profile.md", content="过期版本不能覆盖"),
            call("memory_read", path="profile.md"),
            call(
                "memory_edit",
                path="profile.md",
                content="另一个窗口的新内容，加上本次补充。",
            ),
            ModelResponse(text="完成"),
        ]
    )
    RuntimeEngine(store, model, lambda _: None).run(run["id"], threading.Event())
    values = results(store, run)
    assert values[0]["code"] == "memory_read_required"
    assert values[2]["code"] == "memory_revision_conflict"
    assert values[4]["code"] == "memory_saved"
    assert (
        service.read(
            MemoryReadRequest(session_id=session["id"], path="profile.md")
        ).revision
        == 3
    )
    assert store.read_run(run["id"])["status"] == "succeeded"


def test_archived_notes_survive_restart_can_be_found_and_restored(setup):
    store, session = setup
    service = store.database.memory
    seed(service, session)
    run, _ = store.create_run(session["id"], "整理重复笔记")
    model = ScriptedModel(
        [
            call("memory_read", path="profile.md"),
            call("memory_edit", path="profile.md", action="delete"),
            ModelResponse(text="已归档"),
        ]
    )
    RuntimeEngine(store, model, lambda _: None).run(run["id"], threading.Event())
    assert results(store, run)[1]["code"] == "memory_archived"
    assert not service.list(MemoryListRequest(session_id=session["id"])).documents
    assert (
        "profile.md"
        not in service.project(session["id"], 10000, run_id=run["id"]).rendered_payload
    )
    root = store.data_directory
    store.close()
    reopened = SessionStore(root)
    reopened.initialize()
    try:
        found = reopened.database.memory.list(
            MemoryListRequest(
                session_id=session["id"], include_archived=True, query="背景"
            )
        )
        assert len(found.documents) == 1 and found.documents[0].archived
        restored, _ = reopened.create_run(session["id"], "恢复归档笔记")
        replay = ScriptedModel(
            [
                call("memory_search", includeArchived=True),
                call("memory_read", path="profile.md"),
                call("memory_edit", path="profile.md", content="用户使用中文。"),
                ModelResponse(text="已恢复"),
            ]
        )
        RuntimeEngine(reopened, replay, lambda _: None).run(
            restored["id"], threading.Event()
        )
        values = results(reopened, restored)
        assert values[0]["data"]["documents"][0]["archived"] is True
        assert values[1]["data"]["archived"] is True
        assert values[2]["code"] == "memory_saved"
        assert (
            reopened.database.memory.list(MemoryListRequest(session_id=session["id"]))
            .documents[0]
            .revision
            == 3
        )
    finally:
        reopened.close()


def test_other_note_changes_do_not_cancel_requests_or_overwrite_the_index(
    setup, monkeypatch
):
    store, session = setup
    service = store.database.memory
    seed(service, session)
    before = service.read(
        MemoryReadRequest(session_id=session["id"], path="profile.md"), for_use=True
    )
    prepared = service.prepared
    inserted = False

    @contextmanager
    def concurrent_note(scope, body):
        nonlocal inserted
        with prepared(scope, body) as reference:
            if not inserted:
                inserted = True
                service.edit(
                    MemoryEditRequest(
                        session_id=session["id"],
                        operation_id="other-note",
                        path="other.md",
                        content="另一篇笔记",
                    ),
                    automatic=True,
                )
            yield reference

    monkeypatch.setattr(service, "prepared", concurrent_note)
    with service.admit(before.epochs, threading.Event()) as cancellation:
        service.edit(
            MemoryEditRequest(
                session_id=session["id"],
                operation_id="update",
                path="profile.md",
                expected_revision=1,
                content="当前笔记更新",
            ),
            automatic=True,
        )
        assert not cancellation.is_set()
    index = service.read(MemoryReadRequest(session_id=session["id"])).content
    assert "other.md" in index and "profile.md" in index
    reference = store.connection.execute(
        "SELECT file_ref FROM memory_indexes WHERE scope_id=?", (before.scope_id,)
    ).fetchone()[0]
    assert service.repository.files.read(reference) == index


def test_child_runs_have_no_memory_tools_index_or_direct_access(setup):
    store, session = setup
    service = store.database.memory
    seed(service, session)
    parent, _ = store.create_run(session["id"], "分析任务")
    spawn = store.create_tool_item(parent["id"], 1, 0, "spawn", "spawn_agent", "{}")
    child = CollaborationRepository(store.database).spawn(
        parent["id"],
        spawn["id"],
        SpawnAgent(task_name="inspect", message="分析指定文件", role="explorer"),
    )
    assert store.claim_next_run()["id"] == child.run_id
    model = ScriptedModel([ModelResponse(text="完成分析")])
    RuntimeEngine(store, model, lambda _: None).run(child.run_id, threading.Event())
    assert not any(
        tool.name.startswith("memory_") for tool in model.tool_definitions_history[0]
    )
    assert not any(
        item.get("sectionId") == "memory-evidence" for item in model.contexts[0]
    )
    assert not service.access(child.session_id).read_enabled
    with pytest.raises(MemoryRejected, match="child_disabled"):
        service.read(
            MemoryReadRequest(session_id=child.session_id, path="profile.md"),
            for_use=True,
        )
    assert not memory_entries(child=True)


def test_v42_upgrade_preserves_notes_and_does_not_resurrect_forgotten_entries(tmp_path):
    path = tmp_path / "state.sqlite"
    with sqlite3.connect(path) as connection:
        connection.executescript(V42_SCHEMA_SQL + "PRAGMA user_version=42;")
        connection.execute(
            "UPDATE memory_metadata SET value='0' WHERE key='legacy_cleanup_pending'"
        )
        connection.execute(
            "INSERT INTO memory_scopes(id,kind,mode,enabled_after) VALUES('global','global','auto',0)"
        )
        for identifier, status in (
            ("keep", "active"),
            ("forgotten", "forgotten"),
            ("revoked", "quarantined"),
        ):
            connection.execute(
                "INSERT INTO memory_documents(id,scope_id,path,current_revision,status,user_owned,created_at,updated_at) VALUES(?,'global',?,1,?,0,1,1)",
                (identifier, identifier + ".md", status),
            )
        connection.commit()
        flow_migration.migrate_memory_flow(connection)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert connection.execute(
            "SELECT id,status,archived FROM memory_documents ORDER BY id"
        ).fetchall() == [
            ("forgotten", "forgotten", 0),
            ("keep", "active", 0),
            ("revoked", "quarantined", 0),
        ]


def test_flow_migration_failure_rolls_back_column_triggers_and_version(monkeypatch):
    with sqlite3.connect(":memory:") as connection:
        connection.executescript(V42_SCHEMA_SQL + "PRAGMA user_version=42;")
        before = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='memory_source_change'"
        ).fetchone()[0]
        monkeypatch.setattr(
            flow_migration,
            "MEMORY_FLOW_SQL",
            flow_migration.MEMORY_FLOW_SQL
            + "SELECT nonexistent_column FROM memory_scopes;",
        )
        with pytest.raises(sqlite3.OperationalError):
            flow_migration.migrate_memory_flow(connection)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 42
        assert "archived" not in {
            row[1] for row in connection.execute("PRAGMA table_info(memory_documents)")
        }
        assert (
            connection.execute(
                "SELECT sql FROM sqlite_master WHERE name='memory_source_change'"
            ).fetchone()[0]
            == before
        )

from __future__ import annotations

from contextlib import contextmanager
import json
from itertools import count
import sqlite3
import threading

import pytest

from eidos_runtime.db.schema import V43_SCHEMA_VERSION, V42_SCHEMA_SQL
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
from eidos_runtime.runtime.approval import ApprovalDecision
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
            content=changes.pop("content", "用户使用中文。"),
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
    assert store.claim_next_run_committed().value["id"] == child.run_id
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
        assert connection.execute("PRAGMA user_version").fetchone()[0] == V43_SCHEMA_VERSION
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


@pytest.mark.parametrize("action", ["write", "append", "delete"])
@pytest.mark.parametrize("mode,resume_mode", [("read", "read"), ("off", "off"), ("off", "auto")])
@pytest.mark.parametrize("changed", [False, True])
def test_approval_recovery_retains_cas_and_archive_semantics(setup, action, mode, resume_mode, changed):
    store, session = setup
    seed(store.database.memory, session)
    store.database.memory.policy(
        MemoryPolicyRequest(session_id=session["id"], scope="current", mode=mode)
    )
    run, _ = store.create_run(
        session["id"], "明确更正或遗忘笔记", approval_mode="manual"
    )
    arguments = {
        "path": "profile.md",
        "action": action,
    }
    if action == "write":
        arguments.update(content="用户明确更正后的内容。", summary="更正后的背景")
    if action == "append":
        arguments.update(content="补充：用户也使用英文。")

    class Interrupted(BaseException):
        pass

    def interrupt(request, _cancel):
        assert request["kind"] == "file_change"
        assert request["summary"] and isinstance(request["diff"], str)
        assert request["expectedRevision"] == 1
        if mode == "off":
            assert "用户使用中文。" not in json.dumps(request, ensure_ascii=False)
        elif action == "append":
            assert "+补充：用户也使用英文。" in request["diff"]
            assert "+用户使用中文。" in request["diff"]
        else:
            assert "-用户使用中文。" in request["diff"]
        if action == "append" and mode == "off":
            assert "+补充：用户也使用英文。" in request["diff"]
        raise Interrupted()

    model = ScriptedModel(
        ([call("memory_read", path="profile.md")] if mode == "read" else [])
        + [call("memory_edit", **arguments)]
    )
    with pytest.raises(Interrupted):
        RuntimeEngine(
            store,
            model,
            lambda _: None,
            request_approval=interrupt,
        ).run(run["id"], threading.Event())
    if mode == "off":
        assert not any("用户使用中文。" in str(context) for context in model.contexts)
    root = store.data_directory
    store.close()
    reopened = SessionStore(root)
    reopened.initialize()
    approvals = []
    try:
        if resume_mode != mode:
            reopened.database.memory.policy(
                MemoryPolicyRequest(session_id=session["id"], scope="current", mode=resume_mode)
            )
        if changed:
            reopened.database.memory.edit(
                MemoryEditRequest(
                    session_id=session["id"],
                    operation_id="parallel-edit",
                    path="profile.md",
                    expected_revision=1,
                    content="另一个窗口修改后的内容。",
                )
            )

        def approve(request, _cancel):
            approvals.append(request)
            return ApprovalDecision("approve")

        RuntimeEngine(
            reopened,
            ScriptedModel([ModelResponse(text="恢复完成")]),
            lambda _: None,
            request_approval=approve,
        ).run(run["id"], threading.Event())
        assert len(approvals) == (0 if changed else 1)
        assert reopened.read_run(run["id"])["status"] == "succeeded"
        code = (
            "memory_revision_conflict" if changed
            else "memory_saved" if action in {"write", "append"}
            else "memory_archived"
        )
        assert results(reopened, run)[-1]["code"] == code
        assert bool(
            reopened.database.memory.list(
                MemoryListRequest(session_id=session["id"])
            ).documents
        ) == (action in {"write", "append"} or changed)
        assert len(
            reopened.database.memory.list(
                MemoryListRequest(session_id=session["id"], include_archived=True)
            ).documents
        ) == 1
        if action == "append" and not changed:
            assert reopened.database.memory.read(
                MemoryReadRequest(session_id=session["id"], path="profile.md")
            ).content == "用户使用中文。\n补充：用户也使用英文。"
        if changed:
            assert reopened.database.memory.read(
                MemoryReadRequest(session_id=session["id"], path="profile.md")
            ).content == "另一个窗口修改后的内容。"
            approval = reopened.connection.execute(
                "SELECT status,decision FROM approvals WHERE run_id=?", (run["id"],)
            ).fetchone()
            assert tuple(approval) == ("invalidated", None)
            assert not reopened.connection.execute(
                "SELECT 1 FROM durable_intents WHERE run_id=?", (run["id"],)
            ).fetchone()
            assert reopened.connection.execute(
                "SELECT 1 FROM events e JOIN event_outbox o ON o.event_id=e.id WHERE e.run_id=? AND e.event_type='approval.status_changed' AND json_extract(e.payload_json,'$.current')='invalidated'",
                (run["id"],),
            ).fetchone()
    finally:
        reopened.close()


def test_append_and_replace_merge_without_a_prior_read(setup):
    store, session = setup
    service = store.database.memory
    seed(service, session, content="# 背景\n\n- 用户使用中文。\n")
    run, _ = store.create_run(session["id"], "补充并更正我的背景", approval_mode="manual")
    model = ScriptedModel(
        [
            call("memory_edit", path="profile.md", action="append", content="- 用户是后端开发。"),
            call("memory_edit", path="profile.md", action="replace", oldText="使用中文", content="使用简体中文"),
            call("memory_edit", path="profile.md", action="replace", oldText="不存在的文字", content="x"),
            call("memory_edit", path="profile.md", action="replace", oldText="- 用户", content="- 他"),
            call("memory_edit", path="missing.md", action="replace", oldText="x", content="y"),
            call("memory_edit", path="new.md", action="append", content="新笔记的第一行。"),
            ModelResponse(text="已更新"),
        ]
    )
    RuntimeEngine(store, model, lambda _: None).run(run["id"], threading.Event())
    assert [result.get("code") for result in results(store, run)] == [
        "memory_saved",
        "memory_saved",
        "memory_text_not_found",
        "memory_text_ambiguous",
        "memory_document_not_found",
        "memory_saved",
    ]
    read = service.read(MemoryReadRequest(session_id=session["id"], path="profile.md"))
    assert read.content == "# 背景\n\n- 用户使用简体中文。\n- 用户是后端开发。"
    assert read.revision == 3 and read.document.kind == "user"
    created = service.read(MemoryReadRequest(session_id=session["id"], path="new.md"))
    assert created.content == "新笔记的第一行。" and created.revision == 1
    assert not store.connection.execute("SELECT 1 FROM approvals").fetchone()
    assert store.read_run(run["id"])["status"] == "succeeded"
    operation = store.connection.execute(
        "SELECT operation_id FROM memory_actions WHERE operation_id LIKE 'memory-tool:%' ORDER BY created_at LIMIT 1"
    ).fetchone()[0]
    assert service.replayed(operation).code == "memory_saved"


def test_replace_needs_reading_and_merges_respect_the_note_limit(setup):
    store, session = setup
    service = store.database.memory
    seed(service, session, content="x" * 65_000)
    service.policy(MemoryPolicyRequest(session_id=session["id"], scope="current", mode="off"))
    service.policy(MemoryPolicyRequest(session_id=session["id"], scope="global", mode="off"))
    run, _ = store.create_run(session["id"], "更正笔记", approval_mode="full_access")
    model = ScriptedModel(
        [
            call("memory_edit", path="profile.md", action="replace", oldText="x", content="y"),
            call("memory_edit", path="profile.md", action="append", content="y" * 1_000),
            ModelResponse(text="未能修改"),
        ]
    )
    RuntimeEngine(store, model, lambda _: None).run(run["id"], threading.Event())
    assert [result.get("code") for result in results(store, run)] == [
        "memory_read_disabled",
        "memory_note_too_large",
    ]
    assert store.read_run(run["id"])["status"] == "succeeded"


def test_new_notes_report_similar_notes_and_index_overflow(setup):
    store, session = setup
    service = store.database.memory
    service.edit(
        MemoryEditRequest(
            session_id=session["id"],
            operation_id="existing",
            path="testing-policy.md",
            title="测试策略",
            summary="只在明确要求时运行全量测试，平时运行定向测试。",
            content="用户只在明确要求时运行全量测试。",
        )
    )
    run, _ = store.create_run(session["id"], "记住测试习惯", approval_mode="manual")
    model = ScriptedModel(
        [
            call(
                "memory_edit",
                path="test-policy.md",
                title="测试策略",
                summary="只在明确要求时运行全量测试，平时跑定向测试。",
                content="全量测试只在用户明确要求时运行。",
            ),
            call("memory_edit", path="unrelated.md", summary="发布前需要更新版本号。", content="发布前更新版本号。"),
            ModelResponse(text="已保存"),
        ]
    )
    RuntimeEngine(store, model, lambda _: None).run(run["id"], threading.Event())
    similar, unrelated = results(store, run)
    assert similar["data"]["similar"] == [
        {"path": "testing-policy.md", "summary": "只在明确要求时运行全量测试，平时运行定向测试。"}
    ]
    assert "merge into it" in similar["summary"]
    assert "summary" not in unrelated
    assert "similar" not in unrelated["data"]
    assert "indexHidden" not in unrelated["data"]

    for index in range(230):
        service.edit(
            MemoryEditRequest(
                session_id=session["id"],
                operation_id=f"bulk-{index}",
                path=f"bulk-{index:03d}.md",
                summary=f"批量条目 {index}",
                content=f"条目 {index}",
            )
        )
    second, _ = store.create_run(session["id"], "再记一条", approval_mode="manual")
    RuntimeEngine(
        store,
        ScriptedModel([call("memory_edit", path="late.md", content="最后一条。"), ModelResponse(text="好")]),
        lambda _: None,
    ).run(second["id"], threading.Event())
    late = results(store, second)[0]
    hidden = late["data"]["indexHidden"]
    assert hidden > 0
    assert f"{hidden} notes in this scope no longer fit" in late["summary"]
    assert hidden == service.index_hidden(
        session["id"], service.list(MemoryListRequest(session_id=session["id"], scope="current")).scopes[-1].id
    )


def test_v53_upgrade_adds_read_tracking_and_keeps_notes(tmp_path):
    from schema_fixtures import remove_memory_usage_schema

    from eidos_runtime.db.schema import SCHEMA_VERSION

    data = tmp_path / "data"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = SessionStore(data)
    store.initialize()
    session = store.create_session(str(workspace))
    seed(store.database.memory, session)
    store.close()
    with sqlite3.connect(data / "state.sqlite") as connection:
        remove_memory_usage_schema(connection)
        connection.execute("PRAGMA user_version=53")
    reopened = SessionStore(data)
    reopened.initialize()
    try:
        assert reopened.health()["state"] == "ready"
        assert reopened.connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        read = reopened.database.memory.read(
            MemoryReadRequest(session_id=session["id"], path="profile.md")
        )
        assert read.content == "用户使用中文。" and read.document.last_read_at is None
        assert reopened.connection.execute(
            "SELECT read_count FROM memory_documents WHERE id=?", (read.document.id,)
        ).fetchone()[0] == 0
    finally:
        reopened.close()


def test_usage_migration_failure_rolls_back_columns_and_version(tmp_path, monkeypatch):
    from schema_fixtures import remove_memory_usage_schema

    from eidos_runtime.db.schema import SCHEMA_SQL
    from eidos_runtime.memory import usage_migration

    with sqlite3.connect(tmp_path / "state.sqlite") as connection:
        connection.executescript(SCHEMA_SQL)
        remove_memory_usage_schema(connection)
        connection.execute("PRAGMA user_version=53")
        connection.commit()
        monkeypatch.setattr(
            usage_migration,
            "MEMORY_USAGE_SCHEMA_SQL",
            usage_migration.MEMORY_USAGE_SCHEMA_SQL + "SELECT nonexistent_column FROM memory_scopes;",
        )
        with pytest.raises(sqlite3.OperationalError):
            usage_migration.migrate_memory_usage(connection)
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 53
        assert not {"last_read_at", "read_count"} & {
            row[1] for row in connection.execute("PRAGMA table_info(memory_documents)")
        }


def test_model_reads_update_usage_without_changing_versions(setup):
    store, session = setup
    service = store.database.memory
    original = seed(service, session)
    generation = service.list(MemoryListRequest(session_id=session["id"], scope="current")).scopes[-1].generation
    run, _ = store.create_run(session["id"], "看看我的背景", approval_mode="manual")
    RuntimeEngine(
        store,
        ScriptedModel(
            [
                call("memory_read", path="profile.md"),
                call("memory_read"),
                call("memory_search", order="oldest"),
                ModelResponse(text="好"),
            ]
        ),
        lambda _: None,
    ).run(run["id"], threading.Event())
    read, index, oldest = results(store, run)
    assert read["data"]["updated"] and len(read["data"]["updated"]) == 10
    assert index["outcome"] == oldest["outcome"] == "success"
    row = store.connection.execute(
        "SELECT read_count,last_read_at,current_revision FROM memory_documents WHERE id=?",
        (original.document_id,),
    ).fetchone()
    assert row[0] == 1 and row[1] is not None and row[2] == 1
    assert service.list(MemoryListRequest(session_id=session["id"], scope="current")).scopes[-1].generation == generation

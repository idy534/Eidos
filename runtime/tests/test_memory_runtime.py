from __future__ import annotations

import json
import threading

import pytest

from eidos_runtime.application.runs import RunApplication
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.memory.contracts import (
    MemoryEditRequest,
    MemoryListRequest,
    MemoryPolicyRequest,
)
from eidos_runtime.memory.repository import MemoryRejected
from eidos_runtime.model.client import (
    ModelResponse,
    ModelToolCall,
    ModelUsage,
    ScriptedModel,
)
from eidos_runtime.protocol.methods import ContextUsageRequestDto
from eidos_runtime.runtime.engine import RuntimeEngine

pytestmark = pytest.mark.integration


@pytest.fixture
def setup(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    store = SessionStore(tmp_path / "data")
    store.initialize()
    session = store.create_session(str(root))
    yield store, session
    store.close()


@pytest.mark.parametrize(
    "mode,user_requested", [("auto", False), ("read", True), ("off", True)]
)
def test_save_completes_in_current_run(setup, mode, user_requested):
    store, session = setup
    service = store.database.memory
    service.policy(
        MemoryPolicyRequest(session_id=session["id"], scope="current", mode=mode)
    )
    if mode == "off":
        service.policy(
            MemoryPolicyRequest(session_id=session["id"], scope="global", mode="off")
        )
    run, _ = store.create_run(
        session["id"], "以后都用中文回答，请记住。", approval_mode="full_access"
    )
    model = ScriptedModel(
        [
            ModelResponse(
                tool_calls=(
                    ModelToolCall(
                        "save",
                        "memory_edit",
                        {
                            "path": "user_language.md",
                            "summary": "用户希望以后使用中文回答。",
                            "content": "用户希望以后使用中文回答。",
                            "userRequested": user_requested,
                        },
                    ),
                )
            ),
            ModelResponse(text="已经记住。"),
        ]
    )
    RuntimeEngine(store, model, lambda _: None).run(run["id"], threading.Event())
    tool = next(
        item["toolCall"]
        for item in store.read_session_snapshot(session["id"])["items"]
        if item["kind"] == "tool_call"
    )
    result = json.loads(tool["resultJson"])
    assert result["outcome"] == "success", result
    assert result["data"]["action"]["code"] == "memory_saved"
    assert len(service.list(MemoryListRequest(session_id=session["id"])).documents) == 1
    assert store.read_run(run["id"])["status"] == "succeeded"
    assert {
        tool.name
        for tool in model.tool_definitions_history[0]
        if tool.name.startswith("memory_")
    } == (
        {"memory_edit"}
        if mode == "off"
        else {"memory_edit", "memory_read", "memory_search"}
    )


def test_read_only_mode_rejects_automatic_save_without_ending_run(setup):
    store, session = setup
    service = store.database.memory
    service.policy(
        MemoryPolicyRequest(session_id=session["id"], scope="current", mode="read")
    )
    run, _ = store.create_run(session["id"], "以后都用中文回答。")
    model = ScriptedModel(
        [
            ModelResponse(
                tool_calls=(
                    ModelToolCall(
                        "save",
                        "memory_edit",
                        {
                            "path": "user_language.md",
                            "summary": "中文",
                            "content": "使用中文。",
                        },
                    ),
                )
            ),
            ModelResponse(text="继续处理任务。"),
        ]
    )
    RuntimeEngine(store, model, lambda _: None).run(run["id"], threading.Event())
    assert not service.list(MemoryListRequest(session_id=session["id"])).documents
    assert store.read_run(run["id"])["status"] == "succeeded"
    assert any("memory_automatic_disabled" in str(item) for item in model.contexts[-1])


def test_context_uses_index_and_usage_survives_forgetting(setup, monkeypatch):
    store, session = setup
    service = store.database.memory
    service.edit(
        MemoryEditRequest(
            session_id=session["id"],
            operation_id="initial",
            path="feedback_testing.md",
            summary="只在要求时运行全量测试。",
            content="条件很长的正文" * 100,
        )
    )
    run, _ = store.create_run(session["id"], "Answer briefly")
    model = ScriptedModel(
        [
            ModelResponse(
                text="Done", usage=ModelUsage(input_tokens=1850, output_tokens=10)
            )
        ]
    )
    runtime = RuntimeEngine(store, model, lambda _: None)
    runtime.run(run["id"], threading.Event())
    assert any("feedback_testing.md" in str(item) for item in model.contexts[0])
    assert not any("条件很长的正文" in str(item) for item in model.contexts[0])
    application = RunApplication(store=store, runtime=runtime)
    request = ContextUsageRequestDto(runId=run["id"])
    before = application.context_usage(request).context_usage
    service.edit(
        MemoryEditRequest(
            session_id=session["id"],
            operation_id="forget",
            path="feedback_testing.md",
            action="delete",
            expected_revision=1,
        )
    )
    with pytest.raises(MemoryRejected):
        store.read_latest_context_snapshot(run["id"])
    assert not store.read_step_resolution_snapshots(run["id"])
    monkeypatch.setattr(
        store.database.json_blobs,
        "read",
        lambda *_args, **_kwargs: pytest.fail("usage must not read revoked bodies"),
    )
    assert (
        application.context_usage(request).context_usage.active_tokens
        == before.active_tokens
    )


def test_assistant_prose_is_not_source_evidence(setup):
    store, session = setup
    run, _ = store.create_run(session["id"], "分析现状")
    RuntimeEngine(
        store, ScriptedModel([ModelResponse(text="这不是事实证据。")]), lambda _: None
    ).run(run["id"], threading.Event())
    source = store.connection.execute(
        "SELECT id FROM items WHERE run_id=? AND kind='assistant_message'", (run["id"],)
    ).fetchone()[0]
    with pytest.raises(MemoryRejected, match="verified_tool"):
        store.database.memory.edit(
            MemoryEditRequest(
                session_id=session["id"],
                operation_id="bad",
                path="project.md",
                summary="提议",
                content="未经确认的假设",
                source_item_ids=[source],
            ),
            automatic=True,
        )


def test_forked_memory_results_are_redacted_and_scrubbed_after_forgetting(setup):
    from uuid import uuid4

    from eidos_runtime.persistence.session_history import SessionHistoryRepository
    from eidos_runtime.protocol.session_fork import SessionForkRequest

    store, session = setup
    service = store.database.memory
    body = "这份记忆中的独特正文必须遗忘。"
    service.edit(
        MemoryEditRequest(
            session_id=session["id"],
            operation_id="initial",
            path="project_note.md",
            summary="项目约定",
            content=body,
        )
    )
    run, _ = store.create_run(session["id"], "查看项目约定")
    model = ScriptedModel(
        [
            ModelResponse(
                tool_calls=(
                    ModelToolCall("read", "memory_read", {"path": "project_note.md"}),
                )
            ),
            ModelResponse(text="完成读取。"),
        ]
    )
    RuntimeEngine(store, model, lambda _: None).run(run["id"], threading.Event())
    answer = store.read_session_snapshot(session["id"])["items"][-1]["id"]
    history = SessionHistoryRepository(store.database)
    fork = history.fork(
        SessionForkRequest(
            operation_id=str(uuid4()),
            source_session_id=session["id"],
            source_item_id=answer,
            target={"kind": "same_workspace"},
        ),
        session_id=str(uuid4()),
    ).value
    assert body in str(
        [
            record.item.to_json_value()
            for record in history.page(fork, limit=100).records
        ]
    )
    service.edit(
        MemoryEditRequest(
            session_id=session["id"],
            operation_id="forget",
            path="project_note.md",
            action="delete",
            expected_revision=1,
        )
    )
    for record in history.page(fork, limit=100).records:
        assert body not in record.model_dump_json()
    for row in store.connection.execute(
        "SELECT record_json FROM session_history_entries WHERE session_id=?", (fork,)
    ):
        assert body not in store.database.json_blobs.read_json(
            row[0], expected_kind="session-history-record"
        )


@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_explicit_memory_save_obeys_manual_approval(setup, decision):
    from eidos_runtime.runtime.approval import ApprovalDecision

    store, session = setup
    seen = []
    run, _ = store.create_run(
        session["id"], "请记住以后使用中文", approval_mode="manual"
    )
    model = ScriptedModel(
        [
            ModelResponse(
                tool_calls=(
                    ModelToolCall(
                        "save",
                        "memory_edit",
                        {
                            "path": "user_language.md",
                            "summary": "语言",
                            "content": "用户希望使用中文。",
                            "userRequested": True,
                        },
                    ),
                )
            ),
            ModelResponse(text="结束本次处理。"),
        ]
    )

    def approve(payload, _cancel):
        seen.append(payload)
        return ApprovalDecision(decision)

    RuntimeEngine(store, model, lambda _: None, request_approval=approve).run(
        run["id"], threading.Event()
    )
    assert len(seen) == 1
    documents = store.database.memory.list(
        MemoryListRequest(session_id=session["id"])
    ).documents
    assert bool(documents) == (decision == "approve")
    assert store.read_run(run["id"])["status"] == "succeeded"


def test_late_model_answer_is_discarded_when_its_memory_is_revoked(setup):
    store, session = setup
    service = store.database.memory
    service.edit(
        MemoryEditRequest(
            session_id=session["id"],
            operation_id="initial",
            path="project_note.md",
            summary="旧的约定",
            content="已经过时的记忆",
        )
    )
    run, _ = store.create_run(session["id"], "处理当前任务")

    class LateModel(ScriptedModel):
        revoked = False

        def complete(self, context, cancel, on_text_delta, **kwargs):
            if not self.revoked:
                self.revoked = True
                service.edit(
                    MemoryEditRequest(
                        session_id=session["id"],
                        operation_id="forget",
                        path="project_note.md",
                        action="delete",
                        expected_revision=1,
                    )
                )
                return ModelResponse(text="不应发布的迟到答案")
            return super().complete(context, cancel, on_text_delta, **kwargs)

    model = LateModel([ModelResponse(text="使用新上下文完成任务。")])
    RuntimeEngine(store, model, lambda _: None).run(run["id"], threading.Event())
    items = store.read_session_snapshot(session["id"])["items"]
    assert not any(item.get("content") == "不应发布的迟到答案" for item in items)
    assert any(item.get("content") == "使用新上下文完成任务。" for item in items)
    assert store.read_run(run["id"])["status"] == "succeeded"


def test_late_answer_from_a_historical_automatic_version_is_discarded(setup):
    store, session = setup
    service = store.database.memory
    source_run, _ = store.create_run(session["id"], "旧规则来源")
    source = store.get_user_item(source_run["id"])["id"]
    request = MemoryEditRequest(
        session_id=session["id"], operation_id="automatic", path="feedback.md",
        summary="旧规则", content="旧规则", source_item_ids=[source],
    )
    service.edit(request, automatic=True)
    service.edit(request.model_copy(update={
        "operation_id": "user-edit", "expected_revision": 1,
        "summary": "用户独立的新规则", "content": "用户独立的新规则",
    }))
    RuntimeEngine(store, ScriptedModel([ModelResponse(text="来源处理完成")]), lambda _: None).run(
        source_run["id"], threading.Event()
    )
    run, _ = store.create_run(session["id"], "检查历史规则")

    class LateHistoricalModel(ScriptedModel):
        calls = 0

        def complete(self, context, cancel, on_text_delta, **kwargs):
            self.calls += 1
            if self.calls == 2:
                with store.database.transaction() as connection:
                    connection.execute("UPDATE items SET content='来源已更正' WHERE id=?", (source,))
                service.cleanup()
                # Simulate a Provider that ignores cancellation and returns late.
                return super().complete(context, threading.Event(), on_text_delta, **kwargs)
            return super().complete(context, cancel, on_text_delta, **kwargs)

    model = LateHistoricalModel([
        ModelResponse(tool_calls=(ModelToolCall("historical", "memory_read", {
            "path": request.path, "revision": 1,
        }),)),
        ModelResponse(text="不应发布的历史版本答案"),
        ModelResponse(text="使用合法的新上下文完成任务"),
    ])
    RuntimeEngine(store, model, lambda _: None).run(run["id"], threading.Event())
    items = store.read_session_snapshot(session["id"])["items"]
    assert not any(item.get("content") == "不应发布的历史版本答案" for item in items)
    assert any(item.get("content") == "使用合法的新上下文完成任务" for item in items)
    assert "旧规则" not in str(model.contexts[-1])
    assert store.read_run(run["id"])["status"] == "succeeded"


@pytest.mark.parametrize("revocation", ["off", "forget"])
@pytest.mark.parametrize("decision", ["approve", "reject"])
def test_pending_approval_recovers_after_memory_revocation_and_restart(
    setup, revocation, decision, monkeypatch
):
    from eidos_runtime.runtime.approval import ApprovalDecision

    class Crash(BaseException):
        pass

    store, session = setup
    service = store.database.memory
    service.edit(MemoryEditRequest(
        session_id=session["id"], operation_id="seed", path="seed.md",
        summary="必须撤销的旧规则", content="旧规则正文",
    ))
    run, _ = store.create_run(session["id"], "请记住以后都用中文", approval_mode="manual")
    model = ScriptedModel([ModelResponse(tool_calls=(ModelToolCall("save", "memory_edit", {
        "path": "language.md", "summary": "语言偏好", "content": "用户希望使用中文",
        "userRequested": True,
    }),))])

    def crash(_request, _cancel):
        raise Crash()

    with pytest.raises(Crash):
        RuntimeEngine(store, model, lambda _: None, request_approval=crash).run(
            run["id"], threading.Event()
        )
    original_permissions = store.read_run_resolution_snapshot(run["id"]).permission_profile_json
    original_tool = store.read_step_tool_snapshot(run["id"], 1)
    if revocation == "off":
        service.policy(MemoryPolicyRequest(session_id=session["id"], scope="current", mode="off"))
    else:
        service.edit(MemoryEditRequest(
            session_id=session["id"], operation_id="forget", path="seed.md",
            action="delete", expected_revision=1,
        ))
    assert store.read_step_resolution_snapshots(run["id"]) == ()
    store.collect_unreferenced_blobs()
    data = store.data_directory
    store.close()
    reopened = SessionStore(data)
    reopened.initialize()
    try:
        assert reopened.health()["state"] == "ready"
        assert reopened.read_run(run["id"])["status"] == "waiting_approval"
        assert reopened.read_run_resolution_snapshot(run["id"]).permission_profile_json == original_permissions
        assert reopened.read_step_tool_snapshot(run["id"], 1) == original_tool
        confirmations = []

        def confirm(request, _cancel):
            confirmations.append(request)
            return ApprovalDecision(decision)

        def no_revoked_snapshot(_run_id):
            pytest.fail("Approval recovery must not read revoked model context")

        monkeypatch.setattr(reopened, "read_step_resolution_snapshots", no_revoked_snapshot)
        resumed_model = ScriptedModel([ModelResponse(text="恢复后完成任务")])
        RuntimeEngine(reopened, resumed_model, lambda _: None, request_approval=confirm).run(
            run["id"], threading.Event()
        )
        assert len(confirmations) == 1
        assert reopened.read_run(run["id"])["status"] == "succeeded"
        assert "必须撤销的旧规则" not in str(resumed_model.contexts)
        assert reopened.connection.execute("SELECT status FROM approvals").fetchone()[0] == (
            "approved" if decision == "approve" else "rejected"
        )
        assert reopened.connection.execute(
            "SELECT count(*) FROM memory_documents WHERE path='language.md' AND status='active'"
        ).fetchone()[0] == int(decision == "approve")
    finally:
        reopened.close()

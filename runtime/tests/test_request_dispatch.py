from __future__ import annotations

import json
import logging
import threading
import time
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import BaseModel

from eidos_runtime.protocol import dispatch
from eidos_runtime.protocol.dispatch import (
    EXCLUSIVE,
    RequestAdmission,
    RequestEntry,
    classify_request,
)
from eidos_runtime.protocol.registry import MethodRegistration
from eidos_runtime.protocol.response_server import ResponseActionRuntimeServer
from eidos_runtime.protocol.server import RuntimeServer
from eidos_runtime.sandbox.seatbelt import SeatbeltSelfTestResult


class _Recorder:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.admission = RequestAdmission(lambda entry: self.started.append(entry.method))
        self._entries: dict[str, RequestEntry] = {}
        self._sequence = 0

    def submit(self, label: str, method: str, params: dict[str, object] | None = None) -> None:
        request = classify_request(method, params or {})
        assert request is not None
        self._sequence += 1
        entry = RequestEntry(self._sequence, label, request, lambda: None, lambda: None)
        self._entries[label] = entry
        self.admission.submit(entry)

    def finish(self, label: str) -> None:
        self.admission.finish(self._entries[label])


def test_session_read_does_not_wait_for_another_sessions_git_write() -> None:
    recorder = _Recorder()
    recorder.submit("commit-a", "session/gitCommit", {"sessionId": "a"})
    recorder.submit("read-b", "session/read", {"sessionId": "b"})
    recorder.submit("events-b", "event/list", {"sessionId": "b"})

    assert recorder.started == ["commit-a", "read-b", "events-b"]


def test_read_observes_an_earlier_write_to_the_same_session() -> None:
    recorder = _Recorder()
    recorder.submit("start-a", "run/start", {"sessionId": "a"})
    recorder.submit("read-a", "session/read", {"sessionId": "a"})
    recorder.submit("read-b", "session/read", {"sessionId": "b"})

    assert recorder.started == ["start-a", "read-b"]
    recorder.finish("start-a")
    assert recorder.started == ["start-a", "read-b", "read-a"]


def test_index_read_waits_for_session_lifecycle_but_not_git_mutation() -> None:
    recorder = _Recorder()
    recorder.submit("stage", "session/gitStage", {"sessionId": "a"})
    recorder.submit("list-1", "session/list")
    recorder.submit("create", "session/create", {"workspaceRoot": "/tmp/x"})
    recorder.submit("list-2", "session/list")

    # session/create shares the Git lane with the stage, so it waits for it.
    assert recorder.started == ["stage", "list-1"]
    recorder.finish("stage")
    assert recorder.started == ["stage", "list-1", "create"]
    recorder.finish("create")
    assert recorder.started[-1] == "list-2"


def test_exclusive_write_keeps_total_order_among_writes_but_not_reads() -> None:
    recorder = _Recorder()
    recorder.submit("rename-a", "session/rename", {"sessionId": "a"})
    recorder.submit("model", "model/update", {"id": "m"})
    recorder.submit("rename-b", "session/rename", {"sessionId": "b"})
    recorder.submit("read-b", "session/read", {"sessionId": "b"})
    recorder.submit("presets", "model/presets")

    assert recorder.started == ["rename-a"]
    recorder.finish("rename-a")
    assert recorder.started == ["rename-a", "model"]
    recorder.finish("model")
    # The config read observes the exclusive write; the Session read only the rename.
    assert recorder.started == ["rename-a", "model", "rename-b", "presets"]
    recorder.finish("rename-b")
    assert recorder.started[-1] == "read-b"


def test_run_controls_and_unrelated_writes_do_not_wait_for_each_other() -> None:
    recorder = _Recorder()
    recorder.submit("commit", "session/gitCommit", {"sessionId": "a"})
    recorder.submit("cancel", "run/cancel", {"runId": "r1"})
    recorder.submit("start-b", "run/start", {"sessionId": "b"})
    recorder.submit("draft", "input/draftWrite", {"key": "b"})
    recorder.submit("draft-read", "input/draftRead", {"key": "b"})

    assert recorder.started == ["commit", "cancel", "start-b", "draft"]
    recorder.finish("draft")
    assert recorder.started[-1] == "draft-read"


def test_writes_never_wait_for_reads() -> None:
    recorder = _Recorder()
    recorder.submit("read-a", "session/read", {"sessionId": "a"})
    recorder.submit("rename-a", "session/rename", {"sessionId": "a"})

    assert recorder.started == ["read-a", "rename-a"]


def test_missing_scope_falls_back_to_exclusive() -> None:
    assert classify_request("run/start", {}) is EXCLUSIVE
    assert classify_request("run/cancel", {"runId": ""}) is EXCLUSIVE
    assert classify_request("future/unknownWrite", {}) is EXCLUSIVE
    assert classify_request("initialize", {}) is None
    assert classify_request("runtime/shutdown", {}) is None


def test_every_classified_method_is_registered(tmp_path: Path) -> None:
    server = ResponseActionRuntimeServer(StringIO(), data_directory=tmp_path / "data")
    registered = set(server.method_registry.names())
    classified = dispatch.EXPLICITLY_CLASSIFIED_METHODS - dispatch.CONTROL_METHODS

    assert classified - registered == set()
    assert registered - classified == set()


def test_browser_team_hook_plan_sidebar_and_guardrail_dispatch_lanes() -> None:
    # Browser operations serialize against each other without stalling sessions, runs, or teams.
    recorder = _Recorder()
    recorder.submit("browser-open", "browser/open")
    recorder.submit("browser-close", "browser/close")
    recorder.submit("read-s1", "session/read", {"sessionId": "s1"})
    recorder.submit("start-s1", "run/start", {"sessionId": "s1"})
    recorder.submit("cancel-r1", "run/cancel", {"runId": "r1"})
    recorder.submit("team-msgs-1", "team/messages", {"teamId": "team-1"})
    assert recorder.started == [
        "browser-open",
        "read-s1",
        "start-s1",
        "cancel-r1",
        "team-msgs-1",
    ]
    recorder.finish("browser-open")
    assert "browser-close" in recorder.started

    # team/post on team-1 blocks team/messages(team-1) and team/read, not team-2 or unrelated session/read.
    team_rec = _Recorder()
    team_rec.submit("post-1", "team/post", {"teamId": "team-1"})
    team_rec.submit("msgs-1", "team/messages", {"teamId": "team-1"})
    team_rec.submit("team-read", "team/read", {"sessionId": "s-lead"})
    team_rec.submit("msgs-2", "team/messages", {"teamId": "team-2"})
    team_rec.submit("post-2", "team/post", {"teamId": "team-2"})
    team_rec.submit("other-session", "session/read", {"sessionId": "s-other"})
    assert team_rec.started == ["post-1", "msgs-2", "post-2", "other-session"]
    team_rec.finish("post-1")
    assert team_rec.started[-1] == "msgs-1"
    team_rec.finish("post-2")
    assert team_rec.started[-1] == "team-read"

    # team/confirmRoster blocks team/read and session/list but not unrelated session/read or run/cancel.
    roster_rec = _Recorder()
    roster_rec.submit("confirm", "team/confirmRoster", {"proposalId": "p1"})
    roster_rec.submit("team-read", "team/read", {"sessionId": "s-lead"})
    roster_rec.submit("session-list", "session/list")
    roster_rec.submit("other-read", "session/read", {"sessionId": "s-other"})
    roster_rec.submit("cancel", "run/cancel", {"runId": "r-other"})
    assert roster_rec.started == ["confirm", "other-read", "cancel"]
    roster_rec.finish("confirm")
    assert set(roster_rec.started[-2:]) == {"team-read", "session-list"}

    # hook/configuration read vs mutation, plan per-plan serialization, sidebar pins vs git, guardrails & model/update.
    assert classify_request("hook/configuration", {"action": "read", "sessionId": "s1"}).read is True
    assert classify_request("hook/configuration", {"action": "trust", "sourceId": "src-1"}) is EXCLUSIVE
    assert classify_request("model/update", {"id": "m1"}) is EXCLUSIVE

    plan_rec = _Recorder()
    plan_rec.submit("plan1-read", "plan/read", {"planId": "p1"})
    plan_rec.submit("plan1-edit", "plan/edit", {"planId": "p1"})
    plan_rec.submit("plan2-read", "plan/read", {"planId": "p2"})
    assert plan_rec.started == ["plan1-read", "plan2-read"]
    plan_rec.finish("plan1-read")
    assert plan_rec.started[-1] == "plan1-edit"

    sidebar_rec = _Recorder()
    sidebar_rec.submit("pin", "sidebar/setPinned", {"pinned": True})
    sidebar_rec.submit("pins-read", "sidebar/listPins")
    sidebar_rec.submit("git-commit", "session/gitCommit", {"sessionId": "s1"})
    assert sidebar_rec.started == ["pin", "git-commit"]
    sidebar_rec.finish("pin")
    assert sidebar_rec.started[-1] == "pins-read"

    guardrail_rec = _Recorder()
    guardrail_rec.submit("save", "guardrails/save", {"expectedRevision": 0})
    guardrail_rec.submit("read", "guardrails/read")
    assert guardrail_rec.started == ["save"]
    guardrail_rec.finish("save")
    assert guardrail_rec.started == ["save", "read"]


class _SlowRequest(BaseModel):
    pass


class _SlowResponse(BaseModel):
    ok: bool


def _initialized_server(tmp_path: Path) -> tuple[RuntimeServer, StringIO]:
    output = StringIO()
    server = RuntimeServer(output, data_directory=tmp_path / "data")
    with patch(
        "eidos_runtime.protocol.server.run_seatbelt_self_test",
        return_value=SeatbeltSelfTestResult(True, ("test",), ()),
    ):
        server.dispatch({
            "jsonrpc": "2.0",
            "id": "client-init",
            "method": "initialize",
            "params": {"client": {"name": "test", "version": "1"}, "protocolVersion": 1},
        })
    assert server.request_dispatcher is not None
    return server, output


def _responses(output: StringIO) -> dict[str, dict[str, object]]:
    messages = [json.loads(line) for line in output.getvalue().splitlines()]
    return {str(message["id"]): message for message in messages if "id" in message}


def _wait_for(output: StringIO, request_id: str, timeout: float = 5.0) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = _responses(output).get(request_id)
        if response is not None:
            return response
        time.sleep(0.01)
    raise AssertionError(f"no response for {request_id}")


def test_slow_write_does_not_block_session_reads_and_shutdown_drains_it(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    server, output = _initialized_server(tmp_path)
    release = threading.Event()
    entered = threading.Event()

    def slow_write(_request_id: str, _request: _SlowRequest) -> _SlowResponse:
        entered.set()
        release.wait(10)
        return _SlowResponse(ok=True)

    server.method_registry.register(MethodRegistration(
        name="test/slowWrite",
        request_type=_SlowRequest,
        response_type=_SlowResponse,
        handler=slow_write,
    ))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = server.store.create_session(str(workspace))
    try:
        with caplog.at_level(logging.WARNING, logger="eidos.runtime.rpc"):
            server.dispatch({"jsonrpc": "2.0", "id": "client-slow", "method": "test/slowWrite", "params": {}})
            assert entered.wait(5)
            server.dispatch({
                "jsonrpc": "2.0", "id": "client-read", "method": "session/read",
                "params": {"sessionId": session["id"]},
            })
            server.dispatch({
                "jsonrpc": "2.0", "id": "client-events", "method": "event/list",
                "params": {"sessionId": session["id"], "afterEventId": 0},
            })
            read = _wait_for(output, "client-read")
            events = _wait_for(output, "client-events")
            assert "result" in read and "result" in events
            assert "client-slow" not in _responses(output)

            # A later exclusive write keeps the previous order among writes.
            server.dispatch({
                "jsonrpc": "2.0", "id": "client-settings", "method": "settings/update",
                "params": {"automaticCleanup": True, "managedWorktreeLimit": 15},
            })
            time.sleep(0.2)
            assert "client-settings" not in _responses(output)

            time.sleep(dispatch.SLOW_REQUEST_SECONDS)
            release.set()
            assert _wait_for(output, "client-slow")["result"] == {"ok": True}
            assert "result" in _wait_for(output, "client-settings")
        assert any(
            "rpc_slow method=test/slowWrite" in record.getMessage()
            for record in caplog.records
        )
    finally:
        release.set()
        server.dispatch({"jsonrpc": "2.0", "id": "client-shutdown", "method": "runtime/shutdown", "params": {}})
    assert _wait_for(output, "client-shutdown").get("result") == {}
    assert server.request_dispatcher is None


def test_shutdown_finishes_requests_accepted_before_it(tmp_path: Path) -> None:
    server, output = _initialized_server(tmp_path)
    release = threading.Event()
    entered = threading.Event()

    def slow_write(_request_id: str, _request: _SlowRequest) -> _SlowResponse:
        entered.set()
        release.wait(10)
        return _SlowResponse(ok=True)

    server.method_registry.register(MethodRegistration(
        name="test/slowWrite",
        request_type=_SlowRequest,
        response_type=_SlowResponse,
        handler=slow_write,
    ))
    server.dispatch({"jsonrpc": "2.0", "id": "client-slow", "method": "test/slowWrite", "params": {}})
    assert entered.wait(5)
    server.dispatch({"jsonrpc": "2.0", "id": "client-queued", "method": "test/slowWrite", "params": {}})
    threading.Timer(0.3, release.set).start()

    server.dispatch({"jsonrpc": "2.0", "id": "client-shutdown", "method": "runtime/shutdown", "params": {}})

    lines = [json.loads(line) for line in output.getvalue().splitlines()]
    ordered = [message["id"] for message in lines if message.get("id") in {
        "client-slow", "client-queued", "client-shutdown",
    }]
    assert ordered == ["client-slow", "client-queued", "client-shutdown"]
    assert all("result" in message for message in lines if message.get("id") == "client-queued")


def test_dispatcher_failure_falls_back_to_inline_handling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    server, output = _initialized_server(tmp_path)
    dispatcher = server.request_dispatcher
    assert dispatcher is not None

    def broken_call(*_args, **_kwargs):
        raise ValueError("dispatcher state is broken")

    monkeypatch.setattr(dispatcher._kernel, "call", broken_call)
    try:
        server.dispatch({"jsonrpc": "2.0", "id": "client-list", "method": "session/list", "params": {}})
        assert "result" in _responses(output)["client-list"]
    finally:
        monkeypatch.undo()
        server.dispatch({"jsonrpc": "2.0", "id": "client-shutdown", "method": "runtime/shutdown", "params": {}})
    assert _wait_for(output, "client-shutdown").get("result") == {}


def test_rejected_approval_answer_is_still_applied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    server, _output = _initialized_server(tmp_path)
    dispatcher = server.request_dispatcher
    assert dispatcher is not None
    applied: list[str] = []
    approvals = server._applications_or_error().approvals
    monkeypatch.setattr(approvals, "approve", lambda request_id: applied.append(request_id))

    def reject_immediately(_method, _request, _run, reject):
        reject()
        return True

    monkeypatch.setattr(dispatcher, "submit", reject_immediately)
    try:
        server.dispatch({"jsonrpc": "2.0", "id": "server-approval-1", "result": {"decision": "approve"}})
        assert applied == ["server-approval-1"]
    finally:
        monkeypatch.undo()
        server.dispatch({"jsonrpc": "2.0", "id": "client-shutdown", "method": "runtime/shutdown", "params": {}})

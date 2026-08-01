from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest

from eidos_runtime.application.errors import (
    ApplicationError,
    ApplicationInvalidParamsError,
)
from eidos_runtime.application.runs import RunApplication
from eidos_runtime.application.sessions import SessionApplication
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.protocol.methods import (
    EventListRequestDto,
    RunCancelRequestDto,
    RunStartRequestDto,
    SessionCreateRequestDto,
    SessionDeleteRequestDto,
    SessionListRequestDto,
    SessionReadRequestDto,
    SessionRenameRequestDto,
)
from eidos_runtime.sandbox.sensitive import SensitiveContentDenied


def _store(tmp_path: Path) -> SessionStore:
    data = tmp_path / "data"
    data.mkdir(mode=0o700)
    store = SessionStore(data)
    store.initialize()
    assert store.health_state == "ready"
    return store


def test_session_application_owns_current_session_and_event_use_cases(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = _store(tmp_path)
    try:
        application = SessionApplication(store, scan_text=lambda value: value)

        created = application.create(
            SessionCreateRequestDto(workspaceRoot=str(workspace))
        )
        session_id = str(created.root["id"])
        assert created.root["workspaceRoot"] == str(workspace.resolve())

        listed = application.list(SessionListRequestDto())
        assert listed.root["items"] == [created.root]

        snapshot = application.read_snapshot(SessionReadRequestDto(sessionId=session_id))
        assert snapshot.root["session"] == created.root
        assert snapshot.root["runs"] == []

        renamed = application.rename(
            SessionRenameRequestDto(sessionId=session_id, title="  renamed task  ")
        )
        assert renamed.root["title"] == "renamed task"

        events = application.list_events(EventListRequestDto(sessionId=session_id))
        assert events.root["items"]
        assert events.root["throughEventId"] >= 1

        deleted = application.delete(SessionDeleteRequestDto(sessionId=session_id))
        assert deleted.root == {"deletedSessionId": session_id}

        with pytest.raises(ApplicationError) as error:
            application.read_snapshot(SessionReadRequestDto(sessionId=session_id))
        assert error.value.code == "RESOURCE_NOT_FOUND"
    finally:
        store.close()


def test_session_application_preserves_semantic_invalid_params_and_sensitive_errors(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = _store(tmp_path)
    try:
        application = SessionApplication(store, scan_text=lambda value: value)
        with pytest.raises(ApplicationInvalidParamsError):
            application.list(SessionListRequestDto(cursor="not-a-session-cursor"))

        created = application.create(
            SessionCreateRequestDto(workspaceRoot=str(workspace))
        )
        session_id = str(created.root["id"])
        sensitive = SessionApplication(
            store,
            scan_text=lambda _value: (_ for _ in ()).throw(
                SensitiveContentDenied("fixture-rule")
            ),
        )
        with pytest.raises(ApplicationError) as error:
            sensitive.rename(
                SessionRenameRequestDto(sessionId=session_id, title="rename me")
            )
        assert error.value.code == "SENSITIVE_CONTENT_REJECTED"
    finally:
        store.close()


def test_session_application_uses_the_typed_repository_for_session_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = _store(tmp_path)

    def legacy_write_used(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("SessionApplication must use the typed repository")

    try:
        monkeypatch.setattr(store, "create_session", legacy_write_used)
        monkeypatch.setattr(store, "list_sessions", legacy_write_used)
        monkeypatch.setattr(store, "rename_session", legacy_write_used)
        monkeypatch.setattr(store, "delete_session", legacy_write_used)

        application = SessionApplication(store, scan_text=lambda value: value)
        created = application.create(
            SessionCreateRequestDto(workspaceRoot=str(workspace))
        )
        session_id = str(created.root["id"])

        assert application.list(SessionListRequestDto()).root["items"] == [created.root]
        renamed = application.rename(
            SessionRenameRequestDto(sessionId=session_id, title="typed")
        )
        assert renamed.root["title"] == "typed"
        assert application.delete(
            SessionDeleteRequestDto(sessionId=session_id)
        ).root == {"deletedSessionId": session_id}
    finally:
        store.close()


@dataclass(frozen=True)
class _WorkerStart:
    run_id: str


class _RuntimePort:
    def __init__(self, store: SessionStore) -> None:
        self.store = store
        self.prepared: list[_WorkerStart] = []
        self.released: list[_WorkerStart | None] = []
        self.aborted: list[_WorkerStart | None] = []

    def prepare_next(self) -> _WorkerStart | None:
        claimed = self.store.claim_next_run()
        if claimed is None:
            return None
        start = _WorkerStart(str(claimed["id"]))
        self.prepared.append(start)
        return start

    def release(self, start: _WorkerStart | None) -> None:
        self.released.append(start)

    def abort(self, start: _WorkerStart | None) -> None:
        self.aborted.append(start)

    def cancel_run(
        self, run_id: str, *, operation_id: str | None = None
    ) -> dict[str, object]:
        return self.store.cancel_run(run_id, operation_id=operation_id)


class _RunStartEnvironment:
    def __init__(self) -> None:
        self.title_requests: list[tuple[str, str, str]] = []

    def model_is_configured(self) -> bool:
        return True

    def profile_is_selectable(self, _profile: object) -> bool:
        return True

    def model_for(self, _model_id: str) -> object:
        return _LegacyModel()

    def extension_snapshot(self) -> dict[str, object]:
        return {
            "schemaVersion": 1,
            "extensionContractVersion": 1,
            "plugins": [],
            "skillCatalogHash": "0" * 64,
            "mcpConfigHash": "0" * 64,
        }

    def schedule_title_generation(
        self, session_id: str, user_input: str, model_id: str
    ) -> None:
        self.title_requests.append((session_id, user_input, model_id))


class _LegacyModel:
    profile_snapshot = None


def _run_application(store: SessionStore, runtime: _RuntimePort) -> RunApplication:
    return RunApplication(
        store=store,
        runtime=runtime,
        environment=_RunStartEnvironment(),
        scan_text=lambda value: value,
    )


def test_run_application_start_defers_worker_release_until_response_delivery(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = _store(tmp_path)
    try:
        session = store.create_session(str(workspace))
        runtime = _RuntimePort(store)
        environment = _RunStartEnvironment()
        application = RunApplication(
            store=store,
            runtime=runtime,
            environment=environment,
            scan_text=lambda value: value,
        )
        request = RunStartRequestDto(
            sessionId=session["id"],
            userInput="inspect the workspace",
            operationId=str(uuid4()),
        )

        outcome = application.start(request)
        assert outcome.response.root["status"] == "running"
        assert len(runtime.prepared) == 1
        assert runtime.released == []

        outcome.mark_response_delivered()
        assert runtime.released == runtime.prepared
        assert environment.title_requests == [
            (str(session["id"]), "inspect the workspace", "deepseek-v4-flash")
        ]

        replay = application.start(request)
        replay.mark_response_delivered()
        assert replay.response.root["id"] == outcome.response.root["id"]
        assert len(runtime.prepared) == 1
        assert runtime.released == runtime.prepared
        assert len(environment.title_requests) == 1
    finally:
        store.close()


def test_run_start_outcome_aborts_and_interrupts_a_claimed_run_after_response_failure(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = _store(tmp_path)
    try:
        session = store.create_session(str(workspace))
        runtime = _RuntimePort(store)
        application = _run_application(store, runtime)

        outcome = application.start(
            RunStartRequestDto(
                sessionId=session["id"],
                userInput="inspect the workspace",
            )
        )
        run_id = str(outcome.response.root["id"])
        outcome.mark_response_failed()

        assert runtime.aborted == runtime.prepared
        assert runtime.released == runtime.prepared
        assert store.read_run(run_id)["status"] == "interrupted"
    finally:
        store.close()


def test_run_application_cancel_uses_the_runtime_port_and_preserves_idempotence(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = _store(tmp_path)
    try:
        session = store.create_session(str(workspace))
        run, _item = store.enqueue_run(
            session["id"],
            "inspect the workspace",
        )
        runtime = _RuntimePort(store)
        application = _run_application(store, runtime)

        canceled = application.cancel(RunCancelRequestDto(runId=run["id"]))
        assert canceled.root["status"] == "canceled"

        replay = application.cancel(RunCancelRequestDto(runId=run["id"]))
        assert replay.root == canceled.root
    finally:
        store.close()

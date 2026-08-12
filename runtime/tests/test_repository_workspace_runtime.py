from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
import threading

import pytest

from eidos_runtime.application.repository import (
    RepositoryApplication,
    RepositoryApplicationFactory,
    RepositoryWorkspaceRuntime,
)
from eidos_runtime.application.sessions import SessionApplication
from eidos_runtime.db.database import Database
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel
from eidos_runtime.protocol.methods import SessionReadRequestDto
from eidos_runtime.persistence.repository_intelligence import (
    RepositoryIntelligenceRepository,
)
from eidos_runtime.repo_intelligence.watcher import RepositoryChange
from eidos_runtime.runtime.engine import RuntimeEngine


class _BlockingWatchController:
    instances: list["_BlockingWatchController"] = []

    def __init__(self, root: Path) -> None:
        self.root = root
        self.started = threading.Event()
        self.stopped = threading.Event()
        self.on_invalidate: Callable[[tuple[RepositoryChange, ...]], None] | None = None
        self.__class__.instances.append(self)

    def run(
        self,
        stop: threading.Event,
        on_invalidate: Callable[[tuple[RepositoryChange, ...]], None],
    ) -> None:
        self.on_invalidate = on_invalidate
        self.started.set()
        stop.wait()
        self.stopped.set()

    def emit(self, *changes: RepositoryChange) -> None:
        assert self.started.wait(1)
        assert self.on_invalidate is not None
        self.on_invalidate(changes)


@pytest.fixture(autouse=True)
def _clear_watchers() -> None:
    _BlockingWatchController.instances.clear()


def _database(tmp_path: Path) -> Database:
    database = Database(tmp_path / "data")
    database.initialize()
    return database


def _runtime(
    repository: RepositoryIntelligenceRepository,
) -> RepositoryWorkspaceRuntime:
    return RepositoryWorkspaceRuntime(
        RepositoryApplicationFactory(lambda: repository),
        watcher_factory=_BlockingWatchController,
    )


def test_activate_restores_persisted_generation_without_inventory_or_index_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "main.py").write_text("value = 1\n", encoding="utf-8")
    database = _database(tmp_path)
    try:
        repository = RepositoryIntelligenceRepository(database)
        built = RepositoryApplication(root, repository=repository).build()
        runtime = _runtime(repository)
        application = runtime.application_factory.for_workspace(root)
        monkeypatch.setattr(
            application.inventory_builder,
            "build",
            lambda **_kwargs: pytest.fail("activation must not build inventory"),
        )
        monkeypatch.setattr(
            application.indexer,
            "build",
            lambda *_args, **_kwargs: pytest.fail("activation must not build index"),
        )

        active = runtime.activate_workspace(root)

        assert active.snapshot is not None
        assert active.snapshot.inventory.generation == built.inventory.generation
        assert active.snapshot.index is not None
        assert active.snapshot.index.index_generation == built.index.index_generation
        assert active.snapshot.persisted_snapshot == built.persisted_snapshot
        assert len(_BlockingWatchController.instances) == 1
        runtime.shutdown_all()
    finally:
        database.close()


def test_activate_without_persisted_generation_keeps_snapshot_empty(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    database = _database(tmp_path)
    try:
        runtime = _runtime(RepositoryIntelligenceRepository(database))

        active = runtime.activate_workspace(root)

        assert active.snapshot is None
        assert active.recovery_status.complete is False
        assert active.reconciliation_required is True
        runtime.shutdown_all()
    finally:
        database.close()


def test_ensure_ready_builds_first_generation_then_reuses_it_without_rescan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "main.py").write_text("value = 1\n", encoding="utf-8")
    database = _database(tmp_path)
    try:
        runtime = _runtime(RepositoryIntelligenceRepository(database))
        active = runtime.activate_workspace(root)
        application = active.application
        original_build = application.build
        build_count = 0

        def counted_build(**kwargs: object):
            nonlocal build_count
            build_count += 1
            return original_build(**kwargs)

        monkeypatch.setattr(application, "build", counted_build)

        ready = runtime.ensure_ready(root)
        reused = runtime.ensure_ready(root)

        assert ready is active is reused
        assert ready.snapshot is not None
        assert ready.snapshot.complete is True
        assert ready.snapshot.inventory.generation == 1
        assert ready.recovery_status.inventory_generation == 1
        assert ready.recovery_status.index_generation == (
            ready.snapshot.index.index_generation if ready.snapshot.index else None
        )
        assert ready.reconciliation_required is False
        assert build_count == 1
        runtime.shutdown_all()
    finally:
        database.close()


def test_ensure_ready_reconciles_once_after_watcher_invalidation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    source = root / "main.py"
    source.write_text("value = 1\n", encoding="utf-8")
    database = _database(tmp_path)
    try:
        runtime = _runtime(RepositoryIntelligenceRepository(database))
        active = runtime.ensure_ready(root)
        assert active.snapshot is not None
        first_generation = active.snapshot.inventory.generation

        source.write_text("value = 2\n", encoding="utf-8")
        _BlockingWatchController.instances[0].emit(
            RepositoryChange(path="main.py", change="modified")
        )
        ready = runtime.ensure_ready(root)

        assert ready.snapshot is not None
        assert ready.snapshot.inventory.generation == first_generation + 1
        assert ready.dirty_paths == frozenset()
        assert ready.reconciliation_required is False
        runtime.shutdown_all()
    finally:
        database.close()


def test_concurrent_ensure_ready_runs_one_build(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "main.py").write_text("value = 1\n", encoding="utf-8")
    database = _database(tmp_path)
    try:
        runtime = _runtime(RepositoryIntelligenceRepository(database))
        active = runtime.activate_workspace(root)
        original_build = active.application.build
        entered = threading.Event()
        release = threading.Event()
        build_count = 0

        def blocked_build(**kwargs: object):
            nonlocal build_count
            build_count += 1
            entered.set()
            assert release.wait(2)
            return original_build(**kwargs)

        monkeypatch.setattr(active.application, "build", blocked_build)
        results: list[object] = []
        workers = [
            threading.Thread(target=lambda: results.append(runtime.ensure_ready(root)))
            for _ in range(2)
        ]
        for worker in workers:
            worker.start()
        assert entered.wait(1)
        release.set()
        for worker in workers:
            worker.join(2)

        assert len(results) == 2
        assert all(result is active for result in results)
        assert build_count == 1
        runtime.shutdown_all()
    finally:
        database.close()


def test_invalidation_during_build_publishes_baseline_but_keeps_workspace_stale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "main.py").write_text("value = 1\n", encoding="utf-8")
    database = _database(tmp_path)
    try:
        runtime = _runtime(RepositoryIntelligenceRepository(database))
        active = runtime.ensure_ready(root)
        assert active.snapshot is not None
        original_generation = active.snapshot.inventory.generation
        active.invalidate((RepositoryChange(path="main.py", change="modified"),))
        original_build = active.application.build

        def build_with_invalidation(**kwargs: object):
            candidate = original_build(**kwargs)
            active.invalidate((RepositoryChange(path="late.py", change="added"),))
            return candidate

        monkeypatch.setattr(active.application, "build", build_with_invalidation)
        runtime.ensure_ready(root)

        assert active.snapshot is not None
        assert active.snapshot.inventory.generation == original_generation + 1
        assert active.reconciliation_required is True
        assert active.dirty_paths == frozenset({"main.py", "late.py"})
        runtime.shutdown_all()
    finally:
        database.close()


def test_concurrent_readiness_wait_can_be_canceled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    database = _database(tmp_path)
    try:
        runtime = _runtime(RepositoryIntelligenceRepository(database))
        active = runtime.activate_workspace(root)
        original_build = active.application.build
        entered = threading.Event()
        release = threading.Event()

        def blocked_build(**kwargs: object):
            entered.set()
            assert release.wait(2)
            return original_build(**kwargs)

        monkeypatch.setattr(active.application, "build", blocked_build)
        owner = threading.Thread(target=lambda: runtime.ensure_ready(root))
        owner.start()
        assert entered.wait(1)
        cancel = threading.Event()
        waiter_returned = threading.Event()

        def wait_for_readiness() -> None:
            runtime.ensure_ready(root, cancel=cancel)
            waiter_returned.set()

        waiter = threading.Thread(target=wait_for_readiness)
        waiter.start()
        cancel.set()

        assert waiter_returned.wait(1)
        release.set()
        owner.join(2)
        waiter.join(2)
        runtime.shutdown_all()
    finally:
        database.close()


def test_incomplete_candidate_keeps_previous_complete_generation_active(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "main.py").write_text("value = 1\n", encoding="utf-8")
    database = _database(tmp_path)
    try:
        runtime = _runtime(RepositoryIntelligenceRepository(database))
        active = runtime.ensure_ready(root)
        previous = active.snapshot
        assert previous is not None
        for index in range(3):
            (root / f"extra-{index}.txt").write_text("x\n", encoding="utf-8")
        active.application.inventory_builder.max_entries = 1
        active.invalidate((RepositoryChange(path="extra-0.txt", change="added"),))

        runtime.ensure_ready(root)

        assert active.snapshot is previous
        assert active.reconciliation_required is True
        runtime.shutdown_all()
    finally:
        database.close()


def test_same_workspace_and_multiple_sessions_share_one_active_state_and_watcher(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    database = _database(tmp_path)
    try:
        runtime = _runtime(RepositoryIntelligenceRepository(database))

        first_session_state = runtime.activate_workspace(root)
        second_session_state = runtime.activate_workspace(root)

        assert first_session_state is second_session_state
        assert runtime.get_active(root) is first_session_state
        assert len(_BlockingWatchController.instances) == 1
        runtime.shutdown_all()
    finally:
        database.close()


def test_invalidation_coalesces_paths_without_replacing_active_generation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "main.py").write_text("value = 1\n", encoding="utf-8")
    database = _database(tmp_path)
    try:
        repository = RepositoryIntelligenceRepository(database)
        built = RepositoryApplication(root, repository=repository).build()
        runtime = _runtime(repository)
        active = runtime.activate_workspace(root)
        original_snapshot = active.snapshot
        original_epoch = active.invalidation_epoch

        watcher = _BlockingWatchController.instances[0]
        watcher.emit(
            RepositoryChange(path="foo.py", change="modified"),
            RepositoryChange(path="foo.py", change="modified"),
        )
        watcher.emit(RepositoryChange(path="deleted.py", change="deleted"))

        assert active.dirty_paths == frozenset({"foo.py", "deleted.py"})
        assert active.invalidation_epoch == original_epoch + 2
        assert active.reconciliation_required is True
        assert active.snapshot is original_snapshot
        assert active.snapshot is not None
        assert active.snapshot.inventory.generation == built.inventory.generation
        runtime.shutdown_all()
    finally:
        database.close()


def test_changed_workspace_identity_stops_old_watcher_and_creates_new_state(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    database = _database(tmp_path)
    try:
        runtime = _runtime(RepositoryIntelligenceRepository(database))
        old_state = runtime.activate_workspace(root)
        old_watcher = _BlockingWatchController.instances[0]
        retired = tmp_path / "retired"
        root.rename(retired)
        root.mkdir()

        new_state = runtime.activate_workspace(root)

        assert new_state is not old_state
        assert new_state.workspace_identity != old_state.workspace_identity
        assert old_state.closed is True
        assert old_watcher.stopped.wait(1)
        assert len(_BlockingWatchController.instances) == 2
        runtime.shutdown_all()
    finally:
        database.close()


def test_workspace_shutdown_stops_watcher_and_rejects_late_invalidation(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    database = _database(tmp_path)
    try:
        runtime = _runtime(RepositoryIntelligenceRepository(database))
        active = runtime.activate_workspace(root)
        watcher = _BlockingWatchController.instances[0]
        assert watcher.started.wait(1)

        runtime.shutdown_workspace(root)
        active.invalidate((RepositoryChange(path="late.py", change="added"),))

        assert watcher.stopped.wait(1)
        assert active.closed is True
        assert active.dirty_paths == frozenset()
        assert runtime.get_active(root) is None
    finally:
        database.close()


def test_runtime_shutdown_stops_all_workspace_watchers(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    database = _database(tmp_path)
    try:
        runtime = _runtime(RepositoryIntelligenceRepository(database))
        runtime.activate_workspace(first)
        runtime.activate_workspace(second)
        watchers = tuple(_BlockingWatchController.instances)
        assert all(watcher.started.wait(1) for watcher in watchers)

        runtime.shutdown_all()

        assert all(watcher.stopped.wait(1) for watcher in watchers)
        assert runtime.get_active(first) is None
        assert runtime.get_active(second) is None
    finally:
        database.close()


def test_restart_restores_latest_complete_generation_and_marks_cold_start_stale(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    source = root / "main.py"
    source.write_text("value = 1\n", encoding="utf-8")
    database = _database(tmp_path)
    try:
        repository = RepositoryIntelligenceRepository(database)
        built = RepositoryApplication(root, repository=repository).build()

        clean_restart = _runtime(repository)
        clean_state = clean_restart.activate_workspace(root)
        assert clean_state.snapshot is not None
        assert clean_state.snapshot.inventory.generation == built.inventory.generation
        assert clean_state.reconciliation_required is True
        assert clean_state.dirty_paths == frozenset()
        clean_restart.ensure_ready(root)
        assert clean_state.snapshot is not None
        assert clean_state.snapshot.inventory.generation == built.inventory.generation + 1
        assert clean_state.reconciliation_required is False
        clean_restart.shutdown_all()

        source.write_text("value = 2\n", encoding="utf-8")
        changed_restart = _runtime(repository)
        changed_state = changed_restart.activate_workspace(root)
        assert changed_state.snapshot is not None
        assert (
            changed_state.snapshot.inventory.generation
            == built.inventory.generation + 1
        )
        assert changed_state.reconciliation_required is True
        changed_restart.shutdown_all()
    finally:
        database.close()


def test_offline_new_file_does_not_produce_a_false_clean_guarantee(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "known.py").write_text("known = True\n", encoding="utf-8")
    database = _database(tmp_path)
    try:
        repository = RepositoryIntelligenceRepository(database)
        RepositoryApplication(root, repository=repository).build()
        (root / "new_while_offline.py").write_text("new = True\n", encoding="utf-8")

        runtime = _runtime(repository)
        active = runtime.activate_workspace(root)

        assert active.snapshot is not None
        assert "new_while_offline.py" not in {
            record.path for record in active.snapshot.inventory.files
        }
        assert active.reconciliation_required is True
        runtime.shutdown_all()
    finally:
        database.close()


def test_runtime_engine_ensures_workspace_active_before_model_execution(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    store = SessionStore(tmp_path / "runtime-data")
    store.initialize()
    repository_runtime = _runtime(store.repository_intelligence_repository())
    try:
        session = store.create_session(str(root))
        run, _item = store.create_run(session["id"], "inspect")

        RuntimeEngine(
            store,
            ScriptedModel([ModelResponse(text="done")]),
            lambda _message: None,
            repository_runtime=repository_runtime,
        ).run(run["id"], threading.Event())

        assert repository_runtime.get_active(root) is not None
        assert len(_BlockingWatchController.instances) == 1
    finally:
        repository_runtime.shutdown_all()
        store.close()


def test_runtime_engine_captures_repository_once_for_multi_step_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "main.py").write_text("value = 1\n", encoding="utf-8")
    store = SessionStore(tmp_path / "runtime-data")
    store.initialize()
    repository_runtime = _runtime(store.repository_intelligence_repository())
    try:
        session = store.create_session(str(root))
        run, _item = store.create_run(session["id"], "inspect")
        active = repository_runtime.activate_workspace(root)
        original_build = active.application.build
        build_count = 0

        def counted_build(**kwargs: object):
            nonlocal build_count
            build_count += 1
            return original_build(**kwargs)

        monkeypatch.setattr(active.application, "build", counted_build)
        model = ScriptedModel([
            ModelResponse(tool_calls=(
                ModelToolCall("list", "list_files", {}),
            )),
            ModelResponse(text="done"),
        ])
        original_complete = model.complete

        def complete_with_invalidation(*args: object, **kwargs: object):
            response = original_complete(*args, **kwargs)
            if len(model.contexts) == 1:
                _BlockingWatchController.instances[0].emit(
                    RepositoryChange(path="agent-write.py", change="added")
                )
            return response

        monkeypatch.setattr(model, "complete", complete_with_invalidation)
        RuntimeEngine(
            store,
            model,
            lambda _message: None,
            repository_runtime=repository_runtime,
        ).run(run["id"], threading.Event())

        assert len(model.contexts) == 2
        assert build_count == 1
        assert active.snapshot is not None
        assert active.snapshot.inventory.generation == 1
        assert active.reconciliation_required is True
        assert active.dirty_paths == frozenset({"agent-write.py"})
    finally:
        repository_runtime.shutdown_all()
        store.close()


def test_repeated_session_reopen_reuses_one_active_state_and_watcher(
    tmp_path: Path,
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    store = SessionStore(tmp_path / "runtime-data")
    store.initialize()
    repository_runtime = _runtime(store.repository_intelligence_repository())
    try:
        session = store.create_session(str(root))
        application = SessionApplication(
            store,
            scan_text=lambda value: value,
            repository_runtime=repository_runtime,
        )

        application.read_snapshot(SessionReadRequestDto(sessionId=session["id"]))
        first = repository_runtime.get_active(root)
        application.read_snapshot(SessionReadRequestDto(sessionId=session["id"]))

        assert first is not None
        assert repository_runtime.get_active(root) is first
        assert len(_BlockingWatchController.instances) == 1
    finally:
        repository_runtime.shutdown_all()
        store.close()

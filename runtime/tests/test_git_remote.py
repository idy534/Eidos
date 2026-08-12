from __future__ import annotations

from pathlib import Path
import sqlite3
import subprocess
import threading
import uuid

import pytest
from pydantic import ValidationError

from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.application.sessions import SessionApplication
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.git import DulwichGitBackend, WorktreeManager
from eidos_runtime.git.errors import (
    GitCommandFailedError,
    GitCommandTimeoutError,
    GitRemoteCanceledError,
    GitRemoteUnsupportedError,
)
from eidos_runtime.git.native import GitCli, HardenedGitRunner
from eidos_runtime.protocol.methods import (
    SessionCreateRequestDto,
    SessionGitFetchRequestDto,
    SessionGitRemoteStatusRequestDto,
)


def _git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )
    return completed.stdout.strip()


def _commit(repository: Path, path: str, content: str, message: str) -> str:
    (repository / path).write_text(content, encoding="utf-8")
    _git(repository, "add", "--", path)
    _git(repository, "commit", "-qm", message)
    return _git(repository, "rev-parse", "HEAD")


def _remote_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    remote = tmp_path / "remote.git"
    remote.mkdir()
    _git(remote, "init", "--bare", "-q")
    repo_a = tmp_path / "repo-a"
    repo_a.mkdir()
    _git(repo_a, "init", "-q", "-b", "main")
    _git(repo_a, "config", "user.name", "Eidos Tests")
    _git(repo_a, "config", "user.email", "eidos-tests@example.com")
    _commit(repo_a, "tracked.txt", "base\n", "initial")
    _git(repo_a, "remote", "add", "origin", str(remote))
    _git(repo_a, "push", "-qu", "origin", "main")
    repo_b = tmp_path / "repo-b"
    subprocess.run(
        ["git", "clone", "-q", str(remote), str(repo_b)], check=True
    )
    _git(repo_b, "config", "user.name", "Eidos Tests")
    _git(repo_b, "config", "user.email", "eidos-tests@example.com")
    return remote, repo_a, repo_b


def _application(
    tmp_path: Path,
) -> tuple[SessionStore, WorktreeManager, SessionApplication]:
    store = SessionStore(tmp_path / "data")
    store.initialize()
    manager = WorktreeManager(
        store.database, managed_root=tmp_path / "managed-worktrees"
    )
    return store, manager, SessionApplication(
        store, scan_text=lambda value: value, worktree_manager=manager
    )


def test_remote_status_observes_no_remote_upstream_and_divergence(
    tmp_path: Path,
) -> None:
    _remote, repo_a, repo_b = _remote_fixture(tmp_path)
    backend = DulwichGitBackend()
    initial = backend.remote_status(repo_a)
    assert initial.branch == "main"
    assert [remote.name for remote in initial.remotes] == ["origin"]
    assert initial.upstream is not None
    assert initial.upstream.remote == "origin"
    assert initial.upstream.branch == "main"
    assert (initial.ahead, initial.behind) == (0, 0)

    _commit(repo_a, "local.txt", "local\n", "local ahead")
    _commit(repo_b, "remote.txt", "remote\n", "remote ahead")
    _git(repo_b, "push", "-q", "origin", "main")
    backend.fetch(repo_a, "origin", cancel=threading.Event())
    diverged = backend.remote_status(repo_a)
    assert (diverged.ahead, diverged.behind) == (1, 1)

    _git(repo_a, "branch", "--unset-upstream")
    no_upstream = backend.remote_status(repo_a)
    assert no_upstream.upstream is None
    assert no_upstream.ahead is None
    assert no_upstream.behind is None

    _git(repo_a, "remote", "remove", "origin")
    assert backend.remote_status(repo_a).remotes == ()


def test_fetch_request_requires_canonical_operation_identity() -> None:
    session_id = str(uuid.uuid4())
    with pytest.raises(ValidationError):
        SessionGitFetchRequestDto(sessionId=session_id)
    with pytest.raises(ValidationError):
        SessionGitFetchRequestDto(
            operationId="not-canonical", sessionId=session_id
        )


def test_fetch_updates_only_remote_refs_and_reobserves_state(tmp_path: Path) -> None:
    _remote, repo_a, repo_b = _remote_fixture(tmp_path)
    new_remote_head = _commit(repo_b, "remote.txt", "remote\n", "remote commit")
    _git(repo_b, "push", "-q", "origin", "main")
    backend = DulwichGitBackend()
    head_before = backend.head(repo_a)
    status_before = backend.status(repo_a)
    index_before = _git(repo_a, "write-tree")

    fetched = backend.fetch(repo_a, "origin", cancel=threading.Event())

    assert _git(repo_a, "rev-parse", "refs/remotes/origin/main") == new_remote_head
    assert backend.head(repo_a) == head_before
    assert backend.status(repo_a) == status_before
    assert _git(repo_a, "write-tree") == index_before
    assert fetched.branch == "main"
    assert (fetched.ahead, fetched.behind) == (0, 1)


def test_fetch_rejects_unsupported_remote_transport(tmp_path: Path) -> None:
    _remote, repo_a, _repo_b = _remote_fixture(tmp_path)
    _git(repo_a, "remote", "set-url", "origin", "ext::sh -c echo")
    with pytest.raises(GitRemoteUnsupportedError):
        DulwichGitBackend().fetch(repo_a, "origin", cancel=threading.Event())

    store, _manager, application = _application(tmp_path)
    try:
        session = application.create(
            SessionCreateRequestDto(
                workspaceRoot=str(repo_a), executionMode="local"
            )
        ).root
        operation_id = str(uuid.uuid4())
        with pytest.raises(ApplicationError) as unsupported:
            application.prepare_git_fetch(
                SessionGitFetchRequestDto(
                    operationId=operation_id, sessionId=session["id"]
                ),
                request_id="client-unsupported",
            )
        assert unsupported.value.code == "GIT_REMOTE_UNSUPPORTED"
        connection = store.connection
        assert connection is not None
        assert connection.execute(
            "SELECT COUNT(*) FROM operations WHERE id = ?", (operation_id,)
        ).fetchone()[0] == 0
    finally:
        store.close()


def test_fetch_uses_controlled_user_url_instead_of_config(tmp_path: Path) -> None:
    remote, repo_a, repo_b = _remote_fixture(tmp_path)
    new_head = _commit(repo_b, "alias.txt", "alias\n", "alias commit")
    _git(repo_b, "push", "-q", "origin", "main")
    _git(repo_a, "remote", "set-url", "origin", "eidos-remote")
    user_home = tmp_path / "url-home"
    user_home.mkdir()
    (user_home / ".gitconfig").write_text(
        f"[url \"{remote}\"]\n\tinsteadOf = eidos-remote\n",
        encoding="utf-8",
    )
    backend = DulwichGitBackend(
        git_cli=GitCli(runner=HardenedGitRunner(user_home=user_home))
    )

    backend.fetch(repo_a, "origin", cancel=threading.Event())

    assert _git(repo_a, "rev-parse", "refs/remotes/origin/main") == new_head


def test_remote_status_preserves_configured_upstream_until_fetch_restores_ref(
    tmp_path: Path,
) -> None:
    _remote, repo_a, _repo_b = _remote_fixture(tmp_path)
    _git(repo_a, "update-ref", "-d", "refs/remotes/origin/main")
    store, _manager, application = _application(tmp_path)
    try:
        session = application.create(
            SessionCreateRequestDto(
                workspaceRoot=str(repo_a), executionMode="local"
            )
        ).root
        missing = application.git_remote_status(
            SessionGitRemoteStatusRequestDto(sessionId=session["id"])
        ).root
        assert missing["upstream"] == {"remote": "origin", "branch": "main"}
        assert missing["ahead"] is None
        assert missing["behind"] is None

        prepared = application.prepare_git_fetch(
            SessionGitFetchRequestDto(
                operationId=str(uuid.uuid4()), sessionId=session["id"]
            ),
            request_id="client-restore-upstream",
        )
        fetched = prepared.run(threading.Event()).root
        assert _git(repo_a, "rev-parse", "refs/remotes/origin/main")
        assert fetched["upstream"] == {"remote": "origin", "branch": "main"}
        assert fetched["ahead"] == 0
        assert fetched["behind"] == 0
    finally:
        store.close()


def test_fetch_reservation_rolls_back_both_operation_rows_on_async_insert_failure(
    tmp_path: Path,
) -> None:
    _remote, repo_a, _repo_b = _remote_fixture(tmp_path)
    store, _manager, application = _application(tmp_path)
    try:
        session = application.create(
            SessionCreateRequestDto(
                workspaceRoot=str(repo_a), executionMode="local"
            )
        ).root
        operation_id = str(uuid.uuid4())
        connection = store.connection
        assert connection is not None
        connection.execute(
            """
            CREATE TEMP TRIGGER fail_deferred_git_fetch_reservation
            BEFORE INSERT ON async_operations
            BEGIN
                SELECT RAISE(ABORT, 'injected async reservation failure');
            END
            """
        )

        with pytest.raises(sqlite3.IntegrityError):
            application.prepare_git_fetch(
                SessionGitFetchRequestDto(
                    operationId=operation_id, sessionId=session["id"]
                ),
                request_id="client-reservation-failure",
            )

        assert connection.execute(
            "SELECT COUNT(*) FROM operations WHERE id = ?", (operation_id,)
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM async_operations WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()[0] == 0
    finally:
        store.close()


def test_session_fetch_selects_remote_and_replays_operation(tmp_path: Path) -> None:
    _remote, repo_a, repo_b = _remote_fixture(tmp_path)
    store, _manager, application = _application(tmp_path)
    try:
        session = application.create(
            SessionCreateRequestDto(
                workspaceRoot=str(repo_a), executionMode="local"
            )
        ).root
        _commit(repo_b, "remote.txt", "remote\n", "remote commit")
        _git(repo_b, "push", "-q", "origin", "main")
        backup = tmp_path / "backup.git"
        backup.mkdir()
        _git(backup, "init", "--bare", "-q")
        _git(repo_a, "remote", "add", "backup", str(backup))
        request = SessionGitFetchRequestDto(
            operationId=str(uuid.uuid4()), sessionId=session["id"]
        )
        prepared = application.prepare_git_fetch(request, request_id="client-fetch")
        first = prepared.run(threading.Event()).root
        assert first["remote"] == "origin"
        assert first["behind"] == 1

        replay = application.prepare_git_fetch(request, request_id="client-replay")
        assert replay.root == first

        with pytest.raises(ApplicationError) as reused:
            application.prepare_git_fetch(
                SessionGitFetchRequestDto(
                    operationId=request.operation_id,
                    sessionId=session["id"],
                    remote="other",
                ),
                request_id="client-conflict",
            )
        assert reused.value.code == "OPERATION_ID_REUSED"
    finally:
        store.close()


def test_fetch_remote_selection_and_pure_preflight_failures(tmp_path: Path) -> None:
    _remote, repo_a, _repo_b = _remote_fixture(tmp_path)
    store, _manager, application = _application(tmp_path)
    try:
        session = application.create(
            SessionCreateRequestDto(
                workspaceRoot=str(repo_a), executionMode="local"
            )
        ).root
        _git(repo_a, "branch", "--unset-upstream")
        status = application.git_remote_status(
            SessionGitRemoteStatusRequestDto(sessionId=session["id"])
        ).root
        assert status["upstream"] is None

        fallback = application.prepare_git_fetch(
            SessionGitFetchRequestDto(
                operationId=str(uuid.uuid4()), sessionId=session["id"]
            ),
            request_id="client-fallback",
        )
        assert fallback.run(threading.Event()).root["remote"] == "origin"

        _git(repo_a, "remote", "add", "backup", str(tmp_path / "backup.git"))
        operation_id = str(uuid.uuid4())
        with pytest.raises(ApplicationError) as required:
            application.prepare_git_fetch(
                SessionGitFetchRequestDto(
                    operationId=operation_id, sessionId=session["id"]
                ),
                request_id="client-fetch",
            )
        assert required.value.code == "GIT_REMOTE_REQUIRED"
        connection = store.connection
        assert connection is not None
        assert connection.execute(
            "SELECT COUNT(*) FROM operations WHERE id = ?", (operation_id,)
        ).fetchone()[0] == 0

        with pytest.raises(ApplicationError) as missing:
            application.prepare_git_fetch(
                SessionGitFetchRequestDto(
                    operationId=str(uuid.uuid4()),
                    sessionId=session["id"],
                    remote="missing",
                ),
                request_id="client-missing",
            )
        assert missing.value.code == "GIT_REMOTE_NOT_FOUND"
    finally:
        store.close()


def test_fetch_rejects_active_run_and_uncertain_operation_without_git(
    tmp_path: Path,
) -> None:
    _remote, repo_a, _repo_b = _remote_fixture(tmp_path)
    store, _manager, application = _application(tmp_path)
    try:
        session = application.create(
            SessionCreateRequestDto(
                workspaceRoot=str(repo_a), executionMode="local"
            )
        ).root
        busy_operation = str(uuid.uuid4())
        store.create_run(session["id"], "active")
        with pytest.raises(ApplicationError) as busy:
            application.prepare_git_fetch(
                SessionGitFetchRequestDto(
                    operationId=busy_operation, sessionId=session["id"]
                ),
                request_id="client-busy",
            )
        assert busy.value.code == "GIT_WORKFLOW_BUSY"
        connection = store.connection
        assert connection is not None
        assert connection.execute(
            "SELECT COUNT(*) FROM operations WHERE id = ?", (busy_operation,)
        ).fetchone()[0] == 0

        store.fail_run(
            connection.execute(
                "SELECT id FROM runs WHERE session_id = ?", (session["id"],)
            ).fetchone()[0],
            "fixture",
        )
        uncertain = str(uuid.uuid4())
        operation_request = {"sessionId": session["id"]}
        store.prepare_operation(
            uncertain, "session/gitFetch", operation_request
        )
        remote_ref = _git(repo_a, "rev-parse", "refs/remotes/origin/main")
        with pytest.raises(ApplicationError) as in_progress:
            application.prepare_git_fetch(
                SessionGitFetchRequestDto(
                    operationId=uncertain, sessionId=session["id"]
                ),
                request_id="client-uncertain",
            )
        assert in_progress.value.code == "OPERATION_IN_PROGRESS"
        assert _git(repo_a, "rev-parse", "refs/remotes/origin/main") == remote_ref
    finally:
        store.close()


@pytest.mark.parametrize(
    ("failure", "code"),
    [
        (GitCommandTimeoutError("fetch"), "GIT_REMOTE_TIMEOUT"),
        (GitRemoteCanceledError(), "GIT_REMOTE_CANCELED"),
        (
            GitCommandFailedError("fetch", returncode=1, stderr="secret URL"),
            "GIT_REMOTE_FAILED",
        ),
    ],
)
def test_fetch_maps_remote_failures_without_exposing_stderr(
    tmp_path: Path, failure: Exception, code: str
) -> None:
    _remote, repo_a, _repo_b = _remote_fixture(tmp_path)
    store, manager, application = _application(tmp_path)
    try:
        session = application.create(
            SessionCreateRequestDto(
                workspaceRoot=str(repo_a), executionMode="local"
            )
        ).root
        prepared = application.prepare_git_fetch(
            SessionGitFetchRequestDto(
                operationId=str(uuid.uuid4()), sessionId=session["id"]
            ),
            request_id="client-failure",
        )

        def fail_fetch(*_args, **_kwargs):
            raise failure

        manager.git.fetch = fail_fetch  # type: ignore[method-assign]
        with pytest.raises(ApplicationError) as mapped:
            prepared.run(threading.Event())
        assert mapped.value.code == code
        assert "secret URL" not in str(mapped.value)
    finally:
        store.close()

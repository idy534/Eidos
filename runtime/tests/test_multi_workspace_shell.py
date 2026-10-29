from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import threading

import pytest

from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel
from eidos_runtime.protocol.methods import SessionCreateRequestDto
from eidos_runtime.runtime.engine import RuntimeEngine
from eidos_runtime.runtime.resource_registry import ResourceRegistry
from eidos_runtime.sandbox.seatbelt import is_seatbelt_usable
from test_multi_workspace_execution import _create_project, _repository, _runtime


pytestmark = pytest.mark.integration


def _result(store, run_id: str, call_id: str = "shell"):
    row = store.connection.execute(
        "SELECT t.result_json FROM tool_calls t JOIN items i ON i.id=t.item_id "
        "WHERE i.run_id=? AND t.provider_call_id=?",
        (run_id, call_id),
    ).fetchone()
    assert row is not None
    return json.loads(row[0])


def _run(
    store,
    session_id: str,
    arguments: dict,
    mode: str = "manual",
    *,
    warmup: bool = True,
    approval=None,
):
    run, _ = store.create_run(session_id, "check Shell", approval_mode=mode)
    model = ScriptedModel(
        [
            *(
                [
                    ModelResponse(
                        tool_calls=(
                            ModelToolCall(
                                "baseline",
                                "run_shell",
                                {"command": ":", "yieldTimeMs": 250},
                            ),
                        )
                    )
                ]
                if warmup
                else []
            ),
            ModelResponse(tool_calls=(ModelToolCall("shell", "run_shell", arguments),)),
            ModelResponse(text="Checked"),
        ]
    )
    RuntimeEngine(
        store, model, lambda _: None, request_approval=approval, shell_available=True
    ).run(run["id"], threading.Event())
    return run["id"], _result(store, run["id"])


@pytest.mark.parametrize("mode", ["manual", "auto_review", "full_access"])
@pytest.mark.parametrize("execution", ["local", "worktree"])
def test_real_run_shell_can_use_a_secondary_directory_as_cwd(
    tmp_path: Path, mode: str, execution: str
) -> None:
    if mode != "full_access" and not is_seatbelt_usable():
        pytest.skip("native Seatbelt unavailable")
    with tempfile.TemporaryDirectory(
        prefix=".eidos-shell-multi-", dir=Path.home()
    ) as temporary:
        root = Path(temporary).resolve()
        primary = _repository(root / "primary", "primary\n")
        secondary = root / "secondary"
        secondary.mkdir()
        store, _manager, projects, sessions = _runtime(tmp_path)
        try:
            project = _create_project(
                projects, [("primary", primary), ("secondary", secondary)], "primary"
            )
            session = sessions.create(
                SessionCreateRequestDto(projectId=project.id, executionMode=execution)
            ).root
            run_id, result = _run(
                store,
                session["id"],
                {
                    "command": "pwd; printf secondary > marker.txt",
                    "cwd": str(secondary),
                },
                mode,
            )
            assert result["code"] == "ok", result
            assert result["data"]["exitCode"] == 0
            assert result["data"]["stdout"].strip() == str(secondary)
            assert (secondary / "marker.txt").read_text() == "secondary"
            assert not (primary / "marker.txt").exists()
            assert str(secondary / "marker.txt") in result["data"]["created"]
            assert result["data"]["workspaceChanged"]
            assert (
                store.connection.execute(
                    "SELECT count(*) FROM approvals WHERE run_id=?", (run_id,)
                ).fetchone()[0]
                == 0
            )
        finally:
            store.close()


def test_real_run_shell_cannot_use_another_projects_private_directory(
    tmp_path: Path,
) -> None:
    if not is_seatbelt_usable():
        pytest.skip("native Seatbelt unavailable")
    with tempfile.TemporaryDirectory(
        prefix=".eidos-shell-scope-", dir=Path.home()
    ) as temporary:
        root = Path(temporary).resolve()
        primary, attached, other = (
            root / name for name in ("primary", "attached", "other")
        )
        for path in (primary, attached, other):
            path.mkdir()
        store, _manager, projects, sessions = _runtime(tmp_path)
        try:
            first = _create_project(
                projects,
                [("first-main", primary), ("attached", attached)],
                "first-main",
            )
            _create_project(
                projects,
                [("other-main", primary), ("other-private", other)],
                "other-main",
            )
            session = sessions.create(SessionCreateRequestDto(projectId=first.id)).root
            _run_id, result = _run(
                store, session["id"], {"command": "touch forbidden", "cwd": str(other)}
            )
            assert result["outcome"] == "error", result
            assert not (other / "forbidden").exists()
            _run_id, result = _run(
                store,
                session["id"],
                {"command": f"touch {shlex.quote(str(other / 'also-forbidden'))}"},
            )
            assert result["data"]["exitCode"] != 0, result
            assert not (other / "also-forbidden").exists()
        finally:
            store.close()


@pytest.mark.parametrize("primary_has_git", [False, True])
def test_git_write_request_targets_the_repository_at_shell_cwd(
    tmp_path: Path, primary_has_git: bool
) -> None:
    from eidos_runtime.runtime.approval import ApprovalDecision
    from test_multi_workspace_execution import _git

    if not is_seatbelt_usable():
        pytest.skip("native Seatbelt unavailable")
    with tempfile.TemporaryDirectory(
        prefix=".eidos-shell-git-", dir=Path.home()
    ) as temporary:
        root = Path(temporary).resolve()
        primary = root / "primary"
        if primary_has_git:
            _repository(primary, "primary\n")
        else:
            primary.mkdir()
        secondary = _repository(root / "secondary", "secondary\n")
        (secondary / "repository-marker.txt").write_text("stage secondary\n")
        store, _manager, projects, sessions = _runtime(tmp_path)
        approvals = []

        def approve(request, _cancel):
            approvals.append(request)
            return ApprovalDecision("approve")

        try:
            project = _create_project(
                projects, [("primary", primary), ("secondary", secondary)], "primary"
            )
            session = sessions.create(
                SessionCreateRequestDto(projectId=project.id)
            ).root
            run_id, result = _run(
                store,
                session["id"],
                {
                    "command": "git add repository-marker.txt",
                    "cwd": str(secondary),
                    "gitWriteAccess": "request",
                    "justification": "Stage the requested changes in the secondary repository.",
                },
                approval=approve,
            )
            assert result["code"] == "ok", result
            assert result["data"]["exitCode"] == 0, result
            assert (
                _git(secondary, "diff", "--cached", "--name-only")
                == "repository-marker.txt"
            )
            if primary_has_git:
                assert not _git(primary, "diff", "--cached", "--name-only")
            assert len(approvals) == 1
            saved = json.loads(
                store.connection.execute(
                    "SELECT request_json FROM approvals WHERE run_id=?", (run_id,)
                ).fetchone()[0]
            )
            writes = {
                entry["path"]
                for entry in saved["additionalFileSystemAccess"]
                if entry["access"] == "write"
            }
            assert writes == {str(secondary / ".git")}
            assert saved["cwd"] == str(secondary)
        finally:
            store.close()


def test_default_cwd_stays_primary_and_secondary_git_metadata_stays_protected(
    tmp_path: Path,
) -> None:
    if not is_seatbelt_usable():
        pytest.skip("native Seatbelt unavailable")
    with tempfile.TemporaryDirectory(
        prefix=".eidos-shell-default-", dir=Path.home()
    ) as temporary:
        root = Path(temporary).resolve()
        primary = _repository(root / "primary", "primary\n")
        secondary = _repository(root / "secondary", "secondary\n")
        store, _manager, projects, sessions = _runtime(tmp_path)
        try:
            project = _create_project(
                projects, [("primary", primary), ("secondary", secondary)], "primary"
            )
            session = sessions.create(
                SessionCreateRequestDto(projectId=project.id)
            ).root
            _run_id, result = _run(
                store,
                session["id"],
                {
                    "command": f"pwd; printf primary > main.txt; printf secondary > {shlex.quote(str(secondary / 'second.txt'))}",
                },
            )
            assert result["data"]["stdout"].strip() == str(primary), result
            assert (primary / "main.txt").read_text() == "primary"
            assert (secondary / "second.txt").read_text() == "secondary"
            assert {"main.txt", str(secondary / "second.txt")} <= set(
                result["data"]["created"]
            )
            _run_id, denied = _run(
                store,
                session["id"],
                {"command": "touch .git/forbidden", "cwd": str(secondary)},
            )
            assert denied["data"]["exitCode"] != 0, denied
            assert not (secondary / ".git" / "forbidden").exists()
        finally:
            store.close()


@pytest.mark.parametrize("execution", ["local", "worktree"])
def test_shell_cwd_maps_primary_and_same_checkout_roots_but_shares_another_repo(
    tmp_path: Path, execution: str
) -> None:
    from eidos_runtime.runtime.approval import ApprovalDecision
    from test_multi_workspace_execution import _git
    from eidos_runtime.application.projects import ProjectApplication
    from eidos_runtime.application.sessions import SessionApplication
    from eidos_runtime.db.storage import SessionStore
    from eidos_runtime.git import WorktreeManager

    if not is_seatbelt_usable():
        pytest.skip("native Seatbelt unavailable")
    with tempfile.TemporaryDirectory(
        prefix=".eidos-shell-worktree-", dir=Path.home()
    ) as temporary:
        source = _repository(Path(temporary).resolve() / "source", "primary\n")
        for name in ("src", "docs"):
            (source / name).mkdir()
            (source / name / "file.txt").write_text(name)
        _git(source, "add", ".")
        _git(source, "commit", "-qm", "mounted directories")
        shared = _repository(Path(temporary).resolve() / "shared", "shared\n")
        store = SessionStore(tmp_path / "data")
        store.initialize()
        manager = WorktreeManager(store.database)
        projects = ProjectApplication(manager.repository)
        sessions = SessionApplication(
            store, scan_text=lambda value: value, worktree_manager=manager
        )
        try:
            project = _create_project(
                projects,
                [
                    ("src", source / "src"),
                    ("docs", source / "docs"),
                    ("shared", shared),
                ],
                "src",
            )
            session = sessions.create(
                SessionCreateRequestDto(projectId=project.id, executionMode=execution)
            ).root
            checkout = (
                Path(session["worktree"]["worktreeRoot"])
                if execution == "worktree"
                else source
            )
            _run_id, result = _run(
                store,
                session["id"],
                {
                    "command": "pwd; printf docs > changed.txt",
                    "cwd": str(checkout / "docs"),
                },
            )
            assert result["code"] == "ok", result
            assert result["data"]["stdout"].strip() == str(checkout / "docs")
            assert (checkout / "docs" / "changed.txt").read_text() == "docs"
            assert (source / "docs" / "changed.txt").exists() == (execution == "local")
            _run_id, result = _run(
                store, session["id"], {"command": "pwd; printf src > default.txt"}
            )
            assert result["data"]["stdout"].strip() == str(checkout / "src"), result
            assert (checkout / "src" / "default.txt").exists()
            assert (source / "src" / "default.txt").exists() == (execution == "local")
            _run_id, result = _run(
                store,
                session["id"],
                {"command": "printf shared > shared.txt", "cwd": str(shared)},
            )
            assert result["code"] == "ok", result
            assert (shared / "shared.txt").read_text() == "shared"
            _run_id, result = _run(
                store, session["id"], {"command": "git rev-parse --show-toplevel"}
            )
            assert result["code"] == "ok", result
            assert result["data"]["stdout"].strip() == str(checkout)
            _run_id, denied = _run(
                store, session["id"], {"command": "touch ../unmounted.txt"}
            )
            assert denied["data"]["exitCode"] != 0, denied
            assert not (checkout / "unmounted.txt").exists()
            secret = store.data_directory / "private-state.txt"
            secret.write_text("Eidos private state")
            _run_id, denied = _run(
                store, session["id"], {"command": f"cat {shlex.quote(str(secret))}"}
            )
            assert denied["data"]["exitCode"] != 0, denied
            assert "Eidos private state" not in denied["data"]["stdout"]
            if execution == "worktree":
                _run_id, denied = _run(
                    store,
                    session["id"],
                    {"command": "cat ../repository-marker.txt"},
                )
                assert denied["data"]["exitCode"] != 0, denied
                _run_id, denied = _run(
                    store,
                    session["id"],
                    {"command": "ls ../.."},
                )
                assert denied["data"]["exitCode"] != 0, denied
            _run_id, status = _run(
                store,
                session["id"],
                {"command": "git status --short -- ."},
            )
            assert status["code"] == "ok", status
            assert "default.txt" in status["data"]["stdout"]
            _run_id, denied = _run(
                store, session["id"], {"command": "git add file.txt"}
            )
            assert denied["data"]["exitCode"] != 0, denied
            _run_id, staged = _run(
                store,
                session["id"],
                {
                    "command": "git add default.txt",
                    "gitWriteAccess": "request",
                    "justification": "Stage the new file in the mounted primary directory.",
                },
                approval=lambda _request, _cancel: ApprovalDecision("approve"),
            )
            assert staged["code"] == "ok", staged
            assert (
                _git(checkout, "diff", "--cached", "--name-only") == "src/default.txt"
            )
            if execution == "worktree":
                assert not _git(source, "diff", "--cached", "--name-only")
        finally:
            store.close()


def test_first_shell_can_start_in_secondary_subdirectory_without_a_complete_manifest(
    tmp_path: Path,
) -> None:
    if not is_seatbelt_usable():
        pytest.skip("native Seatbelt unavailable")
    with tempfile.TemporaryDirectory(
        prefix=".eidos-shell-first-", dir=Path.home()
    ) as temporary:
        root = Path(temporary).resolve()
        primary, secondary = root / "primary", root / "secondary"
        primary.mkdir()
        (secondary / "child").mkdir(parents=True)
        store, _manager, projects, sessions = _runtime(tmp_path)
        try:
            project = _create_project(
                projects, [("primary", primary), ("secondary", secondary)], "primary"
            )
            session = sessions.create(
                SessionCreateRequestDto(projectId=project.id)
            ).root
            run_id, result = _run(
                store,
                session["id"],
                {
                    "command": "pwd; printf first > first.txt",
                    "cwd": str(secondary / "child"),
                },
                warmup=False,
            )
            assert result["code"] == "ok", result
            assert result["data"]["stdout"].strip() == str(secondary / "child")
            assert (secondary / "child" / "first.txt").read_text() == "first"
            assert result["data"]["workspaceChangeState"] == "unknown"
            assert not result["data"]["workspaceManifestComplete"]
            assert result["data"]["workspaceDiffIncomplete"]
            assert store.read_run(run_id)["status"] == "succeeded"
        finally:
            store.close()


def test_secondary_shell_accepts_stdin_and_persists_terminal_changes(
    tmp_path: Path,
) -> None:
    if not is_seatbelt_usable():
        pytest.skip("native Seatbelt unavailable")
    with tempfile.TemporaryDirectory(
        prefix=".eidos-shell-input-", dir=Path.home()
    ) as temporary:
        root = Path(temporary).resolve()
        primary, secondary = root / "primary", root / "secondary"
        primary.mkdir()
        secondary.mkdir()
        store, _manager, projects, sessions = _runtime(tmp_path)
        resources = ResourceRegistry()
        try:
            project = _create_project(
                projects, [("primary", primary), ("secondary", secondary)], "primary"
            )
            session = sessions.create(
                SessionCreateRequestDto(projectId=project.id)
            ).root
            run, _ = store.create_run(session["id"], "write into secondary stdin")

            class InputModel(ScriptedModel):
                def complete(model, *args, **kwargs):
                    if model._index == 2:
                        running = _result(store, run["id"])
                        assert running["code"] == "shell_running", running
                        model.responses = (
                            *model.responses,
                            ModelResponse(
                                tool_calls=(
                                    ModelToolCall(
                                        "input",
                                        "write_stdin",
                                        {
                                            "sessionId": running["data"]["sessionId"],
                                            "chars": "secondary answer\n",
                                            "yieldTimeMs": 3000,
                                        },
                                    ),
                                )
                            ),
                        )
                    elif model._index == 3:
                        model.responses = (
                            *model.responses,
                            ModelResponse(text="Input completed"),
                        )
                    return super().complete(*args, **kwargs)

            model = InputModel(
                [
                    ModelResponse(
                        tool_calls=(
                            ModelToolCall(
                                "baseline",
                                "run_shell",
                                {"command": ":", "yieldTimeMs": 250},
                            ),
                        )
                    ),
                    ModelResponse(
                        tool_calls=(
                            ModelToolCall(
                                "shell",
                                "run_shell",
                                {
                                    "command": 'IFS= read -r line; printf "%s" "$line" > answer.txt; printf "%s\\n" "$line"',
                                    "cwd": str(secondary),
                                    "yieldTimeMs": 250,
                                },
                            ),
                        )
                    ),
                ]
            )
            RuntimeEngine(
                store,
                model,
                lambda _: None,
                shell_available=True,
                resource_registry=resources,
            ).run(run["id"], threading.Event())
            result = _result(store, run["id"])
            stdin = _result(store, run["id"], "input")
            assert stdin["code"] == "ok", stdin
            assert result["code"] == "ok", result
            assert result["data"]["executionStatus"] == "exited"
            assert result["data"]["stdout"] == "secondary answer\n"
            assert str(secondary / "answer.txt") in result["data"]["created"]
            assert (secondary / "answer.txt").read_text() == "secondary answer"
            assert not (primary / "answer.txt").exists()
            assert store.read_run(run["id"])["status"] == "succeeded"
            resources.ensure_empty()
        finally:
            store.close()


def test_cancel_secondary_shell_stops_its_process_and_does_not_replay_after_restart(
    tmp_path: Path,
) -> None:
    from eidos_runtime.db.storage import SessionStore

    if not is_seatbelt_usable():
        pytest.skip("native Seatbelt unavailable")
    with tempfile.TemporaryDirectory(
        prefix=".eidos-shell-cancel-", dir=Path.home()
    ) as temporary:
        root = Path(temporary).resolve()
        primary, secondary = root / "primary", root / "secondary"
        primary.mkdir()
        secondary.mkdir()
        store, _manager, projects, sessions = _runtime(tmp_path)
        resources = ResourceRegistry()
        cancel = threading.Event()
        try:
            project = _create_project(
                projects, [("primary", primary), ("secondary", secondary)], "primary"
            )
            session = sessions.create(
                SessionCreateRequestDto(projectId=project.id)
            ).root
            run, _ = store.create_run(session["id"], "cancel secondary shell")

            class CancelModel(ScriptedModel):
                def complete(model, *args, **kwargs):
                    if model._index == 2:
                        running = _result(store, run["id"])
                        assert running["code"] == "shell_running", running
                        assert (secondary / "pid.txt").is_file()
                        cancel.set()
                        return ModelResponse()
                    return super().complete(*args, **kwargs)

            model = CancelModel(
                [
                    ModelResponse(
                        tool_calls=(
                            ModelToolCall(
                                "baseline",
                                "run_shell",
                                {"command": ":", "yieldTimeMs": 250},
                            ),
                        )
                    ),
                    ModelResponse(
                        tool_calls=(
                            ModelToolCall(
                                "shell",
                                "run_shell",
                                {
                                    "command": 'printf "%s" "$$" > pid.txt; IFS= read -r line; printf replayed > must-not-exist.txt',
                                    "cwd": str(secondary),
                                    "yieldTimeMs": 250,
                                },
                            ),
                        )
                    ),
                ]
            )
            RuntimeEngine(
                store,
                model,
                lambda _: None,
                shell_available=True,
                resource_registry=resources,
            ).run(run["id"], cancel)
            assert store.read_run(run["id"])["status"] == "canceled"
            result = _result(store, run["id"])
            assert result["data"]["executionStatus"] == "exited", result
            assert result["data"]["termination"] == "canceled", result
            assert str(secondary / "pid.txt") in result["data"]["created"]
            resources.ensure_empty()
            with pytest.raises(ProcessLookupError):
                os.kill(int((secondary / "pid.txt").read_text()), 0)
            assert not (secondary / "must-not-exist.txt").exists()
            directory = store.data_directory
            store.close()
            store = SessionStore(directory)
            store.initialize()
            assert store.read_run(run["id"])["status"] == "canceled"
            assert _result(store, run["id"])["data"]["termination"] == "canceled"
            assert not (secondary / "must-not-exist.txt").exists()
        finally:
            store.close()


@pytest.mark.parametrize("secondary_cwd", [False, True])
def test_secondary_root_replacement_during_environment_preparation_blocks_launch(
    tmp_path: Path, monkeypatch, secondary_cwd: bool
) -> None:
    from eidos_runtime.sandbox.shell import SHELL_ENVIRONMENT_PROVIDER

    if not is_seatbelt_usable():
        pytest.skip("native Seatbelt unavailable")
    with tempfile.TemporaryDirectory(
        prefix=".eidos-shell-replaced-", dir=Path.home()
    ) as temporary:
        root = Path(temporary).resolve()
        primary, secondary = root / "primary", root / "secondary"
        primary.mkdir()
        secondary.mkdir()
        moved = root / "old-secondary"
        store, _manager, projects, sessions = _runtime(tmp_path)
        original_get = SHELL_ENVIRONMENT_PROVIDER.get

        def replace_after_snapshot(*args, **kwargs):
            snapshot = original_get(*args, **kwargs)
            secondary.rename(moved)
            secondary.mkdir()
            return snapshot

        try:
            project = _create_project(
                projects, [("primary", primary), ("secondary", secondary)], "primary"
            )
            session = sessions.create(
                SessionCreateRequestDto(projectId=project.id)
            ).root
            monkeypatch.setattr(
                SHELL_ENVIRONMENT_PROVIDER, "get", replace_after_snapshot
            )
            _run_id, result = _run(
                store,
                session["id"],
                {
                    "command": f"touch {shlex.quote(str(secondary / 'must-not-exist.txt'))}",
                    "cwd": str(secondary) if secondary_cwd else ".",
                },
                warmup=False,
            )
            assert result["code"] == "workspace_identity_changed", result
            assert result["data"]["termination"] == "not_started", result
            assert not (secondary / "must-not-exist.txt").exists()
            assert not (moved / "must-not-exist.txt").exists()
        finally:
            store.close()


def test_subdirectory_profile_rejects_metadata_from_another_repository(
    tmp_path: Path,
) -> None:
    from eidos_runtime.sandbox.seatbelt import SeatbeltProfile

    source = _repository(tmp_path / "source", "source\n")
    unrelated = _repository(tmp_path / "unrelated", "unrelated\n")
    (source / "src").mkdir()
    with pytest.raises(ValueError, match="metadata identity changed"):
        SeatbeltProfile.create(
            workspace_root=source / "src",
            sandbox_home=Path.home(),
            sandbox_tmp=tmp_path,
            git_worktree_dir=unrelated / ".git",
            git_common_dir=unrelated / ".git",
        )


@pytest.mark.parametrize("deny_kind", ["confidential", "additional"])
def test_git_metadata_read_exception_keeps_explicit_denies(
    tmp_path: Path, deny_kind: str
) -> None:
    from eidos_runtime.sandbox.permissions import (
        AdditionalPermissionProfile,
        BasePermissionProfile,
        FileSystemAccessMode,
        FileSystemPermissionEntry,
        materialize_effective_profile,
    )
    from eidos_runtime.sandbox.seatbelt import SeatbeltProfile

    if not is_seatbelt_usable():
        pytest.skip("native Seatbelt unavailable")
    source = _repository(tmp_path / "source", "source\n")
    (source / "src").mkdir()
    marker = source / ".git" / "HEAD"
    base = BasePermissionProfile.for_workspace(
        workspace_root=source / "src", protected_paths=(tmp_path,)
    )

    def read_head(effective):
        profile = SeatbeltProfile.create(
            workspace_root=source / "src",
            sandbox_home=Path.home().resolve(),
            sandbox_tmp=tmp_path.resolve(),
            git_worktree_dir=source / ".git",
            git_common_dir=source / ".git",
            effective_permissions=effective,
        )
        return subprocess.run(
            profile.command(["/bin/cat", str(marker)]),
            cwd=source / "src",
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )

    allowed = read_head(materialize_effective_profile(base))
    assert allowed.returncode == 0, allowed.stderr
    assert allowed.stdout == marker.read_text()
    if deny_kind == "confidential":
        base = BasePermissionProfile.for_workspace(
            workspace_root=source / "src",
            protected_paths=(tmp_path,),
            hard_confidentiality_paths=(marker,),
        )
        effective = materialize_effective_profile(base)
    else:
        effective = materialize_effective_profile(
            base,
            AdditionalPermissionProfile(
                fileSystem=(
                    FileSystemPermissionEntry(
                        path=str(marker), access=FileSystemAccessMode.DENY
                    ),
                )
            ),
        )
    denied = read_head(effective)
    assert denied.returncode != 0
    assert marker.read_text() not in denied.stdout


@pytest.mark.parametrize("primary_is_nested", [False, True])
@pytest.mark.parametrize("absolute_cwd", [False, True])
def test_shell_git_cwd_uses_the_deepest_attached_root(
    tmp_path: Path, primary_is_nested: bool, absolute_cwd: bool
) -> None:
    from eidos_runtime.runtime.approval import ApprovalDecision
    from test_multi_workspace_execution import _git

    if not is_seatbelt_usable():
        pytest.skip("native Seatbelt unavailable")
    with tempfile.TemporaryDirectory(
        prefix=".eidos-shell-nested-", dir=Path.home()
    ) as temporary:
        outer = _repository(Path(temporary).resolve() / "outer", "outer\n")
        nested = _repository(outer / "nested", "nested\n")
        (nested / "repository-marker.txt").write_text("stage nested\n")
        store, _manager, projects, sessions = _runtime(tmp_path)
        try:
            project = _create_project(
                projects,
                [("outer", outer), ("nested", nested)],
                "nested" if primary_is_nested else "outer",
            )
            session = sessions.create(
                SessionCreateRequestDto(projectId=project.id)
            ).root
            _run_id, result = _run(
                store,
                session["id"],
                {
                    "command": "git add repository-marker.txt",
                    "cwd": str(nested)
                    if absolute_cwd
                    else ("." if primary_is_nested else "nested"),
                    "gitWriteAccess": "request",
                    "justification": "Stage the requested nested repository file.",
                },
                approval=lambda _request, _cancel: ApprovalDecision("approve"),
            )
            assert result["code"] == "ok", result
            assert (
                _git(nested, "diff", "--cached", "--name-only")
                == "repository-marker.txt"
            )
            assert not _git(outer, "diff", "--cached", "--name-only")
        finally:
            store.close()

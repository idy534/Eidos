from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import sqlite3
import subprocess
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from eidos_runtime.application.errors import ApplicationError
from eidos_runtime.application.shell_environment import ShellEnvironmentApplication
from eidos_runtime.db.database import WorkspaceIdentity
from eidos_runtime.db.schema import V34_SCHEMA_SQL
from eidos_runtime.db.shell_environment_migration import migrate_shell_environment
from eidos_runtime.db.storage import SessionStore
from eidos_runtime.domain.shell_environment import ShellEnvironmentPolicy
from eidos_runtime.persistence.shell_environment import ShellEnvironmentRepository
from eidos_runtime.protocol.shell_environment import ShellEnvironmentSaveRequest
from eidos_runtime.sandbox.host_shell import (
    CAPTURE_MARKER,
    HostShell,
    ShellEnvironmentSnapshotProvider,
)
from eidos_runtime.sandbox.shell import prepare_shell_launch


@pytest.mark.parametrize(
    "inherit, expected",
    [
        ("all", {"PATH": "/bin", "HOME": "/home", "API_KEY": "key", "TOKEN": "token"}),
        ("core", {"PATH": "/bin", "HOME": "/home"}),
        ("none", {}),
    ],
)
def test_policy_inherits_selected_environment_and_excludes_internal_values(
    inherit, expected
):
    source = {
        "PATH": "/bin",
        "HOME": "/home",
        "API_KEY": "key",
        "TOKEN": "token",
        "EIDOS_INTERNAL": "private",
    }
    assert ShellEnvironmentPolicy(inherit=inherit).apply(source) == expected
    assert source["EIDOS_INTERNAL"] == "private"


@pytest.mark.parametrize(
    "policy",
    [
        {"inherit": "invalid"},
        {"inherit": 1},
        {"filters": {}},
        {"set": {"TASK_VALUE": "no"}},
    ],
)
def test_policy_rejects_invalid_or_unsupported_settings(policy):
    with pytest.raises(ValidationError):
        ShellEnvironmentPolicy.model_validate_json(json.dumps(policy))


def test_capture_policy_cache_refresh_and_failed_capture_retry(tmp_path):
    calls = []
    now = [datetime.now(UTC)]
    fail = [False]

    def capture(argv, cwd, environment, timeout, limit):
        calls.append(dict(environment))
        return subprocess.CompletedProcess(
            argv,
            1 if fail[0] else 0,
            f"{CAPTURE_MARKER}\0HOME={tmp_path}\0PATH=/bin\0PROFILE_TOKEN=from-profile\0".encode(),
            b"",
        )

    provider = ShellEnvironmentSnapshotProvider(
        runner=capture,
        parent_environment={"HOME": str(tmp_path), "PARENT_TOKEN": "parent"},
        clock=lambda: now[0],
    )
    shell = HostShell(Path("/bin/zsh"), "zsh")
    provider.get(shell, tmp_path)
    provider.get(shell, tmp_path)
    assert len(calls) == 1
    core = ShellEnvironmentPolicy(inherit="core")
    provider.get(shell, tmp_path, environment_policy=core)
    assert "PARENT_TOKEN" not in calls[-1]
    provider.invalidate()
    fail[0] = True
    assert provider.get(shell, tmp_path).source == "fallback"
    provider.get(shell, tmp_path)
    assert len(calls) == 3
    now[0] += timedelta(seconds=2)
    fail[0] = False
    assert provider.get(shell, tmp_path).source == "captured"
    assert len(calls) == 4


def test_final_launch_applies_policy_without_changing_command_or_cwd(tmp_path):
    identity = WorkspaceIdentity(tmp_path, 1, 2, 3)
    profile = SimpleNamespace(command=lambda argv: argv)
    launch = prepare_shell_launch(
        profile=profile,
        command="printf '%s' \"${HOST_TOKEN-unset}\"",
        cwd=identity,
        attempt=None,
        shell=HostShell(Path("/bin/sh"), "sh"),
        environment={
            "HOME": str(tmp_path),
            "PATH": "/bin",
            "HOST_TOKEN": "filtered",
        },
        environment_policy=ShellEnvironmentPolicy(inherit="none"),
    )
    assert launch.cwd == tmp_path
    assert launch.environment == {}
    result = subprocess.run(
        launch.argv,
        cwd=launch.cwd,
        env=launch.environment,
        capture_output=True,
        check=True,
    )
    assert result.stdout == b"unset"


def test_settings_persist_and_conflicts_are_atomic(tmp_path):
    directory = tmp_path / "data"
    store = SessionStore(directory)
    store.initialize()
    try:
        repository = ShellEnvironmentRepository(store.database)
        application = ShellEnvironmentApplication(repository)
        assert application.read().policy.inherit == "all"
        request = ShellEnvironmentSaveRequest(
            expected_revision=0,
            policy=ShellEnvironmentPolicy(inherit="core"),
        )
        result = application.save(request)
        assert result.revision == 1
        with pytest.raises(ApplicationError) as error:
            application.save(request)
        assert error.value.code == "SHELL_ENVIRONMENT_CONFIG_CONFLICT"
        events = store.connection.execute(
            "SELECT payload_json FROM events WHERE event_type='shell_environment.settings_changed'"
        ).fetchall()
        assert len(events) == 1 and json.loads(events[0][0]) == {
            "id": "shell-environment"
        }
        # A transaction failure cannot change the active configuration.
        store.connection.execute(
            "CREATE TRIGGER fail_environment_update BEFORE UPDATE ON shell_environment_settings BEGIN SELECT RAISE(ABORT, 'fixture failure'); END"
        )
        store.connection.commit()
        with pytest.raises(sqlite3.Error):
            repository.save(ShellEnvironmentPolicy(inherit="none"), 1)
        assert repository.read().revision == 1
        assert repository.read().policy.inherit == "core"
    finally:
        store.close()
    restarted = SessionStore(directory)
    restarted.initialize()
    try:
        restored = ShellEnvironmentRepository(restarted.database).read()
        assert restored.revision == 1
        assert restored.policy.inherit == "core"
    finally:
        restarted.close()


@pytest.mark.parametrize("inherit", ["all", "core", "none"])
def test_zsh_startup_cannot_restore_variables_after_restricted_inheritance(
    tmp_path, inherit
):
    (tmp_path / ".zshenv").write_text("export TASK_FROM_PROFILE=fixture\n")
    identity = WorkspaceIdentity(tmp_path, 1, 2, 3)
    launch = prepare_shell_launch(
        profile=SimpleNamespace(command=lambda argv: argv),
        command="printf '%s' \"${TASK_FROM_PROFILE-unset}\"",
        cwd=identity,
        attempt=None,
        shell=HostShell(Path("/bin/zsh"), "zsh"),
        environment={"HOME": str(tmp_path), "PATH": "/bin"},
        environment_policy=ShellEnvironmentPolicy(inherit=inherit),
    )
    result = subprocess.run(
        launch.argv,
        cwd=launch.cwd,
        env=launch.environment,
        capture_output=True,
        check=True,
    )
    assert result.stdout == (b"fixture" if inherit == "all" else b"unset")


@pytest.mark.platform
@pytest.mark.parametrize("inherit", ["all", "core", "none"])
def test_inheritance_modes_execute_inside_real_seatbelt(tmp_path, inherit):
    from eidos_runtime.sandbox.seatbelt import SeatbeltProfile, is_seatbelt_usable

    if not is_seatbelt_usable():
        pytest.skip("Native macOS Seatbelt is unavailable")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    metadata = workspace.stat()
    identity = WorkspaceIdentity(
        workspace, metadata.st_dev, metadata.st_ino, metadata.st_uid
    )
    profile = SeatbeltProfile.create(
        workspace_root=workspace, sandbox_home=tmp_path, sandbox_tmp=tmp_path
    )
    launch = prepare_shell_launch(
        profile=profile,
        command="printf '%s' \"${HOST_TOKEN-unset}\"",
        cwd=identity,
        attempt=None,
        shell=HostShell(Path("/bin/zsh"), "zsh"),
        environment={"HOME": str(tmp_path), "PATH": "/bin", "HOST_TOKEN": "fixture"},
        environment_policy=ShellEnvironmentPolicy(inherit=inherit),
    )
    assert launch.sandboxed
    result = subprocess.run(
        launch.argv,
        cwd=launch.cwd,
        env=launch.environment,
        capture_output=True,
        check=True,
    )
    assert result.stdout == (b"fixture" if inherit == "all" else b"unset")


def test_migration_upgrades_v34_and_rolls_back_on_failure(tmp_path):
    connection = sqlite3.connect(":memory:")
    connection.executescript(V34_SCHEMA_SQL + "PRAGMA user_version=34;")
    migrate_shell_environment(connection)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 35
    assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    connection.close()
    broken = sqlite3.connect(":memory:")
    broken.executescript(
        "CREATE TABLE shell_environment_settings(id); PRAGMA user_version=34;"
    )
    with pytest.raises(sqlite3.Error):
        migrate_shell_environment(broken)
    assert broken.execute("PRAGMA user_version").fetchone()[0] == 34
    assert not broken.in_transaction
    broken.close()


def test_existing_database_upgrade_preserves_settings(tmp_path):
    directory = tmp_path / "data"
    directory.mkdir(mode=0o700)
    connection = sqlite3.connect(directory / "state.sqlite")
    connection.executescript(V34_SCHEMA_SQL + "PRAGMA user_version=34;")
    connection.execute("UPDATE runtime_settings SET managed_worktree_limit=7")
    connection.commit()
    connection.close()
    (directory / "state.sqlite").chmod(0o600)
    store = SessionStore(directory)
    store.initialize()
    try:
        assert store.health_state == "ready"
        assert store.connection.execute("PRAGMA user_version").fetchone()[0] == 35
        assert (
            store.connection.execute(
                "SELECT managed_worktree_limit FROM runtime_settings"
            ).fetchone()[0]
            == 7
        )
        repository = ShellEnvironmentRepository(store.database)
        repository.save(ShellEnvironmentPolicy(inherit="core"), 0)
        assert repository.read().policy.inherit == "core"
    finally:
        store.close()


def test_runtime_shell_launch_uses_saved_policy_without_extra_approval(
    tmp_path, monkeypatch
):
    import threading
    from eidos_runtime.model.client import ModelResponse, ModelToolCall, ScriptedModel
    from eidos_runtime.runtime.engine import RuntimeEngine
    from eidos_runtime.sandbox.host_shell import ShellEnvironmentSnapshot

    store = SessionStore(tmp_path / "data")
    store.initialize()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = store.create_session(str(workspace))
    run, _ = store.create_run(
        session["id"], "Use configured environment", approval_mode="full_access"
    )
    ShellEnvironmentRepository(store.database).save(
        ShellEnvironmentPolicy(inherit="none"), 0
    )
    shell = HostShell(Path("/bin/sh"), "sh")
    snapshot = ShellEnvironmentSnapshot(
        shell,
        {"HOME": str(tmp_path), "PATH": "/bin", "HOST_TOKEN": "filtered"},
        datetime.now(UTC),
        "captured",
        "fixture",
    )
    monkeypatch.setattr(
        "eidos_runtime.sandbox.shell.HOST_SHELL_RESOLVER.resolve", lambda: shell
    )
    monkeypatch.setattr(
        "eidos_runtime.sandbox.shell.SHELL_ENVIRONMENT_PROVIDER.get",
        lambda *args, **kwargs: snapshot,
    )
    launches = []

    def start(manager, launch, **kwargs):
        launches.append(launch)
        return {
            "schemaVersion": 1,
            "toolName": "run_shell",
            "outcome": "success",
            "code": "ok",
            "summary": "done",
            "data": {
                "exitCode": 0,
                "stdout": "",
                "stderr": "",
                "truncated": False,
                "termination": "exit",
                "executionStatus": "exited",
                "workspaceChanged": False,
            },
            "sideEffectsMayExist": False,
            "reconciliationRequired": False,
        }

    monkeypatch.setattr(
        "eidos_runtime.runtime.shell_process_manager.ShellProcessManager.start", start
    )
    model = ScriptedModel(
        [
            ModelResponse(
                tool_calls=(ModelToolCall("shell", "run_shell", {"command": "true"}),)
            ),
            ModelResponse(text="done"),
        ]
    )
    try:
        RuntimeEngine(store, model, lambda _: None, shell_available=True).run(
            run["id"], threading.Event()
        )
        assert len(launches) == 1
        assert launches[0].environment == {}
        assert (
            store.connection.execute("SELECT count(*) FROM approvals").fetchone()[0]
            == 0
        )
        assert store.read_run(run["id"])["status"] == "succeeded"
    finally:
        store.close()


def test_generated_protocol_fixture_matches_python_models():
    from eidos_runtime.protocol.shell_environment import (
        ShellEnvironmentReadRequest,
        ShellEnvironmentResponse,
    )

    fixture = json.loads(
        (
            Path(__file__).resolve().parents[2]
            / "protocol/fixtures/shell-environment-v1.json"
        ).read_text()
    )
    for name, model in [
        ("ReadRequests", ShellEnvironmentReadRequest),
        ("Requests", ShellEnvironmentSaveRequest),
        ("Responses", ShellEnvironmentResponse),
    ]:
        for value in fixture[f"valid{name}"]:
            model.model_validate_json(json.dumps(value))
        for value in fixture[f"invalid{name}"]:
            with pytest.raises(ValidationError):
                model.model_validate_json(json.dumps(value))

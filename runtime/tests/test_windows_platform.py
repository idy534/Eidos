from __future__ import annotations

import hashlib
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import zipfile

import pytest

from eidos_runtime.db.database import Database
from eidos_runtime.git.native import HardenedGitRunner
from eidos_runtime.infrastructure.secret_files import SecretFiles
from eidos_runtime.sandbox.permissions import (
    AdditionalPermissionProfile, BasePermissionProfile, SandboxAttempt, SandboxType, materialize_effective_profile,
)
from eidos_runtime.sandbox.seatbelt import is_seatbelt_ready
from eidos_runtime.sandbox.shell import prepare_shell_launch_for_execution
from eidos_runtime.runtime.shell_process_manager import ShellProcessManager
from eidos_runtime.tools.workspace import FileChange, ToolExecutor
from eidos_runtime.workspace.reader import capture_directory_identity
from eidos_runtime.workspace.search_driver import RipgrepFileEnumerator


pytestmark = [pytest.mark.platform, pytest.mark.skipif(sys.platform != "win32", reason="Requires Windows and NTFS")]


@pytest.fixture(autouse=True, scope="module")
def _runtime_file_owner() -> None:
    # Runtime startup makes the current account the default owner of new
    # files, including when an administrator account starts it.
    from eidos_runtime.infrastructure.windows_files import configure_process_file_owner

    configure_process_file_owner()


def test_state_lock_and_credentials(tmp_path: Path) -> None:
    data = tmp_path / "Eidos 数据"
    first, second = Database(data), Database(data)
    try:
        first.initialize()
        assert first.health() == {"state": "ready"}
        second.initialize()
        assert second.health() == {"state": "health_only", "code": "state_locked"}
        credentials = SecretFiles(data / "secrets")
        assert credentials.read(credentials.write("测试")) == "测试"
    finally:
        second.close()
        first.close()
    reopened = Database(data)
    try:
        reopened.initialize()
        assert reopened.health() == {"state": "ready"}
    finally:
        reopened.close()


def test_ntfs_commit_keeps_the_open_file_identity_and_rejects_stale_changes(tmp_path: Path) -> None:
    workspace = tmp_path / "工作区"
    workspace.mkdir(mode=0o700)
    profile = materialize_effective_profile(
        BasePermissionProfile.for_workspace(workspace_root=workspace), AdditionalPermissionProfile(),
    )
    path = workspace / "笔记.md"
    cancel = threading.Event()
    with ToolExecutor(workspace) as tools, tools.external_write_scope((), profile, lambda: profile):
        def commit(change: FileChange) -> dict[str, object]:
            return tools.commit_file_change("apply_patch", change, cancel)

        original = b"old\r\n"
        create = FileChange(path.name, original, None, 0o644, "")
        assert (result := commit(create))["outcome"] == "success", result
        identity = path.stat().st_ino
        assert (result := commit(create))["code"] == "file_version_conflict", result
        sha = hashlib.sha256(original).hexdigest()
        update = FileChange(path.name, b"new\r\n", sha, 0o644, "")
        assert (result := commit(update))["outcome"] == "success", result
        assert path.stat().st_ino == identity
        assert path.read_bytes() == b"new\r\n"
        assert (result := commit(update))["code"] == "file_version_conflict", result
        delete = FileChange(path.name, b"", hashlib.sha256(b"new\r\n").hexdigest(), 0o644, "", delete=True)
        assert (result := commit(delete))["outcome"] == "success", result
        assert not path.exists()
        nested = FileChange("文档/新建.md", b"nested\r\n", None, 0o644, "", create_missing_parent=True)
        assert (result := commit(nested))["outcome"] == "success", result
        assert (workspace / "文档" / "新建.md").read_bytes() == b"nested\r\n"
        blocked = FileChange(".GIT/config", b"blocked", None, 0o600, "", create_missing_parent=True)
        assert (result := commit(blocked))["code"] == "permission_not_requestable", result
        assert not (workspace / ".git").exists()


def test_bundled_windows_git_and_ripgrep(tmp_path: Path) -> None:
    workspace = tmp_path / "代码 仓库"
    workspace.mkdir(mode=0o700)
    git = HardenedGitRunner()
    git.run(("init", "--quiet"), cwd=workspace, operation="test-init")
    result = git.run(("rev-parse", "--is-inside-work-tree"), cwd=workspace, operation="test-worktree")
    assert result.stdout.strip() == b"true"
    (workspace / "文档").mkdir()
    (workspace / "文档" / "示例.txt").write_text("中文", encoding="utf-8")
    paths, truncated = RipgrepFileEnumerator().enumerate(
        workspace, deadline=time.monotonic() + 10, cancel=threading.Event(), max_entries=20,
    )
    assert "文档/示例.txt" in paths
    assert not truncated


@pytest.mark.parametrize("tty", [False, True], ids=["pipe", "pty"])
def test_powershell_job_output_and_cancel(tmp_path: Path, tty: bool) -> None:
    assert not is_seatbelt_ready()
    root = capture_directory_identity(tmp_path.resolve())
    profile = materialize_effective_profile(
        BasePermissionProfile.for_workspace(workspace_root=root.path).with_full_access(), AdditionalPermissionProfile(),
    )
    attempt = SandboxAttempt(
        ordinal=0, sandbox=SandboxType.NONE, sandboxRequested=False, permissions=profile,
        sandboxCwd=str(root.path), workspaceRoots=profile.workspace_roots,
    )
    manager = ShellProcessManager()
    try:
        launch = prepare_shell_launch_for_execution(
            root, "Write-Output '中文'; exit 7", root, attempt, tty=tty,
        )
        outcome = manager.start(launch, wait_for_exit=True, yield_time_ms=5_000)
        assert outcome["data"]["exitCode"] == 7
        if tty:
            assert "中文" in outcome["data"]["stdout"]
        else:
            assert outcome["data"]["stdout"].strip() == "中文"
        launch = prepare_shell_launch_for_execution(
            root, """
$child = Start-Process (Join-Path $PSHOME 'powershell.exe') -ArgumentList '-NoProfile -Command Start-Sleep -Seconds 60' -PassThru
if (-not $child -or $child.HasExited) { exit 2 }
Write-Output 'CHILD-STARTED'
Start-Sleep -Seconds 60
""", root, attempt, tty=tty,
        )
        running = manager.start(launch, yield_time_ms=250)
        session = running["data"]["sessionId"]
        captured = running["data"]["stdout"]
        deadline = time.monotonic() + 8
        while "CHILD-STARTED" not in captured and time.monotonic() < deadline:
            captured += manager.write_stdin(session, yield_time_ms=250)["data"]["stdout"]
        assert "CHILD-STARTED" in captured
        terminated = manager.control(session, "terminate", yield_time_ms=5_000)
        assert terminated["data"]["executionStatus"] == "exited"
        assert terminated["data"]["termination"] == "terminated"
    finally:
        # cleanup() reports whether it had to stop a process and raises
        # ShellSessionFinalizationError when a session is not quiescent.
        manager.cleanup()


def test_powershell_pty_starts_after_commit_resizes_and_exchanges_utf8(tmp_path: Path) -> None:
    root = capture_directory_identity(tmp_path.resolve())
    effective = materialize_effective_profile(BasePermissionProfile.for_workspace(workspace_root=root.path).with_full_access())
    attempt = SandboxAttempt(
        ordinal=0, sandbox=SandboxType.NONE, sandboxRequested=False, permissions=effective,
        sandboxCwd=str(root.path), workspaceRoots=effective.workspace_roots,
    )
    launch = prepare_shell_launch_for_execution(
        root, """
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
[System.IO.File]::WriteAllText('started.txt', 'started')
Write-Output 'READY'
$text = [Console]::ReadLine()
Write-Output ('INPUT-' + $text)
Write-Output ('SIZE-' + [Console]::WindowWidth + 'x' + [Console]::WindowHeight)
[Console]::TreatControlCAsInput = $true
Write-Output 'KEY-READY'
$key = [Console]::ReadKey($true)
Write-Output ('KEY-' + [int]$key.KeyChar)
exit 7
""", root, attempt=attempt, tty=True, rows=27, columns=105,
    )
    manager = ShellProcessManager()
    committed = []

    def on_started():
        assert not (root.path / "started.txt").exists()
        committed.append(True)

    try:
        pending = manager.start(launch, yield_time_ms=250, on_started=on_started)
        assert committed == [True]
        assert pending["data"]["transport"] == "pty"
        assert pending["data"]["outputMerged"] is True
        session = pending["data"]["sessionId"]
        output = pending["data"]["stdout"]
        deadline = time.monotonic() + 8
        while "READY" not in output and time.monotonic() < deadline:
            output += manager.write_stdin(session, yield_time_ms=250)["data"]["stdout"]
        assert "READY" in output
        resized = manager.control(session, "resize", columns=110, rows=38)
        assert resized["data"]["columns"] == 110 and resized["data"]["rows"] == 38
        with pytest.raises(RuntimeError, match="shell_terminal_cannot_close_stdin"):
            manager.control(session, "close_stdin")
        output = manager.write_stdin(session, "中文\r", yield_time_ms=250)["data"]["stdout"]
        deadline = time.monotonic() + 8
        while "KEY-READY" not in output and time.monotonic() < deadline:
            output += manager.write_stdin(session, yield_time_ms=250)["data"]["stdout"]
        assert "KEY-READY" in output
        result = manager.write_stdin(session, "\x03", yield_time_ms=250)
        output += result["data"]["stdout"]
        deadline = time.monotonic() + 8
        while result["data"]["executionStatus"] == "running" and time.monotonic() < deadline:
            result = manager.write_stdin(session, yield_time_ms=250)
            output += result["data"]["stdout"]
        assert result["data"]["exitCode"] == 7
        assert "INPUT-中文" in output and "SIZE-110x38" in output
        assert "KEY-3" in output
    finally:
        # cleanup() reports whether it had to stop a process and raises
        # ShellSessionFinalizationError when a session is not quiescent.
        manager.cleanup()


def test_windows_bundled_extension_helper_sends_the_connect_page(tmp_path: Path) -> None:
    import secrets

    import anyio
    from anyio.abc import SocketAttribute

    from eidos_runtime.infrastructure.browser_runtime import resolve_browser_runtime
    from eidos_runtime.infrastructure.chrome_extension import (
        BROKER_PORT_ENV, BROKER_TOKEN_ENV, PLAYWRIGHT_EXTENSION_ID, ConnectPageBroker,
    )
    from eidos_runtime.runtime.runtime_dependencies import discover_runtime_dependency_catalog

    catalog = discover_runtime_dependency_catalog(resource_root=tmp_path / "Resource")
    catalog.resources.ensure(("browser",), threading.Event())
    runtime = resolve_browser_runtime(catalog.resources)
    url = (f"chrome-extension://{PLAYWRIGHT_EXTENSION_ID}/connect.html?"
           "mcpRelayUrl=ws%3A%2F%2F127.0.0.1%3A50123%2Fextension%2F5733f635-0f0a-41ef-9972-305b283803db")

    async def exercise():
        token = secrets.token_urlsafe(32)
        opened = anyio.Event()

        async def open_page(page):
            assert page == url
            opened.set()

        broker = ConnectPageBroker(open_page, token=token)
        async with await anyio.create_tcp_listener(local_host="127.0.0.1") as listener:
            async with anyio.create_task_group() as group:
                group.start_soon(broker.serve, listener)
                environment = {**os.environ, BROKER_PORT_ENV: str(listener.extra(SocketAttribute.local_port)),
                               BROKER_TOKEN_ENV: token}
                with anyio.fail_after(10):
                    await anyio.run_process([str(runtime.connect_helper), url], env=environment)
                    await opened.wait()
                group.cancel_scope.cancel()

    anyio.run(exercise)


def test_windows_chrome_owns_its_profile_and_releases_the_debug_port(tmp_path: Path) -> None:
    import socket
    from urllib.parse import urlsplit

    from eidos_runtime.infrastructure.browser_host import BrowserHost, find_chrome

    if find_chrome() is None:
        pytest.skip("Google Chrome is not installed")
    host = BrowserHost(tmp_path / "Chrome profile", extra_arguments=("--headless=new", "--disable-gpu"))
    try:
        endpoint = host.ensure(threading.Event())
        assert endpoint == host.endpoint()
        assert host.status().running
    finally:
        host.shutdown()
    assert not host.status().running
    port = urlsplit(endpoint).port
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            pass
    except OSError:
        return
    # Name the process that still listens so a failure explains itself.
    owners = subprocess.run(
        [
            "powershell.exe", "-NoProfile", "-Command",
            f"Get-NetTCPConnection -LocalPort {port} -State Listen | ForEach-Object {{ "
            "Get-CimInstance Win32_Process -Filter \"ProcessId=$($_.OwningProcess)\" "
            "| Format-List ProcessId,ParentProcessId,CommandLine }",
        ],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False,
    )
    pytest.fail(f"Chrome debug port {port} is still open:\n{owners.stdout}{owners.stderr}")


def test_directory_relative_rename_and_tree_removal_keep_junction_targets(tmp_path: Path) -> None:
    import _winapi

    from eidos_runtime.infrastructure import local_files

    root = tmp_path / "目录 根"
    (root / "tree" / "子目录").mkdir(parents=True)
    (root / "tree" / "子目录" / "文件.txt").write_text("内容", encoding="utf-8")
    (root / "note.tmp").write_bytes(b"new")
    (root / "note.md").write_bytes(b"old")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")
    _winapi.CreateJunction(str(outside), str(root / "tree" / "link"))
    _winapi.CreateJunction(str(outside), str(root / "junction"))
    descriptor = local_files.open_directory(root)
    try:
        local_files.replace_at("note.tmp", "note.md", descriptor)
        local_files.replace_at("tree", "tree-removed", descriptor)
        local_files.remove_tree_at("tree-removed", descriptor)
        with pytest.raises(PermissionError):
            local_files.remove_tree_at("junction", descriptor)
    finally:
        os.close(descriptor)
    assert (root / "note.md").read_bytes() == b"new"
    assert not os.path.lexists(root / "tree-removed")
    assert (outside / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_memory_hooks_skill_removal_and_backup_use_ntfs_handles(tmp_path: Path) -> None:
    from eidos_runtime.db.backup import backup
    from eidos_runtime.db.storage import SessionStore
    from eidos_runtime.extensions.hook_sources import verify_source_paths
    from eidos_runtime.extensions.plugins import PluginCatalog
    from eidos_runtime.extensions.skill_management import SkillManagement
    from eidos_runtime.extensions.skills import SkillCatalog, deploy_system_skills
    from eidos_runtime.memory.contracts import MemoryEditRequest
    from eidos_runtime.runtime.hook_transcript import write_transcript
    from eidos_runtime.sandbox.sensitive import SensitiveScanner

    store = SessionStore(tmp_path / "Eidos 数据")
    store.initialize()
    try:
        data = store.data_directory
        assert data is not None
        store.database.memory.edit(
            MemoryEditRequest(operation_id="save", scope="global", path="note.md", content="中文笔记")
        )
        transcript = write_transcript(store, "session-1", SensitiveScanner())
        assert "transcript_metadata" in transcript.read_text(encoding="utf-8")

        hooks = data / "hooks.json"
        hooks.write_text('{"hooks": {}}', encoding="utf-8")
        digest = hashlib.sha256(b"hooks.json\0" + hooks.read_bytes() + b"\0").hexdigest()
        assert verify_source_paths((str(hooks),), digest) == data

        plugins = PluginCatalog(store)
        deploy_system_skills(data)
        catalog = SkillCatalog(plugins)
        snapshot = catalog.catalog_snapshot(catalog.extension_snapshot())
        assert any(entry.qualified_id == "system:docx" for entry in snapshot.entries)
        assert "docx" in catalog.read_skill(snapshot, "system:docx")["content"].lower()
        from eidos_runtime.extensions.skill_access import SkillAccess

        access = SkillAccess.from_snapshot(snapshot)
        assert access.activate_explicit("system:docx").canonical_root == (data / "skills" / ".system" / "docx").resolve()
        run_script = data / "skills" / ".system" / "docx" / "scripts" / "run.py"
        implicit = access.activate_implicit(f"& $env:RUNTIME_PYTHON '{run_script}' 'python' 'demo.py'", tmp_path)
        assert implicit is not None and implicit.qualified_id == "system:docx"
        skill = data / "skills" / "personal"
        skill.mkdir(mode=0o700)
        (skill / "SKILL.md").write_bytes(
            "---\nname: personal\ndescription: Personal skill.\n---\nPersonal body.\n".encode()
        )
        assert not SkillManagement(SkillCatalog(plugins)).remove("user:personal").cleanup_pending
        assert not skill.exists()

        archive = tmp_path / "备份.zip"
        backup(store.database.memory, archive)
        with zipfile.ZipFile(archive) as contents:
            assert {"manifest.json", "state.sqlite"} <= set(contents.namelist())
    finally:
        store.close()


def test_skill_creator_scripts_run_without_posix_owner_ids(tmp_path: Path) -> None:
    scripts = Path(__file__).resolve().parents[1] / "eidos_runtime/resources/skills/.system/skill-creator/scripts"
    environment = {**os.environ, "PYTHONUTF8": "1"}
    root = tmp_path / "user skills"
    created = subprocess.run(
        [sys.executable, "-B", str(scripts / "init_skill.py"), "demo-skill", "--path", str(root)],
        capture_output=True, text=True, encoding="utf-8", env=environment, check=False,
    )
    assert created.returncode == 0, created.stderr
    skill = root / "demo-skill" / "SKILL.md"
    skill.write_bytes(b"---\nname: demo-skill\ndescription: Demo skill.\n---\n\n# Demo Skill\n\nUse it.\n")
    validated = subprocess.run(
        [sys.executable, "-B", str(scripts / "quick_validate.py"), str(skill.parent)],
        capture_output=True, text=True, encoding="utf-8", env=environment, check=False,
    )
    assert validated.returncode == 0, validated.stdout + validated.stderr

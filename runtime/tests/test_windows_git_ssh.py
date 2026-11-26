from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from eidos_runtime.git import native
from eidos_runtime.git.native import GitExecutionProfile, HardenedGitRunner


def _make_runner_fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[HardenedGitRunner, Path, Path, list[dict[str, str]]]:
    git_root = tmp_path / "mingit"
    git_exe = git_root / "cmd" / "git.exe"
    git_exe.parent.mkdir(parents=True)
    git_exe.write_bytes(b"git")
    bundled_ssh = git_root / "usr" / "bin" / "ssh.exe"
    bundled_ssh.parent.mkdir(parents=True)
    bundled_ssh.write_bytes(b"ssh")

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    user_home = tmp_path / "home"
    user_home.mkdir()

    captured_envs: list[dict[str, str]] = []

    def fake_start(_argv, *, cwd, env, stdin, stdout, stderr):
        del cwd, stdin, stdout, stderr
        captured_envs.append(dict(env))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(native.local_files, "WINDOWS", True)
    monkeypatch.setattr(native.local_files, "trusted_asset", lambda _path, _meta: True)
    monkeypatch.setattr(native.host_processes, "start", fake_start)
    monkeypatch.setattr(native.host_processes, "close_job", lambda _process: None)
    monkeypatch.setattr(
        native,
        "_communicate_bounded",
        lambda *_args, **_kwargs: (b"", b"", False, False),
    )

    runner = HardenedGitRunner(git_executable=str(git_exe), user_home=user_home)
    return runner, workspace, user_home, captured_envs


def test_windows_remote_git_prefers_native_openssh_and_preserves_named_pipe_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner, workspace, user_home, captured_envs = _make_runner_fixture(tmp_path, monkeypatch)
    system_root = tmp_path / "Windows"
    native_ssh = system_root / "System32" / "OpenSSH" / "ssh.exe"
    native_ssh.parent.mkdir(parents=True)
    native_ssh.write_bytes(b"native-ssh")

    monkeypatch.setenv("SYSTEMROOT", str(system_root))
    monkeypatch.setenv("SSH_AUTH_SOCK", r"\\.\pipe\openssh-ssh-agent")

    runner.run(("ls-remote", "origin"), cwd=workspace, operation="fetch", profile=GitExecutionProfile.REMOTE)

    assert len(captured_envs) == 1
    env = captured_envs[0]
    assert native_ssh.as_posix() in env["GIT_SSH_COMMAND"]
    assert "-o BatchMode=yes" in env["GIT_SSH_COMMAND"]
    assert env["USERPROFILE"] == str(user_home.resolve(strict=False))
    assert env["SSH_AUTH_SOCK"] == r"\\.\pipe\openssh-ssh-agent"


def test_windows_remote_git_accepts_forward_slash_pipe_and_strips_invalid_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner, workspace, _user_home, captured_envs = _make_runner_fixture(tmp_path, monkeypatch)
    missing_sysroot = tmp_path / "NoOpenSSH"
    missing_sysroot.mkdir()
    monkeypatch.setenv("SYSTEMROOT", str(missing_sysroot))
    monkeypatch.setenv("SSH_AUTH_SOCK", "//./pipe/openssh-ssh-agent")

    runner.run(("ls-remote", "origin"), cwd=workspace, operation="fetch", profile=GitExecutionProfile.REMOTE)

    first_env = captured_envs[0]
    assert "usr/bin/ssh.exe" in first_env["GIT_SSH_COMMAND"]
    assert first_env["SSH_AUTH_SOCK"] == "//./pipe/openssh-ssh-agent"

    assert native._validated_windows_ssh_auth_sock("not\x00valid") is None
    assert native._validated_windows_ssh_auth_sock("\\\\.\\pipe\\") is None

    # Invalid / missing socket paths and control characters must be stripped.
    for bad_sock in ("//./pipe/", "\\\\.\\pipe\\", str(tmp_path / "missing.sock"), "line\nbreak"):
        monkeypatch.setenv("SSH_AUTH_SOCK", bad_sock)
        runner.run(("ls-remote", "origin"), cwd=workspace, operation="fetch", profile=GitExecutionProfile.REMOTE)
        assert "SSH_AUTH_SOCK" not in captured_envs[-1]

    # Existing socket file path is allowed as fallback when agent sets a file path.
    existing_sock = tmp_path / "agent.sock"
    existing_sock.write_text("sock", encoding="utf-8")
    monkeypatch.setenv("SSH_AUTH_SOCK", str(existing_sock))
    runner.run(("ls-remote", "origin"), cwd=workspace, operation="fetch", profile=GitExecutionProfile.REMOTE)
    assert captured_envs[-1]["SSH_AUTH_SOCK"] == str(existing_sock)
    assert os.environ.get("SSH_AUTH_SOCK") == str(existing_sock)

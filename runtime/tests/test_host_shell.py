from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
from typing import Mapping

import pytest

from eidos_runtime.sandbox import host_shell
from eidos_runtime.sandbox.host_shell import (
    CAPTURE_MARKER,
    MAX_CAPTURE_BYTES,
    HostShell,
    HostShellResolver,
    ShellEnvironmentSnapshotProvider,
)


class _Runner:
    def __init__(
        self,
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int = 0,
        error: BaseException | None = None,
    ) -> None:
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.error = error
        self.calls: list[dict[str, object]] = []

    def __call__(
        self,
        argv: tuple[str, ...],
        cwd: Path,
        environment: Mapping[str, str],
        timeout_seconds: float,
        output_limit_bytes: int,
    ) -> subprocess.CompletedProcess[bytes]:
        self.calls.append({
            "argv": argv,
            "cwd": cwd,
            "environment": dict(environment),
            "timeout_seconds": timeout_seconds,
            "output_limit_bytes": output_limit_bytes,
        })
        if self.error is not None:
            raise self.error
        return subprocess.CompletedProcess(
            argv,
            self.returncode,
            stdout=self.stdout,
            stderr=self.stderr,
        )


def _executable(path: Path) -> Path:
    path.write_text("#!/bin/sh\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def _shell(tmp_path: Path, kind: str = "zsh") -> HostShell:
    tmp_path.mkdir(parents=True, exist_ok=True)
    return HostShell(_executable(tmp_path / kind), kind)  # type: ignore[arg-type]


def _env_output(values: Mapping[str, str], *, prefix: bytes = b"") -> bytes:
    records = b"\0".join(
        f"{key}={value}".encode("utf-8") for key, value in values.items()
    )
    return prefix + CAPTURE_MARKER.encode("ascii") + b"\0" + records + b"\0"


def test_host_shell_is_frozen_and_accepts_only_supported_kinds() -> None:
    shell = HostShell(Path("/bin/zsh"), "zsh")

    with pytest.raises(FrozenInstanceError):
        shell.kind = "bash"  # type: ignore[misc]
    with pytest.raises(ValueError):
        HostShell(Path("/bin/fish"), "fish")  # type: ignore[arg-type]


def test_resolver_prefers_valid_pwd_shell_over_environment_shell(
    tmp_path: Path,
) -> None:
    pwd_shell = _shell(tmp_path / "pwd")
    environment_shell = _shell(tmp_path / "environment")

    resolved = HostShellResolver(
        parent_environment={"SHELL": str(environment_shell.executable)},
        pwd_resolver=lambda: SimpleNamespace(pw_shell=str(pwd_shell.executable)),
    ).resolve()

    assert resolved == pwd_shell


def test_resolver_uses_environment_after_invalid_pwd_shell(tmp_path: Path) -> None:
    environment_shell = _shell(tmp_path / "environment")
    invalid_pwd_shell = tmp_path / "not-a-shell"
    _executable(invalid_pwd_shell)

    resolved = HostShellResolver(
        parent_environment={"SHELL": str(environment_shell.executable)},
        pwd_resolver=lambda: SimpleNamespace(pw_shell=str(invalid_pwd_shell)),
    ).resolve()

    assert resolved == environment_shell


def test_resolver_tries_zsh_bash_sh_fallback_chain(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fallback_bash = _shell(tmp_path / "fallback", "bash")
    monkeypatch.setattr(
        host_shell,
        "DEFAULT_SHELL_PATHS",
        (tmp_path / "missing-zsh", fallback_bash.executable, tmp_path / "missing-sh"),
    )

    resolved = HostShellResolver(
        parent_environment={"SHELL": str(tmp_path / "missing-environment")},
        pwd_resolver=lambda: SimpleNamespace(pw_shell=str(tmp_path / "missing-pwd")),
    ).resolve()

    assert resolved == fallback_bash


def test_resolver_requires_regular_executable_with_matching_basename(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    directory_named_zsh = tmp_path / "zsh"
    directory_named_zsh.mkdir()
    non_executable_bash = tmp_path / "bash"
    non_executable_bash.write_text("#!/bin/sh\n", encoding="utf-8")
    executable_wrong_name = _executable(tmp_path / "fish")
    valid_sh = _shell(tmp_path / "valid", "sh")
    monkeypatch.setattr(
        host_shell,
        "DEFAULT_SHELL_PATHS",
        (
            directory_named_zsh,
            non_executable_bash,
            executable_wrong_name,
            valid_sh.executable,
        ),
    )

    resolved = HostShellResolver(
        parent_environment={},
        pwd_resolver=lambda: SimpleNamespace(pw_shell="relative/zsh"),
    ).resolve()

    assert resolved == valid_sh


def test_provider_captures_with_login_shell_and_preserves_parsed_values(
    tmp_path: Path,
) -> None:
    shell = _shell(tmp_path / "shell")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    captured_at = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
    runner = _Runner(stdout=_env_output({
        "HOME": "/captured/home",
        "PATH": "/captured/bin",
        "SHELL": str(shell.executable),
        "TMPDIR": "/captured/tmp",
        "NORMAL": "kept",
        "EQUAL": "left=right",
        "MULTILINE": "first\nsecond",
    }))
    provider = ShellEnvironmentSnapshotProvider(
        runner=runner,
        parent_environment={
            "NORMAL": "parent",
            "EIDOS_RUN_ID": "control-plane",
            "PYTHONHOME": "runtime-home",
        },
        clock=lambda: captured_at,
    )

    snapshot = provider.get(shell, cwd)

    assert snapshot.source == "captured"
    assert snapshot.captured_at == captured_at
    assert snapshot.environment["NORMAL"] == "kept"
    assert snapshot.environment["EQUAL"] == "left=right"
    assert snapshot.environment["MULTILINE"] == "first\nsecond"
    assert snapshot.environment["HOME"] == "/captured/home"
    assert snapshot.environment["PATH"] == "/captured/bin"
    assert snapshot.environment["SHELL"] == str(shell.executable)
    assert snapshot.environment["TMPDIR"] == "/captured/tmp"

    call = runner.calls[0]
    assert call["argv"][:2] == (str(shell.executable), "-lc")
    assert "/usr/bin/printf" in call["argv"][2]
    assert "/usr/bin/env -0" in call["argv"][2]
    assert call["timeout_seconds"] == 10.0
    assert call["output_limit_bytes"] == MAX_CAPTURE_BYTES


def test_provider_ignores_profile_stdout_before_nul_marker(tmp_path: Path) -> None:
    shell = _shell(tmp_path / "shell")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    runner = _Runner(stdout=_env_output(
        {"PROFILE_SAFE": "yes"}, prefix=b"profile warning\n"
    ))

    snapshot = ShellEnvironmentSnapshotProvider(
        runner=runner,
        parent_environment={"HOME": "/home", "PATH": "/bin"},
    ).get(shell, cwd)

    assert snapshot.source == "captured"
    assert "profile warning\n" not in snapshot.environment
    assert snapshot.environment["PROFILE_SAFE"] == "yes"


def test_provider_captures_real_zsh_profile_without_leaking_profile_noise(
    tmp_path: Path,
) -> None:
    zsh = Path("/bin/zsh")
    if not zsh.is_file() or not os.access(zsh, os.X_OK):
        pytest.skip("/bin/zsh is unavailable")
    home = tmp_path / "home"
    zdotdir = tmp_path / "zdotdir"
    cwd = tmp_path / "cwd"
    profile_path = zdotdir / ".zprofile"
    profile_path.parent.mkdir()
    home.mkdir()
    cwd.mkdir()
    profile_path.write_text(
        "print -r -- 'profile stdout noise'\n"
        f"export PATH=\"{tmp_path / 'profile-bin'}:$PATH\"\n"
        "export HOST_SNAPSHOT_ORDINARY=from-profile\n",
        encoding="utf-8",
    )
    (tmp_path / "profile-bin").mkdir()
    shell = HostShell(zsh, "zsh")
    snapshot = ShellEnvironmentSnapshotProvider(
        parent_environment={
            "HOME": str(home),
            "ZDOTDIR": str(zdotdir),
            "PATH": "/usr/bin:/bin",
            "SHELL": str(zsh),
            "TMPDIR": str(tmp_path),
            "HOST_SNAPSHOT_ORDINARY": "from-parent",
        }
    ).get(shell, cwd)

    assert snapshot.source == "captured"
    assert snapshot.environment["HOST_SNAPSHOT_ORDINARY"] == "from-profile"
    assert snapshot.environment["PATH"].split(os.pathsep)[0] == str(
        tmp_path / "profile-bin"
    )
    assert "profile stdout noise" not in snapshot.environment


def test_provider_scrubs_control_plane_and_shell_path_state(
    tmp_path: Path,
) -> None:
    shell = _shell(tmp_path / "shell")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    runner = _Runner(stdout=_env_output({
        "EIDOS_SECRET": "must-not-leak",
        "PYTHONHOME": "/profile/python-home",
        "PYTHONPATH": "/profile/python-path",
        "PYTHONNOUSERSITE": "profile-value",
        "PYTHONDONTWRITEBYTECODE": "profile-value",
        "PWD": str(cwd),
        "OLDPWD": "/old",
        "USER_VALUE": "ordinary",
    }))

    snapshot = ShellEnvironmentSnapshotProvider(
        runner=runner,
        parent_environment={
            "EIDOS_PARENT": "must-not-leak",
            "PYTHONHOME": "/runtime",
            "PYTHONPATH": "/runtime",
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "USER_VALUE": "ordinary-parent",
            "HOME": "/home",
            "PATH": "/bin",
        },
    ).get(shell, cwd)

    assert snapshot.environment["USER_VALUE"] == "ordinary"
    assert snapshot.environment["PYTHONHOME"] == "/profile/python-home"
    assert snapshot.environment["PYTHONPATH"] == "/profile/python-path"
    assert snapshot.environment["PYTHONNOUSERSITE"] == "profile-value"
    assert snapshot.environment["PYTHONDONTWRITEBYTECODE"] == "profile-value"
    assert not any(key.startswith("EIDOS_") for key in snapshot.environment)
    for key in ("PWD", "OLDPWD"):
        assert key not in snapshot.environment
    runner_environment = runner.calls[0]["environment"]
    assert isinstance(runner_environment, dict)
    assert "EIDOS_PARENT" not in runner_environment
    assert "PYTHONHOME" not in runner_environment
    assert "PYTHONPATH" not in runner_environment
    assert "PYTHONNOUSERSITE" not in runner_environment
    assert "PYTHONDONTWRITEBYTECODE" not in runner_environment
    assert "USER_VALUE" in runner_environment


@pytest.mark.parametrize(
    ("error", "returncode", "expected_diagnostic"),
    [
        (
            subprocess.TimeoutExpired(["shell"], 10),
            0,
            "timeout",
        ),
        (None, 7, "nonzero"),
    ],
)
def test_provider_falls_back_for_timeout_and_nonzero_exit(
    tmp_path: Path,
    error: BaseException | None,
    returncode: int,
    expected_diagnostic: str,
) -> None:
    shell = _shell(tmp_path / "shell")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    runner = _Runner(
        stdout=_env_output({"LEAK": "stdout-value"}),
        stderr=b"stderr-secret",
        returncode=returncode,
        error=error,
    )
    parent = {
        "HOME": "/parent/home",
        "PATH": "/parent/bin",
        "SHELL": str(shell.executable),
        "TMPDIR": "/parent/tmp",
        "ORDINARY": "preserved",
        "PWD": str(cwd),
        "OLDPWD": "/old",
        "EIDOS_CONTROL": "removed",
    }

    snapshot = ShellEnvironmentSnapshotProvider(
        runner=runner,
        parent_environment=parent,
    ).get(shell, cwd)

    assert snapshot.source == "fallback"
    assert snapshot.environment["ORDINARY"] == "preserved"
    assert "LEAK" not in snapshot.environment
    assert "stderr-secret" not in snapshot.diagnostic
    assert "stdout-value" not in snapshot.diagnostic
    assert expected_diagnostic in snapshot.diagnostic
    assert len(snapshot.diagnostic) <= 256
    assert "PWD" not in snapshot.environment
    assert "OLDPWD" not in snapshot.environment


def test_provider_falls_back_when_output_exceeds_bound(tmp_path: Path) -> None:
    shell = _shell(tmp_path / "shell")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    runner = _Runner(
        stdout=_env_output({"TOO_LARGE": "x"}) + b"x" * MAX_CAPTURE_BYTES,
    )

    snapshot = ShellEnvironmentSnapshotProvider(
        runner=runner,
        parent_environment={"HOME": "/home", "PATH": "/bin", "ORDINARY": "yes"},
    ).get(shell, cwd)

    assert snapshot.source == "fallback"
    assert snapshot.environment["ORDINARY"] == "yes"
    assert "output_limit" in snapshot.diagnostic
    assert len(snapshot.diagnostic) <= 256


def test_provider_logs_one_bounded_warning_per_failed_cache_key(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    shell = _shell(tmp_path / "shell")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    provider = ShellEnvironmentSnapshotProvider(
        runner=_Runner(error=OSError("secret runner detail")),
        parent_environment={"HOME": "/home", "PATH": "/bin"},
    )

    with caplog.at_level(logging.WARNING, logger=host_shell.__name__):
        first = provider.get(shell, cwd)
        second = provider.get(shell, cwd / ".")

    warnings = [
        record
        for record in caplog.records
        if record.name == host_shell.__name__
    ]
    assert first.source == "fallback"
    assert second is first
    assert len(warnings) == 1
    assert warnings[0].getMessage() == (
        "shell environment capture fallback: runner_error"
    )
    assert len(warnings[0].getMessage()) <= 256
    assert "secret runner detail" not in warnings[0].getMessage()


def test_provider_fills_required_variables_from_pwd_and_safe_fallback(
    tmp_path: Path,
) -> None:
    shell = _shell(tmp_path / "shell")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    pwd_home = tmp_path / "pwd-home"
    pwd_home.mkdir()
    runner = _Runner(error=OSError("runner unavailable"))

    snapshot = ShellEnvironmentSnapshotProvider(
        runner=runner,
        parent_environment={"ORDINARY": "yes"},
        pwd_resolver=lambda: SimpleNamespace(pw_dir=str(pwd_home)),
    ).get(shell, cwd)

    assert snapshot.source == "fallback"
    assert snapshot.environment["HOME"] == str(pwd_home)
    assert snapshot.environment["PATH"]
    assert snapshot.environment["SHELL"] == str(shell.executable)
    assert snapshot.environment["TMPDIR"] == "/tmp"


def test_provider_fills_user_logname_and_lang_from_capture_parent_and_pwd(
    tmp_path: Path,
) -> None:
    shell = _shell(tmp_path / "shell")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    runner = _Runner(stdout=_env_output({"USER": "captured-user"}))
    provider = ShellEnvironmentSnapshotProvider(
        runner=runner,
        parent_environment={
            "HOME": "/home",
            "PATH": "/bin",
            "LOGNAME": "parent-login",
            "LANG": "parent-lang",
        },
        pwd_resolver=lambda: SimpleNamespace(pw_name="pwd-user"),
    )

    snapshot = provider.get(shell, cwd)

    assert snapshot.environment["USER"] == "captured-user"
    assert snapshot.environment["LOGNAME"] == "parent-login"
    assert snapshot.environment["LANG"] == "parent-lang"


def test_provider_fallback_fills_user_logname_and_default_lang_from_pwd(
    tmp_path: Path,
) -> None:
    shell = _shell(tmp_path / "shell")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    provider = ShellEnvironmentSnapshotProvider(
        runner=_Runner(error=OSError("unavailable")),
        parent_environment={"HOME": "/home", "PATH": "/bin"},
        pwd_resolver=lambda: SimpleNamespace(pw_name="pwd-user"),
    )

    snapshot = provider.get(shell, cwd)

    assert snapshot.source == "fallback"
    assert snapshot.environment["USER"] == "pwd-user"
    assert snapshot.environment["LOGNAME"] == "pwd-user"
    assert snapshot.environment["LANG"] == "en_US.UTF-8"


def test_provider_caches_by_shell_executable_and_canonical_cwd(
    tmp_path: Path,
) -> None:
    shell = _shell(tmp_path / "shell")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    other_cwd = tmp_path / "other-cwd"
    other_cwd.mkdir()
    runner = _Runner(stdout=_env_output({"VALUE": "captured"}))
    provider = ShellEnvironmentSnapshotProvider(
        runner=runner,
        parent_environment={"HOME": "/home", "PATH": "/bin"},
    )

    first = provider.get(shell, cwd)
    same_canonical_cwd = provider.get(shell, cwd / ".")
    different_cwd = provider.get(shell, other_cwd)

    assert first is same_canonical_cwd
    assert different_cwd is not first
    assert len(runner.calls) == 2
    assert runner.calls[0]["cwd"] == cwd.resolve()
    assert runner.calls[1]["cwd"] == other_cwd.resolve()


def test_provider_cache_identity_includes_shell_executable(tmp_path: Path) -> None:
    first_shell = _shell(tmp_path / "first")
    second_shell = _shell(tmp_path / "second", "bash")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    runner = _Runner(stdout=_env_output({"VALUE": "captured"}))
    provider = ShellEnvironmentSnapshotProvider(
        runner=runner,
        parent_environment={"HOME": "/home", "PATH": "/bin"},
    )

    provider.get(first_shell, cwd)
    provider.get(second_shell, cwd)

    assert len(runner.calls) == 2


def test_provider_wraps_capture_and_separates_execution_identity(
    tmp_path: Path,
) -> None:
    shell = _shell(tmp_path / "shell")
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    runner = _Runner(stdout=_env_output({"VALUE": "captured"}))
    provider = ShellEnvironmentSnapshotProvider(
        runner=runner,
        parent_environment={"HOME": "/home", "PATH": "/bin"},
    )

    def sandbox(command: tuple[str, ...]) -> tuple[str, ...]:
        return ("sandbox-exec", "--", *command)

    provider.get(shell, cwd, command_wrapper=sandbox)
    provider.get(shell, cwd)

    assert runner.calls[0]["argv"][:2] == ("sandbox-exec", "--")
    assert runner.calls[0]["argv"][2:4] == (str(shell.executable), "-lc")
    assert len(runner.calls) == 2

from __future__ import annotations

from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from eidos_runtime.db.storage import WorkspaceIdentity
from eidos_runtime.domain.shell_environment import ShellEnvironmentPolicy
from eidos_runtime.sandbox.host_shell import HostShell
from eidos_runtime.sandbox.shell import prepare_shell_launch


def _identity(path: Path) -> WorkspaceIdentity:
    metadata = path.stat()
    return WorkspaceIdentity(path.resolve(), metadata.st_dev, metadata.st_ino, metadata.st_uid)


@pytest.mark.integration
@pytest.mark.parametrize("inherit", ["all", "core", "none"])
def test_zsh_preserves_glob_arguments_after_startup_without_replaying_commands(
    tmp_path: Path, inherit: str,
) -> None:
    workspace = tmp_path / "workspace with spaces"
    workspace.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    (home / ".zshenv").write_text(
        "setopt NOMATCH\n"
        "alias builtin=false\n"
        "alias unsetopt=false\n"
        "export TASK_FROM_PROFILE=loaded\n",
        encoding="utf-8",
    )
    (workspace / "matching.tsx").write_text("needle\n", encoding="utf-8")
    command = (
        "printf 'once\\n' >> once.txt\n"
        "/usr/bin/grep -l --include=*.tsx needle matching.tsx\n"
        "printf 'file=<%s>\\n' *.tsx\n"
        "printf 'missing=<%s>\\n' *.absent\n"
        "printf 'quoted=<%s>\\n' '--include=*.tsx'\n"
        "printf 'startup=<%s>\\n' \"${TASK_FROM_PROFILE-unset}\"\n"
        "/bin/cat <<'DATA'\n--include=*.tsx\nDATA"
    )
    launch = prepare_shell_launch(
        profile=SimpleNamespace(command=lambda argv: argv),
        command=command,
        cwd=_identity(workspace),
        attempt=None,
        shell=HostShell(Path("/bin/zsh"), "zsh"),
        environment={"HOME": str(home), "PATH": "/usr/bin:/bin"},
        environment_policy=ShellEnvironmentPolicy(inherit=inherit),
    )
    result = subprocess.run(
        launch.argv, cwd=launch.cwd, env=launch.environment,
        capture_output=True, text=True, timeout=5,
    )

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert result.stdout.splitlines() == [
        "matching.tsx", "file=<matching.tsx>", "missing=<*.absent>",
        "quoted=<--include=*.tsx>",
        "startup=<loaded>" if inherit == "all" else "startup=<unset>",
        "--include=*.tsx",
    ]
    assert (workspace / "once.txt").read_text(encoding="utf-8") == "once\n"


@pytest.mark.integration
@pytest.mark.parametrize("command, exit_code", [("false | true", 1), ("exit 7", 7)])
def test_zsh_glob_compatibility_keeps_pipeline_and_explicit_failures(
    tmp_path: Path, command: str, exit_code: int,
) -> None:
    launch = prepare_shell_launch(
        profile=SimpleNamespace(command=lambda argv: argv),
        command=command,
        cwd=_identity(tmp_path),
        attempt=None,
        shell=HostShell(Path("/bin/zsh"), "zsh"),
        environment={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )
    result = subprocess.run(
        launch.argv, cwd=launch.cwd, env=launch.environment,
        capture_output=True, timeout=5,
    )
    assert result.returncode == exit_code


@pytest.mark.parametrize("kind", ["bash", "sh"])
def test_other_shells_keep_the_original_command(tmp_path: Path, kind: str) -> None:
    command = "printf '%s\\n' *.tsx"
    launch = prepare_shell_launch(
        profile=SimpleNamespace(command=lambda argv: argv),
        command=command,
        cwd=_identity(tmp_path),
        attempt=None,
        shell=HostShell(Path(f"/bin/{kind}"), kind),
        environment={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )
    assert launch.argv[-2:] == ("-c", command)


@pytest.mark.platform
@pytest.mark.parametrize("inherit", ["all", "core", "none"])
def test_zsh_glob_compatibility_executes_in_real_seatbelt(
    tmp_path: Path, inherit: str,
) -> None:
    from eidos_runtime.sandbox.seatbelt import SeatbeltProfile, is_seatbelt_usable

    if not is_seatbelt_usable():
        pytest.skip("Native macOS Seatbelt is unavailable")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (tmp_path / ".zshenv").write_text("setopt NOMATCH\n", encoding="utf-8")
    (workspace / "matching.tsx").write_text("needle\n", encoding="utf-8")
    profile = SeatbeltProfile.create(
        workspace_root=workspace, sandbox_home=tmp_path, sandbox_tmp=tmp_path,
    )
    launch = prepare_shell_launch(
        profile=profile,
        command="printf 'once\\n' >> once.txt; /usr/bin/grep -l --include=*.tsx needle matching.tsx",
        cwd=_identity(workspace),
        attempt=None,
        shell=HostShell(Path("/bin/zsh"), "zsh"),
        environment={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
        environment_policy=ShellEnvironmentPolicy(inherit=inherit),
    )
    assert launch.sandboxed
    result = subprocess.run(
        launch.argv, cwd=launch.cwd, env=launch.environment,
        capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "matching.tsx\n"
    assert result.stderr == ""
    assert (workspace / "once.txt").read_text(encoding="utf-8") == "once\n"

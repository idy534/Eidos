from __future__ import annotations

import os
import stat
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
import shutil
import tempfile
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from eidos_runtime.db.storage import WorkspaceIdentity
from eidos_runtime.models import EidosFrozenStrictModel
from eidos_runtime.sandbox.dependency_environment import (
    DependencyShellEnvironment,
    apply_dependency_environment,
)
from eidos_runtime.sandbox.host_shell import HostShell, ShellEnvironmentSnapshot
from eidos_runtime.sandbox.seatbelt import is_seatbelt_ready
from eidos_runtime.sandbox.shell import (
    prepare_shell_launch,
    run_shell,
)


class _PassthroughProfile:
    @staticmethod
    def command(command: list[str]) -> list[str]:
        return list(command)


class _FixedHostShellResolver:
    def __init__(self, shell: HostShell) -> None:
        self.shell = shell

    def resolve(self) -> HostShell:
        return self.shell


class _FixedSnapshotProvider:
    def __init__(self, snapshot: ShellEnvironmentSnapshot) -> None:
        self.snapshot = snapshot

    def get(
        self,
        shell: HostShell,
        cwd: Path,
        *,
        command_wrapper: object | None = None,
    ) -> ShellEnvironmentSnapshot:
        del shell, cwd, command_wrapper
        return self.snapshot

    def fallback_environment(self, shell: HostShell) -> dict[str, str]:
        del shell
        return dict(self.snapshot.environment)


def _identity(path: Path) -> WorkspaceIdentity:
    metadata = path.stat()
    return WorkspaceIdentity(
        path=path.resolve(),
        device=metadata.st_dev,
        inode=metadata.st_ino,
        owner=metadata.st_uid,
    )


def _binding() -> DependencyShellEnvironment:
    return DependencyShellEnvironment(
        binding_id="binding-1",
        python_executable="/runtime/python with spaces/bin/python3",
        python_path=("/runtime/python with spaces/lib",),
        node_executable="/runtime/node with spaces/bin/node",
        node_modules="/runtime/node with spaces/node_modules",
        node_loader=(
            "/runtime/node with spaces/dependencies/node/runtime-loader.mjs"
        ),
        bin_paths=("/runtime/bin with spaces",),
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("python_executable", "python3"),
        ("python_path", ("relative/python",)),
        ("node_executable", "node"),
        ("node_modules", "node_modules"),
        ("node_loader", "runtime-loader.mjs"),
        ("bin_paths", ("bin",)),
    ],
)
def test_dependency_environment_rejects_non_absolute_paths(
    field: str,
    value: object,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        DependencyShellEnvironment(binding_id="binding-1", **{field: value})


def test_dependency_environment_rejects_non_tuple_path_collections() -> None:
    with pytest.raises((TypeError, ValueError)):
        DependencyShellEnvironment(
            binding_id="binding-1",
            python_path=["/runtime/python/lib"],  # type: ignore[arg-type]
        )


def test_dependency_environment_uses_the_frozen_strict_eidos_model() -> None:
    binding = DependencyShellEnvironment(binding_id="binding-1")

    assert isinstance(binding, EidosFrozenStrictModel)
    with pytest.raises(ValidationError):
        DependencyShellEnvironment.model_validate(
            {"binding_id": "binding-1", "unexpected": "value"}
        )
    with pytest.raises(ValidationError):
        DependencyShellEnvironment.model_validate(
            {"binding_id": "binding-1", "python_path": ["/runtime/python/lib"]}
        )


def test_bound_environment_precedes_host_path_and_scrubs_shadowing_controls() -> None:
    original = {
        "HOME": "/real/home",
        "TMPDIR": "/real/tmp",
        "PATH": "/host/bin:/bin",
        "NODE_ENV": "test",
        "NODE_OPTIONS": "--require=/untrusted/preload.cjs",
        "NODE_PATH": "/untrusted/node_modules",
        "NODE_DEBUG": "module",
        "NODE_EXTRA_CA_CERTS": "/untrusted/ca.pem",
        "NODE_TLS_REJECT_UNAUTHORIZED": "0",
        "NODE_V8_COVERAGE": "/untrusted/coverage",
        "PYTHONHOME": "/untrusted/python",
        "PYTHONPATH": "/untrusted/python-path",
        "PYTHONSTARTUP": "/untrusted/startup.py",
        "PYTHONNOUSERSITE": "0",
        "PYTHONDONTWRITEBYTECODE": "0",
        "PYTHONHASHSEED": "random",
        "PYTHONLEGACYWINDOWSFSENCODING": "1",
        "PYTHONLEGACYWINDOWSSTDIO": "1",
        "PYTHONWARNINGS": "default",
        "PYTHONUTF8": "0",
        "PYTHONVERBOSE": "1",
        "PYTHONBREAKPOINT": "untrusted.breakpoint",
        "RUNTIME_PYTHON": "/untrusted/python3",
        "RUNTIME_NODE": "/untrusted/node",
        "RUNTIME_NODE_MODULES": "/untrusted/node_modules",
        "RUNTIME_BIN_DIR": "/untrusted/bin",
    }

    environment = apply_dependency_environment(original, _binding())

    assert environment["HOME"] == original["HOME"]
    assert environment["TMPDIR"] == original["TMPDIR"]
    assert environment["NODE_ENV"] == original["NODE_ENV"]
    assert environment["PATH"] == (
        "/runtime/bin with spaces:/runtime/python with spaces/bin:"
        "/runtime/node with spaces/bin:/host/bin:/bin"
    )
    assert environment["PYTHONPATH"] == "/runtime/python with spaces/lib"
    assert environment["PYTHONNOUSERSITE"] == "1"
    assert environment["PYTHONDONTWRITEBYTECODE"] == "1"
    assert environment["RUNTIME_PYTHON"] == _binding().python_executable
    assert environment["RUNTIME_NODE"] == _binding().node_executable
    assert environment["RUNTIME_NODE_MODULES"] == _binding().node_modules
    assert environment["RUNTIME_BIN_DIR"] == "/runtime/bin with spaces"
    assert environment["NODE_OPTIONS"] == (
        "--import=file:///runtime/node%20with%20spaces/dependencies/"
        "node/runtime-loader.mjs"
    )
    for name in (
        "NODE_PATH",
        "NODE_DEBUG",
        "NODE_EXTRA_CA_CERTS",
        "NODE_TLS_REJECT_UNAUTHORIZED",
        "NODE_V8_COVERAGE",
        "PYTHONHOME",
        "PYTHONSTARTUP",
        "PYTHONHASHSEED",
        "PYTHONLEGACYWINDOWSFSENCODING",
        "PYTHONLEGACYWINDOWSSTDIO",
        "PYTHONWARNINGS",
        "PYTHONUTF8",
        "PYTHONVERBOSE",
        "PYTHONBREAKPOINT",
    ):
        assert name not in environment
    assert original["PATH"] == "/host/bin:/bin"
    assert original["NODE_OPTIONS"] == "--require=/untrusted/preload.cjs"


def test_runtime_executable_directories_resolve_without_host_path(tmp_path: Path) -> None:
    python_executable = tmp_path / "python runtime" / "bin" / "python3"
    node_executable = tmp_path / "node runtime" / "bin" / "node"
    python_executable.parent.mkdir(parents=True)
    node_executable.parent.mkdir(parents=True)
    for executable in (python_executable, node_executable):
        executable.write_text("#!/bin/sh\nexit 0\n")
        executable.chmod(executable.stat().st_mode | stat.S_IXUSR)

    binding = DependencyShellEnvironment(
        binding_id="binding-1",
        python_executable=str(python_executable),
        node_executable=str(node_executable),
    )
    environment = apply_dependency_environment(
        {"PATH": "/host/does-not-exist"},
        binding,
    )

    result = subprocess.run(
        ["/bin/sh", "-c", "command -v python3; command -v node"],
        check=True,
        capture_output=True,
        cwd=tmp_path,
        env=environment,
        text=True,
    )

    assert result.stdout.splitlines() == [
        str(python_executable),
        str(node_executable),
    ]
    assert environment["RUNTIME_BIN_DIR"] == str(python_executable.parent)


def test_bound_python_safe_path_prefers_trusted_docx_for_command_and_script(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    trusted = tmp_path / "trusted-python"
    workspace.mkdir()
    trusted.mkdir()
    (workspace / "docx.py").write_text("VALUE = 'workspace'\n")
    (trusted / "docx.py").write_text("VALUE = 'trusted'\n")
    script = workspace / "check_docx.py"
    script.write_text("import docx\nprint(docx.VALUE)\n")

    binding = DependencyShellEnvironment(
        binding_id="binding-1",
        python_executable=sys.executable,
        python_path=(str(trusted),),
    )
    environment = apply_dependency_environment(
        {
            "PATH": "/bin",
            "PYTHONSAFEPATH": "",
        },
        binding,
    )
    runtime_python = environment["RUNTIME_PYTHON"]

    command_result = subprocess.run(
        [runtime_python, "-c", "import docx; print(docx.VALUE)"],
        check=True,
        capture_output=True,
        cwd=workspace,
        env=environment,
        text=True,
    )
    script_result = subprocess.run(
        [runtime_python, str(script)],
        check=True,
        capture_output=True,
        cwd=workspace,
        env=environment,
        text=True,
    )

    assert command_result.stdout.strip() == "trusted"
    assert script_result.stdout.strip() == "trusted"
    assert environment["PYTHONSAFEPATH"] == "1"


def test_runtime_loader_script_runs_from_the_runtime_pytest_entrypoint() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("local Node is unavailable")

    version_result = subprocess.run(
        [node, "--version"],
        check=True,
        capture_output=True,
        text=True,
    )
    version_parts = version_result.stdout.strip().lstrip("v").split(".")
    major, minor = (int(version_parts[0]), int(version_parts[1]))
    if (major, minor) < (22, 15):
        pytest.skip("local Node does not support module.registerHooks")

    test_script = Path(__file__).with_name("test_dependency_runtime_loader.mjs")
    environment = os.environ.copy()
    environment.pop("NODE_OPTIONS", None)
    environment.pop("RUNTIME_NODE_MODULES", None)
    result = subprocess.run(
        [node, str(test_script)],
        check=False,
        capture_output=True,
        env=environment,
        text=True,
    )

    assert result.returncode == 0, (
        f"runtime loader test failed:\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )

@pytest.mark.platform
@pytest.mark.skipif(
    sys.platform != "darwin" or not is_seatbelt_ready(),
    reason="native macOS Seatbelt is unavailable",
)
def test_bound_dependency_directory_is_readable_but_not_writable_under_seatbelt(
    tmp_path: Path,
) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("local Node is unavailable")
    if not Path("/private/var/tmp").is_dir():
        pytest.skip("a Seatbelt-read-only temporary dependency root is unavailable")

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    with tempfile.TemporaryDirectory(
        prefix="eidos-runtime-dependencies-",
        dir="/private/var/tmp",
    ) as dependency_directory:
        node_modules = Path(dependency_directory)
        (node_modules / "trusted.txt").write_text("trusted", encoding="utf-8")
        workspace_identity = _identity(workspace)
        binding = DependencyShellEnvironment(
            binding_id="binding-1",
            node_executable=str(Path(node).resolve()),
            node_modules=str(node_modules),
        )
        result = run_shell(
            workspace_identity,
            (
                '"$RUNTIME_NODE" -e '
                "'const fs=require(\"node:fs\");"
                "const root=process.env.RUNTIME_NODE_MODULES;"
                "let denied=false;"
                "try { fs.writeFileSync(root+\"/blocked.txt\",\"no\"); }"
                "catch { denied=true; }"
                "fs.writeFileSync(\"workspace-output.txt\",\"ok\");"
                "process.stdout.write(fs.readFileSync(root+\"/trusted.txt\",\"utf8\")"
                "+\"|\"+denied);'"
            ),
            workspace_identity,
            10,
            threading.Event(),
            lambda _delta: None,
            dependency_environment=binding,
        )

        assert result["outcome"] == "success", result
        data = result["data"]
        assert isinstance(data, dict)
        assert data["stdout"] == "trusted|true"
        assert not (node_modules / "blocked.txt").exists()
    assert (workspace / "workspace-output.txt").read_text(encoding="utf-8") == "ok"


def test_bound_environment_does_not_change_command_or_cwd(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace with spaces"
    workspace.mkdir()
    identity = _identity(workspace)

    launch = prepare_shell_launch(
        profile=_PassthroughProfile(),
        command="printf '%s' \"$RUNTIME_NODE_MODULES\"",
        cwd=identity,
        attempt=None,
        shell=HostShell(Path("/bin/sh"), "sh"),
        environment={"HOME": "/home", "TMPDIR": "/tmp", "PATH": "/bin"},
        dependency_environment=_binding(),
    )

    assert launch.argv == (
        "/bin/sh",
        "-c",
        "printf '%s' \"$RUNTIME_NODE_MODULES\"",
    )
    assert launch.cwd == workspace.resolve()


def test_run_shell_forwards_binding_to_existing_process_launch(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    identity = _identity(workspace)
    shell = HostShell(Path("/bin/sh"), "sh")
    snapshot = ShellEnvironmentSnapshot(
        shell=shell,
        environment={
            "HOME": str(tmp_path),
            "TMPDIR": str(tmp_path),
            "PATH": "/host/bin:/bin",
            "SHELL": str(shell.executable),
            "USER": "test-user",
            "LOGNAME": "test-user",
            "LANG": "en_US.UTF-8",
        },
        captured_at=datetime(2026, 8, 31, tzinfo=timezone.utc),
        source="captured",
        diagnostic="captured",
    )
    resolver = _FixedHostShellResolver(shell)
    provider = _FixedSnapshotProvider(snapshot)
    captured: dict[str, object] = {}

    def fake_process(
        launch: object,
        *,
        timeout_seconds: int,
        cancel: threading.Event,
        on_delta: object,
        started: float,
        resource_registry: object,
        owner_id: str,
    ) -> dict[str, object]:
        del timeout_seconds, cancel, on_delta, started, resource_registry, owner_id
        captured["launch"] = launch
        return {"outcome": "success", "data": {}}

    with (
        patch("eidos_runtime.sandbox.shell.HOST_SHELL_RESOLVER", resolver),
        patch("eidos_runtime.sandbox.shell.SHELL_ENVIRONMENT_PROVIDER", provider),
        patch(
            "eidos_runtime.sandbox.shell.SeatbeltProfile.create",
            return_value=_PassthroughProfile(),
        ),
        patch(
            "eidos_runtime.sandbox.shell.run_shell_process",
            side_effect=fake_process,
        ),
    ):
        result = run_shell(
            identity,
            "true",
            identity,
            2,
            threading.Event(),
            lambda _delta: None,
            dependency_environment=_binding(),
        )

    assert result["outcome"] == "success"
    launch = captured["launch"]
    assert getattr(launch, "environment")["RUNTIME_NODE"] == _binding().node_executable
    assert getattr(launch, "environment")["RUNTIME_NODE_MODULES"] == _binding().node_modules


def test_none_dependency_environment_keeps_environment_unchanged() -> None:
    original = {"HOME": "/home", "PATH": "/bin", "NODE_OPTIONS": "keep"}

    assert apply_dependency_environment(original, None) == original

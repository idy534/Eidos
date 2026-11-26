"""Verified, Run-owned Windows ProcessContainer launches."""
from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import TYPE_CHECKING, BinaryIO

from eidos_runtime.infrastructure import local_files
from eidos_runtime.sandbox.permissions import EffectivePermissionProfile

if TYPE_CHECKING:
    from eidos_runtime.db.storage import WorkspaceIdentity
    from eidos_runtime.infrastructure.runtime_dependencies import RuntimeDependencyCatalog
    from eidos_runtime.infrastructure.windows_processes import MxcJobProcess


_HELPER_PATH = Path("app/eidos_runtime/resources/bin/windows-sandbox/eidos-sandbox-win.exe")
_MAX_HELPER_BYTES = 64 * 1024 * 1024
_HELPER_VERSION = "0.1.0"
_SDK_VERSION = "1.1.0"


class MxcUnavailableError(RuntimeError):
    """MXC could not enforce the requested boundary before launch."""


@dataclass(frozen=True)
class MxcHelper:
    path: Path
    sha256: str
    bundle_root: Path

    @contextmanager
    def verified_path(self) -> Iterator[Path]:
        from eidos_runtime.infrastructure.windows_files import commit_handle, owner_matches

        with commit_handle(self.path) as descriptor:
            info = os.fstat(descriptor)
            if info.st_nlink != 1 or not 0 < info.st_size <= _MAX_HELPER_BYTES:
                raise MxcUnavailableError("mxc_helper_invalid")
            if not owner_matches(descriptor, asset=True):
                raise MxcUnavailableError("mxc_helper_invalid")
            with os.fdopen(descriptor, "rb", closefd=False) as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != self.sha256:
                    raise MxcUnavailableError("mxc_helper_hash_mismatch")
            yield self.path

    def probe(self) -> None:
        with self.verified_path() as path:
            result = subprocess.run(
                (str(path), "probe"), cwd=path.parent, env=_helper_environment(),
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW, timeout=5, check=False,
            )
        if result.returncode != 0 or len(result.stdout) > 1024:
            raise MxcUnavailableError("mxc_probe_failed")
        try:
            value = json.loads(result.stdout)
        except (ValueError, UnicodeError):
            raise MxcUnavailableError("mxc_probe_failed") from None
        if (
            not isinstance(value, dict)
            or value.get("helperVersion") != _HELPER_VERSION
            or value.get("mxcVersion") != _SDK_VERSION
        ):
            raise MxcUnavailableError("mxc_probe_failed")
        if value.get("baseContainer") is not True:
            raise MxcUnavailableError("mxc_psec_unavailable")
        if value.get("fsDeny") is not True:
            raise MxcUnavailableError("mxc_fs_deny_unsupported")


@dataclass(frozen=True)
class MxcLaunch:
    helper: MxcHelper
    permissions: EffectivePermissionProfile
    workspace: WorkspaceIdentity
    cwd: WorkspaceIdentity

    def start(
        self, argv: Sequence[str], environment: Mapping[str, str], *,
        stdin: int | BinaryIO, stdout: int, stderr: int,
    ) -> MxcJobProcess:
        from eidos_runtime.infrastructure.windows_processes import MxcJobProcess
        from eidos_runtime.sandbox.mxc_policy import compile_mxc_policy
        from eidos_runtime.sandbox.shell import _open_verified_directory

        with ExitStack() as resources:
            for identity in (self.workspace, *self.workspace.additional_roots, self.cwd):
                fd = _open_verified_directory(identity)
                resources.callback(os.close, fd)
            scratch = local_files.canonical_path(resources.enter_context(
                tempfile.TemporaryDirectory(prefix="eidos-mxc-", ignore_cleanup_errors=True)
            ))
            home, tmp = scratch / "home", scratch / "tmp"
            home.mkdir()
            tmp.mkdir()
            try:
                permissions = self.permissions.model_copy(update={
                    "protected_write_paths": tuple(dict.fromkeys((
                        *self.permissions.protected_write_paths, str(self.helper.bundle_root),
                    ))),
                })
                policy = compile_mxc_policy(
                    permissions, sandbox_home=home, sandbox_tmp=tmp,
                    git_worktree_dir=self.workspace.git_dir, git_common_dir=self.workspace.git_common_dir,
                )
                resources.enter_context(policy.pin_readonly_paths())
                request = policy.request(argv, self.cwd.path, environment)
                with self.helper.verified_path() as helper_path:
                    process_resources = ExitStack()
                    process = MxcJobProcess(
                        (str(helper_path), "run"), cwd=helper_path.parent, env=_helper_environment(),
                        stdin=stdin, stdout=stdout, stderr=stderr, request=request,
                        launch_resources=process_resources,
                    )
                    process_resources.enter_context(resources.pop_all())
                    return process
            except (OSError, ValueError):
                raise MxcUnavailableError("mxc_launch_policy_unavailable") from None


@dataclass(frozen=True)
class MxcSelfTestResult:
    available: bool
    failures: tuple[str, ...] = ()


_ready_helper: MxcHelper | None = None


def current_mxc_helper() -> MxcHelper | None:
    return _ready_helper if local_files.WINDOWS else None


def run_mxc_self_test(catalog: RuntimeDependencyCatalog | None) -> MxcSelfTestResult:
    from eidos_runtime.sandbox.mxc_self_test import check_mxc_execution

    global _ready_helper
    _ready_helper = None
    if not local_files.WINDOWS or sys.getwindowsversion().build < 26100:
        return MxcSelfTestResult(False, ("unsupported_platform",))
    if os.environ.get("EIDOS_WINDOWS_SANDBOX", "").lower() == "off":
        return MxcSelfTestResult(False, ("mxc_disabled",))
    try:
        helper = _catalog_helper(catalog)
        helper.probe()
        failures = check_mxc_execution(helper)
    except MxcUnavailableError as error:
        failures = (str(error),)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
        failures = ("mxc_self_test_failed",)
    if failures:
        return MxcSelfTestResult(False, failures)
    _ready_helper = helper
    return MxcSelfTestResult(True)


def _catalog_helper(catalog: RuntimeDependencyCatalog | None) -> MxcHelper:
    if catalog is None:
        raise MxcUnavailableError("mxc_helper_missing")
    snapshot = catalog.snapshot()
    root = Path(snapshot.bundle_root)
    path = root / _HELPER_PATH
    entry = next((value for value in snapshot.files if Path(value.path) == path), None)
    if snapshot.target != "win32-x64" or entry is None:
        raise MxcUnavailableError("mxc_helper_missing")
    return MxcHelper(path, entry.sha256, root)


def _helper_environment() -> dict[str, str]:
    system_root = os.environ.get("SYSTEMROOT")
    if not system_root or not Path(system_root).is_absolute():
        raise MxcUnavailableError("mxc_system_root_unavailable")
    return {"SYSTEMROOT": system_root, "WINDIR": system_root, "PATH": str(Path(system_root) / "System32")}

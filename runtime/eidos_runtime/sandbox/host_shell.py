from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import pwd
import selectors
import signal
import stat
import subprocess
import threading
import time
from types import MappingProxyType
from typing import Literal


HostShellKind = Literal["zsh", "bash", "sh"]
SnapshotSource = Literal["captured", "fallback"]
PwdResolver = Callable[[], object]
ShellRunner = Callable[
    [tuple[str, ...], Path, Mapping[str, str], float, int],
    subprocess.CompletedProcess[bytes],
]
CommandWrapper = Callable[[tuple[str, ...]], Sequence[str]]


CAPTURE_MARKER = "__EIDOS_SHELL_ENV_V1__"
CAPTURE_SCRIPT = (
    f"/usr/bin/printf '%s\\0' '{CAPTURE_MARKER}'; /usr/bin/env -0"
)
MAX_CAPTURE_BYTES = 512 * 1024
CAPTURE_TIMEOUT_SECONDS = 10.0
MAX_DIAGNOSTIC_LENGTH = 256
DEFAULT_SHELL_PATHS: tuple[Path, ...] = (
    Path("/bin/zsh"),
    Path("/bin/bash"),
    Path("/bin/sh"),
)
SAFE_SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"
SAFE_HOME = "/var/empty"
SAFE_TMPDIR = "/tmp"
_ALLOWED_SHELL_KINDS = frozenset(("zsh", "bash", "sh"))
_SCRUBBED_ENV_NAMES = frozenset(
    (
        "PYTHONHOME",
        "PYTHONPATH",
        "PYTHONNOUSERSITE",
        "PYTHONDONTWRITEBYTECODE",
        "PWD",
        "OLDPWD",
    )
)
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class HostShell:
    executable: Path
    kind: HostShellKind

    def __post_init__(self) -> None:
        if self.kind not in _ALLOWED_SHELL_KINDS:
            raise ValueError("unsupported host shell kind")
        object.__setattr__(self, "executable", Path(self.executable))


class HostShellUnavailableError(RuntimeError):
    """Raised when no supported executable shell can be resolved."""


class HostShellResolver:
    """Resolve a supported host shell from passwd, environment, or defaults."""

    def __init__(
        self,
        *,
        parent_environment: Mapping[str, str] | None = None,
        pwd_resolver: PwdResolver | None = None,
    ) -> None:
        self._parent_environment = dict(
            os.environ if parent_environment is None else parent_environment
        )
        self._pwd_resolver = pwd_resolver or _default_pwd_resolver

    def resolve(self) -> HostShell:
        passwd_record = _safe_pwd_record(self._pwd_resolver)
        candidates: list[object] = []
        if passwd_record is not None:
            candidates.append(_passwd_value(passwd_record, "pw_shell"))
        candidates.append(self._parent_environment.get("SHELL"))
        candidates.extend(DEFAULT_SHELL_PATHS)

        for candidate in candidates:
            shell = _validated_shell(candidate)
            if shell is not None:
                return shell
        raise HostShellUnavailableError("no supported host shell is available")


@dataclass(frozen=True, slots=True)
class ShellEnvironmentSnapshot:
    shell: HostShell
    environment: Mapping[str, str]
    captured_at: datetime
    source: SnapshotSource
    diagnostic: str

    def __post_init__(self) -> None:
        if self.source not in {"captured", "fallback"}:
            raise ValueError("unsupported environment snapshot source")
        object.__setattr__(
            self,
            "environment",
            MappingProxyType(dict(self.environment)),
        )
        object.__setattr__(
            self,
            "diagnostic",
            str(self.diagnostic)[:MAX_DIAGNOSTIC_LENGTH],
        )


class ShellEnvironmentSnapshotProvider:
    """Capture a bounded login-shell environment with a safe fallback."""

    def __init__(
        self,
        *,
        runner: ShellRunner | None = None,
        parent_environment: Mapping[str, str] | None = None,
        clock: Callable[[], datetime] | None = None,
        pwd_resolver: PwdResolver | None = None,
    ) -> None:
        self._runner = runner or _run_bounded_capture
        self._parent_environment = dict(
            os.environ if parent_environment is None else parent_environment
        )
        self._clock = clock or _utc_now
        self._pwd_resolver = pwd_resolver or _default_pwd_resolver
        self._cache: dict[
            tuple[Path, Path, tuple[str, ...]], ShellEnvironmentSnapshot
        ] = {}
        self._cache_lock = threading.Lock()

    def get(
        self,
        shell: HostShell,
        cwd: Path,
        *,
        command_wrapper: CommandWrapper | None = None,
    ) -> ShellEnvironmentSnapshot:
        canonical_shell = shell.executable.resolve(strict=False)
        canonical_cwd = Path(cwd).resolve(strict=False)
        capture_argv = (str(shell.executable), "-lc", CAPTURE_SCRIPT)
        launch_argv = tuple(
            command_wrapper(capture_argv)
            if command_wrapper is not None
            else capture_argv
        )
        cache_key = (canonical_shell, canonical_cwd, launch_argv)
        with self._cache_lock:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached

            passwd_record = _safe_pwd_record(self._pwd_resolver)
            parent_environment = _complete_environment(
                sanitize_shell_environment(self._parent_environment),
                shell=shell,
                passwd_record=passwd_record,
                parent_environment={},
            )
            snapshot = self._capture(
                shell=shell,
                cwd=canonical_cwd,
                launch_argv=launch_argv,
                parent_environment=parent_environment,
                passwd_record=passwd_record,
            )
            self._cache[cache_key] = snapshot
            return snapshot

    def fallback_environment(self, shell: HostShell) -> dict[str, str]:
        """Return the sanitized, completed parent environment without capture."""
        passwd_record = _safe_pwd_record(self._pwd_resolver)
        return _complete_environment(
            sanitize_shell_environment(self._parent_environment),
            shell=shell,
            passwd_record=passwd_record,
            parent_environment=self._parent_environment,
        )

    def _capture(
        self,
        *,
        shell: HostShell,
        cwd: Path,
        launch_argv: tuple[str, ...],
        parent_environment: Mapping[str, str],
        passwd_record: object | None,
    ) -> ShellEnvironmentSnapshot:
        try:
            completed = self._runner(
                launch_argv,
                cwd,
                parent_environment,
                CAPTURE_TIMEOUT_SECONDS,
                MAX_CAPTURE_BYTES,
            )
            stdout = _output_bytes(completed.stdout)
            stderr = _output_bytes(completed.stderr)
            if len(stdout) + len(stderr) > MAX_CAPTURE_BYTES:
                return self._fallback(
                    shell=shell,
                    parent_environment=parent_environment,
                    passwd_record=passwd_record,
                    reason="output_limit",
                )
            if completed.returncode != 0:
                return self._fallback(
                    shell=shell,
                    parent_environment=parent_environment,
                    passwd_record=passwd_record,
                    reason="nonzero_exit",
                )
            captured_environment = _parse_environment(stdout)
            if captured_environment is None:
                return self._fallback(
                    shell=shell,
                    parent_environment=parent_environment,
                    passwd_record=passwd_record,
                    reason="invalid_output",
                )
            environment = _complete_environment(
                _sanitize_captured_environment(captured_environment),
                shell=shell,
                passwd_record=passwd_record,
                parent_environment=parent_environment,
            )
            return ShellEnvironmentSnapshot(
                shell=shell,
                environment=environment,
                captured_at=self._clock(),
                source="captured",
                diagnostic="captured",
            )
        except subprocess.TimeoutExpired:
            reason = "timeout"
        except _CaptureOutputLimitExceeded:
            reason = "output_limit"
        except Exception:
            reason = "runner_error"
        return self._fallback(
            shell=shell,
            parent_environment=parent_environment,
            passwd_record=passwd_record,
            reason=reason,
        )

    def _fallback(
        self,
        *,
        shell: HostShell,
        parent_environment: Mapping[str, str],
        passwd_record: object | None,
        reason: str,
    ) -> ShellEnvironmentSnapshot:
        _LOGGER.warning("shell environment capture fallback: %s", reason)
        environment = _complete_environment(
            sanitize_shell_environment(parent_environment),
            shell=shell,
            passwd_record=passwd_record,
            parent_environment=parent_environment,
        )
        return ShellEnvironmentSnapshot(
            shell=shell,
            environment=environment,
            captured_at=self._clock(),
            source="fallback",
            diagnostic=f"capture_fallback:{reason}",
        )


def _default_pwd_resolver() -> object:
    return pwd.getpwuid(os.getuid())


def _safe_pwd_record(resolver: PwdResolver) -> object | None:
    try:
        return resolver()
    except Exception:
        return None


def _passwd_value(record: object, attribute: str) -> object | None:
    if isinstance(record, (str, bytes, os.PathLike)):
        return record if attribute == "pw_shell" else None
    return getattr(record, attribute, None)


def _validated_shell(candidate: object) -> HostShell | None:
    if candidate is None:
        return None
    try:
        path = Path(candidate)
    except (TypeError, ValueError, OSError):
        return None
    if not path.is_absolute() or path.name not in _ALLOWED_SHELL_KINDS:
        return None
    try:
        metadata = path.stat()
    except OSError:
        return None
    if not stat.S_ISREG(metadata.st_mode):
        return None
    if not metadata.st_mode & 0o111 or not os.access(path, os.X_OK):
        return None
    return HostShell(path, path.name)  # type: ignore[arg-type]


def sanitize_shell_environment(environment: Mapping[str, str]) -> dict[str, str]:
    """Remove Eidos control-plane and Runtime-injected environment state."""
    sanitized: dict[str, str] = {}
    for key, value in environment.items():
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        if key.startswith("EIDOS_") or key in _SCRUBBED_ENV_NAMES:
            continue
        sanitized[key] = value
    return sanitized


def _sanitize_captured_environment(
    environment: Mapping[str, str],
) -> dict[str, str]:
    sanitized: dict[str, str] = {}
    for key, value in environment.items():
        if not isinstance(key, str) or not isinstance(value, str):
            continue
        if key.startswith("EIDOS_") or key in {"PWD", "OLDPWD"}:
            continue
        sanitized[key] = value
    return sanitized


def _complete_environment(
    environment: Mapping[str, str],
    *,
    shell: HostShell,
    passwd_record: object | None,
    parent_environment: Mapping[str, str],
) -> dict[str, str]:
    completed = dict(environment)
    parent = sanitize_shell_environment(parent_environment)
    passwd_home = (
        _passwd_value(passwd_record, "pw_dir")
        if passwd_record is not None
        else None
    )
    values = {
        "HOME": _first_text(
            completed.get("HOME"),
            parent.get("HOME"),
            passwd_home,
            SAFE_HOME,
        ),
        "PATH": _first_text(
            completed.get("PATH"),
            parent.get("PATH"),
            SAFE_SYSTEM_PATH,
        ),
        "SHELL": _first_text(
            completed.get("SHELL"),
            parent.get("SHELL"),
            str(shell.executable),
        ),
        "TMPDIR": _first_text(
            completed.get("TMPDIR"),
            parent.get("TMPDIR"),
            SAFE_TMPDIR,
        ),
        "USER": _first_text(
            completed.get("USER"),
            parent.get("USER"),
            _passwd_value(passwd_record, "pw_name")
            if passwd_record is not None
            else None,
            "",
        ),
        "LOGNAME": _first_text(
            completed.get("LOGNAME"),
            parent.get("LOGNAME"),
            _passwd_value(passwd_record, "pw_name")
            if passwd_record is not None
            else None,
            "",
        ),
        "LANG": _first_text(
            completed.get("LANG"),
            parent.get("LANG"),
            "en_US.UTF-8",
        ),
    }
    completed.update(values)
    completed.pop("PWD", None)
    completed.pop("OLDPWD", None)
    return completed


def _first_text(*values: object) -> str:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return ""


def _parse_environment(stdout: bytes) -> dict[str, str] | None:
    marker = CAPTURE_MARKER.encode("ascii") + b"\0"
    marker_position = stdout.rfind(marker)
    if marker_position < 0:
        return None
    payload = stdout[marker_position + len(marker):]
    environment: dict[str, str] = {}
    for record in payload.split(b"\0"):
        if not record:
            continue
        key, separator, value = record.partition(b"=")
        if not separator or not key:
            return None
        environment[os.fsdecode(key)] = os.fsdecode(value)
    return environment


def _output_bytes(output: bytes | bytearray | str | None) -> bytes:
    if output is None:
        return b""
    if isinstance(output, bytes):
        return output
    if isinstance(output, bytearray):
        return bytes(output)
    if isinstance(output, str):
        return output.encode("utf-8", errors="surrogateescape")
    raise TypeError("shell runner output must be bytes or text")


class _CaptureOutputLimitExceeded(RuntimeError):
    pass


def _run_bounded_capture(
    argv: tuple[str, ...],
    cwd: Path,
    environment: Mapping[str, str],
    timeout_seconds: float,
    output_limit_bytes: int,
) -> subprocess.CompletedProcess[bytes]:
    process = subprocess.Popen(
        argv,
        cwd=str(cwd),
        env=dict(environment),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
        close_fds=True,
        shell=False,
    )
    assert process.stdout is not None and process.stderr is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    outputs = {"stdout": bytearray(), "stderr": bytearray()}
    total = 0
    deadline = time.monotonic() + timeout_seconds
    try:
        while selector.get_map() or process.poll() is None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate_capture_process(process)
                raise subprocess.TimeoutExpired(argv, timeout_seconds)
            ready = selector.select(min(0.05, remaining)) if selector.get_map() else ()
            if not ready:
                if process.poll() is None:
                    time.sleep(0.005)
                continue
            for key, _mask in ready:
                chunk = os.read(key.fileobj.fileno(), 64 * 1024)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                if total + len(chunk) > output_limit_bytes:
                    _terminate_capture_process(process)
                    raise _CaptureOutputLimitExceeded
                outputs[key.data].extend(chunk)
                total += len(chunk)
        returncode = process.wait(timeout=1)
        return subprocess.CompletedProcess(
            argv,
            returncode,
            stdout=bytes(outputs["stdout"]),
            stderr=bytes(outputs["stderr"]),
        )
    finally:
        selector.close()
        if process.poll() is None:
            _terminate_capture_process(process)
        process.stdout.close()
        process.stderr.close()


def _terminate_capture_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except (OSError, PermissionError):
        pass
    try:
        process.wait(timeout=0.25)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (OSError, PermissionError):
        pass
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=1)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


HOST_SHELL_RESOLVER = HostShellResolver()
SHELL_ENVIRONMENT_PROVIDER = ShellEnvironmentSnapshotProvider()

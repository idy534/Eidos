"""Own the one system Chrome that every Session's browser tools share.

Chrome runs outside Eidos Seatbelt with its own renderer sandbox and a
dedicated profile under the Eidos data directory. Playwright MCP processes
connect to its loopback CDP port. Process state here is coordination only;
tool calls, approvals and results stay in SQLite.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import errno
import json
import logging
import os
from pathlib import Path
import plistlib
import signal
import socket
import stat
import subprocess
import threading
import time
import urllib.error
import urllib.request
from typing import Protocol

from eidos_runtime.sandbox.host_shell import sanitize_shell_environment

logger = logging.getLogger(__name__)

CHROME_BUNDLE_ID = "com.google.Chrome"
CHROME_APPLICATIONS = (
    Path("/Applications/Google Chrome.app"),
    Path.home() / "Applications" / "Google Chrome.app",
)
DEFAULT_IDLE_SECONDS = 30 * 60
START_TIMEOUT_SECONDS = 20.0
STOP_TIMEOUT_SECONDS = 10.0
_MAX_PLIST_BYTES = 256 * 1024
_MAX_STATE_BYTES = 4096


class BrowserUnavailable(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ChromeInstallation:
    executable: Path
    version: str


@dataclass(frozen=True)
class BrowserHostStatus:
    chrome_installed: bool
    chrome_version: str | None
    running: bool


def find_chrome(candidates: Sequence[Path] = CHROME_APPLICATIONS) -> ChromeInstallation | None:
    """Accept only a Google Chrome bundle owned by root or the current user."""

    for bundle in candidates:
        try:
            info_path = bundle / "Contents" / "Info.plist"
            metadata = info_path.lstat()
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > _MAX_PLIST_BYTES:
                continue
            info = plistlib.loads(info_path.read_bytes())
        except (OSError, plistlib.InvalidFileException, ValueError):
            continue
        name = info.get("CFBundleExecutable")
        version = info.get("CFBundleShortVersionString")
        if info.get("CFBundleIdentifier") != CHROME_BUNDLE_ID or not isinstance(name, str) or "/" in name:
            continue
        executable = bundle / "Contents" / "MacOS" / name
        try:
            binary = executable.lstat()
        except OSError:
            continue
        if (
            stat.S_ISREG(binary.st_mode)
            and binary.st_uid in {0, os.getuid()}
            and binary.st_mode & stat.S_IXUSR
        ):
            return ChromeInstallation(executable, version if isinstance(version, str) else "unknown")
    return None


def _probe_cdp(port: int) -> bool:
    # Never route the loopback endpoint through a configured HTTP proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/json/version", timeout=1) as response:
            body = response.read(64 * 1024)
        value = json.loads(body)
    except (OSError, urllib.error.URLError, ValueError):
        return False
    return isinstance(value, dict) and str(value.get("Browser", "")).startswith(("Chrome/", "HeadlessChrome/"))


class _Closable(Protocol):
    def close(self) -> None: ...


def _deny_downloads(port: int) -> _Closable:
    """Chrome resets download behavior when the client that set it disconnects."""

    from websockets.sync.client import connect

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f"http://127.0.0.1:{port}/json/version", timeout=2) as response:
        url = json.loads(response.read(64 * 1024)).get("webSocketDebuggerUrl")
    if not isinstance(url, str) or not url.startswith(f"ws://127.0.0.1:{port}/devtools/browser/"):
        raise ValueError("browser_control_endpoint_invalid")
    connection = connect(url, proxy=None, open_timeout=5, close_timeout=2, max_size=1024 * 1024, compression=None)
    try:
        connection.send(json.dumps({
            "id": 1, "method": "Browser.setDownloadBehavior",
            "params": {"behavior": "deny", "eventsEnabled": False},
        }))
        reply = json.loads(connection.recv(timeout=5))
    except BaseException:
        connection.close()
        raise
    if not isinstance(reply, dict) or reply.get("id") != 1 or "error" in reply:
        connection.close()
        raise RuntimeError("browser_download_policy_rejected")
    return connection


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _port_is_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


class BrowserHost:
    def __init__(
        self,
        profile_root: Path,
        *,
        chrome: Callable[[], ChromeInstallation | None] = find_chrome,
        popen: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
        probe: Callable[[int], bool] = _probe_cdp,
        control: Callable[[int], _Closable] | None = None,
        clock: Callable[[], float] = time.monotonic,
        extra_arguments: Sequence[str] = (),
        idle_seconds: float = DEFAULT_IDLE_SECONDS,
    ) -> None:
        self.profile = profile_root
        self._chrome = chrome
        self._popen = popen
        self._probe = probe
        self._control_factory = control or _deny_downloads
        self._control: _Closable | None = None
        self._clock = clock
        self._extra_arguments = tuple(extra_arguments)
        self.idle_seconds = idle_seconds
        self._lock = threading.RLock()
        self._process: subprocess.Popen[bytes] | None = None
        self._adopted_pid: int | None = None
        self._port: int | None = None
        self._last_used = clock()
        self._closed = False

    # Endpoint and lifecycle -------------------------------------------------

    def endpoint(self) -> str:
        """Return the stable CDP URL that MCP servers are launched with."""

        with self._lock:
            if self._closed:
                raise BrowserUnavailable("browser_unavailable")
            if self._port is None or (not self._running() and not _port_is_free(self._port)):
                adopted = self._adoptable_port()
                self._port = adopted if adopted is not None else _free_port()
                if adopted is not None:
                    self._adopted_pid = self._profile_lock_pid()
            return f"http://127.0.0.1:{self._port}"

    def ensure(self, cancel: threading.Event) -> str:
        """Start Chrome on the published port, or confirm it still answers."""

        with self._lock:
            if self._closed:
                raise BrowserUnavailable("browser_unavailable")
            # Keep the port already given to MCP servers; never move it here.
            if self._port is None:
                self.endpoint()
            port = self._port
            assert port is not None
            endpoint = f"http://127.0.0.1:{port}"
            if self._running():
                if self._probe(port):
                    if not self._control_alive():
                        self._attach_control(port)
                    self._last_used = self._clock()
                    return endpoint
                # Our Chrome stopped answering CDP; replace it on the same port.
                self._stop()
            installation = self._chrome()
            if installation is None:
                raise BrowserUnavailable("browser_unavailable")
            owner = self._profile_lock_pid()
            if owner is not None:
                if self._probe(port):
                    self._adopted_pid = owner
                    self._attach_control(port)
                    self._last_used = self._clock()
                    return endpoint
                raise BrowserUnavailable("browser_profile_in_use")
            if not _port_is_free(port):
                # Another process took the published port; later Runs get a new one.
                self._port = None
                raise BrowserUnavailable("browser_start_failed")
            self._launch(installation, port, cancel)
            self._last_used = self._clock()
            return endpoint

    def touch(self) -> None:
        with self._lock:
            self._last_used = self._clock()

    def status(self) -> BrowserHostStatus:
        installation = self._chrome()
        with self._lock:
            running = self._running()
        return BrowserHostStatus(
            chrome_installed=installation is not None,
            chrome_version=installation.version if installation is not None else None,
            running=running,
        )

    def close_if_idle(self) -> bool:
        with self._lock:
            if not self._running() or self._clock() - self._last_used < self.idle_seconds:
                return False
            self._stop()
            return True

    def close_browser(self) -> None:
        with self._lock:
            self._stop()

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
            self._stop()

    def run(self, cancel: threading.Event) -> None:
        """Persistent managed task: close idle Chrome, then Chrome at shutdown."""

        try:
            while not cancel.wait(30):
                try:
                    self.close_if_idle()
                except OSError as error:
                    logger.warning("Browser idle check deferred error=%s", error.__class__.__name__)
        finally:
            self.shutdown()

    # Internals --------------------------------------------------------------

    def _attach_control(self, port: int) -> None:
        """Hold the connection that keeps downloads denied; fail closed without it."""

        self._close_control()
        try:
            self._control = self._control_factory(port)
        except (OSError, ValueError, RuntimeError):
            self._stop()
            raise BrowserUnavailable("browser_start_failed") from None

    def _control_alive(self) -> bool:
        # websockets sets close_code once the connection has closed.
        return self._control is not None and getattr(self._control, "close_code", None) is None

    def _close_control(self) -> None:
        control, self._control = self._control, None
        if control is not None:
            try:
                control.close()
            except (OSError, RuntimeError):
                pass

    def _running(self) -> bool:
        if self._process is not None:
            if self._process.poll() is None:
                return True
            self._process = None
        if self._adopted_pid is not None:
            if _pid_alive(self._adopted_pid):
                return True
            self._adopted_pid = None
        return False

    def _launch(self, installation: ChromeInstallation, port: int, cancel: threading.Event) -> None:
        self._prepare_profile()
        arguments = [
            str(installation.executable),
            f"--user-data-dir={self.profile}",
            f"--remote-debugging-port={port}",
            "--no-first-run",
            "--no-default-browser-check",
            *self._extra_arguments,
            "about:blank",
        ]
        try:
            process = self._popen(
                arguments,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                env=sanitize_shell_environment(os.environ), cwd=str(self.profile),
                start_new_session=True, close_fds=True,
            )
        except OSError:
            raise BrowserUnavailable("browser_start_failed") from None
        self._process = process
        deadline = self._clock() + START_TIMEOUT_SECONDS
        while self._clock() < deadline:
            if cancel.is_set():
                self._stop()
                raise BrowserUnavailable("browser_start_canceled")
            if process.poll() is not None:
                self._process = None
                raise BrowserUnavailable("browser_start_failed")
            if self._probe(port):
                self._write_state(port, process.pid)
                self._attach_control(port)
                return
            cancel.wait(0.1)
        self._stop()
        raise BrowserUnavailable("browser_start_failed")

    def _prepare_profile(self) -> None:
        self.profile.mkdir(mode=0o700, parents=True, exist_ok=True)
        metadata = self.profile.lstat()
        if not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.getuid():
            raise BrowserUnavailable("browser_profile_invalid")
        if stat.S_IMODE(metadata.st_mode) != 0o700:
            os.chmod(self.profile, 0o700)
        self._remove_state()

    @property
    def _state_path(self) -> Path:
        # Chrome owns the profile directory; this sibling file only lets a
        # restarted Runtime find the port of the Chrome it launched earlier.
        return self.profile.parent / f"{self.profile.name}.cdp.json"

    def _write_state(self, port: int, pid: int) -> None:
        temporary = self._state_path.with_suffix(".tmp")
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump({"port": port, "pid": pid}, stream)
        os.replace(temporary, self._state_path)

    def _read_state(self) -> tuple[int, int] | None:
        try:
            descriptor = os.open(self._state_path, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError:
            return None
        with os.fdopen(descriptor, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
                return None
            try:
                value = json.loads(stream.read(_MAX_STATE_BYTES))
            except ValueError:
                return None
        port, pid = (value.get("port"), value.get("pid")) if isinstance(value, dict) else (None, None)
        if not isinstance(port, int) or not 0 < port < 65536 or not isinstance(pid, int) or pid <= 1:
            return None
        return port, pid

    def _remove_state(self) -> None:
        try:
            if stat.S_ISREG(self._state_path.lstat().st_mode):
                self._state_path.unlink()
        except FileNotFoundError:
            pass

    def _profile_lock_pid(self) -> int | None:
        """Chrome's SingletonLock names the live owner of this profile."""

        try:
            target = os.readlink(self.profile / "SingletonLock")
        except OSError:
            return None
        text = target.rsplit("-", 1)[-1]
        if not text.isdigit():
            return None
        pid = int(text)
        return pid if _pid_alive(pid) else None

    def _adoptable_port(self) -> int | None:
        owner = self._profile_lock_pid()
        state = self._read_state()
        if owner is None or state is None or state[1] != owner:
            return None
        return state[0] if self._probe(state[0]) else None

    def _stop(self) -> None:
        pid = self._process.pid if self._process is not None else self._adopted_pid
        process = self._process
        self._process = None
        self._adopted_pid = None
        self._close_control()
        if pid is None:
            return
        self._remove_state()
        _signal_group(pid, signal.SIGTERM)
        deadline = time.monotonic() + STOP_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if (process.poll() is not None) if process is not None else not _pid_alive(pid):
                return
            time.sleep(0.1)
        _signal_group(pid, signal.SIGKILL)
        if process is not None:
            try:
                process.wait(STOP_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                logger.warning("Chrome did not exit after SIGKILL")


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return False
    return True


def _signal_group(pid: int, signum: int) -> None:
    try:
        os.killpg(pid, signum)
    except ProcessLookupError:
        return
    except PermissionError:
        return
    except OSError as error:
        if error.errno != errno.ESRCH:
            try:
                os.kill(pid, signum)
            except OSError:
                return

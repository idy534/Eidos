from __future__ import annotations

import os
from pathlib import Path
import plistlib
import socket
import threading

import pytest

from eidos_runtime.infrastructure.browser_host import (
    BrowserHost,
    BrowserUnavailable,
    ChromeInstallation,
    find_chrome,
)


class FakeChrome:
    """Writes DevToolsActivePort like Chrome and answers the probe while alive."""

    def __init__(self, arguments, profile: Path, *, write_port: bool = True, exit_code: int | None = None):
        self.arguments = arguments
        self.pid = os.getpid()
        self.returncode = exit_code
        self.listening = write_port and exit_code is None

    def poll(self):
        return self.returncode

    def wait(self, _timeout=None):
        return self.returncode


class FakeControl:
    def __init__(self, port: int) -> None:
        self.port = port
        self.close_code = None

    def close(self) -> None:
        self.close_code = 1000


@pytest.fixture
def host(tmp_path, monkeypatch):
    launched: list[FakeChrome] = []
    profile = tmp_path / "browser" / "profiles" / "default"
    behaviour = {"write_port": True, "exit_code": None, "chrome": True}
    signals: list[tuple[int, int]] = []

    def popen(arguments, **options):
        assert options["start_new_session"] is True
        assert not any(key.startswith("EIDOS_") for key in options["env"])
        process = FakeChrome(arguments, profile, write_port=behaviour["write_port"], exit_code=behaviour["exit_code"])
        launched.append(process)
        return process

    def probe(port):
        return any(
            p.returncode is None and p.listening and f"--remote-debugging-port={port}" in p.arguments for p in launched
        )

    def killpg(pid, signum):
        signals.append((pid, signum))
        for process in launched:
            process.returncode = -signum

    monkeypatch.setattr("eidos_runtime.infrastructure.browser_host.os.killpg", killpg)
    monkeypatch.setenv("EIDOS_INTERNAL_TOKEN", "secret")
    controls: list[FakeControl] = []

    def control(port):
        if behaviour.get("control_error"):
            raise OSError("control refused")
        controls.append(FakeControl(port))
        return controls[-1]

    value = BrowserHost(
        profile,
        chrome=lambda: ChromeInstallation(Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"), "156.0")
        if behaviour["chrome"] else None,
        popen=popen, probe=probe, control=control,
    )
    value.test_controls = controls
    return value, launched, behaviour, signals


def test_endpoint_is_stable_and_chrome_starts_only_on_ensure(host):
    browser, launched, _, _ = host
    endpoint = browser.endpoint()
    assert endpoint.startswith("http://127.0.0.1:")
    assert browser.endpoint() == endpoint
    assert launched == []
    assert browser.ensure(threading.Event()) == endpoint
    assert browser.ensure(threading.Event()) == endpoint
    assert len(launched) == 1
    arguments = launched[0].arguments
    assert f"--user-data-dir={browser.profile}" in arguments
    assert "--no-sandbox" not in arguments
    assert (browser.profile.stat().st_mode & 0o777) == 0o700


def test_missing_chrome_reports_a_stable_error_without_launching(host):
    browser, launched, behaviour, _ = host
    behaviour["chrome"] = False
    with pytest.raises(BrowserUnavailable) as raised:
        browser.ensure(threading.Event())
    assert raised.value.code == "browser_unavailable"
    assert launched == []
    assert browser.status().chrome_installed is False


def test_chrome_that_exits_or_never_listens_fails_to_start(host, monkeypatch):
    browser, _, behaviour, _ = host
    behaviour["exit_code"] = 1
    with pytest.raises(BrowserUnavailable) as raised:
        browser.ensure(threading.Event())
    assert raised.value.code == "browser_start_failed"
    behaviour["exit_code"] = None
    behaviour["write_port"] = False
    monkeypatch.setattr("eidos_runtime.infrastructure.browser_host.START_TIMEOUT_SECONDS", 0.3)
    with pytest.raises(BrowserUnavailable) as raised:
        browser.ensure(threading.Event())
    assert raised.value.code == "browser_start_failed"


def test_cancel_during_start_stops_chrome(host):
    browser, launched, behaviour, signals = host
    behaviour["write_port"] = False
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(BrowserUnavailable) as raised:
        browser.ensure(cancel)
    assert raised.value.code == "browser_start_canceled"
    assert signals and launched[0].returncode is not None


def test_idle_and_shutdown_close_chrome(host):
    browser, launched, _, signals = host
    now = [100.0]
    browser._clock = lambda: now[0]
    browser.ensure(threading.Event())
    assert browser.close_if_idle() is False
    now[0] += browser.idle_seconds + 1
    assert browser.close_if_idle() is True
    assert launched[0].returncode is not None
    browser.ensure(threading.Event())
    assert len(launched) == 2
    browser.shutdown()
    assert launched[1].returncode is not None
    with pytest.raises(BrowserUnavailable):
        browser.endpoint()
    assert len(signals) == 2


def test_profile_held_by_another_chrome_without_cdp_is_reported(host):
    browser, launched, _, _ = host
    browser.profile.mkdir(parents=True)
    os.symlink(f"host-{os.getpid()}", browser.profile / "SingletonLock")
    with pytest.raises(BrowserUnavailable) as raised:
        browser.ensure(threading.Event())
    assert raised.value.code == "browser_profile_in_use"
    assert launched == []


def test_symlinked_or_mismatched_state_is_not_adopted(host, tmp_path):
    browser, _, _, _ = host
    browser.profile.mkdir(parents=True)
    os.symlink(f"host-{os.getpid()}", browser.profile / "SingletonLock")
    target = tmp_path / "elsewhere.json"
    target.write_text('{"port": 9222, "pid": %d}' % os.getpid())
    os.symlink(target, browser._state_path)
    assert browser._read_state() is None
    browser._state_path.unlink()
    browser._state_path.write_text('{"port": 9222, "pid": 1}')
    assert browser._adoptable_port() is None


def test_occupied_published_port_is_not_reused(host):
    browser, launched, _, _ = host
    endpoint = browser.endpoint()
    port = int(endpoint.rsplit(":", 1)[1])
    with socket.socket() as squatter:
        squatter.bind(("127.0.0.1", port))
        squatter.listen(1)
        with pytest.raises(BrowserUnavailable) as raised:
            browser.ensure(threading.Event())
        assert raised.value.code == "browser_start_failed"
        assert browser.endpoint() != endpoint
    assert launched == []


def test_find_chrome_requires_the_google_chrome_bundle(tmp_path):
    def bundle(name: str, identifier: str) -> Path:
        root = tmp_path / name
        (root / "Contents/MacOS").mkdir(parents=True)
        (root / "Contents/Info.plist").write_bytes(plistlib.dumps({
            "CFBundleIdentifier": identifier, "CFBundleExecutable": "Google Chrome",
            "CFBundleShortVersionString": "156.0.1",
        }))
        executable = root / "Contents/MacOS/Google Chrome"
        executable.write_text("#!/bin/sh\n")
        executable.chmod(0o755)
        return root

    impostor = bundle("Impostor.app", "com.example.Chrome")
    real = bundle("Google Chrome.app", "com.google.Chrome")
    assert find_chrome((impostor,)) is None
    found = find_chrome((impostor, real))
    assert found == ChromeInstallation(real / "Contents/MacOS/Google Chrome", "156.0.1")


@pytest.mark.platform
@pytest.mark.skipif(find_chrome() is None, reason="Google Chrome is not installed")
def test_real_chrome_starts_is_adopted_after_restart_and_stops(tmp_path, monkeypatch):
    from eidos_runtime.infrastructure.browser_host import _probe_cdp

    # The adopted Chrome is still this test's child, so it stays a zombie
    # until reaped below; a launchd-owned orphan in production exits fully.
    monkeypatch.setattr("eidos_runtime.infrastructure.browser_host.STOP_TIMEOUT_SECONDS", 2)

    profile = Path("/private/tmp") / f"eidos-chrome-{os.getpid()}-{tmp_path.name[-8:]}"
    first = BrowserHost(profile, extra_arguments=("--headless=new",))
    try:
        endpoint = first.ensure(threading.Event())
        port = int(endpoint.rsplit(":", 1)[1])
        assert _probe_cdp(port)
        process = first._process
        # A restarted Runtime adopts the live Chrome instead of starting another.
        second = BrowserHost(
            profile, extra_arguments=("--headless=new",),
            popen=lambda *_a, **_k: pytest.fail("a live profile must be adopted"),
        )
        assert second.endpoint() == endpoint
        assert second.ensure(threading.Event()) == endpoint
        assert second._control_alive()
        second.shutdown()
        assert process.wait(timeout=5) is not None
        assert not _probe_cdp(port)
        assert not second._state_path.exists()
    finally:
        first.shutdown()
        import shutil
        shutil.rmtree(profile, ignore_errors=True)


def test_managed_task_cancel_stops_chrome_for_shutdown_quiescence(host):
    browser, launched, _, _ = host
    browser.ensure(threading.Event())
    cancel = threading.Event()
    worker = threading.Thread(target=browser.run, args=(cancel,))
    worker.start()
    cancel.set()
    worker.join(5)
    assert not worker.is_alive()
    assert launched[0].returncode is not None
    with pytest.raises(BrowserUnavailable):
        browser.ensure(threading.Event())


def test_downloads_stay_denied_by_a_held_control_connection(host):
    browser, launched, behaviour, signals = host
    browser.ensure(threading.Event())
    assert len(browser.test_controls) == 1 and browser.test_controls[0].close_code is None
    browser.ensure(threading.Event())
    assert len(browser.test_controls) == 1
    # A dropped control connection is replaced before the next tool call.
    browser.test_controls[0].close()
    browser.ensure(threading.Event())
    assert len(browser.test_controls) == 2
    browser.close_browser()
    assert browser.test_controls[1].close_code == 1000


def test_chrome_without_download_denial_is_stopped(host):
    browser, launched, behaviour, signals = host
    behaviour["control_error"] = True
    with pytest.raises(BrowserUnavailable) as raised:
        browser.ensure(threading.Event())
    assert raised.value.code == "browser_start_failed"
    assert launched[0].returncode is not None and signals

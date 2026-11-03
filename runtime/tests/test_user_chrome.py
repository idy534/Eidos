from __future__ import annotations

import os
import socket

import pytest

from eidos_runtime.infrastructure.user_chrome import user_chrome_endpoint


@pytest.fixture
def listener():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(4)
    yield server.getsockname()[1]
    server.close()


def test_live_devtools_port_becomes_a_browser_websocket(tmp_path, listener):
    (tmp_path / "DevToolsActivePort").write_text(f"{listener}\n/devtools/browser/0b1c-42\n")
    assert user_chrome_endpoint(tmp_path) == f"ws://127.0.0.1:{listener}/devtools/browser/0b1c-42"


def test_missing_stale_or_malformed_files_mean_not_available(tmp_path, listener):
    assert user_chrome_endpoint(tmp_path) is None
    port_file = tmp_path / "DevToolsActivePort"
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        stale = probe.getsockname()[1]
    port_file.write_text(f"{stale}\n/devtools/browser/abc\n")
    assert user_chrome_endpoint(tmp_path) is None
    port_file.write_text(f"{listener}\n/devtools/page/abc\n")
    assert user_chrome_endpoint(tmp_path) is None
    port_file.write_text(f"{listener}\n/devtools/browser/../../x\n")
    assert user_chrome_endpoint(tmp_path) is None


def test_symlinked_port_file_is_ignored(tmp_path, listener):
    target = tmp_path / "elsewhere"
    target.write_text(f"{listener}\n/devtools/browser/abc\n")
    data = tmp_path / "Chrome"
    data.mkdir()
    os.symlink(target, data / "DevToolsActivePort")
    assert user_chrome_endpoint(data) is None

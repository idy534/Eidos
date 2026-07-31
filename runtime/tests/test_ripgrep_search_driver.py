from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import socket
import stat
import subprocess
import sys
import threading
import time

import pytest

from eidos_runtime.tools.workspace import ToolExecutor
from eidos_runtime.workspace.discovery_scope import WorkspaceDiscoveryScope
from eidos_runtime.workspace.search_driver import (
    MAX_RG_JSON_LINE_BYTES,
    MAX_RG_STDOUT_BYTES,
    RipgrepBinaryResolver,
    RipgrepSearchDriver,
    SearchDriverError,
    WorkspaceSearchMatch,
    WorkspaceSearchRequest,
    WorkspaceSearchResult,
)


def _scope(workspace: Path) -> WorkspaceDiscoveryScope:
    descriptor = os.open(workspace, os.O_RDONLY | os.O_DIRECTORY)
    try:
        return WorkspaceDiscoveryScope.load(descriptor)
    finally:
        os.close(descriptor)


def _request(
    workspace: Path,
    query: str = "needle",
    *,
    deadline_seconds: float = 5,
    max_results: int = 100,
) -> WorkspaceSearchRequest:
    return WorkspaceSearchRequest(
        query=query,
        workspace_path=workspace,
        deadline=time.monotonic() + deadline_seconds,
        max_results=max_results,
        max_preview_characters=300,
        discovery_scope=_scope(workspace),
    )


def _fixture_binary(tmp_path: Path, body: str) -> Path:
    interpreter = tmp_path / "python-fixture"
    if not interpreter.exists():
        interpreter.symlink_to(sys.executable)
    binary = tmp_path / "rg-fixture"
    binary.write_text(f"#!{interpreter}\n{body}", encoding="utf-8")
    binary.chmod(0o700)
    return binary


def _driver(binary: Path) -> RipgrepSearchDriver:
    return RipgrepSearchDriver(RipgrepBinaryResolver(explicit_path=binary))


class _FakeSearchDriver:
    def __init__(
        self,
        result: WorkspaceSearchResult | None = None,
        error: SearchDriverError | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.requests: list[WorkspaceSearchRequest] = []

    def search(
        self,
        request: WorkspaceSearchRequest,
        cancel: threading.Event,
    ) -> WorkspaceSearchResult:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


def test_tool_executor_maps_driver_result_without_changing_contract(tmp_path: Path) -> None:
    fake = _FakeSearchDriver(
        WorkspaceSearchResult(
            matches=(WorkspaceSearchMatch("src/example.py", 12, 8, "a needle"),),
            scanned_bytes=12345,
            truncated=False,
            truncation_reason=None,
        )
    )

    with ToolExecutor(tmp_path, search_driver=fake) as executor:
        result = executor.execute("search_text", {"query": "needle"}, threading.Event())

    assert result == {
        "schemaVersion": 1,
        "toolContractVersion": 1,
        "toolName": "search_text",
        "outcome": "success",
        "code": "ok",
        "summary": "Searched text",
        "data": {
            "matches": [
                {
                    "path": "src/example.py",
                    "line": 12,
                    "column": 8,
                    "preview": "a needle",
                }
            ],
            "scannedBytes": 12345,
            "truncated": False,
            "truncationReason": None,
        },
        "sideEffectsMayExist": False,
        "reconciliationRequired": False,
    }
    assert fake.requests[0].query == "needle"
    assert fake.requests[0].workspace_path == tmp_path
    assert fake.requests[0].max_results == 100
    assert fake.requests[0].max_preview_characters == 300


@pytest.mark.parametrize(
    ("driver_code", "tool_code"),
    [
        ("search_backend_unavailable", "search_backend_unavailable"),
        ("search_backend_invalid", "search_backend_invalid"),
        ("search_backend_protocol_error", "search_backend_protocol_error"),
        ("search_backend_failed", "search_backend_failed"),
        ("search_backend_timeout", "search_backend_timeout"),
        ("search_backend_canceled", "canceled"),
    ],
)
def test_tool_executor_maps_stable_driver_errors(
    tmp_path: Path, driver_code: str, tool_code: str
) -> None:
    fake = _FakeSearchDriver(error=SearchDriverError(driver_code))
    with ToolExecutor(tmp_path, search_driver=fake) as executor:
        result = executor.execute("search_text", {"query": "needle"}, threading.Event())
    assert result["outcome"] == "error"
    assert result["code"] == tool_code


@pytest.mark.skipif(
    sys.platform != "darwin" or platform.machine() != "arm64",
    reason="the C3 production artifact is intentionally macOS arm64 only",
)
def test_real_ripgrep_literal_ascii_case_and_result_shape(tmp_path: Path) -> None:
    (tmp_path / "literal.txt").write_text(
        "A.B\nvalue[0]\nfoo*bar\nNEEDLE café Ä\nneedle CAFÉ ä\n",
        encoding="utf-8",
    )
    driver = RipgrepSearchDriver()

    assert [match.preview for match in driver.search(
        _request(tmp_path, "a.b"), threading.Event()
    ).matches] == ["A.B"]
    assert [match.preview for match in driver.search(
        _request(tmp_path, "value[0]"), threading.Event()
    ).matches] == ["value[0]"]
    assert [match.preview for match in driver.search(
        _request(tmp_path, "foo*bar"), threading.Event()
    ).matches] == ["foo*bar"]
    needle = driver.search(_request(tmp_path, "needle"), threading.Event())
    assert [(match.line, match.column) for match in needle.matches] == [(4, 1), (5, 1)]
    assert driver.search(_request(tmp_path, "Ä"), threading.Event()).matches == (
        WorkspaceSearchMatch("literal.txt", 4, 13, "NEEDLE café Ä"),
    )
    no_match = driver.search(_request(tmp_path, "absent"), threading.Event())
    assert no_match == WorkspaceSearchResult((), 0, False, None)


@pytest.mark.skipif(
    sys.platform != "darwin" or platform.machine() != "arm64",
    reason="the C3 production artifact is intentionally macOS arm64 only",
)
def test_real_ripgrep_cancellation_reaps_process(tmp_path: Path) -> None:
    (tmp_path / "large.txt").write_bytes(b"needle\n" * 20_000)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(SearchDriverError, match="search_backend_canceled"):
        RipgrepSearchDriver().search(_request(tmp_path), cancel)


@pytest.mark.skipif(
    sys.platform != "darwin" or platform.machine() != "arm64",
    reason="the C3 production artifact is intentionally macOS arm64 only",
)
def test_real_ripgrep_preserves_c2_ignore_sources_and_refresh(tmp_path: Path) -> None:
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    nested = tmp_path / "nested"
    nested.mkdir()
    for path in (
        tmp_path / "visible.txt",
        tmp_path / "ignored.log",
        fixtures / "hidden.txt",
        fixtures / "agent-test.json",
        nested / "kept.txt",
        tmp_path / "ignored-by-dot-ignore.txt",
        tmp_path / "ignored-by-rgignore.txt",
    ):
        path.write_text("needle\n", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("*.log\nfixtures/\n", encoding="utf-8")
    (tmp_path / ".eidosignore").write_text(
        "!fixtures/agent-test.json\n", encoding="utf-8"
    )
    (nested / ".gitignore").write_text("kept.txt\n", encoding="utf-8")
    (tmp_path / ".ignore").write_text("ignored-by-dot-ignore.txt\n", encoding="utf-8")
    (tmp_path / ".rgignore").write_text("ignored-by-rgignore.txt\n", encoding="utf-8")
    driver = RipgrepSearchDriver()

    first = driver.search(_request(tmp_path), threading.Event())
    assert [match.path for match in first.matches] == [
        "fixtures/agent-test.json",
        "ignored-by-dot-ignore.txt",
        "ignored-by-rgignore.txt",
        "nested/kept.txt",
        "visible.txt",
    ]

    (tmp_path / ".gitignore").write_text("", encoding="utf-8")
    second = driver.search(_request(tmp_path), threading.Event())
    assert "ignored.log" in [match.path for match in second.matches]


@pytest.mark.skipif(
    sys.platform != "darwin" or platform.machine() != "arm64",
    reason="the C3 production artifact is intentionally macOS arm64 only",
)
def test_real_ripgrep_enforces_sensitive_symlink_and_file_compatibility(
    tmp_path: Path,
) -> None:
    for directory in (".git", ".eidos", ".ssh", ".aws", "node_modules"):
        path = tmp_path / directory
        path.mkdir()
        (path / "visible.txt").write_text("needle\n", encoding="utf-8")
    for name in (
        ".env",
        "private.key",
        "credentials.json",
        "client-secret.txt",
        "access-token.txt",
    ):
        (tmp_path / name).write_text("needle\n", encoding="utf-8")
    (tmp_path / ".env.example").write_text("needle\n", encoding="utf-8")
    (tmp_path / "invalid.txt").write_bytes(b"needle\n\xff")
    (tmp_path / "binary.txt").write_bytes(b"needle\x00later\n")
    (tmp_path / "oversized.txt").write_bytes(b"needle\n" + b"x" * (256 * 1024))
    ordinary = tmp_path / "ordinary.txt"
    ordinary.write_text("needle\n", encoding="utf-8")
    outside = tmp_path.parent / f"{tmp_path.name}-outside.txt"
    outside.write_text("needle\n", encoding="utf-8")
    (tmp_path / "outside-link.txt").symlink_to(outside)
    fifo = tmp_path / "stream"
    os.mkfifo(fifo)
    socket_path = tmp_path / "socket"
    local_socket = socket.socket(socket.AF_UNIX)
    previous_cwd = Path.cwd()
    try:
        os.chdir(tmp_path)
        local_socket.bind("socket")
    finally:
        os.chdir(previous_cwd)
    (tmp_path / ".eidosignore").write_text(
        "!.git/visible.txt\n!.ssh/visible.txt\n!.env\n", encoding="utf-8"
    )

    try:
        result = RipgrepSearchDriver().search(_request(tmp_path), threading.Event())
        assert [match.path for match in result.matches] == [
            ".env.example", "ordinary.txt"
        ]
    finally:
        local_socket.close()
        fifo.unlink(missing_ok=True)
        socket_path.unlink(missing_ok=True)
        outside.unlink()


def test_no_match_exit_one_is_success_with_zero_scanned_bytes(tmp_path: Path) -> None:
    binary = _fixture_binary(tmp_path, "import sys\nsys.exit(1)\n")
    result = _driver(binary).search(_request(tmp_path), threading.Event())
    assert result == WorkspaceSearchResult((), 0, False, None)


def test_nonzero_backend_error_does_not_leak_stderr(tmp_path: Path) -> None:
    binary = _fixture_binary(
        tmp_path,
        "import sys\nsys.stderr.write('Authorization: secret-value\\n')\nsys.exit(2)\n",
    )
    with pytest.raises(SearchDriverError, match="search_backend_failed") as captured:
        _driver(binary).search(_request(tmp_path), threading.Event())
    assert "secret-value" not in str(captured.value)


@pytest.mark.parametrize(
    "body",
    [
        "print('not-json')\n",
        f"print('x' * {MAX_RG_JSON_LINE_BYTES + 1})\n",
        f"import os\nos.write(1, b'x' * {MAX_RG_STDOUT_BYTES + 1})\n",
    ],
)
def test_malformed_or_unbounded_stdout_is_protocol_error(
    tmp_path: Path, body: str
) -> None:
    binary = _fixture_binary(tmp_path, body)
    with pytest.raises(SearchDriverError, match="search_backend_protocol_error"):
        _driver(binary).search(_request(tmp_path), threading.Event())


@pytest.mark.parametrize(
    ("cancel_first", "code"),
    [(False, "search_backend_timeout"), (True, "search_backend_canceled")],
)
def test_timeout_and_cancellation_reap_process(
    tmp_path: Path, cancel_first: bool, code: str
) -> None:
    binary = _fixture_binary(tmp_path, "import time\ntime.sleep(30)\n")
    cancel = threading.Event()
    if cancel_first:
        cancel.set()
    with pytest.raises(SearchDriverError, match=code):
        _driver(binary).search(
            _request(tmp_path, deadline_seconds=0.05),
            cancel,
        )


def test_result_limit_terminates_cleanly_as_controlled_truncation(tmp_path: Path) -> None:
    event = json.dumps({
        "type": "match",
        "data": {
            "path": {"text": "visible.txt"},
            "lines": {"text": "needle\\n"},
            "line_number": 1,
            "submatches": [{"start": 0, "end": 6}],
        },
    })
    (tmp_path / "visible.txt").write_text("needle\n", encoding="utf-8")
    binary = _fixture_binary(
        tmp_path,
        "import json, sys, time\n"
        f"event = json.loads({event!r})\n"
        "for index in range(1000):\n"
        "    event['data']['line_number'] = index + 1\n"
        "    print(json.dumps(event), flush=True)\n"
        "time.sleep(30)\n",
    )
    result = _driver(binary).search(
        _request(tmp_path, max_results=2), threading.Event()
    )
    assert len(result.matches) == 2
    assert result.truncated is True
    assert result.truncation_reason == "result_limit"


def test_binary_resolver_rejects_missing_hash_mismatch_and_writable_artifact(
    tmp_path: Path,
) -> None:
    resource_root = tmp_path / "ripgrep"
    artifact_dir = resource_root / "darwin-arm64"
    artifact_dir.mkdir(parents=True)
    manifest = {
        "version": "15.2.0",
        "artifacts": {
            "darwin-arm64": {
                "path": "darwin-arm64/rg",
                "sha256": "0" * 64,
            }
        },
    }
    (resource_root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    resolver = RipgrepBinaryResolver(resource_root=resource_root)
    with pytest.raises(SearchDriverError, match="search_backend_unavailable"):
        resolver.resolve()

    binary = artifact_dir / "rg"
    binary.write_bytes(b"fixture")
    binary.chmod(0o700)
    with pytest.raises(SearchDriverError, match="search_backend_invalid"):
        resolver.resolve()

    manifest["artifacts"]["darwin-arm64"]["sha256"] = hashlib.sha256(
        b"fixture"
    ).hexdigest()
    (resource_root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    binary.chmod(0o722)
    with pytest.raises(SearchDriverError, match="search_backend_invalid"):
        RipgrepBinaryResolver(resource_root=resource_root).resolve()


def test_binary_resolver_cache_invalidates_when_artifact_identity_changes(
    tmp_path: Path,
) -> None:
    resource_root = tmp_path / "ripgrep"
    artifact_dir = resource_root / "darwin-arm64"
    artifact_dir.mkdir(parents=True)
    binary = artifact_dir / "rg"
    binary.write_bytes(b"trusted")
    binary.chmod(0o700)
    manifest = {
        "version": "15.2.0",
        "artifacts": {
            "darwin-arm64": {
                "path": "darwin-arm64/rg",
                "sha256": hashlib.sha256(b"trusted").hexdigest(),
            }
        },
    }
    (resource_root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    resolver = RipgrepBinaryResolver(resource_root=resource_root)

    assert resolver.resolve() == binary
    binary.write_bytes(b"changed")
    binary.chmod(0o700)

    with pytest.raises(SearchDriverError, match="search_backend_invalid"):
        resolver.resolve()


def test_driver_uses_fixed_argv_no_shell_path_or_user_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = _fixture_binary(tmp_path, "import sys\nsys.exit(1)\n")
    original_popen = subprocess.Popen
    observed: dict[str, object] = {}

    def recording_popen(*args: object, **kwargs: object):
        observed["args"] = args
        observed["kwargs"] = kwargs
        return original_popen(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", recording_popen)
    monkeypatch.setenv("RIPGREP_CONFIG_PATH", "/tmp/host-ripgreprc")
    monkeypatch.setenv("PATH", "/tmp/host-path")

    _driver(binary).search(_request(tmp_path, "foo*bar"), threading.Event())

    argv = observed["args"][0]
    kwargs = observed["kwargs"]
    assert isinstance(argv, list)
    assert argv[0] == str(binary)
    assert "--fixed-strings" in argv
    assert "--ignore-case" in argv
    assert "--no-unicode" in argv
    assert "--no-config" in argv
    assert "--no-ignore-parent" in argv
    assert "--no-ignore-global" in argv
    assert "--no-ignore-vcs" in argv
    assert "--no-ignore-dot" in argv
    assert argv[-3:] == ["--", "foo*bar", "."]
    assert kwargs["shell"] is False
    assert "PATH" not in kwargs["env"]
    assert "RIPGREP_CONFIG_PATH" not in kwargs["env"]


def test_repository_manifest_matches_managed_binary() -> None:
    resource_root = (
        Path(__file__).resolve().parents[1]
        / "eidos_runtime"
        / "resources"
        / "bin"
        / "ripgrep"
    )
    manifest = json.loads((resource_root / "manifest.json").read_text(encoding="utf-8"))
    artifact = manifest["artifacts"]["darwin-arm64"]
    binary = resource_root / artifact["path"]
    assert manifest["version"] == "15.2.0"
    assert hashlib.sha256(binary.read_bytes()).hexdigest() == artifact["sha256"]
    mode = binary.stat().st_mode
    assert stat.S_ISREG(mode)
    assert mode & stat.S_IXUSR
    assert not mode & (stat.S_IWGRP | stat.S_IWOTH)
